"""Contrato de keywords danadas (BIDS 02, P.3a).

Hoja activa con racha vigente de recortes, que vendia (>= 1 pedido en los
90 dias previos a la racha) y cuyos clics de los ultimos 14 dias legibles
son menos de 30 % de los 14 dias previos a la racha. Puro contrato, sin
plantillas: `HojaDanada`/`PantallaDanadas`/`como_dict` son puros y
`lee_danadas` es SOLO SELECT sobre la conexion de lectura (fakes aqui;
Postgres real al final, como `tests/test_jev_pantallas.py`).
"""

from __future__ import annotations

import datetime as dt
import os
import socket
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from psycopg import sql as pgsql
from psycopg.types.json import Json
from test_schema import _postgres_obligatorio_ausente, _test_dsn

from app.pantalla_danadas import HojaDanada, PantallaDanadas, lee_danadas

RAIZ = Path(__file__).resolve().parents[1]

_FALTA_PG = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)

UTC = dt.UTC
HOY = dt.date(2026, 10, 9)  # ultima fecha con metricas en los fakes
AHORA_DESDE = dt.date(2026, 9, 24)  # HOY - 15
AHORA_HASTA = dt.date(2026, 10, 7)  # HOY - 2


class _CursorFalso:
    def __init__(self, filas):
        self._filas = filas

    def fetchall(self):
        return self._filas

    def fetchone(self):
        return self._filas[0] if self._filas else None


class _ConnFalsa:
    """Conexion de lectura falsa: respuestas en orden de llamada."""

    def __init__(self, respuestas):
        self._respuestas = list(respuestas)
        self.consultas = []

    def execute(self, sql, params=None):
        self.consultas.append((sql, params))
        return _CursorFalso(self._respuestas.pop(0))


def _cambio(hoja, bid_hoy, confirmado, antes, despues, origen, decision):
    """Fila de cambios: (hoja_id, current_bid, confirmado_el, bid_antes,
    bid_despues, origen, decision_id)."""
    return (hoja, bid_hoy, confirmado, antes, despues, origen, decision)


def _metrica(hoja, fecha, *, clics=None, pedidos=None, venta=None):
    return (hoja, fecha, clics, pedidos, venta)


def _nombre(hoja, kind, name, texto, campana):
    return (hoja, kind, name, texto, campana)


def _lee(cambios, *, hoy=HOY, metricas=(), nombres=(), plataforma="amazon_mx"):
    conn = _ConnFalsa([list(cambios), [(hoy,)], list(metricas), list(nombres)])
    return lee_danadas(conn, plataforma=plataforma)


# --- criterio y orden ----------------------------------------------------------


