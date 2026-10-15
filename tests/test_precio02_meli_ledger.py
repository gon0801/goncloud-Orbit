"""Ledger de Mercado Libre (REPRICING 02 M.2).

`meli` entra a `ledger_event` (adshare del excluido a cero), sus ventas
fijan `product_id` por SKU y se atribuyen por `item_id` (`externo_unico`)
o por producto unico (`sku`); sus cargos de envio mapean a `meli_envio`
y lo desconocido cae en `otros`, contado. Amazon no cambia.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest
from test_precio02_base import _db
from test_schema import _postgres_obligatorio_ausente

from app.ledger import main as ledger_main
from app.ledger import sync_ledger
from app.meli.ledger import AtribuidorMeli, atribuidor_meli
from app.precio.envio import CargoEnvio, clasificar_cargo, costo_de_orden
from app.precio.tipos import Importe, MonedasMezcladas

_skip_sin_pg = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)

HOY = date(2026, 10, 1)
DIA = date(2026, 9, 1)

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


def _fila_meli(
    con: sqlite3.Connection,
    *,
    evento: str,
    categoria: str | None,
    orden: str,
    sku: str | None,
    monto: float,
    dedupe: str,
    item: str | None = "MLM2",
    cantidad: int | None = 1,
) -> None:
    crudo = {"row": ({"item_id": item} if item is not None else {}), "source": "t"}
    con.execute(
        "INSERT INTO ledger_events ("
        " platform, order_id, event_type, fee_category, sku, quantity,"
        " amount, currency, event_date, cogs_at_sale, raw_payload, dedupe_key"
        ") VALUES ('meli', ?, ?, ?, ?, ?, ?, 'MXN',"
        " '2026-09-15 10:00:00', NULL, ?, ?)",
        (orden, evento, categoria, sku, cantidad, monto, json.dumps(crudo), dedupe),
    )


def _snapshot(ruta: Path, filas: list[dict]) -> Path:
    con = sqlite3.connect(ruta)
    con.executescript(_DDL_LEDGER)
    for fila in filas:
        _fila_meli(con, **fila)
    con.commit()
    con.close()
    return ruta


def _producto(conn, sku: str) -> int:
    return conn.execute(
        "INSERT INTO product (odoo_sku, name) VALUES (%s, 'Producto M.2') RETURNING id",
        (sku,),
    ).fetchone()[0]


def _listing_meli(conn, pid: int, ext: str, sku: str, *, con_miembro: bool = True) -> int:
    lid = conn.execute(
        "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
        " VALUES (%s, 'meli', %s, %s) RETURNING id",
        (pid, ext, sku),
    ).fetchone()[0]
    if con_miembro:
        conn.execute(
            "INSERT INTO listing_miembro"
            " (listing_id, platform, variante_ext, seller_sku, product_id, activo, visto_at)"
            " VALUES (%s, 'meli', '', %s, %s, true, '2026-09-15 11:00:00+00')",
            (lid, sku, pid),
        )
    return lid


def _atribuciones(conn) -> list[tuple]:
    return conn.execute(
        "SELECT listing_id, variante_ext, resuelto_por FROM ledger_event_atribucion ORDER BY 1"
    ).fetchall()


def _corre(conn, snap: Path, **kwargs):
    kwargs.setdefault("atribuidores", {"meli": AtribuidorMeli(conn)})
    return sync_ledger(conn, snap, **kwargs)


@_skip_sin_pg
def test_meli_ya_no_se_excluye(tmp_path):
    with _db() as conn:
        snap = _snapshot(
            tmp_path / "led.db",
            [
                {
                    "evento": "sale_gross",
                    "categoria": None,
                    "orden": "200-m2-1",
                    "sku": "SKU-M2-A",
                    "monto": 980.0,
                    "dedupe": "m|sale_gross|200-m2-1",
                }
            ],
        )
        resumen = _corre(conn, snap)
        assert "plataforma meli excluida" not in (resumen.skip_reason or "")
        assert conn.execute("SELECT platform FROM ledger_event").fetchone()[0] == "meli"


@_skip_sin_pg
def test_venta_meli_fija_product_id_por_sku(tmp_path):
    with _db() as conn:
        pid = _producto(conn, "SKU-M2-A")
        snap = _snapshot(
            tmp_path / "led.db",
            [
                {
                    "evento": "sale_gross",
                    "categoria": None,
                    "orden": "200-m2-2",
                    "sku": "SKU-M2-A",
                    "monto": 980.0,
                    "dedupe": "m|sale_gross|200-m2-2",
                }
            ],
        )
        _corre(conn, snap)
        assert conn.execute("SELECT product_id FROM ledger_event").fetchone()[0] == pid


@_skip_sin_pg
def test_venta_meli_sin_sku_mapeado_queda_sin_producto(tmp_path):
    with _db() as conn:
        snap = _snapshot(
            tmp_path / "led.db",
            [
                {
                    "evento": "sale_gross",
                    "categoria": None,
                    "orden": "200-m2-3",
                    "sku": "SKU-NADIE",
                    "monto": 980.0,
                    "dedupe": "m|sale_gross|200-m2-3",
                }
            ],
        )
        resumen = _corre(conn, snap)
        assert conn.execute("SELECT product_id FROM ledger_event").fetchone()[0] is None
        assert _atribuciones(conn) == []
        assert (resumen.ventas_meli_sin_sku, resumen.ventas_meli_sin_producto) == (0, 1)


@_skip_sin_pg
def test_venta_meli_sin_sku_cuenta_hueco(tmp_path):
    with _db() as conn:
        snap = _snapshot(
            tmp_path / "led.db",
            [
                {
                    "evento": "sale_gross",
                    "categoria": None,
                    "orden": "200-m2-11",
                    "sku": None,
                    "monto": 980.0,
                    "dedupe": "m|sale_gross|200-m2-11",
                }
            ],
        )
        resumen = _corre(conn, snap)
        assert conn.execute("SELECT product_id FROM ledger_event").fetchone()[0] is None
        assert (resumen.ventas_meli_sin_sku, resumen.ventas_meli_sin_producto) == (1, 0)


@_skip_sin_pg
def test_venta_meli_mapeada_sin_cantidad_cuenta_hueco(tmp_path):
    with _db() as conn:
        _producto(conn, "SKU-M2-A")
        snap = _snapshot(
            tmp_path / "led.db",
            [
                {
                    "evento": "sale_gross",
                    "categoria": None,
                    "orden": "200-m2-12",
                    "sku": "SKU-M2-A",
                    "monto": 980.0,
                    "dedupe": "m|sale_gross|200-m2-12",
                    "cantidad": None,
                }
            ],
        )
        resumen = _corre(conn, snap)
        assert conn.execute("SELECT product_id FROM ledger_event").fetchone()[0] is None
        assert resumen.ventas_meli_sin_cantidad == 1


@_skip_sin_pg
def test_main_imprime_cobertura_meli(monkeypatch, capsys, tmp_path):
    with _db() as conn:
        snap = _snapshot(
            tmp_path / "led.db",
            [
                {
                    "evento": "sale_gross",
                    "categoria": None,
                    "orden": "200-m2-14",
                    "sku": "SKU-NADIE",
                    "monto": 980.0,
                    "dedupe": "m|sale_gross|200-m2-14",
                }
            ],
        )
        db = conn.execute("SELECT current_database()").fetchone()[0]
        base = os.environ.get("ORBIT_TEST_DSN", "postgresql://orbit:orbit@localhost:5432/postgres")
        partes = urlsplit(base)
        monkeypatch.setenv(
            "ORBIT_DSN_INGEST", urlunsplit((partes.scheme, partes.netloc, "/" + db, "", ""))
        )
        assert ledger_main(["--sqlite", str(snap)]) == 0
        out = capsys.readouterr().out
        assert "meli_sin_sku=0 meli_sin_producto=1 meli_sin_cantidad=0" in out


def test_plan_sin_mapa_productos_cuenta_hueco_fuerte():
    from app.ledger import FilaOrigenLedger, plan_eventos

    fila = FilaOrigenLedger(
        platform="meli",
        order_id="200-m2-13",
        event_type="sale_gross",
        fee_category=None,
        sku="SKU-M2-A",
        quantity=1,
        amount=980.0,
        currency="MXN",
        event_date="2026-09-15 10:00:00",
        cogs_at_sale=None,
        raw_payload="{}",
        dedupe_key="m|sale_gross|200-m2-13",
    )
    eventos, _, huecos = plan_eventos([fila], listings={})
    assert len(eventos) == 1
    assert huecos["venta meli sin producto"] == 1


@_skip_sin_pg
def test_atribuye_por_item_id_externo_unico(tmp_path):
    with _db() as conn:
        pid = _producto(conn, "SKU-M2-A")
        lid = _listing_meli(conn, pid, "MLM2", "SKU-M2-A")
        snap = _snapshot(
            tmp_path / "led.db",
            [
                {
                    "evento": "sale_gross",
                    "categoria": None,
                    "orden": "200-m2-4",
                    "sku": "SKU-M2-A",
                    "monto": 980.0,
                    "dedupe": "m|sale_gross|200-m2-4",
                    "item": "MLM2",
                }
            ],
        )
        _corre(conn, snap)
        assert _atribuciones(conn) == [(lid, None, "externo_unico")]


@_skip_sin_pg
def test_atribuye_por_sku_con_un_solo_listing(tmp_path):
    with _db() as conn:
        pid = _producto(conn, "SKU-M2-A")
        lid = _listing_meli(conn, pid, "MLM9", "SKU-M2-A")
        snap = _snapshot(
            tmp_path / "led.db",
            [
                {
                    "evento": "sale_gross",
                    "categoria": None,
                    "orden": "200-m2-5",
                    "sku": "SKU-M2-A",
                    "monto": 980.0,
                    "dedupe": "m|sale_gross|200-m2-5",
                    "item": None,
                }
            ],
        )
        _corre(conn, snap)
        assert _atribuciones(conn) == [(lid, None, "sku")]


@_skip_sin_pg
def test_dos_listings_no_se_atribuye_y_se_reintenta(tmp_path):
    with _db() as conn:
        pid = _producto(conn, "SKU-M2-A")
        primero = _listing_meli(conn, pid, "MLM9", "SKU-M2-A")
        segundo = _listing_meli(conn, pid, "MLM8", "SKU-M2-A")
        snap = _snapshot(
            tmp_path / "led.db",
            [
                {
                    "evento": "sale_gross",
                    "categoria": None,
                    "orden": "200-m2-6",
                    "sku": "SKU-M2-A",
                    "monto": 980.0,
                    "dedupe": "m|sale_gross|200-m2-6",
                    "item": None,
                }
            ],
        )
        uno = _corre(conn, snap)
        assert _atribuciones(conn) == []
        assert "venta_sin_atribuir" in (uno.skip_reason or "")
        conn.execute("UPDATE listing_miembro SET activo = false WHERE listing_id = %s", (segundo,))
        _corre(conn, snap)
        assert _atribuciones(conn) == [(primero, None, "sku")]


@_skip_sin_pg
def test_registro_por_defecto_atribuye_meli(monkeypatch, tmp_path):
    from app.ledger import _ATRIBUIDORES

    assert _ATRIBUIDORES["meli"] is atribuidor_meli
    with _db() as conn:
        pid = _producto(conn, "SKU-M2-A")
        lid = _listing_meli(conn, pid, "MLM2", "SKU-M2-A")
        snap = _snapshot(
            tmp_path / "led.db",
            [
                {
                    "evento": "sale_gross",
                    "categoria": None,
                    "orden": "200-m2-7",
                    "sku": "SKU-M2-A",
                    "monto": 980.0,
                    "dedupe": "m|sale_gross|200-m2-7",
                    "item": "MLM2",
                }
            ],
        )
        db = conn.execute("SELECT current_database()").fetchone()[0]
        base = os.environ.get("ORBIT_TEST_DSN", "postgresql://orbit:orbit@localhost:5432/postgres")
        partes = urlsplit(base)
        monkeypatch.setenv(
            "ORBIT_DSN_INGEST", urlunsplit((partes.scheme, partes.netloc, "/" + db, "", ""))
        )
        fresco = AtribuidorMeli()
        monkeypatch.setitem(_ATRIBUIDORES, "meli", fresco)
        try:
            sync_ledger(conn, snap)
            assert _atribuciones(conn) == [(lid, None, "externo_unico")]
        finally:
            fresco.close()


@_skip_sin_pg
def test_reversa_positiva_se_salta_contada(tmp_path):
    with _db() as conn:
        snap = _snapshot(
            tmp_path / "led.db",
            [
                {
                    "evento": "fee",
                    "categoria": "referral_fee",
                    "orden": "200-m2-8",
                    "sku": "SKU-M2-A",
                    "monto": 387.86,
                    "dedupe": "m|fee_reversal|200-m2-8",
                }
            ],
        )
        resumen = _corre(conn, snap)
        assert "viola ledger_convencion_signos" in (resumen.skip_reason or "")
        assert conn.execute("SELECT count(*) FROM ledger_event").fetchone()[0] == 0


@_skip_sin_pg
def test_reembolso_meli_sin_categoria_guarda_fee_type_nulo(tmp_path):
    with _db() as conn:
        snap = _snapshot(
            tmp_path / "led.db",
            [
                {
                    "evento": "refund",
                    "categoria": None,
                    "orden": "200-m2-9",
                    "sku": "SKU-M2-A",
                    "monto": -299.0,
                    "dedupe": "m|refund|200-m2-9",
                }
            ],
        )
        _corre(conn, snap)
        assert conn.execute("SELECT kind, fee_type FROM ledger_event").fetchone() == (
            "refund",
            None,
        )


@_skip_sin_pg
def test_refund_meli_mismo_periodo_compensa_venta(tmp_path):
    with _db() as conn:
        pid = _producto(conn, "SKU-M2-A")
        snap = _snapshot(
            tmp_path / "led.db",
            [
                {
                    "evento": "sale_gross",
                    "categoria": None,
                    "orden": "200-m2-22",
                    "sku": "SKU-M2-A",
                    "monto": 980.0,
                    "dedupe": "m|sale_gross|200-m2-22",
                },
                {
                    "evento": "refund",
                    "categoria": None,
                    "orden": "200-m2-22",
                    "sku": "SKU-M2-A",
                    "monto": -980.0,
                    "dedupe": "m|refund|200-m2-22",
                },
            ],
        )
        _corre(conn, snap)
        filas = sorted(conn.execute("SELECT kind, product_id, amount FROM ledger_event").fetchall())
        # M.2: la atribucion es solo-venta (igual que amazon); el refund compensa
        # por order_id en M.3, aqui solo se exige neto cero y kinds correctos.
        assert [(k, p) for k, p, _ in filas] == [("refund", None), ("sale", pid)]
        assert sum(a for _, _, a in filas) == 0


def test_identidad_fee_es_meli_envio():
    assert clasificar_cargo("m|fee|200|SKU", "shipping_fee", "meli") == "meli_envio"
    assert clasificar_cargo("m|fee|200|SKU", "shipping_fee", "amazon_mx") == "otros"
    assert clasificar_cargo("m|fee|200|SKU", "shipping_fee") == "otros"


def test_amazon_no_cambia():
    casos = {
        "amazon|shipping_label|702|x": "shipping_label",
        "a|finance|f|o|s|LabmanLabelPurchase": "labman_label",
        "a|finance|f|o|s|MFNPostageFee": "mfn_postage",
        "a|finance|f|o|s|ShippingHB": "shipping_hb",
        "a|finance|f|o|s|ShippingChargeback": "otros",
        "a|finance|f|o|s|MFNShippingChargeback": "otros",
    }
    for llave, fuente in casos.items():
        assert clasificar_cargo(llave, "shipping_fee", "amazon_mx") == fuente
        assert clasificar_cargo(llave, "shipping_fee") == fuente


def test_cargo_desconocido_cae_en_otros():
    assert clasificar_cargo("m|extrano|200|SKU", "shipping_fee") == "otros"
    assert clasificar_cargo(None, "shipping_fee") == "otros"


def _cargo_meli(monto: str, moneda: str = "MXN") -> CargoEnvio:
    return CargoEnvio(
        orden="200-m2-e", fuente="meli_envio", importe=Importe(Decimal(monto), moneda), dia=DIA
    )


def test_meli_envio_suma_por_orden():
    assert costo_de_orden([_cargo_meli("190")]) == Importe(Decimal("190"), "MXN")
    assert costo_de_orden([_cargo_meli("190"), _cargo_meli("10")]) == Importe(Decimal("200"), "MXN")


def test_meli_envio_moneda_mezclada_falla_fuerte():
    with pytest.raises(MonedasMezcladas):
        costo_de_orden([_cargo_meli("190", "MXN"), _cargo_meli("10", "USD")])


def _run(conn) -> int:
    return conn.execute("INSERT INTO ingest_run (source) VALUES ('m2') RETURNING id").fetchone()[0]


def _venta_directa(conn, run: int, *, orden: str, producto: int | None) -> None:
    conn.execute(
        "INSERT INTO ledger_event (platform, kind, event_date, order_id, product_id,"
        " quantity, amount, amount_currency, source_event_id, ingest_run_id)"
        " VALUES ('meli', 'sale', %s, %s, %s, 1, 980.0, 'MXN', %s, %s)",
        (DIA, orden, producto, f"v|{orden}", run),
    )


def _cargo_directo(conn, run: int, *, orden: str, identidad: str, monto: str) -> None:
    conn.execute(
        "INSERT INTO ledger_event (platform, kind, event_date, order_id, amount,"
        " amount_currency, fee_type, source_event_id, ingest_run_id)"
        " VALUES ('meli', 'fee', %s, %s, %s, 'MXN', 'shipping_fee', %s, %s)",
        (DIA, orden, Decimal(monto), f"m|{identidad}|{orden}", run),
    )


@_skip_sin_pg
def test_cargo_meli_desconocido_se_cuenta_en_otros():
    from app.envio_muestras import refrescar_muestras

    with _db() as conn:
        pid = _producto(conn, "SKU-M2-O")
        _listing_meli(conn, pid, "MLM-O", "SKU-M2-O", con_miembro=False)
        run = _run(conn)
        _venta_directa(conn, run, orden="200-m2-o", producto=pid)
        _cargo_directo(conn, run, orden="200-m2-o", identidad="extrano", monto="-44")
        excl = refrescar_muestras(conn, "meli", "meli", hoy=HOY, ventana_dias=180)
        assert excl.cargos_otros == 1


@_skip_sin_pg
def test_cargo_meli_envio_da_muestra_propia():
    from app.envio_muestras import refrescar_muestras

    with _db() as conn:
        pid = _producto(conn, "SKU-M2-E")
        _listing_meli(conn, pid, "MLM-E", "SKU-M2-E", con_miembro=False)
        run = _run(conn)
        _venta_directa(conn, run, orden="200-m2-e", producto=pid)
        _cargo_directo(conn, run, orden="200-m2-e", identidad="fee", monto="-190")
        excl = refrescar_muestras(conn, "meli", "meli", hoy=HOY, ventana_dias=180)
        assert excl.cargos_otros == 0
        fila = conn.execute(
            "SELECT origen, valor FROM precio_envio_muestra WHERE product_id = %s", (pid,)
        ).fetchone()
        assert fila == ("propio", Decimal("190.0000"))


@_skip_sin_pg
def test_excluida_baja_a_cero_en_origen_realista(tmp_path):
    with _db() as conn:
        snap = _snapshot(
            tmp_path / "led.db",
            [
                {
                    "evento": "sale_gross",
                    "categoria": None,
                    "orden": "200-m2-10",
                    "sku": "SKU-M2-A",
                    "monto": 110.0,
                    "dedupe": "m|sale_gross|200-m2-10",
                },
                {
                    "evento": "fee",
                    "categoria": "shipping_fee",
                    "orden": "200-m2-10",
                    "sku": "SKU-M2-A",
                    "monto": -95.0,
                    "dedupe": "m|fee|200-m2-10|s",
                },
                {
                    "evento": "fee",
                    "categoria": "isr_withheld",
                    "orden": "200-m2-10",
                    "sku": "SKU-M2-A",
                    "monto": -53.64,
                    "dedupe": "m|fee|200-m2-10|i",
                },
            ],
        )
        resumen = _corre(conn, snap)
        assert "plataforma meli excluida" not in (resumen.skip_reason or "")
        assert conn.execute("SELECT count(*) FROM ledger_event").fetchone()[0] == 3
        assert resumen.por_kind == {"sale": 1, "fee": 1, "withholding": 1}
