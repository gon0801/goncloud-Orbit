"""Tests del router de SOLO LECTURA `/api/ads-optimizer` (ORBIT 03, task 3.2).

INTEGRACION (patron `_db_temporal` de test_cycle, COPIADO; skipif fail-closed
`_postgres_obligatorio_ausente` de test_schema): DB temporal con la migracion
entera, sembrada por el test (config_version + goals + ciclos + decisiones +
metricas), y el router conectado como rol de lectura via `ORBIT_DSN_READ`
apuntando a esa DB (el DSN real del deploy usa el LOGIN `orbit_read` -> rol
`app_read`, SELECT en todo el esquema; aqui el usuario de test lo cubre).

Contrato sellado de la task:

1. `/status`: ultimo ciclo POR PLATAFORMA + watermarks (la MISMA fuente del
   motor: `v_metric_latest` y `ad_entity_state.synced_at` -- regla 2, un
   numero una fuente) + los skips estructurados del notes. El notes es de
   FORMATO MIXTO (residual declarado en el plan): JSON en ciclos normales,
   texto plano "rastro: ..." en ciclos muertos reclamados -- el endpoint
   tolera AMBOS sin reventar.
2. `/audit`: decisiones paginadas (limit/offset, ORDER BY estable id DESC),
   filtros por ciclo/entidad/kind, 404 para ids inexistentes.
3. `/goals`: lectura de goals (sin escritura: la escalera y los goals se
   escriben por el camino humano de 4.3; `/goals` write llega en PR2).
4. Superficie: el router no registra NINGUN metodo distinto de GET
   (introspeccion de rutas, no convencion).
5. Fail-closed sin config: sin `ORBIT_DSN_READ` -> 503 con mensaje claro,
   jamas un DSN en la respuesta.

Dinero como STRING (regla 4): old_value/new_value/target/floor/ceiling salen
`str()` tal cual (la escala del string es artefacto deterministico del
NUMERIC de origen, mismo criterio que el congelado de app/cycle).
"""

from __future__ import annotations

import datetime as dt
import os
import socket
from contextlib import contextmanager
from decimal import Decimal

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Json
from test_schema import SQL, _postgres_obligatorio_ausente, _test_dsn

from app.main import app

DECIDED_AT = dt.datetime(2026, 8, 22, 12, 0, tzinfo=dt.UTC)
SYNCED_AT = DECIDED_AT - dt.timedelta(hours=4)

JOB_KEY = "ads_optimizer:amazon_us"

_DIA = dt.timedelta(days=1)


def _obs(fecha: dt.date, hora: int = 1) -> dt.datetime:
    """observed_at de una observacion: medianoche + hora UTC (>= metric_date)."""
    return dt.datetime(fecha.year, fecha.month, fecha.day, hora, tzinfo=dt.UTC)


# ---------------------------------------------------------------------------
# Patron _db_temporal COPIADO de test_cycle (con factory de conecs)
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
        conn.execute(SQL)  # la migracion entera
        # DSN del rol de lectura apuntando a la MISMA DB temporal: el router
        # lee via app.db.connect(ORBIT_DSN_READ), como en el deploy.
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


# ---------------------------------------------------------------------------
# Seeds (helpers del estilo test_cycle, copiados)
# ---------------------------------------------------------------------------


def _run(conn) -> int:
    return conn.execute("INSERT INTO ingest_run (source) VALUES ('test') RETURNING id").fetchone()[
        0
    ]


def _entidad(conn, platform: str, kind: str, external: str, parent=None, **extra) -> int:
    return conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id, parent_id, match_type,"
        " keyword_text) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (platform, kind, external, parent, extra.get("match_type"), extra.get("keyword_text")),
    ).fetchone()[0]


def _estado(conn, ad_entity_id, *, synced_at, current_bid=None, bid_currency=None) -> None:
    conn.execute(
        "INSERT INTO ad_entity_state (ad_entity_id, current_bid, bid_currency, status,"
        " acos_target, synced_at) VALUES (%s, %s, %s, 'ENABLED', NULL, %s)",
        (ad_entity_id, current_bid, bid_currency, synced_at),
    )