def test_dos_hojas_ordena_por_venta_previa_y_llena_la_fila():
    cambios = [
        _cambio(
            11,
            Decimal("6"),
            dt.datetime(2026, 9, 20, 12, tzinfo=UTC),
            Decimal("10"),
            Decimal("8"),
            "motor",
            101,
        ),
        _cambio(
            11,
            Decimal("6"),
            dt.datetime(2026, 9, 25, 12, tzinfo=UTC),
            Decimal("8"),
            Decimal("6"),
            "motor",
            102,
        ),
        _cambio(
            12,
            Decimal("4"),
            dt.datetime(2026, 9, 22, 12, tzinfo=UTC),
            Decimal("5"),
            Decimal("4"),
            "motor",
            103,
        ),
    ]
    metricas = [
        _metrica(11, dt.date(2026, 9, 10), clics=100, pedidos=3, venta=Decimal("300.00")),
        _metrica(11, dt.date(2026, 8, 1), clics=50, pedidos=2, venta=Decimal("200.00")),
        _metrica(11, dt.date(2026, 10, 1), clics=20),
        _metrica(12, dt.date(2026, 9, 10), clics=40, pedidos=1, venta=Decimal("100.00")),
        _metrica(12, dt.date(2026, 10, 2), clics=5),
    ]
    nombres = [
        _nombre(11, "keyword", None, "zapato rojo", "Campaña MX"),
        _nombre(12, "product_target", '[{"type": "ASIN_SAME_AS"}]', None, "Campaña MX"),
    ]
    pantalla = _lee(cambios, metricas=metricas, nombres=nombres)
    assert pantalla.plataforma == "amazon_mx"
    assert pantalla.calculado_el == dt.datetime(2026, 10, 9, tzinfo=UTC)
    assert [h.hoja_id for h in pantalla.hojas] == [11, 12]
    primera, segunda = pantalla.hojas
    assert primera == HojaDanada(
        hoja_id=11,
        nombre="zapato rojo",
        campana="Campaña MX",
        recortes=2,
        bid_antes=Decimal("10"),
        bid_hoy=Decimal("6"),
        moneda="MXN",
        clics_antes_14d=100,
        clics_ahora_14d=20,
        pedidos_antes_90d=5,
        venta_antes_90d=Decimal("500.00"),
        ya_regresada=False,
        regresada_el=None,
    )
    assert segunda == HojaDanada(
        hoja_id=12,
        nombre="mismo ASIN",
        campana="Campaña MX",
        recortes=1,
        bid_antes=Decimal("5"),
        bid_hoy=Decimal("4"),
        moneda="MXN",
        clics_antes_14d=40,
        clics_ahora_14d=5,
        pedidos_antes_90d=1,
        venta_antes_90d=Decimal("100.00"),
        ya_regresada=False,
        regresada_el=None,
    )


def test_treinta_porciento_exacta_no_entra():
    cambios = [
        _cambio(
            11,
            Decimal("6"),
            dt.datetime(2026, 9, 20, 12, tzinfo=UTC),
            Decimal("10"),
            Decimal("6"),
            "motor",
            101,
        ),
    ]
    metricas = [
        _metrica(11, dt.date(2026, 9, 10), clics=100, pedidos=1, venta=Decimal("50.00")),
        _metrica(11, dt.date(2026, 10, 1), clics=30),  # 30 % exacto: fuera
    ]
    nombres = [_nombre(11, "keyword", None, "kw", "C")]
    assert _lee(cambios, metricas=metricas, nombres=nombres).hojas == ()


def test_sin_pedidos_previos_no_entra():
    cambios = [
        _cambio(
            11,
            Decimal("6"),
            dt.datetime(2026, 9, 20, 12, tzinfo=UTC),
            Decimal("10"),
            Decimal("6"),
            "motor",
            101,
        ),
    ]
    metricas = [
        _metrica(11, dt.date(2026, 9, 10), clics=100, pedidos=0, venta=Decimal("0")),
        _metrica(11, dt.date(2026, 10, 1), clics=1),
    ]
    nombres = [_nombre(11, "keyword", None, "kw", "C")]
    assert _lee(cambios, metricas=metricas, nombres=nombres).hojas == ()


def test_sin_clics_previos_no_entra():
    cambios = [
        _cambio(
            11,
            Decimal("6"),
            dt.datetime(2026, 9, 20, 12, tzinfo=UTC),
            Decimal("10"),
            Decimal("6"),
            "motor",
            101,
        ),
    ]
    metricas = [
        _metrica(11, dt.date(2026, 8, 1), pedidos=2, venta=Decimal("80.00")),
        _metrica(11, dt.date(2026, 10, 1), clics=1),
    ]
    nombres = [_nombre(11, "keyword", None, "kw", "C")]
    assert _lee(cambios, metricas=metricas, nombres=nombres).hojas == ()


def test_ventana_actual_con_nulls_no_entra():
    """R03-F6: NULLs recientes (ventana desconocida) sacan a la hoja aunque
    el resto grite danada: un todo-NULL sumaba 0 y entraba en falso."""
    cambios = [
        _cambio(
            11,
            Decimal("6"),
            dt.datetime(2026, 9, 20, 12, tzinfo=UTC),
            Decimal("10"),
            Decimal("6"),
            "motor",
            101,
        ),
    ]
    nombres = [_nombre(11, "keyword", None, "kw", "C")]
    assert len(_lee(cambios, metricas=_danada_minima(), nombres=nombres).hojas) == 1
    con_null = _danada_minima() + [_metrica(11, dt.date(2026, 10, 2))]
    assert _lee(cambios, metricas=con_null, nombres=nombres).hojas == ()


