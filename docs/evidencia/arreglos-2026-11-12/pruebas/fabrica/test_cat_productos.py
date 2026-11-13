"""Reproducciones de la revision del catalogo (solo lectura del codigo).

Cada prueba FALLA por la asercion que describe la falla."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_fabrica_migracion import _producto  # noqa: E402
from test_pantalla_productos import (  # noqa: E402
    HOY,
    _activa,
    _clics_ads,
    _db_p4,
    _skip_db,
    _ventas,
)

from app import pantalla_productos as pp  # noqa: E402
from app import retiro_anuncios as ra  # noqa: E402


@_skip_db
def test_cat_candidatos_cuentan_clics_de_otro_sku_del_mismo_asin():
    """S5 (regla del motor en productos_io): solo cuentan las filas con
    advertised_sku = seller_sku del listing. La pantalla de candidatos une
    por ASIN y suma los clics de otro SKU: el producto deja de ser
    candidato a impulso aunque sus propios anuncios tengan 5 clics."""
    with _db_p4("orbit_rev_cat_s5") as conn:
        pid, lid = _producto(conn, sku="NH-X", asin="B0REVS5001", seller_sku="NH-X")
        _activa(conn, lid, "ad-s5")
        _ventas(conn, pid, 3)
        _clics_ads(conn, "B0REVS5001", "NH-X", 5)
        _clics_ads(conn, "B0REVS5001", "SET-X", 30)  # otro SKU en el mismo ASIN
        pantalla = pp.lee_productos(conn, "amazon_mx", HOY)
        assert [c.product_id for c in pantalla.candidatos] == [pid], (
            "los 30 clics del SKU SET-X se sumaron al producto NH-X"
        )


@_skip_db
def test_cat_retiro_sin_pedidos_no_verifica_que_no_haya_pedidos():
    """El retiro con motivo sin_pedidos lo decide la pagina: el servidor
    arma el plan con cualquier listing que llegue, aunque el producto haya
    vendido hace 10 dias (no esta en la lista «Sin un pedido en 11 meses»)."""
    with _db_p4("orbit_rev_cat_retiro") as conn:
        pid, lid = _producto(conn, sku="S-VENDE", asin="B0REVRT001", seller_sku="SV-1")
        _activa(conn, lid, "ad-vende")
        _ventas(conn, pid, 4, hace_dias=10)
        pantalla = pp.lee_productos(conn, "amazon_mx", HOY)
        assert pid not in [s.product_id for s in pantalla.sin_pedido]
        plan = ra.planea_retiro(
            conn,
            "amazon_mx",
            listing_ids=(lid,),
            motivo="sin_pedidos",
            conservar_grupo_id=None,
        )
        assert plan.anuncios == (), (
            "el plan sin_pedidos incluye el anuncio de un producto que vendio hace 10 dias"
        )
