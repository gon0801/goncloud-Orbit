"""Traduce ordenes y envios crudos de Mercado Libre a `EnvioObservado` (puro).

Lista cerrada: de la orden se leen `id`, `pack_id`, `date_created`, `shipping.id`
y producto y cantidad de `order_items`; del envio, `id`, tipo logistico, estados,
guia, `last_updated`, `status_history` y los plazos de `shipping_option`.
Comprador, direcciones, emisor, receptor, comentarios y montos no se leen aunque
vengan. Un dato ausente o ilegible es `None`; sin identidad no hay envio.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime

from app.envios.modelo import Articulo, EnvioObservado, PaqueteObservado, texto_de_fuente

_texto = texto_de_fuente


class EnvioIlegible(ValueError):
    """Sin id de envio o sin ningun pedido con id. El mensaje no lleva datos."""


def _objeto(valor: object) -> dict:
    return valor if isinstance(valor, dict) else {}


def _objetos(valor: object) -> list[dict]:
    return [v for v in valor if isinstance(v, dict)] if isinstance(valor, list) else []


def _id(valor: object) -> str | None:
    """Id numerico de pedido, pack o envio, siempre como texto."""
    if type(valor) is int:
        return str(valor) if valor >= 0 else None
    texto = _texto(valor)
    return texto if texto is not None and texto.isascii() and texto.isdigit() else None


def _fecha(valor: object) -> datetime | None:
    texto = _texto(valor)
    if texto is None:
        return None
    try:
        momento = datetime.fromisoformat(texto)
        # Mercado Libre manda offset; suponerlo cuando falta seria inventar la hora.
        return None if momento.tzinfo is None else momento.astimezone(UTC)
    except (ValueError, OverflowError):
        return None


def pedido_ref_de_orden(orden: dict) -> str | None:
    return _id(orden.get("id"))


def envio_ref_de_orden(orden: dict) -> str | None:
    return _id(_objeto(orden.get("shipping")).get("id"))


def creada_el(orden: dict) -> datetime | None:
    return _fecha(orden.get("date_created"))


def _articulo(renglon: dict, pedido_ref: str) -> Articulo:
    producto = _objeto(renglon.get("item"))
    cantidad = renglon.get("quantity")
    return Articulo(
        pedido_ref=pedido_ref,
        sku=_texto(producto.get("seller_sku")),
        titulo=_texto(producto.get("title")),
        cantidad=cantidad if type(cantidad) is int and cantidad >= 0 else None,
        producto_ref=_texto(producto.get("id")),
    )


def _hechos_del_envio(envio: dict) -> dict:
    """Campos de `EnvioObservado` que salen de `/shipments/{id}`.

    En Mercado Libre el envio es su propio y unico paquete.
    """
    ref = _id(envio.get("id"))
    if ref is None:
        raise EnvioIlegible("envio sin id")
    historia = _objeto(envio.get("status_history"))
    opcion = _objeto(envio.get("shipping_option"))
    entrega = _objeto(opcion.get("estimated_delivery_time"))
    return {
        "envio_ref": ref,
        "logistica_fuente": _texto(envio.get("logistic_type")),
        "estado_fuente": _texto(envio.get("status")),
        "subestado_fuente": _texto(envio.get("substatus")),
        "fuente_actualizado_el": _fecha(envio.get("last_updated")),
        "despachar_antes_de": _fecha(_objeto(opcion.get("estimated_handling_limit")).get("date")),
        "entrega_prometida_el": _fecha(entrega.get("date")),
        "entrega_limite_el": _fecha(_objeto(entrega.get("offset")).get("date")),
        "entregado_el": _fecha(historia.get("date_delivered")),
        "devuelto_el": _fecha(historia.get("date_returned")),
        "paquetes": (
            PaqueteObservado(
                paquete_ref=ref,
                transportista=_texto(envio.get("tracking_method")),
                servicio=None,
                guia=_texto(envio.get("tracking_number")),
                despachado_el=_fecha(historia.get("date_shipped")),
                estado_fuente=None,
                detalle_fuente=None,
            ),
        ),
    }


def envio_desde_meli(ordenes: Sequence[dict], envio: dict) -> EnvioObservado:
    """Un envio con todas sus ordenes (un pack comparte `shipping.id`)."""
    por_ref = {ref: orden for orden in ordenes if (ref := pedido_ref_de_orden(orden))}
    if not por_ref:
        raise EnvioIlegible("envio sin pedidos")
    pedidos = tuple(sorted(por_ref))
    packs = (_id(por_ref[ref].get("pack_id")) for ref in pedidos)
    fechas = (creada_el(orden) for orden in por_ref.values())
    return EnvioObservado(
        platform="meli",
        pedidos=pedidos,
        pack_ref=next((pack for pack in packs if pack), None),
        pedido_creado_el=min((fecha for fecha in fechas if fecha), default=None),
        articulos=tuple(
            _articulo(renglon, ref)
            for ref in pedidos
            for renglon in _objetos(por_ref[ref].get("order_items"))
        ),
        **_hechos_del_envio(envio),
    )


def envio_actualizado_desde_meli(previo: EnvioObservado, envio: dict) -> EnvioObservado:
    """Relee un envio cuyas ordenes ya no vienen en la busqueda: se conservan
    pedidos, pack, fecha del pedido y articulos de `previo`."""
    return replace(previo, **_hechos_del_envio(envio))
