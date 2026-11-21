"""Atribuidor de MeLi y mapeo de sus tipos de cargo (REPRICING 02, M.2)."""

from __future__ import annotations

from typing import Any

from app.ledger_atribucion import Atribucion, AtribuidorBase

__all__ = ["AtribuidorMeli", "atribuidor_meli"]

_SQL_POR_ITEM = "SELECT id FROM listing WHERE platform = 'meli' AND external_id = %s"
_SQL_POR_PRODUCTO = (
    "SELECT DISTINCT listing_id FROM listing_miembro"
    " WHERE platform = 'meli' AND product_id = %s AND activo"
)


class AtribuidorMeli(AtribuidorBase):
    """Ventas de MeLi a su publicacion, por item o producto unico.

    Con `conn` usa esa conexion (pruebas y sync_ledger); sin ella hereda
    la apertura lazy read-only de `AtribuidorBase`.
    """

    _nombre_log = "meli"

    def atribuir(self, fila: Any) -> Atribucion | None:
        if getattr(fila, "kind", None) != "sale":
            return None
        if getattr(fila, "platform", None) != "meli":
            return None
        item = None
        crudo = getattr(fila, "payload", None)
        if isinstance(crudo, dict):
            renglon = crudo.get("row")
            if isinstance(renglon, dict):
                item = renglon.get("item_id")
        con_item = isinstance(item, str) and bool(item)
        product_id = getattr(fila, "product_id", None)
        con_producto = isinstance(product_id, int)
        if not con_item and not con_producto:
            return None
        db = self._db()
        if db is None:
            return None
        if con_item:
            hallado = db.execute(_SQL_POR_ITEM, (item,)).fetchone()
            if hallado is not None:
                return Atribucion(
                    listing_id=hallado[0], variante=None, resuelto_por="externo_unico"
                )
        if not con_producto:
            return None
        candidatos = db.execute(_SQL_POR_PRODUCTO, (product_id,)).fetchall()
        if len(candidatos) == 1:
            return Atribucion(listing_id=candidatos[0][0], variante=None, resuelto_por="sku")
        return None


atribuidor_meli = AtribuidorMeli()
