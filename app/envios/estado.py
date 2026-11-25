"""Clasificador unico de envios (ENVIOS 01 §3, puro).

Deriva estado unificado, tipo logistico y motivo de atencion de los hechos de
la fuente; nada de esto se persiste. El reloj entra como `juzgado_al` para que
una lectura atrasada no invente demoras. Un estado que no esta en las tablas es
`sin_clasificar` y un tipo logistico desconocido es `None`; ninguno genera
atencion.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.envios.modelo import EnvioObservado, PaqueteObservado

Estado = Literal[
    "pendiente",
    "por_despachar",
    "en_camino",
    "en_reparto",
    "esperando_recoleccion",
    "entregado",
    "enviado_sin_confirmacion",
    "problema",
    "en_devolucion",
    "devuelto",
    "cancelado",
    "sin_clasificar",
]
Tipo = Literal["amazon_fba", "amazon_fbm", "meli_full", "meli_flex", "meli_envios"]
Motivo = Literal["problema_entrega", "despacho_vencido", "entrega_vencida"]


@dataclass(frozen=True)
class Clasificacion:
    estado: Estado
    tipo: Tipo | None
    motivo: Motivo | None


# Sonda 2026-11-25: solo estos pares reportan DELIVERED. Con el resto, SHIPPED
# es lo ultimo que Amazon dice y esperar la entrega daria demoras falsas.
AMAZON_CONFIRMA_ENTREGA = frozenset({("amazon_mx", "AMZMX"), ("amazon_mx", "DHLC")})

_TIPO: dict[str, Tipo] = {
    "AMAZON": "amazon_fba",
    "MERCHANT": "amazon_fbm",
    "fulfillment": "meli_full",
    "self_service": "meli_flex",
    "xd_drop_off": "meli_envios",
    "drop_off": "meli_envios",
    "cross_docking": "meli_envios",
}

# Sin paquetes manda el fulfillmentStatus del pedido.
_AMAZON_PEDIDO: dict[str, Estado] = {
    "PENDING": "pendiente",
    "PENDING_AVAILABILITY": "pendiente",
    "INVOICE_UNCONFIRMED": "pendiente",
    "UNSHIPPED": "por_despachar",
    "PARTIALLY_SHIPPED": "por_despachar",
    "SHIPPED": "enviado_sin_confirmacion",
    "CANCELLED": "cancelado",
    "UNFULFILLABLE": "problema",
}

# Con paquetes, el par (status, detailedStatus) manda sobre el status solo.
_AMAZON_PAQUETE_DETALLE: dict[tuple[str, str], Estado] = {
    ("UNDELIVERABLE", "RETURNING_TO_SELLER"): "en_devolucion",
    ("UNDELIVERABLE", "RETURNED_TO_SELLER"): "devuelto",
    ("UNDELIVERABLE", "RETURNED"): "devuelto",
    ("IN_TRANSIT", "OUT_FOR_DELIVERY"): "en_reparto",
}
_AMAZON_PAQUETE: dict[str, Estado] = {
    "DELIVERED": "entregado",
    "UNDELIVERABLE": "problema",
    "IN_TRANSIT": "en_camino",
}

# Un envio con varios paquetes toma el primero de esta lista que tenga alguno.
_SEVERIDAD_AMAZON: tuple[Estado, ...] = (
    "problema",
    "en_devolucion",
    "sin_clasificar",
    "por_despachar",
    "en_reparto",
    "en_camino",
    "enviado_sin_confirmacion",
    "devuelto",
    "entregado",
)

# status -> (estado sin substatus conocido, estado por substatus)
_MELI: dict[str, tuple[Estado, dict[str, Estado]]] = {
    "pending": ("pendiente", {}),
    "handling": ("por_despachar", {}),
    "ready_to_ship": (
        "por_despachar",
        {"dropped_off": "en_camino", "picked_up": "en_camino"},
    ),
    "shipped": (
        "en_camino",
        {
            "out_for_delivery": "en_reparto",
            "soon_deliver": "en_reparto",
            "at_the_door": "en_reparto",
            "in_delivery_zone": "en_reparto",
            "waiting_for_withdrawal": "esperando_recoleccion",
            "bad_address": "problema",
            "refused_delivery": "problema",
            "not_localized": "problema",
            "contact_with_carrier_required": "problema",
        },
    ),
    "delivered": ("entregado", {}),
    "not_delivered": (
        "problema",
        {"returning_to_sender": "en_devolucion", "returned_to_hub": "en_devolucion"},
    ),
    "cancelled": ("cancelado", {}),
}
_MELI_DESPACHO_ATRASADO = frozenset({"delayed", "stale"})

# Full y FBA los opera la plataforma: el vendedor no tiene nada que atender.
_TIPOS_DEL_VENDEDOR: frozenset[Tipo] = frozenset({"amazon_fbm", "meli_flex", "meli_envios"})


def _estado_paquete(platform: str, paquete: PaqueteObservado) -> Estado:
    if paquete.estado_fuente == "SHIPPED":
        if (platform, paquete.transportista) in AMAZON_CONFIRMA_ENTREGA:
            return "en_camino"
        return "enviado_sin_confirmacion"
    por_detalle = _AMAZON_PAQUETE_DETALLE.get((paquete.estado_fuente, paquete.detalle_fuente))
    return por_detalle or _AMAZON_PAQUETE.get(paquete.estado_fuente, "sin_clasificar")


def _estado_amazon(envio: EnvioObservado) -> Estado:
    if not envio.paquetes:
        return _AMAZON_PEDIDO.get(envio.estado_fuente, "sin_clasificar")
    presentes = {_estado_paquete(envio.platform, paquete) for paquete in envio.paquetes}
    if envio.estado_fuente == "PARTIALLY_SHIPPED":
        presentes.add("por_despachar")
    return next(estado for estado in _SEVERIDAD_AMAZON if estado in presentes)


def _estado_meli(envio: EnvioObservado) -> Estado:
    if envio.estado_fuente == "not_delivered" and envio.devuelto_el is not None:
        return "devuelto"
    sin_subestado, por_subestado = _MELI.get(envio.estado_fuente, ("sin_clasificar", {}))
    return por_subestado.get(envio.subestado_fuente, sin_subestado)


def _vencido(limite: datetime | None, juzgado_al: datetime) -> bool:
    return limite is not None and juzgado_al > limite


def _motivo(
    envio: EnvioObservado, estado: Estado, tipo: Tipo | None, juzgado_al: datetime
) -> Motivo | None:
    if tipo not in _TIPOS_DEL_VENDEDOR:
        return None
    if estado in ("problema", "en_devolucion"):
        return "problema_entrega"
    if estado == "por_despachar" and (
        _vencido(envio.despachar_antes_de, juzgado_al)
        # xd_drop_off no manda plazo de despacho: el atraso llega por substatus.
        or (envio.platform == "meli" and envio.subestado_fuente in _MELI_DESPACHO_ATRASADO)
    ):
        return "despacho_vencido"
    if estado in ("en_camino", "en_reparto") and _vencido(
        envio.entrega_limite_el or envio.entrega_prometida_el, juzgado_al
    ):
        return "entrega_vencida"
    return None


def clasificar(envio: EnvioObservado, *, juzgado_al: datetime) -> Clasificacion:
    estado = _estado_meli(envio) if envio.platform == "meli" else _estado_amazon(envio)
    tipo = _TIPO.get(envio.logistica_fuente)
    return Clasificacion(estado, tipo, _motivo(envio, estado, tipo, juzgado_al))
