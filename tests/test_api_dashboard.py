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
from app.optimizer import hygiene

_DIA = dt.timedelta(days=1)

# Reloj FIJO tz-aware (determinismo): los seeds de decisiones y ciclos se
# cuelgan de AHORA (patron test_api).
AHORA = dt.datetime(2026, 8, 22, 12, 0, tzinfo=dt.UTC)
DECIDED_AT = AHORA

SQL_MIGRACION = (
    Path(__file__).resolve().parent.parent / "migrations" / "0001_initial.sql"
).read_text(encoding="utf-8")
# SP-API 01 A.5: /salud siempre trae el bloque spapi, que lee
# ingest_run.llamadas (0033) y .platform (0036). Aditivas puras.
for _mig in (
    "0033_ingest_run_llamadas.sql",
    "0034_ingest_run_llamadas_grant.sql",
    "0036_ingest_run_platform.sql",
    "0037_ingest_run_salud_idx.sql",
):
    SQL_MIGRACION += (Path(__file__).resolve().parent.parent / "migrations" / _mig).read_text(
        encoding="utf-8"
    )

# CORTES UI 01: la cola apply_queue vive en la migracion 0002.
SQL02 = (Path(__file__).resolve().parent.parent / "migrations" / "0002_apply.sql").read_text(
    encoding="utf-8"
)

# ADS PROTECCION C.4: propuestas de campana (pantalla + /salud).
SQL42 = (
    Path(__file__).resolve().parent.parent / "migrations" / "0042_ads_campaign_proposal.sql"
).read_text(encoding="utf-8")

# BIDS 01 1.3: la vista v_entidad_inerte (migracion 0013) es la UNICA fuente
# de "inerte" (D2 sellada); el endpoint la lee, no reimplementa el diagnostico.
SQL13 = (
    Path(__file__).resolve().parent.parent / "migrations" / "0013_entidad_inerte.sql"
).read_text(encoding="utf-8")

# A5: /campanas lee el mapeo campana -> familia (kind 'product_ad', 0004),
# la membresia 0047 y la vista v_margen_familia 0048.
SQL04 = (
    Path(__file__).resolve().parent.parent / "migrations" / "0004_ad_entity_kind_product_ad.sql"
).read_text(encoding="utf-8")
SQL46 = (
    Path(__file__).resolve().parent.parent / "migrations" / "0046_target_acos_ciclo.sql"
).read_text(encoding="utf-8")
SQL47 = (Path(__file__).resolve().parent.parent / "migrations" / "0047_familias.sql").read_text(
    encoding="utf-8"
)
SQL48 = (
    Path(__file__).resolve().parent.parent / "migrations" / "0048_margen_familia.sql"
).read_text(encoding="utf-8")

# BIDS 01 2.6: /inertes muestra la puerta de antiguedad del archivado
# (first_seen_at, migracion 0017) y el resumen del ledger de lotes
# (keyword_archivo_manual, migracion 0014).
SQL14 = (
    Path(__file__).resolve().parent.parent / "migrations" / "0014_keyword_archivo_manual.sql"
).read_text(encoding="utf-8")
SQL17 = (
    Path(__file__).resolve().parent.parent / "migrations" / "0017_first_seen_at.sql"
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
        conn.execute(SQL04)  # 0004 (A5): /campanas lee kind 'product_ad'
        conn.execute(SQL46)  # 0046 (A5): snapshot target_acos_ciclo
        conn.execute(SQL47)  # 0047 (A5): membresia producto_familia
        conn.execute(SQL48)  # 0048 (A5): vista v_margen_familia
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


def _product_target(conn, platform: str, external: str, parent: int, name: str) -> int:
    return conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id, parent_id, name)"
        " VALUES (%s, 'product_target', %s, %s, %s) RETURNING id",
        (platform, external, parent, name),
    ).fetchone()[0]


