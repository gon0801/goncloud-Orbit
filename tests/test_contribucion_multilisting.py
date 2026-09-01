"""Precio multilisting US: MIN marcado (ORBIT 06 · 1.5, enmienda D1.bis).

Sello del dueno 2026-09-01: un producto US con varios listings a precios
distintos NO excluye a la entidad; la contribucion usa el precio MENOR y la
fila sale marcada (precio_min_multilisting). Causa real medida en prod:
productos 120/356 con dos ASINs a precios distintos excluian 273 entidades.

TDD: estos tests nacen ROJOS contra la definicion 0007 (la entidad con
precio inconsistente sale AUSENTE y la columna no existe). Skip sin Postgres.
"""

from __future__ import annotations

import os
import socket
from datetime import date, timedelta
from decimal import Decimal

import pytest
from test_schema import (
    SQL,
    SQL2,
    SQL3,
    SQL4,
    SQL5,
    SQL6,
    SQL7,
    SQL8,
    _hay_postgres_local,
    _test_dsn,
)

_DSN_EXPLICITO = bool(os.environ.get("ORBIT_TEST_DSN"))

pytestmark = pytest.mark.skipif(
    not _DSN_EXPLICITO and not _hay_postgres_local(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)


def _aplicar_esquema(conn):
    conn.execute("SET TIME ZONE 'UTC'")
    for sql in (SQL, SQL2, SQL3, SQL4, SQL5, SQL6, SQL7, SQL8):
        conn.execute(sql)


def _run_id(conn):
    rid = conn.execute("INSERT INTO ingest_run (source) VALUES ('test') RETURNING id").fetchone()[0]
    conn.execute("UPDATE ingest_run SET finished_at = now(), ok = true WHERE id = %s", (rid,))
    return rid


def _entidad(conn, platform, kind, ext, parent=None, listing_id=None):
    match = texto = None
    if kind == "keyword":
        match, texto = "EXACT", ext
    eid = conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id, parent_id,"
        " match_type, keyword_text, listing_id)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (platform, kind, ext, parent, match, texto, listing_id),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO ad_entity_state (ad_entity_id, status, synced_at)"
        " VALUES (%s, 'ENABLED', now())",
        (eid,),
    )
    return eid


def _ventana_madura():
    d_corte = date.today() - timedelta(days=15)
    return d_corte - timedelta(days=89), d_corte


@pytest.fixture
def db():
    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql as pgsql

    dsn = _test_dsn()
    name = f"orbit_multilist_test_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(name)))
        conn = psycopg.connect(dsn, dbname=name, autocommit=True)
        _aplicar_esquema(conn)
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(name))
        )
        admin.close()


