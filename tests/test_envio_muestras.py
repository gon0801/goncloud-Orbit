"""Job diario de muestras de envio (REPRICING 02 F.2, seccion O).

Lee los cargos `shipping_fee` de la ventana, mapea cada orden a su unico
producto por las ventas de la misma orden y escribe una `precio_envio_muestra`
por producto con publicacion. Solo inserta si la muestra cambio: correrlo dos
veces el mismo dia no escribe nada la segunda.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from test_precio02_base import _db
from test_schema import _postgres_obligatorio_ausente

from app.envio_muestras import main, refrescar_muestras

_skip_sin_pg = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)

HOY = date(2026, 10, 1)
DIA = date(2026, 9, 1)
VIEJO = date(2026, 1, 5)


def _run(conn) -> int:
    return conn.execute("INSERT INTO ingest_run (source) VALUES ('f2') RETURNING id").fetchone()[0]


def _venta(conn, run: int, *, orden: str, producto: int | None, dia: date = DIA) -> None:
    conn.execute(
        "INSERT INTO ledger_event (platform, kind, event_date, order_id, product_id,"
        " quantity, amount, amount_currency, source_event_id, ingest_run_id)"
        " VALUES ('amazon_mx', 'sale', %s, %s, %s, 1, 980.0, 'MXN', %s, %s)",
        (dia, orden, producto, f"v|{orden}|{producto}", run),
    )


def _cargo(
    conn,
    run: int,
    *,
    orden: str,
    fuente: str,
    monto: str,
    dia: date = DIA,
    n: int = 0,
) -> None:
    if fuente == "shipping_label":
        origen = f"amazon|shipping_label|{orden}|{dia.isoformat()}"
    else:
        subtipo = {
            "labman_label": "LabmanLabelPurchase",
            "mfn_postage": "MFNPostageFee",
            "shipping_hb": "ShippingHB",
            "otros": "ShippingChargeback",
        }[fuente]
        origen = f"amazon|finance|fee|{orden}|SKU-F2|{subtipo}"
    if n:
        origen = f"{origen}|{n}"
    conn.execute(
        "INSERT INTO ledger_event (platform, kind, event_date, order_id, amount,"
        " amount_currency, fee_type, source_event_id, ingest_run_id)"
        " VALUES ('amazon_mx', 'fee', %s, %s, %s, 'MXN', 'shipping_fee', %s, %s)",
        (dia, orden, Decimal(monto), origen, run),
    )


def _familia(conn, slug: str, productos: list[int]) -> int:
    fid = conn.execute(
        "INSERT INTO familia (platform, nombre, slug) VALUES ('amazon_mx', %s, %s) RETURNING id",
        (slug, slug),
    ).fetchone()[0]
    for pid in productos:
        conn.execute(
            "INSERT INTO producto_familia (product_id, platform, familia_id)"
            " VALUES (%s, 'amazon_mx', %s)",
            (pid, fid),
        )
    return fid


def _muestras(conn, pid: int | None = None) -> list[tuple]:
    where = "" if pid is None else " WHERE product_id = %s"
    params = () if pid is None else (pid,)
    return conn.execute(
        "SELECT product_id, platform, canal, origen, valor, valor_currency, envios,"
        " donantes, familia_id, ventana_desde, ventana_hasta, mediana, p90, maximo,"
        " regla_version, percentil, excl_multiproducto, excl_sin_producto, cargos_otros"
        f" FROM precio_envio_muestra{where} ORDER BY id",
        params,
    ).fetchall()


def _producto(conn, sku: str) -> tuple[int, int]:
    pid = conn.execute(
        "INSERT INTO product (odoo_sku, name) VALUES (%s, 'Producto F.2') RETURNING id",
        (sku,),
    ).fetchone()[0]
    lid = conn.execute(
        "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
        " VALUES (%s, 'amazon_mx', %s, %s) RETURNING id",
        (pid, f"B0{sku}", sku),
    ).fetchone()[0]
    return pid, lid


@_skip_sin_pg
def test_orden_propia_da_muestra_propia():
    with _db() as conn:
        pid, _lid = _producto(conn, "SKU-F2-P1")
        run = _run(conn)
        _venta(conn, run, orden="702-f2-p1", producto=pid)
        _cargo(conn, run, orden="702-f2-p1", fuente="shipping_label", monto="-450")
        _cargo(conn, run, orden="702-f2-p1", fuente="labman_label", monto="-449")
        _cargo(conn, run, orden="702-f2-p1", fuente="shipping_hb", monto="-80")
        excl = refrescar_muestras(conn, "amazon_mx", "fbm", hoy=HOY, ventana_dias=180)
        assert (excl.ordenes_multiproducto, excl.ordenes_sin_producto, excl.cargos_otros) == (
            0,
            0,
            0,
        )
        (fila,) = _muestras(conn, pid)
        assert fila[3] == "propio"
        assert (fila[4], fila[5]) == (Decimal("530.0000"), "MXN")
        assert fila[6] == 1
        assert fila[7] is None
        assert fila[8] is None
        assert (fila[9], fila[10]) == (date(2026, 4, 4), date(2026, 9, 30))
        assert fila[11] == Decimal("530.0000")
        assert fila[14] == "f2.1"
        assert fila[15] == "mediana"


@_skip_sin_pg
def test_orden_multiproducto_no_entra_y_se_cuenta():
    with _db() as conn:
        p1, _l1 = _producto(conn, "SKU-F2-M1")
        p2, _l2 = _producto(conn, "SKU-F2-M2")
        run = _run(conn)
        _venta(conn, run, orden="702-f2-m", producto=p1)
        _venta(conn, run, orden="702-f2-m", producto=p2)
        _cargo(conn, run, orden="702-f2-m", fuente="mfn_postage", monto="-91")
        excl = refrescar_muestras(conn, "amazon_mx", "fbm", hoy=HOY, ventana_dias=180)
        assert excl.ordenes_multiproducto == 1
        assert _muestras(conn) == []


@_skip_sin_pg
def test_orden_sin_producto_se_cuenta():
    with _db() as conn:
        _producto(conn, "SKU-F2-S1")
        run = _run(conn)
        _venta(conn, run, orden="702-f2-s", producto=None)
        _cargo(conn, run, orden="702-f2-s", fuente="mfn_postage", monto="-91")
        excl = refrescar_muestras(conn, "amazon_mx", "fbm", hoy=HOY, ventana_dias=180)
        assert excl.ordenes_sin_producto == 1
        assert _muestras(conn) == []


@_skip_sin_pg
def test_cargo_otros_se_cuenta_y_no_entra_al_costo():
    with _db() as conn:
        pid, _lid = _producto(conn, "SKU-F2-O1")
        run = _run(conn)
        _venta(conn, run, orden="702-f2-o", producto=pid)
        _cargo(conn, run, orden="702-f2-o", fuente="mfn_postage", monto="-91")
        _cargo(conn, run, orden="702-f2-o", fuente="otros", monto="-44")
        excl = refrescar_muestras(conn, "amazon_mx", "fbm", hoy=HOY, ventana_dias=180)
        assert excl.cargos_otros == 1
        (fila,) = _muestras(conn, pid)
        assert (fila[3], fila[4]) == ("propio", Decimal("91.0000"))
        assert fila[18] == 1


@_skip_sin_pg
def test_sin_propios_con_familia_da_familia():
    with _db() as conn:
        p1, _l1 = _producto(conn, "SKU-F2-F1")
        p2, _l2 = _producto(conn, "SKU-F2-F2")
        p3, _l3 = _producto(conn, "SKU-F2-F3")
        fid = _familia(conn, "f2_hermanos", [p1, p2, p3])
        run = _run(conn)
        _venta(conn, run, orden="702-f2-f2", producto=p2)
        _cargo(conn, run, orden="702-f2-f2", fuente="mfn_postage", monto="-100")
        _venta(conn, run, orden="702-f2-f3", producto=p3)
        _cargo(conn, run, orden="702-f2-f3", fuente="mfn_postage", monto="-200")
        refrescar_muestras(conn, "amazon_mx", "fbm", hoy=HOY, ventana_dias=180)
        (fila,) = _muestras(conn, p1)
        assert fila[3] == "familia"
        assert fila[4] == Decimal("150.0000")
        assert fila[7] == 2
        assert fila[8] == fid


@_skip_sin_pg
def test_sin_familia_da_marketplace():
    with _db() as conn:
        p1, _l1 = _producto(conn, "SKU-F2-K1")
        p2, _l2 = _producto(conn, "SKU-F2-K2")
        run = _run(conn)
        _venta(conn, run, orden="702-f2-k2", producto=p2)
        _cargo(conn, run, orden="702-f2-k2", fuente="mfn_postage", monto="-120")
        refrescar_muestras(conn, "amazon_mx", "fbm", hoy=HOY, ventana_dias=180)
        (fila,) = _muestras(conn, p1)
        assert fila[3] == "marketplace"
        assert fila[4] == Decimal("120.0000")
        assert fila[7] == 1
        assert fila[8] is None


@_skip_sin_pg
def test_sin_envios_en_el_universo_no_hay_muestra():
    with _db() as conn:
        _producto(conn, "SKU-F2-N1")
        _run(conn)
        excl = refrescar_muestras(conn, "amazon_mx", "fbm", hoy=HOY, ventana_dias=180)
        assert (excl.ordenes_multiproducto, excl.ordenes_sin_producto, excl.cargos_otros) == (
            0,
            0,
            0,
        )
        assert _muestras(conn) == []


@_skip_sin_pg
def test_segunda_corrida_no_escribe():
    with _db() as conn:
        pid, _lid = _producto(conn, "SKU-F2-D1")
        run = _run(conn)
        _venta(conn, run, orden="702-f2-d", producto=pid)
        _cargo(conn, run, orden="702-f2-d", fuente="mfn_postage", monto="-91")
        refrescar_muestras(conn, "amazon_mx", "fbm", hoy=HOY, ventana_dias=180)
        refrescar_muestras(conn, "amazon_mx", "fbm", hoy=HOY, ventana_dias=180)
        assert len(_muestras(conn, pid)) == 1


@_skip_sin_pg
def test_muestra_cambiada_agrega_fila():
    with _db() as conn:
        pid, _lid = _producto(conn, "SKU-F2-C1")
        run = _run(conn)
        _venta(conn, run, orden="702-f2-c1", producto=pid)
        _cargo(conn, run, orden="702-f2-c1", fuente="mfn_postage", monto="-100")
        refrescar_muestras(conn, "amazon_mx", "fbm", hoy=HOY, ventana_dias=180)
        _venta(conn, run, orden="702-f2-c2", producto=pid)
        _cargo(conn, run, orden="702-f2-c2", fuente="mfn_postage", monto="-300")
        refrescar_muestras(conn, "amazon_mx", "fbm", hoy=HOY, ventana_dias=180)
        filas = _muestras(conn, pid)
        assert [f[4] for f in filas] == [Decimal("100.0000"), Decimal("200.0000")]


@_skip_sin_pg
def test_fuera_de_ventana_no_entra():
    with _db() as conn:
        pid, _lid = _producto(conn, "SKU-F2-V1")
        run = _run(conn)
        _venta(conn, run, orden="702-f2-v", producto=pid, dia=VIEJO)
        _cargo(conn, run, orden="702-f2-v", fuente="mfn_postage", monto="-91", dia=VIEJO)
        refrescar_muestras(conn, "amazon_mx", "fbm", hoy=HOY, ventana_dias=180)
        assert _muestras(conn) == []


def test_main_sin_dsn_sale_2(monkeypatch):
    monkeypatch.delenv("ORBIT_DSN_DECIDE", raising=False)
    assert main(["--platform", "amazon_mx", "--canal", "fbm"]) == 2


@_skip_sin_pg
def test_meli_cubre_cada_miembro_activo():
    # El escenario meli se cuenta por miembro activo: el miembro que no es el
    # producto de la publicacion tambien necesita su muestra.
    with _db() as conn:
        principal, variante, inactiva = (
            conn.execute(
                "INSERT INTO product (odoo_sku, name) VALUES (%s, 'Producto F.2') RETURNING id",
                (sku,),
            ).fetchone()[0]
            for sku in ("SKU-F2-ML-A", "SKU-F2-ML-B", "SKU-F2-ML-C")
        )
        lid = conn.execute(
            "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
            " VALUES (%s, 'meli', 'MLM-F2-1', 'SKU-F2-ML-A') RETURNING id",
            (principal,),
        ).fetchone()[0]
        for pid, sku, activo in (
            (principal, "SKU-F2-ML-A", True),
            (variante, "SKU-F2-ML-B", True),
            (inactiva, "SKU-F2-ML-C", False),
        ):
            conn.execute(
                "INSERT INTO listing_miembro"
                " (listing_id, platform, variante_ext, seller_sku, product_id, activo, visto_at)"
                " VALUES (%s, 'meli', %s, %s, %s, %s, now())",
                (lid, f"v-{sku}", sku, pid, activo),
            )
        run = _run(conn)
        conn.execute(
            "INSERT INTO ledger_event (platform, kind, event_date, order_id, product_id,"
            " quantity, amount, amount_currency, source_event_id, ingest_run_id)"
            " VALUES ('meli', 'sale', %s, 'ML-F2-1', %s, 1, 980.0, 'MXN', 'v|ML-F2-1', %s)",
            (DIA, principal, run),
        )
        conn.execute(
            "INSERT INTO ledger_event (platform, kind, event_date, order_id, amount,"
            " amount_currency, fee_type, source_event_id, ingest_run_id)"
            " VALUES ('meli', 'fee', %s, 'ML-F2-1', -120, 'MXN', 'shipping_fee',"
            " 'meli|fee|ML-F2-1|envio', %s)",
            (DIA, run),
        )
        refrescar_muestras(conn, "meli", "meli", hoy=HOY, ventana_dias=180)
        por_producto = {f[0]: (f[3], f[4]) for f in _muestras(conn)}
        assert por_producto == {
            principal: ("propio", Decimal("120.0000")),
            variante: ("marketplace", Decimal("120.0000")),
        }
