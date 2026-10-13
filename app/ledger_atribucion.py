"""Atribucion de eventos del ledger a su publicacion (REPRICING 02, seccion P).

`ledger_event` es append-only: la atribucion vive en tabla aparte. El
protocolo `Atribuidor` lo despacha `app/ledger.py` por plataforma de origen;
`Atribucion` solo existe si se pudo decidir. Lo que hoy no se decide NO se
escribe (una fila `indeterminado` quedaria fija para siempre): la siguiente
ingesta lo reintenta cuando llegue el dato que lo resuelve.

En 0.a el atribuidor de Amazon devuelve None (F.1 lo llena); el de MeLi vive
en `app/meli/ledger.py` y tambien devuelve None (M.2 lo llena).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

__all__ = ["Atribucion", "Atribuidor", "atribuidor_amazon"]


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


class _AtribuidorAmazonVacio:
    """F.1 lo llena: la atribucion por SKU y canal de orden llega con F.1."""

    def atribuir(self, _fila: Any) -> None:
        return None


atribuidor_amazon = _AtribuidorAmazonVacio()
