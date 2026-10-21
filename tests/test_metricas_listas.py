"""Guardas de "métricas de ayer listas" (app.metricas_listas, ingesta-01 A.3).

Los cuatro comandos que deciden con las métricas del día anterior —cycle,
impulso-vigia, avisos-campana y jev-senales— consultan la guarda antes de
abrir nada: sin la corrida ok de hoy con filas de D-1 imprimen
`sin métricas de ayer: espera al siguiente horario` y salen 0, con cero
envelopes, cero escrituras y cero mensajes. Con la corrida lista corren;
`--forzar` salta la guarda. cycle además no repite un ciclo done/degraded
de hoy ni interrumpe uno running de menos de 3 h (un running más viejo es
huérfano y sí se reintenta: el rastro lo cierra al ganar el claim).

Postgres real (patron de tests/test_ingest_diario.py); el "corre" de cada
comando se stubbea (monkeypatch) porque lo que se prueba es la guarda, no el
interior de cada corrida.
"""

from __future__ import annotations

import datetime as dt
import os
import socket
import types
from pathlib import Path

import pytest
from test_schema import SQL, _postgres_obligatorio_ausente, _test_dsn

from app import metricas_listas

SQL40 = (Path(__file__).resolve().parents[1] / "migrations/0040_ads_report_result.sql").read_text()
SQL62 = (Path(__file__).resolve().parents[1] / "migrations/0062_bids02_placement.sql").read_text()

HOY = dt.date(2026, 10, 21)
AYER = HOY - dt.timedelta(days=1)

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)


class _R:
    def __init__(self, fila=None, filas=()):
        self._fila = fila
        self._filas = filas

    def fetchone(self):
        return self._fila

    def fetchall(self):
        return self._filas


class _ConnGuarda:
    """Responde las consultas de metricas_listas con plataformas canned."""

    def __init__(self, *, metricas=(), placements=()):
        self._metricas = set(metricas)
        self._placements = set(placements)

    def execute(self, sql, params=None):
        params = params or ()
        if "FROM ads_metric_observation" in sql:
            assert params[:3] == ("amazon_ads_reports_v3", HOY, AYER)
            return _R((1,) if params[3] in self._metricas else None)
        if "FROM ads_placement_observation" in sql:
            assert params[:3] == ("amazon_ads_placements_v3", HOY, AYER)
            return _R((1,) if params[3] in self._placements else None)
        raise AssertionError(f"SQL no esperado: {sql}")


def test_guarda_exige_cada_plataforma_y_placements_cuando_se_pide():
    conn = _ConnGuarda(metricas=("amazon_us",))
    assert metricas_listas.guarda(conn, ("amazon_us",), hoy=HOY) is True
    assert metricas_listas.guarda(conn, ("amazon_us", "amazon_mx"), hoy=HOY) is False
    assert metricas_listas.guarda(conn, ("amazon_us",), exigir_placements=True, hoy=HOY) is False
    conn2 = _ConnGuarda(metricas=("amazon_us",), placements=("amazon_us",))
    assert metricas_listas.guarda(conn2, ("amazon_us",), exigir_placements=True, hoy=HOY)
    # --forzar salta la guarda con todo faltante
    assert metricas_listas.guarda(_ConnGuarda(), ("amazon_us",), forzar=True, hoy=HOY)


def test_motivo_de_salto_con_conn_falso(monkeypatch):
    from app import cycle

    class _ConnCiclo:
        def __init__(self, fila):
            self._fila = fila

        def execute(self, sql, params=None):
            assert "FROM optimizer_cycle" in sql
            return _R(self._fila)

    def _motivo(fila):
        return cycle.motivo_de_salto(_ConnCiclo(fila), "amazon_us")

    assert _motivo(None) is None, "sin ciclo hoy: se corre"
    assert _motivo(("done", False)) is not None
    assert _motivo(("degraded", False)) is not None
    assert "ya hay un ciclo done hoy" in _motivo(("done", False))
    assert _motivo(("running", True)) is not None, "running de menos de 3 h"
    assert _motivo(("running", False)) is None, "running viejo = huerfano: se reintenta"
    assert _motivo(("failed", False)) is None
    assert _motivo(("skipped", False)) is None


def test_sql_de_metricas_listas_parsea_como_postgres():
    import pglast

    for nombre in ("_SQL_METRICAS_LISTAS", "_SQL_PLACEMENTS_LISTOS"):
        sql = getattr(metricas_listas, nombre).replace("%s", "NULL")
        assert pglast.parse_sql(sql), f"{nombre} no parseo"


