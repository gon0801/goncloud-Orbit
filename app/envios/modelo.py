"""Hechos de un envio tal como los reporta la fuente (ENVIOS 01, puro).

Sin montos (el dinero tiene dueno en pedidos y ledger) y sin PII: no existe
campo de comprador, destinatario, direccion ni comentarios. Un dato que la
fuente no manda es `None`. Las fechas llevan zona horaria.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Literal

# Ventana unica de seguimiento: ingesta, API y pantalla.
DIAS_SEGUIMIENTO = 30

Plataforma = Literal["amazon_mx", "amazon_us", "meli"]


@dataclass(frozen=True)
class Articulo:
    pedido_ref: str
    sku: str | None
    titulo: str | None
    cantidad: int | None
    producto_ref: str | None


@dataclass(frozen=True)
class PaqueteObservado:
    paquete_ref: str
    transportista: str | None
    servicio: str | None
    guia: str | None
    despachado_el: datetime | None
    estado_fuente: str | None
    detalle_fuente: str | None


@dataclass(frozen=True)
class EnvioObservado:
    platform: Plataforma
    envio_ref: str
    pedidos: tuple[str, ...]
    pack_ref: str | None
    logistica_fuente: str | None
    estado_fuente: str | None
    subestado_fuente: str | None
    pedido_creado_el: datetime | None
    fuente_actualizado_el: datetime | None
    despachar_antes_de: datetime | None
    entrega_prometida_el: datetime | None
    entrega_limite_el: datetime | None
    entregado_el: datetime | None
    devuelto_el: datetime | None
    articulos: tuple[Articulo, ...]
    paquetes: tuple[PaqueteObservado, ...]


def texto_de_fuente(valor: object) -> str | None:
    """Texto crudo de la fuente listo para guardar; vacio o no texto es `None`."""
    if not isinstance(valor, str):
        return None
    # Postgres no admite NUL ni sustitutos sueltos: uno solo tumbaria el lote.
    limpio = valor.replace("\x00", "").encode("utf-8", "replace").decode("utf-8")
    return limpio.strip() or None


def _json(valor: object) -> str:
    return json.dumps(valor, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _canonico(valor: object) -> object:
    if isinstance(valor, dict):
        return {clave: _canonico(v) for clave, v in valor.items()}
    if isinstance(valor, tuple):
        # La fuente no garantiza orden en pedidos, articulos ni paquetes.
        return sorted((_canonico(v) for v in valor), key=_json)
    if isinstance(valor, datetime):
        if valor.utcoffset() is None:
            raise ValueError(f"fecha sin zona horaria: {valor.isoformat()}")
        return valor.astimezone(UTC).isoformat()
    return valor


def huella(envio: EnvioObservado) -> str:
    """sha256 del JSON canonico de los hechos del envio y sus paquetes.

    Excluye `fuente_actualizado_el`: la fuente lo mueve sin que cambie nada de
    lo observado y cada movimiento dejaria una fila nueva.
    """
    hechos = asdict(envio)
    del hechos["fuente_actualizado_el"]
    return hashlib.sha256(_json(_canonico(hechos)).encode("utf-8")).hexdigest()