# --- racha vigente ---------------------------------------------------------------


def _danada_minima(hoja=11, *, hoy=HOY):
    """Metricas donde la hoja entra si su racha sigue vigente."""
    return [
        _metrica(hoja, dt.date(2026, 9, 10), clics=100, pedidos=2, venta=Decimal("90.00")),
        _metrica(hoja, dt.date(2026, 10, 1), clics=10),
    ]


def test_subida_del_motor_cierra_la_racha():
    cambios = [
        _cambio(
            11,
            Decimal("9"),
            dt.datetime(2026, 9, 20, 12, tzinfo=UTC),
            Decimal("10"),
            Decimal("8"),
            "motor",
            101,
        ),
        _cambio(
            11,
            Decimal("9"),
            dt.datetime(2026, 9, 25, 12, tzinfo=UTC),
            Decimal("8"),
            Decimal("9"),
            "motor",
            102,
        ),
    ]
    nombres = [_nombre(11, "keyword", None, "kw", "C")]
    assert _lee(cambios, metricas=_danada_minima(), nombres=nombres).hojas == ()


def test_regreso_del_dueno_no_cierra_y_marca_ya_regresada():
    cambios = [
        _cambio(
            11,
            Decimal("10"),
            dt.datetime(2026, 9, 20, 12, tzinfo=UTC),
            Decimal("10"),
            Decimal("8"),
            "motor",
            101,
        ),
        _cambio(
            11,
            Decimal("10"),
            dt.datetime(2026, 9, 26, 12, tzinfo=UTC),
            None,
            Decimal("10"),
            "regreso_del_dueno",
            101,
        ),
    ]
    nombres = [_nombre(11, "keyword", None, "kw", "C")]
    (fila,) = _lee(cambios, metricas=_danada_minima(), nombres=nombres).hojas
    assert fila.recortes == 1
    assert fila.bid_antes == Decimal("10")
    assert fila.bid_hoy == Decimal("10")  # el vigente, no el del ultimo recorte
    assert fila.ya_regresada is True
    assert fila.regresada_el == dt.date(2026, 9, 26)


def test_regreso_en_medio_de_recortes_no_marca_ya_regresada():
    cambios = [
        _cambio(
            11,
            Decimal("7"),
            dt.datetime(2026, 9, 20, 12, tzinfo=UTC),
            Decimal("10"),
            Decimal("8"),
            "motor",
            101,
        ),
        _cambio(
            11,
            Decimal("7"),
            dt.datetime(2026, 9, 22, 12, tzinfo=UTC),
            None,
            Decimal("10"),
            "regreso_del_dueno",
            101,
        ),
        _cambio(
            11,
            Decimal("7"),
            dt.datetime(2026, 9, 25, 12, tzinfo=UTC),
            Decimal("10"),
            Decimal("7"),
            "motor",
            102,
        ),
    ]
    nombres = [_nombre(11, "keyword", None, "kw", "C")]
    (fila,) = _lee(cambios, metricas=_danada_minima(), nombres=nombres).hojas
    assert fila.recortes == 2
    assert fila.ya_regresada is False
    assert fila.regresada_el is None


def test_regreso_por_desplome_cierra_como_subida_del_motor():
    cambios = [
        _cambio(
            11,
            Decimal("10"),
            dt.datetime(2026, 9, 20, 12, tzinfo=UTC),
            Decimal("10"),
            Decimal("8"),
            "motor",
            101,
        ),
        _cambio(
            11,
            Decimal("10"),
            dt.datetime(2026, 9, 26, 12, tzinfo=UTC),
            Decimal("8"),
            Decimal("10"),
            "regreso_por_desplome",
            102,
        ),
    ]
    nombres = [_nombre(11, "keyword", None, "kw", "C")]
    assert _lee(cambios, metricas=_danada_minima(), nombres=nombres).hojas == ()


