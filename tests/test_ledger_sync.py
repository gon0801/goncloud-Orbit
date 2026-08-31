"""Tests de la ingesta del ledger desde contabilidad (ORBIT 06 0.6).

Evidencia TDD (regla 9) — rojo contra codigo anterior (2026-08-31):
  .venv/Scripts/python.exe -m pytest tests/test_ledger_sync.py -q --tb=line
  -> ModuleNotFoundError: No module named 'app.ledger'
  (exit 2, collection error; 1 error in 12.70s)

Fuente: snapshot de `ledger_events` (platform, order_id, event_type,
fee_category, sku, quantity, amount, currency, event_date, dedupe_key,
raw_payload). Decisiones: plans/orbit-06.md §Decisiones de la 0.6 (D1-D8).

(a) UNITARIOS: MAPA_KIND / mapear_destino / plan_eventos / leer_origen /
    main fail-closed. Signo: fee+ NO se voltea. Tres caminos de dedupe.
(b) INTEGRACION: sync_ledger contra Postgres con la migracion; doble
    corrida no-op REAL; ON CONFLICT en los tres indices. Skip sin Postgres.
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from test_schema import SQL, _hay_postgres_local, _test_dsn

from app.ledger import (
    MAPA_KIND,
    SOURCE,
    FilaOrigenLedger,
    leer_origen,
    main,
    mapear_destino,
    plan_eventos,
    sync_ledger,
)

_DSN_EXPLICITO = bool(os.environ.get("ORBIT_TEST_DSN"))

# ---------------------------------------------------------------------------
# Helpers: snapshot de accounting con el esquema REAL de ledger_events
# ---------------------------------------------------------------------------

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


def _payload_sale(
    *,
    asin: str = "B0TESTASIN1",
    currency: str = "MXN",
    item_price: str = "980.00",
    item_tax: str = "135.17",
    qty: int = 1,
) -> str:
    return json.dumps(
        {
            "ASIN": asin,
            "ItemPrice": {"CurrencyCode": currency, "Amount": item_price},
            "ItemTax": {"CurrencyCode": currency, "Amount": item_tax},
            "ShippingPrice": {"CurrencyCode": currency, "Amount": "0.00"},
            "ShippingTax": {"CurrencyCode": currency, "Amount": "0.00"},
            "QuantityShipped": qty,
            "SellerSKU": "LQ-FV4D-DY2I",
        }
    )


def _snapshot(ruta: Path, filas: list[tuple]) -> Path:
    """filas: columnas alineadas al INSERT abajo."""
    con = sqlite3.connect(ruta)
    con.executescript(_DDL_LEDGER)
    con.executemany(
        "INSERT INTO ledger_events ("
        " platform, order_id, event_type, fee_category, sku, quantity,"
        " amount, currency, event_date, cogs_at_sale, raw_payload, dedupe_key"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        filas,
    )
    con.commit()
    con.close()
    return ruta


def _fila(
    *,
    platform: str = "amazon",
    order_id: str = "702-1",
    event_type: str = "sale_gross",
    fee_category: str | None = None,
    sku: str = "LQ-FV4D-DY2I",
    quantity: int | None = 1,
    amount: float | None = 980.0,
    currency: str = "MXN",
    event_date: str = "2025-12-14 20:03:57",
    cogs_at_sale: float | None = 301.5,
    raw_payload: str | None = None,
    dedupe_key: str | None = "amazon|sale|1",
) -> FilaOrigenLedger:
    return FilaOrigenLedger(
        platform=platform,
        order_id=order_id,
        event_type=event_type,
        fee_category=fee_category,
        sku=sku,
        quantity=quantity,
        amount=amount,
        currency=currency,
        event_date=event_date,
        cogs_at_sale=cogs_at_sale,
        raw_payload=raw_payload if raw_payload is not None else _payload_sale(),
        dedupe_key=dedupe_key,
    )


# ---------------------------------------------------------------------------
# (a) MAPA_KIND / mapear_destino — D1 D2 D4 D5 D6 D7 D8
# ---------------------------------------------------------------------------


def test_mapa_kind_cubre_la_tabla_sellada():
    """D2: la tabla es la fuente, no ifs dispersos."""
    assert MAPA_KIND[("sale_gross", None)] == ("sale", None)
    assert mapear_destino(_fila(event_type="sale_gross")).kind == "sale"
    assert mapear_destino(_fila(event_type="sale_gross")).fee_type is None

    ev = mapear_destino(
        _fila(event_type="refund", fee_category="refund", amount=-50.0, dedupe_key="r1")
    )
    assert ev is not None and ev.kind == "refund" and ev.fee_type == "refund"

    wh = mapear_destino(
        _fila(
            event_type="fee",
            fee_category="isr_withheld",
            amount=-100.0,
            order_id="",
            dedupe_key="isr1",
            raw_payload="{}",
        )
    )
    assert wh is not None and wh.kind == "withholding" and wh.fee_type == "isr_withheld"

    ads = mapear_destino(
        _fila(
            event_type="fee",
            fee_category="ads",
            amount=-20.0,
            dedupe_key="ads1",
            raw_payload="{}",
        )
    )
    assert ads is not None and ads.kind == "fee" and ads.fee_type == "ads"


def test_event_type_desconocido_no_se_escribe():
    eventos, skips, _ = plan_eventos([_fila(event_type="mystery", dedupe_key="x")], listings={})
    assert eventos == []
    assert skips["event_type desconocido"] == 1


def test_meli_excluida_y_amazon_renombra_a_mx():
    """D1: meli fuera contado; amazon → amazon_mx."""
    ev = mapear_destino(_fila(platform="amazon"))
    assert ev is not None and ev.platform == "amazon_mx"
    ev_us = mapear_destino(_fila(platform="amazon_us", dedupe_key="us1"))
    assert ev_us is not None and ev_us.platform == "amazon_us"
    _, skips, _ = plan_eventos([_fila(platform="meli", dedupe_key="m1")], listings={})
    assert skips["plataforma meli excluida"] == 1


def test_fee_positivo_no_se_voltea_ni_se_inserta():
    """D4: jamás abs/negacion; viola ledger_convencion_signos → skip contado."""
    fila = _fila(
        event_type="fee",
        fee_category="referral_fee",
        amount=12.5,
        dedupe_key="fee+",
        raw_payload="{}",
    )
    assert mapear_destino(fila) is None
    eventos, skips, _ = plan_eventos([fila], listings={})
    assert eventos == []
    assert skips["viola ledger_convencion_signos"] == 1


def test_sale_cero_o_negativa_viola_signos():
    assert mapear_destino(_fila(amount=0.0, dedupe_key="s0")) is None
    assert mapear_destino(_fila(amount=-10.0, dedupe_key="s-")) is None


def test_event_date_timestamp_colapsa_a_date_sin_fusionar():
    """D7: intradía → DATE; dos dedupe_key el mismo día = dos hechos."""
    a = mapear_destino(_fila(event_date="2026-01-16 06:52:15", dedupe_key="a"))
    b = mapear_destino(_fila(event_date="2026-01-16 18:00:00", dedupe_key="b"))
    assert a is not None and b is not None
    assert a.event_date == date(2026, 1, 16) == b.event_date
    assert a.source_event_id != b.source_event_id


def test_order_id_vacio_a_null_e_isr_entra():
    """D6: order_id '' → NULL; ISR sin orden se planifica (no se excluye)."""
    ev = mapear_destino(
        _fila(
            event_type="fee",
            fee_category="isr_withheld",
            amount=-3459.0,
            order_id="",
            dedupe_key="isr-bulto",
            raw_payload="{}",
        )
    )
    assert ev is not None
    assert ev.order_id is None
    assert ev.kind == "withholding"
    assert ev.source_event_id == "isr-bulto"


def test_product_id_solo_via_asin_nunca_por_sku():
    """D5: ASIN → listing; el texto de SKU no resuelve producto."""
    listings = {("amazon_mx", "B0TESTASIN1"): 42}
    con_asin = mapear_destino(_fila(), listings=listings)
    assert con_asin is not None and con_asin.product_id == 42
    sin_asin = mapear_destino(
        _fila(raw_payload=json.dumps({"SellerSKU": "LQ-FV4D-DY2I"}), dedupe_key="noasin"),
        listings=listings,
    )
    assert sin_asin is not None and sin_asin.product_id is None
    # listings keyed by wrong platform must not match
    wrong = mapear_destino(_fila(), listings={("amazon_us", "B0TESTASIN1"): 99})
    assert wrong is not None and wrong.product_id is None


def test_cogs_at_sale_no_se_ingere_desglose_fiscal_si():
    """D8: cogs ignorado; ItemPrice/ItemTax cuando CurrencyCode coincide."""
    ev = mapear_destino(_fila(cogs_at_sale=999.0))
    assert ev is not None
    assert not hasattr(ev, "cogs_at_sale") or getattr(ev, "cogs_at_sale", None) is None
    assert ev.item_price == Decimal("980.0000")
    assert ev.item_tax == Decimal("135.1700")
    assert ev.quantity == 1
    # moneda del desglose distinta → NULL
    bad = mapear_destino(
        _fila(
            raw_payload=_payload_sale(currency="USD"),
            currency="MXN",
            dedupe_key="div",
        )
    )
    assert bad is not None
    assert bad.item_price is None and bad.item_tax is None


def test_moneda_se_guarda_tal_cual_incluso_amazon_us_mxn():
    ev = mapear_destino(_fila(platform="amazon_us", currency="MXN", dedupe_key="usmxn"))
    assert ev is not None
    assert ev.platform == "amazon_us"
    assert ev.amount_currency == "MXN"


def test_amount_con_mas_de_4_decimales_se_rechaza():
    eventos, skips, _ = plan_eventos(
        [_fila(amount=10.123456, dedupe_key="prec")],
        listings={},
    )
    assert eventos == []
    assert skips["amount con mas de 4 decimales"] == 1


def test_leer_origen_lee_ledger_events(tmp_path):
    snap = _snapshot(
        tmp_path / "led.db",
        [
            (
                "amazon",
                "702-1",
                "sale_gross",
                None,
                "SKU1",
                1,
                100.0,
                "MXN",
                "2026-01-01",
                50.0,
                _payload_sale(),
                "dk1",
            ),
            (
                "meli",
                "M-1",
                "sale_gross",
                None,
                "S",
                1,
                10.0,
                "MXN",
                "2026-01-01",
                None,
                "{}",
                "dk2",
            ),
        ],
    )
    filas = leer_origen(snap)
    assert len(filas) == 2
    assert filas[0].platform == "amazon" and filas[0].dedupe_key == "dk1"
    assert filas[1].platform == "meli"


def test_main_sin_dsn_falla_cerrado(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("ORBIT_DSN_INGEST", raising=False)
    assert main(["--sqlite", str(tmp_path / "x.db")]) == 2
    assert "ORBIT_DSN_INGEST" in capsys.readouterr().err


def test_main_sin_snapshot_falla_cerrado(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("ORBIT_DSN_INGEST", "postgresql://x/y")
    assert main(["--sqlite", str(tmp_path / "no-existe.db")]) == 2
    err = capsys.readouterr().err.lower()
    assert "snapshot" in err or "existe" in err


# ---------------------------------------------------------------------------
# (b) integracion: sync_ledger + tres ON CONFLICT + doble corrida
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _DSN_EXPLICITO and not _hay_postgres_local(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_sync_ledger_ciclo_completo_y_tres_dedupes(tmp_path):
    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql as pgsql

    dsn = _test_dsn()
    db = f"orbit_ledger_test_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        conn.execute(SQL)

        # producto + listing para resolver ASIN
        pid = conn.execute(
            "INSERT INTO product (odoo_sku, name) VALUES ('4207', 'test') RETURNING id"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
            " VALUES (%s, 'amazon_mx', 'B0TESTASIN1', 'LQ-FV4D-DY2I')",
            (pid,),
        )

        snap = _snapshot(
            tmp_path / "led.db",
            [
                # 1) sale con source_event_id → ledger_dedupe_source
                (
                    "amazon",
                    "702-1",
                    "sale_gross",
                    None,
                    "LQ-FV4D-DY2I",
                    1,
                    980.0,
                    "MXN",
                    "2025-12-14 20:03:57",
                    301.5,
                    _payload_sale(),
                    "src-sale-1",
                ),
                # 2) fee withholding ISR sin orden, CON source id
                (
                    "amazon",
                    "",
                    "fee",
                    "isr_withheld",
                    None,
                    None,
                    -3459.0,
                    "MXN",
                    "2026-03-13",
                    None,
                    "{}",
                    "src-isr-1",
                ),
                # 3) fee ads
                (
                    "amazon",
                    "702-2",
                    "fee",
                    "ads",
                    None,
                    None,
                    -40.0,
                    "MXN",
                    "2026-03-01",
                    None,
                    "{}",
                    "src-ads-1",
                ),
                # 4) fee+ (reversa): NO escrita
                (
                    "amazon",
                    "702-3",
                    "fee",
                    "referral_fee",
                    None,
                    None,
                    12.5,
                    "MXN",
                    "2026-03-02",
                    None,
                    "{}",
                    "src-fee-plus",
                ),
                # 5) meli excluida
                (
                    "meli",
                    "M-1",
                    "sale_gross",
                    None,
                    "S",
                    1,
                    10.0,
                    "MXN",
                    "2026-01-01",
                    None,
                    "{}",
                    "src-meli",
                ),
                # 6) sin source_event_id + sin order → ledger_dedupe_sin_orden
                (
                    "amazon",
                    "",
                    "fee",
                    "storage_fee",
                    None,
                    None,
                    -5.0,
                    "MXN",
                    "2026-04-01",
                    None,
                    "{}",
                    None,
                ),
                # 7) sin source_event_id + con order → ledger_dedupe_con_orden
                (
                    "amazon",
                    "702-9",
                    "fee",
                    "fba_fee",
                    None,
                    None,
                    -8.0,
                    "MXN",
                    "2026-04-02",
                    None,
                    "{}",
                    None,
                ),
            ],
        )

        r1 = sync_ledger(conn, snap)
        assert r1.ok is True
        assert SOURCE == "accounting_ledger_events"
        # escritas: sale, isr, ads, sin_orden, con_orden = 5; skips: fee+, meli = 2
        assert r1.rows_written == 5
        assert r1.rows_skipped >= 2
        assert "viola ledger_convencion_signos" in (r1.skip_reason or "")
        assert "plataforma meli excluida" in (r1.skip_reason or "")

        por_kind = dict(
            conn.execute("SELECT kind::text, count(*) FROM ledger_event GROUP BY 1").fetchall()
        )
        assert por_kind.get("sale") == 1
        assert por_kind.get("fee") == 3  # ads + storage + fba
        assert por_kind.get("withholding") == 1

        sale = conn.execute(
            "SELECT platform::text, product_id, quantity, amount, amount_currency::text,"
            " item_price, source_event_id, order_id"
            " FROM ledger_event WHERE kind = 'sale'"
        ).fetchone()
        assert sale[0] == "amazon_mx"
        assert sale[1] == pid
        assert sale[2] == 1
        assert sale[3] == Decimal("980.0000")
        assert sale[4] == "MXN"
        assert sale[5] == Decimal("980.0000")
        assert sale[6] == "src-sale-1"
        assert sale[7] == "702-1"

        isr = conn.execute(
            "SELECT order_id, fee_type, amount FROM ledger_event WHERE kind = 'withholding'"
        ).fetchone()
        assert isr[0] is None and isr[1] == "isr_withheld" and isr[2] == Decimal("-3459.0000")

        # fee+ NO escrito
        assert (
            conn.execute(
                "SELECT count(*) FROM ledger_event WHERE source_event_id = 'src-fee-plus'"
            ).fetchone()[0]
            == 0
        )

        # --- ejercitar los tres indices: re-insertar las mismas filas ---
        conteo_antes = conn.execute("SELECT count(*) FROM ledger_event").fetchone()[0]
        r2 = sync_ledger(conn, snap)
        assert r2.ok is True
        assert r2.rows_written == 0
        assert "conflicto dedupe" in (r2.skip_reason or "")
        assert conn.execute("SELECT count(*) FROM ledger_event").fetchone()[0] == conteo_antes
        assert (
            conn.execute("SELECT count(*) FROM ingest_run WHERE source = %s", (SOURCE,)).fetchone()[
                0
            ]
            == 2
        )

        # fixtures explicitas sin source_event_id: segundo INSERT choca sin_orden / con_orden
        snap_nat = _snapshot(
            tmp_path / "led2.db",
            [
                (
                    "amazon",
                    "",
                    "fee",
                    "storage_fee",
                    None,
                    None,
                    -5.0,
                    "MXN",
                    "2026-04-01",
                    None,
                    "{}",
                    None,
                ),
                (
                    "amazon",
                    "702-9",
                    "fee",
                    "fba_fee",
                    None,
                    None,
                    -8.0,
                    "MXN",
                    "2026-04-02",
                    None,
                    "{}",
                    None,
                ),
            ],
        )
        r3 = sync_ledger(conn, snap_nat)
        assert r3.rows_written == 0
        assert r3.rows_skipped == 2
        assert conn.execute("SELECT count(*) FROM ledger_event").fetchone()[0] == conteo_antes
    finally:
        if conn is not None:
            conn.close()
        admin.execute(pgsql.SQL("DROP DATABASE {} WITH (FORCE)").format(pgsql.Identifier(db)))
        admin.close()


@pytest.mark.skipif(
    not _DSN_EXPLICITO and not _hay_postgres_local(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_sync_ledger_persiste_sin_autocommit(tmp_path):
    """Candado del bug listings 35/36: sin SELECT antes de la primera txn."""
    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql as pgsql

    dsn = _test_dsn()
    db = f"orbit_ledger_ac_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db)  # sin autocommit
        conn.execute("SET TIME ZONE 'UTC'")
        conn.execute(SQL)
        conn.commit()

        snap = _snapshot(
            tmp_path / "led.db",
            [
                (
                    "amazon",
                    "702-1",
                    "sale_gross",
                    None,
                    "SKU",
                    1,
                    100.0,
                    "MXN",
                    "2026-01-01",
                    None,
                    _payload_sale(asin="B0NONE"),
                    "src-1",
                ),
            ],
        )
        with conn:
            r = sync_ledger(conn, snap)
        assert r.ok and r.rows_written == 1
        # nueva conexion verifica persistencia
        check = psycopg.connect(dsn, dbname=db, autocommit=True)
        try:
            assert check.execute("SELECT count(*) FROM ledger_event").fetchone()[0] == 1
            assert (
                check.execute("SELECT ok FROM ingest_run WHERE id = %s", (r.run_id,)).fetchone()[0]
                is True
            )
        finally:
            check.close()
    finally:
        if conn is not None:
            conn.close()
        admin.execute(pgsql.SQL("DROP DATABASE {} WITH (FORCE)").format(pgsql.Identifier(db)))
        admin.close()