def _metrica(conn, run_id, ad_entity_id, fecha, *, cost, ad_revenue, clicks, orders) -> None:
    conn.execute(
        "INSERT INTO ads_metric_observation (ad_entity_id, metric_date, observed_at,"
        " metric_currency, cost, ad_revenue, impressions, clicks, orders, ingest_run_id)"
        " VALUES (%s, %s, %s, 'USD', %s, %s, NULL, %s, %s, %s)",
        (
            ad_entity_id,
            fecha,
            _obs(fecha),
            Decimal(cost),
            Decimal(ad_revenue),
            clicks,
            orders,
            run_id,
        ),
    )


def _config_version(conn, settings: dict) -> int:
    return conn.execute(
        "INSERT INTO config_version (label, settings) VALUES (%s, %s) RETURNING id",
        ("test-api", Json(settings)),
    ).fetchone()[0]


def _goal_plataforma(conn) -> int:
    return conn.execute(
        "INSERT INTO ads_optimizer_goal (scope, platform, target_acos_pct, bid_floor,"
        " bid_ceiling, bid_currency, enabled, mode)"
        " VALUES ('platform', 'amazon_us', 25, 0.40, 2.50, 'USD', true, 'shadow')"
        " RETURNING id"
    ).fetchone()[0]


def _ciclo(conn, *, platform: str, status: str, notes: str, mode: str = "shadow") -> int:
    return conn.execute(
        "INSERT INTO optimizer_cycle (motor, mode, platform, status, finished_at,"
        " decisions_count, notes) VALUES ('ads_optimizer', %s, %s::platform, %s, %s, 0, %s)"
        " RETURNING id",
        (mode, platform, status, DECIDED_AT, notes),
    ).fetchone()[0]


def _decision(
    conn,
    cycle_id,
    ad_entity_id,
    *,
    kind: str,
    config_id: int,
    search_term: str | None = None,
    window_end: dt.date | None = None,
    old_value: str | None = None,
    new_value: str | None = None,
    moneda: str | None = None,
) -> int:
    return conn.execute(
        "INSERT INTO decision (cycle_id, ad_entity_id, kind, decided_at, config_version_id,"
        " data_observed_at, window_start, window_end, search_term, old_value, new_value,"
        " value_currency, inputs) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
        " RETURNING id",
        (
            cycle_id,
            ad_entity_id,
            kind,
            DECIDED_AT,
            config_id,
            DECIDED_AT - dt.timedelta(hours=1),
            dt.date(2026, 7, 14),
            window_end or dt.date(2026, 8, 16),
            search_term,
            old_value,
            new_value,
            moneda,
            Json({"motor": "bid" if kind != "negative" else "hygiene"}),
        ),
    ).fetchone()[0]


