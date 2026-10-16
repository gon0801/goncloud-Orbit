"""Atribuidor de MeLi y mapeo de sus tipos de cargo (REPRICING 02, M.2)."""

from __future__ import annotations

import logging
import os
from typing import Any

from app.db import connect
from app.ledger_atribucion import Atribucion

__all__ = ["AtribuidorMeli", "atribuidor_meli"]

logger = logging.getLogger(__name__)

_SQL_POR_ITEM = "SELECT id FROM listing WHERE platform = 'meli' AND external_id = %s"
_SQL_POR_PRODUCTO = (
    "SELECT DISTINCT listing_id FROM listing_miembro"
    " WHERE platform = 'meli' AND product_id = %s AND activo"
)


class AtribuidorMeli:
    """Ventas de MeLi a su publicacion, por item o producto unico.

    Con `conn` usa esa conexion (pruebas); sin ella la abre de solo
    lectura desde `ORBIT_DSN_INGEST` al primer evento que pida base.
    """

    def __init__(self, conn: Any | None = None) -> None:
        self._conn = conn
        self._propia = False
        self._sin_base = False

    def _db(self) -> Any | None:
        if self._conn is not None:
            return self._conn
        if self._sin_base:
            return None
        dsn = os.environ.get("ORBIT_DSN_INGEST")
        if not dsn:
            logger.warning("atribuidor meli sin ORBIT_DSN_INGEST: todo queda sin atribuir")
            self._sin_base = True
            return None
        try:
            conn = connect(dsn, autocommit=True)
            conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
        except Exception as exc:
            logger.error("atribuidor meli sin base: %s", exc)
            self._sin_base = True
            return None
        self._conn = conn
        self._propia = True
        return conn

    def close(self) -> None:
        """Cierra la conexion propia, si la abrio. La inyectada no se toca."""

        if self._propia and self._conn is not None:
            self._conn.close()
            self._conn = None
            self._propia = False

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
