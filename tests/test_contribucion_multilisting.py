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
    SQL9,
    SQL10,
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
    for sql in (SQL, SQL2, SQL3, SQL4, SQL5, SQL6, SQL7, SQL8, SQL9, SQL10):
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


def test_contribucion_publica_escala_dinero_4_decimales(db):
    """Regla 4 en la frontera de la vista: las columnas de dinero COMPUTADAS
    (cogs/contrib, que vienen de w_i*(cost_i/price_i)) salen con a lo mas 4
    decimales, como cualquier NUMERIC(14,4) del schema. Bug prod 2026-09-01:
    la division repetida publicaba colas de ~40 digitos en el dashboard
    (ej. -356.843896106237...90338). ROJO contra 0008 (sin ROUND)."""
    s = _semilla_us(db, "US-R3", (30,))  # ratio = 10/30 = 1/3 (decimal periodico)

    fila = db.execute(
        "SELECT contrib_sin_halo, contrib_con_halo, cogs_sin_halo, cogs_con_halo"
        " FROM v_contribucion_entidad WHERE ad_entity_id = %s",
        (s["kw"],),
    ).fetchone()
    assert fila is not None
    for valor in fila:
        assert valor is not None
        assert valor.as_tuple().exponent >= -4, (
            f"{valor} tiene mas de 4 decimales: la vista publica dinero sin la "
            "escala del schema (regla 4)"
        )
    # cogs sin halo = 25/3 = 8.3333 (ROUND), contrib = 25 - 5 - 8.3333
    assert fila[2] == Decimal("8.3333")
    assert fila[0] == Decimal("11.6667")


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


def _producto_us(conn, prefijo, precios, con_venta, rid, d_from, dia):
    """Producto US con N listings (precios dados) y su sku_cost. con_venta
    agrega una venta ledger en la ventana -> el producto tiene peso w_i y
    su precio ENTRA al ratio del grupo; sin venta, su precio no participa."""
    pid = conn.execute(
        "INSERT INTO product (odoo_sku, name) VALUES (%s, 'p') RETURNING id",
        (f"SKU-{prefijo}",),
    ).fetchone()[0]
    lids = []
    for i, precio in enumerate(precios, start=1):
        lids.append(
            conn.execute(
                "INSERT INTO listing (product_id, platform, external_id, listing_price,"
                " price_currency) VALUES (%s, 'amazon_us', %s, %s, 'USD') RETURNING id",
                (pid, f"ASIN-{prefijo}-{i}", precio),
            ).fetchone()[0]
        )
    conn.execute(
        "INSERT INTO sku_cost (product_id, cost_amount, cost_currency, includes_tax,"
        " valid_from, ingest_run_id)"
        " VALUES (%s, 180, 'MXN', false, %s, %s)",
        (pid, d_from - timedelta(days=1), rid),
    )
    if con_venta:
        conn.execute(
            "INSERT INTO ledger_event (platform, kind, event_date, product_id, quantity,"
            " amount, amount_currency, source_event_id, ingest_run_id)"
            " VALUES ('amazon_us', 'sale', %s, %s, 1, 360, 'MXN', %s, %s)",
            (dia, pid, f"S-{prefijo}-1", rid),
        )
    return lids


def test_marca_solo_si_el_min_entro_al_calculo(db):
    """Cross-review 1.5 (claude, hallazgo 1): la marca precio_min_multilisting
    afirma "esta contribucion uso el precio MENOR de un producto multilisting".
    Un producto multilisting SIN ventas en el ledger (w_i NULL) no aporta
    termino al ratio: su MIN nunca entro al calculo y marcarlo MENTIRIA en
    el dashboard y en el digest. ROJO contra 0009: grupo_multilisting marcaba
    por presencia en vivos, sin exigir peso."""
    rid = _run_id(db)
    d_from, d_to = _ventana_madura()
    dia = d_to - timedelta(days=5)
    # Producto A: multilisting (20 y 25 USD) pero SIN ventas -> sin peso.
    lids_a = _producto_us(db, "NP", (20, 25), False, rid, d_from, dia)
    # Producto B: precio unico 20 USD, CON venta -> sostiene el ratio solo.
    lids_b = _producto_us(db, "CP", (20,), True, rid, d_from, dia)
    db.execute(
        "INSERT INTO fx_rate (rate_date, base_currency, quote_currency, rate,"
        " ingest_run_id) VALUES (%s, 'USD', 'MXN', 18, %s)",
        (dia, rid),
    )
    cam = _entidad(db, "amazon_us", "campaign", "C-MARCA")
    ag = _entidad(db, "amazon_us", "ad_group", "AG-MARCA", cam)
    kw = _entidad(db, "amazon_us", "keyword", "KW-MARCA", ag)
    for i, lid in enumerate(lids_a + lids_b):
        _entidad(db, "amazon_us", "product_ad", f"PA-MARCA-{i}", ag, listing_id=lid)
    db.execute(
        "INSERT INTO ads_metric_observation (ad_entity_id, metric_date, observed_at,"
        " metric_currency, cost, ad_revenue, revenue_same_sku, ingest_run_id)"
        " VALUES (%s, %s, now(), 'USD', 5, 50, 25, %s)",
        (kw, dia, rid),
    )

    fila = db.execute(
        "SELECT contrib_sin_halo, precio_min_multilisting"
        " FROM v_contribucion_entidad WHERE ad_entity_id = %s",
        (kw,),
    ).fetchone()
    assert fila is not None
    # Solo B aporta: ratio = (180/18)/20 = 0.5; contrib = 25 - 5 - 25*0.5.
    assert fila[0] == Decimal("7.5000")
    assert fila[1] is False, (
        "el producto multilisting no tiene peso (sin ventas): su MIN no entro "
        "al calculo y la marca afirmaria que si"
    )


