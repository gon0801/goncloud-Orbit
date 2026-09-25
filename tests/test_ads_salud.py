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


def _base_a3d(monkeypatch, nombre, respuestas):
    """Fixture A.3d: DB temporal con 0001+0040+0041, rol app_ingest y canal
    con respuestas programadas. Devuelve (conn, textos, admin) para cerrar."""
    import psycopg
    from psycopg import sql as pgsql

    db = f"orbit_a3d_{nombre}_{os.getpid()}"
    admin = psycopg.connect(_test_dsn(), autocommit=True)
    admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
    conn = psycopg.connect(_test_dsn(), dbname=db, autocommit=True)
    conn.execute(SQL)
    conn.execute(SQL40)
    conn.execute(SQL41)
    conn.execute("SET ROLE app_ingest")
    monkeypatch.setattr(salud.notifica, "canal_activo", lambda: True)
    textos = []
    cola = list(respuestas)

    def enviar(texto):
        textos.append(texto)
        assert cola, "envio no programado"
        return cola.pop(0)

    monkeypatch.setattr(salud.notifica, "_envia_texto", enviar)
    return conn, textos, admin, db


def _cerrar_a3d(conn, admin, db):
    from psycopg import sql as pgsql

    conn.close()
    admin.execute(pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db)))
    admin.close()


def _run(conn, source, ok, status, reporte="campaigns", perfil=101, plataforma="amazon_us"):
    run_id = conn.execute(
        "INSERT INTO ingest_run (source, finished_at, ok) VALUES (%s, now(), %s) RETURNING id",
        (source, ok),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO ads_report_result "
        "(ingest_run_id, profile_id, platform, report_name, status, reason) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (run_id, perfil, plataforma, reporte, status, "fallo" if status == "failed" else None),
    )
    salud.procesar_run(conn, run_id)
    return run_id


@pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="Postgres no disponible")
def test_a3d_fallo_nuevo_tras_recovery_pendiente_no_se_pierde(monkeypatch):
    """A.3d secuencia 1 (ya cubierta por 13af38e, se deja sellada): fallo ->
    aviso ok -> exito -> recovery pendiente (canal falla) -> fallo nuevo
    cancela el recovery viejo y abre episodio nuevo con su aviso."""
    conn, textos, admin, db = _base_a3d(monkeypatch, "fallo", [True, False, True])
    try:
        _run(conn, salud.SOURCE, False, "failed")
        _run(conn, salud.SOURCE, True, "written")
        viejo = conn.execute(
            "SELECT id FROM ads_ingest_incident WHERE tipo = 'fallo' ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        assert conn.execute(
            "SELECT recovered_at IS NOT NULL, recovery_sent_at IS NULL, closed_at IS NULL"
            " FROM ads_ingest_incident WHERE id = %s",
            (viejo,),
        ).fetchone() == (True, True, True)
        _run(conn, salud.SOURCE, False, "failed")
        assert conn.execute("SELECT count(*) FROM ads_ingest_incident").fetchone()[0] == 2
        assert conn.execute(
            "SELECT recovery_cancelled_at IS NOT NULL, closed_at IS NOT NULL"
            " FROM ads_ingest_incident WHERE id = %s",
            (viejo,),
        ).fetchone() == (True, True)
        assert (
            conn.execute(
                "SELECT alert_sent_at IS NOT NULL FROM ads_ingest_incident"
                " WHERE tipo = 'fallo' AND closed_at IS NULL"
            ).fetchone()[0]
            is True
        )
        assert any("fallo de ingesta principal" in t for t in textos[2:])
    finally:
        _cerrar_a3d(conn, admin, db)


@pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="Postgres no disponible")
def test_a3d_atraso_nuevo_tras_recovery_pendiente_no_se_pierde(monkeypatch):
    """A.3d secuencia 2 (el hueco): atraso -> aviso ok -> exito -> recovery
    pendiente -> atraso nuevo debe cancelar el recovery viejo y abrir
    episodio nuevo. Antes del fix simetrico, el ABRIR choca con el episodio
    abierto (ON CONFLICT DO NOTHING) y el atraso nuevo se pierde."""
    conn, textos, admin, db = _base_a3d(monkeypatch, "atraso", [True, False, True])
    try:
        conn.execute(
            "INSERT INTO ingest_run (source, finished_at, ok) "
            "VALUES (%s, now() - interval '1 day', true)",
            (salud.SOURCE,),
        )
        vieja = conn.execute("SELECT id FROM ingest_run ORDER BY id DESC LIMIT 1").fetchone()[0]
        conn.execute(
            "INSERT INTO ads_report_result "
            "(ingest_run_id, profile_id, platform, report_name, status)"
            " VALUES (%s, 101, 'amazon_us', 'campaigns', 'written')",
            (vieja,),
        )
        hoy_11 = dt.datetime.combine(dt.datetime.now(dt.UTC).date(), dt.time(11, 0), tzinfo=dt.UTC)
        manana_11 = hoy_11 + dt.timedelta(days=1)
        salud.comprobar_atraso(conn, ahora=hoy_11)
        assert (
            conn.execute(
                "SELECT count(*) FROM ads_ingest_incident "
                "WHERE tipo = 'atraso' AND closed_at IS NULL"
            ).fetchone()[0]
            == 1
        )
        _run(conn, salud.SOURCE, True, "written")
        viejo = conn.execute(
            "SELECT id FROM ads_ingest_incident WHERE tipo = 'atraso' ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        assert conn.execute(
            "SELECT recovered_at IS NOT NULL, recovery_sent_at IS NULL, closed_at IS NULL"
            " FROM ads_ingest_incident WHERE id = %s",
            (viejo,),
        ).fetchone() == (True, True, True)
        salud.comprobar_atraso(conn, ahora=manana_11)
        assert conn.execute("SELECT count(*) FROM ads_ingest_incident").fetchone()[0] == 2
        assert conn.execute(
            "SELECT recovery_cancelled_at IS NOT NULL, closed_at IS NOT NULL"
            " FROM ads_ingest_incident WHERE id = %s",
            (viejo,),
        ).fetchone() == (True, True)
        assert any("atrasada" in t for t in textos[2:])
    finally:
        _cerrar_a3d(conn, admin, db)


def test_a3d_resumen_omitido_pre_1030_sin_tocar_db():
    """Antes de las 10:30 UTC el chequeo se omite y lo dice (sin DB)."""
    antes = dt.datetime(2026, 9, 24, 9, 0, tzinfo=dt.UTC)
    assert salud.comprobar_atraso(object(), ahora=antes) == {
        "chequeo": "omitido",
        "motivo": "pre_1030",
        "ahora": "2026-09-24T09:00:00+00:00",
    }


@pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="Postgres no disponible")
def test_a3d_resumen_ejecutado_cuenta_unidades_y_aperturas(monkeypatch):
    """Tras las 10:30 el resumen trae unidades revisadas y episodios abiertos."""
    conn, _textos, admin, db = _base_a3d(monkeypatch, "resumen", [True])
    try:
        tarde = dt.datetime.combine(dt.datetime.now(dt.UTC).date(), dt.time(11, 0), tzinfo=dt.UTC)
        assert salud.comprobar_atraso(conn, ahora=tarde) == {
            "chequeo": "ejecutado",
            "ahora": tarde.isoformat(),
            "unidades": 0,
            "episodios_abiertos": 1,
        }
    finally:
        _cerrar_a3d(conn, admin, db)


@pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="Postgres no disponible")
def test_a3d_main_heartbeat_omitido_pre_1030(monkeypatch, capsys):
    """main() antes de las 10:30 UTC: sale 0 e imprime omitido. Hora fija."""
    import psycopg

    class _Reloj(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return dt.datetime(2026, 9, 24, 9, 0, tzinfo=dt.UTC)

    monkeypatch.setattr(salud.dt, "datetime", _Reloj)
    conn, _textos, admin, db = _base_a3d(monkeypatch, "main1", [])
    try:
        monkeypatch.setenv("ORBIT_DSN_INGEST", "postgres://test-inyectado/db")
        monkeypatch.setattr(salud, "connect", lambda _dsn: psycopg.connect(_test_dsn(), dbname=db))
        assert salud.main([]) == 0
        out, _err = capsys.readouterr()
        assert out == "ads-salud chequeo=omitido motivo=pre_1030 ahora=2026-09-24T09:00:00+00:00\n"
    finally:
        _cerrar_a3d(conn, admin, db)


@pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="Postgres no disponible")
def test_a3d_main_heartbeat_ejecutado_tras_1030(monkeypatch, capsys):
    """main() tras las 10:30 UTC: sale 0 e imprime ejecutado con conteos.
    Hora fija; el reloj congelado solo afecta a Python (SQL usa now())."""
    import psycopg

    class _Reloj(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return dt.datetime(2026, 9, 24, 11, 0, tzinfo=dt.UTC)

    monkeypatch.setattr(salud.dt, "datetime", _Reloj)
    conn, textos, admin, db = _base_a3d(monkeypatch, "main2", [True])
    try:
        monkeypatch.setenv("ORBIT_DSN_INGEST", "postgres://test-inyectado/db")
        monkeypatch.setattr(salud, "connect", lambda _dsn: psycopg.connect(_test_dsn(), dbname=db))
        assert salud.main([]) == 0
        out, _err = capsys.readouterr()
        assert out == (
            "ads-salud chequeo=ejecutado ahora=2026-09-24T11:00:00+00:00 unidades=0"
            " episodios_abiertos=1\n"
        )
        assert len(textos) == 1
    finally:
        _cerrar_a3d(conn, admin, db)
