"""Tests de insumos MARGEN ESTIMADO 01 A.2 (precio/oferta, costo, FX).

Fuente bridge: snapshot SQLite READ-ONLY `amazon_listing_prices` (acta 0.3).
Destino: `estimacion_oferta_observation` + resolucion de `sku_cost`/`fx_resolve`.

(a) UNITARIOS: funciones puras sobre filas bridge y reglas de frescura/universo.
(b) INTEGRACION: persistencia idempotente y resolucion costo/FX en Postgres real
    (0001 + 0028). Skip fail-closed sin Postgres utilizable.

Regla 9: cada caso distingue el codigo previo (ModuleNotFoundError al coleccionar).
"""

from __future__ import annotations

import os
import socket
import sqlite3
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from test_schema import _postgres_obligatorio_ausente, _test_dsn

from app.estimacion_insumos import (
    FRESHNESS_LIMIT,
    FilaOfertaBridge,
    construir_canonical_input,
    construir_context_fingerprint,
    construir_source_event_id,
    evaluar_costo_candidatos,
    evaluar_frescura,
    leer_ofertas_bridge,
    mapear_canal,
    mapear_plataforma,
    normalizar_precio,
    parse_fetched_at_utc,
    persistir_oferta_observation,
    resolver_costo,
    resolver_fx,
    resolver_oferta_para_listing,
)

ROOT = Path(__file__).resolve().parents[1]

NOW = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)
FETCH_FRESH = NOW - timedelta(hours=6)
FETCH_STALE = NOW - timedelta(hours=6, seconds=1)
FETCH_FUTURE = NOW + timedelta(minutes=1)

_DDL_BRIDGE = """
CREATE TABLE amazon_listing_prices (
    id INTEGER PRIMARY KEY,
    seller_sku TEXT NOT NULL,
    asin TEXT,
    listing_id TEXT,
    marketplace_id TEXT NOT NULL,
    marketplace_name TEXT,
    price REAL,
    quantity INTEGER,
    fulfillment_channel TEXT,
    item_name TEXT,
    status TEXT,
    fetched_at TEXT,
    UNIQUE(seller_sku, marketplace_id)
);
"""


def _snapshot_bridge(ruta: Path, filas: list[tuple]) -> Path:
    """filas: (seller_sku, asin, marketplace_id, marketplace_name, price, channel, fetched_at)."""
    con = sqlite3.connect(ruta)
    con.executescript(_DDL_BRIDGE)
    con.executemany(
        "INSERT INTO amazon_listing_prices"
        " (seller_sku, asin, marketplace_id, marketplace_name, price,"
        " fulfillment_channel, fetched_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        filas,
    )
    con.commit()
    con.close()
    return ruta


def _fila_bridge(
    *,
    seller_sku: str = "SS-MX-1",
    asin: str = "B0EST01",
    marketplace_id: str = "A1AM78C64UM0Y8",
    marketplace_name: str = "amazon_mx",
    price: float | None = 116.0,
    channel: str = "AMAZON_NA",
    fetched_at: str = "2026-09-08T06:00:00+00:00",
) -> FilaOfertaBridge:
    return FilaOfertaBridge(
        seller_sku=seller_sku,
        asin=asin,
        marketplace_id=marketplace_id,
        marketplace_name=marketplace_name,
        price=price,
        fulfillment_channel=channel,
        fetched_at=fetched_at,
    )


# ---------------------------------------------------------------------------
# (a) UNITARIOS — bridge, frescura, universo, identidad
# ---------------------------------------------------------------------------


def test_leer_ofertas_bridge_mode_ro_y_columnas(tmp_path):
    snap = _snapshot_bridge(
        tmp_path / "bridge.db",
        [
            (
                "SS-MX-1",
                "B0EST01",
                "A1AM78C64UM0Y8",
                "amazon_mx",
                116.0,
                "AMAZON_NA",
                "2026-09-08T06:00:00+00:00",
            )
        ],
    )
    filas = leer_ofertas_bridge(snap)
    assert len(filas) == 1
    assert filas[0].seller_sku == "SS-MX-1"
    assert filas[0].fulfillment_channel == "AMAZON_NA"


