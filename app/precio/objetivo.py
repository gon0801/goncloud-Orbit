"""Objetivo de precio por forma cerrada (REPRICING 01 A.2, S4 #3 y #11).

De `contribucion = I - C - F - L - R` con `I = P / d` (o `I = P` donde no
hay impuesto al precio), `R = r * I`, `F = ref * P + fijo` y
`contribucion / I = goal` sale:

    P* = (C + fijo + L) / ((1 - r - goal) / d - ref)

Denominador <= 0 -> `goal_inalcanzable(margen_imposible)`, sin dividir.

`ref` y `fijo` se derivan de `fee_details` (residual del brief, va al PR):
`ref = final_fee(ReferralFee) / P_cotizado`,
`fijo = fee_total - final_fee(ReferralFee)`. Exactamente un `ReferralFee`
con `final_fee > 0`; si falta, hay mas de uno o `P_cotizado <= 0` ->
`fee_error:referral_ausente`. Nunca un cero.

La maquina `paso` es pura: recibe las cotizaciones que YA existen (cero,
una o dos) y devuelve o bien `PideCotizacion(precio)` o bien el resultado
final. Nunca pide una tercera. `TaxAmount != 0` en cualquiera ->
`no_evaluado(impuesto_fee_pendiente)`; en error o sin conciliar ->
`no_evaluado(fee_error:<code>)`.

Redondeo (S4 #3 y #4): `P*` hacia arriba (`ROUND_CEILING`); el tope del
escalon `P_actual * (1 + escalon)` hacia abajo (`ROUND_FLOOR`) y el piso
hacia arriba.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from app.estimacion_venta import DetalleFee
from app.precio.tipos import CotizacionVerificada, Importe, PideCotizacion, exigir_decimal

__all__ = [
    "ErrorObjetivo",
    "ResultadoObjetivo",
    "derivar_ref_fijo",
    "precio_estrella",
    "margen_a_precio",
    "techo_centavo",
    "piso_centavo",
    "paso",
]

_CENTAVO = Decimal("0.01")


class ErrorObjetivo(Exception):
    """La forma cerrada no cierra: el motivo exacto viaja en el mensaje."""

    def __init__(self, motivo: str) -> None:
        super().__init__(motivo)
        self.motivo = motivo


@dataclass(frozen=True)
class ResultadoObjetivo:
    """Salida final de la maquina: verificado, no_evaluado o inalcanzable."""

    resultado: str
    motivo: str | None
    precio: Importe | None
    ref: Decimal
    fijo: Decimal

    def __post_init__(self) -> None:
        if self.resultado not in ("verificado", "no_evaluado", "goal_inalcanzable"):
            raise ValueError(f"resultado objetivo invalido: {self.resultado!r}")


def _tiene_tax(detalles: tuple[DetalleFee, ...]) -> bool:
    return any(
        (det.tax_amount is not None and det.tax_amount != Decimal(0))
        or _tiene_tax(det.included_fee_details)
        for det in detalles
    )


def _suma_top(detalles: tuple[DetalleFee, ...]) -> Decimal:
    return sum((det.final_fee for det in detalles), Decimal(0))


def derivar_ref_fijo(
    detalles: tuple[DetalleFee, ...],
    fee_total: Decimal,
    p_cotizado: Decimal,
) -> tuple[Decimal, Decimal]:
    """`(ref, fijo)` desde `fee_details`; sin referral unico -> `ErrorObjetivo`."""
    exigir_decimal(fee_total, campo="fee_total")
    exigir_decimal(p_cotizado, campo="p_cotizado")
    referrals = [det for det in detalles if det.fee_type == "ReferralFee" and det.final_fee > 0]
    if len(referrals) != 1 or p_cotizado <= 0:
        raise ErrorObjetivo("fee_error:referral_ausente")
    ref = referrals[0].final_fee / p_cotizado
    return (ref, fee_total - referrals[0].final_fee)


def precio_estrella(
    costo: Decimal,
    fijo: Decimal,
    envio: Decimal,
    isr_tasa: Decimal,
    goal: Decimal,
    iva_divisor: Decimal,
    incluye_iva: bool,
    ref: Decimal,
) -> Decimal:
    """`P*` por forma cerrada; denominador <= 0 -> `margen_imposible`."""
    for campo, valor in (
        ("costo", costo),
        ("fijo", fijo),
        ("envio", envio),
        ("isr_tasa", isr_tasa),
        ("goal", goal),
        ("iva_divisor", iva_divisor),
        ("ref", ref),
    ):
        exigir_decimal(valor, campo=campo)
    divisor = iva_divisor if incluye_iva else Decimal(1)
    denominador = (Decimal(1) - isr_tasa - goal) / divisor - ref
    if denominador <= 0:
        raise ErrorObjetivo("margen_imposible")
    return (costo + fijo + envio) / denominador


def margen_a_precio(
    precio: Decimal,
    costo: Decimal,
    ref: Decimal,
    fijo: Decimal,
    envio: Decimal,
    isr_tasa: Decimal,
    iva_divisor: Decimal,
    incluye_iva: bool,
) -> Decimal:
    """`m(P) = contribucion / I` con la forma cerrada (`F = ref * P + fijo`)."""
    exigir_decimal(precio, campo="precio")
    divisor = iva_divisor if incluye_iva else Decimal(1)
    ingreso = precio / divisor
    fees = ref * precio + fijo
    contribucion = ingreso - costo - fees - envio - isr_tasa * ingreso
    return contribucion / ingreso


def techo_centavo(precio: Decimal) -> Decimal:
    """Redondeo al centavo hacia arriba (S4 #3: `P*`; S4 #4: `P_goal`, piso)."""
    return exigir_decimal(precio, campo="precio").quantize(_CENTAVO, rounding=ROUND_CEILING)


def piso_centavo(precio: Decimal) -> Decimal:
    """Redondeo al centavo hacia abajo (S4 #3: tope del escalon)."""
    return exigir_decimal(precio, campo="precio").quantize(_CENTAVO, rounding=ROUND_FLOOR)


def _revisar_cotizacion(cot: CotizacionVerificada, moneda: str) -> str | None:
    """Motivo exacto si la cotizacion no sirve; `None` si sirve."""
    if cot.precio.moneda != moneda:
        raise ValueError(f"cotizacion en {cot.precio.moneda}, se esperaba {moneda}")
    if cot.estado == "error":
        return f"fee_error:{cot.error_code or 'desconocido'}"
    if _tiene_tax(cot.detalles):
        return "impuesto_fee_pendiente"
    if _suma_top(cot.detalles) != cot.fee_total:
        return "fee_error:fee_no_concilia"
    return None


def paso(
    *,
    costo: Decimal,
    fijo: Decimal,
    envio: Decimal,
    isr_tasa: Decimal,
    goal: Decimal,
    iva_divisor: Decimal,
    incluye_iva: bool,
    ref: Decimal,
    moneda: str,
    tolerancia: Decimal,
    cotizaciones: tuple[CotizacionVerificada, ...],
) -> PideCotizacion | ResultadoObjetivo:
    """Un paso de la maquina de cotizaciones (cero, una o dos ya hechas)."""
    exigir_decimal(tolerancia, campo="tolerancia")
    if len(cotizaciones) > 2:
        raise ValueError("la maquina nunca recibe mas de dos cotizaciones")
    for cot in cotizaciones:
        motivo = _revisar_cotizacion(cot, moneda)
        if motivo is not None:
            return ResultadoObjetivo("no_evaluado", motivo, None, ref, fijo)
    if not cotizaciones:
        try:
            estrella = precio_estrella(
                costo, fijo, envio, isr_tasa, goal, iva_divisor, incluye_iva, ref
            )
        except ErrorObjetivo as exc:
            return ResultadoObjetivo("goal_inalcanzable", exc.motivo, None, ref, fijo)
        return PideCotizacion(Importe(techo_centavo(estrella), moneda), 1)
    primera = cotizaciones[0]
    try:
        ref1, fijo1 = derivar_ref_fijo(primera.detalles, primera.fee_total, primera.precio.valor)
    except ErrorObjetivo as exc:
        return ResultadoObjetivo("no_evaluado", exc.motivo, None, ref, fijo)
    m1 = margen_a_precio(
        primera.precio.valor, costo, ref1, fijo1, envio, isr_tasa, iva_divisor, incluye_iva
    )
    if abs(m1 - goal) <= tolerancia:
        return ResultadoObjetivo("verificado", None, primera.precio, ref1, fijo1)
    if len(cotizaciones) == 1:
        try:
            estrella = precio_estrella(
                costo, fijo1, envio, isr_tasa, goal, iva_divisor, incluye_iva, ref1
            )
        except ErrorObjetivo as exc:
            return ResultadoObjetivo("goal_inalcanzable", exc.motivo, None, ref1, fijo1)
        return PideCotizacion(Importe(techo_centavo(estrella), moneda), 2)
    segunda = cotizaciones[1]
    try:
        ref2, fijo2 = derivar_ref_fijo(segunda.detalles, segunda.fee_total, segunda.precio.valor)
    except ErrorObjetivo as exc:
        return ResultadoObjetivo("no_evaluado", exc.motivo, None, ref1, fijo1)
    m2 = margen_a_precio(
        segunda.precio.valor, costo, ref2, fijo2, envio, isr_tasa, iva_divisor, incluye_iva
    )
    if abs(m2 - goal) <= tolerancia:
        return ResultadoObjetivo("verificado", None, segunda.precio, ref2, fijo2)
    return ResultadoObjetivo("goal_inalcanzable", "fee_no_lineal", None, ref2, fijo2)