def _siembra_maestra(conn) -> dict:
    """Fixture del DoD: config + goal + 4 keywords con metricas + 2 ciclos
    (uno viejo con 1 decision, uno nuevo con 5) en amazon_us; amazon_mx queda
    con un ciclo MUERTO reclamado (notes en texto plano 'rastro: ...').

    UNA decision por entidad por ciclo (indice sellado
    decision_unica_entidad_ciclo): los 4 bid del ciclo nuevo van sobre 4
    keywords DISTINTAS, jamas 2 del mismo par (ciclo, entidad).
    """
    run_id = _run(conn)
    config_id = _config_version(conn, {"ads_optimizer_mode": "shadow"})
    _goal_plataforma(conn)

    camp = _entidad(conn, "amazon_us", "campaign", "9001")
    ag = _entidad(conn, "amazon_us", "ad_group", "9101", parent=camp)
    keywords: list[int] = []
    for i in range(1, 5):
        kw = _entidad(
            conn,
            "amazon_us",
            "keyword",
            f"920{i}",
            parent=ag,
            match_type="EXACT",
            keyword_text=f"kw {i}",
        )
        _estado(conn, kw, synced_at=SYNCED_AT, current_bid=Decimal("1.00"), bid_currency="USD")
        keywords.append(kw)
    # watermark: max(metric_date) en v_metric_latest = 2026-08-19
    for fecha in (dt.date(2026, 8, 18), dt.date(2026, 8, 19)):
        _metrica(
            conn, run_id, keywords[0], fecha, cost="1.00", ad_revenue="3.00", clicks=1, orders=1
        )

    # ciclo VIEJO (id menor) con 1 decision: el status debe tomar el NUEVO
    ciclo_viejo = _ciclo(
        conn,
        platform="amazon_us",
        status="done",
        notes='{"skips": {"entidad": {}}, "decisiones": {"bid": 1}}',
    )
    _decision(
        conn,
        ciclo_viejo,
        keywords[0],
        kind="bid",
        config_id=config_id,
        old_value="1.0000",
        new_value="0.7500",
        moneda="USD",
    )

    # ciclo NUEVO (id mayor) con 5 decisiones: 4 bid (keywords distintas) +
    # 1 negative maduro del ad group
    notas_nuevo = json_dumps(
        {
            "skips": {"entidad": {"sin_goal": 1}, "termino": {"asin_like": 1}},
            "decisiones": {"bid": 4, "negative": 1},
            "entidades": 1,
            "ad_groups": 0,
            "terminos": 5,
            "ciclos_muertos": [],
            "degradacion_live": None,
        }
    )
    ciclo_nuevo = _ciclo(conn, platform="amazon_us", status="done", notes=notas_nuevo)
    ids_dec: list[int] = []
    for i, kw in enumerate(keywords):
        ids_dec.append(
            _decision(
                conn,
                ciclo_nuevo,
                kw,
                kind="bid",
                config_id=config_id,
                old_value="1.0000",
                new_value=f"0.7{i}00",
                moneda="USD",
            )
        )
    ids_dec.append(
        _decision(
            conn,
            ciclo_nuevo,
            ag,
            kind="negative",
            config_id=config_id,
            search_term="tortugas ninja calzas",
            window_end=dt.date(2026, 8, 12),
        )
    )

    # amazon_mx: ciclo MUERTO reclamado -> notes TEXTO PLANO (formato mixto)
    ciclo_muerto = _ciclo(
        conn,
        platform="amazon_mx",
        status="failed",
        notes="rastro: ciclo muerto (lock expirado, reclamado por otro-owner)",
    )

    return {
        "config_id": config_id,
        "kw1": keywords[0],
        "kw2": keywords[1],
        "kw3": keywords[2],
        "kw4": keywords[3],
        "ag": ag,
        "ciclo_viejo": ciclo_viejo,
        "ciclo_nuevo": ciclo_nuevo,
        "ciclo_muerto": ciclo_muerto,
        "ids_dec_nuevo": ids_dec,
    }


def json_dumps(obj) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# 1. /status: ultimo ciclo por plataforma + watermarks + notes (formato mixto)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_status_ultimo_ciclo_watermark_y_skips_estructurados(monkeypatch):
    with _db_temporal("orbit_api_status") as (conn, dsn):
        ids = _siembra_maestra(conn)
        resp = _cliente(dsn, monkeypatch).get("/api/ads-optimizer/status")

        assert resp.status_code == 200
        data = resp.json()
        us = data["plataformas"]["amazon_us"]
        # el ultimo ciclo es el NUEVO (id mayor), no el viejo
        assert us["ultimo_ciclo"]["id"] == ids["ciclo_nuevo"]
        assert us["ultimo_ciclo"]["status"] == "done"
        # skips estructurados del notes JSON
        assert us["ultimo_ciclo"]["notes"]["skips"] == {
            "entidad": {"sin_goal": 1},
            "termino": {"asin_like": 1},
        }
        assert us["ultimo_ciclo"]["notes"]["decisiones"] == {"bid": 4, "negative": 1}
        # watermarks de la MISMA fuente del motor (v_metric_latest / synced_at)
        assert us["watermark"] == "2026-08-19"
        assert us["synced_at"] is not None
        # amazon_mx existe en la respuesta con su ciclo muerto
        mx = data["plataformas"]["amazon_mx"]
        assert mx["ultimo_ciclo"]["id"] == ids["ciclo_muerto"]
        assert mx["watermark"] is None


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_status_tolera_notes_de_ciclo_muerto_en_texto_plano(monkeypatch):
    """FORMATO MIXTO (residual declarado del plan): un ciclo muerto reclamado
    tiene notes 'rastro: ...' en TEXTO PLANO -- el endpoint NO puede reventar
    parseandolo como JSON; lo devuelve como texto bajo una clave explicita."""
    with _db_temporal("orbit_api_muerto") as (conn, dsn):
        _siembra_maestra(conn)
        resp = _cliente(dsn, monkeypatch).get("/api/ads-optimizer/status")

        assert resp.status_code == 200
        mx = resp.json()["plataformas"]["amazon_mx"]
        notes = mx["ultimo_ciclo"]["notes"]
        assert isinstance(notes, dict)
        assert "texto" in notes
        assert "rastro" in notes["texto"]


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_status_plataforma_sin_datos_devuelve_nulls(monkeypatch):
    """Una plataforma sin ciclos ni metricas aparece con nulls, no con 404."""
    with _db_temporal("orbit_api_vacio") as (conn, dsn):
        resp = _cliente(dsn, monkeypatch).get("/api/ads-optimizer/status")
        assert resp.status_code == 200
        for plataforma in ("amazon_us", "amazon_mx"):
            entrada = resp.json()["plataformas"][plataforma]
            assert entrada["ultimo_ciclo"] is None
            assert entrada["watermark"] is None
            assert entrada["synced_at"] is None