def test_mapeo_marketplace_y_canal():
    assert mapear_plataforma("A1AM78C64UM0Y8") == "amazon_mx"
    assert mapear_plataforma("ATVPDKIKX0DER") == "amazon_us"
    assert mapear_plataforma("DESCONOCIDO") is None
    assert mapear_canal("AMAZON_NA") == "fba"
    assert mapear_canal("DEFAULT") == "fbm"
    assert mapear_canal("OTRO") is None


def test_frescura_exacta_6h_es_fresca():
    fetched = NOW - FRESHNESS_LIMIT
    assert evaluar_frescura(fetched, NOW) == "fresco"


def test_frescura_vieja_mas_de_6h():
    assert evaluar_frescura(FETCH_STALE, NOW) == "desactualizada"


def test_fetched_at_futuro_rechazado():
    assert evaluar_frescura(FETCH_FUTURE, NOW) == "futura"


def test_precio_invalido_rechazado():
    assert normalizar_precio(None)[0] is None
    assert normalizar_precio(0.0)[0] is None
    assert normalizar_precio(float("inf"))[0] is None
    assert normalizar_precio(1.234567)[0] is None
    ok, _ = normalizar_precio(116.0)
    assert ok == Decimal("116.0000")


def test_resolver_oferta_ausente():
    resultado = resolver_oferta_para_listing(
        (),
        listing_id=1,
        platform="amazon_mx",
        seller_sku="SS-MX-1",
        asin="B0EST01",
        now_utc=NOW,
    )
    assert resultado.motivo == "oferta_ausente"
    assert resultado.oferta is None


def test_resolver_oferta_desactualizada():
    fila = _fila_bridge(fetched_at=FETCH_STALE.isoformat())
    resultado = resolver_oferta_para_listing(
        (fila,),
        listing_id=1,
        platform="amazon_mx",
        seller_sku="SS-MX-1",
        asin="B0EST01",
        now_utc=NOW,
    )
    assert resultado.motivo == "oferta_desactualizada"
    assert resultado.oferta is None


def test_resolver_oferta_futura():
    fila = _fila_bridge(fetched_at=FETCH_FUTURE.isoformat())
    resultado = resolver_oferta_para_listing(
        (fila,),
        listing_id=1,
        platform="amazon_mx",
        seller_sku="SS-MX-1",
        asin="B0EST01",
        now_utc=NOW,
    )
    assert resultado.motivo == "oferta_futura"
    assert resultado.oferta is None


def test_resolver_identidad_ambigua_precios_distintos():
    f1 = _fila_bridge(price=116.0, fetched_at=FETCH_FRESH.isoformat())
    f2 = _fila_bridge(price=120.0, fetched_at=FETCH_FRESH.isoformat())
    resultado = resolver_oferta_para_listing(
        (f1, f2),
        listing_id=1,
        platform="amazon_mx",
        seller_sku="SS-MX-1",
        asin="B0EST01",
        now_utc=NOW,
    )
    assert resultado.motivo == "identidad_ambigua"
    assert resultado.oferta is None


def test_resolver_identidad_ambigua_dos_skus_mismo_asin_uno_precio_invalido():
    """Reproduccion: A-SKU FBA 116 + B-SKU FBA price NULL; listing A-SKU -> ambigua."""
    f_a = _fila_bridge(
        seller_sku="A-SKU",
        price=116.0,
        fetched_at=FETCH_FRESH.isoformat(),
    )
    f_b = _fila_bridge(
        seller_sku="B-SKU",
        price=None,
        fetched_at=FETCH_FRESH.isoformat(),
    )
    resultado = resolver_oferta_para_listing(
        (f_a, f_b),
        listing_id=1,
        platform="amazon_mx",
        seller_sku="A-SKU",
        asin="B0EST01",
        now_utc=NOW,
    )
    assert resultado.motivo == "identidad_ambigua"
    assert resultado.oferta is None