def _semilla_us(conn, prefijo, precios):
    """Grupo US con UN producto visto por N listings (un product_ad por
    listing, precios distintos entre ellos) + keyword con serie limpia.

    Numeros elegidos para asserts exactos: cost 180 MXN, fx exact 18 ->
    cost_i = 10 USD; precio MIN esperado = min(precios)."""
    rid = _run_id(conn)
    d_from, d_to = _ventana_madura()
    dia = d_to - timedelta(days=5)

    pid = conn.execute(
        "INSERT INTO product (odoo_sku, name) VALUES (%s, 'p') RETURNING id",
        (f"SKU-{prefijo}",),
    ).fetchone()[0]
    for i, precio in enumerate(precios, start=1):
        conn.execute(
            "INSERT INTO listing (product_id, platform, external_id, listing_price,"
            " price_currency) VALUES (%s, 'amazon_us', %s, %s, 'USD')",
            (pid, f"ASIN-{prefijo}-{i}", precio),
        )
    conn.execute(
        "INSERT INTO sku_cost (product_id, cost_amount, cost_currency, includes_tax,"
        " valid_from, ingest_run_id)"
        " VALUES (%s, 180, 'MXN', false, %s, %s)",
        (pid, d_from - timedelta(days=1), rid),
    )
    conn.execute(
        "INSERT INTO fx_rate (rate_date, base_currency, quote_currency, rate,"
        " ingest_run_id) VALUES (%s, 'USD', 'MXN', 18, %s)",
        (dia, rid),
    )
    cam = _entidad(conn, "amazon_us", "campaign", f"C-{prefijo}")
    ag = _entidad(conn, "amazon_us", "ad_group", f"AG-{prefijo}", cam)
    kw = _entidad(conn, "amazon_us", "keyword", f"KW-{prefijo}", ag)
    for i, _precio in enumerate(precios, start=1):
        lid = conn.execute(
            "SELECT id FROM listing WHERE external_id = %s", (f"ASIN-{prefijo}-{i}",)
        ).fetchone()[0]
        _entidad(conn, "amazon_us", "product_ad", f"PA-{prefijo}-{i}", ag, listing_id=lid)
    conn.execute(
        "INSERT INTO ads_metric_observation (ad_entity_id, metric_date, observed_at,"
        " metric_currency, cost, ad_revenue, revenue_same_sku, ingest_run_id)"
        " VALUES (%s, %s, now(), 'USD', 5, 50, 25, %s)",
        (kw, dia, rid),
    )
    conn.execute(
        "INSERT INTO ledger_event (platform, kind, event_date, product_id, quantity,"
        " amount, amount_currency, source_event_id, ingest_run_id)"
        " VALUES ('amazon_us', 'sale', %s, %s, 1, 360, 'MXN', %s, %s)",
        (dia, pid, f"S-{prefijo}-1", rid),
    )
    return {"kw": kw, "dia": dia}


def test_multilisting_usa_precio_menor_y_marca(db):
    """Sello: dos listings (20 y 25 USD) del MISMO producto -> la entidad
    PUBLICA con price_i = MIN (20): ratio = (180/18)/20 = 0.5 y la fila
    sale marcada. ROJO contra 0007: la entidad sale AUSENTE (HAVING
    COUNT(DISTINCT listing_price) = 1) y la columna no existe."""
    s = _semilla_us(db, "US-ML", (20, 25))

    fila = db.execute(
        "SELECT contrib_sin_halo, contrib_con_halo, fx_source"
        " FROM v_contribucion_entidad WHERE ad_entity_id = %s",
        (s["kw"],),
    ).fetchone()
    assert fila is not None, "multilisting no debe excluir: publica con precio MIN"
    # ratio = 10/20 = 0.5; cogs sin=25*0.5, con=50*0.5
    assert fila[0] == Decimal("7.5000")
    assert fila[1] == Decimal("20.0000")
    assert fila[2] == "exact"

    flag = db.execute(
        "SELECT precio_min_multilisting FROM v_contribucion_entidad WHERE ad_entity_id = %s",
        (s["kw"],),
    ).fetchone()
    assert flag is not None and flag[0] is True, "la fila multilisting sale MARCADA"

    # Y ya no aparece como ausente en la cobertura.
    assert (
        db.execute(
            "SELECT 1 FROM v_contribucion_cobertura WHERE ad_entity_id = %s",
            (s["kw"],),
        ).fetchone()
        is None
    )


def test_precio_unico_sin_marca(db):
    """Control: un solo listing -> publica igual que siempre, flag false."""
    s = _semilla_us(db, "US-1L", (20,))

    fila = db.execute(
        "SELECT contrib_sin_halo, contrib_con_halo, precio_min_multilisting"
        " FROM v_contribucion_entidad WHERE ad_entity_id = %s",
        (s["kw"],),
    ).fetchone()
    assert fila is not None
    assert fila[0] == Decimal("7.5000")
    assert fila[1] == Decimal("20.0000")
    assert fila[2] is False


def test_mx_sin_multilisting_flag_false(db):
    """MX no usa listing_price (D1.mx: neto realizado del ledger): la columna
    existe y sale en false aunque el producto tuviera varios listings."""
    from test_contribucion_entidad import _semilla_mx_completa

    s = _semilla_mx_completa(db)
    fila = db.execute(
        "SELECT precio_min_multilisting FROM v_contribucion_entidad WHERE ad_entity_id = %s",
        (s["kw"],),
    ).fetchone()
    assert fila is not None
    assert fila[0] is False
