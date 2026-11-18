"""Tests de A.5: evaluacion pura de alertas + digest (acta 0.5 D3 + §2).

(a) PUROS: cada tipo dispara SOLO en flanco con fixtures; sin datos
    no dispara; fixture sin-texto no rompe.
(b) INTEGRACION: PG desechable; flanco contra alertas abiertas
    (no duplica; resuelve al bajar), DISTINCT ON en reviews (0027),
    CLI `reputacion alertas` ejecutado de verdad.
(c) DIGEST: el resumen del ciclo ya no carga reputacion (ARREGLOS 03
    D-K: las alertas salen solas en la revision de las 10:30).
"""

from __future__ import annotations

import datetime as dt

import pytest
from test_reputacion import _skip_db, db_reputacion

from app import cli as app_cli
from app.notifica import digest_ciclo
from app.reputacion_alertas import (
    ejecuta_alertas,
    evalua_caida_rating,
    evalua_rating_bajo,
    evalua_reclamos_suben,
    evalua_resena_1,
    evalua_salud_cuenta,
    evalua_y_persiste,
)

HOY = dt.date(2026, 9, 8)
FETCH = dt.datetime(2026, 9, 8, 9, 30, tzinfo=dt.UTC)


def _snap(conn, platform, external_id, metric_date, rating):
    conn.execute(
        "INSERT INTO reputation_snapshot"
        " (platform, external_id, alcance, metric_date, rating, fetched_at)"
        " VALUES (%s, %s, 'listing', %s, %s, %s)",
        (platform, external_id, metric_date, rating, FETCH),
    )


def _seller(conn, metric_date, level_id, disputas_total):
    conn.execute(
        "INSERT INTO seller_reputation_snapshot"
        " (platform, metric_date, level_id, disputas_total, fetched_at)"
        " VALUES ('meli', %s, %s, %s, %s)",
        (metric_date, level_id, disputas_total, FETCH),
    )


def _review(conn, item_id, review_id, rate, publicada, observed_at):
    conn.execute(
        "INSERT INTO review_event"
        " (platform, external_id, review_external_id, rating, publicada,"
        "  fetched_at, observed_at)"
        " VALUES ('meli', %s, %s, %s, %s, %s, %s)",
        (item_id, review_id, rate, publicada, FETCH, observed_at),
    )


# ---------------------------------------------------------------------------
# rating_bajo (aviso): ultimo snapshot < 4.2, en flanco
# ---------------------------------------------------------------------------


def test_rating_bajo_dispara_bajo_umbral():
    alerta = evalua_rating_bajo("meli", "MLM1", 4.1)
    assert alerta is not None
    assert alerta.tipo == "rating_bajo"
    assert alerta.severidad == "aviso"
    assert alerta.platform == "meli" and alerta.external_id == "MLM1"
    assert "4.1" in alerta.mensaje


def test_rating_bajo_no_dispara_en_umbral_ni_arriba():
    assert evalua_rating_bajo("meli", "MLM1", 4.2) is None
    assert evalua_rating_bajo("amazon_mx", "B0X", 4.8) is None


def test_rating_bajo_sin_datos_no_dispara():
    assert evalua_rating_bajo("meli", "MLM1", None) is None


# ---------------------------------------------------------------------------
# resena_1 (critica): MeLi rate==1 publicada; Amazon jamas en v1
# ---------------------------------------------------------------------------


def test_resena_1_dispara_rate_1_publicada():
    alerta = evalua_resena_1("MLM1", "R1", 1, True)
    assert alerta is not None
    assert alerta.tipo == "resena_1"
    assert alerta.severidad == "critica"
    assert "R1" in alerta.mensaje and "MLM1" in alerta.mensaje


def test_resena_1_no_dispara_otros_rates_ni_oculta():
    assert evalua_resena_1("MLM1", "R2", 2, True) is None
    assert evalua_resena_1("MLM1", "R1", 1, False) is None
    assert evalua_resena_1("MLM1", "R9", None, True) is None


def test_resena_1_sin_texto_no_rompe():
    # El mensaje jamas incluye texto externo: solo ids (seguro Telegram).
    alerta = evalua_resena_1("MLM1", "R1", 1, True)
    assert alerta is not None and "<" not in alerta.mensaje and ">" not in alerta.mensaje