def test_resolver_identidad_ambigua_dos_skus_mismo_asin():
    """Reproduccion: ASIN/MX con A-SKU FBA 116 y B-SKU FBA 150; listing heredado A-SKU."""
    f_a = _fila_bridge(
        seller_sku="A-SKU",
        price=116.0,
        fetched_at=FETCH_FRESH.isoformat(),
    )
    f_b = _fila_bridge(
        seller_sku="B-SKU",
        price=150.0,
        fetched_at=FETCH_FRESH.isoformat(),
    )
    resultado = resolver_oferta_para_listing(
        (f_a, f_b),
        listing_id=1,
        platform="amazon_mx",
        seller_sku="A-SKU",
        asin="B0EST01",
        now_utc=NOW,
    )
    assert resultado.motivo == "identidad_ambigua"
    assert resultado.oferta is None


def test_resolver_identidad_ambigua_sku_unico_no_coincide_listing():
    """Oferta coherente pero seller_sku distinto al listing: no persistir."""
    fila = _fila_bridge(
        seller_sku="B-SKU",
        price=116.0,
        fetched_at=FETCH_FRESH.isoformat(),
    )
    resultado = resolver_oferta_para_listing(
        (fila,),
        listing_id=1,
        platform="amazon_mx",
        seller_sku="A-SKU",
        asin="B0EST01",
        now_utc=NOW,
    )
    assert resultado.motivo == "identidad_ambigua"
    assert resultado.oferta is None


def test_fbm_y_us_quedan_fuera_del_universo():
    fbm = _fila_bridge(channel="DEFAULT", fetched_at=FETCH_FRESH.isoformat())
    us = _fila_bridge(
        marketplace_id="ATVPDKIKX0DER",
        marketplace_name="amazon_us",
        price=29.99,
        fetched_at=FETCH_FRESH.isoformat(),
    )
    r_fbm = resolver_oferta_para_listing(
        (fbm,),
        listing_id=1,
        platform="amazon_mx",
        seller_sku="SS-MX-1",
        asin="B0EST01",
        now_utc=NOW,
    )
    r_us = resolver_oferta_para_listing(
        (us,),
        listing_id=2,
        platform="amazon_us",
        seller_sku="SS-MX-1",
        asin="B0EST01",
        now_utc=NOW,
    )
    assert r_fbm.motivo == "logistica_fbm_pendiente"
    assert r_us.motivo == "us_sin_politica_prospectiva"
    assert r_fbm.oferta is None and r_us.oferta is None


def test_resolver_oferta_precio_invalido_cuenta_como_ausente():
    fila = _fila_bridge(price=-1.0, fetched_at=FETCH_FRESH.isoformat())
    resultado = resolver_oferta_para_listing(
        (fila,),
        listing_id=1,
        platform="amazon_mx",
        seller_sku="SS-MX-1",
        asin="B0EST01",
        now_utc=NOW,
    )
    assert resultado.motivo == "oferta_ausente"


def test_fba_mx_fresca_resuelve_oferta():
    fila = _fila_bridge(fetched_at=FETCH_FRESH.isoformat())
    resultado = resolver_oferta_para_listing(
        (fila,),
        listing_id=1,
        platform="amazon_mx",
        seller_sku="SS-MX-1",
        asin="B0EST01",
        now_utc=NOW,
    )
    assert resultado.motivo is None
    assert resultado.oferta is not None
    assert resultado.oferta.canal == "fba"
    assert resultado.oferta.price_amount == Decimal("116.0000")
    assert resultado.oferta.price_currency == "MXN"


def test_canonical_y_evento_estables_sin_observed_at():
    fila = _fila_bridge(fetched_at=FETCH_FRESH.isoformat())
    platform = "amazon_mx"
    canal = "fba"
    precio = Decimal("116.0000")
    moneda = "MXN"
    fetched = parse_fetched_at_utc(fila.fetched_at)
    canon1 = construir_canonical_input(fila, platform, canal, precio, moneda)
    canon2 = construir_canonical_input(fila, platform, canal, precio, moneda)
    assert canon1 == canon2
    assert "observed_at" not in canon1
    evt1 = construir_source_event_id(canon1)
    evt2 = construir_source_event_id(canon2)
    assert evt1 == evt2
    huella = construir_context_fingerprint(canal, precio, moneda, fetched)
    assert huella == f"fba:116.0000:MXN:{fetched.isoformat()}"