def test_contrib_reconcilia_con_las_columnas_publicadas(db):
    """Cross-review 1.5 (claude, hallazgo 3): las columnas publicadas deben
    cuadrar entre si — contrib = revenue - cost - cogs con los valores
    PUBLICADOS (ya redondeados). 0009 redondeaba cogs pero computaba contrib
    del cogs CRUDO: ROUND(a-b) != a - ROUND(b) y quien cuadre a mano ve un
    descuadre al 4o decimal. Numeros discriminantes (empate exacto en el 5o
    decimal, medido en PG 16: ROUND(x.xxxx5, 4) aleja de cero): ratio =
    10/64 = 0.15625, cogs crudo = 0.0016*0.15625 = 0.00025 -> publica 0.0003;
    contrib crudo = 0.0016 - 0 - 0.00025 = 0.00135 -> 0009 publica 0.0014,
    pero con las columnas PUBLICADAS: 0.0016 - 0 - 0.0003 = 0.0013.
    ROJO contra 0009."""
    rid = _run_id(db)
    d_from, d_to = _ventana_madura()
    dia = d_to - timedelta(days=5)
    lids = _producto_us(db, "REC", (64,), True, rid, d_from, dia)
    db.execute(
        "INSERT INTO fx_rate (rate_date, base_currency, quote_currency, rate,"
        " ingest_run_id) VALUES (%s, 'USD', 'MXN', 18, %s)",
        (dia, rid),
    )
    cam = _entidad(db, "amazon_us", "campaign", "C-REC")
    ag = _entidad(db, "amazon_us", "ad_group", "AG-REC", cam)
    kw = _entidad(db, "amazon_us", "keyword", "KW-REC", ag)
    _entidad(db, "amazon_us", "product_ad", "PA-REC-0", ag, listing_id=lids[0])
    db.execute(
        "INSERT INTO ads_metric_observation (ad_entity_id, metric_date, observed_at,"
        " metric_currency, cost, ad_revenue, revenue_same_sku, ingest_run_id)"
        " VALUES (%s, %s, now(), 'USD', 0, 0.0016, 0.0016, %s)",
        (kw, dia, rid),
    )

    fila = db.execute(
        "SELECT cogs_sin_halo, contrib_sin_halo"
        " FROM v_contribucion_entidad WHERE ad_entity_id = %s",
        (kw,),
    ).fetchone()
    assert fila is not None
    assert fila[0] == Decimal("0.0003")
    # Identidad contable con lo PUBLICADO: 0.0016 - 0 - 0.0003 = 0.0013.
    assert fila[1] == Decimal("0.0013"), (
        "contrib no cuadra con revenue - cost - cogs publicados: se computo "
        "del cogs crudo en vez del redondeado"
    )


def test_marca_viaja_por_el_sql_real_del_dashboard(db):
    """Cross-review 1.5 (grok, hallazgo 2): el rollup de campanas mapea la
    marca por posicion (fila[12]) y ningun test la leia a traves del SQL
    REAL del dashboard (los de UI inyectan el dict; los de Postgres usan
    semilla MX, flag siempre false). Un corrimiento de columnas saldria
    como bool de otra columna — d_to es True en todo rango — y el chip se
    prenderia en TODAS las campanas. El control de precio unico lo caza."""
    _semilla_us(db, "US-UI", (20, 25))
    # Control de precio unico en el MISMO dia (el fx_rate ya lo sembro la
    # semilla multilisting; un segundo insert chocaria por UNIQUE).
    rid = _run_id(db)
    d_from, _d_to = _ventana_madura()
    dia = date.today() - timedelta(days=20)  # mismo dia que _semilla_us
    lids = _producto_us(db, "UC", (20,), True, rid, d_from, dia)
    cam_1l = _entidad(db, "amazon_us", "campaign", "C-US-UC")
    ag = _entidad(db, "amazon_us", "ad_group", "AG-US-UC", cam_1l)
    kw = _entidad(db, "amazon_us", "keyword", "KW-US-UC", ag)
    _entidad(db, "amazon_us", "product_ad", "PA-US-UC-0", ag, listing_id=lids[0])
    db.execute(
        "INSERT INTO ads_metric_observation (ad_entity_id, metric_date, observed_at,"
        " metric_currency, cost, ad_revenue, revenue_same_sku, ingest_run_id)"
        " VALUES (%s, %s, now(), 'USD', 5, 50, 25, %s)",
        (kw, dia, rid),
    )
    from app.dashboard_contribucion import _contribucion_plataforma

    filas = {f["ad_entity_id"]: f for f in _contribucion_plataforma(db, "amazon_us")["filas"]}
    cam_ml = db.execute("SELECT id FROM ad_entity WHERE external_id = 'C-US-UI'").fetchone()[0]
    assert filas[cam_ml]["precio_min_multilisting"] is True
    assert filas[cam_1l]["precio_min_multilisting"] is False
