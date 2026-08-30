"""Tests de la ingesta de listings desde el bridge (ORBIT 06 0.2, app/listings.py).

Fuente: snapshot de la SQLite del bridge (`amazon_listing_prices` +
`amazon_sku_mapping`), esquema y formas medidos en vivo el 2026-08-30 (ver
plans/orbit-06.md, Decisiones de la 0.2). La trampa sellada: los SKU de Amazon
NO son los de Odoo — la unica union valida es amazon_sku_mapping por
seller_sku; unir por texto esta PROHIBIDO.

(a) UNITARIOS: plan_listings pura (sin mapeo contado, moneda derivada por
    plataforma, precio NULL => ambos NULL, precio <= 0 => NULL contado) y
    leer_origen contra una SQLite temporal con el esquema real del bridge.
(b) INTEGRACION: sync_listings contra Postgres real con la migracion: un SKU
    en dos plataformas => dos filas, listing sin precio escrito con ambos
    NULL, el CHECK rechaza precio sin moneda, doble corrida no-op REAL,
    re-mapeo actualiza product_id contandolo. Skip automatico sin Postgres.
"""

from __future__ import annotations

import os
import socket
import sqlite3
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest
from test_schema import SQL, _hay_postgres_local, _test_dsn

from app.listings import (
    FilaListing,
    leer_origen,
    main,
    plan_listings,
    sync_listings,
)