def test_cambio_real_de_precio_cambia_evento():
    f1 = _fila_bridge(price=116.0, fetched_at=FETCH_FRESH.isoformat())
    f2 = _fila_bridge(price=120.0, fetched_at=FETCH_FRESH.isoformat())
    kwargs = dict(
        listing_id=1,
        platform="amazon_mx",
        seller_sku="SS-MX-1",
        asin="B0EST01",
        now_utc=NOW,
    )
    o1 = resolver_oferta_para_listing((f1,), **kwargs).oferta
    o2 = resolver_oferta_para_listing((f2,), **kwargs).oferta
    assert o1 and o2
    assert o1.source_event_id != o2.source_event_id


# ---------------------------------------------------------------------------
# (b) INTEGRACION — persistencia, costo, FX
# ---------------------------------------------------------------------------

ORDEN = ("0001_initial.sql", "0028_estimacion_venta.sql")
_skip_db = pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres")


@contextmanager
def db_insumos(prefijo: str = "orbit_margen_a2"):
    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql as pgsql

    dsn = _test_dsn()
    db = f"{prefijo}_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=False)
        conn.execute("SET TIME ZONE 'UTC'")
        for nombre in ORDEN:
            conn.execute((ROOT / "migrations" / nombre).read_text(encoding="utf-8"))
        conn.commit()
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def _sembrar_listing(conn, *, platform="amazon_mx", asin="B0EST01", seller_sku="SS-MX-1"):
    pid = conn.execute(
        "INSERT INTO product (odoo_sku, name) VALUES ('P-MX', 'P-MX') RETURNING id"
    ).fetchone()[0]
    lid = conn.execute(
        "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
        " VALUES (%s, %s, %s, %s) RETURNING id",
        (pid, platform, asin, seller_sku),
    ).fetchone()[0]
    conn.commit()
    return pid, lid


_DEFAULT_FINISHED = object()


def _sembrar_corrida_costos(
    conn,
    dia: date,
    *,
    ok: bool = True,
    started_at: datetime | None = None,
    finished_at: datetime | None | object = _DEFAULT_FINISHED,
    source: str = "accounting_sku_costs",
    rows_skipped: int = 0,
    skip_reason: str | None = None,
) -> int:
    if started_at is None:
        started_at = datetime(dia.year, dia.month, dia.day, 8, 15, tzinfo=UTC)
    if finished_at is _DEFAULT_FINISHED:
        finished_at = datetime(dia.year, dia.month, dia.day, 8, 20, tzinfo=UTC)
    if rows_skipped > 0 and skip_reason is None:
        skip_reason = "1x costo cero o nulo (dato faltante)"
    run_id = conn.execute(
        "INSERT INTO ingest_run (source, started_at, finished_at, ok, rows_written, rows_skipped,"
        " skip_reason)"
        " VALUES (%s, %s, %s, %s, 1, %s, %s) RETURNING id",
        (source, started_at, finished_at, ok, rows_skipped, skip_reason),
    ).fetchone()[0]
    conn.commit()
    return run_id


@_skip_db
def test_persistir_oferta_idempotente_sin_rejuvenecer():
    with db_insumos() as conn:
        _, lid = _sembrar_listing(conn)
        fila = _fila_bridge(fetched_at=FETCH_FRESH.isoformat())
        resuelta = resolver_oferta_para_listing(
            (fila,),
            listing_id=lid,
            platform="amazon_mx",
            seller_sku="SS-MX-1",
            asin="B0EST01",
            now_utc=NOW,
        ).oferta
        assert resuelta is not None
        obs1 = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
        obs2 = datetime(2026, 9, 8, 13, 0, tzinfo=UTC)
        r1 = persistir_oferta_observation(conn, resuelta, observed_at=obs1)
        conn.commit()
        r2 = persistir_oferta_observation(conn, resuelta, observed_at=obs2)
        conn.commit()
        assert r1.id == r2.id
        assert r1.observed_at == r2.observed_at == obs1
        assert r2.reutilizada is True
        assert conn.execute("SELECT count(*) FROM estimacion_oferta_observation").fetchone()[0] == 1