def _metrica(
    conn,
    run_id,
    ad_entity_id,
    fecha,
    *,
    cost,
    ad_revenue,
    clicks,
    moneda,
    observed_at=None,
    impressions=None,
    orders=None,
) -> None:
    """Una observacion de metricas (bitemporal: observed_at controlable).

    BIDS 01 1.3: `impressions`/`orders` explicitos (default None =
    comportamiento de antes) para sembrar hojas inertes con forma de
    reporte (impresiones reales viejas) o peso muerto (sin filas)."""
    conn.execute(
        "INSERT INTO ads_metric_observation (ad_entity_id, metric_date, observed_at,"
        " metric_currency, cost, ad_revenue, impressions, clicks, orders, ingest_run_id)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            ad_entity_id,
            fecha,
            observed_at or _obs(fecha),
            moneda,
            None if cost is None else Decimal(cost),
            None if ad_revenue is None else Decimal(ad_revenue),
            impressions,
            clicks,
            orders,
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
    """Una decision (inputs JSONB congelados). CHECKs sellados: bid/budget/
    harvest exigen moneda (decision_valor_con_moneda) aunque los valores vayan
    null; pause/negative/harvest son CORTES y el trigger decision_madurez_corte
    exige window_end <= decided_at - 10d. pause SI tolera old/new/moneda NULL
    (trampa real de la evidencia) — pero NO esta exento de la madurez."""
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


def test_feed_sql_compuesto_parsea_con_todos_los_filtros():
    """Regla 9 (bug REAL del bloque 2): _filtros_feed devolvia un string ya
    unido y decisiones() lo trataba como lista -> " AND ".join() re-unia
    caracter por caracter (SQL basura con cualquier filtro) y .append()
    reventaba con cursor. Los tests de integracion lo atrapan en CI, pero
    skipean sin Postgres: este candado compone el SQL EXACTAMENTE como el
    endpoint (todas las combinaciones de filtros) y lo parsea con pglast,
    atrapando la clase entera en cualquier maquina."""
    for platform in (None, "amazon_us"):
        for kind in (None, "bid"):
            for cursor in (None, 42):
                clausulas, params = dash._filtros_feed(platform, kind)
                assert isinstance(clausulas, list), "los filtros del feed son lista, no string"
                if cursor is not None:
                    clausulas.append("d.id < %s")
                    params.append(cursor)
                sql = dash._SQL_DECISIONES_FEED.format(
                    filtros=" AND ".join(clausulas) or "true"
                ).replace("%s", "NULL")
                assert pglast.parse_sql(sql), (platform, kind, cursor)


def test_sql_del_modulo_dashboard_parsea_como_postgres():
    """Sintaxis de las SQL del modulo (patron test_api): pglast valida que las
    constantes parsean como Postgres real (un typo muere en CI, no en prod)."""
    from app import dashboard_pagina as pagina

    for modulo, etiqueta in ((dash, "api_dashboard"), (pagina, "dashboard_pagina")):
        nombres = sorted(n for n in vars(modulo) if n.startswith("_SQL_"))
        assert nombres, f"no se encontraron constantes _SQL_* en app/{etiqueta}"
        for nombre in nombres:
            sql = getattr(modulo, nombre).replace("%s", "NULL").replace("{filtros}", "true")
            assert pglast.parse_sql(sql), f"{etiqueta}.{nombre} no parseo"


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


def test_page_window_clampa_y_deriva():
    v = dash.PageWindow.desde_total(total=101, page=99, page_size=50)
    assert (v.page, v.pages, v.offset, v.prev, v.next) == (3, 3, 100, 2, None)
    vacia = dash.PageWindow.desde_total(total=0, page=1, page_size=50)
    assert (vacia.pages, vacia.prev, vacia.next) == (1, None, None)
    with pytest.raises(ValueError):
        dash.PageWindow.desde_total(total=1, page=1, page_size=0)


def test_sql_pagina_decisiones_offset_sobre_el_mismo_from():
    sql = dash._SQL_DECISIONES_PAGINA.replace("%s", "NULL")
    normalizada = " ".join(pglast.prettify(sql).lower().split())
    assert "join ad_entity" in normalizada
    assert "order by d.id desc" in normalizada
    assert "offset" in normalizada
    assert "limit" in normalizada
    feed = " ".join(
        pglast.prettify(
            dash._SQL_DECISIONES_FEED.replace("%s", "NULL").replace("{filtros}", "true")
        )
        .lower()
        .split()
    )
    assert "offset" not in feed


# ---------------------------------------------------------------------------
# 1.4 - INTEGRACION /campanas: procedencia en los 5 peldanos, goal, anti-mezcla
# ---------------------------------------------------------------------------


def _ciclo_live(conn, platform: str) -> int:
    """Ciclo live+done (el congelado solo lee esos)."""
    return conn.execute(
        "INSERT INTO optimizer_cycle (motor, mode, platform, status, finished_at,"
        " decisions_count) VALUES ('ads_optimizer', 'live', %s::platform, 'done', %s, 0)"
        " RETURNING id",
        (platform, AHORA),
    ).fetchone()[0]


def _congelado(conn, cycle_id: int, hoja_id: int, target, procedencia, decided_at=None) -> None:
    conn.execute(
        "INSERT INTO target_acos_ciclo (cycle_id, ad_entity_id, decided_at,"
        " target_acos_pct, procedencia) VALUES (%s, %s, %s, %s, %s)",
        (cycle_id, hoja_id, decided_at or AHORA, str(target), procedencia),
    )


def _campana_con_hojas(conn, run, platform, external, hojas):
    """Campana con metrica 30d + ad group + keywords (aparece en items)."""
    camp = _campana(conn, platform, external, name=external)
    ag = _grupo(conn, platform, f"{external}-G", camp)
    ids = [_keyword(conn, platform, f"{external}-K{n}", ag, f"kw {n}") for n in range(hojas)]
    _metrica(
        conn,
        run,
        camp,
        dt.date(2026, 8, 20),
        cost="1.0000",
        ad_revenue="3.0000",
        clicks=1,
        moneda="USD" if platform == "amazon_us" else "MXN",
    )
    return camp, ids


def test_congelado_unico_compara_redondeados():
    """A7r3 F4: 29.501 y 29.504 difieren en crudo pero muestran "29.50":
    unico compara lo ya redondeado, nunca sale "29.50–29.50"."""
    cong = dash.CongeladoCampana(
        minimo=Decimal("29.501"),
        maximo=Decimal("29.504"),
        procedencias=frozenset({"margen_familia"}),
        ciclo_ids=frozenset({7}),
        decideds=frozenset({dt.datetime(2026, 8, 20, 12, tzinfo=dt.UTC)}),
    )
    assert cong.display()["valor"] == "29.50"


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_campanas_muestra_congelado_unico_rango_y_null(monkeypatch):
    """A7 B3: /campanas publica lo que congelo el ULTIMO ciclo live+done,
    no la historia de cada hoja: unico a 2 decimales, rango "min–max" si
    difieren en ese ciclo, null-honesto sin ciclo. Trampas: shadow,
    running y hojas viejas no se cuelan; el goal sigue resuelto."""
    with _db_temporal("orbit_dash_cong") as (conn, dsn):
        run = _run(conn)
        _config_version(conn, {"ads_optimizer_mode": "shadow"})
        camp_a, (a1, a2) = _campana_con_hojas(conn, run, "amazon_us", "9001", 2)
        camp_b, (b1, b2, b3) = _campana_con_hojas(conn, run, "amazon_us", "9002", 3)
        camp_c, _ = _campana_con_hojas(conn, run, "amazon_mx", "9003", 1)
        camp_d, (d1, d2) = _campana_con_hojas(conn, run, "amazon_us", "9004", 2)
        _goal_db(conn, scope="campaign", ad_entity_id=camp_a, target="18")
        c1 = _ciclo_live(conn, "amazon_us")
        c2 = _ciclo_live(conn, "amazon_us")
        manana = AHORA + dt.timedelta(days=1)
        _congelado(conn, c1, a1, "28.5", "margen_familia")
        _congelado(conn, c2, a2, "29.50", "margen_familia")
        _congelado(conn, c1, b1, "28.5", "margen_familia")
        _congelado(conn, c2, b2, "29.5", "margen_familia", decided_at=manana)
        _congelado(conn, c1, b3, "27.0", "margen_familia")
        _congelado(conn, c2, d1, "28.0", "margen_familia")
        _congelado(conn, c2, d2, "29.5", "margen_familia")
        # Trampas anti-filtro (IDs mayores que c1/c2): sin mode='live' o
        # status='done' el filtro del ultimo ciclo las excluye; si colaran,
        # los asserts de camp_a mueren.
        c_shadow = _ciclo(conn, platform="amazon_us")
        _congelado(conn, c_shadow, a1, "99.9", "margen_familia")
        c_running = conn.execute(
            "INSERT INTO optimizer_cycle (motor, mode, platform, status,"
            " decisions_count) VALUES ('ads_optimizer', 'live',"
            " 'amazon_us'::platform, 'running', 0) RETURNING id"
        ).fetchone()[0]
        _congelado(conn, c_running, a1, "88.8", "margen_familia")
        _hoy(monkeypatch, dt.date(2026, 8, 24))
        resp = _cliente(dsn, monkeypatch).get("/api/dashboard/campanas")
        assert resp.status_code == 200, resp.text
        por_id = {i["ad_entity_id"]: i for i in resp.json()["items"]}
        assert por_id[camp_a]["target_efectivo"] == {
            "valor": "29.50",
            "minimo": "29.50",
            "maximo": "29.50",
            "peldano": "margen_familia",
            "ciclo": {
                "id_min": c2,
                "id_max": c2,
                "decided_min": AHORA.isoformat(),
                "decided_max": AHORA.isoformat(),
            },
        }
        assert por_id[camp_b]["target_efectivo"] == {
            "valor": "29.50",
            "minimo": "29.50",
            "maximo": "29.50",
            "peldano": "margen_familia",
            "ciclo": {
                "id_min": c2,
                "id_max": c2,
                "decided_min": manana.isoformat(),
                "decided_max": manana.isoformat(),
            },
        }
        assert por_id[camp_d]["target_efectivo"] == {
            "valor": "28.00–29.50",
            "minimo": "28.00",
            "maximo": "29.50",
            "peldano": "margen_familia",
            "ciclo": {
                "id_min": c2,
                "id_max": c2,
                "decided_min": AHORA.isoformat(),
                "decided_max": AHORA.isoformat(),
            },
        }
        assert por_id[camp_c]["target_efectivo"] is None
        assert por_id[camp_a]["goal"]["scope"] == "campaign"
        assert por_id[camp_c]["goal"] is None


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_campanas_peldano_mixto_cuando_procedencias_difieren(monkeypatch):
    """A7 B3: hojas con la misma cifra pero distinta procedencia: valor
    unico y peldano "mixto" (default documentado)."""
    with _db_temporal("orbit_dash_mixto") as (conn, dsn):
        run = _run(conn)
        _config_version(conn, {"ads_optimizer_mode": "shadow"})
        camp, (k1, k2) = _campana_con_hojas(conn, run, "amazon_us", "9004", 2)
        c1 = _ciclo_live(conn, "amazon_us")
        _congelado(conn, c1, k1, "29.5", "margen_familia")
        _congelado(conn, c1, k2, "29.5", "setting_plataforma")
        _hoy(monkeypatch, dt.date(2026, 8, 24))
        resp = _cliente(dsn, monkeypatch).get("/api/dashboard/campanas")
        assert resp.status_code == 200, resp.text
        por_id = {i["ad_entity_id"]: i for i in resp.json()["items"]}
        assert por_id[camp]["target_efectivo"]["valor"] == "29.50"
        assert por_id[camp]["target_efectivo"]["peldano"] == "mixto"


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
        # estado publicado (feedback smoke 1.7): camp_a con estado, camp_b sin
        _estado_acos(conn, camp_a, None)
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
        # estado publicado visible (feedback smoke 1.7); sin estado -> null
        assert por_id[camp_a]["status"] == "ENABLED"
        assert por_id[camp_b]["status"] is None


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
        inputs = {"motor": "bid", "motivo": "banda_menos_12", "target_acos_pct_usado": "25.00"}
        # CHECKs sellados (fallos reales de las primeras corridas en CI): bid
        # exige moneda (decision_valor_con_moneda) y solo cabe UNA decision
        # por entidad por ciclo (decision_unica_entidad_ciclo) -> un ciclo
        # NUEVO por decision, como en la operacion real (un ciclo diario)
        ids = [
            _decision(
                conn,
                _ciclo(conn, platform="amazon_us"),
                camp,
                kind="bid",
                config_id=config_id,
                inputs=inputs,
                moneda="USD",
            )
            for _ in range(5)
        ]
        cliente = _cliente(dsn, monkeypatch)

        r1 = cliente.get("/api/dashboard/decisiones", params={"limit": 2})
        data1 = r1.json()
        assert [i["id"] for i in data1["items"]] == [ids[4], ids[3]]
        assert data1["next_cursor"] == ids[3]
        assert data1["has_more"] is True

        # insercion concurrente simulada: decision NUEVA con id mayor (su
        # propio ciclo: una decision por entidad por ciclo)
        _decision(
            conn,
            _ciclo(conn, platform="amazon_us"),
            camp,
            kind="bid",
            config_id=config_id,
            inputs=inputs,
            moneda="USD",
        )

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
            # ciclo PROPIO: bid ya decidio sobre camp en `ciclo` y el schema
            # sella una decision por entidad por ciclo
            _ciclo(conn, platform="amazon_us"),
            camp,
            kind="pause",
            config_id=config_id,
            # pause ES un corte: el trigger decision_madurez_corte exige
            # window_end <= decided_at - 10d (fallo real de la primera
            # corrida en CI: el default 08-16 lo violaba con decided 08-22)
            window_end=dt.date(2026, 8, 12),
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
def test_decisiones_entidad_humana_no_vuelca_json_de_target(monkeypatch):
    with _db_temporal("orbit_dash_etiqueta") as (conn, dsn):
        config_id = _config_version(conn, {})
        camp = _campana(conn, "amazon_us", "9001", name="Campana A")
        ag = _grupo(conn, "amazon_us", "9101", parent=camp)
        kw = _keyword(conn, "amazon_us", "9201", ag, "zapato blanco")
        target = _product_target(
            conn,
            "amazon_us",
            "9301",
            ag,
            '[{"type":"ASIN_SAME_AS","value":"B086TVLJ43"}]',
        )
        ciclo = _ciclo(conn, platform="amazon_us")
        id_kw = _decision(
            conn,
            ciclo,
            kw,
            kind="bid",
            config_id=config_id,
            moneda="USD",
            inputs={"motor": "bid", "motivo": "banda_menos_12", "target_acos_pct_usado": "25.00"},
        )
        id_tg = _decision(
            conn,
            ciclo,
            target,
            kind="bid",
            config_id=config_id,
            moneda="USD",
            inputs={"motor": "bid", "motivo": "banda_menos_12", "target_acos_pct_usado": "25.00"},
        )
        items = _cliente(dsn, monkeypatch).get("/api/dashboard/decisiones").json()["items"]
        por_id = {i["id"]: i for i in items}
        assert por_id[id_kw]["nombre"] == "zapato blanco · Campana A"
        assert por_id[id_tg]["nombre"] == "mismo ASIN B086TVLJ43 · Campana A"
        for item in items:
            assert "ASIN_SAME_AS" not in (item["nombre"] or "")
            assert "[" not in (item["nombre"] or "")


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
def test_decisiones_feed_vacio(monkeypatch):
    """Feed sin decisiones (hallazgo review): items vacios, next_cursor null,
    has_more false — jamas un crash ni un 404."""
    with _db_temporal("orbit_dash_feed_vacio") as (_conn, dsn):
        resp = _cliente(dsn, monkeypatch).get("/api/dashboard/decisiones")
        assert resp.status_code == 200
        assert resp.json() == {"items": [], "next_cursor": None, "has_more": False}


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
        # grok r2: un failed REAL persiste el error scrubbeado en notes.error
        # (_sello_fallido) y un skipped trae motivo_skip=escalera_off — ambos
        # quedaban sin motivo visible
        _ciclo(
            conn,
            platform="amazon_us",
            status="failed",
            notes=json_dumps({"skips": {"entidad": {}}, "error": "ValueError: sin watermark"}),
        )
        _ciclo(
            conn,
            platform="amazon_us",
            status="skipped",
            notes=json_dumps({"skips": {"entidad": {}}, "motivo_skip": "escalera_off"}),
        )
        _hoy(monkeypatch, dt.date(2026, 8, 24))
        historico = (
            _cliente(dsn, monkeypatch)
            .get("/api/dashboard/salud")
            .json()["plataformas"]["amazon_us"]["historico_14d"]
        )
        # next(...) revienta con StopIteration si el status no aparece: eso ES
        # el assert (el `assert por_id` que habia aqui no discriminaba nada)
        degradado = next(h for h in historico if h["status"] == "degraded")
        assert degradado["motivo"] == "Watermark de la plataforma vencido (> 7 dias)"
        motivos_failed = {h["motivo"] for h in historico if h["status"] == "failed"}
        assert motivos_failed == {"rastro: fallo del ciclo", "ValueError: sin watermark"}
        saltado = next(h for h in historico if h["status"] == "skipped")
        assert saltado["motivo"] == "Escalera global off"


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
                            "origen_es_destino": 2,
                            "destino_inconsistente": 1,
                            "sin_destino_de_harvest": 7,
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
        # A.6: los motivos F2 del resolutor llegan traducidos a /salud
        assert skips["termino"]["origen_es_destino"] == {
            "count": 2,
            "motivo_es": "Harvest saltado: el ad group de origen es el destino de la campaña",
        }
        assert skips["termino"]["destino_inconsistente"] == {
            "count": 1,
            "motivo_es": "Harvest bloqueado: la terna del goal contradice la exacta del grupo",
        }
        assert skips["termino"]["sin_destino_de_harvest"] == {
            "count": 7,
            "motivo_es": "Harvest sin destino: sin grupo, sin excepcion y sin terna",
        }
        # motivo desconocido -> fallback al id crudo, sin crash
        assert skips["termino"]["motivo_futuro"]["motivo_es"] == "motivo_futuro"


# ---------------------------------------------------------------------------
# FABRICA 02 A.6: motivos F2 traducidos donde se ven los skips
# ---------------------------------------------------------------------------

MOTIVOS_ES_F2 = [
    (
        hygiene.MOTIVO_ORIGEN_ES_DESTINO,
        "Harvest saltado: el ad group de origen es el destino de la campaña",
    ),
    (
        hygiene.MOTIVO_SIN_DESTINO_HARVEST,
        "Harvest sin destino: sin grupo, sin excepcion y sin terna",
    ),
    (
        hygiene.MOTIVO_DESTINO_INCONSISTENTE,
        "Harvest bloqueado: la terna del goal contradice la exacta del grupo",
    ),
    (
        hygiene.MOTIVO_DESTINO_DESINCRONIZADO,
        "Harvest descartado: la exacta del grupo cambio despues de decidir",
    ),
    (
        hygiene.MOTIVO_MIGRACION_PENDIENTE,
        "Destino por terna del goal (migracion a grupo o excepcion pendiente)",
    ),
]


@pytest.mark.parametrize("motivo, esperado", MOTIVOS_ES_F2)
def test_salud_motivos_f2_traducidos_con_texto_exacto(motivo, esperado):
    """A.6: los cinco motivos que A.1 agrego a hygiene entran a
    MOTIVOS_ES_SALUD con su texto exacto. El `!=` mata al mutante que
    devuelve el id crudo (el fallback de _skips_traducidos lo tragaria)."""
    assert dash.MOTIVOS_ES_SALUD[motivo] != motivo
    assert dash.MOTIVOS_ES_SALUD[motivo] == esperado


def test_salud_motivo_es_helper_fallback_y_none():
    """A.6: `motivo_es` traduce por MOTIVOS_ES_SALUD, cae al id crudo con
    motivos desconocidos y pasa None a None (regla 3)."""
    assert dash.motivo_es(hygiene.MOTIVO_DESTINO_INCONSISTENTE) == (
        "Harvest bloqueado: la terna del goal contradice la exacta del grupo"
    )
    assert dash.motivo_es("motivo_futuro") == "motivo_futuro"
    assert dash.motivo_es(None) is None


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_salud_harvest_destino_bloque_con_resueltos_y_saltos(monkeypatch):
    """A.6: /salud expone harvest_destino del ultimo ciclo — resueltos por
    procedencia, terna_es exacto y saltos de grupo traducidos."""
    with _db_temporal("orbit_dash_hdest") as (conn, dsn):
        _ciclo(
            conn,
            platform="amazon_us",
            notes=json_dumps(
                {
                    "skips": {"entidad": {}, "termino": {}},
                    "decisiones": {},
                    "harvest_destino": {
                        "resueltos": {"grupo": 3, "excepcion": 0, "terna": 241},
                        "saltos_grupo": {"6102": "destino_inconsistente"},
                    },
                }
            ),
        )
        _hoy(monkeypatch, dt.date(2026, 8, 24))
        bloque = (
            _cliente(dsn, monkeypatch)
            .get("/api/dashboard/salud")
            .json()["plataformas"]["amazon_us"]["harvest_destino"]
        )
        assert bloque == {
            "resueltos": {"grupo": 3, "excepcion": 0, "terna": 241},
            "terna_es": ("Destino por terna del goal (migracion a grupo o excepcion pendiente)"),
            "saltos_grupo": [
                {
                    "campaign_id": 6102,
                    "motivo": "destino_inconsistente",
                    "motivo_es": (
                        "Harvest bloqueado: la terna del goal contradice la exacta del grupo"
                    ),
                }
            ],
        }


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_salud_sin_clave_harvest_destino_es_none(monkeypatch):
    """A.6: ciclo sin la clave (pre-A.6) o plataforma sin ciclos ->
    harvest_destino null. Mata al mutante que inventa resueltos {0,0,0}."""
    with _db_temporal("orbit_dash_hdest_none") as (conn, dsn):
        _ciclo(
            conn,
            platform="amazon_us",
            notes=json_dumps({"skips": {"entidad": {}, "termino": {}}, "decisiones": {}}),
        )
        _hoy(monkeypatch, dt.date(2026, 8, 24))
        plataformas = _cliente(dsn, monkeypatch).get("/api/dashboard/salud").json()["plataformas"]
        assert plataformas["amazon_us"]["harvest_destino"] is None
        assert plataformas["amazon_mx"]["harvest_destino"] is None


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


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_salud_nota_telegram_visible_en_la_respuesta(monkeypatch):
    """ORBIT 04 3.3 (sellado 2): la NOTA notes['telegram'] del ciclo — la
    unica visibilidad del fallo del canal — sobrevive el parseo y llega a la
    respuesta de /salud (un ciclo 'done' con el canal caido NO se disfraza
    de salud perfecta)."""
    with _db_temporal("orbit_dash_salud_tg") as (conn, dsn):
        notas = json_dumps(
            {
                "skips": {"entidad": {}},
                "apply": {"cortes_encolados": {"live": 1, "shadow": 0, "choques": 0}},
                "telegram": {
                    "aviso_encola": "fallo: aviso de corte encolado no enviado por Telegram",
                    "digest": "fallo: digest del ciclo no enviado por Telegram",
                },
            }
        )
        _ciclo(conn, platform="amazon_us", notes=notas)
        _hoy(monkeypatch, dt.date(2026, 8, 24))
        ultimo = (
            _cliente(dsn, monkeypatch)
            .get("/api/dashboard/salud")
            .json()["plataformas"]["amazon_us"]["ultimo_ciclo"]
        )
        assert ultimo["status"] == "done"
        assert ultimo["notes"]["telegram"]["aviso_encola"].startswith("fallo:")
        assert ultimo["notes"]["telegram"]["digest"].startswith("fallo:")


def test_motivos_salud_traducen_los_gates_de_ancestros():
    """CAMPANA ACTIVA 01: los dos motivos nuevos del orquestador tienen
    traduccion en /salud (sin ella la pantalla mostraria el id crudo)."""
    from app import cycle as ciclo
    from app.api_dashboard import MOTIVOS_ES_SALUD

    assert MOTIVOS_ES_SALUD[ciclo.MOTIVO_CAMPANA_NO_ENABLED].startswith("Campaña")
    assert MOTIVOS_ES_SALUD[ciclo.MOTIVO_GRUPO_NO_ENABLED].startswith("Ad group")


def test_motivo_inversion_sin_evidencia_traducido_en_salud():
    """R-D2-1 (DeepSeek F1 Low en #357): inversion_sin_evidencia con
    traduccion en /salud (sin ella la pantalla mostraria el id crudo).
    A6-r2 F5: la etiqueta nombra la regla de CADA politica (10 dias en
    v1, 20 clics post-cambio en v2): un texto solo-dias mentiria bajo
    evidencia_v2."""
    from app import cycle as ciclo
    from app.api_dashboard import MOTIVOS_ES_SALUD

    texto = MOTIVOS_ES_SALUD[ciclo.MOTIVO_INVERSION_SIN_EVIDENCIA]
    assert texto != ciclo.MOTIVO_INVERSION_SIN_EVIDENCIA
    assert texto == (
        "Inversión frenada: el último bid aplicado tiene menos de 10 días de evidencia"
        " (bandas v1) o menos de 20 clics post-cambio (evidencia v2)"
    )


def test_motivo_cero_ventas_tiene_etiqueta_en_decisiones():
    """BIDS 01: el motivo nuevo de cero ventas tiene traduccion en el feed
    (sin ella la pantalla mostraria el id crudo)."""
    from app.api_dashboard import MOTIVOS_ES_DECISIONES
    from app.optimizer import bid as bid_mod

    assert MOTIVOS_ES_DECISIONES[bid_mod.MOTIVO_BANDA_MENOS_25_CERO_VENTAS].startswith(
        "Cero ventas"
    )


# ---------------------------------------------------------------------------
# BIDS 01 1.3 - /inertes: lee v_entidad_inerte (regla 2: la vista es la
# UNICA fuente; el endpoint no reimplementa el diagnostico)
# ---------------------------------------------------------------------------


def _siembra_inertes(conn):
    """Dos hojas inertes de distinta clasificacion + ancla de watermark +
    hoja con trafico reciente (NO aparece)."""
    run = _run(conn)
    camp = _campana(conn, "amazon_us", "8101", name="Campana Inerte")
    ag = _grupo(conn, "amazon_us", "8102", camp)
    gasto = _keyword(conn, "amazon_us", "8103", ag, "gasto sin ventas")
    muerto = _keyword(conn, "amazon_us", "8104", ag, "peso muerto")
    viva = _keyword(conn, "amazon_us", "8105", ag, "con trafico")
    ancla = _keyword(conn, "amazon_us", "8106", ag, "ancla")
    for eid in (camp, ag, gasto, muerto, viva, ancla):
        _estado_acos(conn, eid, None)
    # Watermark US = 08-16 (viva + ancla). Ventana 14d: metric_date > 08-02.
    _metrica(
        conn,
        run,
        gasto,
        dt.date(2026, 7, 28),
        cost="12.50",
        ad_revenue="0.00",
        clicks=4,
        orders=0,
        impressions=40,
        moneda="USD",
    )
    _metrica(
        conn,
        run,
        viva,
        dt.date(2026, 8, 16),
        cost="3.00",
        ad_revenue="0.00",
        clicks=2,
        orders=0,
        impressions=30,
        moneda="USD",
    )
    _metrica(
        conn,
        run,
        ancla,
        dt.date(2026, 8, 16),
        cost="1.00",
        ad_revenue="0.00",
        clicks=1,
        orders=0,
        impressions=10,
        moneda="USD",
    )


def test_router_dashboard_expone_inertes_get():
    """Superficie: GET /api/dashboard/inertes existe y es solo GET (el
    candado de solo-GET lo cubre el test de superficie existente)."""
    rutas = app.openapi()["paths"]
    assert "/api/dashboard/inertes" in rutas, "falta la ruta de inertes en el router"
    assert set(rutas["/api/dashboard/inertes"]) == {"get"}


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_inertes_devuelve_shape_y_totales(monkeypatch):
    """BIDS 01 1.3: dos hojas inertes de distinta clasificacion -> shape con
    items + totales por plataforma y clasificacion; la hoja con trafico
    reciente NO aparece; dinero con moneda y NULL como null."""
    with _db_temporal("orbit_dash_inertes") as (conn, dsn):
        conn.execute(SQL13)
        conn.execute(SQL17)
        conn.execute(SQL14)
        _siembra_inertes(conn)
        resp = _cliente(dsn, monkeypatch).get("/api/dashboard/inertes")
        assert resp.status_code == 200, resp.text
        cuerpo = resp.json()
        assert cuerpo["totales"] == {"amazon_us": {"gasto_sin_ventas": 1, "peso_muerto": 1}}
        items = cuerpo["items"]
        assert [i["texto"] for i in items] == ["gasto sin ventas", "peso muerto"]
        gasto = items[0]
        assert gasto["plataforma"] == "amazon_us"
        assert gasto["kind"] == "keyword"
        assert gasto["external_id"] == "8103"
        assert gasto["campana"] == "Campana Inerte"
        assert gasto["clasificacion"] == "gasto_sin_ventas"
        assert gasto["dias_sin_impresiones"] == 19
        assert gasto["ultima_impresion"] == "2026-07-28"
        assert gasto["gasto_90d"] == "12.5000"
        assert gasto["moneda"] == "USD"
        assert gasto["ordenes_90d"] == 0
        muerto = items[1]
        assert muerto["texto"] == "peso muerto"
        assert muerto["clasificacion"] == "peso_muerto"
        assert muerto["dias_sin_impresiones"] is None
        assert muerto["ultima_impresion"] is None
        assert muerto["gasto_90d"] == "0"
        assert muerto["moneda"] is None
        assert muerto["ordenes_90d"] == 0


def _siembra_lotes_archivo(conn, keyword_id: int) -> None:
    """Dos lotes en el ledger keyword_archivo_manual (0014): uno viejo con
    applied + failed y uno nuevo planeado. `applied` exige ack y readback
    (CONSTRAINT archivo_evidencia_applied)."""
    conn.execute(
        "INSERT INTO keyword_archivo_manual (lote, ad_entity_id, platform,"
        " campaign_external, ad_group_external, keyword_external, keyword_text,"
        " match_type, clasificacion, go_literal, intentado_at, ack,"
        " readback_estado, estado)"
        " VALUES ('inertes-2026-09-01', %s, 'amazon_us', '8101', '8102', '8103',"
        " 'gasto sin ventas', 'EXACT', 'gasto_sin_ventas', 'si, archivala',"
        " '2026-09-01 10:00+00', '{}'::jsonb, 'ARCHIVED', 'applied')",
        (keyword_id,),
    )
    conn.execute(
        "INSERT INTO keyword_archivo_manual (lote, ad_entity_id, platform,"
        " campaign_external, ad_group_external, keyword_external, keyword_text,"
        " match_type, clasificacion, go_literal, intentado_at, estado)"
        " VALUES ('inertes-2026-09-01', %s, 'amazon_us', '8101', '8102', '8104',"
        " 'peso muerto', 'EXACT', 'peso_muerto', 'si, archivala',"
        " '2026-09-01 10:05+00', 'failed')",
        (keyword_id,),
    )
    conn.execute(
        "INSERT INTO keyword_archivo_manual (lote, ad_entity_id, platform,"
        " campaign_external, ad_group_external, keyword_external, keyword_text,"
        " match_type, clasificacion, go_literal, intentado_at, estado)"
        " VALUES ('inertes-2026-09-04', %s, 'amazon_us', '8101', '8102', '8103',"
        " 'gasto sin ventas', 'EXACT', 'gasto_sin_ventas', 'dale',"
        " '2026-09-04 09:00+00', 'planeado')",
        (keyword_id,),
    )


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_inertes_en_espera_por_antiguedad(monkeypatch):
    """BIDS 01 2.6: en_espera es ESPEJO del predicado del tool
    (tools/archiva_inertes.py _SQL_PLAN, default --min-antiguedad-dias=30):
    hoja recien vista (first_seen_at = hoy, default del seed) -> en_espera
    true y archivable_desde = hoy + 30; hoja vieja (60d, pisada con UPDATE)
    -> en_espera false. Sin lotes sembrados -> lotes = []."""
    with _db_temporal("orbit_dash_inertes_espera") as (conn, dsn):
        conn.execute(SQL13)
        conn.execute(SQL17)
        conn.execute(SQL14)
        _siembra_inertes(conn)
        conn.execute(
            "UPDATE ad_entity SET first_seen_at = (now() AT TIME ZONE 'UTC')::date - 60"
            " WHERE external_id = '8104'"
        )
        # El reloj lo da la DB (misma base del predicado SQL): nada de
        # dt.date.today() del lado de Python.
        hoy = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
        resp = _cliente(dsn, monkeypatch).get("/api/dashboard/inertes")
        assert resp.status_code == 200, resp.text
        cuerpo = resp.json()
        por_texto = {i["texto"]: i for i in cuerpo["items"]}
        reciente = por_texto["gasto sin ventas"]
        assert reciente["first_seen_at"] == hoy.isoformat()
        assert reciente["en_espera"] is True
        assert reciente["archivable_desde"] == (hoy + dt.timedelta(days=30)).isoformat()
        vieja = por_texto["peso muerto"]
        assert vieja["first_seen_at"] == (hoy - dt.timedelta(days=60)).isoformat()
        assert vieja["en_espera"] is False
        assert vieja["archivable_desde"] == (hoy - dt.timedelta(days=30)).isoformat()
        assert cuerpo["lotes"] == []


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_inertes_lotes_resumen_por_lote(monkeypatch):
    """BIDS 01 2.6: lotes resume keyword_archivo_manual por lote (conteos por
    estado, min(intentado_at) como fecha ISO, mas reciente primero, tope 5)."""
    with _db_temporal("orbit_dash_inertes_lotes") as (conn, dsn):
        conn.execute(SQL13)
        conn.execute(SQL17)
        conn.execute(SQL14)
        _siembra_inertes(conn)
        keyword_id = conn.execute("SELECT id FROM ad_entity WHERE external_id = '8103'").fetchone()[
            0
        ]
        _siembra_lotes_archivo(conn, keyword_id)
        resp = _cliente(dsn, monkeypatch).get("/api/dashboard/inertes")
        assert resp.status_code == 200, resp.text
        lotes = resp.json()["lotes"]
        assert [lote["lote"] for lote in lotes] == ["inertes-2026-09-04", "inertes-2026-09-01"]
        nuevo = lotes[0]
        assert nuevo["fecha"].startswith("2026-09-04")
        assert nuevo["planeado"] == 1
        assert nuevo["applied"] == 0
        assert nuevo["failed"] == 0
        assert nuevo["repuesto"] == 0
        viejo = lotes[1]
        assert viejo["fecha"].startswith("2026-09-01")
        assert viejo["planeado"] == 0
        assert viejo["applied"] == 1
        assert viejo["failed"] == 1
        assert viejo["repuesto"] == 0


# ---------------------------------------------------------------------------
# ORBIT 06 2.3 - bloque target en /salud (rojo e, tests puros)
# ---------------------------------------------------------------------------


def _notas_con_target(**campos) -> dict:
    base = {
        "procedencia": "margen_plataforma",
        "motivo_abstencion": None,
        "margen_neto_pct": "40",
        "fraccion": "0.5",
        "cobertura": "1",
        "ventana_desde": "2026-05-22",
        "ventana_hasta": "2026-08-20",
        "target_derivado": "20",
        "target_aplicado": "20",
        "ledger_fresco_at": "2026-09-03T12:00:00+00:00",
        "moneda": "MXN",
    }
    base.update(campos)
    return {"target": base}


def test_salud_bloque_target_desde_notes():
    """Rojo (e): el bloque target de /salud sale de notes.target del ultimo
    ciclo: vigente + procedencia + margen + fraccion + ventana + edad."""
    from app import api_common

    bloque = api_common.bloque_target_margen(
        {"notes": _notas_con_target()}, hoy=dt.date(2026, 9, 4)
    )
    assert bloque["target_vigente"] == "20"
    assert bloque["procedencia"] == "margen_plataforma"
    assert bloque["margen_neto_pct"] == "40"
    assert bloque["fraccion"] == "0.5"
    assert bloque["ventana_desde"] == "2026-05-22"
    assert bloque["ventana_hasta"] == "2026-08-20"
    assert bloque["ledger_edad_dias"] == 1
    assert bloque["motivo_abstencion"] is None


def test_salud_bloque_target_abstencion_con_etiqueta():
    """Rojo (e): abstencion -> vigente null + motivo con etiqueta ES."""
    from app import api_common

    bloque = api_common.bloque_target_margen(
        {
            "notes": _notas_con_target(
                procedencia=None,
                motivo_abstencion="cobertura_baja",
                target_derivado=None,
                target_aplicado=None,
            )
        },
        hoy=dt.date(2026, 9, 4),
    )
    assert bloque["target_vigente"] is None
    assert bloque["motivo_abstencion"] == "cobertura_baja"
    assert isinstance(bloque["motivo_etiqueta"], str) and "cobertura" in bloque["motivo_etiqueta"]


def test_salud_bloque_target_cobertura_y_ratio():
    """Rojo (h, §9): cobertura por monto pasa tal cual; ratio_ads_venta =
    ad_revenue_ventana / venta_total (None si falta un lado o venta 0)."""
    from app import api_common

    base = _notas_con_target()
    base["target"].update({"cobertura": "0.98", "venta_total": "7000", "ad_revenue_ventana": "728"})
    bloque = api_common.bloque_target_margen({"notes": base}, hoy=dt.date(2026, 9, 4))
    assert bloque["cobertura"] == "0.98"
    assert bloque["ratio_ads_venta"] == Decimal("728") / Decimal("7000")
    sin = api_common.bloque_target_margen({"notes": _notas_con_target()}, hoy=dt.date(2026, 9, 4))
    assert sin["cobertura"] == "1"
    assert sin["ratio_ads_venta"] is None


def test_salud_bloque_target_sin_clave_da_nulls():
    """Rojo (e) regla 3: ciclos viejos sin notes.target -> todo null, sin
    reventar (ni ciclo, ni notes, ni notes no-dict)."""
    from app import api_common

    for ultimo in (None, {}, {"notes": None}, {"notes": {"texto": "rastro: x"}}, {"notes": {}}):
        bloque = api_common.bloque_target_margen(ultimo, hoy=dt.date(2026, 9, 4))
        assert bloque["target_vigente"] is None
        assert bloque["procedencia"] is None
        assert bloque["motivo_abstencion"] is None


# ---------------------------------------------------------------------------
# CORTES UI 01 1.1: el endpoint declara etiqueta, direccion, efecto e
# indicador (la UI consume, no reimplementa: regla 22).
# ---------------------------------------------------------------------------


def _encola_corte(conn, plataforma, entidad, kind, decision_id, term=None):
    return conn.execute(
        "INSERT INTO apply_queue (platform, ad_entity_id, kind, search_term,"
        " decision_id, modo, estado, vence_el, request_payload)"
        " VALUES (%s, %s, %s, %s, %s, 'live', 'pending_veto',"
        " now() + interval '48 hours', '{}'::jsonb) RETURNING id",
        (plataforma, entidad, kind, term, decision_id),
    ).fetchone()[0]


def _siembra_cortes_ui01(conn):
    ciclo = _ciclo(conn, platform="amazon_us")
    config = _config_version(conn, {})
    camp = _campana(conn, "amazon_us", "9001", name="Campana A")
    ag = _grupo(conn, "amazon_us", "9101", camp)
    kw = _keyword(conn, "amazon_us", "9201", ag, "zapato")
    madura = dt.date(2026, 8, 1)  # trigger decision_madurez_corte: window_end <= decided_at - 10d
    dec_pause = _decision(
        conn,
        ciclo,
        kw,
        kind="pause",
        config_id=config,
        inputs={"motivo": "x"},
        window_end=madura,
    )
    dec_neg = _decision(
        conn,
        ciclo,
        kw,
        kind="negative",
        config_id=config,
        inputs={},
        search_term="tenis blancos",
        window_end=madura,
    )
    termino = {
        "search_term": "arras para boda cristiana",
        "cost": "28.2200",
        "ad_revenue": "203.2000",
        "clicks": 63,
        "orders": 2,
        "fechas_distintas": 9,
        "moneda": "USD",
    }
    dec_harv = _decision(
        conn,
        ciclo,
        ag,
        kind="harvest",
        config_id=config,
        inputs={"termino": termino},
        search_term="arras para boda cristiana",
        moneda="USD",
        window_end=madura,
    )
    dec_harv_sin = _decision(
        conn,
        ciclo,
        ag,
        kind="harvest",
        config_id=config,
        inputs={},
        moneda="USD",
        search_term="otro termino",
        window_end=madura,
    )
    _encola_corte(conn, "amazon_us", kw, "pause", dec_pause)
    _encola_corte(conn, "amazon_us", kw, "negative", dec_neg, "tenis blancos")
    _encola_corte(conn, "amazon_us", ag, "harvest", dec_harv, "arras para boda cristiana")
    _encola_corte(conn, "amazon_us", ag, "harvest", dec_harv_sin, "otro termino")


@pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres local")
def test_cortes_ui01_etiqueta_direccion_efecto_e_indicador(monkeypatch):
    """CORTES UI 01: cada item declara etiqueta exacta por kind (D2),
    direccion (D3), efecto del rechazo (D4) y, solo el harvest con
    termino, el indicador leido de decision.inputs->'termino'.
    Sin termino: indicador None (regla 3, jamas inventado)."""
    with _db_temporal("orbit_dash_cortesui") as (conn, dsn):
        conn.execute(SQL02)
        _siembra_cortes_ui01(conn)
        resp = _cliente(dsn, monkeypatch).get("/api/dashboard/cortes")
        assert resp.status_code == 200, resp.text
        por_kind = {}
        for item in resp.json()["items"]:
            por_kind.setdefault(item["kind"], item)
        pause = por_kind["pause"]
        assert pause["etiqueta"] == "Apagar palabra"
        assert pause["direccion"] == "recorta"
        assert pause["indicador"] is None
        assert pause["efecto_rechazo"] == "Rechazar: la palabra NO se apagara (seguira gastando)"
        neg = por_kind["negative"]
        assert neg["etiqueta"] == "Bloquear busqueda"
        assert neg["direccion"] == "recorta"
        assert neg["indicador"] is None
        assert neg["efecto_rechazo"] == "Rechazar: la busqueda NO se bloqueara"
        harv = [i for i in resp.json()["items"] if i["kind"] == "harvest"]
        assert len(harv) == 2
        con = [i for i in harv if i["search_term"] == "arras para boda cristiana"][0]
        assert con["etiqueta"] == "Capturar termino que vende"
        assert con["direccion"] == "crece"
        assert con["efecto_rechazo"] == "Rechazar: la palabra NO se creara"
        assert con["indicador"] == {
            "ordenes": 2,
            "ingreso": "203.2000",
            "clics": 63,
            "moneda": "USD",
        }
        sin = [i for i in harv if i["search_term"] == "otro termino"][0]
        assert sin["indicador"] is None, "sin termino no hay indicador"
        assert sin["etiqueta"] == "Capturar termino que vende"
        assert sin["direccion"] == "crece"
        assert sin["efecto_rechazo"] == "Rechazar: la palabra NO se creara"


def test_cortes_ui01_indicador_exige_termino_completo():
    """Regla 3 (grok): termino ausente o parcial -> indicador None, jamas
    un dict hueco que la plantilla pintaria como ' ordenes - de ingreso'."""
    from app import api_dashboard as dash

    base = {"search_term": "t", "cost": "1.0000", "clicks": 5, "moneda": "USD"}
    lleno = dict(base, orders=2, ad_revenue="203.2000")
    assert dash._indicador_harvest("harvest", {"termino": lleno}) == {
        "ordenes": 2,
        "ingreso": "203.2000",
        "clics": 5,
        "moneda": "USD",
    }
    assert dash._indicador_harvest("harvest", {}) is None
    assert dash._indicador_harvest("harvest", {"termino": {}}) is None
    assert dash._indicador_harvest("harvest", {"termino": {"orders": 2}}) is None
    assert dash._indicador_harvest("harvest", None) is None
    assert dash._indicador_harvest("pause", {"termino": lleno}) is None


def _propuesta_campana(conn, camp: int, *, estado: str = "open", external: str = "9001") -> int:
    """Fila de propuesta (C.4) con los NOT NULL minimos."""
    cerrado = AHORA if estado != "open" else None
    return conn.execute(
        "INSERT INTO ads_campaign_proposal (campaign_id, platform, campaign_external_id,"
        " risk_type, status, first_seen_at, last_seen_at, closed_at, close_reason,"
        " window_start, window_end,"
        " observed_at, cost, revenue, currency, target_pct, target_source, excess,"
        " campaign_status, status_synced_at, evidence)"
        " VALUES (%s, 'amazon_us', %s, 'exceso_economico', %s, %s, %s, %s,"
        " 'estado_pausado_observado', '2026-08-16', '2026-09-14', %s, 200, 0, 'USD',"
        " 20, 'goal_plataforma', 200, 'ENABLED', %s, %s) RETURNING id",
        (
            camp,
            external,
            estado,
            AHORA,
            AHORA,
            cerrado,
            AHORA,
            AHORA,
            Json({"motivo": "exceso_economico"}),
        ),
    ).fetchone()[0]


@pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres local")
def test_cortes_incluye_propuestas_campana(monkeypatch):
    """C.4 B2: /api/dashboard/cortes trae open + paused_observed con motivo,
    sales30d y aviso (la pantalla los pinta en su seccion)."""
    with _db_temporal("orbit_dash_cortesprop") as (conn, dsn):
        conn.execute(SQL02)
        conn.execute(SQL42)
        camp = _campana(conn, "amazon_us", "9001", name="A1U")
        _propuesta_campana(conn, camp)
        camp2 = _campana(conn, "amazon_us", "9002", name="AU2")
        _propuesta_campana(conn, camp2, estado="paused_observed", external="9002")
        resp = _cliente(dsn, monkeypatch).get("/api/dashboard/cortes")
        assert resp.status_code == 200, resp.text
        props = resp.json()["propuestas_campana"]
        assert [pr["status"] for pr in props] == ["open", "paused_observed"]
        assert props[0]["motivo"] == "exceso_economico"
        assert props[0]["sales30d"] == "0.0000"
        assert props[0]["aviso_estado"] == "pending"


@pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres local")
def test_salud_muestra_avisos_propuesta_pendientes_y_fallo(monkeypatch):
    """C.4 B1: /salud trae pendientes de aviso + fallo del ultimo ciclo."""
    with _db_temporal("orbit_dash_saludaviso") as (conn, dsn):
        conn.execute(SQL42)
        camp = _campana(conn, "amazon_us", "9001", name="A1U")
        _propuesta_campana(conn, camp)
        # M12r2: una paused_observed pending NO cuenta (solo open avisan).
        camp2 = _campana(conn, "amazon_us", "9002", name="AU2")
        _propuesta_campana(conn, camp2, estado="paused_observed", external="9002")
        notas = json_dumps({"telegram": {"aviso_propuesta": "fallo: canal caido"}})
        _ciclo(conn, platform="amazon_us", notes=notas)
        data = _cliente(dsn, monkeypatch).get("/api/dashboard/salud").json()["plataformas"]
        assert data["amazon_us"]["avisos_propuesta"] == {
            "pendientes": 1,
            "fallo": "fallo: canal caido",
        }
        assert data["amazon_mx"]["avisos_propuesta"] == {"pendientes": 0, "fallo": None}


def test_motivos_v2_en_ambos_dicts_es():
    """A4: las abstenciones v2 viven en DECISIONES (plan-literal) y en
    SALUD (clase no-op, forward-A6; precedente MOTIVO_PAUSE dual)."""
    from app.api_dashboard import MOTIVOS_ES_DECISIONES, MOTIVOS_ES_SALUD
    from app.optimizer import bid as b

    assert MOTIVOS_ES_DECISIONES[b.MOTIVO_EVIDENCIA_INSUFICIENTE] == (
        "Sin evidencia: la posterior no alcanza la confianza para ajustar"
    )
    assert MOTIVOS_ES_DECISIONES[b.MOTIVO_CPC_POST_CAMBIO_INSUFICIENTE] == (
        "CPC desconocido tras el cambio: menos de 20 clics al bid vigente"
    )
    assert MOTIVOS_ES_SALUD[b.MOTIVO_EVIDENCIA_INSUFICIENTE] == (
        "Sin evidencia: la posterior no alcanza la confianza para ajustar"
    )
    assert MOTIVOS_ES_SALUD[b.MOTIVO_CPC_POST_CAMBIO_INSUFICIENTE] == (
        "CPC desconocido tras el cambio: menos de 20 clics al bid vigente"
    )


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_decisiones_feed_evidencia_v2_union_y_mantiene(monkeypatch):
    """A4: el feed expone la linea de sombra (motivo_es por UNION:
    abstencion nueva, banda y SALUD-only) + mantiene (mismo kind+factor
    que el vivo); sin clave => null (filas pre-A4)."""
    with _db_temporal("orbit_dash_a4") as (conn, dsn):
        config_id = _config_version(conn, {"ads_optimizer_mode": "shadow"})
        camp = _campana(conn, "amazon_us", "9001", name="Campana A")
        ag = _grupo(conn, "amazon_us", "9101", parent=camp)
        ciclo = _ciclo(conn, platform="amazon_us")
        id_abstiene = _decision(
            conn,
            ciclo,
            camp,
            kind="bid",
            config_id=config_id,
            moneda="USD",
            inputs={
                "motor": "bid",
                "motivo": "banda_menos_12",
                "factor": "-0.12",
                "target_acos_pct_usado": "25.00",
                "evidencia_v2": {
                    "politica": "evidencia_v2",
                    "veredicto": {
                        "kind": None,
                        "motivo": "cpc_post_cambio_insuficiente",
                        "factor": None,
                        "new_value": None,
                    },
                },
            },
        )
        id_banda = _decision(
            conn,
            _ciclo(conn, platform="amazon_us"),
            camp,
            kind="bid",
            config_id=config_id,
            moneda="USD",
            inputs={
                "motor": "bid",
                "motivo": "banda_menos_12",
                "factor": "-0.12",
                "target_acos_pct_usado": "25.00",
                "evidencia_v2": {
                    "politica": "evidencia_v2",
                    "veredicto": {
                        "kind": "bid",
                        "motivo": "banda_menos_12",
                        "factor": "-0.12",
                        "new_value": "0.8800",
                    },
                },
            },
        )
        id_salud = _decision(
            conn,
            _ciclo(conn, platform="amazon_us"),
            ag,
            kind="bid",
            config_id=config_id,
            moneda="USD",
            inputs={
                "motor": "bid",
                "motivo": "banda_menos_12",
                "factor": "-0.12",
                "target_acos_pct_usado": "25.00",
                "evidencia_v2": {
                    "politica": "evidencia_v2",
                    "veredicto": {
                        "kind": None,
                        "motivo": "rango_bloquea_ajuste",
                        "factor": None,
                        "new_value": None,
                    },
                },
            },
        )
        id_vieja = _decision(
            conn,
            _ciclo(conn, platform="amazon_us"),
            ag,
            kind="bid",
            config_id=config_id,
            moneda="USD",
            inputs={"motor": "bid", "motivo": "banda_menos_12", "factor": "-0.12"},
        )
        cliente = _cliente(dsn, monkeypatch)
        data = cliente.get("/api/dashboard/decisiones").json()
        por_id = {i["id"]: i for i in data["items"]}

        abstiene = por_id[id_abstiene]["evidencia_v2"]
        assert abstiene["motivo"] == "cpc_post_cambio_insuficiente"
        assert abstiene["motivo_es"] == (
            "CPC desconocido tras el cambio: menos de 20 clics al bid vigente"
        )
        assert abstiene["mantiene"] is False

        banda = por_id[id_banda]["evidencia_v2"]
        assert banda["motivo_es"] == "ACoS sobre 1.15x del target: -12%"
        assert banda["mantiene"] is True
        assert banda["new_value"] == "0.8800"

        salud = por_id[id_salud]["evidencia_v2"]
        assert salud["motivo_es"] == "Rango [floor, ceiling] bloquea el ajuste"

        assert por_id[id_vieja]["evidencia_v2"] is None


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_decisiones_feed_fallback_muestra_abstencion_no_motivo_v1(monkeypatch):
    """A6-r1 F1 (DeepSeek): fila via=decide con fallback (veredicto v1 vivo
    + abstencion_v2): el feed muestra la ABSTENCION v2 real con marca de
    fallback, NO el motivo v1 rotulado como v2. Sin el fix, el operador
    lee 'v2: ACoS sobre...' y cree que v2 recorta cuando abstuvo."""
    with _db_temporal("orbit_dash_a6f1") as (conn, dsn):
        config_id = _config_version(conn, {"ads_optimizer_mode": "shadow"})
        camp = _campana(conn, "amazon_us", "9001", name="Campana A")
        _grupo(conn, "amazon_us", "9101", parent=camp)
        ciclo = _ciclo(conn, platform="amazon_us")
        id_fb = _decision(
            conn,
            ciclo,
            camp,
            kind="bid",
            config_id=config_id,
            moneda="USD",
            inputs={
                "motor": "bid",
                "motivo": "banda_menos_25",
                "factor": "-0.25",
                "target_acos_pct_usado": "25.00",
                "evidencia_v2": {
                    "politica": "evidencia_v2",
                    "via": "decide",
                    "abstencion_v2": "evidencia_insuficiente",
                    "veredicto": {
                        "kind": "bid",
                        "motivo": "banda_menos_25",
                        "factor": "-0.25",
                        "new_value": "0.7500",
                    },
                },
            },
        )
        data = _cliente(dsn, monkeypatch).get("/api/dashboard/decisiones").json()
        fb = {i["id"]: i for i in data["items"]}[id_fb]["evidencia_v2"]
        assert fb["motivo"] == "evidencia_insuficiente"
        assert fb["fallback_v1"] is True
        assert "banda_menos_25" not in (fb["motivo"] or "")


# ---------------------------------------------------------------------------
# JEV 2.1: asesoria guardada en /cortes (lectura pura; GET sin HTTP ni
# INSERT/UPDATE; veto visible; compatible jamas es error economico)
# ---------------------------------------------------------------------------

SQL_JEV = "\n".join(
    (Path(__file__).resolve().parent.parent / "migrations" / nombre).read_text(encoding="utf-8")
    for nombre in ("0049_jev_ads.sql", "0050_jev_revision_created_at.sql")
)


def _ficha_jev(
    conn, producto: int, listing: int, sha: str = "f" * 64, revisar_antes_de=None
) -> object:
    import uuid as _uuid

    ficha_id = _uuid.uuid4()
    conn.execute(
        "INSERT INTO jev_ficha_version (id, producto_id, plataforma, listings,"
        " hechos, desconocidos, sha256, aprobador, observado_at, revisar_antes_de)"
        " VALUES (%s, %s, 'amazon_us', ARRAY[%s], %s::jsonb, '{}', %s, 'aprobador',"
        " %s, %s)",
        (
            ficha_id,
            producto,
            listing,
            json.dumps([{"texto": "hecho", "fuente": "fuente"}]),
            sha,
            AHORA,
            revisar_antes_de if revisar_antes_de is not None else AHORA + _DIA * 120,
        ),
    )
    return ficha_id


def _censo_jev(conn, platform: str, ad_group: int, ficha_por_producto: dict):
    """Censo del grupo con las fichas ya congeladas (patron de evaluar)."""
    from dataclasses import replace as _replace

    from app.jev_ads import CensoCongelado
    from app.jev_catalogo import censo_grupo

    censo = censo_grupo(conn, plataforma=platform, ad_group_id=ad_group)
    miembros = []
    for miembro in censo.miembros:
        ficha = ficha_por_producto.get(miembro.producto_id)
        miembros.append(_replace(miembro, ficha_version_id=ficha))
    return CensoCongelado(miembros=tuple(miembros), exhaustivo=censo.exhaustivo)


@pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres local")
def test_cortes_asesoria_guardada_sin_escritura_ni_http(monkeypatch):
    import uuid as _uuid

    from app.jev_ads import DecisionARevisar
    from app.jev_asesor import AsesorAds

    with _db_temporal("orbit_dash_jev21") as (conn, dsn):
        conn.execute(SQL02)
        conn.execute(SQL_JEV)
        _siembra_cortes_ui01(conn)
        # catalogo Jev del grupo 9101 (el del harvest): producto + listing +
        # product_ad + estado ENABLED + ficha vigente.
        p1 = conn.execute(
            "INSERT INTO product (odoo_sku, name) VALUES ('S-1', 'S-1') RETURNING id"
        ).fetchone()[0]
        l1 = conn.execute(
            "INSERT INTO listing (product_id, platform, external_id)"
            " VALUES (%s, 'amazon_us', 'B0JEV00001') RETURNING id",
            (p1,),
        ).fetchone()[0]
        grupo = conn.execute("SELECT id FROM ad_entity WHERE external_id = '9101'").fetchone()[0]
        conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id, parent_id, listing_id)"
            " VALUES ('amazon_us', 'product_ad', 'jev-ad-1', %s, %s)",
            (grupo, l1),
        )
        conn.execute(
            "INSERT INTO ad_entity_state (ad_entity_id, status, synced_at)"
            " VALUES ((SELECT id FROM ad_entity WHERE external_id = 'jev-ad-1'),"
            " 'ENABLED', now())"
        )
        ficha_id = _ficha_jev(conn, p1, l1)
        censo = _censo_jev(conn, "amazon_us", grupo, {p1: ficha_id})
        dec_neg = conn.execute(
            "SELECT id FROM decision WHERE kind = 'negative' LIMIT 1"
        ).fetchone()[0]
        asesor = AsesorAds(
            conn,
            pedir=lambda termino, ficha: _juicio_falso(termino, ficha, "satisface"),
            api_key="k",
            presupuesto=5,
            ahora=lambda: AHORA,
        )
        solicitud = _uuid.uuid4()
        asesor.evaluar(
            DecisionARevisar(
                decision_id=dec_neg,
                termino="tenis blancos",
                censo=censo,
                plataforma="amazon_us",
                decided_at=AHORA,
            ),
            solicitud_id=solicitud,
        )
        conteos_antes = _conteos_jev(conn)

        # Cero HTTP externo: si la lectura usara el transporte real, revienta.
        monkeypatch.setattr(
            "app.jev_juicios.transporte_httpx",
            lambda *a, **kw: (_ for _ in ()).throw(
                AssertionError("el GET de cortes no llama a TypeSafe")
            ),
        )
        resp = _cliente(dsn, monkeypatch).get("/api/dashboard/cortes")
        assert resp.status_code == 200, resp.text
        por_decision = {item["decision_id"]: item for item in resp.json()["items"]}
        item = por_decision[dec_neg]
        # La asesoria guardada llega: categoria, cobertura, fecha, ficha.
        asesoria = item["asesoria"]
        assert asesoria is not None
        assert asesoria["sujeto"] == "decision"
        assert asesoria["vigencia"] == "vigente"
        resultado = asesoria["resultados"][0]
        assert resultado["termino"] == "tenis blancos"
        assert resultado["resultado"]["tipo"] == "hay_compatible"
        assert resultado["resultado"]["miembros_con_juicio"] == 1
        assert resultado["resultado"]["miembros_totales"] == 1
        assert asesoria["fichas"][0]["aprobador"] == "aprobador"
        assert asesoria["captured_at"] == AHORA.isoformat()
        # Un item sin revision ligada NO inventa asesoria.
        sin_asesoria = [i for i in resp.json()["items"] if i["decision_id"] != dec_neg]
        assert sin_asesoria, "la siembra trae mas items"
        assert all(i["asesoria"] is None for i in sin_asesoria)
        # El veto sigue visible en todos los items (regla de las 48 horas).
        assert all(i["vence_el"] and i["estado"] for i in resp.json()["items"])
        # Cero INSERT/UPDATE: los conteos de las tablas Jev no cambian.
        assert _conteos_jev(conn) == conteos_antes