@pytest.fixture
def conn_pg():
    import psycopg
    from psycopg import sql as pgsql
    from psycopg.conninfo import make_dsn

    dsn = _test_dsn()
    db = f"orbit_guarda_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        conn.execute(SQL)
        conn.execute(SQL40)
        conn.execute(SQL62)
        yield conn, make_dsn(dsn, dbname=db)
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def _run_ok(conn, source, *, ok=True):
    return conn.execute(
        "INSERT INTO ingest_run (source, ok, started_at, finished_at)"
        " VALUES (%s, %s, now(), now()) RETURNING id",
        (source, ok),
    ).fetchone()[0]


_contador_seed = iter(range(100000))


def _siembra_metricas(conn, plataformas=("amazon_us", "amazon_mx"), *, metric_date=AYER, ok=True):
    """Una corrida de metricas de HOY con filas de `metric_date` (default
    D-1) para cada plataforma pedida."""
    run = _run_ok(conn, "amazon_ads_reports_v3", ok=ok)
    moneda = {"amazon_us": "USD", "amazon_mx": "MXN"}
    for plataforma in plataformas:
        entidad = conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id) VALUES (%s, 'campaign', %s)"
            " RETURNING id",
            (plataforma, f"seed-{plataforma}-{next(_contador_seed)}"),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO ads_metric_observation"
            " (ad_entity_id, metric_date, observed_at, metric_currency,"
            "  cost, source_report_id, ingest_run_id)"
            " VALUES (%s, %s, now(), %s, 1.0, %s, %s)",
            (entidad, metric_date, moneda[plataforma], f"R-{plataforma}", run),
        )
    return run


def _siembra_placements(conn, plataformas=("amazon_us", "amazon_mx")):
    run = _run_ok(conn, "amazon_ads_placements_v3")
    moneda = {"amazon_us": "USD", "amazon_mx": "MXN"}
    for plataforma in plataformas:
        entidad = conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id) VALUES (%s, 'campaign', %s)"
            " RETURNING id",
            (plataforma, f"seed-pl-{plataforma}"),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO ads_placement_observation"
            " (platform, ad_entity_id, placement, metric_date, observed_at,"
            "  metric_currency, clicks, source_report_id)"
            " VALUES (%s, %s, 'arriba_de_busqueda', %s, now(), %s, 1, %s)",
            (plataforma, entidad, AYER, moneda[plataforma], f"RPL-{plataforma}"),
        )
    return run


# ---------------------------------------------------------------------------
# La consulta en sí (roja con el código de hoy: el módulo no existía)
# ---------------------------------------------------------------------------


@_skip_db
def test_metricas_de_ayer_listas_con_postgres_real(conn_pg):
    conn, _ = conn_pg
    assert metricas_listas.metricas_de_ayer_listas(conn, "amazon_us", hoy=HOY) is False
    _siembra_metricas(conn, plataformas=("amazon_us",))
    assert metricas_listas.metricas_de_ayer_listas(conn, "amazon_us", hoy=HOY) is True
    # MX nunca se sembro
    assert metricas_listas.metricas_de_ayer_listas(conn, "amazon_mx", hoy=HOY) is False
    # corrida ok de HOY cuyas filas NO son de D-1: no cuenta
    _siembra_metricas(conn, plataformas=("amazon_mx",), metric_date=HOY)
    assert metricas_listas.metricas_de_ayer_listas(conn, "amazon_mx", hoy=HOY) is False
    # y con D-1 de MX si
    _siembra_metricas(conn, plataformas=("amazon_mx",))
    assert metricas_listas.metricas_de_ayer_listas(conn, "amazon_mx", hoy=HOY) is True


# ---------------------------------------------------------------------------
# cycle
# ---------------------------------------------------------------------------


def _corre_cycle(conn_pg, monkeypatch, capsys, argv=("cycle", "--platform", "amazon_us")):
    from app import cli, cycle

    conn, dsn = conn_pg
    monkeypatch.setenv("ORBIT_DSN_DECIDE", dsn)
    llamados: list = []
    monkeypatch.setattr(
        cycle,
        "corre_ciclo",
        lambda *a, **k: (
            llamados.append((a, k)),
            types.SimpleNamespace(cycle_id=1, status="done", decisions_count=0, notes="{}"),
        )[1],
    )
    salida = cli.main(list(argv))
    return salida, llamados, capsys.readouterr().out


@_skip_db
def test_cycle_sin_metricas_no_abre_envelope(conn_pg, monkeypatch, capsys):
    salida, llamados, out = _corre_cycle(conn_pg, monkeypatch, capsys)
    conn, _ = conn_pg
    assert salida == 0
    assert llamados == [], "la guarda bloquea antes del orquestador"
    assert metricas_listas.TEXTO_ESPERA in out
    assert conn.execute("SELECT count(*) FROM optimizer_cycle").fetchone()[0] == 0


