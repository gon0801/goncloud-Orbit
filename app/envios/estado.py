"""Clasificador unico de envios (ENVIOS 01 §3, puro).

Deriva estado unificado, tipo logistico y motivo de atencion de los hechos de
la fuente; nada de esto se persiste. El reloj entra como argumento: `juzgado_al`
mide las demoras, para que una lectura atrasada no las invente, y `ahora` mide el
cierre por vencimiento. Un estado que no esta en las tablas es `sin_clasificar`
y un tipo logistico desconocido es `None`; ninguno genera atencion.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Literal

from app.envios.modelo import DIAS_SEGUIMIENTO, EnvioObservado, PaqueteObservado

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
    "cerrado_sin_confirmar",
    "sin_clasificar",
]
Tipo = Literal["amazon_fba", "amazon_fbm", "meli_full", "meli_flex", "meli_envios"]
Motivo = Literal["problema_entrega", "despacho_vencido", "entrega_vencida"]


@dataclass(frozen=True)
class Clasificacion:
    estado: Estado
    tipo: Tipo | None
    motivo: Motivo | None


_TERMINALES_DE_LA_FUENTE: frozenset[Estado] = frozenset({"entregado", "devuelto", "cancelado"})
# Cerrado no se relee ni cuenta como en curso.
ESTADOS_CERRADOS: frozenset[Estado] = _TERMINALES_DE_LA_FUENTE | {"cerrado_sin_confirmar"}

# Cierre por vencimiento de Mercado Libre, en dias desde el pedido (config_version).
CLAVE_DIAS_CIERRE = "envios_meli_dias_cierre"
DIAS_CIERRE_POR_OMISION = 90
DIAS_CIERRE_MAXIMO = 365


class DiasCierreInvalidos(ValueError):
    """La clave existe y no sirve. El mensaje es para el operador y no repite el
    valor guardado: sale por la API y por stderr."""


def dias_cierre_desde_settings(settings: Mapping) -> int:
    """Sin la clave rige el valor por omision; presente, solo texto entero entre
    la ventana de busqueda y el maximo."""
    if CLAVE_DIAS_CIERRE not in settings:
        return DIAS_CIERRE_POR_OMISION
    crudo = settings[CLAVE_DIAS_CIERRE]
    if (
        isinstance(crudo, str)
        and crudo.isascii()
        and crudo.isdigit()
        and DIAS_SEGUIMIENTO <= int(crudo) <= DIAS_CIERRE_MAXIMO
    ):
        return int(crudo)
    raise DiasCierreInvalidos(
        f"Configuración inválida: {CLAVE_DIAS_CIERRE} debe ser un número entero de"
        f" {DIAS_SEGUIMIENTO} a {DIAS_CIERRE_MAXIMO}. Se ajusta con:"
        " python -m app.cli envios-ventana --dias N"
    )


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


class _Regla(Enum):
    """Entradas de la tabla de Mercado Libre que no son un substatus literal."""

    CON_DEVOLUCION = "la fuente trae devuelto_el"
    CUALQUIERA = "cualquier substatus, tambien ninguno"


# (status, substatus que lo producen, estado). `None` es "sin substatus". Un par
# que no esta aqui es `sin_clasificar`.
_MELI_TABLA: tuple[tuple[str, tuple[str | _Regla | None, ...], Estado], ...] = (
    ("pending", (_Regla.CUALQUIERA,), "pendiente"),
    ("handling", (None,), "por_despachar"),
    (
        "ready_to_ship",
        (None, "ready_to_print", "printed", "delayed", "stale", "in_packing_list"),
        "por_despachar",
    ),
    ("ready_to_ship", ("dropped_off", "picked_up", "in_hub"), "en_camino"),
    ("shipped", (None, "delayed"), "en_camino"),
    (
        "shipped",
        ("out_for_delivery", "soon_deliver", "at_the_door", "in_delivery_zone"),
        "en_reparto",
    ),
    ("shipped", ("waiting_for_withdrawal",), "esperando_recoleccion"),
    (
        "shipped",
        ("bad_address", "refused_delivery", "not_localized", "contact_with_carrier_required"),
        "problema",
    ),
    ("delivered", (_Regla.CUALQUIERA,), "entregado"),
    ("not_delivered", (_Regla.CON_DEVOLUCION,), "devuelto"),
    ("not_delivered", ("returning_to_sender", "returned_to_hub"), "en_devolucion"),
    ("not_delivered", (_Regla.CUALQUIERA,), "problema"),
    ("cancelled", (_Regla.CUALQUIERA,), "cancelado"),
)
_MELI: dict[tuple[str, str | _Regla | None], Estado] = {
    (status, substatus): estado
    for status, substatuses, estado in _MELI_TABLA
    for substatus in substatuses
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
    # De la entrada mas especifica a la mas general.
    entradas = [envio.subestado_fuente, _Regla.CUALQUIERA]
    if envio.devuelto_el is not None:
        entradas.insert(0, _Regla.CON_DEVOLUCION)
    claves = ((envio.estado_fuente, entrada) for entrada in entradas)
    return next((_MELI[clave] for clave in claves if clave in _MELI), "sin_clasificar")


def _vence_sin_confirmar(
    envio: EnvioObservado, estado: Estado, *, ahora: datetime, dias_cierre: int
) -> bool:
    """Solo Mercado Libre: sin estado terminal y con mas de `dias_cierre` dias
    desde el pedido. Sin fecha de pedido no vence."""
    return (
        envio.platform == "meli"
        and estado not in _TERMINALES_DE_LA_FUENTE
        and envio.pedido_creado_el is not None
        and ahora - envio.pedido_creado_el > timedelta(days=dias_cierre)
    )


def fecha_limite(envio: EnvioObservado, estado: Estado) -> datetime | None:
    """El plazo que corre en ese estado: despachar antes de salir; entregar en
    ruta, con problema o en devolucion. En los demas no corre ninguno: los
    enviados sin confirmacion nunca cierran y su fecha vieja enterraria lo
    pendiente."""
    if estado in ("pendiente", "por_despachar"):
        return envio.despachar_antes_de
    if estado in ("en_camino", "en_reparto", "problema", "en_devolucion"):
        return envio.entrega_limite_el or envio.entrega_prometida_el
    return None


def _vencido(limite: datetime | None, juzgado_al: datetime) -> bool:
    return limite is not None and juzgado_al > limite


def _motivo(
    envio: EnvioObservado, estado: Estado, tipo: Tipo | None, juzgado_al: datetime
) -> Motivo | None:
    if tipo not in _TIPOS_DEL_VENDEDOR:
        return None
    if estado in ("problema", "en_devolucion"):
        return "problema_entrega"
    plazo_vencido = _vencido(fecha_limite(envio, estado), juzgado_al)
    if estado == "por_despachar" and (
        plazo_vencido
        # xd_drop_off no manda plazo de despacho: el atraso llega por substatus.
        or (envio.platform == "meli" and envio.subestado_fuente in _MELI_DESPACHO_ATRASADO)
    ):
        return "despacho_vencido"
    if estado in ("en_camino", "en_reparto") and plazo_vencido:
        return "entrega_vencida"
    return None


def clasificar(
    envio: EnvioObservado, *, juzgado_al: datetime, ahora: datetime, dias_cierre: int
) -> Clasificacion:
    estado = _estado_meli(envio) if envio.platform == "meli" else _estado_amazon(envio)
    if _vence_sin_confirmar(envio, estado, ahora=ahora, dias_cierre=dias_cierre):
        estado = "cerrado_sin_confirmar"
    tipo = _TIPO.get(envio.logistica_fuente)
    return Clasificacion(estado, tipo, _motivo(envio, estado, tipo, juzgado_al))