# ---------------------------------------------------------------------------
# reclamos_suben (aviso, cuenta): nuevas7d >= previas7d + 2
# ---------------------------------------------------------------------------


def test_reclamos_suben_dispara_con_mas_2():
    alerta = evalua_reclamos_suben(10, 7, 6)
    assert alerta is not None
    assert alerta.tipo == "reclamos_suben"
    assert alerta.severidad == "aviso"
    assert alerta.platform == "meli" and alerta.external_id is None


def test_reclamos_suben_no_dispara_sin_delta():
    assert evalua_reclamos_suben(8, 7, 6) is None  # nuevas=1, previas=1
    assert evalua_reclamos_suben(7, 7, 7) is None


def test_reclamos_suben_sin_datos_no_dispara():
    assert evalua_reclamos_suben(None, 7, 6) is None
    assert evalua_reclamos_suben(10, None, 6) is None
    assert evalua_reclamos_suben(10, 7, None) is None


# ---------------------------------------------------------------------------
# caida_rating (aviso): baja >= 0.3 (MeLi 7d / Amazon N vs N-1)
# ---------------------------------------------------------------------------


def test_caida_rating_dispara_con_baja_03():
    alerta = evalua_caida_rating("meli", "MLM1", 4.5, 4.8)
    assert alerta is not None
    assert alerta.tipo == "caida_rating"
    assert alerta.severidad == "aviso"


def test_caida_rating_no_dispara_con_baja_menor_ni_subida():
    assert evalua_caida_rating("meli", "MLM1", 4.6, 4.8) is None
    assert evalua_caida_rating("amazon_us", "B0X", 4.9, 4.8) is None


def test_caida_rating_sin_datos_no_dispara():
    assert evalua_caida_rating("meli", "MLM1", None, 4.8) is None
    assert evalua_caida_rating("meli", "MLM1", 4.5, None) is None


# ---------------------------------------------------------------------------
# salud_cuenta (aviso; info si level no mapeado)
# ---------------------------------------------------------------------------


def test_salud_cuenta_dispara_si_empeora():
    alerta = evalua_salud_cuenta("4_light_green", "5_green")
    assert alerta is not None
    assert alerta.tipo == "salud_cuenta" and alerta.severidad == "aviso"


def test_salud_cuenta_no_dispara_si_mejora_o_igual():
    assert evalua_salud_cuenta("5_green", "4_light_green") is None
    assert evalua_salud_cuenta("5_green", "5_green") is None


def test_salud_cuenta_level_desconocido_es_info_visible():
    alerta = evalua_salud_cuenta("6_platinum", "5_green")
    assert alerta is not None
    assert alerta.severidad == "info"
    assert "6_platinum" in (alerta.mensaje or "")


def test_salud_cuenta_sin_datos_no_dispara():
    assert evalua_salud_cuenta(None, "5_green") is None
    assert evalua_salud_cuenta("4_light_green", None) is None


def test_mapa_level_orden_completo():
    # peor -> mejor: 1_red < 2_orange < 3_yellow < 4_light_green < 5_green
    assert evalua_salud_cuenta("1_red", "2_orange") is not None
    assert evalua_salud_cuenta("3_yellow", "1_red") is None


# ---------------------------------------------------------------------------
# Flanco contra DB: abre una vez, no duplica, resuelve al bajar
# ---------------------------------------------------------------------------


@_skip_db
def test_flanco_rating_bajo_abre_y_no_duplica():
    with db_reputacion("orbit_repa5") as (conn, _dsn):
        _snap(conn, "meli", "MLM1", HOY, 4.1)
        primero = evalua_y_persiste(conn, HOY)
        segundo = evalua_y_persiste(conn, HOY)
        assert primero["abiertas"] == 1
        assert segundo["abiertas"] == 0
        assert conn.execute("SELECT count(*) FROM reputation_alert").fetchone()[0] == 1


@_skip_db
def test_flanco_rating_bajo_resuelve_al_subir():
    with db_reputacion("orbit_repa5") as (conn, _dsn):
        _snap(conn, "meli", "MLM1", HOY - dt.timedelta(days=1), 4.1)
        assert evalua_y_persiste(conn, HOY - dt.timedelta(days=1))["abiertas"] == 1
        _snap(conn, "meli", "MLM1", HOY, 4.6)
        resultado = evalua_y_persiste(conn, HOY)
        assert resultado["resueltas"] == 1
        fila = conn.execute("SELECT resolved, resolved_at FROM reputation_alert").fetchone()
        assert fila[0] is True and fila[1] is not None