@_skip_db
def test_cycle_con_corrida_que_no_trae_d1_tampoco_corre(conn_pg, monkeypatch, capsys):
    conn, _ = conn_pg
    _siembra_metricas(conn, metric_date=HOY)  # filas de HOY, no de D-1
    salida, llamados, out = _corre_cycle(conn_pg, monkeypatch, capsys)
    assert salida == 0 and llamados == []
    assert metricas_listas.TEXTO_ESPERA in out


@_skip_db
def test_cycle_con_metricas_listas_corre(conn_pg, monkeypatch, capsys):
    conn, _ = conn_pg
    _siembra_metricas(conn)
    salida, llamados, out = _corre_cycle(conn_pg, monkeypatch, capsys)
    assert salida == 0
    assert len(llamados) == 1, "con D-1 de ambas plataformas el ciclo corre"
    assert metricas_listas.TEXTO_ESPERA not in out


@_skip_db
def test_cycle_forzar_corre_sin_metricas(conn_pg, monkeypatch, capsys):
    salida, llamados, _ = _corre_cycle(
        conn_pg, monkeypatch, capsys, ("cycle", "--platform", "amazon_us", "--forzar")
    )
    assert salida == 0 and len(llamados) == 1


@_skip_db
@pytest.mark.parametrize("status", ["done", "degraded"])
def test_cycle_no_repite_un_ciclo_cerrado_hoy(conn_pg, monkeypatch, capsys, status):
    conn, _ = conn_pg
    _siembra_metricas(conn)
    conn.execute(
        "INSERT INTO optimizer_cycle (motor, mode, platform, started_at, status)"
        " VALUES ('ads_optimizer', 'live', 'amazon_us', now(), %s)",
        (status,),
    )
    salida, llamados, out = _corre_cycle(conn_pg, monkeypatch, capsys)
    assert salida == 0 and llamados == []
    assert f"ya hay un ciclo {status} hoy" in out


@_skip_db
def test_cycle_respeta_un_running_reciente_y_reintenta_uno_viejo(conn_pg, monkeypatch, capsys):
    conn, _ = conn_pg
    _siembra_metricas(conn)
    for horas, debe_correr in ((1, False), (4, True)):
        conn.execute("DELETE FROM optimizer_cycle")
        conn.execute(
            "INSERT INTO optimizer_cycle (motor, mode, platform, started_at, status)"
            " VALUES ('ads_optimizer', 'live', 'amazon_us', now() - make_interval(hours => %s),"
            " 'running')",
            (horas,),
        )
        salida, llamados, out = _corre_cycle(conn_pg, monkeypatch, capsys)
        assert salida == 0
        assert (len(llamados) == 1) is debe_correr, f"running de {horas} h"
        if not debe_correr:
            assert "hace menos de 3 h" in out


@_skip_db
@pytest.mark.parametrize("status", ["failed", "skipped"])
def test_cycle_reintenta_fallidos_y_skipped_de_hoy(conn_pg, monkeypatch, capsys, status):
    conn, _ = conn_pg
    _siembra_metricas(conn)
    conn.execute(
        "INSERT INTO optimizer_cycle (motor, mode, platform, started_at, status)"
        " VALUES ('ads_optimizer', 'live', 'amazon_us', now(), %s)",
        (status,),
    )
    salida, llamados, _ = _corre_cycle(conn_pg, monkeypatch, capsys)
    assert salida == 0 and len(llamados) == 1, f"un ciclo {status} hoy no bloquea el reintento"


# ---------------------------------------------------------------------------
# impulso-vigia
# ---------------------------------------------------------------------------


def _corre_vigia(conn_pg, monkeypatch, capsys, argv=("impulso-vigia",)):
    from app import cli, impulso_io

    conn, dsn = conn_pg
    monkeypatch.setenv("ORBIT_DSN_ADMIN", dsn)
    monkeypatch.setattr(impulso_io, "_SQL_VIVOS", "SELECT 1 AS id, 'amazon_mx' AS platform")
    llamados: list = []
    monkeypatch.setattr(impulso_io, "vigila", lambda *a, **k: (llamados.append((a, k)), [])[1])
    salida = cli.main(list(argv))
    return salida, llamados, capsys.readouterr().out


@_skip_db
def test_vigia_sin_metricas_no_abre_nada(conn_pg, monkeypatch, capsys):
    salida, llamados, out = _corre_vigia(conn_pg, monkeypatch, capsys)
    assert salida == 0 and llamados == [], "sin vigila no hay impulso_lectura ni fabrica_lote"
    assert metricas_listas.TEXTO_ESPERA in out


