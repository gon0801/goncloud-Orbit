"""Salud y avisos de la ingesta principal de Amazon Ads."""

import datetime as dt
import os
import socket
from pathlib import Path

import pytest
from test_schema import SQL, _postgres_obligatorio_ausente, _test_dsn

from app.ads import salud

SQL40 = (Path(__file__).resolve().parents[1] / "migrations/0040_ads_report_result.sql").read_text()
SQL41 = (Path(__file__).resolve().parents[1] / "migrations/0041_ads_ingest_alert.sql").read_text()


def test_fallo_principal_abre_aviso_y_productos_no_lo_cierra():
    abiertos = salud.incidentes_de_run(
        source="amazon_ads_reports_v3",
        ok=False,
        unidades=[(101, "amazon_us", "campaigns", "failed")],
    )
    assert abiertos == [(101, "amazon_us", "fallo")]
    assert (
        salud.incidentes_de_run(
            source="amazon_ads_products_v3",
            ok=True,
            unidades=[(101, "amazon_us", "productos", "written")],
        )
        == []
    )


def test_fallo_global_no_se_atribuye_a_un_perfil():
    assert salud.incidentes_de_run(
        source="amazon_ads_reports_v3",
        ok=False,
        unidades=[(None, None, None, "global_failed")],
    ) == [(None, None, "fallo")]
    assert salud.incidentes_de_run(
        source="amazon_ads_reports_v3",
        ok=True,
        unidades=[(None, "amazon_us", None, "rejected")],
    ) == [(None, None, "fallo")]


def test_atraso_solo_para_unidades_sin_exito_de_hoy():
    hoy = dt.date(2026, 9, 24)
    ultimos = [(101, "amazon_us", hoy), (202, "amazon_mx", hoy - dt.timedelta(days=1))]
    assert salud.unidades_atrasadas(ultimos, hoy) == [(202, "amazon_mx", "atraso")]


@pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="Postgres no disponible")
def test_episodio_persiste_reintenta_y_recupera_solo_con_principal(monkeypatch):
    """El acuse HTTP, el dedupe y la fuente se prueban contra PostgreSQL."""
    import psycopg
    from psycopg import sql as pgsql

    db = f"orbit_ads_salud_{socket.gethostname().lower().replace('-', '_')}_{os.getpid()}"
    admin = psycopg.connect(_test_dsn(), autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(_test_dsn(), dbname=db, autocommit=True)
        conn.execute(SQL)
        conn.execute(SQL40)
        conn.execute(SQL41)
        conn.execute("SET ROLE app_ingest")
        monkeypatch.setattr(salud.notifica, "canal_activo", lambda: True)
        respuestas = iter([False, True, True, True])
        textos = []

        def enviar(texto):
            textos.append(texto)
            return next(respuestas)

        monkeypatch.setattr(salud.notifica, "_envia_texto", enviar)

        def run(source, ok, status, reporte="campaigns"):
            run_id = conn.execute(
                "INSERT INTO ingest_run (source, finished_at, ok) "
                "VALUES (%s, now(), %s) RETURNING id",
                (source, ok),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO ads_report_result "
                "(ingest_run_id, profile_id, platform, report_name, status, reason) "
                "VALUES (%s, 101, 'amazon_us', %s, %s, %s)",
                (run_id, reporte, status, "fallo" if status == "failed" else None),
            )
            salud.procesar_run(conn, run_id)

        run(salud.SOURCE, False, "failed")
        assert conn.execute(
            "SELECT alert_attempts, alert_sent_at FROM ads_ingest_incident"
        ).fetchone() == (1, None)
        estado = salud.bloque_salud(conn, "amazon_us")
        assert estado["incidentes"][0]["estado_entrega"] == "pending"
        assert estado["reportes"][0]["metric_date"] is None
        assert estado["reportes"][0]["ultimo_estado"] == "failed"
        run("amazon_ads_products_v3", True, "written", "productos")
        assert len(textos) == 1
        run(salud.SOURCE, False, "failed")
        assert conn.execute("SELECT count(*) FROM ads_ingest_incident").fetchone()[0] == 1
        run(salud.SOURCE, True, "written")
        assert "recuperada" in textos[-1]
        assert conn.execute(
            "SELECT alert_attempts, recovery_attempts, closed_at IS NOT NULL "
            "FROM ads_ingest_incident"
        ).fetchone() == (2, 1, True)
        assert salud.bloque_salud(conn, "amazon_us")["incidentes"] == []

        dia_siguiente = dt.datetime.combine(
            dt.datetime.now(dt.UTC).date() + dt.timedelta(days=1),
            dt.time(10, 30),
            tzinfo=dt.UTC,
        )
        salud.comprobar_atraso(conn, ahora=dia_siguiente)
        assert "atrasada" in textos[-1]
        assert conn.execute("SELECT count(*) FROM ads_ingest_incident").fetchone()[0] == 2

        monkeypatch.setattr(salud.notifica, "canal_activo", lambda: False)
        run(salud.SOURCE, True, "written")
        run(salud.SOURCE, False, "failed")
        assert conn.execute(
            "SELECT alert_sent_at, alert_attempts FROM ads_ingest_incident "
            "WHERE tipo = 'fallo' ORDER BY id DESC LIMIT 1"
        ).fetchone() == (None, 0)
        run(salud.SOURCE, True, "written")
        assert len(textos) == 4
        assert conn.execute(
            "SELECT recovery_sent_at, closed_at IS NOT NULL FROM ads_ingest_incident "
            "WHERE tipo = 'fallo' ORDER BY id DESC LIMIT 1"
        ).fetchone() == (None, True)

        run_id_global = conn.execute(
            "INSERT INTO ingest_run (source, finished_at, ok) "
            "VALUES (%s, now(), false) RETURNING id",
            (salud.SOURCE,),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO ads_report_result (ingest_run_id, status, reason) "
            "VALUES (%s, 'global_failed', 'perfil no identificado')",
            (run_id_global,),
        )
        salud.procesar_run(conn, run_id_global)
        assert salud.bloque_salud(conn, "amazon_us")["incidentes"][0]["platform"] is None
        assert salud.bloque_salud(conn, "amazon_mx")["incidentes"][0]["platform"] is None

        from app.ads import reports

        def credenciales_rotas():
            raise ValueError("credenciales ausentes")

        monkeypatch.setenv(
            "ORBIT_DSN_INGEST", psycopg.conninfo.make_conninfo(_test_dsn(), dbname=db)
        )
        monkeypatch.setattr(reports.AdsCredentials, "from_secrets_dir", credenciales_rotas)
        anteriores = conn.execute(
            "SELECT count(*) FROM ads_report_result WHERE status = 'global_failed'"
        ).fetchone()[0]
        assert reports.main([]) == 1
        assert (
            conn.execute(
                "SELECT count(*) FROM ads_report_result WHERE status = 'global_failed'"
            ).fetchone()[0]
            == anteriores + 1
        )
        incidentes_antes = conn.execute("SELECT count(*) FROM ads_ingest_incident").fetchone()[0]
        assert reports.main(["--productos"]) == 1
        assert (
            conn.execute("SELECT source FROM ingest_run ORDER BY id DESC LIMIT 1").fetchone()[0]
            == "amazon_ads_products_v3"
        )
        assert (
            conn.execute("SELECT count(*) FROM ads_ingest_incident").fetchone()[0]
            == incidentes_antes
        )

        run_mixta = conn.execute(
            "INSERT INTO ingest_run (source, finished_at, ok) "
            "VALUES (%s, now(), true) RETURNING id",
            (salud.SOURCE,),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO ads_report_result "
            "(ingest_run_id, profile_id, platform, report_name, status) "
            "VALUES (%s, 101, 'amazon_us', 'campaigns', 'written')",
            (run_mixta,),
        )
        conn.execute(
            "INSERT INTO ads_report_result "
            "(ingest_run_id, platform, status, reason) "
            "VALUES (%s, 'amazon_us', 'rejected', 'profileId invalido')",
            (run_mixta,),
        )
        salud.procesar_run(conn, run_mixta)
        assert (
            conn.execute(
                "SELECT count(*) FROM ads_ingest_incident "
                "WHERE profile_id IS NULL AND closed_at IS NULL"
            ).fetchone()[0]
            == 1
        )

        # Un fallo nuevo no puede perderse mientras sigue pendiente el aviso
        # de recuperacion del episodio anterior del mismo perfil.
        monkeypatch.setattr(salud.notifica, "canal_activo", lambda: False)
        run(salud.SOURCE, True, "written")
        monkeypatch.setattr(salud.notifica, "canal_activo", lambda: True)
        salud.entregar_pendientes(conn, enviar=lambda _texto: True)
        nuevos_textos = []
        nuevas_respuestas = iter([True, False, True, True])

        def nuevo_envio(texto):
            nuevos_textos.append(texto)
            return next(nuevas_respuestas)

        monkeypatch.setattr(salud.notifica, "_envia_texto", nuevo_envio)
        run(salud.SOURCE, False, "failed")
        run(salud.SOURCE, True, "written")
        episodio_anterior = conn.execute(
            "SELECT id FROM ads_ingest_incident "
            "WHERE profile_id = 101 AND tipo = 'fallo' ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        assert conn.execute(
            "SELECT recovered_at IS NOT NULL, recovery_sent_at IS NULL, closed_at "
            "FROM ads_ingest_incident WHERE id = %s",
            (episodio_anterior,),
        ).fetchone() == (True, True, None)
        run(salud.SOURCE, False, "failed")
        assert (
            conn.execute(
                "SELECT count(*) FROM ads_ingest_incident "
                "WHERE profile_id = 101 AND tipo = 'fallo' "
                "AND recovered_at IS NULL AND closed_at IS NULL"
            ).fetchone()[0]
            == 1
        )
        assert conn.execute(
            "SELECT recovery_cancelled_at IS NOT NULL, recovery_sent_at, "
            "closed_at IS NOT NULL FROM ads_ingest_incident WHERE id = %s",
            (episodio_anterior,),
        ).fetchone() == (True, None, True)
        assert any("fallo de ingesta principal" in texto for texto in nuevos_textos[2:])
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()