def _juicio_falso(termino, ficha, relacion):
    import uuid as _uuid
    from decimal import Decimal

    from app.jev_ads import ClavePar, Juicio
    from app.jev_juicios import ResultadoPar

    return ResultadoPar(
        juicio=Juicio(
            intento_id=_uuid.uuid4(),
            clave=ClavePar("a" * 64, ficha.id, "b" * 64),
            relacion=relacion,
            probabilidades={
                "satisface": Decimal("0.70"),
                "no_satisface": Decimal("0.20"),
                "informacion_insuficiente": Decimal("0.10"),
            },
            confidence=Decimal("0.80"),
            observado_at=AHORA,
        ),
        usage=None,
        duracion_ms=5,
    )


def _conteos_jev(conn):
    return {
        tabla: conn.execute(f"SELECT count(*) FROM {tabla}").fetchone()[0]
        for tabla in (
            "jev_revision",
            "jev_par_evento",
            "jev_ficha_version",
            "jev_ficha_revocacion",
        )
    }


@pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres local")
def test_asesoria_vigencia_vigente_obsoleta_y_no_comprobable(monkeypatch):
    import uuid as _uuid

    from app.jev_ads import (
        DecisionARevisar,
        NoComprobable,
        Obsoleta,
        Vigente,
    )
    from app.jev_asesor import AsesorAds

    with _db_temporal("orbit_dash_jevvig") as (conn, dsn):
        conn.execute(SQL02)
        conn.execute(SQL_JEV)
        _siembra_cortes_ui01(conn)
        p1 = conn.execute(
            "INSERT INTO product (odoo_sku, name) VALUES ('S-1', 'S-1') RETURNING id"
        ).fetchone()[0]
        l1 = conn.execute(
            "INSERT INTO listing (product_id, platform, external_id)"
            " VALUES (%s, 'amazon_us', 'B0JEV00001') RETURNING id",
            (p1,),
        ).fetchone()[0]
        grupo = conn.execute("SELECT id FROM ad_entity WHERE external_id = '9101'").fetchone()[0]
        conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id, parent_id, listing_id)"
            " VALUES ('amazon_us', 'product_ad', 'jev-ad-1', %s, %s)",
            (grupo, l1),
        )
        conn.execute(
            "INSERT INTO ad_entity_state (ad_entity_id, status, synced_at)"
            " VALUES ((SELECT id FROM ad_entity WHERE external_id = 'jev-ad-1'),"
            " 'ENABLED', now())"
        )
        ficha_id = _ficha_jev(conn, p1, l1)
        censo = _censo_jev(conn, "amazon_us", grupo, {p1: ficha_id})
        dec_neg = conn.execute(
            "SELECT id FROM decision WHERE kind = 'negative' LIMIT 1"
        ).fetchone()[0]
        asesor = AsesorAds(
            conn,
            pedir=lambda termino, ficha: _juicio_falso(termino, ficha, "satisface"),
            api_key="k",
            presupuesto=5,
            ahora=lambda: AHORA,
        )
        asesor.evaluar(
            DecisionARevisar(
                decision_id=dec_neg,
                termino="tenis blancos",
                censo=censo,
                plataforma="amazon_us",
                decided_at=AHORA,
            ),
            solicitud_id=_uuid.uuid4(),
        )
        # Vigente: la ficha congelada sigue aprobada, no vencida y es la ultima.
        vistas = asesor.leer([dec_neg], ahora=AHORA + _DIA)
        assert vistas[dec_neg].vigencia == Vigente()
        # Obsoleta por revocacion (revisiones anteriores consultables).
        conn.execute(
            "INSERT INTO jev_ficha_revocacion (ficha_version_id, autor, motivo)"
            " VALUES (%s, 'autor', 'motivo')",
            (ficha_id,),
        )
        vistas = asesor.leer([dec_neg], ahora=AHORA + _DIA)
        assert vistas[dec_neg].vigencia == Obsoleta()
        # No comprobable: una revision SIN fichas congeladas no puede
        # comprobar vigencia (censo sin fichas).
        censo_vacio = _censo_jev(conn, "amazon_us", grupo, {})
        asesor.evaluar(
            DecisionARevisar(
                decision_id=dec_neg,
                termino="otro termino",
                censo=censo_vacio,
                plataforma="amazon_us",
                decided_at=AHORA,
            ),
            solicitud_id=_uuid.uuid4(),
        )
        vistas = asesor.leer([dec_neg], ahora=AHORA + _DIA)
        assert vistas[dec_neg].vigencia == NoComprobable()


@pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres local")
def test_asesoria_vigencia_usa_el_predicado_de_ficha_vigente(monkeypatch):
    """Regresion B4-r3 B1: el chip de vigencia deriva del MISMO predicado que
    ficha_vigente (cubre el listing, no vencida, seleccionada por
    observado_at/created_at), no de un EXISTS por producto+plataforma. Una
    ficha posterior de OTRO listing del mismo producto, o una posterior
    VENCIDA, no vuelven obsoleta una revision cuya ficha sigue siendo la
    seleccionada para su listing; una ficha que SI la desplaza, si."""
    import uuid as _uuid

    from app.jev_ads import DecisionARevisar, Obsoleta, Vigente
    from app.jev_asesor import AsesorAds

    with _db_temporal("orbit_dash_jevb4r3a") as (conn, dsn):
        conn.execute(SQL02)
        conn.execute(SQL_JEV)
        _siembra_cortes_ui01(conn)
        p1 = conn.execute(
            "INSERT INTO product (odoo_sku, name) VALUES ('S-1', 'S-1') RETURNING id"
        ).fetchone()[0]
        l1 = conn.execute(
            "INSERT INTO listing (product_id, platform, external_id)"
            " VALUES (%s, 'amazon_us', 'B0JEV00001') RETURNING id",
            (p1,),
        ).fetchone()[0]
        l2 = conn.execute(
            "INSERT INTO listing (product_id, platform, external_id)"
            " VALUES (%s, 'amazon_us', 'B0JEV00002') RETURNING id",
            (p1,),
        ).fetchone()[0]
        grupo = conn.execute("SELECT id FROM ad_entity WHERE external_id = '9101'").fetchone()[0]
        conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id, parent_id, listing_id)"
            " VALUES ('amazon_us', 'product_ad', 'jev-ad-1', %s, %s)",
            (grupo, l1),
        )
        conn.execute(
            "INSERT INTO ad_entity_state (ad_entity_id, status, synced_at)"
            " VALUES ((SELECT id FROM ad_entity WHERE external_id = 'jev-ad-1'),"
            " 'ENABLED', now())"
        )
        ficha_r1 = _ficha_jev(conn, p1, l1)
        censo = _censo_jev(conn, "amazon_us", grupo, {p1: ficha_r1})
        dec_neg = conn.execute(
            "SELECT id FROM decision WHERE kind = 'negative' LIMIT 1"
        ).fetchone()[0]
        asesor = AsesorAds(
            conn,
            pedir=lambda termino, ficha: _juicio_falso(termino, ficha, "satisface"),
            api_key="k",
            presupuesto=5,
            ahora=lambda: AHORA,
        )
        asesor.evaluar(
            DecisionARevisar(
                decision_id=dec_neg,
                termino="tenis blancos",
                censo=censo,
                plataforma="amazon_us",
                decided_at=AHORA,
            ),
            solicitud_id=_uuid.uuid4(),
        )

        def vigencia():
            return asesor.leer([dec_neg], ahora=AHORA + _DIA)[dec_neg].vigencia

        # Control: la ficha de la revision sigue siendo la seleccionada.
        assert vigencia() == Vigente()
        # Sentido 1: ficha posterior del MISMO producto por OTRO listing no
        # vuelve obsoleta la revision (la de R1 sigue cubriendo su listing).
        _ficha_jev(conn, p1, l2, sha="d" * 64)
        assert vigencia() == Vigente()
        # Sentido 2: una ficha posterior que cubre el listing pero VENCIDA
        # tampoco la desplaza (no es seleccionable para ese listing).
        _ficha_jev(conn, p1, l1, sha="c" * 64, revisar_antes_de=AHORA)
        assert vigencia() == Vigente()
        # Inverso (control): una ficha posterior viva que SI cubre el listing
        # desplaza a la de R1: la revision queda obsoleta.
        _ficha_jev(conn, p1, l1, sha="b" * 64)
        assert vigencia() == Obsoleta()


@pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres local")
def test_asesoria_leer_salta_solo_no_activos_como_evaluar(monkeypatch):
    """Regresion B4-r3 B2: la composicion historica de leer() salta los
    miembros solo no activos (ARCHIVED) igual que evaluar: un ARCHIVED sin
    ficha no aporta FichaFaltante de mas y la vista reproduce EXACTAMENTE
    los motivos que evaluar compuso (fidelidad leer==evaluar)."""
    import uuid as _uuid

    from app.jev_ads import DecisionARevisar, Indeterminado
    from app.jev_asesor import AsesorAds

    with _db_temporal("orbit_dash_jevb4r3b") as (conn, dsn):
        conn.execute(SQL02)
        conn.execute(SQL_JEV)
        _siembra_cortes_ui01(conn)
        p1 = conn.execute(
            "INSERT INTO product (odoo_sku, name) VALUES ('S-1', 'S-1') RETURNING id"
        ).fetchone()[0]
        l1 = conn.execute(
            "INSERT INTO listing (product_id, platform, external_id)"
            " VALUES (%s, 'amazon_us', 'B0JEV00001') RETURNING id",
            (p1,),
        ).fetchone()[0]
        p2 = conn.execute(
            "INSERT INTO product (odoo_sku, name) VALUES ('S-2', 'S-2') RETURNING id"
        ).fetchone()[0]
        l2 = conn.execute(
            "INSERT INTO listing (product_id, platform, external_id)"
            " VALUES (%s, 'amazon_us', 'B0JEV00003') RETURNING id",
            (p2,),
        ).fetchone()[0]
        grupo = conn.execute("SELECT id FROM ad_entity WHERE external_id = '9101'").fetchone()[0]
        conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id, parent_id, listing_id)"
            " VALUES ('amazon_us', 'product_ad', 'jev-ad-1', %s, %s)",
            (grupo, l1),
        )
        conn.execute(
            "INSERT INTO ad_entity_state (ad_entity_id, status, synced_at)"
            " VALUES ((SELECT id FROM ad_entity WHERE external_id = 'jev-ad-1'),"
            " 'ENABLED', now())"
        )
        conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id, parent_id, listing_id)"
            " VALUES ('amazon_us', 'product_ad', 'jev-ad-2', %s, %s)",
            (grupo, l2),
        )
        conn.execute(
            "INSERT INTO ad_entity_state (ad_entity_id, status, synced_at)"
            " VALUES ((SELECT id FROM ad_entity WHERE external_id = 'jev-ad-2'),"
            " 'ARCHIVED', now())"
        )
        ficha_r1 = _ficha_jev(conn, p1, l1)
        censo = _censo_jev(conn, "amazon_us", grupo, {p1: ficha_r1})
        dec_neg = conn.execute(
            "SELECT id FROM decision WHERE kind = 'negative' LIMIT 1"
        ).fetchone()[0]
        asesor = AsesorAds(
            conn,
            pedir=lambda termino, ficha: _juicio_falso(termino, ficha, "no_satisface"),
            api_key="k",
            presupuesto=5,
            ahora=lambda: AHORA,
        )
        revision = asesor.evaluar(
            DecisionARevisar(
                decision_id=dec_neg,
                termino="tenis blancos",
                censo=censo,
                plataforma="amazon_us",
                decided_at=AHORA,
            ),
            solicitud_id=_uuid.uuid4(),
        )
        resultado_evaluar = revision.resultados[0][1]
        vista = asesor.leer([dec_neg], ahora=AHORA + _DIA)[dec_neg]
        resultado_leer = vista.resultados[0][1]
        assert isinstance(resultado_evaluar, Indeterminado), resultado_evaluar
        assert isinstance(resultado_leer, Indeterminado), resultado_leer
        assert resultado_leer.motivos == resultado_evaluar.motivos, (
            f"leer={resultado_leer.motivos} evaluar={resultado_evaluar.motivos}"
        )
        assert "ficha_ausente" not in resultado_leer.motivos


@pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres local")
def test_asesoria_lee_solo_eventos_de_su_revision(monkeypatch):
    import uuid as _uuid

    from app.jev_ads import DecisionARevisar, HayCompatible, Indeterminado
    from app.jev_asesor import AsesorAds

    with _db_temporal("orbit_dash_jeviso") as (conn, dsn):
        conn.execute(SQL02)
        conn.execute(SQL_JEV)
        _siembra_cortes_ui01(conn)
        p1 = conn.execute(
            "INSERT INTO product (odoo_sku, name) VALUES ('S-1', 'S-1') RETURNING id"
        ).fetchone()[0]
        l1 = conn.execute(
            "INSERT INTO listing (product_id, platform, external_id)"
            " VALUES (%s, 'amazon_us', 'B0JEV00001') RETURNING id",
            (p1,),
        ).fetchone()[0]
        grupo = conn.execute("SELECT id FROM ad_entity WHERE external_id = '9101'").fetchone()[0]
        conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id, parent_id, listing_id)"
            " VALUES ('amazon_us', 'product_ad', 'jev-ad-1', %s, %s)",
            (grupo, l1),
        )
        conn.execute(
            "INSERT INTO ad_entity_state (ad_entity_id, status, synced_at)"
            " VALUES ((SELECT id FROM ad_entity WHERE external_id = 'jev-ad-1'),"
            " 'ENABLED', now())"
        )
        ficha_id = _ficha_jev(conn, p1, l1)
        censo = _censo_jev(conn, "amazon_us", grupo, {p1: ficha_id})
        kw_2 = _keyword(conn, "amazon_us", "9202", grupo, "otra palabra")
        kw_3 = _keyword(conn, "amazon_us", "9203", grupo, "otra mas")
        dec_1 = conn.execute(
            "INSERT INTO decision (cycle_id, ad_entity_id, kind, decided_at,"
            " config_version_id, data_observed_at, window_start, window_end, inputs,"
            " search_term)"
            " SELECT d.cycle_id, %s, 'negative', now(), d.config_version_id, now(),"
            " '2026-07-02', '2026-08-01', '{}', 'tenis blancos'"
            " FROM decision d WHERE d.kind = 'negative' LIMIT 1 RETURNING id",
            (kw_2,),
        ).fetchone()[0]
        dec_2 = conn.execute(
            "INSERT INTO decision (cycle_id, ad_entity_id, kind, decided_at,"
            " config_version_id, data_observed_at, window_start, window_end, inputs,"
            " search_term)"
            " SELECT d.cycle_id, %s, 'negative', now(), d.config_version_id, now(),"
            " '2026-07-02', '2026-08-01', '{}', 'tenis blancos'"
            " FROM decision d WHERE d.kind = 'negative' LIMIT 1 RETURNING id",
            (kw_3,),
        ).fetchone()[0]
        # R1 (dec_1): el proveedor FALLA (sin clave: estado visible).
        asesor_fallo = AsesorAds(conn, api_key="", presupuesto=5, ahora=lambda: AHORA)
        asesor_fallo.evaluar(
            DecisionARevisar(dec_1, "tenis blancos", censo, "amazon_us", AHORA),
            solicitud_id=_uuid.uuid4(),
        )
        # R2 (dec_2): exito satisface.
        asesor_exito = AsesorAds(
            conn,
            pedir=lambda termino, ficha: _juicio_falso(termino, ficha, "satisface"),
            api_key="k",
            presupuesto=5,
            ahora=lambda: AHORA,
        )
        asesor_exito.evaluar(
            DecisionARevisar(dec_2, "tenis blancos", censo, "amazon_us", AHORA),
            solicitud_id=_uuid.uuid4(),
        )
        vistas = asesor_exito.leer([dec_2], ahora=AHORA + _DIA)
        vista = vistas[dec_2]
        resultado = vista.resultados[0][1]
        assert isinstance(resultado, HayCompatible), (
            "el fallo de la revision R1 no puede teñir la vista de R2"
        )
        # y R1 conserva su propio fallo (vista propia, sin exito prestado).
        vista_1 = asesor_exito.leer([dec_1], ahora=AHORA + _DIA)[dec_1]
        assert isinstance(vista_1.resultados[0][1], Indeterminado)
        assert "fallo_proveedor" in vista_1.resultados[0][1].motivos