@_skip_db
def test_sin_datos_no_toca_abiertas():
    with db_reputacion("orbit_repa5") as (conn, _dsn):
        # Abierta previa de entidad sin snapshots: intacta (regla 3:
        # ausencia no es evidencia de mejora; sin DELETE: trigger
        # append-only, se siembra la alerta directo).
        conn.execute(
            "INSERT INTO reputation_alert"
            " (platform, external_id, tipo, severidad, mensaje)"
            " VALUES ('meli', 'MLM-FANTASMA', 'rating_bajo', 'aviso', 'x')"
        )
        resultado = evalua_y_persiste(conn, HOY)
        assert resultado["abiertas"] == 0 and resultado["resueltas"] == 0
        assert conn.execute("SELECT every(NOT resolved) FROM reputation_alert").fetchone()[0]


@_skip_db
def test_resena_1_distinct_on_y_una_sola_alerta():
    with db_reputacion("orbit_repa5") as (conn, _dsn):
        # 0027: N filas por review; el lector toma la mas reciente (R2-2).
        _review(conn, "MLM1", "R1", 5, True, FETCH - dt.timedelta(days=1))
        _review(conn, "MLM1", "R1", 1, True, FETCH)
        primero = evalua_y_persiste(conn, HOY)
        segundo = evalua_y_persiste(conn, HOY)
        assert primero["abiertas"] == 1
        assert segundo["abiertas"] == 0
        fila = conn.execute("SELECT severidad, mensaje FROM reputation_alert").fetchone()
        assert fila[0] == "critica" and "R1" in fila[1]


@_skip_db
def test_resena_1_segunda_review_del_mismo_item_si_abre():
    with db_reputacion("orbit_repa5") as (conn, _dsn):
        _review(conn, "MLM1", "R1", 1, True, FETCH)
        assert evalua_y_persiste(conn, HOY)["abiertas"] == 1
        _review(conn, "MLM1", "R2", 1, True, FETCH + dt.timedelta(hours=1))
        resultado = evalua_y_persiste(conn, HOY)
        assert resultado["abiertas"] == 1
        assert conn.execute("SELECT count(*) FROM reputation_alert").fetchone()[0] == 2


@_skip_db
def test_resena_1_moderada_no_alerta():
    """Kimi A.R H1: review 1 estrella moderada (ultima fila publicada
    FALSE) no dispara, aunque su fila vieja calificaba."""
    with db_reputacion("orbit_repa5") as (conn, _dsn):
        _review(conn, "MLM1", "R9", 1, True, FETCH - dt.timedelta(days=1))
        _review(conn, "MLM1", "R9", 1, False, FETCH)
        resultado = evalua_y_persiste(conn, HOY)
        assert resultado["abiertas"] == 0
        assert "resena_1" not in {a["tipo"] for a in resultado["alertas"]}


@_skip_db
def test_caida_rating_meli_ventana_7d():
    with db_reputacion("orbit_repa5") as (conn, _dsn):
        _snap(conn, "meli", "MLM1", HOY - dt.timedelta(days=7), 4.8)
        _snap(conn, "meli", "MLM1", HOY, 4.5)
        resultado = evalua_y_persiste(conn, HOY)
        tipos = {a["tipo"] for a in resultado["alertas"]}
        assert "caida_rating" in tipos


@_skip_db
def test_caida_rating_amazon_n_vs_n1():
    with db_reputacion("orbit_repa5") as (conn, _dsn):
        _snap(conn, "amazon_mx", "B0X", HOY - dt.timedelta(days=7), 4.9)
        _snap(conn, "amazon_mx", "B0X", HOY, 4.5)
        resultado = evalua_y_persiste(conn, HOY)
        tipos = {a["tipo"] for a in resultado["alertas"]}
        assert "caida_rating" in tipos
        # Y rating_bajo NO (4.5 >= 4.2): granularidad por tipo.
        assert "rating_bajo" not in tipos