@_skip_db
def test_persistir_cambio_real_crea_fila_nueva():
    with db_insumos() as conn:
        _, lid = _sembrar_listing(conn)
        f1 = _fila_bridge(price=116.0, fetched_at=FETCH_FRESH.isoformat())
        f2 = _fila_bridge(price=120.0, fetched_at=FETCH_FRESH.isoformat())
        kwargs = dict(
            listing_id=lid,
            platform="amazon_mx",
            seller_sku="SS-MX-1",
            asin="B0EST01",
            now_utc=NOW,
        )
        o1 = resolver_oferta_para_listing((f1,), **kwargs).oferta
        o2 = resolver_oferta_para_listing((f2,), **kwargs).oferta
        obs = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
        persistir_oferta_observation(conn, o1, observed_at=obs)
        persistir_oferta_observation(conn, o2, observed_at=obs)
        conn.commit()
        n = conn.execute("SELECT count(*) FROM estimacion_oferta_observation").fetchone()[0]
        assert n == 2


@_skip_db
def test_resolver_costo_vigente():
    with db_insumos() as conn:
        pid, _ = _sembrar_listing(conn)
        dia = date(2026, 9, 8)
        run_id = _sembrar_corrida_costos(conn, dia)
        conn.execute(
            "INSERT INTO sku_cost (product_id, cost_amount, cost_currency, includes_tax,"
            " valid_from, ingest_run_id)"
            " VALUES (%s, 40.0000, 'MXN', false, %s, %s)",
            (pid, dia, run_id),
        )
        conn.commit()
        resultado = resolver_costo(conn, product_id=pid, fecha_escenario=dia, now_utc=NOW)
        assert resultado.motivo is None
        assert resultado.costo is not None
        assert resultado.costo.cost_amount == Decimal("40.0000")
        assert resultado.costo.includes_tax is False
        assert resultado.costo.validation_run_id == run_id
        assert resultado.costo.validated_at == datetime(2026, 9, 8, 8, 20, tzinfo=UTC)


@_skip_db
def test_resolver_costo_estable_ayer_validado_hoy():
    """Costo sin cambios (ingest_run_id de ayer) + corrida global ok hoy -> acepta."""
    with db_insumos() as conn:
        pid, _ = _sembrar_listing(conn)
        ayer = date(2026, 9, 7)
        hoy = date(2026, 9, 8)
        run_ayer = _sembrar_corrida_costos(conn, ayer)
        run_hoy = _sembrar_corrida_costos(conn, hoy)
        conn.execute(
            "INSERT INTO sku_cost (product_id, cost_amount, cost_currency, includes_tax,"
            " valid_from, ingest_run_id)"
            " VALUES (%s, 40.0000, 'MXN', false, %s, %s)",
            (pid, ayer, run_ayer),
        )
        conn.commit()
        resultado = resolver_costo(conn, product_id=pid, fecha_escenario=hoy, now_utc=NOW)
        assert resultado.motivo is None
        assert resultado.costo is not None
        assert resultado.costo.ingest_run_id == run_ayer
        assert resultado.costo.validation_run_id == run_hoy
        assert resultado.costo.validated_at == datetime(2026, 9, 8, 8, 20, tzinfo=UTC)


@_skip_db
def test_resolver_costo_no_vigente():
    with db_insumos() as conn:
        pid, _ = _sembrar_listing(conn)
        dia = date(2026, 9, 8)
        run_id = _sembrar_corrida_costos(conn, dia)
        conn.execute(
            "INSERT INTO sku_cost (product_id, cost_amount, cost_currency, includes_tax,"
            " valid_from, valid_to, ingest_run_id)"
            " VALUES (%s, 40.0000, 'MXN', false, '2026-01-01', '2026-06-01', %s)",
            (pid, run_id),
        )
        conn.commit()
        resultado = resolver_costo(conn, product_id=pid, fecha_escenario=dia, now_utc=NOW)
        assert resultado.motivo == "costo_no_vigente"
        assert resultado.costo is None