def _fallo_jev(codigo: str = "timeout", detalle: str = "se agoto la espera"):
    from app.jev_juicios import FalloPar

    return FalloPar(codigo=codigo, detalle=detalle, duracion_ms=12)


@pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres local")
def test_asesoria_muestra_el_exito_de_la_reanudacion(monkeypatch):
    """Regresion VEREDICTO-B4-r1 B1: una reanudacion de la MISMA revision
    reintenta el par fallido (1.4: intencion y resultado con ordinal nuevo).
    leer() y /cortes deben reproducir lo que evaluar compuso (el exito del
    reintento), no quedarse con el fallo del primer intento."""
    import uuid as _uuid

    from app.jev_ads import DecisionARevisar, HayCompatible
    from app.jev_asesor import AsesorAds

    with _db_temporal("orbit_dash_jevb4r2") as (conn, dsn):
        conn.execute(SQL02)
        conn.execute(SQL_JEV)
        _siembra_cortes_ui01(conn)
        p1 = conn.execute(
            "INSERT INTO product (odoo_sku, name) VALUES ('S-1', 'S-1') RETURNING id"
        ).fetchone()[0]
        l1 = conn.execute(
            "INSERT INTO listing (product_id, platform, external_id)"
            " VALUES (%s, 'amazon_us', 'B0JEV00001') RETURNING id",
            (p1,),
        ).fetchone()[0]
        grupo = conn.execute("SELECT id FROM ad_entity WHERE external_id = '9101'").fetchone()[0]
        conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id, parent_id, listing_id)"
            " VALUES ('amazon_us', 'product_ad', 'jev-ad-1', %s, %s)",
            (grupo, l1),
        )
        conn.execute(
            "INSERT INTO ad_entity_state (ad_entity_id, status, synced_at)"
            " VALUES ((SELECT id FROM ad_entity WHERE external_id = 'jev-ad-1'),"
            " 'ENABLED', now())"
        )
        ficha_id = _ficha_jev(conn, p1, l1)
        censo = _censo_jev(conn, "amazon_us", grupo, {p1: ficha_id})
        dec_neg = conn.execute(
            "SELECT id FROM decision WHERE kind = 'negative' LIMIT 1"
        ).fetchone()[0]
        solicitud = _uuid.uuid4()
        sujeto = DecisionARevisar(dec_neg, "tenis blancos", censo, "amazon_us", AHORA)
        # Intento 1: fallo del proveedor, visible sin clave (cero HTTP).
        AsesorAds(conn, api_key="", presupuesto=5, ahora=lambda: AHORA).evaluar(
            sujeto, solicitud_id=solicitud
        )
        # Reanudacion de la MISMA revision: el fallo no se reutiliza y el
        # reintento resuelve con exito (ordinal nuevo).
        AsesorAds(
            conn,
            pedir=lambda termino, ficha: _juicio_falso(termino, ficha, "satisface"),
            api_key="k",
            presupuesto=5,
            ahora=lambda: AHORA,
        ).evaluar(sujeto, solicitud_id=solicitud)
        # El par quedo en ESA revision: fallo (ordinal 1) y exito (ordinal 2).
        eventos = conn.execute(
            "SELECT respuesta IS NOT NULL FROM jev_par_evento"
            " WHERE revision_id = %s AND tipo = 'resultado' ORDER BY ordinal",
            (solicitud,),
        ).fetchall()
        assert [fila[0] for fila in eventos] == [False, True]
        # leer() reproduce lo que evaluar compuso: el exito del reintento.
        vista = AsesorAds(conn).leer([dec_neg], ahora=AHORA + _DIA)[dec_neg]
        assert isinstance(vista.resultados[0][1], HayCompatible), vista.resultados[0][1]
        # Y /cortes lo muestra igual, sin una sola llamada externa.
        monkeypatch.setattr(
            "app.jev_juicios.transporte_httpx",
            lambda *a, **kw: (_ for _ in ()).throw(
                AssertionError("el GET de cortes no llama a TypeSafe")
            ),
        )
        resp = _cliente(dsn, monkeypatch).get("/api/dashboard/cortes")
        assert resp.status_code == 200, resp.text
        item = {i["decision_id"]: i for i in resp.json()["items"]}[dec_neg]
        assert item["asesoria"]["resultados"][0]["resultado"]["tipo"] == "hay_compatible"


@pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres local")
def test_cortes_asesoria_origen_y_destino_por_separado(monkeypatch):
    """DoD 2.1 (origen/destino): para harvest, el termino se compone POR
    SEPARADO contra el censo del origen y contra el censo del destino
    congelado. La UI muestra cada ambito con su propio veredicto: mismo
    termino, resultados distintos, sin mezclar universos."""
    import uuid as _uuid

    from app.jev_ads import DecisionARevisar, HayCompatible, Indeterminado
    from app.jev_asesor import AsesorAds

    with _db_temporal("orbit_dash_jevod") as (conn, dsn):
        conn.execute(SQL02)
        conn.execute(SQL_JEV)
        _siembra_cortes_ui01(conn)
        # Origen: grupo 9101 con P1 (ya sembrado por los tests Jev).
        p1 = conn.execute(
            "INSERT INTO product (odoo_sku, name) VALUES ('S-1', 'S-1') RETURNING id"
        ).fetchone()[0]
        l1 = conn.execute(
            "INSERT INTO listing (product_id, platform, external_id)"
            " VALUES (%s, 'amazon_us', 'B0JEV00001') RETURNING id",
            (p1,),
        ).fetchone()[0]
        grupo_origen = conn.execute(
            "SELECT id FROM ad_entity WHERE external_id = '9101'"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id, parent_id, listing_id)"
            " VALUES ('amazon_us', 'product_ad', 'jev-ad-1', %s, %s)",
            (grupo_origen, l1),
        )
        conn.execute(
            "INSERT INTO ad_entity_state (ad_entity_id, status, synced_at)"
            " VALUES ((SELECT id FROM ad_entity WHERE external_id = 'jev-ad-1'),"
            " 'ENABLED', now())"
        )
        ficha_origen = _ficha_jev(conn, p1, l1)
        # Destino: grupo PROPIO (9301, campana 9002) con P2 y su ficha.
        camp_destino = _campana(conn, "amazon_us", "9002", name="Campana destino")
        grupo_destino = _grupo(conn, "amazon_us", "9301", camp_destino)
        p2 = conn.execute(
            "INSERT INTO product (odoo_sku, name) VALUES ('S-2', 'S-2') RETURNING id"
        ).fetchone()[0]
        l2 = conn.execute(
            "INSERT INTO listing (product_id, platform, external_id)"
            " VALUES (%s, 'amazon_us', 'B0JEV00002') RETURNING id",
            (p2,),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id, parent_id, listing_id)"
            " VALUES ('amazon_us', 'product_ad', 'jev-ad-2', %s, %s)",
            (grupo_destino, l2),
        )
        conn.execute(
            "INSERT INTO ad_entity_state (ad_entity_id, status, synced_at)"
            " VALUES ((SELECT id FROM ad_entity WHERE external_id = 'jev-ad-2'),"
            " 'ENABLED', now())"
        )
        ficha_destino = _ficha_jev(conn, p2, l2, sha="e" * 64)
        censo_origen = _censo_jev(conn, "amazon_us", grupo_origen, {p1: ficha_origen})
        censo_destino = _censo_jev(conn, "amazon_us", grupo_destino, {p2: ficha_destino})
        dec_harv = conn.execute(
            "SELECT id FROM decision WHERE kind = 'harvest' AND inputs ? 'termino' LIMIT 1"
        ).fetchone()[0]
        # El destino congelado de la decision ES el grupo 9301 (R3: la vista
        # compara el destino de la revision con el que la decision guarda).
        # decision es append-only y el corte sembrado apunta a ESTA decision:
        # solo en el fixture se apaga el trigger para fijarle su destino.
        conn.execute("ALTER TABLE decision DISABLE TRIGGER USER")
        conn.execute(
            "UPDATE decision SET inputs = jsonb_set(inputs, '{goal}',"
            " coalesce(inputs->'goal', '{}') || '{\"harvest\": {\"ad_group_id\": \"9301\"}}')"
            " WHERE id = %s",
            (dec_harv,),
        )
        conn.execute("ALTER TABLE decision ENABLE TRIGGER USER")

        def pedir(termino, ficha):
            relacion = "satisface" if ficha.id == ficha_destino else "no_satisface"
            return _juicio_falso(termino, ficha, relacion)

        asesor = AsesorAds(conn, pedir=pedir, api_key="k", presupuesto=5, ahora=lambda: AHORA)
        revision = asesor.evaluar(
            DecisionARevisar(
                decision_id=dec_harv,
                termino="arras para boda cristiana",
                censo=censo_origen,
                plataforma="amazon_us",
                decided_at=AHORA,
                destino_censo=censo_destino,
            ),
            solicitud_id=_uuid.uuid4(),
        )
        # En la revision ya vienen los dos ambitos, compuestos por separado.
        _, resultado_origen = revision.resultados[0]
        _, resultado_destino = revision.destinos[0]
        assert isinstance(resultado_origen, Indeterminado), resultado_origen
        assert isinstance(resultado_destino, HayCompatible), resultado_destino
        assert resultado_destino.producto_ids == (p2,)
        conteos_antes = _conteos_jev(conn)
        # El GET muestra cada ambito con su veredicto, sin HTTP ni escritura.
        monkeypatch.setattr(
            "app.jev_juicios.transporte_httpx",
            lambda *a, **kw: (_ for _ in ()).throw(
                AssertionError("el GET de cortes no llama a TypeSafe")
            ),
        )
        resp = _cliente(dsn, monkeypatch).get("/api/dashboard/cortes")
        assert resp.status_code == 200, resp.text
        item = {i["decision_id"]: i for i in resp.json()["items"]}[dec_harv]
        asesoria = item["asesoria"]
        assert asesoria is not None
        origen = asesoria["resultados"][0]
        destino = asesoria["destinos"][0]
        assert origen["resultado"]["tipo"] == "indeterminado"
        assert "universo_desconocido" in origen["resultado"]["motivos"]
        assert destino["resultado"]["tipo"] == "hay_compatible"
        assert destino["resultado"]["producto_ids"] == [p2]
        assert destino["resultado"]["miembros_totales"] == 1
        assert asesoria["vigencia"] == "vigente"
        # El veto sigue visible (regla de las 48 horas, asesoria no lo detiene).
        assert item["vence_el"] and item["estado"]
        # Cero INSERT/UPDATE en las tablas Jev durante el GET.
        assert _conteos_jev(conn) == conteos_antes


def test_cortes_plantilla_asesoria_sin_error_economico_y_veto_visible():
    from app import ui

    ctx = _ctx_cortes_local()
    html = ui.templates.env.get_template("cortes.html").render(**ctx)
    assert 'class="asesoria-jev"' in html
    assert "compatible" in html
    assert "cobertura" in html
    assert "revisado con catalogo del" in html
    # DoD 2.1: la asesoria distingue el ambito de cada veredicto. B5-r2
    # (B4): el rotulo va PEGADO a su propio resultado (con espacios
    # normalizados), no basta que ambas palabras aparezcan por separado:
    # intercambiar los rotulos de los dos bucles debe romper esta prueba.
    plano = " ".join(html.split())
    assert "origen · tenis blancos: compatible (7) · cobertura 1/1" in plano
    assert "destino · tenis blancos: sin compatibilidad en 3 producto(s)" in plano
    assert "sin compatibilidad" in html
    # Relevancia compatible jamas se presenta como error economico: la
    # palabra "error" no aparece en el HTML renderizado de esta pantalla.
    assert "error" not in html.lower()
    # El veto permanece visible.
    assert "se aplica solo el" in html


def test_cortes_asesoria_ilegible_se_avisa_y_la_pantalla_sigue(monkeypatch):
    """R12 (triage G4-3): si `leer` falla, la pantalla responde 200, los
    cortes siguen y el operador VE que la asesoria no esta disponible (antes
    quedaba igual que "sin revision")."""
    with _db_temporal("orbit_dash_jev_r12") as (conn, dsn):
        conn.execute(SQL02)
        conn.execute(SQL_JEV)
        _siembra_cortes_ui01(conn)
        cliente = _cliente(dsn, monkeypatch)
        normal = cliente.get("/api/dashboard/cortes")
        assert normal.status_code == 200
        assert normal.json()["asesoria_disponible"] is True

        def leer_roto(self, referencias, *, ahora):
            raise RuntimeError("relation jev_revision does not exist")

        monkeypatch.setattr("app.jev_asesor.AsesorAds.leer", leer_roto)
        roto = cliente.get("/api/dashboard/cortes")
        assert roto.status_code == 200
        assert roto.json()["asesoria_disponible"] is False
        assert [i["id"] for i in roto.json()["items"]] == [i["id"] for i in normal.json()["items"]]
        assert roto.json()["items"]
        html = cliente.get("/cortes")
        assert html.status_code == 200
        assert "Asesoria Jev no disponible" in html.text


def test_cortes_plantilla_rotula_grupo_sin_destino_y_avisa_si_no_hay_asesoria():
    """R12: sin destino (negativos) el veredicto es del GRUPO; "origen" y
    "destino" solo aparecen en harvest. El aviso de asesoria no disponible
    sale solo cuando la lectura fallo."""
    from app import ui

    plantilla = ui.templates.env.get_template("cortes.html")
    ctx = _ctx_cortes_local()
    ctx["items"][0]["asesoria"]["destinos"] = []
    plano = " ".join(plantilla.render(**ctx, asesoria_disponible=True).split())
    assert "grupo · tenis blancos: compatible (7) · cobertura 1/1" in plano
    assert "origen ·" not in plano
    assert "Asesoria Jev no disponible" not in plano
    plano = " ".join(plantilla.render(**ctx, asesoria_disponible=False).split())
    assert "Asesoria Jev no disponible" in plano
    assert "error" not in plano.lower()


def _ctx_cortes_local():
    """Contexto de cortes con UNA fila negative y asesoria compatible."""
    ctx = {
        "pantalla": "cortes",
        "items": [
            {
                "id": 11,
                "plataforma": "amazon_us",
                "familia": "term_cut",
                "kind": "negative",
                "ad_entity_id": 3,
                "external_id": "7101",
                "nombre": "Campana A",
                "search_term": "tenis blancos",
                "estado": "pending_veto",
                "vence_el": "2026-09-25T12:00:00+00:00",
                "encolado_at": "2026-08-26T12:00:00+00:00",
                "decision_id": 99,
                "etiqueta": "Bloquear busqueda",
                "direccion": "recorta",
                "efecto_rechazo": "Rechazar: la busqueda NO se bloqueara",
                "indicador": None,
                "asesoria": {
                    "sujeto": "decision",
                    "decision_id": 99,
                    "plan_sha256": None,
                    "captured_at": "2026-10-04T00:00:00+00:00",
                    "vigencia": "vigente",
                    "solicitud": "00000000-0000-0000-0000-000000000000",
                    "resultados": [
                        {
                            "termino": "tenis blancos",
                            "resultado": {
                                "tipo": "hay_compatible",
                                "producto_ids": [7],
                                "miembros_con_juicio": 1,
                                "miembros_totales": 1,
                            },
                        }
                    ],
                    "destinos": [
                        {
                            "termino": "tenis blancos",
                            "resultado": {
                                "tipo": "ninguno_compatible",
                                "miembros_totales": 3,
                            },
                        }
                    ],
                    "fichas": [
                        {
                            "id": "00000000-0000-0000-0000-000000000001",
                            "aprobador": "aprobador",
                            "sha256": "abcd1234ef56",
                            "observado_at": "2026-10-04T00:00:00+00:00",
                        }
                    ],
                },
            }
        ],
    }
    return ctx