@_skip_db
def test_vigia_con_metricas_de_su_plataforma_corre(conn_pg, monkeypatch, capsys):
    conn, _ = conn_pg
    _siembra_metricas(conn, plataformas=("amazon_mx",))
    salida, llamados, _ = _corre_vigia(conn_pg, monkeypatch, capsys)
    assert salida == 0 and len(llamados) == 1


@_skip_db
def test_vigia_forzar_corre_sin_metricas(conn_pg, monkeypatch, capsys):
    salida, llamados, _ = _corre_vigia(conn_pg, monkeypatch, capsys, ("impulso-vigia", "--forzar"))
    assert salida == 0 and len(llamados) == 1


# ---------------------------------------------------------------------------
# avisos-campana (lee ubicaciones: exige tambien la corrida de placements)
# ---------------------------------------------------------------------------


def _corre_avisos(conn_pg, monkeypatch, capsys, argv=("avisos-campana",)):
    from app import cli, notifica

    conn, dsn = conn_pg
    monkeypatch.setenv("ORBIT_DSN_READ", dsn)
    monkeypatch.setenv("ORBIT_DSN_INGEST", dsn)
    enviados: list = []
    monkeypatch.setattr(notifica, "avisar_campanas", lambda *a, **k: enviados.append((a, k)) or {})
    salida = cli.main(list(argv))
    return salida, enviados, capsys.readouterr().out


@_skip_db
def test_avisos_sin_metricas_no_envia_ni_marca(conn_pg, monkeypatch, capsys):
    salida, enviados, out = _corre_avisos(conn_pg, monkeypatch, capsys)
    conn, _ = conn_pg
    assert salida == 0 and enviados == []
    assert metricas_listas.TEXTO_ESPERA in out
    marcas = conn.execute(
        "SELECT count(*) FROM ingest_run WHERE source LIKE 'avisos_campana%'"
    ).fetchone()[0]
    assert marcas == 0, "un aviso bloqueado no deja marca: se reevalua mañana"


@_skip_db
def test_avisos_exigen_tambien_la_corrida_de_placements(conn_pg, monkeypatch, capsys):
    conn, _ = conn_pg
    _siembra_metricas(conn)
    salida, enviados, out = _corre_avisos(conn_pg, monkeypatch, capsys)
    assert salida == 0 and enviados == [], "lee ubicaciones: sin placements no corre"
    assert metricas_listas.TEXTO_ESPERA in out
    _siembra_placements(conn)
    salida, enviados, _ = _corre_avisos(conn_pg, monkeypatch, capsys)
    assert salida == 0 and len(enviados) == 1


@_skip_db
def test_avisos_forzar_corre_sin_metricas(conn_pg, monkeypatch, capsys):
    salida, enviados, _ = _corre_avisos(
        conn_pg, monkeypatch, capsys, ("avisos-campana", "--forzar")
    )
    assert salida == 0 and len(enviados) == 1


# ---------------------------------------------------------------------------
# jev-senales
# ---------------------------------------------------------------------------


def _corre_jev(conn_pg, monkeypatch, capsys, argv=("jev-senales",)):
    from app import cli, jev_senales

    conn, dsn = conn_pg
    monkeypatch.setenv("ORBIT_DSN_READ", dsn)
    llamados: list = []
    monkeypatch.setattr(jev_senales, "correr", lambda *a, **k: (llamados.append((a, k)), None)[1])
    salida = cli.main(list(argv))
    return salida, llamados, capsys.readouterr().out


@_skip_db
def test_jev_sin_metricas_no_abre_nada(conn_pg, monkeypatch, capsys):
    salida, llamados, out = _corre_jev(conn_pg, monkeypatch, capsys)
    assert salida == 0 and llamados == [], "sin correr no hay candado ni jev_corrida"
    assert metricas_listas.TEXTO_ESPERA in out


@_skip_db
def test_jev_con_metricas_corre(conn_pg, monkeypatch, capsys):
    conn, _ = conn_pg
    _siembra_metricas(conn)
    salida, llamados, _ = _corre_jev(conn_pg, monkeypatch, capsys)
    assert salida == 0 and len(llamados) == 1


@_skip_db
def test_jev_forzar_corre_sin_metricas(conn_pg, monkeypatch, capsys):
    salida, llamados, _ = _corre_jev(conn_pg, monkeypatch, capsys, ("jev-senales", "--forzar"))
    assert salida == 0 and len(llamados) == 1
