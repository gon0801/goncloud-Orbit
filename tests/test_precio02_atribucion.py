"""Atribuidor de Amazon: ventas del ledger a su publicacion (REPRICING 02 F.1).

Una venta con SKU unico (un solo listing para su producto en la plataforma)
se atribuye por `sku`. Un producto con FBA y FBM se desempata por el canal
de la orden (`canal_orden`): el canal del listing sale de
`spapi_inventario_observation` (tabla FBA por definicion: fila presente =
FBA, ausente = FBM) y el de la orden de `spapi_order_observation`. Lo que
hoy no se puede decidir devuelve None: no se escribe, se cuenta como
`venta_sin_atribuir` y la siguiente ingesta lo reintenta.
"""

from __future__ import annotations

import itertools
import json
import os
import socket
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from test_schema import _postgres_obligatorio_ausente

from app.ledger import sync_ledger
from app.ledger_atribucion import AtribuidorAmazon

RAIZ = Path(__file__).resolve().parents[1]

# ORDEN02 de 0.a mas las dos tablas que lee el atribuidor: ordenes (0030)
# e inventario FBA (0035). Las dos solo dependen de la 0001.
ORDEN_F1 = (
    "0001_initial.sql",
    "0028_estimacion_venta.sql",
    "0030_spapi_orders.sql",
    "0035_spapi_listings_inventario.sql",
    "0039_precio.sql",
    "0047_familias.sql",
    "0053_precio02_canal_meli.sql",
    "0054_precio02_base.sql",
)

_skip_sin_pg = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)

_CONTADOR = itertools.count()


def _dsn_base() -> str:
    return os.environ.get("ORBIT_TEST_DSN", "postgresql://orbit:orbit@localhost:5432/postgres")


def _dsn_de_db(dsn_base: str, db: str) -> str:
    partes = urlsplit(dsn_base)
    return urlunsplit((partes.scheme, partes.netloc, "/" + db, partes.query, partes.fragment))


@contextmanager
def _db():
    from psycopg import sql as pgsql

    dsn = _dsn_base()
    db = f"precio02f1_{socket.gethostname().lower()}_{os.getpid()}_{next(_CONTADOR)}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(_dsn_de_db(dsn, db), autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        for nombre in ORDEN_F1:
            conn.execute((RAIZ / "migrations" / nombre).read_text(encoding="utf-8"))
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


_DDL_LEDGER = """
CREATE TABLE ledger_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT,
    platform TEXT,
    account TEXT,
    order_id TEXT,
    external_ref TEXT,
    event_type TEXT,
    fee_category TEXT,
    expense_category TEXT,
    sku TEXT,
    quantity INTEGER,
    amount REAL,
    currency TEXT,
    event_date TEXT,
    source_import_id INTEGER,
    cogs_at_sale REAL,
    raw_payload TEXT,
    dedupe_key TEXT
);
"""


def _venta(
    con: sqlite3.Connection,
    *,
    orden: str,
    asin: str | None,
    dedupe: str,
    origen: str = "amazon",
    cantidad: int | None = 1,
) -> None:
    carga = {"QuantityShipped": cantidad} if cantidad is not None else {}
    if asin is not None:
        carga["ASIN"] = asin
    con.execute(
        "INSERT INTO ledger_events ("
        " platform, order_id, event_type, fee_category, sku, quantity,"
        " amount, currency, event_date, cogs_at_sale, raw_payload, dedupe_key"
        ") VALUES (?, ?, 'sale_gross', NULL, 'SKU-F1', ?, 980.0, 'MXN',"
        " '2026-10-01 10:00:00', NULL, ?, ?)",
        (origen, orden, cantidad, json.dumps(carga), dedupe),
    )


def _snapshot(ruta: Path, ventas: list[dict]) -> Path:
    con = sqlite3.connect(ruta)
    con.executescript(_DDL_LEDGER)
    for venta in ventas:
        _venta(con, **venta)
    con.commit()
    con.close()
    return ruta


def _producto(conn, *, sku: str = "SKU-F1") -> int:
    return conn.execute(
        "INSERT INTO product (odoo_sku, name) VALUES (%s, 'Producto F.1') RETURNING id",
        (sku,),
    ).fetchone()[0]


def _listing(conn, pid: int, *, plataforma: str, sku: str, ext: str) -> int:
    return conn.execute(
        "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
        " VALUES (%s, %s, %s, %s) RETURNING id",
        (pid, plataforma, ext, sku),
    ).fetchone()[0]


def _inventario(conn, *, plataforma: str, sku: str) -> None:
    conn.execute(
        "INSERT INTO spapi_inventario_observation"
        " (seller_sku, platform, metric_date, observed_at, total_quantity, api_version)"
        " VALUES (%s, %s, '2026-10-01', '2026-10-01 05:00:00+00', 7, 'v1')",
        (sku, plataforma),
    )