# ---------------------------------------------------------------------------
# Helpers: snapshot del bridge con el esquema REAL (medido en vivo)
# ---------------------------------------------------------------------------

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
CREATE TABLE amazon_sku_mapping (
    seller_sku TEXT PRIMARY KEY,
    odoo_product_id INTEGER,
    odoo_default_code TEXT,
    asin TEXT,
    parent_asin TEXT,
    keepa_domain INTEGER DEFAULT 11,
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
"""


def _snapshot_bridge(ruta: Path, listings: list[tuple], mapeo: list[tuple]) -> Path:
    """Crea la SQLite del bridge con las filas dadas.

    listings: (seller_sku, asin, marketplace_name, price)
    mapeo: (seller_sku, odoo_default_code)
    """
    con = sqlite3.connect(ruta)
    con.executescript(_DDL_BRIDGE)
    con.executemany(
        "INSERT INTO amazon_listing_prices (seller_sku, asin, marketplace_id,"
        " marketplace_name, price, status) VALUES (?, ?, ?, ?, ?, 'Active')",
        [
            (sku, asin, "A1AM78C64UM0Y8" if mk == "amazon_mx" else "ATVPDKIKX0DER", mk, precio)
            for sku, asin, mk, precio in listings
        ],
    )
    con.executemany(
        "INSERT INTO amazon_sku_mapping (seller_sku, odoo_default_code) VALUES (?, ?)",
        mapeo,
    )
    con.commit()
    con.close()
    return ruta


def _fila(
    seller_sku: str,
    asin: str,
    marketplace: str = "amazon_mx",
    precio: float | None = 199.0,
) -> FilaListing:
    return FilaListing(seller_sku=seller_sku, asin=asin, plataforma=marketplace, precio=precio)


# ---------------------------------------------------------------------------
# (a) plan_listings: pura
# ---------------------------------------------------------------------------


def test_sin_mapeo_no_se_escribe_y_queda_contado():
    """La trampa sellada: 285 publicaciones sin fila en amazon_sku_mapping NO
    se escriben (jamás un producto inventado, regla 3) y quedan contadas."""
    planes, skips, stats = plan_listings([_fila("01-5LZU-V9KZ", "B0SINMAPEO")], mapeo={})
    assert planes == {}
    assert skips == Counter({"seller_sku sin mapeo a SKU de Odoo": 1})
    assert stats["filas"] == 1


def test_mapeo_por_seller_sku_nunca_por_texto():
    """El mismo TEXTO del seller_sku NO puede producir una fila aunque exista
    un producto de Orbit con ese mismo texto: la unión es SOLO el mapeo."""
    # 'ARR-16-DOR-CAM' existe como producto de Orbit, pero si el mapeo no lo
    # trae, la fila no se escribe (los SKU de Amazon no son los de Odoo).
    planes, skips, _ = plan_listings([_fila("ARR-16-DOR-CAM", "B0TEXTO")], mapeo={})
    assert planes == {}
    assert skips == Counter({"seller_sku sin mapeo a SKU de Odoo": 1})


def test_un_sku_en_dos_plataformas_dos_filas():
    """DoD: un SKU de Odoo con listing en MX y US => DOS filas listing (la
    UNIQUE es (platform, external_id), no el producto)."""
    planes, skips, _ = plan_listings(
        [
            _fila("XM-MX-1", "B0DUAL", "amazon_mx", 1500.0),
            _fila("XM-US-1", "B0DUAL", "amazon_us", 89.99),
        ],
        mapeo={"XM-MX-1": "ARR-16-DOR-CAM", "XM-US-1": "ARR-16-DOR-CAM"},
    )
    assert skips == Counter()
    assert len(planes) == 2
    mx = planes[("amazon_mx", "B0DUAL")]
    us = planes[("amazon_us", "B0DUAL")]
    # misma publicacion (ASIN) en dos mercados: dos filas, mismo producto
    assert mx.producto == us.producto == "ARR-16-DOR-CAM"
    # moneda DERIVADA de la plataforma (D3): MX => MXN, US => USD
    assert (mx.precio, mx.moneda) == (Decimal("1500"), "MXN")
    assert (us.precio, us.moneda) == (Decimal("89.99"), "USD")


def test_listing_sin_precio_se_escribe_con_ambos_null():
    """DoD: un listing sin precio (los 133 Incomplete medidos) se escribe
    IGUAL, con precio y moneda ambos NULL (CHECK precio-con-moneda)."""
    planes, skips, _ = plan_listings(
        [_fila("XM-NOPRECIO", "B0NULL", precio=None)],
        mapeo={"XM-NOPRECIO": "SKU-X"},
    )
    assert skips == Counter()
    (plan,) = planes.values()
    assert plan.precio is None and plan.moneda is None


def test_precio_no_positivo_es_dato_faltante():
    """Regla 3: un precio <= 0 no es un precio. El listing se escribe (el MAPA
    manda) pero sin precio, y el caso queda contado."""
    planes, skips, _ = plan_listings(
        [_fila("XM-CERO", "B0CERO", precio=0.0)],
        mapeo={"XM-CERO": "SKU-X"},
    )
    (plan,) = planes.values()
    assert plan.precio is None and plan.moneda is None
    assert skips == Counter({"precio no positivo (dato faltante)": 1})


def test_ruido_binario_del_precio_se_cuantiza():
    planes, _, _ = plan_listings(
        [_fila("XM-RUIDO", "B0RUIDO", precio=554.1800000000001)],
        mapeo={"XM-RUIDO": "SKU-X"},
    )
    (plan,) = planes.values()
    assert plan.precio == Decimal("554.18")


def test_plataforma_fuera_de_dominio_rechazada():
    _, skips, _ = plan_listings(
        [_fila("XM-MLM", "B0MLM", marketplace="mercadolibre")],
        mapeo={"XM-MLM": "SKU-X"},
    )
    assert skips == Counter({"plataforma fuera de dominio (amazon_mx/amazon_us): mercadolibre": 1})


def test_asin_vacio_rechazado():
    _, skips, _ = plan_listings(
        [_fila("XM-SINASIN", "  ")],
        mapeo={"XM-SINASIN": "SKU-X"},
    )
    assert skips == Counter({"listing sin ASIN": 1})


def test_mapeo_a_sku_inexistente_en_productos_rechazado():
    """Defensa: el mapeo trae un odoo_default_code que no existe en product."""
    planes, skips, _ = plan_listings(
        [_fila("XM-FANTASMA", "B0FANTASMA")],
        mapeo={"XM-FANTASMA": "SKU-FANTASMA"},
        productos={"SKU-REAL"},
    )
    assert planes == {}
    assert skips == Counter({"mapeo a SKU sin producto en Orbit": 1})


def test_conflicto_mismo_asin_distinto_producto_rechazado():
    """Defensa (0 casos medidos): dos seller_sku con el MISMO (plataforma,
    asin) mapeando a productos DISTINTOS: no se elige uno arbitrario."""
    planes, skips, _ = plan_listings(
        [
            _fila("XM-A", "B0CHOQUE"),
            _fila("XM-B", "B0CHOQUE"),
        ],
        mapeo={"XM-A": "SKU-A", "XM-B": "SKU-B"},
        productos={"SKU-A", "SKU-B"},
    )
    assert planes == {}
    assert skips == Counter({"ASIN con conflicto de producto": 1})


# ---------------------------------------------------------------------------
# (a) leer_origen: snapshot real del bridge
# ---------------------------------------------------------------------------


def test_leer_origen_lee_listings_y_mapeo(tmp_path):
    snap = _snapshot_bridge(
        tmp_path / "bridge.db",
        [("XM-1", "B0UNO", "amazon_mx", 100.0), ("XM-2", "B0DOS", "amazon_us", None)],
        mapeo=[("XM-1", "ARR-16"), ("XM-2", "NH-CAR")],
    )
    origen = leer_origen(snap)
    assert len(origen.listings) == 2
    assert origen.listings[0].seller_sku == "XM-1"
    assert origen.listings[0].asin == "B0UNO"
    assert origen.listings[0].plataforma == "amazon_mx"
    assert origen.mapeo == {"XM-1": "ARR-16", "XM-2": "NH-CAR"}


# ---------------------------------------------------------------------------
# (a) main: fail-closed de config
# ---------------------------------------------------------------------------


def test_main_sin_dsn_falla_cerrado(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("ORBIT_DSN_INGEST", raising=False)
    snap = _snapshot_bridge(tmp_path / "b.db", [], [])
    assert main(["--sqlite", str(snap)]) == 2
    assert "ORBIT_DSN_INGEST" in capsys.readouterr().err


def test_main_sin_snapshot_falla_cerrado(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("ORBIT_DSN_INGEST", "postgresql://x/y")
    assert main(["--sqlite", str(tmp_path / "no-existe.db")]) == 2
    assert "no-existe.db" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# (b) integracion: sync_listings contra la migracion real
# ---------------------------------------------------------------------------

_DSN_EXPLICITO = bool(os.environ.get("ORBIT_TEST_DSN"))


def _estado_listing(conn) -> list[tuple]:
    return sorted(
        conn.execute(
            "SELECT p.odoo_sku, l.platform, l.external_id, l.seller_sku,"
            " l.listing_price, l.price_currency"
            " FROM listing l JOIN product p ON p.id = l.product_id"
        ).fetchall()
    )


@pytest.mark.skipif(
    not _DSN_EXPLICITO and not _hay_postgres_local(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_sync_listings_ciclo_completo_en_vivo(tmp_path):
    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql as pgsql

    dsn = _test_dsn()
    db = f"orbit_listings_test_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        conn.execute(SQL)  # la migracion entera
        # productos de Orbit (la 0.1 ya debio escribirlos; aqui sembramos)
        for sku in ("ARR-16-DOR-CAM", "NH-CAR-AZU", "SKU-C"):
            conn.execute("INSERT INTO product (odoo_sku, name) VALUES (%s, %s)", (sku, sku))

        # ---------------- corrida 1: el mapa ----------------
        snap1 = _snapshot_bridge(
            tmp_path / "v1.db",
            [
                # un SKU de Odoo en DOS plataformas (el caso del DoD)
                ("XM-MX-1", "B0DUAL", "amazon_mx", 1500.0),
                ("XM-US-1", "B0DUAL", "amazon_us", 89.99),
                # sin precio (Incomplete): se escribe con ambos NULL
                ("XM-NOPRECIO", "B0NULL", "amazon_mx", None),
                # sin mapeo: NO se escribe, contado (la trampa)
                ("01-5LZU-V9KZ", "B0SINMAPEO", "amazon_mx", 250.0),
                # precio cero: listing sin precio + contado
                ("XM-CERO", "B0CERO", "amazon_us", 0.0),
            ],
            mapeo=[
                ("XM-MX-1", "ARR-16-DOR-CAM"),
                ("XM-US-1", "ARR-16-DOR-CAM"),
                ("XM-NOPRECIO", "NH-CAR-AZU"),
                ("XM-CERO", "SKU-C"),
            ],
        )
        r1 = sync_listings(conn, snap1)

        assert r1.ok is True
        assert r1.rows_written == 4  # 4 listings insertados
        assert r1.rows_skipped == 2  # sin mapeo + precio cero
        assert "1x seller_sku sin mapeo a SKU de Odoo" in r1.skip_reason
        assert "1x precio no positivo (dato faltante)" in r1.skip_reason

        run = conn.execute(
            "SELECT source, ok, rows_written, rows_skipped, finished_at"
            " FROM ingest_run WHERE id = %s",
            (r1.run_id,),
        ).fetchone()
        assert run[0] == "bridge_listings"
        assert run[1] is True and run[2] == 4 and run[3] == 2 and run[4] is not None

        estado1 = _estado_listing(conn)
        assert estado1 == [
            ("ARR-16-DOR-CAM", "amazon_mx", "B0DUAL", "XM-MX-1", Decimal("1500"), "MXN"),
            ("ARR-16-DOR-CAM", "amazon_us", "B0DUAL", "XM-US-1", Decimal("89.99"), "USD"),
            ("NH-CAR-AZU", "amazon_mx", "B0NULL", "XM-NOPRECIO", None, None),
            ("SKU-C", "amazon_us", "B0CERO", "XM-CERO", None, None),
        ]
        # el sin-mapeo NO dejo fila
        assert (
            conn.execute(
                "SELECT count(*) FROM listing WHERE external_id = 'B0SINMAPEO'"
            ).fetchone()[0]
            == 0
        )

        # ---------------- el CHECK del esquema: precio sin moneda ----------------
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO listing (product_id, platform, external_id, listing_price)"
                " SELECT id, 'amazon_mx', 'B0CHECK', 100 FROM product WHERE odoo_sku = 'SKU-C'"
            )

        # ---------------- corrida 2: no-op REAL ----------------
        antes = _estado_listing(conn)
        r2 = sync_listings(conn, snap1)
        assert r2.ok is True
        assert r2.rows_written == 0
        assert _estado_listing(conn) == antes
        assert (
            conn.execute(
                "SELECT count(*) FROM ingest_run WHERE source = 'bridge_listings'"
            ).fetchone()[0]
            == 2
        )

        # ---------------- corrida 3: precio cambia + re-mapeo ----------------
        snap3 = _snapshot_bridge(
            tmp_path / "v3.db",
            [
                ("XM-MX-1", "B0DUAL", "amazon_mx", 1600.0),  # precio nuevo
                ("XM-US-1", "B0DUAL", "amazon_us", 89.99),  # igual
                ("XM-NOPRECIO", "B0NULL", "amazon_mx", 749.0),  # precio donde habia NULL
                ("XM-CERO", "B0CERO", "amazon_us", 12.5),
            ],
            mapeo=[
                ("XM-MX-1", "SKU-C"),  # RE-MAPEO a otro producto
                ("XM-US-1", "ARR-16-DOR-CAM"),
                ("XM-NOPRECIO", "NH-CAR-AZU"),
                ("XM-CERO", "SKU-C"),
            ],
        )
        r3 = sync_listings(conn, snap3)
        assert r3.ok is True
        assert r3.rows_written == 3  # precio MX + precio NULL->749 + remapeo
        assert r3.remapeos == 1
        estado3 = _estado_listing(conn)
        assert ("SKU-C", "amazon_mx", "B0DUAL", "XM-MX-1", Decimal("1600"), "MXN") in estado3
        assert (
            "NH-CAR-AZU",
            "amazon_mx",
            "B0NULL",
            "XM-NOPRECIO",
            Decimal("749"),
            "MXN",
        ) in estado3
        assert ("SKU-C", "amazon_us", "B0CERO", "XM-CERO", Decimal("12.5"), "USD") in estado3
        # el re-mapeo dejo de apuntar al producto viejo
        assert (
            "ARR-16-DOR-CAM",
            "amazon_mx",
            "B0DUAL",
            "XM-MX-1",
            Decimal("1600"),
            "MXN",
        ) not in estado3
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


@pytest.mark.skipif(
    not _DSN_EXPLICITO and not _hay_postgres_local(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_sync_listings_persiste_con_conexion_sin_autocommit(tmp_path):
    """Regresion del bug REAL de las corridas 35/36 contra la base viva
    (2026-08-30): con una conexion SIN autocommit (la del CLI via app.db.connect,
    a diferencia de los otros tests que usan autocommit=True), los SELECT
    previos al primer bloque transaccional dejaban una transaccion implicita
    abierta y conn.close() hacia ROLLBACK de TODO el trabajo — la corrida
    imprimia sus contadores y la base quedaba vacia. El pipeline tiene que
    commitear de verdad: cerrar y reabrir tiene que encontrar las filas."""
    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql as pgsql

    dsn = _test_dsn()
    db = f"orbit_listings_nc_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db)  # SIN autocommit, como el CLI
        conn.execute("SET TIME ZONE 'UTC'")
        conn.execute(SQL)
        conn.commit()
        conn.execute("INSERT INTO product (odoo_sku, name) VALUES ('SKU-A', 'SKU-A')")
        conn.commit()
        conn.close()

        snap = _snapshot_bridge(
            tmp_path / "nc.db",
            [("XM-A", "B0UNO", "amazon_mx", 100.0)],
            mapeo=[("XM-A", "SKU-A")],
        )
        # conexion sin autocommit, exactamente como la abre app.db.connect
        conn = psycopg.connect(dsn, dbname=db)
        try:
            resultado = sync_listings(conn, snap)
            assert resultado.ok is True
            assert resultado.listings_insertadas == 1
        finally:
            conn.close()

        # reabrir con autocommit y VERIFICAR que el trabajo sobrevivio al close
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        filas = conn.execute(
            "SELECT p.odoo_sku, l.platform, l.external_id, l.listing_price, l.price_currency"
            " FROM listing l JOIN product p ON p.id = l.product_id"
        ).fetchall()
        assert filas == [("SKU-A", "amazon_mx", "B0UNO", Decimal("100"), "MXN")]
        runs = conn.execute(
            "SELECT ok, rows_written FROM ingest_run WHERE source = 'bridge_listings'"
        ).fetchall()
        assert runs == [(True, 1)]
        conn.close()
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


# --- hallazgos del adversario de la 0.2 (0 altos, 3 medios, 4 bajos) --------


def test_precio_subcentavo_y_fuera_de_rango_no_revientan_la_corrida():
    """Hallazgo 1 (medio): un precio sub-centavo cuantiza a 0.0000 y violaba
    listing_precio_positivo ABORTANDO la corrida entera; uno de 11+ enteros
    desbordaba NUMERIC(14,4). Ambos son dato faltante contado, no abort."""
    planes, skips, _ = plan_listings(
        [
            _fila("XM-SUB", "B0SUB", precio=1e-06),
            _fila("XM-GRANDE", "B0GRANDE", precio=1e11),
        ],
        mapeo={"XM-SUB": "SKU-A", "XM-GRANDE": "SKU-A"},
    )
    assert skips == Counter(
        {
            "precio no positivo (dato faltante)": 1,
            "precio fuera de rango NUMERIC(14,4)": 1,
        }
    )
    # los listings se escriben igual (el MAPA manda), sin precio
    assert planes[("amazon_mx", "B0SUB")].precio is None
    assert planes[("amazon_mx", "B0GRANDE")].precio is None


def test_mapeo_con_espacios_se_normaliza_simetricamente(tmp_path):
    """Hallazgo 2 (medio): la llave del mapeo quedaba cruda mientras el
    listing se stripeaba — un ' XM-1' en ambas tablas se perdia con motivo
    falso ('sin mapeo'), y claves gemelas por espacio escribian el producto
    EQUIVOCADO en silencio. Ahora ambas partes se normalizan, y una colision
    de claves tras el strip deja el SKU sin escribir y contado."""
    snap = _snapshot_bridge(
        tmp_path / "espacios.db",
        [(" XM-1", " B0UNO ", "amazon_mx", 100.0), ("XM-2", "B0DOS", "amazon_mx", 200.0)],
        mapeo=[(" XM-1", " SKU-A "), ("XM-1", "SKU-B"), ("XM-2", " SKU-C ")],
    )
    origen = leer_origen(snap)
    # la clave y el codigo se stripean: el lookup simetrico encuentra el mapa
    assert origen.mapeo.get("XM-2") == "SKU-C"
    planes, skips, _ = plan_listings(
        origen.listings,
        origen.mapeo,
        productos={"SKU-A", "SKU-B", "SKU-C"},
        mapeo_ambiguo=origen.mapeo_ambiguo,
    )
    # XM-1 colisiona tras normalizar ('XM-1' y ' XM-1' con codigos distintos):
    # no se elige arbitrario — el listing queda sin escribir y contado
    assert ("amazon_mx", "B0UNO") not in planes
    assert planes[("amazon_mx", "B0DOS")].producto == "SKU-C"
    assert skips == Counter({"seller_sku con mapeo ambiguo tras normalizar": 1})


def test_asin_con_precios_divergentes_descarta_el_precio():
    """Hallazgo 3 (medio): dos filas del mismo (plataforma, ASIN) y mismo
    producto con precios DISTINTOS elegian la primera alfabeticamente en
    silencio. Ahora el precio divergente se DESCARTA (dato faltante, regla 3)
    y queda contado; igual precio si colapsa."""
    planes, skips, stats = plan_listings(
        [
            _fila("XM-A", "B0MISMO", precio=100.0),
            _fila("XM-B", "B0MISMO", precio=100.0),
            _fila("XM-C", "B0OTRO", precio=300.0),
            _fila("XM-D", "B0OTRO", precio=400.0),
        ],
        mapeo={"XM-A": "SKU-A", "XM-B": "SKU-A", "XM-C": "SKU-A", "XM-D": "SKU-A"},
        productos={"SKU-A"},
    )
    assert skips == Counter({"ASIN con precios divergentes en el origen (precio descartado)": 1})
    assert planes[("amazon_mx", "B0MISMO")].precio == Decimal("100")
    assert planes[("amazon_mx", "B0OTRO")].precio is None
    assert stats["colapsados_por_asin"] == 1