# ---------------------------------------------------------------------------
# 2. /audit: paginacion, filtros, 404 para ids inexistentes
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_audit_paginacion_con_order_by_estable(monkeypatch):
    with _db_temporal("orbit_api_audit_pag") as (conn, dsn):
        ids = _siembra_maestra(conn)
        resp = _cliente(dsn, monkeypatch).get(
            "/api/ads-optimizer/audit", params={"limit": 2, "offset": 2}
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 6  # 5 del ciclo nuevo + 1 del viejo
        assert data["limit"] == 2 and data["offset"] == 2
        items = data["items"]
        assert len(items) == 2
        # ORDER BY estable: id DESC; la pagina offset=2 son los ids 3 y 2
        ids_dec = sorted(ids["ids_dec_nuevo"])
        assert [item["id"] for item in items] == [ids_dec[2], ids_dec[1]]
        # dinero como STRING (regla 4)
        assert isinstance(items[0]["new_value"], str)
        assert isinstance(items[0]["old_value"], str)
        assert items[0]["value_currency"] == "USD"


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_audit_filtros_por_ciclo_entidad_y_kind(monkeypatch):
    with _db_temporal("orbit_api_audit_filtros") as (conn, dsn):
        ids = _siembra_maestra(conn)
        cliente = _cliente(dsn, monkeypatch)

        # por ciclo: solo las 5 del ciclo nuevo
        r = cliente.get("/api/ads-optimizer/audit", params={"cycle_id": ids["ciclo_nuevo"]})
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 5
        assert all(item["cycle_id"] == ids["ciclo_nuevo"] for item in data["items"])

        # por entidad: solo las de la keyword 1 (1 del viejo + 1 del nuevo)
        r = cliente.get("/api/ads-optimizer/audit", params={"ad_entity_id": ids["kw1"]})
        assert r.json()["total"] == 2

        # por kind: negative -> exactamente 1 (la del ad group con search_term)
        r = cliente.get("/api/ads-optimizer/audit", params={"kind": "negative"})
        data = r.json()
        assert data["total"] == 1
        assert data["items"][0]["search_term"] == "tortugas ninja calzas"
        assert data["items"][0]["ad_entity_id"] == ids["ag"]

        # combinados: ciclo nuevo + kind bid -> 4
        r = cliente.get(
            "/api/ads-optimizer/audit",
            params={"cycle_id": ids["ciclo_nuevo"], "kind": "bid"},
        )
        assert r.json()["total"] == 4


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_audit_404_para_ids_inexistentes(monkeypatch):
    with _db_temporal("orbit_api_audit_404") as (conn, dsn):
        _siembra_maestra(conn)
        cliente = _cliente(dsn, monkeypatch)

        r = cliente.get("/api/ads-optimizer/audit", params={"cycle_id": 999_999})
        assert r.status_code == 404
        assert "cycle_id" in r.json()["detail"]

        r = cliente.get("/api/ads-optimizer/audit", params={"ad_entity_id": 999_999})
        assert r.status_code == 404
        assert "ad_entity_id" in r.json()["detail"]


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_audit_kind_fuera_del_vocabulario_rechazado(monkeypatch):
    """kind es vocabulario CERRADO (enum decision_kind): un valor ajeno debe
    rechazarse (422), jamas 'caer' a un filtro vacio que mienta."""
    with _db_temporal("orbit_api_audit_kind") as (conn, dsn):
        _siembra_maestra(conn)
        resp = _cliente(dsn, monkeypatch).get("/api/ads-optimizer/audit", params={"kind": "x"})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 3. /goals: lectura
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_goals_lectura_con_filtros(monkeypatch):
    with _db_temporal("orbit_api_goals") as (conn, dsn):
        _siembra_maestra(conn)
        camp = _entidad(conn, "amazon_us", "campaign", "9002")
        conn.execute(
            "INSERT INTO ads_optimizer_goal (scope, ad_entity_id, target_acos_pct,"
            " bid_floor, bid_ceiling, bid_currency, enabled, mode)"
            " VALUES ('campaign', %s, 30, 0.20, 2.00, 'USD', false, 'off')",
            (camp,),
        )
        cliente = _cliente(dsn, monkeypatch)

        # todos
        r = cliente.get("/api/ads-optimizer/goals")
        assert r.status_code == 200
        goals = r.json()
        assert len(goals) == 2
        assert {g["scope"] for g in goals} == {"platform", "campaign"}

        # filtro por plataforma
        r = cliente.get("/api/ads-optimizer/goals", params={"platform": "amazon_us"})
        assert len(r.json()) == 2
        r = cliente.get("/api/ads-optimizer/goals", params={"platform": "amazon_mx"})
        assert r.json() == []

        # filtro por scope y enabled
        r = cliente.get("/api/ads-optimizer/goals", params={"scope": "campaign"})
        assert [g["scope"] for g in r.json()] == ["campaign"]
        r = cliente.get("/api/ads-optimizer/goals", params={"enabled": "false"})
        assert [g["enabled"] for g in r.json()] == [False]

        # dinero como STRING (regla 4)
        assert goals[0]["bid_floor"] == "0.4000"
        assert goals[0]["target_acos_pct"] == "25.00"


# ---------------------------------------------------------------------------
# 4. Superficie: el router NO registra ningun metodo distinto de GET
# ---------------------------------------------------------------------------


def test_router_ads_optimizer_solo_registra_get():
    """Test de SUPERFICIE (introspeccion de rutas, no convencion): en PR1 el
    router es de SOLO LECTURA (hallazgo Security del plan: un endpoint sin
    auth en host compartido no puede portar orbit_admin). /run y /goals write
    llegan en PR2 con auth propia.

    Se introspecciona el OpenAPI de la app (el contrato publicado): cada
    path /api/ads-optimizer/* debe existir y exponer SOLO el verbo `get`.
    """
    paths = app.openapi()["paths"]
    rutas = {
        path: metodos for path, metodos in paths.items() if path.startswith("/api/ads-optimizer")
    }
    assert rutas, "no se encontraron rutas /api/ads-optimizer en el OpenAPI"
    for path, metodos in rutas.items():
        assert set(metodos) == {"get"}, (
            f"{path} expone {sorted(metodos)}: el router de PR1 solo puede exponer GET"
        )


# ---------------------------------------------------------------------------
# 5. Fail-closed sin config: 503 con mensaje claro, jamas el DSN
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_sin_dsn_de_lectura_fail_closed_503(monkeypatch):
    monkeypatch.delenv("ORBIT_DSN_READ", raising=False)
    resp = TestClient(app).get("/api/ads-optimizer/status")
    assert resp.status_code == 503
    assert "ORBIT_DSN_READ" in resp.json()["detail"]
