"""Calculador puro MARGEN ESTIMADO 01 A.4 — formula S3 sin IO.

Entrada congelada + politica versionada -> contribucion, porcentaje, componentes,
estado y motivos. Sin DB, HTTP ni imports del motor Ads.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

_MAX_DECIMALES = Decimal("0.0001")
_MAX_DINERO = Decimal(10) ** 10

EXCLUSIONES_FIJAS: tuple[str, ...] = (
    "ads",
    "reembolsos",
    "almacenamiento",
    "devoluciones",
    "costos_periodicos",
    "gastos_generales",
)

ESTADOS_DESACTUALIZADA = frozenset(
    {
        "oferta_desactualizada",
        "oferta_futura",
        "costo_desactualizado",
        "costo_no_vigente",
    }
)


@dataclass(frozen=True)
class PoliticaCalculo:
    politica_version_id: int | None
    label: str
    universo: str
    formula_version: str
    settings: dict[str, Any]
    valid_from: date
    valid_to: date | None
    created_at: datetime | None


@dataclass(frozen=True)
class DetalleFee:
    fee_type: str
    final_fee: Decimal
    tax_amount: Decimal | None = None
    included_fee_details: tuple[DetalleFee, ...] = ()


@dataclass(frozen=True)
class ComponenteEconomico:
    nombre: str
    importe_original: Decimal | None = None
    moneda_original: str | None = None
    importe_normalizado: Decimal | None = None
    moneda_normalizada: str | None = None
    tasa: Decimal | None = None
    fecha: date | None = None
    fuente: str | None = None
    pertenece_a_total: bool = True


@dataclass(frozen=True)
class EntradaCalculo:
    precio_bruto: Decimal | None
    precio_moneda: str | None
    costo_original: Decimal | None
    costo_moneda: str | None
    fee_total: Decimal | None
    costo_includes_tax: bool | None = None
    fee_detalles: tuple[DetalleFee, ...] = ()
    fee_estado: str | None = None
    fx_rate: Decimal | None = None
    fx_rate_date: date | None = None
    fx_base: str | None = None
    fx_quote: str | None = None
    fx_source: str | None = None
    moneda_base: str | None = None
    motivos_entrada: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResultadoCalculo:
    estado: str
    motivos: tuple[str, ...]
    contribucion: Decimal | None
    contribucion_pct: Decimal | None
    moneda: str | None
    componentes: tuple[ComponenteEconomico, ...]
    exclusiones: tuple[str, ...]
    ingreso_normalizado: Decimal | None = None
    logistica: Decimal | None = None
    isr: Decimal | None = None


def validar_dinero(
    valor: Decimal,
    *,
    campo: str,
    positivo: bool = True,
    permitir_cero: bool = False,
) -> Decimal:
    """Decimal finito dentro de NUMERIC(14,4); positivo si se exige."""
    if not isinstance(valor, Decimal):
        raise ValueError(f"{campo} debe ser Decimal")
    if not valor.is_finite():
        raise ValueError(f"{campo} no finito")
    if abs(valor) >= _MAX_DINERO:
        raise ValueError(f"{campo} fuera de rango NUMERIC(14,4)")
    try:
        cuantizado = valor.quantize(_MAX_DECIMALES)
    except InvalidOperation as exc:
        raise ValueError(f"{campo} fuera de rango NUMERIC(14,4)") from exc
    if valor != cuantizado:
        raise ValueError(f"{campo} excede 4 decimales")
    if positivo and cuantizado <= 0:
        raise ValueError(f"{campo} no positivo")
    if not positivo and not permitir_cero and cuantizado < 0:
        raise ValueError(f"{campo} negativo")
    if permitir_cero and cuantizado < 0:
        raise ValueError(f"{campo} negativo")
    return cuantizado


def _decimal_setting(settings: dict[str, Any], clave: str) -> Decimal:
    raw = settings.get(clave)
    if raw is None:
        raise ValueError(f"politica sin {clave}")
    return Decimal(str(raw))


def _cuantizar_frontera(valor: Decimal) -> Decimal:
    return valor.quantize(_MAX_DECIMALES)


def _validar_tasa_no_negativa(valor: Decimal, *, campo: str) -> Decimal:
    if not valor.is_finite():
        raise ValueError(f"{campo} no finito")
    if abs(valor) >= _MAX_DINERO:
        raise ValueError(f"{campo} fuera de rango")
    if valor < 0:
        raise ValueError(f"{campo} negativo")
    return valor


def _tiene_tax_amount(detalle: DetalleFee) -> bool:
    if detalle.tax_amount is not None and detalle.tax_amount != Decimal("0"):
        return True
    return any(_tiene_tax_amount(h) for h in detalle.included_fee_details)


def _suma_final_fee_top_level(detalles: tuple[DetalleFee, ...]) -> Decimal:
    return sum((d.final_fee for d in detalles), Decimal("0"))


def _validar_fee_detalles(
    fee_total: Decimal | None,
    detalles: tuple[DetalleFee, ...],
    *,
    fee_estado: str | None,
) -> str | None:
    if fee_total is None:
        return None
    if fee_estado != "success":
        return None
    try:
        total = validar_dinero(fee_total, campo="fee_total", positivo=False, permitir_cero=True)
    except ValueError:
        return "fee_invalido"

    def validar_detalle(det: DetalleFee) -> bool:
        try:
            validar_dinero(det.final_fee, campo="fee_detalle", positivo=False, permitir_cero=True)
            if det.tax_amount is not None:
                validar_dinero(
                    det.tax_amount,
                    campo="fee_tax_amount",
                    positivo=False,
                    permitir_cero=True,
                )
        except ValueError:
            return False
        return all(validar_detalle(inc) for inc in det.included_fee_details)

    if not all(validar_detalle(det) for det in detalles):
        return "fee_incompatible"
    if not detalles:
        return "fee_incompatible"
    suma_top = _suma_final_fee_top_level(detalles)
    if suma_top != total:
        return "fee_incompatible"
    return None


def _validar_politica(
    politica: PoliticaCalculo,
    fecha: date,
    *,
    universo_esperado: str | None = None,
) -> str | None:
    if politica.valid_from > fecha:
        return "politica_no_vigente"
    if politica.valid_to is not None and politica.valid_to <= fecha:
        return "politica_no_vigente"
    if universo_esperado is not None and politica.universo != universo_esperado:
        return "politica_invalida"
    if politica.formula_version != "S3":
        return "politica_invalida"
    settings = politica.settings
    if settings.get("precio_incluye_iva") not in (True, False):
        return "politica_invalida"
    if settings.get("fee_tax_amount_requiere_politica") is not True:
        return "politica_invalida"
    try:
        divisor = _decimal_setting(settings, "iva_divisor")
        if not divisor.is_finite() or divisor <= 0 or divisor >= _MAX_DINERO:
            return "politica_invalida"
        isr_tasa = _validar_tasa_no_negativa(
            _decimal_setting(settings, "isr_tasa"), campo="isr_tasa"
        )
        logistica = _validar_tasa_no_negativa(
            _decimal_setting(settings, "logistica"), campo="logistica"
        )
        ret_iva = _validar_tasa_no_negativa(
            _decimal_setting(settings, "retencion_iva_reconciliacion"),
            campo="retencion_iva_reconciliacion",
        )
        validar_dinero(isr_tasa, campo="isr_tasa", positivo=False, permitir_cero=True)
        validar_dinero(logistica, campo="logistica", positivo=False, permitir_cero=True)
        validar_dinero(
            ret_iva, campo="retencion_iva_reconciliacion", positivo=False, permitir_cero=True
        )
        if logistica == Decimal("0"):
            sem = settings.get("logistica_semantica")
            if not isinstance(sem, str) or not sem.strip():
                return "politica_invalida"
    except (ValueError, InvalidOperation):
        return "politica_invalida"
    return None


def _normalizar_costo(
    entrada: EntradaCalculo,
    moneda_venta: str,
    motivos: list[str],
    componentes: list[ComponenteEconomico],
) -> Decimal | None:
    if entrada.costo_original is None or entrada.costo_moneda is None:
        motivos.append("costo_ausente")
        return None
    try:
        costo_orig = validar_dinero(entrada.costo_original, campo="costo", positivo=True)
    except ValueError:
        motivos.append("costo_invalido")
        return None

    if entrada.costo_includes_tax is not False:
        motivos.append(
            "costo_impuesto_incompatible"
            if entrada.costo_includes_tax is True
            else "costo_base_fiscal_ausente"
        )

    componentes.append(
        ComponenteEconomico(
            nombre="costo_original",
            importe_original=costo_orig,
            moneda_original=entrada.costo_moneda,
        )
    )

    if entrada.costo_moneda == moneda_venta:
        componentes.append(
            ComponenteEconomico(
                nombre="costo_normalizado",
                importe_original=costo_orig,
                moneda_original=entrada.costo_moneda,
                importe_normalizado=costo_orig,
                moneda_normalizada=moneda_venta,
            )
        )
        return costo_orig

    if entrada.fx_rate is None or entrada.fx_base is None or entrada.fx_quote is None:
        if "fx_ausente" not in motivos:
            motivos.append("fx_ausente")
        return None
    if entrada.fx_base != moneda_venta or entrada.fx_quote != entrada.costo_moneda:
        motivos.append("fx_direccion_invalida")
        return None
    tasa = entrada.fx_rate
    if (
        not isinstance(tasa, Decimal)
        or not tasa.is_finite()
        or tasa <= 0
        or tasa >= Decimal(10) ** 10
        or tasa != tasa.quantize(Decimal("0.00000001"))
    ):
        motivos.append("fx_tasa_invalida")
        return None

    costo_norm_exacto = costo_orig / tasa
    costo_norm = _cuantizar_frontera(costo_norm_exacto)
    componentes.append(
        ComponenteEconomico(
            nombre="fx",
            tasa=tasa.quantize(Decimal("0.00000001")),
            fecha=entrada.fx_rate_date,
            fuente=entrada.fx_source,
            moneda_original=entrada.costo_moneda,
            moneda_normalizada=moneda_venta,
        )
    )
    componentes.append(
        ComponenteEconomico(
            nombre="costo_normalizado",
            importe_original=costo_orig,
            moneda_original=entrada.costo_moneda,
            importe_normalizado=costo_norm,
            moneda_normalizada=moneda_venta,
            tasa=tasa.quantize(Decimal("0.00000001")),
            fecha=entrada.fx_rate_date,
            fuente=entrada.fx_source,
        )
    )
    return costo_norm_exacto


def calcular_contribucion(
    entrada: EntradaCalculo,
    politica: PoliticaCalculo,
    *,
    valoracion_date: date,
    universo_esperado: str | None = None,
) -> ResultadoCalculo:
    """Formula S3: I - C - F - L - R; retencion IVA solo conciliacion."""
    motivos: list[str] = list(entrada.motivos_entrada)
    componentes: list[ComponenteEconomico] = []
    exclusiones = EXCLUSIONES_FIJAS

    motivo_pol = _validar_politica(politica, valoracion_date, universo_esperado=universo_esperado)
    if motivo_pol:
        motivos.append(motivo_pol)

    if "identidad_ambigua" in motivos:
        return ResultadoCalculo(
            estado="identidad_ambigua",
            motivos=tuple(dict.fromkeys(motivos)),
            contribucion=None,
            contribucion_pct=None,
            moneda=None,
            componentes=tuple(componentes),
            exclusiones=exclusiones,
        )

    if any(m in ESTADOS_DESACTUALIZADA for m in motivos):
        return ResultadoCalculo(
            estado="desactualizada",
            motivos=tuple(dict.fromkeys(motivos)),
            contribucion=None,
            contribucion_pct=None,
            moneda=None,
            componentes=tuple(componentes),
            exclusiones=exclusiones,
        )

    if motivo_pol:
        return ResultadoCalculo(
            estado="incompleta",
            motivos=tuple(dict.fromkeys(motivos)),
            contribucion=None,
            contribucion_pct=None,
            moneda=None,
            componentes=tuple(componentes),
            exclusiones=exclusiones,
        )

    moneda_venta = entrada.moneda_base or entrada.precio_moneda

    ingreso_exacto: Decimal | None = None
    ingreso: Decimal | None = None
    if entrada.precio_bruto is None or entrada.precio_moneda is None:
        motivos.append("precio_ausente")
    else:
        try:
            precio = validar_dinero(entrada.precio_bruto, campo="precio", positivo=True)
        except ValueError:
            motivos.append("precio_invalido")
            precio = None
        if precio is not None:
            componentes.append(
                ComponenteEconomico(
                    nombre="precio_bruto",
                    importe_original=precio,
                    moneda_original=entrada.precio_moneda,
                    importe_normalizado=precio,
                    moneda_normalizada=entrada.precio_moneda,
                )
            )
            if politica.settings.get("precio_incluye_iva"):
                divisor = _decimal_setting(politica.settings, "iva_divisor")
                ingreso_exacto = precio / divisor
            else:
                ingreso_exacto = precio
            ingreso = _cuantizar_frontera(ingreso_exacto)
            if moneda_venta is None:
                moneda_venta = entrada.precio_moneda
            componentes.append(
                ComponenteEconomico(
                    nombre="ingreso_normalizado",
                    importe_normalizado=ingreso,
                    moneda_normalizada=moneda_venta,
                )
            )

    fee_exacto: Decimal | None = None
    fee: Decimal | None = None
    if entrada.fee_total is None or entrada.fee_estado != "success":
        motivos.append("fee_ausente")
    else:
        for det in entrada.fee_detalles:
            if _tiene_tax_amount(det):
                motivos.append("impuesto_fee_pendiente")
                break
        motivo_fee_det = _validar_fee_detalles(
            entrada.fee_total,
            entrada.fee_detalles,
            fee_estado=entrada.fee_estado,
        )
        if motivo_fee_det:
            motivos.append(motivo_fee_det)
        try:
            fee_exacto = validar_dinero(
                entrada.fee_total,
                campo="fee_total",
                positivo=False,
                permitir_cero=True,
            )
            fee = fee_exacto
        except ValueError:
            motivos.append("fee_invalido")
            fee_exacto = None
            fee = None
        if fee is not None:
            componentes.append(
                ComponenteEconomico(
                    nombre="fee_total",
                    importe_normalizado=fee,
                    moneda_normalizada=moneda_venta,
                )
            )
            for det in entrada.fee_detalles:
                componentes.append(
                    ComponenteEconomico(
                        nombre=f"fee_detalle:{det.fee_type}",
                        importe_normalizado=det.final_fee,
                        moneda_normalizada=moneda_venta,
                        pertenece_a_total=False,
                    )
                )

    costo_exacto = _normalizar_costo(entrada, moneda_venta or "", motivos, componentes)

    logistica = _decimal_setting(politica.settings, "logistica")
    isr_tasa = _decimal_setting(politica.settings, "isr_tasa")
    ret_iva_tasa = _decimal_setting(politica.settings, "retencion_iva_reconciliacion")

    isr_exacto: Decimal | None = None
    isr: Decimal | None = None
    ret_iva: Decimal | None = None
    if ingreso_exacto is not None:
        isr_exacto = ingreso_exacto * isr_tasa
        isr = _cuantizar_frontera(isr_exacto)
        ret_iva = _cuantizar_frontera(ingreso_exacto * ret_iva_tasa)
        componentes.append(
            ComponenteEconomico(
                nombre="logistica",
                importe_normalizado=_cuantizar_frontera(logistica),
                moneda_normalizada=moneda_venta,
            )
        )
        componentes.append(
            ComponenteEconomico(
                nombre="isr",
                importe_normalizado=isr,
                moneda_normalizada=moneda_venta,
            )
        )
        componentes.append(
            ComponenteEconomico(
                nombre="retencion_iva_conciliacion",
                importe_normalizado=ret_iva,
                moneda_normalizada=moneda_venta,
                pertenece_a_total=False,
            )
        )

    bloqueantes = {
        "precio_ausente",
        "precio_invalido",
        "costo_ausente",
        "costo_invalido",
        "costo_impuesto_incompatible",
        "costo_base_fiscal_ausente",
        "fee_ausente",
        "fee_invalido",
        "fx_ausente",
        "fx_direccion_invalida",
        "fx_tasa_invalida",
        "impuesto_fee_pendiente",
        "politica_no_vigente",
        "politica_invalida",
        "fee_incompatible",
        "politica_ausente",
        "politica_ambigua",
    }
    if any(m in bloqueantes for m in motivos):
        return ResultadoCalculo(
            estado="incompleta",
            motivos=tuple(dict.fromkeys(motivos)),
            contribucion=None,
            contribucion_pct=None,
            moneda=None,
            componentes=tuple(componentes),
            exclusiones=exclusiones,
            ingreso_normalizado=ingreso,
            logistica=logistica,
            isr=isr,
        )

    assert (
        ingreso_exacto is not None
        and costo_exacto is not None
        and fee_exacto is not None
        and moneda_venta
    )

    contrib_exacto = (
        ingreso_exacto - costo_exacto - fee_exacto - logistica - (isr_exacto or Decimal("0"))
    )
    contrib = _cuantizar_frontera(contrib_exacto)
    pct = _cuantizar_frontera(contrib_exacto * Decimal("100") / ingreso_exacto)

    return ResultadoCalculo(
        estado="disponible",
        motivos=(),
        contribucion=contrib,
        contribucion_pct=pct,
        moneda=moneda_venta,
        componentes=tuple(componentes),
        exclusiones=exclusiones,
        ingreso_normalizado=ingreso,
        logistica=logistica,
        isr=isr,
    )
