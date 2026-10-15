"""Costo de envio por orden y su origen (REPRICING 02 F.2, seccion C).

Puro: sin base, sin red, sin reloj. La etiqueta se cuenta UNA vez por
cualquiera de sus tres caminos (`max(labman_label, shipping_label,
mfn_postage)`) mas `shipping_hb` (regla del plan, de E.0). `resolver_envio`
es la unica escalera de origen: propio -> familia -> marketplace. `familia`
y `marketplace` son medianas de MEDIANAS por producto donante: un producto
con 40 envios no aplasta a los demas.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from app.precio.tipos import Importe, MonedasMezcladas

__all__ = [
    "REGLA_VERSION",
    "CargoEnvio",
    "Exclusiones",
    "FuenteCargo",
    "MuestraNueva",
    "OrdenMedida",
    "clasificar_cargo",
    "costo_de_orden",
    "resolver_envio",
]

REGLA_VERSION = "f2.1"

FuenteCargo = Literal[
    "labman_label", "shipping_label", "mfn_postage", "shipping_hb", "meli_envio", "otros"
]

_ETIQUETAS = ("labman_label", "shipping_label", "mfn_postage")

_IDENTIDAD_A_FUENTE = {
    "LabmanLabelPurchase": "labman_label",
    "shipping_label": "shipping_label",
    "MFNPostageFee": "mfn_postage",
    "ShippingHB": "shipping_hb",
}


@dataclass(frozen=True)
class CargoEnvio:
    """Un evento `fee` de envio del ledger, ya clasificado. Monto en positivo."""

    orden: str
    fuente: str
    importe: Importe
    dia: date


@dataclass(frozen=True)
class OrdenMedida:
    """Una orden con UN solo producto y su costo de envio."""

    orden: str
    product_id: int
    costo: Importe
    dia: date


@dataclass(frozen=True)
class Exclusiones:
    """Lo que no entro a ninguna muestra, contado (ningun silencio)."""

    ordenes_multiproducto: int
    ordenes_sin_producto: int
    cargos_otros: int


@dataclass(frozen=True)
class MuestraNueva:
    """Fila lista para `precio_envio_muestra` (sin id)."""

    product_id: int
    plataforma: str
    canal: str
    origen: Literal["propio", "familia", "marketplace"]
    valor: Importe
    envios: int
    donantes: int | None
    familia_id: int | None
    ventana: tuple[date, date]
    mediana: Importe
    p90: Importe | None
    maximo: Importe
    regla_version: str


def clasificar_cargo(source_event_id: str | None, fee_type: str | None) -> str:
    """De donde viene el cargo. Lo desconocido es `otros` y se cuenta.

    La identidad sale de `source_event_id` partido por `|`: el campo 6 si
    el campo 2 es `finance`, y si no, el campo 2 (F.0 confirma que solo hay
    seis identidades). `fee_type` distinto de `shipping_fee` es `otros`.
    """
    if fee_type is not None and fee_type != "shipping_fee":
        return "otros"
    if not source_event_id:
        return "otros"
    partes = source_event_id.split("|")
    if len(partes) < 2:
        return "otros"
    identidad = partes[5] if (partes[1] == "finance" and len(partes) >= 6) else partes[1]
    if partes[1] == "finance" and len(partes) < 6:
        return "otros"
    return _IDENTIDAD_A_FUENTE.get(identidad, "otros")


def costo_de_orden(cargos: Sequence[CargoEnvio]) -> Importe | None:
    """`max(labman_label, shipping_label, mfn_postage) + shipping_hb`.

    Las tres primeras son la etiqueta por tres caminos: se cuenta UNA vez
    (Labman y `shipping_label` son el mismo cobro). `shipping_hb` suma.
    `otros` no entra. None si la orden no trae etiqueta alguna (ni siquiera
    con HB solo). Una sola moneda o `MonedasMezcladas`.
    """
    etiquetas = [c.importe for c in cargos if c.fuente in _ETIQUETAS]
    if not etiquetas:
        return None
    base = max(etiquetas, key=lambda importe: importe.valor)
    for etiqueta in etiquetas:
        if etiqueta.moneda != base.moneda:
            raise MonedasMezcladas(f"etiquetas en moneda distinta: {etiquetas!r}")
    total = base
    for extra in [c.importe for c in cargos if c.fuente == "shipping_hb"]:
        total = total + extra
    return total


def _mediana(importes: Sequence[Importe]) -> Importe:
    primero = importes[0]
    for importe in importes[1:]:
        if importe.moneda != primero.moneda:
            raise MonedasMezcladas(f"mediana con moneda distinta: {importes!r}")
    return Importe(statistics.median([i.valor for i in importes]), primero.moneda)


def _p90(importes: Sequence[Importe]) -> Importe:
    ordenados = sorted(importes, key=lambda importe: importe.valor)
    return ordenados[math.ceil(Decimal("0.9") * len(ordenados)) - 1]


def _maximo(importes: Sequence[Importe]) -> Importe:
    return max(importes, key=lambda importe: importe.valor)


def _medianas_por_producto(ordenes: Sequence[OrdenMedida]) -> dict[int, Importe]:
    por_producto: dict[int, list[Importe]] = {}
    for orden in ordenes:
        por_producto.setdefault(orden.product_id, []).append(orden.costo)
    return {pid: _mediana(costos) for pid, costos in por_producto.items()}


def resolver_envio(
    product_id: int,
    *,
    propias: Sequence[OrdenMedida],
    hermanas: Sequence[OrdenMedida],
    familia_id: int | None,
    marketplace: Sequence[OrdenMedida],
    plataforma: str,
    canal: str,
    ventana: tuple[date, date],
) -> MuestraNueva | None:
    """UNICA escalera de origen: propio -> familia -> marketplace -> None.

    `propio` con al menos una orden (en US tambien: el dueno pidio no
    esperar). `familia`: mediana de las MEDIANAS de los hermanos, con la
    familia del producto. `marketplace`: mediana de medianas de todos los
    productos del universo. None = no hay un solo envio medido: nunca una
    constante.
    """
    if propias:
        costos = [o.costo for o in propias]
        mediana = _mediana(costos)
        return MuestraNueva(
            product_id=product_id,
            plataforma=plataforma,
            canal=canal,
            origen="propio",
            valor=mediana,
            envios=len(propias),
            donantes=None,
            familia_id=None,
            ventana=ventana,
            mediana=mediana,
            p90=_p90(costos),
            maximo=_maximo(costos),
            regla_version=REGLA_VERSION,
        )
    if hermanas and familia_id is not None:
        medianas = list(_medianas_por_producto(hermanas).values())
        mediana = _mediana(medianas)
        return MuestraNueva(
            product_id=product_id,
            plataforma=plataforma,
            canal=canal,
            origen="familia",
            valor=mediana,
            envios=len(hermanas),
            donantes=len(medianas),
            familia_id=familia_id,
            ventana=ventana,
            mediana=mediana,
            p90=_p90(medianas),
            maximo=_maximo(medianas),
            regla_version=REGLA_VERSION,
        )
    if marketplace:
        medianas = list(_medianas_por_producto(marketplace).values())
        mediana = _mediana(medianas)
        return MuestraNueva(
            product_id=product_id,
            plataforma=plataforma,
            canal=canal,
            origen="marketplace",
            valor=mediana,
            envios=len(marketplace),
            donantes=len(medianas),
            familia_id=None,
            ventana=ventana,
            mediana=mediana,
            p90=_p90(medianas),
            maximo=_maximo(medianas),
            regla_version=REGLA_VERSION,
        )
    return None