# --- vacios y bordes -----------------------------------------------------------------


def test_sin_rachas_devuelve_vacia_sin_pedir_metricas_ni_nombres():
    conn = _ConnFalsa([[], [(HOY,)]])
    pantalla = lee_danadas(conn, plataforma="amazon_mx")
    assert pantalla.hojas == ()
    assert pantalla.calculado_el == dt.datetime(2026, 10, 9, tzinfo=UTC)
    assert len(conn.consultas) == 2


def test_sin_metricas_calculado_none_y_vacia():
    cambios = [
        _cambio(
            11,
            Decimal("6"),
            dt.datetime(2026, 9, 20, 12, tzinfo=UTC),
            Decimal("10"),
            Decimal("6"),
            "motor",
            101,
        ),
    ]
    conn = _ConnFalsa([list(cambios), [(None,)]])
    pantalla = lee_danadas(conn, plataforma="amazon_mx")
    assert pantalla.hojas == ()
    assert pantalla.calculado_el is None
    assert len(conn.consultas) == 2


def test_dato_faltante_viaja_none():
    cambios = [
        _cambio(
            11,
            None,
            dt.datetime(2026, 9, 20, 12, tzinfo=UTC),
            Decimal("10"),
            Decimal("6"),
            "motor",
            101,
        ),
    ]
    nombres = [_nombre(11, "product_target", "  ", None, None)]
    (fila,) = _lee(cambios, metricas=_danada_minima(), nombres=nombres).hojas
    assert fila.bid_hoy is None
    assert fila.nombre is None
    assert fila.campana is None


def test_acepta_conexion_con_dict_row():
    cambios = [
        {
            "hoja_id": 11,
            "current_bid": Decimal("6"),
            "confirmado_el": dt.datetime(2026, 9, 20, 12, tzinfo=UTC),
            "bid_antes": Decimal("10"),
            "bid_despues": Decimal("6"),
            "origen": "motor",
            "decision_id": 101,
        }
    ]
    metricas = [
        {
            "ad_entity_id": 11,
            "metric_date": dt.date(2026, 9, 10),
            "clicks": 100,
            "orders": 2,
            "ad_revenue": Decimal("90.00"),
        },
        {
            "ad_entity_id": 11,
            "metric_date": dt.date(2026, 10, 1),
            "clicks": 10,
            "orders": 0,
            "ad_revenue": Decimal("0"),
        },
    ]
    nombres = [
        {"id": 11, "kind": "keyword", "name": None, "keyword_text": "kw", "campana": "C"},
    ]
    conn = _ConnFalsa([cambios, [{"fecha_max": HOY}], metricas, nombres])
    (fila,) = lee_danadas(conn, plataforma="amazon_mx").hojas
    assert fila.hoja_id == 11
    assert fila.nombre == "kw"
    assert fila.venta_antes_90d == Decimal("90.00")


# --- contrato serializado ----------------------------------------------------------------


def _hoja_llena(**cambios):
    base = dict(
        hoja_id=11,
        nombre="zapato rojo",
        campana="Campaña MX",
        recortes=2,
        bid_antes=Decimal("10"),
        bid_hoy=Decimal("6"),
        moneda="MXN",
        clics_antes_14d=100,
        clics_ahora_14d=20,
        pedidos_antes_90d=5,
        venta_antes_90d=Decimal("500.00"),
        ya_regresada=False,
        regresada_el=None,
    )
    base.update(cambios)
    return HojaDanada(**base)