@_skip_db
def test_reclamos_suben_requiere_3_snapshots():
    with db_reputacion("orbit_repa5") as (conn, _dsn):
        _seller(conn, HOY - dt.timedelta(days=14), "5_green", 6)
        _seller(conn, HOY - dt.timedelta(days=7), "5_green", 7)
        assert evalua_y_persiste(conn, HOY)["abiertas"] == 0
        _seller(conn, HOY, "5_green", 10)
        resultado = evalua_y_persiste(conn, HOY)
        assert "reclamos_suben" in {a["tipo"] for a in resultado["alertas"]}


@_skip_db
def test_salud_cuenta_empeora_y_resuelve_al_recuperar():
    with db_reputacion("orbit_repa5") as (conn, _dsn):
        _seller(conn, HOY - dt.timedelta(days=1), "5_green", 0)
        _seller(conn, HOY, "4_light_green", 0)
        assert "salud_cuenta" in {a["tipo"] for a in evalua_y_persiste(conn, HOY)["alertas"]}
        _seller(conn, HOY + dt.timedelta(days=1), "5_green", 0)
        assert evalua_y_persiste(conn, HOY + dt.timedelta(days=1))["resueltas"] == 1


@_skip_db
def test_dry_run_cero_writes():
    with db_reputacion("orbit_repa5") as (conn, _dsn):
        _snap(conn, "meli", "MLM1", HOY, 4.1)
        resultado = evalua_y_persiste(conn, HOY, dry_run=True)
        assert resultado["abiertas"] == 1 and resultado["dry_run"] is True
        assert conn.execute("SELECT count(*) FROM reputation_alert").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# Digest: el resumen del ciclo ya no lleva reputacion (ARREGLOS 03 D-K)
# ---------------------------------------------------------------------------

_RESUMEN_MINIMO = {
    "cycle_id": 7,
    "plataforma": "meli",
    "status": "ok",
    "decisions_count": 0,
    "apply": {},
}


def test_digest_sin_bloque_formato_intacto():
    assert digest_ciclo(_RESUMEN_MINIMO) == digest_ciclo({**_RESUMEN_MINIMO, "reputacion": []})
    assert "reputacion" not in digest_ciclo(_RESUMEN_MINIMO)


# ---------------------------------------------------------------------------
# CLI reputacion alertas (ORBIT_DSN_DECIDE; AC4 same-day)
# ---------------------------------------------------------------------------


@_skip_db
def test_cli_alertas_dry_run_real(monkeypatch, capsys):
    import json as json_mod

    with db_reputacion("orbit_repa5") as (conn, dsn_db):
        _snap(conn, "meli", "MLM1", HOY - dt.timedelta(days=1), 4.1)
        monkeypatch.setenv("ORBIT_DSN_DECIDE", dsn_db)
        codigo = app_cli.main(["reputacion", "alertas", "--fecha", "2026-09-08", "--dry-run"])
        assert codigo == 0
        assert json_mod.loads(capsys.readouterr().out)["abiertas"] == 1
        assert conn.execute("SELECT count(*) FROM reputation_alert").fetchone()[0] == 0


@_skip_db
def test_ejecuta_alertas_fin_a_fin(monkeypatch, capsys):
    import json as json_mod

    with db_reputacion("orbit_repa5") as (conn, dsn_db):
        _snap(conn, "meli", "MLM1", HOY - dt.timedelta(days=1), 4.1)
        monkeypatch.setenv("ORBIT_DSN_DECIDE", dsn_db)
        assert ejecuta_alertas(fecha="2026-09-08") == 0
        resumen = json_mod.loads(capsys.readouterr().out)
        assert resumen["abiertas"] == 1 and resumen["ok"] is True
        assert conn.execute("SELECT count(*) FROM reputation_alert").fetchone()[0] == 1


def test_cli_alertas_exit_2_sin_dsn_ni_fecha(monkeypatch, capsys):
    monkeypatch.delenv("ORBIT_DSN_DECIDE", raising=False)
    assert app_cli.main(["reputacion", "alertas"]) == 2
    assert "ORBIT_DSN_DECIDE" in capsys.readouterr().err
    monkeypatch.setenv("ORBIT_DSN_DECIDE", "postgresql://u:p@h/db")
    assert app_cli.main(["reputacion", "alertas", "--fecha", "ayer"]) == 2


