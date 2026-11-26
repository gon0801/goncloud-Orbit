"""Traduce una orden cruda de Orders 2026-01-01 a `EnvioObservado` (puro).

Lista cerrada: solo se leen las ventanas de `fulfillment`, el producto y la
cantidad de `orderItems` y los datos logisticos de `packages`. Comprador,
destinatario, remitente, direcciones y montos no se leen aunque vengan. La
identidad, los estados y las fechas del pedido salen de la `OrdenParseada`.
Un dato ausente o ilegible es `None`: nada de aqui tumba la orden ni el lote.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.envios.modelo import Articulo, EnvioObservado, PaqueteObservado, texto_de_fuente

if TYPE_CHECKING:
    from app.spapi.orders import OrdenParseada

_texto = texto_de_fuente


def _objeto(valor: object) -> dict:
    return valor if isinstance(valor, dict) else {}


def _objetos(valor: object) -> list[dict]:
    return [v for v in valor if isinstance(v, dict)] if isinstance(valor, list) else []


def _fecha(valor: object) -> datetime | None:
    texto = _texto(valor)
    if texto is None:
        return None
    try:
        momento = datetime.fromisoformat(texto)
        # Sin zona se lee como UTC, igual que en parsear_orden.
        if momento.tzinfo is None:
            momento = momento.replace(tzinfo=UTC)
        return momento.astimezone(UTC)
    except (ValueError, OverflowError):
        return None


def _articulo(item: dict, pedido_ref: str) -> Articulo:
    producto = _objeto(item.get("product"))
    cantidad = item.get("quantityOrdered")
    return Articulo(
        pedido_ref=pedido_ref,
        sku=_texto(producto.get("sellerSku")),
        titulo=_texto(producto.get("title")),
        cantidad=cantidad if type(cantidad) is int and cantidad >= 0 else None,
        producto_ref=_texto(producto.get("asin")),
    )


def _paquetes(crudos: object) -> tuple[PaqueteObservado, ...]:
    """Un paquete por referencia (gana la primera aparicion); sin ella se omite."""
    por_ref: dict[str, PaqueteObservado] = {}
    for crudo in _objetos(crudos):
        referencia = crudo.get("packageReferenceId")
        # La sonda no registro el tipo JSON: se acepta texto o entero.
        ref = str(referencia) if type(referencia) is int else _texto(referencia)
        if ref is None or ref in por_ref:
            continue
        estado = _objeto(crudo.get("packageStatus"))
        por_ref[ref] = PaqueteObservado(
            paquete_ref=ref,
            transportista=_texto(crudo.get("carrier")),
            servicio=_texto(crudo.get("shippingService")),
            guia=_texto(crudo.get("trackingNumber")),
            despachado_el=_fecha(crudo.get("shipTime")),
            estado_fuente=_texto(estado.get("status")),
            detalle_fuente=_texto(estado.get("detailedStatus")),
        )
    return tuple(por_ref.values())


def envio_desde_orden(cruda: dict, *, platform: str, orden: OrdenParseada) -> EnvioObservado:
    """En Amazon el envio es el pedido: un `EnvioObservado` por `orderId`."""
    fulfillment = _objeto(cruda.get("fulfillment"))
    despacho = _objeto(fulfillment.get("shipByWindow"))
    entrega = _objeto(fulfillment.get("deliverByWindow"))
    return EnvioObservado(
        platform=platform,
        envio_ref=orden.amazon_order_id,
        pedidos=(orden.amazon_order_id,),
        pack_ref=None,
        logistica_fuente=orden.fulfillment_channel,
        estado_fuente=orden.fulfillment_status,
        subestado_fuente=None,
        pedido_creado_el=orden.purchase_date,
        fuente_actualizado_el=orden.last_updated_time,
        despachar_antes_de=_fecha(despacho.get("latestDateTime")),
        entrega_prometida_el=_fecha(entrega.get("latestDateTime")),
        entrega_limite_el=None,
        entregado_el=None,
        devuelto_el=None,
        articulos=tuple(
            _articulo(item, orden.amazon_order_id) for item in _objetos(cruda.get("orderItems"))
        ),
        paquetes=_paquetes(cruda.get("packages")),
    )