def test_como_dict_serializa_dinero_fechas_y_nones():
    pantalla = PantallaDanadas(
        "amazon_mx",
        dt.datetime(2026, 10, 9, tzinfo=UTC),
        (
            _hoja_llena(),
            _hoja_llena(
                hoja_id=12,
                bid_hoy=None,
                nombre=None,
                ya_regresada=True,
                regresada_el=dt.date(2026, 9, 26),
            ),
        ),
    )
    assert pantalla.como_dict() == {
        "plataforma": "amazon_mx",
        "calculado_el": "2026-10-09T00:00:00+00:00",
        "hojas": [
            {
                "hoja_id": 11,
                "nombre": "zapato rojo",
                "campana": "Campaña MX",
                "recortes": 2,
                "bid_antes": "10",
                "bid_hoy": "6",
                "moneda": "MXN",
                "clics_antes_14d": 100,
                "clics_ahora_14d": 20,
                "pedidos_antes_90d": 5,
                "venta_antes_90d": "500.00",
                "ya_regresada": False,
                "regresada_el": None,
            },
            {
                "hoja_id": 12,
                "nombre": None,
                "campana": "Campaña MX",
                "recortes": 2,
                "bid_antes": "10",
                "bid_hoy": None,
                "moneda": "MXN",
                "clics_antes_14d": 100,
                "clics_ahora_14d": 20,
                "pedidos_antes_90d": 5,
                "venta_antes_90d": "500.00",
                "ya_regresada": True,
                "regresada_el": "2026-09-26",
            },
        ],
    }


def test_como_dict_vacia_lleva_calculado_none():
    pantalla = PantallaDanadas("amazon_us", None, ())
    assert pantalla.como_dict() == {"plataforma": "amazon_us", "calculado_el": None, "hojas": []}


# --- Postgres real ------------------------------------------------------------------


@contextmanager
def _db_danadas():
    """DB temporal con todas las migraciones en orden, sin la 0011 y sin las
    reversas (igual que `tests/test_bids02_vistas.py`)."""
    dsn = _test_dsn()
    db = f"orbit_bids02_danadas_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        for ruta in sorted((RAIZ / "migrations").glob("*.sql")):
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
_DECIDIDA = dt.datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
_OBSERVADO = dt.datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
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


def _triple(conn, platform="amazon_mx", *, campana="ENABLED", bid=Decimal("6"), tag=""):
    c = _entidad(conn, platform, "campaign", f"c-{tag}")
    g = _entidad(conn, platform, "ad_group", f"g-{tag}", c)
    h = _entidad(conn, platform, "keyword", f"h-{tag}", g, match="EXACT", texto=f"kw-{tag}")
    conn.execute("UPDATE ad_entity SET name = %s WHERE id = %s", (f"Camp {tag}", c))
    _estado(conn, c, campana, targeting="MANUAL")
    _estado(conn, g, "ENABLED")
    _estado(conn, h, "ENABLED", bid=bid, moneda=_MONEDA[platform])
    return c, g, h


def _config(conn):
    return conn.execute(
        "INSERT INTO config_version (settings) VALUES (%s) RETURNING id", (Json({}),)
    ).fetchone()[0]


def _ciclo(conn, mode, platform="amazon_mx"):
    return conn.execute(
        "INSERT INTO optimizer_cycle (mode, platform) VALUES (%s, %s) RETURNING id",
        (mode, platform),
    ).fetchone()[0]