# ---------------------------------------------------------------------------
# ARREGLOS 03 D-K: la revision de las 10:30 avisa si la lectura no corrio
# ---------------------------------------------------------------------------


@_skip_db
def test_arreglos03_dk_lectura_que_no_corrio_se_avisa(tmp_path, monkeypatch, capsys):
    """Falla ya ocurrida: del 28-sep al 16-oct no hubo ninguna lectura de
    reputacion (ni fallida) y nadie se entero."""
    from app import notifica
    from app.reputacion import SOURCE_MELI, ejecuta_snapshot
    from app.reputacion_alertas import lectura_de_hoy

    hoy = dt.datetime.now(dt.UTC).date()
    with db_reputacion("orbit_repdk") as (conn, dsn_db):
        conn.execute(
            "INSERT INTO ingest_run (source, started_at, finished_at, rows_written, ok)"
            " VALUES (%s, now() - interval '1 day', now() - interval '1 day', 3, TRUE)",
            (SOURCE_MELI,),
        )
        monkeypatch.setenv("ORBIT_DSN_DECIDE", dsn_db)
        monkeypatch.setenv("ORBIT_DSN_INGEST", dsn_db)
        enviados: list[str] = []
        monkeypatch.setattr(notifica, "envia_aviso", lambda texto: enviados.append(texto) or True)

        # Hoy no corrio la lectura: la revision lo dice y sale 1 (cron-avisa avisa).
        assert lectura_de_hoy(conn, hoy) == "ausente"
        assert ejecuta_alertas() == 1
        assert "no corrió" in capsys.readouterr().err

        # La lectura se cae antes de abrir su corrida (sin llaves): deja una
        # corrida fallida de hoy, no se confunde con una que no corrio.
        assert ejecuta_snapshot("meli", secrets_dir=tmp_path) == 1
        fallidas = conn.execute(
            "SELECT count(*) FROM ingest_run WHERE source = %s AND ok IS FALSE"
            " AND started_at >= date_trunc('day', now())",
            (SOURCE_MELI,),
        ).fetchone()[0]
        assert fallidas == 1
        capsys.readouterr()

        # Con la corrida fallida de hoy, ese punto no hace salir 1: la falla ya
        # la aviso cron-avisa a las 09:30.
        assert lectura_de_hoy(conn, hoy) == "fallida"
        assert ejecuta_alertas() == 0
        assert "no corrió" not in capsys.readouterr().err
        assert enviados == []  # sin alertas pendientes no se manda nada


@_skip_db
def test_arreglos03_dk_migracion_avisada_y_su_reversa():
    """Datos de produccion: la migracion marca como avisadas las alertas que
    ya existen (salieron en el resumen del ciclo) y la reversa la quita."""
    from test_reputacion import ORDEN, ROOT, _migracion

    avisada = _migracion("arreglos03_reputacion_avisada")
    reversa = avisada.replace("_arreglos03_", "_reversa_arreglos03_", 1)
    texto = (ROOT / "migrations" / avisada).read_text(encoding="utf-8")
    (sonda,) = [
        linea.removeprefix("-- sonda-aplicada:").strip()
        for linea in texto.splitlines()[:10]
        if linea.startswith("-- sonda-aplicada:")
    ]
    sin_avisada = tuple(nombre for nombre in ORDEN if nombre != avisada)
    with db_reputacion("orbit_repdk", orden=sin_avisada) as (conn, _dsn):
        conn.execute(
            "INSERT INTO reputation_alert"
            " (platform, external_id, tipo, severidad, mensaje, created_at)"
            " VALUES ('meli', 'MLM1', 'resena_1', 'critica',"
            " '1 estrella nueva: review 9 en MLM1', '2026-10-17 10:30+00')"
        )
        assert conn.execute(sonda).fetchone()[0] is False

        conn.execute(texto)
        assert conn.execute(sonda).fetchone()[0] is True
        creada, avisada_at = conn.execute(
            "SELECT created_at, avisada_at FROM reputation_alert"
        ).fetchone()
        assert avisada_at == creada
        assert conn.execute(
            "SELECT has_column_privilege('app_decide', 'reputation_alert', 'avisada_at', 'UPDATE')"
        ).fetchone()[0]

        conn.execute((ROOT / "migrations" / reversa).read_text(encoding="utf-8"))
        assert conn.execute(sonda).fetchone()[0] is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