def _orden(conn, *, plataforma: str, orden: str, canal: str | None) -> None:
    conn.execute(
        "INSERT INTO spapi_order_observation"
        " (amazon_order_id, platform, marketplace_id, last_updated_time,"
        " fulfillment_channel, observed_at)"
        " VALUES (%s, %s, 'ATVPDKIKX0DER', '2026-10-01 09:00:00+00', %s,"
        " '2026-10-01 10:00:00+00')",
        (orden, plataforma, canal),
    )


def _atribuciones(conn) -> list[tuple]:
    return conn.execute(
        "SELECT listing_id, variante_ext, resuelto_por FROM ledger_event_atribucion ORDER BY 1"
    ).fetchall()


@_skip_sin_pg
def test_venta_con_sku_unico_se_atribuye_por_sku(tmp_path):
    with _db() as conn:
        pid = _producto(conn)
        lid = _listing(conn, pid, plataforma="amazon_mx", sku="AMZ-F1-U", ext="B0F1UNICO1")
        snap = _snapshot(
            tmp_path / "led.db",
            [{"orden": "702-f1-u", "asin": "B0F1UNICO1", "dedupe": "src-f1-u"}],
        )
        sync_ledger(conn, snap, atribuidores={"amazon_mx": AtribuidorAmazon(conn)})
        assert _atribuciones(conn) == [(lid, None, "sku")]


@_skip_sin_pg
def test_producto_fba_fbm_se_desempata_por_canal(tmp_path):
    with _db() as conn:
        pid = _producto(conn, sku="SKU-F1-DOBLE")
        fba = _listing(conn, pid, plataforma="amazon_mx", sku="AMZ-F1-FBA", ext="B0F1FBAAAA")
        fbm = _listing(conn, pid, plataforma="amazon_mx", sku="AMZ-F1-FBM", ext="B0F1FBMMBB")
        _inventario(conn, plataforma="amazon_mx", sku="AMZ-F1-FBA")
        _orden(conn, plataforma="amazon_mx", orden="702-f1-a", canal="AMAZON")
        _orden(conn, plataforma="amazon_mx", orden="702-f1-m", canal="MERCHANT")
        snap = _snapshot(
            tmp_path / "led.db",
            [
                {"orden": "702-f1-a", "asin": "B0F1FBAAAA", "dedupe": "src-f1-a"},
                {"orden": "702-f1-m", "asin": "B0F1FBMMBB", "dedupe": "src-f1-m"},
            ],
        )
        sync_ledger(conn, snap, atribuidores={"amazon_mx": AtribuidorAmazon(conn)})
        assert _atribuciones(conn) == [(fba, None, "canal_orden"), (fbm, None, "canal_orden")]


@_skip_sin_pg
def test_lo_indecidible_no_se_escribe_se_cuenta_y_se_reintenta(tmp_path):
    with _db() as conn:
        pid = _producto(conn, sku="SKU-F1-RE")
        fba = _listing(conn, pid, plataforma="amazon_mx", sku="AMZ-F1-RE1", ext="B0F1REINT1")
        _listing(conn, pid, plataforma="amazon_mx", sku="AMZ-F1-RE2", ext="B0F1REINT2")
        _inventario(conn, plataforma="amazon_mx", sku="AMZ-F1-RE1")
        snap = _snapshot(
            tmp_path / "led.db",
            [{"orden": "702-f1-re", "asin": "B0F1REINT1", "dedupe": "src-f1-re"}],
        )
        primero = sync_ledger(conn, snap, atribuidores={"amazon_mx": AtribuidorAmazon(conn)})
        assert _atribuciones(conn) == []
        assert "venta_sin_atribuir" in (primero.skip_reason or "")
        _orden(conn, plataforma="amazon_mx", orden="702-f1-re", canal="AMAZON")
        sync_ledger(conn, snap, atribuidores={"amazon_mx": AtribuidorAmazon(conn)})
        assert _atribuciones(conn) == [(fba, None, "canal_orden")]


@_skip_sin_pg
def test_primera_corrida_con_atribuidor_llena_la_historia(tmp_path):
    with _db() as conn:
        pid = _producto(conn, sku="SKU-F1-HIST")
        lid = _listing(conn, pid, plataforma="amazon_mx", sku="AMZ-F1-HI", ext="B0F1HIST01")
        snap = _snapshot(
            tmp_path / "led.db",
            [{"orden": "702-f1-h", "asin": "B0F1HIST01", "dedupe": "src-f1-h"}],
        )

        class _NoPuede:
            def atribuir(self, _fila):
                return None

        sync_ledger(conn, snap, atribuidores={"amazon_mx": _NoPuede()})
        assert _atribuciones(conn) == []
        sync_ledger(conn, snap, atribuidores={"amazon_mx": AtribuidorAmazon(conn)})
        assert _atribuciones(conn) == [(lid, None, "sku")]


