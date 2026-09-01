"""v_contribucion_entidad y ola fail-loud D6 (ORBIT 06 · 1.2).

TDD contra docs/MARGEN-ENTIDAD.md (SELLADO). Skip sin Postgres/DSN.
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
    conn.execute(SQL)
    conn.execute(SQL2)
    conn.execute(SQL3)
    conn.execute(SQL4)
    conn.execute(SQL5)
    conn.execute(SQL6)


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
    name = f"orbit_contrib_test_{socket.gethostname().lower()}_{os.getpid()}"
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


def _semilla_mx_completa(conn):
    """Keyword MX con catalogo completo, mezcla ledger y serie limpia."""
    rid = _run_id(conn)
    d_from, d_to = _ventana_madura()
    dia = d_to - timedelta(days=5)

    pid = conn.execute(
        "INSERT INTO product (odoo_sku, name) VALUES ('SKU-MX-1', 'p') RETURNING id"
    ).fetchone()[0]
    lid = conn.execute(
        # listing_price (250) DISTINTO del neto realizado del ledger (100) a
        # PROPOSITO (review del lead): si fueran iguales, una mutacion que
        # usara la vitrina en MX (contra la enmienda D1.mx del sello) pasaria
        # estos tests identicos. Con 250, el ratio via vitrina seria 40/250 y
        # todos los asserts exactos (0.4) revientan.
        "INSERT INTO listing (product_id, platform, external_id, listing_price,"
        " price_currency) VALUES (%s, 'amazon_mx', 'ASIN-MX-1', 250, 'MXN')"
        " RETURNING id",
        (pid,),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO sku_cost (product_id, cost_amount, cost_currency, includes_tax,"
        " valid_from, ingest_run_id)"
        " VALUES (%s, 40, 'MXN', false, %s, %s)",
        (pid, d_from - timedelta(days=1), rid),
    )

    cam = _entidad(conn, "amazon_mx", "campaign", "C-MX-1")
    ag = _entidad(conn, "amazon_mx", "ad_group", "AG-MX-1", cam)
    kw = _entidad(conn, "amazon_mx", "keyword", "KW-MX-1", ag)
    _entidad(conn, "amazon_mx", "product_ad", "PA-MX-1", ag, listing_id=lid)

    conn.execute(
        "INSERT INTO ads_metric_observation (ad_entity_id, metric_date, observed_at,"
        " metric_currency, cost, ad_revenue, revenue_same_sku, ingest_run_id)"
        " VALUES (%s, %s, now(), 'MXN', 10, 100, 60, %s)",
        (kw, dia, rid),
    )
    conn.execute(
        "INSERT INTO ledger_event (platform, kind, event_date, product_id, quantity,"
        " amount, amount_currency, item_price, source_event_id, ingest_run_id)"
        " VALUES ('amazon_mx', 'sale', %s, %s, 1, 116, 'MXN', 100, 'S-MX-1', %s)",
        (dia, pid, rid),
    )
    return {
        "kw": kw,
        "cam": cam,
        "ag": ag,
        "pid": pid,
        "lid": lid,
        "rid": rid,
        "dia": dia,
        "d_from": d_from,
        "d_to": d_to,
    }


def test_contribucion_mx_publica_par_halo(db):
    s = _semilla_mx_completa(db)
    fila = db.execute(
        "SELECT ad_entity_id, contrib_sin_halo, contrib_con_halo, rango_invertido,"
        " no_decisoria, metric_currency, cogs_sin_halo, cogs_con_halo"
        " FROM v_contribucion_entidad WHERE ad_entity_id = %s",
        (s["kw"],),
    ).fetchone()
    assert fila is not None, "fila MX esperada ausente"
    assert fila[1] is not None and fila[2] is not None, "par halo incompleto"
    assert fila[3] is False
    assert fila[4] is True
    assert fila[5] == "MXN"
    # ratio = 40/100 = 0.4; cogs_sin = 60*0.4=24; cogs_con=100*0.4=40
    # contrib_sin = 60-10-24=26; contrib_con=100-10-40=50
    assert fila[6] == Decimal("24.0000") or fila[6] == Decimal("24")
    assert fila[7] == Decimal("40.0000") or fila[7] == Decimal("40")
    assert fila[1] == Decimal("26.0000") or fila[1] == Decimal("26")
    assert fila[2] == Decimal("50.0000") or fila[2] == Decimal("50")


def test_sin_costo_ausente_no_cero(db):
    s = _semilla_mx_completa(db)
    dia2 = s["d_to"] - timedelta(days=3)
    db.execute(
        "INSERT INTO ads_metric_observation (ad_entity_id, metric_date, observed_at,"
        " metric_currency, cost, ad_revenue, revenue_same_sku, ingest_run_id)"
        " VALUES (%s, %s, now(), 'MXN', NULL, 50, 30, %s)",
        (s["kw"], dia2, s["rid"]),
    )
    fila = db.execute(
        "SELECT 1 FROM v_contribucion_entidad WHERE ad_entity_id = %s",
        (s["kw"],),
    ).fetchone()
    assert fila is None, "con cost NULL la entidad debe AUSENTARSE, no salir en cero"
    motivo = db.execute(
        "SELECT motivo FROM v_contribucion_cobertura WHERE ad_entity_id = %s",
        (s["kw"],),
    ).fetchone()
    assert motivo is not None and motivo[0] == "serie_incompleta"


def test_serie_incompleta_sin_par_halo(db):
    s = _semilla_mx_completa(db)
    dia2 = s["d_to"] - timedelta(days=2)
    db.execute(
        "INSERT INTO ads_metric_observation (ad_entity_id, metric_date, observed_at,"
        " metric_currency, cost, ad_revenue, revenue_same_sku, ingest_run_id)"
        " VALUES (%s, %s, now(), 'MXN', 5, 40, NULL, %s)",
        (s["kw"], dia2, s["rid"]),
    )
    assert (
        db.execute(
            "SELECT 1 FROM v_contribucion_entidad WHERE ad_entity_id = %s",
            (s["kw"],),
        ).fetchone()
        is None
    )


def test_sigma_cero_ausente(db):
    rid = _run_id(db)
    d_from, d_to = _ventana_madura()
    dia = d_to - timedelta(days=5)
    pid = db.execute(
        "INSERT INTO product (odoo_sku, name) VALUES ('SKU-SIGMA-0', 'p') RETURNING id"
    ).fetchone()[0]
    lid = db.execute(
        "INSERT INTO listing (product_id, platform, external_id, listing_price,"
        " price_currency) VALUES (%s, 'amazon_mx', 'ASIN-SIGMA-0', 100, 'MXN')"
        " RETURNING id",
        (pid,),
    ).fetchone()[0]
    db.execute(
        "INSERT INTO sku_cost (product_id, cost_amount, cost_currency, includes_tax,"
        " valid_from, ingest_run_id)"
        " VALUES (%s, 40, 'MXN', false, %s, %s)",
        (pid, d_from - timedelta(days=1), rid),
    )
    cam = _entidad(db, "amazon_mx", "campaign", "C-SIGMA-0")
    ag = _entidad(db, "amazon_mx", "ad_group", "AG-SIGMA-0", cam)
    kw = _entidad(db, "amazon_mx", "keyword", "KW-SIGMA-0", ag)
    _entidad(db, "amazon_mx", "product_ad", "PA-SIGMA-0", ag, listing_id=lid)
    db.execute(
        "INSERT INTO ads_metric_observation (ad_entity_id, metric_date, observed_at,"
        " metric_currency, cost, ad_revenue, revenue_same_sku, ingest_run_id)"
        " VALUES (%s, %s, now(), 'MXN', 10, 100, 60, %s)",
        (kw, dia, rid),
    )
    assert (
        db.execute("SELECT 1 FROM v_contribucion_entidad WHERE ad_entity_id = %s", (kw,)).fetchone()
        is None
    )
    motivo = db.execute(
        "SELECT motivo FROM v_contribucion_cobertura WHERE ad_entity_id = %s", (kw,)
    ).fetchone()
    assert motivo is not None and motivo[0] == "sin_mezcla_ledger"


def test_catalogo_parcial_ausente(db):
    s = _semilla_mx_completa(db)
    # Segundo product_ad vivo SIN listing → cobertura < 100%.
    _entidad(db, "amazon_mx", "product_ad", "PA-MX-HUECO", s["ag"], listing_id=None)
    assert (
        db.execute(
            "SELECT 1 FROM v_contribucion_entidad WHERE ad_entity_id = %s",
            (s["kw"],),
        ).fetchone()
        is None
    )
    motivo = db.execute(
        "SELECT motivo FROM v_contribucion_cobertura WHERE ad_entity_id = %s",
        (s["kw"],),
    ).fetchone()
    assert motivo is not None and motivo[0] == "catalogo_parcial"


def test_rango_invertido_publicado(db):
    rid = _run_id(db)
    d_from, d_to = _ventana_madura()
    dia = d_to - timedelta(days=5)
    pid = db.execute(
        "INSERT INTO product (odoo_sku, name) VALUES ('SKU-INV', 'p') RETURNING id"
    ).fetchone()[0]
    lid = db.execute(
        "INSERT INTO listing (product_id, platform, external_id, listing_price,"
        " price_currency) VALUES (%s, 'amazon_mx', 'ASIN-INV', 100, 'MXN')"
        " RETURNING id",
        (pid,),
    ).fetchone()[0]
    db.execute(
        "INSERT INTO sku_cost (product_id, cost_amount, cost_currency, includes_tax,"
        " valid_from, ingest_run_id)"
        " VALUES (%s, 200, 'MXN', false, %s, %s)",
        (pid, d_from - timedelta(days=1), rid),
    )
    cam = _entidad(db, "amazon_mx", "campaign", "C-INV")
    ag = _entidad(db, "amazon_mx", "ad_group", "AG-INV", cam)
    kw = _entidad(db, "amazon_mx", "keyword", "KW-INV", ag)
    _entidad(db, "amazon_mx", "product_ad", "PA-INV", ag, listing_id=lid)
    db.execute(
        "INSERT INTO ads_metric_observation (ad_entity_id, metric_date, observed_at,"
        " metric_currency, cost, ad_revenue, revenue_same_sku, ingest_run_id)"
        " VALUES (%s, %s, now(), 'MXN', 10, 100, 60, %s)",
        (kw, dia, rid),
    )
    db.execute(
        "INSERT INTO ledger_event (platform, kind, event_date, product_id, quantity,"
        " amount, amount_currency, item_price, source_event_id, ingest_run_id)"
        " VALUES ('amazon_mx', 'sale', %s, %s, 1, 116, 'MXN', 100, 'S-INV', %s)",
        (dia, pid, rid),
    )
    fila = db.execute(
        "SELECT rango_invertido, cogs_sin_halo IS NOT NULL, cogs_con_halo IS NOT NULL"
        " FROM v_contribucion_entidad WHERE ad_entity_id = %s",
        (kw,),
    ).fetchone()
    assert fila is not None
    assert fila[0] is True
    assert fila[1] and fila[2]


def test_us_nearest_prior_marcada_y_sin_fx_ausente(db):
    rid = _run_id(db)
    d_from, d_to = _ventana_madura()
    dia = d_to - timedelta(days=5)
    dia_sin_fx = d_to - timedelta(days=40)

    pid = db.execute(
        "INSERT INTO product (odoo_sku, name) VALUES ('SKU-US-1', 'p') RETURNING id"
    ).fetchone()[0]
    lid = db.execute(
        "INSERT INTO listing (product_id, platform, external_id, listing_price,"
        " price_currency) VALUES (%s, 'amazon_us', 'ASIN-US-1', 20, 'USD')"
        " RETURNING id",
        (pid,),
    ).fetchone()[0]
    db.execute(
        "INSERT INTO sku_cost (product_id, cost_amount, cost_currency, includes_tax,"
        " valid_from, ingest_run_id)"
        " VALUES (%s, 180, 'MXN', false, %s, %s)",
        (pid, d_from - timedelta(days=1), rid),
    )
    db.execute(
        "INSERT INTO fx_rate (rate_date, base_currency, quote_currency, rate,"
        " ingest_run_id) VALUES (%s, 'USD', 'MXN', 18, %s)",
        (dia - timedelta(days=2), rid),
    )

    cam = _entidad(db, "amazon_us", "campaign", "C-US-1")
    ag = _entidad(db, "amazon_us", "ad_group", "AG-US-1", cam)
    kw = _entidad(db, "amazon_us", "keyword", "KW-US-1", ag)
    kw_sin = _entidad(db, "amazon_us", "keyword", "KW-US-SINFX", ag)
    _entidad(db, "amazon_us", "product_ad", "PA-US-1", ag, listing_id=lid)

    db.execute(
        "INSERT INTO ads_metric_observation (ad_entity_id, metric_date, observed_at,"
        " metric_currency, cost, ad_revenue, revenue_same_sku, ingest_run_id)"
        " VALUES (%s, %s, now(), 'USD', 5, 50, 25, %s)",
        (kw, dia, rid),
    )
    db.execute(
        "INSERT INTO ads_metric_observation (ad_entity_id, metric_date, observed_at,"
        " metric_currency, cost, ad_revenue, revenue_same_sku, ingest_run_id)"
        " VALUES (%s, %s, now(), 'USD', 5, 50, 25, %s)",
        (kw_sin, dia_sin_fx, rid),
    )
    db.execute(
        "INSERT INTO ledger_event (platform, kind, event_date, product_id, quantity,"
        " amount, amount_currency, source_event_id, ingest_run_id)"
        " VALUES ('amazon_us', 'sale', %s, %s, 1, 360, 'MXN', 'S-US-1', %s)",
        (dia, pid, rid),
    )
    db.execute(
        "INSERT INTO ledger_event (platform, kind, event_date, product_id, quantity,"
        " amount, amount_currency, source_event_id, ingest_run_id)"
        " VALUES ('amazon_us', 'sale', %s, %s, 1, 360, 'MXN', 'S-US-2', %s)",
        (dia_sin_fx, pid, rid),
    )

    fila = db.execute(
        "SELECT fx_source, contrib_sin_halo, contrib_con_halo, cogs_sin_halo, cogs_con_halo"
        " FROM v_contribucion_entidad WHERE ad_entity_id = %s",
        (kw,),
    ).fetchone()
    assert fila is not None, "US con nearest_prior debe publicar fila"
    assert fila[0] == "nearest_prior"
    # ratio = (180 MXN / 18) / 20 USD = 0.5; COGS sin=25*0.5, con=50*0.5
    assert fila[3] == Decimal("12.5000")
    assert fila[4] == Decimal("25.0000")
    assert fila[1] == Decimal("7.5000")
    assert fila[2] == Decimal("20.0000")

    assert (
        db.execute(
            "SELECT 1 FROM v_contribucion_entidad WHERE ad_entity_id = %s",
            (kw_sin,),
        ).fetchone()
        is None
    )
    motivo = db.execute(
        "SELECT motivo FROM v_contribucion_cobertura WHERE ad_entity_id = %s",
        (kw_sin,),
    ).fetchone()
    assert motivo is not None and motivo[0] == "sin_fx"


def test_us_listing_mxn_ausente(db):
    """US con listing_price en MXN no publica fila (D5)."""
    rid = _run_id(db)
    d_from, d_to = _ventana_madura()
    dia = d_to - timedelta(days=5)

    pid = db.execute(
        "INSERT INTO product (odoo_sku, name) VALUES ('SKU-US-MXN', 'p') RETURNING id"
    ).fetchone()[0]
    lid = db.execute(
        "INSERT INTO listing (product_id, platform, external_id, listing_price,"
        " price_currency) VALUES (%s, 'amazon_us', 'ASIN-US-MXN', 400, 'MXN')"
        " RETURNING id",
        (pid,),
    ).fetchone()[0]
    db.execute(
        "INSERT INTO sku_cost (product_id, cost_amount, cost_currency, includes_tax,"
        " valid_from, ingest_run_id)"
        " VALUES (%s, 180, 'MXN', false, %s, %s)",
        (pid, d_from - timedelta(days=1), rid),
    )
    db.execute(
        "INSERT INTO fx_rate (rate_date, base_currency, quote_currency, rate,"
        " ingest_run_id) VALUES (%s, 'USD', 'MXN', 18, %s)",
        (dia, rid),
    )
    cam = _entidad(db, "amazon_us", "campaign", "C-US-MXN")
    ag = _entidad(db, "amazon_us", "ad_group", "AG-US-MXN", cam)
    kw = _entidad(db, "amazon_us", "keyword", "KW-US-MXN", ag)
    _entidad(db, "amazon_us", "product_ad", "PA-US-MXN", ag, listing_id=lid)
    db.execute(
        "INSERT INTO ads_metric_observation (ad_entity_id, metric_date, observed_at,"
        " metric_currency, cost, ad_revenue, revenue_same_sku, ingest_run_id)"
        " VALUES (%s, %s, now(), 'USD', 5, 50, 25, %s)",
        (kw, dia, rid),
    )
    db.execute(
        "INSERT INTO ledger_event (platform, kind, event_date, product_id, quantity,"
        " amount, amount_currency, source_event_id, ingest_run_id)"
        " VALUES ('amazon_us', 'sale', %s, %s, 1, 360, 'MXN', 'S-US-MXN', %s)",
        (dia, pid, rid),
    )
    assert (
        db.execute(
            "SELECT 1 FROM v_contribucion_entidad WHERE ad_entity_id = %s",
            (kw,),
        ).fetchone()
        is None
    )


def test_us_dos_product_ads_mismo_producto_publica(db):
    """Mismo product_id en dos product_ads del grupo no vacia la fila US."""
    rid = _run_id(db)
    d_from, d_to = _ventana_madura()
    dia = d_to - timedelta(days=5)

    pid = db.execute(
        "INSERT INTO product (odoo_sku, name) VALUES ('SKU-US-DUP', 'p') RETURNING id"
    ).fetchone()[0]
    lid1 = db.execute(
        "INSERT INTO listing (product_id, platform, external_id, listing_price,"
        " price_currency) VALUES (%s, 'amazon_us', 'ASIN-US-D1', 20, 'USD')"
        " RETURNING id",
        (pid,),
    ).fetchone()[0]
    lid2 = db.execute(
        "INSERT INTO listing (product_id, platform, external_id, listing_price,"
        " price_currency) VALUES (%s, 'amazon_us', 'ASIN-US-D2', 20, 'USD')"
        " RETURNING id",
        (pid,),
    ).fetchone()[0]
    db.execute(
        "INSERT INTO sku_cost (product_id, cost_amount, cost_currency, includes_tax,"
        " valid_from, ingest_run_id)"
        " VALUES (%s, 180, 'MXN', false, %s, %s)",
        (pid, d_from - timedelta(days=1), rid),
    )
    db.execute(
        "INSERT INTO fx_rate (rate_date, base_currency, quote_currency, rate,"
        " ingest_run_id) VALUES (%s, 'USD', 'MXN', 18, %s)",
        (dia, rid),
    )
    cam = _entidad(db, "amazon_us", "campaign", "C-US-DUP")
    ag = _entidad(db, "amazon_us", "ad_group", "AG-US-DUP", cam)
    kw = _entidad(db, "amazon_us", "keyword", "KW-US-DUP", ag)
    _entidad(db, "amazon_us", "product_ad", "PA-US-D1", ag, listing_id=lid1)
    _entidad(db, "amazon_us", "product_ad", "PA-US-D2", ag, listing_id=lid2)
    db.execute(
        "INSERT INTO ads_metric_observation (ad_entity_id, metric_date, observed_at,"
        " metric_currency, cost, ad_revenue, revenue_same_sku, ingest_run_id)"
        " VALUES (%s, %s, now(), 'USD', 5, 50, 25, %s)",
        (kw, dia, rid),
    )
    db.execute(
        "INSERT INTO ledger_event (platform, kind, event_date, product_id, quantity,"
        " amount, amount_currency, source_event_id, ingest_run_id)"
        " VALUES ('amazon_us', 'sale', %s, %s, 1, 360, 'MXN', 'S-US-DUP', %s)",
        (dia, pid, rid),
    )
    fila = db.execute(
        "SELECT contrib_sin_halo, contrib_con_halo FROM v_contribucion_entidad"
        " WHERE ad_entity_id = %s",
        (kw,),
    ).fetchone()
    assert fila is not None
    assert fila[0] == Decimal("7.5000")
    assert fila[1] == Decimal("20.0000")


def test_fx_par_invertido_cero_filas(db):
    rid = _run_id(db)
    dia = date.today() - timedelta(days=20)
    db.execute(
        "INSERT INTO fx_rate (rate_date, base_currency, quote_currency, rate,"
        " ingest_run_id) VALUES (%s, 'USD', 'MXN', 18, %s)",
        (dia, rid),
    )
    ok = db.execute(
        "SELECT count(*) FROM fx_resolve(%s, 'USD'::currency, 'MXN'::currency)",
        (dia,),
    ).fetchone()[0]
    malo = db.execute(
        "SELECT count(*) FROM fx_resolve(%s, 'MXN'::currency, 'USD'::currency)",
        (dia,),
    ).fetchone()[0]
    assert ok == 1
    assert malo == 0, "el par invertido MXN→USD debe devolver cero filas"


def test_vista_solo_usa_v_metric_mature(db):
    s = _semilla_mx_completa(db)
    # Metrica FRESCA (dentro de D-15): no debe entrar a la contribucion.
    fresco = date.today() - timedelta(days=2)
    db.execute(
        "INSERT INTO ads_metric_observation (ad_entity_id, metric_date, observed_at,"
        " metric_currency, cost, ad_revenue, revenue_same_sku, ingest_run_id)"
        " VALUES (%s, %s, now(), 'MXN', 999, 999, 999, %s)",
        (s["kw"], fresco, s["rid"]),
    )
    fila = db.execute(
        "SELECT cost_sum FROM v_contribucion_entidad WHERE ad_entity_id = %s",
        (s["kw"],),
    ).fetchone()
    assert fila is not None
    assert fila[0] == Decimal("10.0000") or fila[0] == Decimal("10")


def test_d8_quantity_distinta_de_uno_falla(db):
    rid = _run_id(db)
    d_from, d_to = _ventana_madura()
    pid = db.execute(
        "INSERT INTO product (odoo_sku, name) VALUES ('SKU-D8', 'p') RETURNING id"
    ).fetchone()[0]
    db.execute(
        "INSERT INTO ledger_event (platform, kind, event_date, product_id, quantity,"
        " amount, amount_currency, source_event_id, ingest_run_id)"
        " VALUES ('amazon_mx', 'sale', %s, %s, 2, 200, 'MXN', 'S-D8', %s)",
        (d_to - timedelta(days=1), pid, rid),
    )
    filas = db.execute(
        """
        SELECT platform, count(*), sum(quantity)
          FROM ledger_event
         WHERE kind = 'sale'
           AND event_date >= (now() AT TIME ZONE 'UTC')::date - 15 - 89
           AND event_date <= (now() AT TIME ZONE 'UTC')::date - 15
           AND quantity IS DISTINCT FROM 1
         GROUP BY platform
        """
    ).fetchall()
    assert filas, "D8: se esperaban filas con quantity != 1 para demostrar el candado"
    # El candado de CI es: esta query debe devolver 0 en prod. Aqui afirmamos
    # que el detector ENCIENDE cuando hay quantity!=1.
    assert any(r[2] != r[1] or r[1] > 0 for r in filas)


def test_d8_candado_verde_con_quantity_uno(db):
    _semilla_mx_completa(db)
    filas = db.execute(
        """
        SELECT platform, count(*), sum(quantity)
          FROM ledger_event
         WHERE kind = 'sale'
           AND event_date >= (now() AT TIME ZONE 'UTC')::date - 15 - 89
           AND event_date <= (now() AT TIME ZONE 'UTC')::date - 15
           AND quantity IS DISTINCT FROM 1
         GROUP BY platform
        """
    ).fetchall()
    assert filas == []


def test_gasto_campaign_sin_contraparte(db):
    s = _semilla_mx_completa(db)
    db.execute(
        "INSERT INTO ads_metric_observation (ad_entity_id, metric_date, observed_at,"
        " metric_currency, cost, ingest_run_id)"
        " VALUES (%s, %s, now(), 'MXN', 15, %s)",
        (s["cam"], s["dia"], s["rid"]),
    )
    mes = s["dia"].replace(day=1)
    fila = db.execute(
        "SELECT gasto_ads, gasto_campaign_sin_contraparte FROM v_tacos"
        " WHERE platform = 'amazon_mx' AND mes = %s",
        (mes,),
    ).fetchone()
    assert fila is not None
    assert fila[0] == Decimal("10.0000") or fila[0] == Decimal("10")
    # campaign 15 - keyword 10 = 5
    assert fila[1] == Decimal("5.0000") or fila[1] == Decimal("5")


def test_desfase_gasto_ads_contado(db):
    s = _semilla_mx_completa(db)
    db.execute(
        "INSERT INTO ledger_event (platform, kind, event_date, amount, amount_currency,"
        " fee_type, source_event_id, ingest_run_id)"
        " VALUES ('amazon_mx', 'fee', %s, -7, 'MXN', 'ads', 'FEE-ADS-1', %s)",
        (s["dia"], s["rid"]),
    )
    fila = db.execute(
        "SELECT gasto_metricas, gasto_ledger_ads, desfase FROM v_desfase_gasto_ads"
        " WHERE platform = 'amazon_mx' AND currency = 'MXN'"
    ).fetchone()
    assert fila is not None
    assert fila[0] == Decimal("10.0000") or fila[0] == Decimal("10")
    assert fila[1] == Decimal("7.0000") or fila[1] == Decimal("7")
    assert fila[2] == Decimal("3.0000") or fila[2] == Decimal("3")


def test_cost_null_anula_tacos_pct(db):
    s = _semilla_mx_completa(db)
    db.execute(
        "INSERT INTO ads_metric_observation (ad_entity_id, metric_date, observed_at,"
        " metric_currency, cost, ingest_run_id)"
        " VALUES (%s, %s, now(), 'MXN', NULL, %s)",
        (s["kw"], s["d_to"] - timedelta(days=1), s["rid"]),
    )
    # Venta del mes para que el CASE llegue a evaluar contadores.
    mes = s["dia"].replace(day=1)
    fila = db.execute(
        "SELECT tacos_pct, filas_gasto_sin_costo FROM v_tacos"
        " WHERE platform = 'amazon_mx' AND mes = %s",
        (mes,),
    ).fetchone()
    assert fila is not None
    assert fila[1] >= 1
    assert fila[0] is None
