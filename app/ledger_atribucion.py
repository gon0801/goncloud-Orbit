"""Atribucion de eventos del ledger a su publicacion (REPRICING 02, seccion P).

`ledger_event` es append-only: la atribucion vive en tabla aparte. El
protocolo `Atribuidor` lo despacha `app/ledger.py` por plataforma de origen;
`Atribucion` solo existe si se pudo decidir. Lo que hoy no se decide NO se
escribe (una fila `indeterminado` quedaria fija para siempre): la siguiente
ingesta lo reintenta cuando llegue el dato que lo resuelve.

El atribuidor de Amazon (F.1) resuelve con dos lecturas: los listings del
producto y el canal de la orden. Un producto con un solo listing se atribuye
por `sku`, sin mirar nada mas. Con varios, el canal de la orden
(`spapi_order_observation.fulfillment_channel`) elige el listing de ese canal
(`canal_orden`); el canal del listing sale de `spapi_inventario_observation`,
que es inventario FBA por definicion (fila presente = FBA, ausente = FBM).
Sin canal conocido en la orden, o con dos candidatos del mismo canal, no hay
decision.

El atribuidor abre su propia conexion de SOLO LECTURA desde
`ORBIT_DSN_INGEST` cuando nadie le pasa una: `app/ledger.py` (contrato del
carril M) le entrega solo el evento, asi que no puede recibir la conexion de
la ingesta. Solo hace SELECT sobre tablas que la ingesta del ledger no
escribe, asi que no ve datos a medias. Sin DSN o sin conexion, devuelve None
y la venta se cuenta para reintento, igual que sin dato.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from app.db import connect

logger = logging.getLogger(__name__)

__all__ = ["Atribucion", "Atribuidor", "AtribuidorAmazon", "atribuidor_amazon"]


@dataclass(frozen=True)
class Atribucion:
    """A que publicacion pertenece un evento del ledger."""

    listing_id: int
    variante: str | None
    resuelto_por: str

    def __post_init__(self) -> None:
        if self.resuelto_por not in ("sku", "externo_unico", "producto_unico", "canal_orden"):
            raise ValueError(f"resuelto_por invalido: {self.resuelto_por!r}")


@runtime_checkable
class Atribuidor(Protocol):
    """Uno por plataforma de origen. None = todavia no se puede decidir."""

    def atribuir(self, fila: Any) -> Atribucion | None: ...


_CANAL_POR_FULFILLMENT = {
    "AMAZON": "fba",
    "AFN": "fba",
    "MERCHANT": "fbm",
    "MFN": "fbm",
}

_SQL_LISTINGS = (
    "SELECT id, seller_sku FROM listing WHERE platform = %s AND product_id = %s ORDER BY id"
)
_SQL_CANAL_ORDEN = (
    "SELECT fulfillment_channel FROM spapi_order_observation"
    " WHERE platform = %s AND amazon_order_id = %s"
    " AND fulfillment_channel IS NOT NULL"
    " ORDER BY last_updated_time DESC, id DESC LIMIT 1"
)
_SQL_TIENE_INVENTARIO = (
    "SELECT 1 FROM spapi_inventario_observation WHERE platform = %s AND seller_sku = %s LIMIT 1"
)


class AtribuidorAmazon:
    """Ventas de Amazon a su listing, por SKU unico o canal de orden.

    Con `conn` usa esa conexion (pruebas y llamadores futuros); sin ella la
    abre de solo lectura desde `ORBIT_DSN_INGEST` al primer evento que pida
    base. Guarda mapas chicos por instancia: listings por producto, canal por
    orden y presencia de inventario por SKU.
    """

    def __init__(self, conn: Any | None = None) -> None:
        self._conn = conn
        self._propia = False
        self._sin_base = False
        self._listings: dict[tuple[str, int], tuple[tuple[int, str | None], ...]] = {}
        self._canal_orden: dict[tuple[str, str], str | None] = {}
        self._tiene_inventario: dict[tuple[str, str], bool] = {}

    @staticmethod
    def canal_de_orden(fulfillment: str | None) -> str | None:
        """Canal fba/fbm desde `fulfilledBy` (AMAZON/MERCHANT) o `FulfillmentChannel` (AFN/MFN)."""
        if not isinstance(fulfillment, str):
            return None
        return _CANAL_POR_FULFILLMENT.get(fulfillment.strip().upper())

    def _db(self) -> Any | None:
        if self._conn is not None:
            return self._conn
        if self._sin_base:
            return None
        dsn = os.environ.get("ORBIT_DSN_INGEST")
        if not dsn:
            logger.warning("atribuidor amazon sin ORBIT_DSN_INGEST: todo queda sin atribuir")
            self._sin_base = True
            return None
        try:
            conn = connect(dsn, autocommit=True)
            # `read_only` no muerde en autocommit (psycopg lo aplica al
            # BEGIN); la sesion entera se declara de lectura.
            conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
        except Exception as exc:
            logger.error("atribuidor amazon sin base: %s", exc)
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

    def _candidatos(self, db: Any, plataforma: str, product_id: int) -> tuple:
        clave = (plataforma, product_id)
        if clave not in self._listings:
            self._listings[clave] = tuple(
                db.execute(_SQL_LISTINGS, (plataforma, product_id)).fetchall()
            )
        return self._listings[clave]

    def _canal_de_orden(self, db: Any, plataforma: str, order_id: str) -> str | None:
        clave = (plataforma, order_id)
        if clave not in self._canal_orden:
            fila = db.execute(_SQL_CANAL_ORDEN, (plataforma, order_id)).fetchone()
            self._canal_orden[clave] = self.canal_de_orden(fila[0] if fila else None)
        return self._canal_orden[clave]

    def _canal_de_listing(self, db: Any, plataforma: str, seller_sku: str | None) -> str | None:
        if seller_sku is None:
            return None
        clave = (plataforma, seller_sku)
        if clave not in self._tiene_inventario:
            self._tiene_inventario[clave] = (
                db.execute(_SQL_TIENE_INVENTARIO, (plataforma, seller_sku)).fetchone() is not None
            )
        return "fba" if self._tiene_inventario[clave] else "fbm"

    def atribuir(self, fila: Any) -> Atribucion | None:
        if getattr(fila, "kind", None) != "sale":
            return None
        plataforma = getattr(fila, "platform", None)
        product_id = getattr(fila, "product_id", None)
        if not isinstance(plataforma, str) or not isinstance(product_id, int):
            return None
        db = self._db()
        if db is None:
            return None
        candidatos = self._candidatos(db, plataforma, product_id)
        if len(candidatos) == 1:
            return Atribucion(listing_id=candidatos[0][0], variante=None, resuelto_por="sku")
        if len(candidatos) < 2:
            return None
        order_id = getattr(fila, "order_id", None)
        if not isinstance(order_id, str) or not order_id:
            return None
        canal = self._canal_de_orden(db, plataforma, order_id)
        if canal is None:
            return None
        ganadores = [
            lid for lid, sku in candidatos if self._canal_de_listing(db, plataforma, sku) == canal
        ]
        if len(ganadores) != 1:
            return None
        return Atribucion(listing_id=ganadores[0], variante=None, resuelto_por="canal_orden")


atribuidor_amazon = AtribuidorAmazon()