@_skip_db
def test_resolver_costo_desactualizado_sin_corrida_del_dia():
    with db_insumos() as conn:
        pid, _ = _sembrar_listing(conn)
        dia = date(2026, 9, 8)
        ayer = date(2026, 9, 7)
        run_id = _sembrar_corrida_costos(conn, ayer)
        conn.execute(
            "INSERT INTO sku_cost (product_id, cost_amount, cost_currency, includes_tax,"
            " valid_from, ingest_run_id)"
            " VALUES (%s, 40.0000, 'MXN', false, %s, %s)",
            (pid, dia, run_id),
        )
        conn.commit()
        resultado = resolver_costo(conn, product_id=pid, fecha_escenario=dia, now_utc=NOW)
        assert resultado.motivo == "costo_desactualizado"
        assert resultado.costo is None


def _fila_costo(
    *,
    sku_cost_id: int,
    amount: Decimal,
    valid_from: date,
    valid_to: date | None,
    ingest_run_id: int,
) -> tuple:
    return (
        sku_cost_id,
        amount,
        "MXN",
        False,
        valid_from,
        valid_to,
        ingest_run_id,
    )


def _corrida_validacion(dia: date, run_id: int = 99) -> tuple[int, datetime]:
    return run_id, datetime(dia.year, dia.month, dia.day, 8, 20, tzinfo=UTC)


def test_evaluar_costo_ambiguo():
    """El EXCLUDE de sku_cost impide dos vigencias solapadas en integracion."""
    dia = date(2026, 9, 8)
    filas = [
        _fila_costo(
            sku_cost_id=1,
            amount=Decimal("40.0000"),
            valid_from=dia,
            valid_to=None,
            ingest_run_id=10,
        ),
        _fila_costo(
            sku_cost_id=2,
            amount=Decimal("41.0000"),
            valid_from=dia,
            valid_to=None,
            ingest_run_id=11,
        ),
    ]
    resultado = evaluar_costo_candidatos(
        filas,
        producto_tiene_alguna=True,
        fecha_escenario=dia,
        now_utc=NOW,
        corrida_validacion=_corrida_validacion(dia),
    )
    assert resultado.motivo == "costo_ambiguo"
    assert resultado.costo is None


def test_evaluar_costo_no_vigente_con_filas_del_producto():
    dia = date(2026, 9, 8)
    filas = [
        _fila_costo(
            sku_cost_id=1,
            amount=Decimal("40.0000"),
            valid_from=date(2026, 1, 1),
            valid_to=date(2026, 6, 1),
            ingest_run_id=10,
        )
    ]
    resultado = evaluar_costo_candidatos(
        filas,
        producto_tiene_alguna=True,
        fecha_escenario=dia,
        now_utc=NOW,
        corrida_validacion=_corrida_validacion(dia),
    )
    assert resultado.motivo == "costo_no_vigente"


def test_evaluar_costo_desactualizado_sin_corrida_global():
    dia = date(2026, 9, 8)
    filas = [
        _fila_costo(
            sku_cost_id=1,
            amount=Decimal("40.0000"),
            valid_from=dia,
            valid_to=None,
            ingest_run_id=10,
        )
    ]
    resultado = evaluar_costo_candidatos(
        filas,
        producto_tiene_alguna=True,
        fecha_escenario=dia,
        now_utc=NOW,
        corrida_validacion=None,
    )
    assert resultado.motivo == "costo_desactualizado"


def test_evaluar_costo_acepta_con_corrida_global():
    dia = date(2026, 9, 8)
    ayer = date(2026, 9, 7)
    filas = [
        _fila_costo(
            sku_cost_id=1,
            amount=Decimal("40.0000"),
            valid_from=ayer,
            valid_to=None,
            ingest_run_id=10,
        )
    ]
    corrida = _corrida_validacion(dia, run_id=20)
    resultado = evaluar_costo_candidatos(
        filas,
        producto_tiene_alguna=True,
        fecha_escenario=dia,
        now_utc=NOW,
        corrida_validacion=corrida,
    )
    assert resultado.motivo is None
    assert resultado.costo is not None
    assert resultado.costo.ingest_run_id == 10
    assert resultado.costo.validation_run_id == 20
    assert resultado.costo.validated_at == corrida[1]