def _recorte(conn, *, hoja, config, ejecutor, old, new, confirmado, motivo=None, moneda="MXN"):
    """Decision de bid aplicada por un ciclo live. Devuelve el decision_id."""
    decision = conn.execute(
        "INSERT INTO decision (cycle_id, ad_entity_id, kind, config_version_id,"
        " decided_at, data_observed_at, window_start, window_end, old_value, new_value,"
        " value_currency, inputs) VALUES (%s, %s, 'bid', %s, %s, %s, %s, %s, %s, %s,"
        " %s, %s) RETURNING id",
        (
            _ciclo(conn, "live"),
            hoja,
            config,
            _DECIDIDA,
            _OBSERVADO,
            dt.date(2026, 8, 15),
            dt.date(2026, 9, 5),
            old,
            new,
            moneda,
            Json({"motivo": motivo} if motivo else {}),
        ),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO decision_application (decision_id, applied_cycle_id, confirmed_at,"
        " platform_ack, verify_ok) VALUES (%s, %s, %s, %s, true)",
        (decision, ejecutor, confirmado, Json({"readback": True})),
    )
    return decision


def _reversa(conn, decision, *, sellada):
    conn.execute(
        "INSERT INTO apply_attempt (decision_id, seq, tipo, request_payload,"
        " quota_cobrada, resultado, finished_at)"
        " VALUES (%s, 1, 'reversa', %s, false, 'ok', %s)",
        (decision, Json({"bid": "viejo"}), sellada),
    )


def _ingest(conn):
    return conn.execute(
        "INSERT INTO ingest_run (source) VALUES ('prueba') RETURNING id"
    ).fetchone()[0]


def _metrica_pg(conn, ingest, hoja, fecha, *, clics=None, pedidos=None, venta=None, moneda="MXN"):
    conn.execute(
        "INSERT INTO ads_metric_observation (ad_entity_id, metric_date, observed_at,"
        " metric_currency, cost, ad_revenue, impressions, clicks, orders, ingest_run_id)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            hoja,
            fecha,
            dt.datetime(2026, 10, 9, 12, 0, tzinfo=UTC),
            moneda,
            venta,
            venta,
            (clics or 0) * 100 if clics is not None else None,
            clics,
            pedidos,
            ingest,
        ),
    )


