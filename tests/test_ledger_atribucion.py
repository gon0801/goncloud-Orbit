"""Atribucion de ventas del ledger a su publicacion (REPRICING 02 corte 0.a).

La ingesta despacha por plataforma con el protocolo `Atribuidor`: cuando
devuelve una atribucion la escribe en `ledger_event_atribucion` (append-only,
ON CONFLICT DO NOTHING); cuando devuelve None no escribe y cuenta
`venta_sin_atribuir` en skip_reason sin sumar a rows_skipped. La ingesta
atribuye toda venta del plan, tambien la que ya estaba en ledger_event.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from test_precio02_base import _db, _producto_listing
from test_schema import _postgres_obligatorio_ausente

from app.ledger import sync_ledger
from app.ledger_atribucion import Atribucion

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
