"""Tablero de ruido (BIDS 02, P.5).

Por ciclo: decisiones contadas por motivo y nivel (cada `inputs["caso"]`
pasa por `CasoHoja.desde_json`; el SQL jamas nombra una llave del caso)
y abstenciones por motivo (de `notes.skips.entidad`). Por hoja: cuanto
se encogio su bid en 90 dias (`v_cambio_bid`). Comparacion simple de
antes y despues del encendido por mercado (`v_metric_latest`, solo
campanas ENABLED): no es causal.

Fakes aqui; Postgres real al final, como `tests/test_pantalla_danadas.py`.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import socket
from contextlib import contextmanager
from decimal import Decimal

import psycopg
import pytest
from psycopg import sql as pgsql
from psycopg.types.json import Json
from test_schema import _postgres_obligatorio_ausente, _test_dsn

from app.optimizer.caso import (
    _CLAVES_CASO,
    BidVigente,
    CasoHoja,
    Economia,
    EconomiaPlataforma,
    InsumosPausa,
    PrecioVentana,
    Trayectoria,
)

UTC = dt.UTC

_FALTA_PG = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)


class _CursorFalso:
    def __init__(self, filas):
        self._filas = filas

    def fetchall(self):
        return self._filas

    def fetchone(self):
        return self._filas[0] if self._filas else None


class _ConnPorSql:
    """Conexion de lectura falsa: responde por fragmento del SQL, no por orden."""

    def __init__(self, respuestas):
        self._respuestas = dict(respuestas)
        self.consultas = []

    def execute(self, sql, params=None):
        self.consultas.append((sql, params))
        for fragmento, filas in self._respuestas.items():
            if fragmento in sql:
                return _CursorFalso(list(filas))
        raise AssertionError(f"SQL no esperado: {sql!r}")


def _caso_minimo(hoja_id, plataforma="amazon_mx", moneda="MXN"):
    return CasoHoja(
        plataforma=plataforma,
        hoja_id=hoja_id,
        ad_group_id=None,
        bid=BidVigente(valor=Decimal("4"), moneda=moneda, piso=Decimal("1"), techo=Decimal("20")),
        economia=Economia(
            plataforma=EconomiaPlataforma(
                moneda=moneda,
                equilibrio_acos_pct=None,
                gasto_para_concluir=Decimal("350"),
                confianza_recorte=Decimal("0.80"),
                confianza_subida=Decimal("0.70"),
            ),
            target_acos_pct=Decimal("25"),
        ),
        propia=None,
        pedidos_inmaduros=0,
        grupo=None,
        cuenta=None,
        precio=PrecioVentana(gasto=None, clics=None),
        trayectoria=Trayectoria(cambios=(), efecto=None),
        pausa=InsumosPausa(
            cortes=None,
            umbral_clics=20,
            gasto_minimo=Decimal("350"),
            expected_clicks=None,
            politica_economica=None,
        ),
        ventana_desde=dt.date(2026, 7, 1),
        ventana_hasta=dt.date(2026, 9, 29),
        observado_al=None,
    ).como_json()


def _inputs(motivo, nivel, hoja_id=11, plataforma="amazon_mx"):
    return {
        "motor": "bid",
        "politica": "niveles_v3",
        "platform": plataforma,
        "modo": "live",
        "motivo": motivo,
        "factor": None,
        "nivel": nivel,
        "veredicto_kind": "Mover",
        "caso": _caso_minimo(hoja_id, plataforma),
    }


def _lee(respuestas, plataforma="amazon_mx", dias=30):
    from app.pantalla_ruido import lee_ruido

    conn = _ConnPorSql(respuestas)
    return lee_ruido(conn, plataforma=plataforma, dias=dias), conn


def _respuestas_vacias(*, encendido=None):
    return {
        "inputs ? 'caso'": [],
        "c.id = ANY": [],
        "v_cambio_bid": [],
        "config_version": [(encendido,)],
        "v_metric_latest": [],
    }


# --- decisiones y abstenciones por ciclo ----------------------------------------


def test_decisiones_por_motivo_y_nivel_y_abstenciones_por_motivo():
    decisiones = [
        (7, _inputs("gasto_sin_venta", "hoja", hoja_id=11)),
        (7, _inputs("gasto_sin_venta", "hoja", hoja_id=12)),
        (7, _inputs("grupo_sangra", "ad_group", hoja_id=13)),
        (7, _inputs("regreso_por_desplome", None, hoja_id=14)),
    ]
    notes = json.dumps(
        {"skips": {"entidad": {"espera_precio": 2, "sin_evidencia": 1}, "termino": {"x": 5}}}
    )
    ciclos = [(7, dt.datetime(2026, 10, 9, 8, 41, tzinfo=UTC), notes)]
    respuestas = _respuestas_vacias()
    respuestas["inputs ? 'caso'"] = decisiones
    respuestas["c.id = ANY"] = ciclos
    pantalla, _conn = _lee(respuestas)
    assert pantalla.plataforma == "amazon_mx"
    assert pantalla.ciclos == (
        {
            "cycle_id": 7,
            "started_at": "2026-10-09T08:41:00+00:00",
            "decisiones": [
                {"motivo": "gasto_sin_venta", "nivel": "hoja", "count": 2},
                {"motivo": "grupo_sangra", "nivel": "ad_group", "count": 1},
                {"motivo": "regreso_por_desplome", "nivel": None, "count": 1},
            ],
            "abstenciones": {"espera_precio": 2, "sin_evidencia": 1},
        },
    )
    assert pantalla.encogimiento == ()
    assert pantalla.antes_y_despues is None


def test_notes_ilegible_abstenciones_none_no_cero():
    decisiones = [
        (7, _inputs("gasto_sin_venta", "hoja", hoja_id=11)),
        (8, _inputs("gasto_sin_venta", "hoja", hoja_id=11)),
    ]
    ciclos = [
        (7, dt.datetime(2026, 10, 9, 8, 41, tzinfo=UTC), "rastro: ciclo muerto"),
        (8, dt.datetime(2026, 10, 10, 8, 41, tzinfo=UTC), None),
    ]
    respuestas = _respuestas_vacias()
    respuestas["inputs ? 'caso'"] = decisiones
    respuestas["c.id = ANY"] = ciclos
    pantalla, _conn = _lee(respuestas)
    assert [c["abstenciones"] for c in pantalla.ciclos] == [None, None]
    assert [c["decisiones"] for c in pantalla.ciclos] == [
        [{"motivo": "gasto_sin_venta", "nivel": "hoja", "count": 1}],
        [{"motivo": "gasto_sin_venta", "nivel": "hoja", "count": 1}],
    ]


def test_ciclo_solo_con_casos_ilegibles_no_sale():
    rotos = dict(_inputs("gasto_sin_venta", "hoja"))
    rotos["caso"] = {"rota": 1}
    respuestas = _respuestas_vacias()
    respuestas["inputs ? 'caso'"] = [(7, rotos)]
    respuestas["c.id = ANY"] = [(7, dt.datetime(2026, 10, 9, 8, 41, tzinfo=UTC), None)]
    pantalla, _conn = _lee(respuestas)
    assert pantalla.ciclos == ()


def test_caso_ilegible_no_cuenta_la_decision_y_no_revienta():
    rotos = dict(_inputs("gasto_sin_venta", "hoja"))
    rotos["caso"] = {"rota": 1}
    decisiones = [(7, rotos), (7, _inputs("grupo_sangra", "ad_group", hoja_id=13))]
    ciclos = [(7, dt.datetime(2026, 10, 9, 8, 41, tzinfo=UTC), None)]
    respuestas = _respuestas_vacias()
    respuestas["inputs ? 'caso'"] = decisiones
    respuestas["c.id = ANY"] = ciclos
    pantalla, _conn = _lee(respuestas)
    assert pantalla.ciclos[0]["decisiones"] == [
        {"motivo": "grupo_sangra", "nivel": "ad_group", "count": 1}
    ]


# --- encogimiento -----------------------------------------------------------------


def test_encogimiento_diez_a_cuatro_da_cero_cuatro_y_sin_cambios_no_sale():
    cambios = [
        (
            11,
            Decimal("4"),
            dt.datetime(2026, 7, 11, 12, tzinfo=UTC),
            Decimal("10"),
            Decimal("8"),
            101,
        ),
        (
            11,
            Decimal("4"),
            dt.datetime(2026, 10, 4, 12, tzinfo=UTC),
            Decimal("8"),
            Decimal("4"),
            102,
        ),
    ]
    respuestas = _respuestas_vacias()
    respuestas["v_cambio_bid"] = cambios
    pantalla, _conn = _lee(respuestas)
    assert pantalla.ciclos == ()
    assert pantalla.encogimiento == (
        {"hoja_id": 11, "bid_base": "10", "bid_hoy": "4", "razon": "0.4", "moneda": "MXN"},
    )


def test_sin_encendido_antes_y_despues_none_y_no_pide_metricas():
    pantalla, conn = _lee(_respuestas_vacias(encendido=None))
    assert pantalla.antes_y_despues is None
    assert all("v_metric_latest" not in sql for sql, _p in conn.consultas)


def test_antes_y_despues_suma_ventanas_y_acos():
    encendido = dt.datetime(2026, 9, 15, 8, 40, tzinfo=UTC)
    mercado = [
        (dt.date(2026, 9, 10), Decimal("100.00"), 2, Decimal("1000.00")),
        (dt.date(2026, 9, 14), Decimal("100.00"), 1, Decimal("1000.00")),
        (dt.date(2026, 9, 15), Decimal("50.00"), 1, Decimal("1000.00")),
        (dt.date(2026, 9, 20), Decimal("50.00"), 0, Decimal("1000.00")),
    ]
    respuestas = _respuestas_vacias(encendido=encendido)
    respuestas["v_metric_latest"] = mercado
    pantalla, _conn = _lee(respuestas)
    assert pantalla.antes_y_despues == {
        "encendido_el": "2026-09-15",
        "moneda": "MXN",
        "antes": {"gasto": "200.00", "pedidos": 3, "venta": "2000.00", "acos_pct": "10.00"},
        "despues": {"gasto": "100.00", "pedidos": 1, "venta": "2000.00", "acos_pct": "5.00"},
    }


def test_ventana_despues_sin_filas_da_bloque_none():
    encendido = dt.datetime(2026, 9, 15, 8, 40, tzinfo=UTC)
    mercado = [(dt.date(2026, 9, 10), Decimal("100.00"), 2, Decimal("1000.00"))]
    respuestas = _respuestas_vacias(encendido=encendido)
    respuestas["v_metric_latest"] = mercado
    pantalla, _conn = _lee(respuestas)
    assert pantalla.antes_y_despues["despues"] is None
    assert pantalla.antes_y_despues["antes"]["gasto"] == "100.00"


# --- contrato y candados ----------------------------------------------------------------


def test_como_dict_pasa_ciclos_encogimiento_y_comparacion():
    from app.pantalla_ruido import PantallaRuido

    pantalla = PantallaRuido(
        plataforma="amazon_us",
        ciclos=({"cycle_id": 7, "decisiones": [], "abstenciones": None},),
        encogimiento=(
            {"hoja_id": 11, "bid_base": "10", "bid_hoy": "4", "razon": "0.4", "moneda": "USD"},
        ),
        antes_y_despues={"encendido_el": "2026-09-15", "antes": None, "despues": None},
    )
    assert pantalla.como_dict() == {
        "plataforma": "amazon_us",
        "ciclos": [{"cycle_id": 7, "decisiones": [], "abstenciones": None}],
        "encogimiento": [
            {"hoja_id": 11, "bid_base": "10", "bid_hoy": "4", "razon": "0.4", "moneda": "USD"}
        ],
        "antes_y_despues": {"encendido_el": "2026-09-15", "antes": None, "despues": None},
    }


def _sql_ruido():
    import app.pantalla_ruido as modulo

    return {nombre: valor for nombre, valor in vars(modulo).items() if nombre.startswith("_SQL_")}


def test_candado_sql_no_lee_llaves_de_caso_ni_filtra_motivo():
    consultas = _sql_ruido()
    assert consultas, "la pantalla expone sus SELECT como _SQL_*"
    for nombre, sql in consultas.items():
        for llave in sorted(_CLAVES_CASO):
            assert not re.search(rf"(->>|->|#>>|#>|@>|\?)\s*'{llave}'", sql), (nombre, llave)
        assert "motivo IN (" not in sql, nombre
        assert "procedencia IN (" not in sql, nombre
        assert "CREATE VIEW" not in sql, nombre
    assert any("inputs ? 'caso'" in sql for sql in consultas.values())


# --- Postgres -------------------------------------------------------------------
# DB temporal con todas las migraciones en orden, sin la 0011 y sin las
# reversas (igual que `tests/test_pantalla_danadas.py`).


@contextmanager
def _db_ruido():
    from pathlib import Path

    raiz = Path(__file__).resolve().parents[1]
    dsn = _test_dsn()
    db = f"orbit_bids02_ruido_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        for ruta in sorted((raiz / "migrations").glob("*.sql")):
            if "0011_" in ruta.name or "_reversa_" in ruta.name:
                continue
            conn.execute(ruta.read_text(encoding="utf-8"))
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


_ANCLA = dt.datetime(2026, 9, 1, 8, 0, tzinfo=UTC)
_MONEDA = {"amazon_mx": "MXN", "amazon_us": "USD"}


def _entidad(conn, platform, kind, external, parent=None, *, match=None, texto=None):
    return conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id, parent_id, match_type,"
        " keyword_text) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (platform, kind, external, parent, match, texto),
    ).fetchone()[0]


def _estado(conn, ad_entity_id, status="ENABLED", *, targeting=None, bid=None, moneda=None):
    conn.execute(
        "INSERT INTO ad_entity_state (ad_entity_id, status, targeting_type, current_bid,"
        " bid_currency, synced_at) VALUES (%s, %s, %s, %s, %s, %s)",
        (ad_entity_id, status, targeting, bid, moneda, _ANCLA),
    )


def _triple(conn, platform="amazon_mx", *, campana="ENABLED", bid=Decimal("4"), tag=""):
    c = _entidad(conn, platform, "campaign", f"c-{tag}")
    g = _entidad(conn, platform, "ad_group", f"g-{tag}", c)
    h = _entidad(conn, platform, "keyword", f"h-{tag}", g, match="EXACT", texto=f"kw-{tag}")
    conn.execute("UPDATE ad_entity SET name = %s WHERE id = %s", (f"Camp {tag}", c))
    _estado(conn, c, campana, targeting="MANUAL")
    _estado(conn, g, "ENABLED")
    _estado(conn, h, "ENABLED", bid=bid, moneda=_MONEDA[platform])
    return c, g, h


def _config(conn, settings=None, *, created_at=None):
    if created_at is None:
        return conn.execute(
            "INSERT INTO config_version (settings) VALUES (%s) RETURNING id",
            (Json(settings or {}),),
        ).fetchone()[0]
    return conn.execute(
        "INSERT INTO config_version (settings, created_at) VALUES (%s, %s) RETURNING id",
        (Json(settings or {}), created_at),
    ).fetchone()[0]


def _ciclo(conn, mode, platform="amazon_mx", *, notes=None):
    return conn.execute(
        "INSERT INTO optimizer_cycle (mode, platform, notes) VALUES (%s, %s, %s) RETURNING id",
        (mode, platform, notes),
    ).fetchone()[0]


def _decision_caso(
    conn,
    ciclo,
    hoja,
    config,
    motivo,
    nivel,
    plataforma="amazon_mx",
    *,
    old=Decimal("10.0000"),
    new=Decimal("8.0000"),
):
    return conn.execute(
        "INSERT INTO decision (cycle_id, ad_entity_id, kind, config_version_id,"
        " data_observed_at, window_start, window_end, old_value, new_value,"
        " value_currency, inputs) VALUES (%s, %s, 'bid', %s, %s, %s, %s, %s, %s,"
        " %s, %s) RETURNING id",
        (
            ciclo,
            hoja,
            config,
            _ANCLA,
            dt.date(2026, 7, 1),
            dt.date(2026, 9, 29),
            old,
            new,
            _MONEDA[plataforma],
            Json(_inputs(motivo, nivel, hoja_id=hoja, plataforma=plataforma)),
        ),
    ).fetchone()[0]


def _aplicada(conn, decision, ejecutor, confirmado):
    conn.execute(
        "INSERT INTO decision_application (decision_id, applied_cycle_id, confirmed_at,"
        " platform_ack, verify_ok) VALUES (%s, %s, %s, %s, true)",
        (decision, ejecutor, confirmado, Json({"readback": True})),
    )


def _ingest(conn):
    return conn.execute(
        "INSERT INTO ingest_run (source) VALUES ('prueba') RETURNING id"
    ).fetchone()[0]


def _metrica_pg(conn, ingest, hoja, fecha, *, costo=None, pedidos=None, venta=None, moneda="MXN"):
    conn.execute(
        "INSERT INTO ads_metric_observation (ad_entity_id, metric_date, observed_at,"
        " metric_currency, cost, ad_revenue, impressions, clicks, orders, ingest_run_id)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            hoja,
            fecha,
            dt.datetime(2026, 10, 9, 12, 0, tzinfo=UTC),
            moneda,
            costo,
            venta,
            100,
            10,
            pedidos,
            ingest,
        ),
    )


@_FALTA_PG
def test_lee_ruido_punta_a_punta_mx_no_trae_us():
    """MX: un ciclo con decisiones y abstenciones, una hoja que encoge 10 a 4
    (fuera de ventana y sin cambios no salen) y la comparacion antes/después.
    US no contamina MX; US sin encendido trae antes_y_despues None."""
    from app.pantalla_ruido import lee_ruido

    with _db_ruido() as conn:
        ingest = _ingest(conn)
        config = _config(conn)
        _config(
            conn,
            {"ads_bid_politica_amazon_mx": "niveles_v3"},
            created_at=dt.datetime(2026, 9, 15, 8, 40, tzinfo=UTC),
        )
        vivo = _ciclo(conn, "live")
        _, _, h1 = _triple(conn, tag="encoge", bid=Decimal("4"))
        _, _, h2 = _triple(conn, tag="quieta", bid=Decimal("4"))
        _, _, h_fuera = _triple(conn, tag="vieja", bid=Decimal("6"))
        _, _, h_pausada = _triple(conn, tag="pausada", campana="PAUSED", bid=Decimal("4"))
        _, _, h_us = _triple(conn, "amazon_us", tag="us", bid=Decimal("4"))
        ciclo_mx = _ciclo(
            conn,
            "live",
            notes=json.dumps({"skips": {"entidad": {"espera_precio": 2}}}),
        )
        d1 = _decision_caso(conn, ciclo_mx, h1, config, "gasto_sin_venta", "hoja")
        _decision_caso(conn, ciclo_mx, h2, config, "gasto_sin_venta", "hoja")
        _decision_caso(conn, ciclo_mx, h_pausada, config, "grupo_sangra", "ad_group")
        ciclo_mx2 = _ciclo(conn, "live")
        d2 = _decision_caso(
            conn,
            ciclo_mx2,
            h1,
            config,
            "gasto_sin_venta",
            "hoja",
            old=Decimal("8.0000"),
            new=Decimal("4.0000"),
        )
        d_viejo = _decision_caso(
            conn,
            ciclo_mx2,
            h_fuera,
            config,
            "gasto_sin_venta",
            "hoja",
            old=Decimal("10.0000"),
            new=Decimal("6.0000"),
        )
        ciclo_us = _ciclo(
            conn, "live", "amazon_us", notes=json.dumps({"skips": {"entidad": {"otro": 9}}})
        )
        _decision_caso(conn, ciclo_us, h_us, config, "gasto_sin_venta", "hoja", "amazon_us")
        hoy = dt.datetime.now(UTC).date()
        hace_90 = dt.datetime.combine(hoy - dt.timedelta(days=90), dt.time(12, tzinfo=UTC))
        _aplicada(conn, d1, vivo, hace_90)
        _aplicada(
            conn, d2, vivo, dt.datetime.combine(hoy - dt.timedelta(days=5), dt.time(12, tzinfo=UTC))
        )
        _aplicada(
            conn,
            d_viejo,
            vivo,
            dt.datetime.combine(hoy - dt.timedelta(days=91), dt.time(12, tzinfo=UTC)),
        )
        _metrica_pg(
            conn,
            ingest,
            h1,
            dt.date(2026, 9, 10),
            costo=Decimal("100"),
            pedidos=2,
            venta=Decimal("1000"),
        )
        _metrica_pg(
            conn,
            ingest,
            h1,
            dt.date(2026, 9, 14),
            costo=Decimal("100"),
            pedidos=1,
            venta=Decimal("1000"),
        )
        _metrica_pg(
            conn,
            ingest,
            h1,
            dt.date(2026, 9, 15),
            costo=Decimal("50"),
            pedidos=1,
            venta=Decimal("1000"),
        )
        _metrica_pg(
            conn,
            ingest,
            h1,
            dt.date(2026, 9, 20),
            costo=Decimal("50"),
            pedidos=0,
            venta=Decimal("1000"),
        )
        _metrica_pg(
            conn,
            ingest,
            h_pausada,
            dt.date(2026, 9, 10),
            costo=Decimal("9999"),
            pedidos=99,
            venta=Decimal("9999"),
        )
        _metrica_pg(
            conn,
            ingest,
            h_us,
            dt.date(2026, 9, 10),
            costo=Decimal("777"),
            pedidos=7,
            venta=Decimal("777"),
            moneda="USD",
        )

        mx = lee_ruido(conn, plataforma="amazon_mx").como_dict()

    assert [c["cycle_id"] for c in mx["ciclos"]] == [ciclo_mx, ciclo_mx2]
    assert mx["ciclos"][0]["decisiones"] == [
        {"motivo": "gasto_sin_venta", "nivel": "hoja", "count": 2},
        {"motivo": "grupo_sangra", "nivel": "ad_group", "count": 1},
    ]
    assert mx["ciclos"][0]["abstenciones"] == {"espera_precio": 2}
    assert mx["ciclos"][1]["decisiones"] == [
        {"motivo": "gasto_sin_venta", "nivel": "hoja", "count": 2}
    ]
    assert mx["ciclos"][1]["abstenciones"] is None
    assert mx["encogimiento"] == [
        {"hoja_id": h1, "bid_base": "10.0000", "bid_hoy": "4.0000", "razon": "0.4", "moneda": "MXN"}
    ]
    assert mx["antes_y_despues"] == {
        "encendido_el": "2026-09-15",
        "moneda": "MXN",
        "antes": {"gasto": "200.0000", "pedidos": 3, "venta": "2000.0000", "acos_pct": "10.00"},
        "despues": {"gasto": "100.0000", "pedidos": 1, "venta": "2000.0000", "acos_pct": "5.00"},
    }

    with _db_ruido() as conn:
        ingest = _ingest(conn)
        config = _config(conn)
        _, _, hoja = _triple(conn, "amazon_us", tag="sola", bid=Decimal("4"))
        ciclo = _ciclo(conn, "live", "amazon_us")
        _decision_caso(conn, ciclo, hoja, config, "gasto_sin_venta", "hoja", "amazon_us")
        _metrica_pg(
            conn,
            ingest,
            hoja,
            dt.date(2026, 9, 10),
            costo=Decimal("10"),
            pedidos=1,
            venta=Decimal("100"),
            moneda="USD",
        )
        us = lee_ruido(conn, plataforma="amazon_us").como_dict()

    assert [c["cycle_id"] for c in us["ciclos"]] == [ciclo]
    assert us["antes_y_despues"] is None