@_FALTA_PG
def test_lee_danadas_punta_a_punta():
    """Seis hojas MX: entra la danada y la regresada; no entran la del 30 %
    exacto, la sin pedidos, la de campana PAUSED ni la que el motor subio.
    Una danada de US no sale en la lectura de MX."""
    with _db_danadas() as conn:
        ingest = _ingest(conn)
        config = _config(conn)
        vivo = _ciclo(conn, "live")
        _, _, entra = _triple(conn, tag="entra", bid=Decimal("6"))
        _, _, exacta = _triple(conn, tag="exacta", bid=Decimal("6"))
        _, _, sin_pedidos = _triple(conn, tag="sin-pedidos", bid=Decimal("6"))
        _, _, pausada = _triple(conn, tag="pausada", campana="PAUSED", bid=Decimal("6"))
        _, _, subida = _triple(conn, tag="subida", bid=Decimal("9"))
        _, _, regresada = _triple(conn, tag="regresada", bid=Decimal("10"))
        _, _, otra_plataforma = _triple(conn, "amazon_us", tag="us", bid=Decimal("6"))
        r1 = dt.datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
        r2 = dt.datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
        _recorte(
            conn,
            hoja=entra,
            config=config,
            ejecutor=vivo,
            old=Decimal("10"),
            new=Decimal("8"),
            confirmado=r1,
        )
        _recorte(
            conn,
            hoja=entra,
            config=config,
            ejecutor=vivo,
            old=Decimal("8"),
            new=Decimal("6"),
            confirmado=r2,
        )
        _recorte(
            conn,
            hoja=exacta,
            config=config,
            ejecutor=vivo,
            old=Decimal("10"),
            new=Decimal("6"),
            confirmado=r1,
        )
        _recorte(
            conn,
            hoja=sin_pedidos,
            config=config,
            ejecutor=vivo,
            old=Decimal("10"),
            new=Decimal("6"),
            confirmado=r1,
        )
        _recorte(
            conn,
            hoja=pausada,
            config=config,
            ejecutor=vivo,
            old=Decimal("10"),
            new=Decimal("6"),
            confirmado=r1,
        )
        _recorte(
            conn,
            hoja=subida,
            config=config,
            ejecutor=vivo,
            old=Decimal("10"),
            new=Decimal("8"),
            confirmado=r1,
        )
        _recorte(
            conn,
            hoja=subida,
            config=config,
            ejecutor=vivo,
            old=Decimal("8"),
            new=Decimal("9"),
            confirmado=r2,
        )
        d_reg = _recorte(
            conn,
            hoja=regresada,
            config=config,
            ejecutor=vivo,
            old=Decimal("10"),
            new=Decimal("8"),
            confirmado=r1,
        )
        _reversa(conn, d_reg, sellada=dt.datetime(2026, 9, 26, 12, 0, tzinfo=UTC))
        _recorte(
            conn,
            hoja=otra_plataforma,
            config=config,
            ejecutor=vivo,
            old=Decimal("10"),
            new=Decimal("6"),
            confirmado=r1,
            moneda="USD",
        )
        for hoja in (entra, exacta, pausada, subida, regresada):
            _metrica_pg(
                conn, ingest, hoja, dt.date(2026, 9, 10), clics=100, pedidos=0, venta=Decimal("0")
            )
            _metrica_pg(
                conn, ingest, hoja, dt.date(2026, 8, 1), clics=50, pedidos=2, venta=Decimal("90.00")
            )
        _metrica_pg(
            conn,
            ingest,
            sin_pedidos,
            dt.date(2026, 9, 10),
            clics=100,
            pedidos=0,
            venta=Decimal("0"),
        )
        _metrica_pg(
            conn, ingest, sin_pedidos, dt.date(2026, 8, 1), clics=50, pedidos=0, venta=Decimal("0")
        )
        _metrica_pg(conn, ingest, entra, dt.date(2026, 10, 1), clics=20)
        _metrica_pg(conn, ingest, exacta, dt.date(2026, 10, 1), clics=30)
        _metrica_pg(conn, ingest, sin_pedidos, dt.date(2026, 10, 1), clics=1)
        _metrica_pg(conn, ingest, pausada, dt.date(2026, 10, 1), clics=20)
        _metrica_pg(conn, ingest, subida, dt.date(2026, 10, 1), clics=20)
        _metrica_pg(conn, ingest, regresada, dt.date(2026, 10, 1), clics=10)
        _metrica_pg(
            conn,
            ingest,
            otra_plataforma,
            dt.date(2026, 9, 10),
            clics=100,
            pedidos=0,
            venta=Decimal("0"),
            moneda="USD",
        )
        _metrica_pg(
            conn,
            ingest,
            otra_plataforma,
            dt.date(2026, 8, 1),
            clics=50,
            pedidos=2,
            venta=Decimal("90.00"),
            moneda="USD",
        )
        _metrica_pg(conn, ingest, otra_plataforma, dt.date(2026, 10, 1), clics=20, moneda="USD")
        # Ancla HOY en MX: ultima fecha con metricas de una hoja activa.
        _metrica_pg(conn, ingest, entra, dt.date(2026, 10, 9), clics=0)

        pantalla = lee_danadas(conn, plataforma="amazon_mx")

    assert pantalla.calculado_el == dt.datetime(2026, 10, 9, tzinfo=UTC)
    assert [h.hoja_id for h in pantalla.hojas] == [entra, regresada]
    assert pantalla.hojas[0] == HojaDanada(
        hoja_id=entra,
        nombre="kw-entra",
        campana="Camp entra",
        recortes=2,
        bid_antes=Decimal("10"),
        bid_hoy=Decimal("6"),
        moneda="MXN",
        clics_antes_14d=100,
        clics_ahora_14d=20,
        pedidos_antes_90d=2,
        venta_antes_90d=Decimal("90.00"),
        ya_regresada=False,
        regresada_el=None,
    )
    assert pantalla.hojas[1].ya_regresada is True
    assert pantalla.hojas[1].regresada_el == dt.date(2026, 9, 26)
    assert pantalla.hojas[1].bid_hoy == Decimal("10")


# --- P.3b: pantalla ------------------------------------------------------------


def _hoja_ui(**cambios):
    """Fila con el shape de `HojaDanada.como_dict` (los numeros son los
    reales de la hoja 2963 del diseno)."""
    hoja = {
        "hoja_id": 2963,
        "nombre": "gorra roja",
        "campana": "AC - Category Exact",
        "recortes": 5,
        "bid_antes": "9.7400",
        "bid_hoy": "3.7300",
        "moneda": "MXN",
        "clics_antes_14d": 133,
        "clics_ahora_14d": 16,
        "pedidos_antes_90d": 31,
        "venta_antes_90d": "28672.0000",
        "ya_regresada": False,
        "regresada_el": None,
    }
    hoja.update(cambios)
    return hoja