@_skip_db
def test_resolver_costo_ausente():
    with db_insumos() as conn:
        pid, _ = _sembrar_listing(conn)
        dia = date(2026, 9, 8)
        _sembrar_corrida_costos(conn, dia)
        conn.commit()
        resultado = resolver_costo(conn, product_id=pid, fecha_escenario=dia, now_utc=NOW)
        assert resultado.motivo == "costo_ausente"
        assert resultado.costo is None


@_skip_db
def test_resolver_costo_desactualizado_corrida_mismo_dia_sin_terminar():
    """Corrida del dia UTC pero finished_at posterior a now_utc -> costo_desactualizado."""
    with db_insumos() as conn:
        pid, _ = _sembrar_listing(conn)
        dia = date(2026, 9, 8)
        run_id = _sembrar_corrida_costos(
            conn,
            dia,
            finished_at=datetime(2026, 9, 8, 13, 0, tzinfo=UTC),
        )
        conn.execute(
            "INSERT INTO sku_cost (product_id, cost_amount, cost_currency, includes_tax,"
            " valid_from, ingest_run_id)"
            " VALUES (%s, 40.0000, 'MXN', false, %s, %s)",
            (pid, dia, run_id),
        )
        conn.commit()
        resultado = resolver_costo(conn, product_id=pid, fecha_escenario=dia, now_utc=NOW)
        assert resultado.motivo == "costo_desactualizado"
        assert resultado.costo is None


@_skip_db
def test_resolver_costo_desactualizado_corrida_fallida():
    with db_insumos() as conn:
        pid, _ = _sembrar_listing(conn)
        dia = date(2026, 9, 8)
        run_id = _sembrar_corrida_costos(conn, dia, ok=False)
        conn.execute(
            "INSERT INTO sku_cost (product_id, cost_amount, cost_currency, includes_tax,"
            " valid_from, ingest_run_id)"
            " VALUES (%s, 40.0000, 'MXN', false, %s, %s)",
            (pid, dia, run_id),
        )
        conn.commit()
        resultado = resolver_costo(conn, product_id=pid, fecha_escenario=dia, now_utc=NOW)
        assert resultado.motivo == "costo_desactualizado"
        assert resultado.costo is None


@_skip_db
def test_resolver_costo_desactualizado_corrida_sin_finished_at():
    with db_insumos() as conn:
        pid, _ = _sembrar_listing(conn)
        dia = date(2026, 9, 8)
        run_id = _sembrar_corrida_costos(conn, dia, finished_at=None)
        conn.execute(
            "INSERT INTO sku_cost (product_id, cost_amount, cost_currency, includes_tax,"
            " valid_from, ingest_run_id)"
            " VALUES (%s, 40.0000, 'MXN', false, %s, %s)",
            (pid, dia, run_id),
        )
        conn.commit()
        resultado = resolver_costo(conn, product_id=pid, fecha_escenario=dia, now_utc=NOW)
        assert resultado.motivo == "costo_desactualizado"
        assert resultado.costo is None