@_skip_sin_pg
def test_evento_atribuido_no_se_vuelve_a_atribuir(tmp_path):
    with _db() as conn:
        pid = _producto(conn, sku="SKU-F1-DOB")
        lid = _listing(conn, pid, plataforma="amazon_mx", sku="AMZ-F1-DO", ext="B0F1DOBLE1")
        snap = _snapshot(
            tmp_path / "led.db",
            [{"orden": "702-f1-d", "asin": "B0F1DOBLE1", "dedupe": "src-f1-d"}],
        )
        atrib = {"amazon_mx": AtribuidorAmazon(conn)}
        sync_ledger(conn, snap, atribuidores=atrib)
        sync_ledger(conn, snap, atribuidores=atrib)
        assert _atribuciones(conn) == [(lid, None, "sku")]


@_skip_sin_pg
def test_dos_listings_del_mismo_canal_no_se_deciden(tmp_path):
    with _db() as conn:
        pid = _producto(conn, sku="SKU-F1-EMP")
        _listing(conn, pid, plataforma="amazon_mx", sku="AMZ-F1-EM1", ext="B0F1EMPAT1")
        _listing(conn, pid, plataforma="amazon_mx", sku="AMZ-F1-EM2", ext="B0F1EMPAT2")
        _inventario(conn, plataforma="amazon_mx", sku="AMZ-F1-EM1")
        _inventario(conn, plataforma="amazon_mx", sku="AMZ-F1-EM2")
        _orden(conn, plataforma="amazon_mx", orden="702-f1-em", canal="AMAZON")
        snap = _snapshot(
            tmp_path / "led.db",
            [{"orden": "702-f1-em", "asin": "B0F1EMPAT1", "dedupe": "src-f1-em"}],
        )
        resultado = sync_ledger(conn, snap, atribuidores={"amazon_mx": AtribuidorAmazon(conn)})
        assert _atribuciones(conn) == []
        assert "venta_sin_atribuir" in (resultado.skip_reason or "")


@_skip_sin_pg
def test_venta_sin_producto_no_se_atribuye(tmp_path):
    with _db() as conn:
        otro = _producto(conn, sku="SKU-F1-OTRO")
        _listing(conn, otro, plataforma="amazon_mx", sku="AMZ-F1-OT", ext="B0F1OTRO01")
        snap = _snapshot(
            tmp_path / "led.db",
            [{"orden": "702-f1-sp", "asin": None, "dedupe": "src-f1-sp"}],
        )
        resultado = sync_ledger(conn, snap, atribuidores={"amazon_mx": AtribuidorAmazon(conn)})
        assert _atribuciones(conn) == []
        assert "venta_sin_atribuir" in (resultado.skip_reason or "")


@_skip_sin_pg
def test_atribuye_en_us_con_vocabulario_afn_mfn(tmp_path):
    with _db() as conn:
        pid = _producto(conn, sku="SKU-F1-US")
        fba = _listing(conn, pid, plataforma="amazon_us", sku="AMZ-F1-US1", ext="B0F1USAAA1")
        fbm = _listing(conn, pid, plataforma="amazon_us", sku="AMZ-F1-US2", ext="B0F1USBBB2")
        _inventario(conn, plataforma="amazon_us", sku="AMZ-F1-US1")
        _orden(conn, plataforma="amazon_us", orden="702-f1-us1", canal="AFN")
        _orden(conn, plataforma="amazon_us", orden="702-f1-us2", canal="MFN")
        snap = _snapshot(
            tmp_path / "led.db",
            [
                {
                    "orden": "702-f1-us1",
                    "asin": "B0F1USAAA1",
                    "dedupe": "src-f1-us1",
                    "origen": "amazon_us",
                },
                {
                    "orden": "702-f1-us2",
                    "asin": "B0F1USBBB2",
                    "dedupe": "src-f1-us2",
                    "origen": "amazon_us",
                },
            ],
        )
        sync_ledger(
            conn,
            snap,
            atribuidores={"amazon_mx": AtribuidorAmazon(conn), "amazon_us": AtribuidorAmazon(conn)},
        )
        assert _atribuciones(conn) == [(fba, None, "canal_orden"), (fbm, None, "canal_orden")]


def test_mapea_canales_de_orden_conocidos():
    assert AtribuidorAmazon.canal_de_orden("AMAZON") == "fba"
    assert AtribuidorAmazon.canal_de_orden("afn") == "fba"
    assert AtribuidorAmazon.canal_de_orden("MERCHANT") == "fbm"
    assert AtribuidorAmazon.canal_de_orden(" mfn ") == "fbm"
    assert AtribuidorAmazon.canal_de_orden(None) is None
    assert AtribuidorAmazon.canal_de_orden("DESCONOCIDO") is None