def _html_danadas(hojas, plataforma="amazon_mx"):
    from app import ui

    return ui.templates.env.get_template("keywords_danadas.html").render(
        pantalla="keywords-danadas",
        plataforma=plataforma,
        calculado_el="2026-10-09T00:00:00+00:00",
        hojas=hojas,
    )


def _plano(html):
    return " ".join(html.split())


def test_pantalla_danadas_fila_pendiente_pinta_tres_lineas_boton_y_frase():
    """P.3b cambio 2: cada fila trae las tres lineas del ejemplo de la hoja
    2963, el boton con el bid de antes y la frase literal del paso con los
    numeros de la fila."""
    html = _html_danadas([_hoja_ui()])
    plano = _plano(html)
    assert "gorra roja · AC - Category Exact - MX" in plano
    assert "Vendía 31 pedidos (28672.00 MXN) en los 3 meses antes de los recortes." in plano
    assert (
        "Clics cada 2 semanas: 133 antes, 16 ahora. Bid: 9.74 antes, 3.73 hoy, tras 5 recortes."
    ) in plano
    assert "Regresar el bid a 9.74" in plano
    assert 'data-regresar="2963"' in html
    assert (
        "Orbit cambia ahora el bid en Amazon de 3.73 a 9.74 MXN. "
        "El motor espera 7 días. Después no la recorta hasta que junte "
        "20 clics al bid nuevo o pasen 14 días, y nunca la baja del bid "
        "que la dañó."
    ) in plano


def test_pantalla_danadas_sin_bid_hoy_boton_deshabilitado_con_data():
    """R03-r2: con bid_hoy desconocido el boton sale deshabilitado pero
    conserva data-regresar (el cableado JS lo encuentra)."""
    html = _html_danadas([_hoja_ui(bid_hoy=None)])
    assert 'data-regresar="2963"' in html
    assert "disabled" in html


def test_pantalla_danadas_fila_regresada_muestra_fecha_y_no_boton():
    """P.3b Comprueba: una fila con `ya_regresada` muestra la fecha de
    `regresada_el` y no pinta el boton."""
    html = _html_danadas([_hoja_ui(ya_regresada=True, regresada_el="2026-10-09")])
    assert "2026-10-09" in html
    assert "Regresar el bid" not in html
    assert "data-regresar=" not in html


def test_pantalla_danadas_regresar_todas_pide_una_confirmacion():
    """P.3b cambio 3: arriba de la lista, "Regresar todas" muestra el bid al
    que vuelve cada pendiente y pide el literal `REGRESAR <N> KEYWORDS` en
    un solo campo (N cuenta solo `ya_regresada` en falso)."""
    html = _html_danadas(
        [
            _hoja_ui(),
            _hoja_ui(
                hoja_id=2871,
                nombre="gorra azul",
                bid_antes="4.1000",
                bid_hoy="2.0000",
            ),
            _hoja_ui(hoja_id=2880, ya_regresada=True, regresada_el="2026-10-08"),
        ]
    )
    plano = _plano(html)
    assert plano.index("Regresar todas") < plano.index("gorra roja")
    assert "REGRESAR 2 KEYWORDS" in plano
    assert 'data-esperada="REGRESAR 2 KEYWORDS"' in html
    assert html.count('name="confirmacion"') == 1
    assert "gorra roja · vuelve a 9.74 MXN" in plano
    assert "gorra azul · vuelve a 4.10 MXN" in plano


def test_pantalla_danadas_sin_pendientes_no_pinta_regresar_todas():
    """P.3b Pruebas primero: con N en 0 (todas regresadas o lista vacia),
    el bloque no se pinta."""
    regresadas = _html_danadas([_hoja_ui(ya_regresada=True, regresada_el="2026-10-09")])
    assert "Regresar todas" not in regresadas
    assert "REGRESAR" not in regresadas
    vacia = _html_danadas([])
    assert "Regresar todas" not in vacia
    assert "REGRESAR" not in vacia
