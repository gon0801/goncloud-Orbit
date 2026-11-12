"""Atribucion de ventas del ledger a su publicacion (REPRICING 02 corte 0.a).

La ingesta despacha por plataforma con el protocolo `Atribuidor`: cuando
devuelve una atribucion la escribe en `ledger_event_atribucion` (append-only,
ON CONFLICT DO NOTHING); cuando devuelve None no escribe y cuenta
`venta_sin_atribuir` en skip_reason sin sumar a rows_skipped. La ingesta
atribuye toda venta del plan, tambien la que ya estaba en ledger_event.

ARREGLOS 01 Y: la reatribucion de product_id de una venta de Amazon deja su
fila en `ledger_reatribucion`; el resto de los UPDATE sigue rechazado.
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import psycopg
import pytest
from test_precio02_base import _db, _dsn_base, _dsn_de_db, _producto_listing
from test_schema import _postgres_obligatorio_ausente

from app.ledger import sync_ledger
from app.ledger_atribucion import Atribucion

RAIZ = Path(__file__).resolve().parents[1]

_skip_sin_pg = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)

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


def _payload_sale() -> str:
    return json.dumps(
        {
            "ASIN": "B0R02ATRIB",
            "ItemPrice": {"CurrencyCode": "MXN", "Amount": "980.00"},
            "ItemTax": {"CurrencyCode": "MXN", "Amount": "135.17"},
            "ShippingPrice": {"CurrencyCode": "MXN", "Amount": "0.00"},
            "ShippingTax": {"CurrencyCode": "MXN", "Amount": "0.00"},
            "QuantityShipped": 1,
            "SellerSKU": "SKU-R02-ATR",
        }
    )


def _snapshot(ruta: Path, dedupe: str) -> Path:
    con = sqlite3.connect(ruta)
    con.executescript(_DDL_LEDGER)
    con.execute(
        "INSERT INTO ledger_events ("
        " platform, order_id, event_type, fee_category, sku, quantity,"
        " amount, currency, event_date, cogs_at_sale, raw_payload, dedupe_key"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "amazon",
            "702-atrib",
            "sale_gross",
            None,
            "SKU-R02-ATR",
            1,
            980.0,
            "MXN",
            "2026-10-01 10:00:00",
            None,
            _payload_sale(),
            dedupe,
        ),
    )
    con.commit()
    con.close()
    return ruta


class _AtribuidorQueDevuelve:
    def __init__(self, listing_id: int) -> None:
        self.listing_id = listing_id

    def atribuir(self, _fila) -> Atribucion:
        return Atribucion(listing_id=self.listing_id, variante=None, resuelto_por="sku")


class _AtribuidorQueNoPuede:
    def atribuir(self, _fila) -> None:
        return None


def _fila_run(conn) -> tuple:
    return conn.execute(
        "SELECT rows_written, rows_skipped, skip_reason FROM ingest_run"
        " WHERE source = 'accounting_ledger_events' ORDER BY id DESC LIMIT 1"
    ).fetchone()


@_skip_sin_pg
def test_atribuye_venta_nueva_y_rellena_la_historia_sin_duplicar(tmp_path):
    with _db() as conn:
        _pid, lid = _producto_listing(conn, sku="SKU-R02-ATR", ext="B0R02ATRIB")
        snap = _snapshot(tmp_path / "led.db", "src-atrib-1")
        r1 = sync_ledger(conn, snap, atribuidores={"amazon_mx": _AtribuidorQueDevuelve(lid)})
        assert r1.rows_written == 1
        filas = conn.execute(
            "SELECT listing_id, variante_ext, resuelto_por FROM ledger_event_atribucion"
        ).fetchall()
        assert filas == [(lid, None, "sku")]
        sync_ledger(conn, snap, atribuidores={"amazon_mx": _AtribuidorQueDevuelve(lid)})
        assert conn.execute("SELECT count(*) FROM ledger_event_atribucion").fetchone()[0] == 1


@_skip_sin_pg
def test_venta_ya_ingerida_sin_atribuidor_se_atribuye_en_la_siguiente_corrida(tmp_path):
    with _db() as conn:
        _pid, lid = _producto_listing(conn, sku="SKU-R02-ATR", ext="B0R02ATRIB")
        snap = _snapshot(tmp_path / "led.db", "src-atrib-2")
        sync_ledger(conn, snap, atribuidores={"amazon_mx": _AtribuidorQueNoPuede()})
        assert conn.execute("SELECT count(*) FROM ledger_event_atribucion").fetchone()[0] == 0
        sync_ledger(conn, snap, atribuidores={"amazon_mx": _AtribuidorQueDevuelve(lid)})
        assert conn.execute("SELECT count(*) FROM ledger_event_atribucion").fetchone()[0] == 1


@_skip_sin_pg
def test_atribuidor_que_no_puede_no_escribe_y_no_suma_rows_skipped(tmp_path):
    with _db() as conn:
        _pid, _lid = _producto_listing(conn, sku="SKU-R02-ATR", ext="B0R02ATRIB")
        snap = _snapshot(tmp_path / "led.db", "src-atrib-3")
        r = sync_ledger(conn, snap, atribuidores={"amazon_mx": _AtribuidorQueNoPuede()})
        escritas, saltadas, motivo = _fila_run(conn)
        assert escritas == 1
        assert saltadas == 0
        assert r.rows_skipped == 0
        assert "venta_sin_atribuir" in (motivo or "")


@contextmanager
def _db_0085():
    """Base temporal con 0001 + la migracion 0085 (trigger y auditoria)."""
    from psycopg import sql as pgsql

    dsn = _dsn_base()
    db = f"ledger_y_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(_dsn_de_db(dsn, db), autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        conn.execute((RAIZ / "migrations" / "0001_initial.sql").read_text(encoding="utf-8"))
        conn.execute(
            (RAIZ / "migrations" / "0085_arreglos01_ledger_reatribucion.sql").read_text(
                encoding="utf-8"
            )
        )
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


@_skip_sin_pg
def test_arreglos01_y_reatribuye_venta_con_rastro_y_sigue_bloqueando(tmp_path):
    """H-H4: si el mapa ASIN -> listing cambia, la venta ya ingerida se mueve
    al producto nuevo y el trigger deja la fila de auditoria. Un UPDATE de
    otro campo y un UPDATE de sin producto a producto siguen rechazados."""
    with _db_0085() as conn:
        pid_antes, lid = _producto_listing(conn, sku="SKU-Y-ANTES", ext="B0R02ATRIB")
        pid_despues = conn.execute(
            "INSERT INTO product (odoo_sku, name) VALUES ('SKU-Y-DESPUES', 'Producto despues')"
            " RETURNING id"
        ).fetchone()[0]
        snap = _snapshot(tmp_path / "led.db", "src-y-1")
        sync_ledger(conn, snap, atribuidores={"amazon_mx": _AtribuidorQueNoPuede()})
        eid, product_id = conn.execute(
            "SELECT id, product_id FROM ledger_event WHERE source_event_id = 'src-y-1'"
        ).fetchone()
        assert product_id == pid_antes

        conn.execute("UPDATE listing SET product_id = %s WHERE id = %s", (pid_despues, lid))
        r = sync_ledger(conn, snap, atribuidores={"amazon_mx": _AtribuidorQueNoPuede()})

        assert (
            conn.execute("SELECT product_id FROM ledger_event WHERE id = %s", (eid,)).fetchone()[0]
            == pid_despues
        )
        assert r.reatribuidas == 1
        assert conn.execute(
            "SELECT ledger_event_id, product_antes, product_despues, ingest_run_id"
            " FROM ledger_reatribucion"
        ).fetchall() == [(eid, pid_antes, pid_despues, r.run_id)]

        r2 = sync_ledger(conn, snap, atribuidores={"amazon_mx": _AtribuidorQueNoPuede()})
        assert r2.reatribuidas == 0
        assert conn.execute("SELECT count(*) FROM ledger_reatribucion").fetchone()[0] == 1

        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute("UPDATE ledger_event SET amount = amount + 1 WHERE id = %s", (eid,))

        conn.execute(
            "INSERT INTO ledger_event"
            " (platform, kind, event_date, order_id, product_id, quantity, amount,"
            " amount_currency, ingest_run_id)"
            " VALUES ('amazon_mx', 'sale', '2026-01-01', 'Y-SIN-PRODUCTO', NULL, 1,"
            " 100.00, 'MXN', %s)",
            (r.run_id,),
        )
        sin_producto = conn.execute(
            "SELECT id FROM ledger_event WHERE order_id = 'Y-SIN-PRODUCTO'"
        ).fetchone()[0]
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute(
                "UPDATE ledger_event SET product_id = %s WHERE id = %s",
                (pid_despues, sin_producto),
            )