@_skip_db
def test_resolver_costo_desactualizado_corrida_global_con_rows_skipped():
    """Una corrida con skips no acredita el costo; una limpia posterior si."""
    with db_insumos() as conn:
        pid, _ = _sembrar_listing(conn)
        ayer = date(2026, 9, 7)
        hoy = date(2026, 9, 8)
        run_ayer = _sembrar_corrida_costos(conn, ayer)
        run_sucia = _sembrar_corrida_costos(
            conn,
            hoy,
            finished_at=datetime(2026, 9, 8, 8, 10, tzinfo=UTC),
            rows_skipped=1,
            skip_reason="1x costo cero o nulo (dato faltante)",
        )
        conn.execute(
            "INSERT INTO sku_cost (product_id, cost_amount, cost_currency, includes_tax,"
            " valid_from, ingest_run_id)"
            " VALUES (%s, 40.0000, 'MXN', false, %s, %s)",
            (pid, ayer, run_ayer),
        )
        conn.commit()
        resultado = resolver_costo(conn, product_id=pid, fecha_escenario=hoy, now_utc=NOW)
        assert resultado.motivo == "costo_desactualizado"
        assert resultado.costo is None

        run_limpia = _sembrar_corrida_costos(
            conn,
            hoy,
            finished_at=datetime(2026, 9, 8, 8, 25, tzinfo=UTC),
            rows_skipped=0,
        )
        conn.commit()
        resultado2 = resolver_costo(conn, product_id=pid, fecha_escenario=hoy, now_utc=NOW)
        assert resultado2.motivo is None
        assert resultado2.costo is not None
        assert resultado2.costo.cost_amount == Decimal("40.0000")
        assert resultado2.costo.ingest_run_id == run_ayer
        assert resultado2.costo.validation_run_id == run_limpia
        assert resultado2.costo.validated_at == datetime(2026, 9, 8, 8, 25, tzinfo=UTC)
        assert run_sucia != run_limpia


@_skip_db
def test_resolver_costo_desactualizado_fuente_distinta():
    with db_insumos() as conn:
        pid, _ = _sembrar_listing(conn)
        dia = date(2026, 9, 8)
        run_id = _sembrar_corrida_costos(conn, dia, source="manual_costo_historico")
        conn.execute(
            "INSERT INTO sku_cost (product_id, cost_amount, cost_currency, includes_tax,"
            " valid_from, ingest_run_id)"
            " VALUES (%s, 40.0000, 'MXN', false, %s, %s)",
            (pid, dia, run_id),
        )
        conn.commit()
        resultado = resolver_costo(conn, product_id=pid, fecha_escenario=dia, now_utc=NOW)
        assert resultado.motivo == "costo_desactualizado"
        assert resultado.costo is None


@_skip_db
def test_resolver_fx_exact_nearest_y_ausente():
    with db_insumos() as conn:
        dia = date(2026, 9, 1)
        started = datetime(2026, 9, 1, 8, 15, tzinfo=UTC)
        finished = datetime(2026, 9, 1, 8, 20, tzinfo=UTC)
        run_id = conn.execute(
            "INSERT INTO ingest_run"
            " (source, started_at, finished_at, ok, rows_written, rows_skipped)"
            " VALUES ('accounting_currency_rates', %s, %s, true, 1, 0) RETURNING id",
            (started, finished),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO fx_rate (rate_date, base_currency, quote_currency, rate, ingest_run_id)"
            " VALUES (%s, 'USD', 'MXN', %s, %s)",
            (dia, Decimal("16.95000000"), run_id),
        )
        conn.commit()

        exact = resolver_fx(conn, fecha=dia, base="USD", quote="MXN")
        assert exact.motivo is None
        assert exact.fx is not None
        assert exact.fx.source == "exact"
        assert exact.fx.rate == Decimal("16.95000000")

        prior = resolver_fx(conn, fecha=dia + timedelta(days=3), base="USD", quote="MXN")
        assert prior.fx is not None
        assert prior.fx.source == "nearest_prior"
        assert prior.fx.rate_date == dia

        lejos = resolver_fx(conn, fecha=dia + timedelta(days=10), base="USD", quote="MXN")
        assert lejos.motivo == "fx_ausente"
        assert lejos.fx is None


@_skip_db
def test_resolver_fx_misma_moneda_no_inventa_tasa():
    with db_insumos() as conn:
        resultado = resolver_fx(conn, fecha=date(2026, 9, 8), base="MXN", quote="MXN")
        assert resultado.motivo is None
        assert resultado.fx is not None
        assert resultado.fx.source == "misma_moneda"
        assert resultado.fx.rate is None
        assert conn.execute("SELECT count(*) FROM fx_rate").fetchone()[0] == 0
