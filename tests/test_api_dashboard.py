"""Tests del router de series temporales del dashboard (`app.api_dashboard`,
ORBIT 16 — DASHBOARD 01, task 1.3).

INTEGRACION (patron `_db_temporal` de test_api, COPIADO; skipif fail-closed
`_postgres_obligatorio_ausente` de test_schema): DB temporal con la migracion
entera, sembrada por el test, y el router conectado como rol de lectura via
`ORBIT_DSN_READ` (misma dependencia que api.py; sin DSN -> 503 fail-closed).

Contrato sellado de la task (brief docs/DASHBOARD.md §3.1/§3.2/§3.6; el header
del plan manda):

1. COLAPSO BITEMPORAL (regla 5): las series leen SIEMPRE `v_metric_latest`
   (ultima observacion por (entidad, fecha)); dos obs de la misma fecha ->
   gana la ultima por observed_at.
2. ANTI-DOBLE-CONTEO (regla 9): grano `kind='campaign'` EXPLICITO via JOIN a
   ad_entity; una fila keyword del mismo dia NO entra a la serie (evidencia de
   produccion: campaign 63.96 = keyword 24.94 + product_target 39.02 -> SUM
   sin filtro = 2x). El candado a nivel SQL (test_series_sql_filtran_kind_*)
   corre SIN Postgres y se demuestra FALLANDO contra el SQL sin el filtro
   (rojo en out/tdd-red-1.3.log).
3. NULL != 0 (regla 3): fecha sin fila -> valores null (spine de fechas, hueco
   visible, jamas 0); metrica NULL en alguna campana del dia -> agregado
   envenenado (bool_and) -> null.
4. SIN_VENTAS: ad_revenue == 0 (conocido) -> acos null + sin_ventas true
   (caso REAL: amazon_us 2026-08-22, cost 66.6300, revenue 0.0000).
5. DIA EN CURSO EXCLUIDO: default [D-30, D-1] UTC; un `hasta` que pida el dia
   en curso se RECORTA a D-1 y la respuesta declara el rango efectivo.
6. DINERO COMO STRING (regla 4): cost/ad_revenue tal cual el NUMERIC(14,4)
   ("363.1400"); clicks entero; acos string de 2 decimales o null; moneda por
   serie, jamas un total que mezcle monedas.
7. SUPERFICIE OpenAPI: /api/dashboard solo registra GET.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import socket
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

import pglast
import psycopg
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Json
from test_schema import _postgres_obligatorio_ausente, _test_dsn

from app import api_dashboard as dash
from app.main import app

_DIA = dt.timedelta(days=1)

# Reloj FIJO tz-aware (determinismo): los seeds de decisiones y ciclos se
# cuelgan de AHORA (patron test_api).
AHORA = dt.datetime(2026, 8, 22, 12, 0, tzinfo=dt.UTC)
DECIDED_AT = AHORA

SQL_MIGRACION = (
    Path(__file__).resolve().parent.parent / "migrations" / "0001_initial.sql"
).read_text(encoding="utf-8")


def _obs(fecha: dt.date, hora: int = 1) -> dt.datetime:
    """observed_at de una observacion: medianoche + hora UTC (>= metric_date)."""
    return dt.datetime(fecha.year, fecha.month, fecha.day, hora, tzinfo=dt.UTC)


# ---------------------------------------------------------------------------
# Patron _db_temporal COPIADO de test_api (con factory de conecs)
# ---------------------------------------------------------------------------


@contextmanager
def _db_temporal(prefijo: str):
    """DB temporal con la migracion entera; yields (conn, dsn_lectura)."""
    from psycopg import sql as pgsql

    dsn = _test_dsn()
    db = f"{prefijo}_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        conn.execute(SQL_MIGRACION)
        dsn_lectura = make_conninfo(dsn, dbname=db)
        yield conn, dsn_lectura
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def _cliente(dsn_lectura: str, monkeypatch) -> TestClient:
    """TestClient con el router conectado a la DB temporal (rol de lectura)."""
    monkeypatch.setenv("ORBIT_DSN_READ", dsn_lectura)
    return TestClient(app)


def _hoy(monkeypatch, fecha: dt.date) -> None:
    """Reloj fijo UTC del dashboard (determinismo; sin now() escondido)."""
    monkeypatch.setattr(dash, "_hoy_utc", lambda: fecha)


# ---------------------------------------------------------------------------
# Seeds (helpers del estilo test_api, copiados)
# ---------------------------------------------------------------------------


def _run(conn) -> int:
    return conn.execute("INSERT INTO ingest_run (source) VALUES ('test') RETURNING id").fetchone()[
        0
    ]


def _campana(conn, platform: str, external: str, name=None) -> int:
    return conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id, name)"
        " VALUES (%s, 'campaign', %s, %s) RETURNING id",
        (platform, external, name),
    ).fetchone()[0]


def _grupo(conn, platform: str, external: str, parent: int) -> int:
    return conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id, parent_id)"
        " VALUES (%s, 'ad_group', %s, %s) RETURNING id",
        (platform, external, parent),
    ).fetchone()[0]


def _keyword(conn, platform: str, external: str, parent: int, text: str) -> int:
    return conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id, parent_id, match_type,"
        " keyword_text) VALUES (%s, 'keyword', %s, %s, 'EXACT', %s) RETURNING id",
        (platform, external, parent, text),
    ).fetchone()[0]


def _metrica(
    conn, run_id, ad_entity_id, fecha, *, cost, ad_revenue, clicks, moneda, observed_at=None
) -> None:
    """Una observacion de metricas (bitemporal: observed_at controlable)."""
    conn.execute(
        "INSERT INTO ads_metric_observation (ad_entity_id, metric_date, observed_at,"
        " metric_currency, cost, ad_revenue, impressions, clicks, orders, ingest_run_id)"
        " VALUES (%s, %s, %s, %s, %s, %s, NULL, %s, NULL, %s)",
        (
            ad_entity_id,
            fecha,
            observed_at or _obs(fecha),
            moneda,
            None if cost is None else Decimal(cost),
            None if ad_revenue is None else Decimal(ad_revenue),
            clicks,
            run_id,
        ),
    )


def _config_version(conn, settings: dict) -> int:
    """config_version (JSONB); la vigente es la de mayor id."""
    return conn.execute(
        "INSERT INTO config_version (label, settings) VALUES (%s, %s) RETURNING id",
        ("test-dashboard", Json(settings)),
    ).fetchone()[0]


def _goal_db(
    conn,
    *,
    scope: str,
    platform=None,
    ad_entity_id=None,
    target=None,
    floor="0.10",
    ceiling="2.50",
    enabled=True,
    mode="shadow",
) -> int:
    """Goal de plataforma o campana (convencion del schema: scope=campaign exige
    ad_entity_id y platform NULL; scope=platform al reves)."""
    return conn.execute(
        "INSERT INTO ads_optimizer_goal (scope, ad_entity_id, platform, target_acos_pct,"
        " bid_floor, bid_ceiling, bid_currency, enabled, mode)"
        " VALUES (%s, %s, %s::platform, %s, %s, %s, 'USD', %s, %s) RETURNING id",
        (scope, ad_entity_id, platform, target, floor, ceiling, enabled, mode),
    ).fetchone()[0]


def _estado_acos(conn, ad_entity_id, acos_target) -> None:
    """ad_entity_state con cache del acos_target publicado (4to peldano)."""
    conn.execute(
        "INSERT INTO ad_entity_state (ad_entity_id, current_bid, bid_currency, status,"
        " acos_target, synced_at) VALUES (%s, NULL, NULL, 'ENABLED', %s, %s)",
        (ad_entity_id, acos_target, AHORA),
    )


def _ciclo(conn, *, platform: str, status: str = "done", notes=None) -> int:
    """Envelope de ciclo (notes TEXT: JSON o texto plano 'rastro: ...')."""
    return conn.execute(
        "INSERT INTO optimizer_cycle (motor, mode, platform, status, finished_at,"
        " decisions_count, notes) VALUES ('ads_optimizer', 'shadow', %s::platform, %s, %s, 0, %s)"
        " RETURNING id",
        (platform, status, AHORA, notes),
    ).fetchone()[0]


def _decision(
    conn,
    ciclo,
    ad_entity_id,
    *,
    kind: str,
    config_id: int,
    inputs: dict,
    search_term=None,
    window_end=dt.date(2026, 8, 16),
    old_value=None,
    new_value=None,
    moneda=None,
) -> int:
    """Una decision (inputs JSONB congelados). kind bid exige moneda por CHECK;
    negative/harvest exigen window_end <= decided_at - 10d (trigger de madurez)
    y moneda NULL; pause tolera TODO null (trampa real de la evidencia)."""
    return conn.execute(
        "INSERT INTO decision (cycle_id, ad_entity_id, kind, decided_at, config_version_id,"
        " data_observed_at, window_start, window_end, search_term, old_value, new_value,"
        " value_currency, inputs) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
        " RETURNING id",
        (
            ciclo,
            ad_entity_id,
            kind,
            DECIDED_AT,
            config_id,
            DECIDED_AT - dt.timedelta(hours=1),
            dt.date(2026, 7, 14),
            window_end,
            search_term,
            old_value,
            new_value,
            moneda,
            Json(inputs),
        ),
    ).fetchone()[0]


# ---------------------------------------------------------------------------
# UNITARIOS puros (corren siempre, sin DB): ACoS, ventana efectiva, spine
# ---------------------------------------------------------------------------


def test_acoso_ratio_2_decimales_y_sin_ventas():
    """Caso real amazon_mx 2026-08-22: 363.1400 / 3262.0600 -> "11.13".
    revenue == 0 conocido -> sin_ventas (caso real amazon_us: 66.6300 / 0).
    cost o revenue None -> dato faltante (acos null, sin_ventas false)."""
    assert dash._acoso(Decimal("363.1400"), Decimal("3262.0600")) == ("11.13", False)
    # redondeo HALF_UP explicito (hallazgo kimi): con el half-even del
    # contexto por default, 11.125 daria "11.12" y este assert fallaria
    assert dash._acoso(Decimal("11.125"), Decimal("100")) == ("11.13", False)
    assert dash._acoso(Decimal("66.6300"), Decimal("0")) == (None, True)
    assert dash._acoso(Decimal("0"), Decimal("0")) == (None, True)  # no hubo ventas
    assert dash._acoso(Decimal("5"), None) == (None, False)
    assert dash._acoso(None, Decimal("5")) == (None, False)
    assert dash._acoso(None, None) == (None, False)


def test_ventana_efectiva_default_recorte_y_validaciones():
    """Default [D-30, D-1]; el dia en curso EXCLUIDO (hasta se recorta a D-1);
    desde > hasta tras recorte -> 422; ventana > 366 dias -> 422."""
    hoy = dt.date(2026, 8, 24)
    assert dash._ventana_efectiva(None, None, hoy) == (
        dt.date(2026, 7, 25),
        dt.date(2026, 8, 23),
    )
    # hasta que pide el dia en curso: recorte a D-1
    assert dash._ventana_efectiva(dt.date(2026, 8, 1), dt.date(2026, 8, 24), hoy) == (
        dt.date(2026, 8, 1),
        dt.date(2026, 8, 23),
    )
    with pytest.raises(HTTPException) as exc:
        dash._ventana_efectiva(dt.date(2026, 8, 24), dt.date(2026, 8, 24), hoy)
    assert exc.value.status_code == 422
    with pytest.raises(HTTPException) as exc2:
        dash._ventana_efectiva(dt.date(2025, 1, 1), dt.date(2026, 8, 23), hoy)
    assert exc2.value.status_code == 422


def test_arma_serie_spine_completo_con_huecos_null():
    """Spine: TODAS las fechas del rango; fecha sin fila -> todo null
    (hueco visible), JAMAS 0; inmaduro relativo a hoy (D-8..D-1)."""
    hoy = dt.date(2026, 8, 24)
    por_fecha = {dt.date(2026, 8, 20): (Decimal("2.0000"), Decimal("6.0000"), 2)}
    serie = dash._arma_serie(dt.date(2026, 8, 19), dt.date(2026, 8, 21), por_fecha, hoy)
    assert [s["fecha"] for s in serie] == ["2026-08-19", "2026-08-20", "2026-08-21"]
    hueco = serie[0]
    assert hueco["cost"] is None and hueco["ad_revenue"] is None
    assert hueco["clicks"] is None and hueco["acos"] is None
    assert hueco["sin_ventas"] is False
    assert serie[1]["cost"] == "2.0000" and serie[1]["acos"] == "33.33"
    assert serie[1]["sin_ventas"] is False
    # inmaduro: D-8..D-1 -> 08-16..08-23; 08-19/20/21 SI, 08-15 NO
    assert serie[0]["inmaduro"] is True and serie[2]["inmaduro"] is True
    serie_fuera = dash._arma_serie(dt.date(2026, 8, 14), dt.date(2026, 8, 15), {}, hoy)
    assert all(s["inmaduro"] is False for s in serie_fuera)


# ---------------------------------------------------------------------------
# Candados SIN Postgres (corren siempre): SQL anti-doble-conteo + parseo
# ---------------------------------------------------------------------------


def _conjuntos_and(nodo):
    """Aplana SOLO conjunciones AND del WHERE: lo que quede bajo un OR no es
    obligatorio (un `... OR TRUE` re-incluiria las hojas por precedencia)."""
    from pglast import ast as pgast
    from pglast import enums as pgenums

    if isinstance(nodo, pgast.BoolExpr) and nodo.boolop == pgenums.BoolExprType.AND_EXPR:
        planos = []
        for arg in nodo.args:
            planos.extend(_conjuntos_and(arg))
        return planos
    return [nodo]


def test_series_sql_filtran_kind_campaign_en_ad_entity():
    """Candado ANTI-DOBLE-CONTEO a nivel SQL (regla 9, corre sin Postgres):
    en ambas queries de serie, e.kind = 'campaign' es conjuncion OBLIGATORIA
    (AND de nivel superior del WHERE), verificado por AST — el containment
    lexico aceptaba un mutante `... OR TRUE` que re-incluye las hojas por
    precedencia SQL (hallazgo kimi + CodeRabbit). Sin el filtro, keyword y
    product_target duplican el dinero (evidencia: campaign 63.96 = keyword
    24.94 + product_target 39.02 -> 2x). Rojo original en out/tdd-red-1.3.log;
    el poder discriminante contra el mutante se demuestra AQUI mismo."""
    from pglast.stream import RawStream

    for nombre in ("_SQL_SERIE_PLATAFORMA", "_SQL_SERIE_CAMPANA", "_SQL_CAMPANAS_30D"):
        sql = getattr(dash, nombre).replace("%s", "NULL")
        normalizada = " ".join(pglast.prettify(sql).lower().split())
        assert "join ad_entity" in normalizada, f"{nombre}: sin JOIN a ad_entity"
        where = pglast.parse_sql(sql)[0].stmt.whereClause
        conjuntos = [RawStream()(c) for c in _conjuntos_and(where)]
        assert "e.kind = 'campaign'" in conjuntos, (
            f"{nombre}: e.kind = 'campaign' no es conjuncion obligatoria del "
            f"WHERE (conjuntos: {conjuntos})"
        )
    # regla 9 in situ: el mutante OR TRUE NO pasa este candado
    mutante = dash._SQL_SERIE_PLATAFORMA.replace("%s", "NULL").replace(
        "e.kind = 'campaign'", "e.kind = 'campaign' OR TRUE"
    )
    where_mutante = pglast.parse_sql(mutante)[0].stmt.whereClause
    conjuntos_mutante = [RawStream()(c) for c in _conjuntos_and(where_mutante)]
    assert "e.kind = 'campaign'" not in conjuntos_mutante, (
        "el candado no discrimina el mutante OR TRUE"
    )


def test_sql_del_modulo_dashboard_parsea_como_postgres():
    """Sintaxis de las SQL del modulo (patron test_api): pglast valida que las
    constantes parsean como Postgres real (un typo muere en CI, no en prod)."""
    nombres = sorted(n for n in vars(dash) if n.startswith("_SQL_"))
    assert nombres, "no se encontraron constantes _SQL_* en app/api_dashboard"
    for nombre in nombres:
        sql = getattr(dash, nombre).replace("%s", "NULL").replace("{filtros}", "true")
        assert pglast.parse_sql(sql), f"{nombre} no parseo"


def test_router_dashboard_solo_registra_get():
    """Superficie OpenAPI COMPLETA: /api/dashboard solo expone GET (CERO
    escrituras en PR1; introspeccion de rutas, no convencion)."""
    paths = app.openapi()["paths"]
    rutas = {path: metodos for path, metodos in paths.items() if path.startswith("/api/dashboard")}
    assert rutas, "no se encontraron rutas /api/dashboard en el OpenAPI"
    for path, metodos in rutas.items():
        assert set(metodos) == {"get"}, (
            f"{path} expone {sorted(metodos)}: el router de PR1 solo puede exponer GET"
        )


def test_sin_dsn_de_lectura_fail_closed_503(monkeypatch):
    """Misma dependencia de lectura que api.py: sin ORBIT_DSN_READ -> 503 con
    mensaje claro (corre sin Postgres)."""
    monkeypatch.delenv("ORBIT_DSN_READ", raising=False)
    resp = TestClient(app).get("/api/dashboard/series/plataforma", params={"platform": "amazon_us"})
    assert resp.status_code == 503
    assert "ORBIT_DSN_READ" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# INTEGRACION (skipif fail-closed sin Postgres): los sellos sobre la DB real
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_serie_plataforma_colapso_bitemporal_gana_la_ultima(monkeypatch):
    """Regla 5: dos observaciones de la misma (entidad, fecha) -> la serie usa
    la ULTIMA por observed_at (v_metric_latest), jamas la cruda."""
    with _db_temporal("orbit_dash_colapso") as (conn, dsn):
        run = _run(conn)
        camp = _campana(conn, "amazon_us", "9001", name="Campaña A")
        fecha = dt.date(2026, 8, 20)
        _metrica(
            conn,
            run,
            camp,
            fecha,
            cost="10.0000",
            ad_revenue="30.0000",
            clicks=3,
            moneda="USD",
            observed_at=_obs(fecha, 1),
        )
        _metrica(
            conn,
            run,
            camp,
            fecha,
            cost="20.0000",
            ad_revenue="60.0000",
            clicks=6,
            moneda="USD",
            observed_at=_obs(fecha, 3),
        )
        _hoy(monkeypatch, dt.date(2026, 8, 24))
        resp = _cliente(dsn, monkeypatch).get(
            "/api/dashboard/series/plataforma",
            params={"platform": "amazon_us", "desde": "2026-08-20", "hasta": "2026-08-20"},
        )
        assert resp.status_code == 200
        fila = resp.json()["series"][0]
        assert fila["cost"] == "20.0000"  # la ULTIMA observacion gana
        assert fila["ad_revenue"] == "60.0000"
        assert fila["clicks"] == 6


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_serie_plataforma_anti_doble_conteo_solo_campaign(monkeypatch):
    """Evidencia real (amazon_us 2026-08-20): campaign suma 63.96 y las hojas
    suman EXACTO lo mismo (keyword 24.94 + product_target 39.02). La serie usa
    SOLO la fila campaign: sin el filtro kind='campaign' duplicaria el dinero
    (regla 9; el candado SQL test_series_sql_filtran_kind_campaign_en_ad_entity
    se demuestra fallando contra el SQL sin el filtro)."""
    with _db_temporal("orbit_dash_doble") as (conn, dsn):
        run = _run(conn)
        camp = _campana(conn, "amazon_us", "9001")
        ag = _grupo(conn, "amazon_us", "9101", parent=camp)
        kw = _keyword(conn, "amazon_us", "9201", parent=ag, text="girasoles")
        fecha = dt.date(2026, 8, 20)
        _metrica(
            conn, run, camp, fecha, cost="63.9600", ad_revenue="100.0000", clicks=10, moneda="USD"
        )
        _metrica(conn, run, kw, fecha, cost="24.9400", ad_revenue="40.0000", clicks=4, moneda="USD")
        _hoy(monkeypatch, dt.date(2026, 8, 24))
        resp = _cliente(dsn, monkeypatch).get(
            "/api/dashboard/series/plataforma",
            params={"platform": "amazon_us", "desde": "2026-08-20", "hasta": "2026-08-20"},
        )
        assert resp.status_code == 200
        fila = resp.json()["series"][0]
        assert fila["cost"] == "63.9600"  # SOLO campaign; 63.96+24.94 seria 2x
        assert fila["ad_revenue"] == "100.0000"
        assert fila["clicks"] == 10


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_serie_spine_fechas_completo_y_huecos_null_no_cero(monkeypatch):
    """Spine: TODAS las fechas del rango; una fecha sin fila -> null (hueco
    visible), JAMAS 0 (regla 3)."""
    with _db_temporal("orbit_dash_spine") as (conn, dsn):
        run = _run(conn)
        camp = _campana(conn, "amazon_us", "9001")
        _metrica(
            conn,
            run,
            camp,
            dt.date(2026, 8, 18),
            cost="1.0000",
            ad_revenue="3.0000",
            clicks=1,
            moneda="USD",
        )
        _metrica(
            conn,
            run,
            camp,
            dt.date(2026, 8, 20),
            cost="2.0000",
            ad_revenue="6.0000",
            clicks=2,
            moneda="USD",
        )
        _hoy(monkeypatch, dt.date(2026, 8, 24))
        resp = _cliente(dsn, monkeypatch).get(
            "/api/dashboard/series/plataforma",
            params={"platform": "amazon_us", "desde": "2026-08-18", "hasta": "2026-08-20"},
        )
        assert resp.status_code == 200
        series = resp.json()["series"]
        assert [s["fecha"] for s in series] == ["2026-08-18", "2026-08-19", "2026-08-20"]
        hueco = series[1]  # 08-19 sin fila
        assert hueco["cost"] is None and hueco["ad_revenue"] is None
        assert hueco["clicks"] is None and hueco["acos"] is None
        assert hueco["sin_ventas"] is False
        assert series[0]["cost"] == "1.0000"


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_serie_null_metrico_envenena_el_agregado(monkeypatch):
    """Regla 3: una campana con cost NULL ese dia envenena el agregado de ESA
    metrica (bool_and, criterio de windows.py): cost null, JAMAS una suma
    PARCIAL enganosa. La segunda campana con cost CONOCIDO es la que
    discrimina (hallazgo CodeRabbit: con una sola campana, SUM(NULL) da NULL
    hasta sin bool_and — un SUM parcial aqui devolveria "1.0000"). Las
    metricas completas del dia (revenue, clicks) SI suman."""
    with _db_temporal("orbit_dash_null") as (conn, dsn):
        run = _run(conn)
        camp = _campana(conn, "amazon_us", "9001")
        camp_con_cost = _campana(conn, "amazon_us", "9002")
        _metrica(
            conn,
            run,
            camp,
            dt.date(2026, 8, 20),
            cost=None,
            ad_revenue="5.0000",
            clicks=2,
            moneda="USD",
        )
        _metrica(
            conn,
            run,
            camp_con_cost,
            dt.date(2026, 8, 20),
            cost="1.0000",
            ad_revenue="3.0000",
            clicks=1,
            moneda="USD",
        )
        _hoy(monkeypatch, dt.date(2026, 8, 24))
        resp = _cliente(dsn, monkeypatch).get(
            "/api/dashboard/series/plataforma",
            params={"platform": "amazon_us", "desde": "2026-08-20", "hasta": "2026-08-20"},
        )
        fila = resp.json()["series"][0]
        assert fila["cost"] is None  # envenenado: un SUM parcial daria "1.0000"
        assert fila["ad_revenue"] == "8.0000"  # metrica completa: suma normal
        assert fila["clicks"] == 3
        assert fila["acos"] is None
        assert fila["sin_ventas"] is False


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_serie_sin_ventas_caso_real_amazon_us(monkeypatch):
    """Caso REAL de la evidencia (amazon_us 2026-08-22): cost 66.6300, revenue
    0.0000 -> acos null + sin_ventas true, jamas division ni 0 enganoso."""
    with _db_temporal("orbit_dash_sinventas") as (conn, dsn):
        run = _run(conn)
        camp = _campana(conn, "amazon_us", "9001")
        _metrica(
            conn,
            run,
            camp,
            dt.date(2026, 8, 22),
            cost="66.6300",
            ad_revenue="0.0000",
            clicks=5,
            moneda="USD",
        )
        _hoy(monkeypatch, dt.date(2026, 8, 24))
        resp = _cliente(dsn, monkeypatch).get(
            "/api/dashboard/series/plataforma",
            params={"platform": "amazon_us", "desde": "2026-08-22", "hasta": "2026-08-22"},
        )
        fila = resp.json()["series"][0]
        assert fila["acos"] is None
        assert fila["sin_ventas"] is True
        assert fila["cost"] == "66.6300"
        assert fila["ad_revenue"] == "0.0000"


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_serie_dinero_string_y_acos_ratio_caso_mx(monkeypatch):
    """Caso REAL amazon_mx 2026-08-22: cost 363.1400, revenue 3262.0600, clicks
    116 -> dinero STRING con 4 decimales tal cual, acos "11.13", moneda MXN."""
    with _db_temporal("orbit_dash_dinero") as (conn, dsn):
        run = _run(conn)
        camp = _campana(conn, "amazon_mx", "9002", name="Campaña MX")
        _metrica(
            conn,
            run,
            camp,
            dt.date(2026, 8, 22),
            cost="363.1400",
            ad_revenue="3262.0600",
            clicks=116,
            moneda="MXN",
        )
        _hoy(monkeypatch, dt.date(2026, 8, 24))
        resp = _cliente(dsn, monkeypatch).get(
            "/api/dashboard/series/plataforma",
            params={"platform": "amazon_mx", "desde": "2026-08-22", "hasta": "2026-08-22"},
        )
        data = resp.json()
        assert data["moneda"] == "MXN"
        fila = data["series"][0]
        assert isinstance(fila["cost"], str) and fila["cost"] == "363.1400"
        assert isinstance(fila["ad_revenue"], str) and fila["ad_revenue"] == "3262.0600"
        assert isinstance(fila["clicks"], int) and fila["clicks"] == 116
        assert fila["acos"] == "11.13"
        assert fila["sin_ventas"] is False


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_serie_dia_en_curso_excluido_default_y_recorte(monkeypatch):
    """Sellado: el dia en curso jamas se sirve. Default [D-30, D-1]; un hasta
    explicito que pide el dia en curso se RECORTA a D-1 y la respuesta declara
    el rango efectivo."""
    with _db_temporal("orbit_dash_hoy") as (conn, dsn):
        run = _run(conn)
        camp = _campana(conn, "amazon_us", "9001")
        _metrica(
            conn,
            run,
            camp,
            dt.date(2026, 8, 23),
            cost="1.0000",
            ad_revenue="3.0000",
            clicks=1,
            moneda="USD",
        )
        _metrica(
            conn,
            run,
            camp,
            dt.date(2026, 8, 24),
            cost="9.0000",
            ad_revenue="27.0000",
            clicks=9,
            moneda="USD",
        )
        _hoy(monkeypatch, dt.date(2026, 8, 24))
        cliente = _cliente(dsn, monkeypatch)
        # default: [D-30, D-1] = 30 dias; el 08-24 (dia en curso) queda fuera
        r = cliente.get("/api/dashboard/series/plataforma", params={"platform": "amazon_us"})
        data = r.json()
        assert data["desde"] == "2026-07-25" and data["hasta"] == "2026-08-23"
        assert len(data["series"]) == 30
        assert data["series"][-1]["fecha"] == "2026-08-23"
        assert data["series"][-1]["cost"] == "1.0000"  # el 08-24 no entra
        # hasta explicito con el dia en curso: recorte a D-1, rango declarado
        r2 = cliente.get(
            "/api/dashboard/series/plataforma",
            params={"platform": "amazon_us", "desde": "2026-08-23", "hasta": "2026-08-24"},
        )
        data2 = r2.json()
        assert data2["hasta"] == "2026-08-23"
        assert [s["fecha"] for s in data2["series"]] == ["2026-08-23"]


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_serie_dias_inmaduros_d8_a_d1_marcados(monkeypatch):
    """D-8..D-1 marcados inmaduro: true (la atribucion madura 5-8d y el costo
    hasta D+15); el resto false. Relativo a hoy, independiente del rango."""
    with _db_temporal("orbit_dash_inmaduro") as (conn, dsn):
        run = _run(conn)
        camp = _campana(conn, "amazon_us", "9001")
        for fecha in (dt.date(2026, 8, 15), dt.date(2026, 8, 16), dt.date(2026, 8, 23)):
            _metrica(
                conn, run, camp, fecha, cost="1.0000", ad_revenue="3.0000", clicks=1, moneda="USD"
            )
        _hoy(monkeypatch, dt.date(2026, 8, 24))
        resp = _cliente(dsn, monkeypatch).get(
            "/api/dashboard/series/plataforma",
            params={"platform": "amazon_us", "desde": "2026-08-15", "hasta": "2026-08-23"},
        )
        data = resp.json()
        assert data["ventana_inmaduros"] == {"desde": "2026-08-16", "hasta": "2026-08-23"}
        por_fecha = {s["fecha"]: s for s in data["series"]}
        assert por_fecha["2026-08-15"]["inmaduro"] is False  # D-9
        assert por_fecha["2026-08-16"]["inmaduro"] is True  # D-8
        assert por_fecha["2026-08-23"]["inmaduro"] is True  # D-1


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_serie_plataforma_sin_datos_devuelve_spine_completo_de_nulls(monkeypatch):
    """Plataforma sin datos: la respuesta trae TODO el rango con nulls
    (spine obligatorio: el D-1 puede venir todo-null en la madrugada, antes
    del cron de las 07:10 UTC), jamas 404 ni ceros."""
    with _db_temporal("orbit_dash_vacio") as (_conn, dsn):
        _hoy(monkeypatch, dt.date(2026, 8, 24))
        resp = _cliente(dsn, monkeypatch).get(
            "/api/dashboard/series/plataforma", params={"platform": "amazon_us"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["moneda"] == "USD"
        assert len(data["series"]) == 30
        assert all(
            s["cost"] is None and s["ad_revenue"] is None and s["clicks"] is None
            for s in data["series"]
        )


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_serie_campana_contrato_404_422_y_nombre_null(monkeypatch):
    """Serie por campana: mismo contrato de filas; 404 id inexistente; 422 si
    la entidad no es kind='campaign'; nombre NULL (ad_entity.name nullable)
    no revienta."""
    with _db_temporal("orbit_dash_campana") as (conn, dsn):
        run = _run(conn)
        camp = _campana(conn, "amazon_us", "9001", name="Campaña A")
        ag = _grupo(conn, "amazon_us", "9101", parent=camp)
        _metrica(
            conn,
            run,
            camp,
            dt.date(2026, 8, 20),
            cost="5.0000",
            ad_revenue="15.0000",
            clicks=2,
            moneda="USD",
        )
        _hoy(monkeypatch, dt.date(2026, 8, 24))
        cliente = _cliente(dsn, monkeypatch)
        r = cliente.get(
            "/api/dashboard/series/campana",
            params={"ad_entity_id": camp, "desde": "2026-08-20", "hasta": "2026-08-20"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["ad_entity_id"] == camp
        assert data["nombre"] == "Campaña A"
        assert data["plataforma"] == "amazon_us" and data["moneda"] == "USD"
        assert data["series"][0]["cost"] == "5.0000"
        # id inexistente -> 404
        assert (
            cliente.get(
                "/api/dashboard/series/campana", params={"ad_entity_id": 999_999}
            ).status_code
            == 404
        )
        # entidad que NO es campaign (ad group) -> 422
        r422 = cliente.get("/api/dashboard/series/campana", params={"ad_entity_id": ag})
        assert r422.status_code == 422
        # nombre NULL no revienta
        camp_sin_nombre = _campana(conn, "amazon_us", "9003")
        r_null = cliente.get(
            "/api/dashboard/series/campana",
            params={"ad_entity_id": camp_sin_nombre, "desde": "2026-08-20", "hasta": "2026-08-20"},
        )
        assert r_null.status_code == 200
        assert r_null.json()["nombre"] is None
        # ad_entity_id fuera del contrato (ge=1) -> 422 (hallazgo reviewer)
        assert (
            cliente.get("/api/dashboard/series/campana", params={"ad_entity_id": 0}).status_code
            == 422
        )
        # campana meli: enum valido SIN moneda sellada del dashboard -> 422
        # (hallazgo reviewer: la rama existia sin test)
        camp_meli = _campana(conn, "meli", "9004")
        r_meli = cliente.get("/api/dashboard/series/campana", params={"ad_entity_id": camp_meli})
        assert r_meli.status_code == 422
        assert "sin moneda sellada" in r_meli.json()["detail"]


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_serie_plataforma_vocabulario_y_ventana_invalidos_422(monkeypatch):
    """platform fuera del vocabulario -> 422 (Literal de FastAPI); desde >
    hasta -> 422 (regla del rango)."""
    with _db_temporal("orbit_dash_422") as (conn, dsn):
        _hoy(monkeypatch, dt.date(2026, 8, 24))
        cliente = _cliente(dsn, monkeypatch)
        r = cliente.get("/api/dashboard/series/plataforma", params={"platform": "meli"})
        assert r.status_code == 422
        r2 = cliente.get(
            "/api/dashboard/series/plataforma",
            params={"platform": "amazon_us", "desde": "2026-08-24", "hasta": "2026-08-20"},
        )
        assert r2.status_code == 422


# ---------------------------------------------------------------------------
# 1.4 - CANDADO del feed (corre sin Postgres): cursor, jamas limit/offset
# ---------------------------------------------------------------------------


def test_sql_feed_decisiones_por_cursor_sin_offset_y_con_join_nombre():
    """Candado del FEED (regla 9, corre sin Postgres): paginacion por CURSOR
    (id <, ORDER BY d.id DESC) — PROHIBIDO limit/offset (decision 8 del
    header: offset sobre una tabla append-only produce huecos/duplicados entre
    paginas) — y JOIN a ad_entity para el nombre (nullable)."""
    sql = dash._SQL_DECISIONES_FEED.replace("%s", "NULL").replace("{filtros}", "true")
    normalizada = " ".join(pglast.prettify(sql).lower().split())
    assert "join ad_entity" in normalizada, "el feed debe JOIN ad_entity para el nombre"
    assert "order by d.id desc" in normalizada, "el feed ordena por id DESC (cursor)"
    assert "offset" not in normalizada, "el feed JAMAS pagina por offset"
    assert "limit" in normalizada


# ---------------------------------------------------------------------------
# 1.4 - INTEGRACION /campanas: procedencia en los 5 peldanos, goal, anti-mezcla
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_campanas_procedencia_en_los_5_peldanos(monkeypatch):
    """DoD: procedencia en los 5 peldanos via el ENDPOINT. Con una sola
    config vigente y dos plataformas no alcanza a aislar los 5 estados, asi
    que la config se avanza entre llamadas (la vigente es la de mayor id): la
    primera config cubre goal_campana/goal_plataforma/cache/default; una
    config NUEVA con la clave de amazon_mx voltea las campañas mx a
    setting_plataforma (el setting pisa el cache)."""
    with _db_temporal("orbit_dash_peldanos") as (conn, dsn):
        run = _run(conn)
        _config_version(conn, {"ads_target_acos_pct_amazon_us": 30})
        _goal_db(conn, scope="platform", platform="amazon_us", target="25")
        camp_a = _campana(conn, "amazon_us", "9001", name="A")
        camp_b = _campana(conn, "amazon_us", "9002", name="B")
        _goal_db(conn, scope="campaign", ad_entity_id=camp_a, target="18")
        camp_c = _campana(conn, "amazon_mx", "9003", name="C")
        camp_d = _campana(conn, "amazon_mx", "9004", name="D")
        _estado_acos(conn, camp_c, Decimal("28"))
        for camp in (camp_a, camp_b):
            _metrica(
                conn,
                run,
                camp,
                dt.date(2026, 8, 20),
                cost="1.0000",
                ad_revenue="3.0000",
                clicks=1,
                moneda="USD",
            )
        for camp in (camp_c, camp_d):
            _metrica(
                conn,
                run,
                camp,
                dt.date(2026, 8, 20),
                cost="1.0000",
                ad_revenue="3.0000",
                clicks=1,
                moneda="MXN",
            )
        _hoy(monkeypatch, dt.date(2026, 8, 24))
        cliente = _cliente(dsn, monkeypatch)

        data = cliente.get("/api/dashboard/campanas").json()["items"]
        por_id = {i["ad_entity_id"]: i for i in data}
        assert por_id[camp_a]["target_efectivo"] == {"valor": "18.00", "peldano": "goal_campana"}
        assert por_id[camp_b]["target_efectivo"] == {"valor": "25.00", "peldano": "goal_plataforma"}
        assert por_id[camp_c]["target_efectivo"] == {"valor": "28.00", "peldano": "cache_estado"}
        assert por_id[camp_d]["target_efectivo"] == {"valor": "55", "peldano": "default"}
        assert por_id[camp_d]["goal"] is None  # sin goal de plataforma ni de campana

        # config VIGENTE nueva: amazon_mx gana setting_plataforma (40)
        _config_version(conn, {"ads_target_acos_pct_amazon_mx": 40})
        data2 = cliente.get("/api/dashboard/campanas").json()["items"]
        por_id2 = {i["ad_entity_id"]: i for i in data2}
        assert por_id2[camp_d]["target_efectivo"] == {
            "valor": "40",
            "peldano": "setting_plataforma",
        }
        assert por_id2[camp_c]["target_efectivo"] == {
            "valor": "40",
            "peldano": "setting_plataforma",
        }
        # los goals siguen pisando: camp_a y camp_b intactos
        assert por_id2[camp_a]["target_efectivo"]["peldano"] == "goal_campana"
        assert por_id2[camp_b]["target_efectivo"]["peldano"] == "goal_plataforma"


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_campanas_goal_estado_resuelto_y_metricas_string(monkeypatch):
    """DoD: estado VIVO del goal RESUELTO (campaña > plataforma, decision 17);
    goal null sin goal (regla 3); dinero string; metricas 30d con acos y
    sin_ventas (revenue 0 -> acos null + flag)."""
    with _db_temporal("orbit_dash_goal") as (conn, dsn):
        run = _run(conn)
        _config_version(conn, {"ads_target_acos_pct_amazon_us": 30})
        _goal_db(
            conn,
            scope="platform",
            platform="amazon_us",
            target=None,
            floor="0.40",
            ceiling="2.50",
            enabled=True,
            mode="shadow",
        )
        camp_a = _campana(conn, "amazon_us", "9001", name="A")
        camp_b = _campana(conn, "amazon_us", "9002", name="B")
        camp_mx = _campana(conn, "amazon_mx", "9003", name="MX")
        _goal_db(
            conn,
            scope="campaign",
            ad_entity_id=camp_a,
            target="18",
            floor="0.20",
            ceiling="2.00",
            enabled=False,
            mode="off",
        )
        _metrica(
            conn,
            run,
            camp_a,
            dt.date(2026, 8, 20),
            cost="1.0000",
            ad_revenue="3.0000",
            clicks=1,
            moneda="USD",
        )
        _metrica(
            conn,
            run,
            camp_b,
            dt.date(2026, 8, 20),
            cost="66.6300",
            ad_revenue="0.0000",
            clicks=5,
            moneda="USD",
        )
        _metrica(
            conn,
            run,
            camp_mx,
            dt.date(2026, 8, 20),
            cost="2.0000",
            ad_revenue="6.0000",
            clicks=2,
            moneda="MXN",
        )
        _hoy(monkeypatch, dt.date(2026, 8, 24))
        data = _cliente(dsn, monkeypatch).get("/api/dashboard/campanas").json()["items"]
        por_id = {i["ad_entity_id"]: i for i in data}
        # goal resuelto = EL DE CAMPANA (pisa a la plataforma INCLUSO disabled)
        assert por_id[camp_a]["goal"] == {
            "enabled": False,
            "floor": "0.2000",
            "ceiling": "2.0000",
            "mode": "off",
            "scope": "campaign",
        }
        # sin goal de campana -> el de plataforma
        assert por_id[camp_b]["goal"] == {
            "enabled": True,
            "floor": "0.4000",
            "ceiling": "2.5000",
            "mode": "shadow",
            "scope": "platform",
        }
        # amazon_mx sin goal -> goal null
        assert por_id[camp_mx]["goal"] is None
        # dinero string + sin_ventas real + moneda por fila
        assert por_id[camp_b]["metricas_30d"]["cost"] == "66.6300"
        assert isinstance(por_id[camp_b]["metricas_30d"]["cost"], str)
        assert por_id[camp_b]["metricas_30d"]["sin_ventas"] is True
        assert por_id[camp_b]["metricas_30d"]["acos"] is None
        assert por_id[camp_b]["moneda"] == "USD"
        assert por_id[camp_mx]["moneda"] == "MXN"
        assert por_id[camp_mx]["metricas_30d"]["acos"] == "33.33"


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_campanas_anti_mezcla_de_monedas(monkeypatch):
    """DoD regla 4: cada fila lleva su moneda y NO existe total al pie que
    sume USD+MXN (el shape del contrato no tiene total)."""
    with _db_temporal("orbit_dash_mezcla") as (conn, dsn):
        run = _run(conn)
        _config_version(conn, {})
        camp_us = _campana(conn, "amazon_us", "9001", name="US")
        camp_mx = _campana(conn, "amazon_mx", "9002", name="MX")
        _metrica(
            conn,
            run,
            camp_us,
            dt.date(2026, 8, 20),
            cost="10.0000",
            ad_revenue="30.0000",
            clicks=3,
            moneda="USD",
        )
        _metrica(
            conn,
            run,
            camp_mx,
            dt.date(2026, 8, 20),
            cost="100.0000",
            ad_revenue="300.0000",
            clicks=3,
            moneda="MXN",
        )
        _hoy(monkeypatch, dt.date(2026, 8, 24))
        data = _cliente(dsn, monkeypatch).get("/api/dashboard/campanas").json()
        assert "total" not in data, "PROHIBIDO un total que mezclaria monedas"
        por_id = {i["ad_entity_id"]: i for i in data["items"]}
        assert por_id[camp_us]["moneda"] == "USD" and por_id[camp_mx]["moneda"] == "MXN"
        assert por_id[camp_us]["metricas_30d"]["cost"] == "10.0000"
        assert por_id[camp_mx]["metricas_30d"]["cost"] == "100.0000"


# ---------------------------------------------------------------------------
# 1.4 - INTEGRACION /decisiones: cursor estable, trampas reales, fallback
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_decisiones_paginacion_cursor_estable_con_insercion(monkeypatch):
    """DoD: cursor estable con insercion concurrente simulada — paginas sin
    duplicados ni huecos. La fila nueva (id mayor) jamas se cuela en una
    pagina cuyo cursor ya quedo atras (offset si lo haria)."""
    with _db_temporal("orbit_dash_cursor") as (conn, dsn):
        config_id = _config_version(conn, {"ads_optimizer_mode": "shadow"})
        camp = _campana(conn, "amazon_us", "9001", name="Campana A")
        ciclo = _ciclo(conn, platform="amazon_us")
        inputs = {"motor": "bid", "motivo": "banda_menos_12", "target_acos_pct_usado": "25.00"}
        ids = [
            _decision(conn, ciclo, camp, kind="bid", config_id=config_id, inputs=inputs)
            for _ in range(5)
        ]
        cliente = _cliente(dsn, monkeypatch)

        r1 = cliente.get("/api/dashboard/decisiones", params={"limit": 2})
        data1 = r1.json()
        assert [i["id"] for i in data1["items"]] == [ids[4], ids[3]]
        assert data1["next_cursor"] == ids[3]
        assert data1["has_more"] is True

        # insercion concurrente simulada: decision NUEVA con id mayor
        _decision(conn, ciclo, camp, kind="bid", config_id=config_id, inputs=inputs)

        r2 = cliente.get(
            "/api/dashboard/decisiones", params={"limit": 2, "cursor": data1["next_cursor"]}
        )
        data2 = r2.json()
        assert [i["id"] for i in data2["items"]] == [ids[2], ids[1]]
        assert data2["next_cursor"] == ids[1]
        assert data2["has_more"] is True

        r3 = cliente.get(
            "/api/dashboard/decisiones", params={"limit": 2, "cursor": data2["next_cursor"]}
        )
        data3 = r3.json()
        assert [i["id"] for i in data3["items"]] == [ids[0]]
        assert data3["next_cursor"] is None
        assert data3["has_more"] is False

        todos = (
            [i["id"] for i in data1["items"]]
            + [i["id"] for i in data2["items"]]
            + [i["id"] for i in data3["items"]]
        )
        assert todos == sorted(ids, reverse=True)  # sin duplicados ni huecos
        assert all(i["nombre"] == "Campana A" for i in data1["items"])
        assert all(i["target_acos_pct_usado"] == "25.00" for i in data1["items"])


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_decisiones_trampa_pause_null_y_target_desde_inputs(monkeypatch):
    """DoD + trampas reales de la evidencia: (a) el target mostrado se lee de
    inputs.target_acos_pct_usado, JAMAS de inputs.goal.target_acos_pct (null
    en produccion cuando gano el default o el setting); (b) los pause traen
    old_value/new_value/value_currency NULL: el feed los renderiza null sin
    inventar 0 ni crashear; (c) negative trae search_term (el vector XSS)."""
    with _db_temporal("orbit_dash_trampas") as (conn, dsn):
        config_id = _config_version(conn, {"ads_optimizer_mode": "shadow"})
        camp = _campana(conn, "amazon_us", "9001", name="Campana A")
        ag = _grupo(conn, "amazon_us", "9101", parent=camp)
        ciclo = _ciclo(conn, platform="amazon_us")
        id_bid = _decision(
            conn,
            ciclo,
            camp,
            kind="bid",
            config_id=config_id,
            moneda="USD",
            inputs={
                "motor": "bid",
                "motivo": "banda_menos_12",
                "target_acos_pct_usado": "25.00",
                # la trampa: el goal congelado NO trae target (null real)
                "goal": {"scope": "platform", "target_acos_pct": None},
            },
        )
        id_pause = _decision(
            conn,
            ciclo,
            camp,
            kind="pause",
            config_id=config_id,
            inputs={"motor": "bid", "motivo": "pause_umbral", "target_acos_pct_usado": "20.00"},
        )
        id_negative = _decision(
            conn,
            ciclo,
            ag,
            kind="negative",
            config_id=config_id,
            search_term="tortugas ninja",
            window_end=dt.date(2026, 8, 12),
            inputs={
                "motor": "hygiene",
                "motivo": "negative_umbral",
                "target_acos_pct_usado": "20.00",
                "termino": {"search_term": "tortugas ninja", "cost": "8.0000", "clicks": 20},
            },
        )
        cliente = _cliente(dsn, monkeypatch)
        data = cliente.get("/api/dashboard/decisiones").json()
        por_id = {i["id"]: i for i in data["items"]}
        assert len(por_id) == 3

        bid = por_id[id_bid]
        assert bid["target_acos_pct_usado"] == "25.00"  # de inputs, no del goal null
        assert bid["motivo_es"] == "ACoS sobre 1.15x del target: -12%"

        pause = por_id[id_pause]
        assert pause["old_value"] is None and pause["new_value"] is None
        assert pause["value_currency"] is None
        assert pause["motivo_es"] == "Pausa: sin ventas con clicks y costo sobre el umbral"

        negative = por_id[id_negative]
        assert negative["search_term"] == "tortugas ninja"
        assert negative["kind"] == "negative"
        assert (
            negative["motivo_es"]
            == "Negativo: termino sin ventas con clicks y costo sobre el umbral"
        )


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_decisiones_motivo_desconocido_fallback_y_nombre_null(monkeypatch):
    """DoD: motivo desconocido -> fallback SIN crash (se devuelve el id crudo,
    jamas se pierde informacion); name NULL (ad_entity.name nullable) no
    revienta."""
    with _db_temporal("orbit_dash_fallback") as (conn, dsn):
        config_id = _config_version(conn, {})
        camp_sin_nombre = _campana(conn, "amazon_us", "9002")  # name NULL
        ciclo = _ciclo(conn, platform="amazon_us")
        _decision(
            conn,
            ciclo,
            camp_sin_nombre,
            kind="bid",
            config_id=config_id,
            moneda="USD",
            inputs={
                "motor": "bid",
                "motivo": "motivo_futuro_desconocido",
                "target_acos_pct_usado": "20.00",
            },
        )
        item = _cliente(dsn, monkeypatch).get("/api/dashboard/decisiones").json()["items"][0]
        assert item["nombre"] is None  # name NULL no revienta
        assert item["motivo_es"] == "motivo_futuro_desconocido"  # fallback sin crash


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_decisiones_filtros_platform_kind_y_vocabulario_cerrado(monkeypatch):
    """Filtros por platform y kind; vocabulario cerrado: valores ajenos -> 422
    (jamas un filtro vacio que mienta, patron /audit)."""
    with _db_temporal("orbit_dash_filtros") as (conn, dsn):
        config_id = _config_version(conn, {})
        camp_us = _campana(conn, "amazon_us", "9001", name="US")
        camp_mx = _campana(conn, "amazon_mx", "9002", name="MX")
        ciclo = _ciclo(conn, platform="amazon_us")
        id_bid = _decision(
            conn,
            ciclo,
            camp_us,
            kind="bid",
            config_id=config_id,
            moneda="USD",
            inputs={"motor": "bid", "motivo": "banda_mas_15", "target_acos_pct_usado": "25.00"},
        )
        id_neg = _decision(
            conn,
            ciclo,
            camp_mx,
            kind="negative",
            config_id=config_id,
            search_term="girasol",
            window_end=dt.date(2026, 8, 12),
            inputs={
                "motor": "hygiene",
                "motivo": "negative_umbral",
                "target_acos_pct_usado": "20.00",
            },
        )
        cliente = _cliente(dsn, monkeypatch)

        # sin filtros: ambas decisiones (orden id DESC)
        r0 = cliente.get("/api/dashboard/decisiones")
        assert [i["id"] for i in r0.json()["items"]] == [id_neg, id_bid]

        r = cliente.get("/api/dashboard/decisiones", params={"platform": "amazon_mx"})
        assert [i["id"] for i in r.json()["items"]] == [id_neg]
        assert r.json()["items"][0]["plataforma"] == "amazon_mx"

        r2 = cliente.get("/api/dashboard/decisiones", params={"kind": "negative"})
        assert [i["id"] for i in r2.json()["items"]] == [id_neg]
        assert r2.json()["items"][0]["search_term"] == "girasol"

        assert cliente.get("/api/dashboard/decisiones", params={"kind": "x"}).status_code == 422
        assert (
            cliente.get("/api/dashboard/decisiones", params={"platform": "meli"}).status_code == 422
        )


# ---------------------------------------------------------------------------
# 1.5 - CANDADO del historico (corre sin Postgres): acotado a 14 ciclos
# ---------------------------------------------------------------------------


def test_sql_historico_14d_acotado():
    """Candado (regla 9, corre sin Postgres): el historico de /salud esta
    ACOTADO a 14 ciclos por plataforma (LIMIT 14) — un historico sin tope
    escala con la historia del envelope (append-only)."""
    sql = dash._SQL_HISTORICO_14D.replace("%s", "NULL")
    normalizada = " ".join(pglast.prettify(sql).lower().split())
    assert "limit 14" in normalizada, "el historico debe estar acotado a 14 ciclos"


# ---------------------------------------------------------------------------
# 1.5 - INTEGRACION /salud: snapshot, historico 14d, notes mixto, skips
# ---------------------------------------------------------------------------


def json_dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_salud_snapshot_ultimo_ciclo_historico_14d_y_watermarks(monkeypatch):
    """Snapshot del ULTIMO ciclo por plataforma + historico ACOTADO a 14d +
    watermarks (las mismas fuentes del motor: v_metric_latest y synced_at)."""
    with _db_temporal("orbit_dash_salud") as (conn, dsn):
        run = _run(conn)
        camp = _campana(conn, "amazon_us", "9001", name="A")
        _metrica(
            conn,
            run,
            camp,
            dt.date(2026, 8, 22),
            cost="1.0000",
            ad_revenue="3.0000",
            clicks=1,
            moneda="USD",
        )
        _estado_acos(conn, camp, Decimal("25"))
        notas_us = json_dumps(
            {"skips": {"entidad": {"estado_no_enabled": 3}}, "decisiones": {"bid": 1}}
        )
        ids = []
        for _i in range(20):
            ids.append(_ciclo(conn, platform="amazon_us", notes=notas_us))
        ciclo_mx = _ciclo(conn, platform="amazon_mx", notes="rastro: ciclo muerto")
        _hoy(monkeypatch, dt.date(2026, 8, 24))
        data = _cliente(dsn, monkeypatch).get("/api/dashboard/salud").json()["plataformas"]

        us = data["amazon_us"]
        assert us["ultimo_ciclo"]["id"] == ids[-1]  # el de mayor id
        assert us["ultimo_ciclo"]["notes"]["skips"] == {"entidad": {"estado_no_enabled": 3}}
        assert len(us["historico_14d"]) == 14  # ACOTADO aunque haya 20
        assert us["historico_14d"][0]["cycle_id"] == ids[-1]
        assert us["watermark"] == "2026-08-22"
        assert us["synced_at"] is not None

        mx = data["amazon_mx"]
        assert mx["ultimo_ciclo"]["id"] == ciclo_mx
        assert mx["ultimo_ciclo"]["notes"] == {"texto": "rastro: ciclo muerto"}  # formato mixto
        assert mx["watermark"] is None


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_salud_notes_mixto_y_ciclos_degradado_failed_con_motivo(monkeypatch):
    """DoD: ciclo degraded/failed VISIBLE con motivo. Notes de FORMATO MIXTO
    (JSON con motivo_skip guarda_* y texto plano 'rastro: ...' en ciclos
    muertos reclamados) — _parse_notes tolera ambos y el motivo se traduce."""
    with _db_temporal("orbit_dash_notes") as (conn, dsn):
        notas_degradado = json_dumps(
            {
                "skips": {"entidad": {}},
                "motivo_skip": "guarda_watermark",
                "detalle": "watermark viejo",
            }
        )
        _ciclo(conn, platform="amazon_us", status="degraded", notes=notas_degradado)
        _ciclo(conn, platform="amazon_us", status="failed", notes="rastro: fallo del ciclo")
        _hoy(monkeypatch, dt.date(2026, 8, 24))
        historico = (
            _cliente(dsn, monkeypatch)
            .get("/api/dashboard/salud")
            .json()["plataformas"]["amazon_us"]["historico_14d"]
        )
        por_id = {h["cycle_id"]: h for h in historico}
        degradado = [h for h in historico if h["status"] == "degraded"][0]
        assert degradado["motivo"] == "Watermark de la plataforma vencido (> 7 dias)"
        failed = [h for h in historico if h["status"] == "failed"][0]
        assert failed["motivo"] == "rastro: fallo del ciclo"
        assert por_id  # sanity: hay filas


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_salud_skips_traducidos_del_orquestador(monkeypatch):
    """DoD: skips agregados por motivo con su traduccion (vocabulario del
    ORQUESTADOR + MOTIVO_* que el orquestador importa a sus contadores);
    motivo desconocido -> fallback al id crudo sin crash (decision 11)."""
    with _db_temporal("orbit_dash_skips") as (conn, dsn):
        _ciclo(
            conn,
            platform="amazon_us",
            notes=json_dumps(
                {
                    "skips": {
                        "entidad": {"estado_no_enabled": 3200, "rango_bloquea_ajuste": 44},
                        "termino": {
                            "asin_like": 84,
                            "sin_umbral_negative": 547,
                            "motivo_futuro": 1,
                        },
                    },
                    "decisiones": {"bid": 4},
                }
            ),
        )
        _hoy(monkeypatch, dt.date(2026, 8, 24))
        skips = (
            _cliente(dsn, monkeypatch)
            .get("/api/dashboard/salud")
            .json()["plataformas"]["amazon_us"]["skips"]
        )
        assert skips["entidad"]["estado_no_enabled"] == {
            "count": 3200,
            "motivo_es": "Entidad sin estado o no habilitada",
        }
        assert (
            skips["entidad"]["rango_bloquea_ajuste"]["motivo_es"]
            == "Rango [floor, ceiling] bloquea el ajuste"
        )
        assert skips["termino"]["asin_like"]["count"] == 84
        assert skips["termino"]["sin_umbral_negative"]["motivo_es"] == (
            "Sin umbral de negative (clicks o costo bajo)"
        )
        # motivo desconocido -> fallback al id crudo, sin crash
        assert skips["termino"]["motivo_futuro"]["motivo_es"] == "motivo_futuro"


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_salud_plataforma_sin_datos_devuelve_null_y_vacio(monkeypatch):
    """Plataforma sin ciclos ni metricas: nulls y vacios, jamas 404 ni ceros
    inventados (regla 3)."""
    with _db_temporal("orbit_dash_salud_vacio") as (_conn, dsn):
        _hoy(monkeypatch, dt.date(2026, 8, 24))
        plataformas = _cliente(dsn, monkeypatch).get("/api/dashboard/salud").json()["plataformas"]
        for p in ("amazon_us", "amazon_mx"):
            assert plataformas[p]["ultimo_ciclo"] is None
            assert plataformas[p]["historico_14d"] == []
            assert plataformas[p]["skips"] == {"entidad": {}, "termino": {}}
            assert plataformas[p]["watermark"] is None
            assert plataformas[p]["synced_at"] is None
