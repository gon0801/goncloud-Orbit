"""Tipos del motor de precios (REPRICING 01 A.2, fila A.2 del plan).

Vocabularios cerrados y contenedores puros. Sin I/O, sin reloj: toda fecha
entra como argumento. Dinero = (valor, moneda); el valor siempre es
`Decimal` — un `float` revienta con `TypeError`, nunca se convierte.

Convenciones (residuales declarados del brief, van al PR):
- `goal` y `m_actual` son FRACCIONES sobre `I` (`0.30` = 30%). La
  herramienta A.1 recibe `--goal-pct 30.00`; la conversion vive ahi, no aqui.
- `ref` y `fijo` NO existen en `fee_details`: se derivan
  (`ref = final_fee(ReferralFee) / P_cotizado`,
  `fijo = fee_total - final_fee(ReferralFee)`). Exactamente un `ReferralFee`
  con `final_fee > 0`; si falta, hay mas de uno o `P_cotizado <= 0` ->
  `no_evaluado(fee_error:referral_ausente)`. Nunca un cero.
- Motivo con parametro usa `base:submotivo` (`ventas_sin_dato:racha_incompleta`,
  `fee_error:fee_http_429`). Los submotivos de `sin_dato` los nombra este
  modulo, uno distinto por causa, en vocabulario cerrado.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from app.estimacion_venta import DetalleFee

__all__ = [
    "RESULTADOS",
    "MOTIVOS_MANTENER",
    "MOTIVOS_NO_EVALUADO",
    "MOTIVOS_GOAL_INALCANZABLE",
    "MOTIVOS_FRENADO",
    "SUBMOTIVOS_SIN_DATO",
    "Importe",
    "Componentes",
    "Escenario",
    "ObservacionPricing",
    "CambioPrevio",
    "HistorialMargen",
    "VentasInsumos",
    "SenalVentas",
    "CotizacionVerificada",
    "PideCotizacion",
    "EntradaDecision",
    "Decision",
    "exigir_decimal",
    "motivo_permitido",
]

RESULTADOS = (
    "subir",
    "bajar",
    "mantener",
    "no_evaluado",
    "goal_inalcanzable",
    "frenado",
)

MOTIVOS_MANTENER = frozenset(
    {
        "en_tolerancia",
        "sobre_goal_sin_perdida",
        "ventas_sin_dato",
        "movimiento_minimo",
        "cuota",
        "cooldown",
    }
)

MOTIVOS_NO_EVALUADO = frozenset(
    {
        # Los que ya declara la estimacion (S2, pasan tal cual).
        "oferta_desactualizada",
        "oferta_futura",
        "costo_desactualizado",
        "costo_no_vigente",
        "fx_ausente",
        "fee_ausente",
        "impuesto_fee_pendiente",
        "fee_incompatible",
        "fee_invalido",
        "precio_ausente",
        "precio_invalido",
        "costo_ausente",
        "costo_invalido",
        "costo_impuesto_incompatible",
        "costo_base_fiscal_ausente",
        "fx_direccion_invalida",
        "fx_tasa_invalida",
        "politica_ausente",
        "politica_ambigua",
        "politica_no_vigente",
        "politica_invalida",
        "identidad_ambigua",
        # Motivo de estimacion futuro o de otra fuente: nunca revienta la
        # corrida (r2-A1, decision 11 «ningun silencio»); el crudo viaja en
        # `diagnostico` y lo pone quien llama, no este vocabulario.
        "estimacion_motivo_desconocido",
        # Coherencia S2 (precios y horas viajan en el diagnostico).
        "precio_divergente",
        "moneda_divergente",
        "precio_sin_observar",
        # Cotizacion del motor (S4 #3, S5): `fee_error:<code>`.
        "fee_error",
        # Ingreso no positivo en el escenario (residual: no deberia pasar en
        # un escenario `disponible`; sin I no hay margen que calcular).
        "ingreso_no_positivo",
        # Componentes que no cuadran entre si (r1-A1, decision del lead, va
        # declarada al PR): precio cotizado distinto del actual, fees que no
        # suman F, I o R fuera de formula. El que falla viaja en `diagnostico`.
        "escenario_incoherente",
    }
)

MOTIVOS_GOAL_INALCANZABLE = frozenset(
    {
        "margen_imposible",
        "precio_mayor_al_doble",
        "precio_no_cubre_costo",
        "fee_no_lineal",
    }
)

MOTIVOS_FRENADO = frozenset(
    {
        "perdiendo_tras_subida",
        "no_converge",
    }
)

SUBMOTIVOS_SIN_DATO = frozenset(
    {
        "ledger_hueco",
        "u60_bajo_minimo",
        "n15_insuficiente",
        "historia_corta",
        "dia_sin_stock",
        "dia_sin_observacion_inventario",
        "listing_inactivo",
        "dia_sin_estado_listing",
        "ventana_60_excluida",
        "racha_incompleta",
    }
)


def exigir_decimal(valor: object, *, campo: str) -> Decimal:
    """Decimal finito o revienta: `float`/`int`/`str` -> `TypeError`."""
    if not isinstance(valor, Decimal):
        raise TypeError(f"{campo} debe ser Decimal, llego {type(valor).__name__}")
    if not valor.is_finite():
        raise ValueError(f"{campo} no finito")
    return valor


@dataclass(frozen=True)
class Importe:
    """Dinero con su moneda (regla 4 de Orbit)."""

    valor: Decimal
    moneda: str

    def __post_init__(self) -> None:
        exigir_decimal(self.valor, campo="importe.valor")
        if not isinstance(self.moneda, str) or not self.moneda.strip():
            raise ValueError("importe.moneda vacia")


@dataclass(frozen=True)
class Componentes:
    """P, I, C, F, L, R ya extraidos y tipados del escenario (S2)."""

    p_actual: Importe
    ingreso: Importe
    costo: Importe
    fees: Importe
    envio: Importe
    isr: Importe


@dataclass(frozen=True)
class Escenario:
    """Escenario vigente tal cual: el motor no recalcula nada (S2)."""

    componentes: Componentes
    fee_detalles: tuple[DetalleFee, ...]
    precio_cotizado: Importe
    iva_divisor: Decimal
    isr_tasa: Decimal
    precio_incluye_iva: bool
    oferta_observada_en: datetime

    def __post_init__(self) -> None:
        exigir_decimal(self.iva_divisor, campo="escenario.iva_divisor")
        exigir_decimal(self.isr_tasa, campo="escenario.isr_tasa")
        if self.iva_divisor <= 0:
            raise ValueError("escenario.iva_divisor no positivo")
        if self.isr_tasa < 0:
            raise ValueError("escenario.isr_tasa negativa")


@dataclass(frozen=True)
class ObservacionPricing:
    """Fila del dia de `spapi_price_observation` (control S2)."""

    precio: Importe
    observada_en: datetime


@dataclass(frozen=True)
class CambioPrevio:
    """Fila `precio_cambio` no-reversa ya ocurrida (reglas #6, #9).

    `aplicado=False` = cambio virtual de `shadow` (S4 #13): en `live` no
    cuenta para #6, #9 ni #10; en `shadow` cuentan todos. El default `True`
    conserva las filas reales.
    """

    enviado_en: date
    direccion: str | None
    estado: str
    es_reversa: bool = False
    aplicado: bool = True

    def __post_init__(self) -> None:
        if self.direccion is not None and self.direccion not in ("subir", "bajar"):
            raise ValueError(f"cambio.direccion invalida: {self.direccion!r}")


@dataclass(frozen=True)
class HistorialMargen:
    """Distancia `|m_actual - goal|` al momento de cada cambio previo (#10).

    `aplicado` igual que en `CambioPrevio`: el virtual no frena en `live`.
    `fecha` = día del cambio (r1-B3: para los frenos solo cuentan los
    puntos con fecha `≥ goal_vigente_desde`).
    """

    direccion: str
    distancia: Decimal
    fecha: date
    aplicado: bool = True

    def __post_init__(self) -> None:
        if self.direccion not in ("subir", "bajar"):
            raise ValueError(f"historial.direccion invalida: {self.direccion!r}")
        exigir_decimal(self.distancia, campo="historial.distancia")
        if self.distancia < 0:
            raise ValueError("historial.distancia negativa")


@dataclass(frozen=True)
class VentasInsumos:
    """Filas ya leidas para la senal de ventas (S4 #5), por producto."""

    ventas: tuple[tuple[date, int], ...]
    inventario: tuple[tuple[date, int | None], ...]
    listing_activo: tuple[tuple[date, bool], ...]
    primera_venta: date | None
    dia_cubierto_hasta: date | None


@dataclass(frozen=True)
class SenalVentas:
    """Salida de `ventas.py`: `perdiendo` solo con racha completa."""

    estado: str
    submotivo: str | None
    u15: int
    u60: int
    n15: int
    n60: int
    racha: int

    def __post_init__(self) -> None:
        if self.estado not in ("perdiendo", "no_perdiendo", "sin_dato"):
            raise ValueError(f"senal.estado invalido: {self.estado!r}")
        if self.estado == "sin_dato":
            if self.submotivo not in SUBMOTIVOS_SIN_DATO:
                raise ValueError(f"senal.submotivo fuera de vocabulario: {self.submotivo!r}")
        elif self.submotivo is not None:
            raise ValueError("senal con submotivo fuera de sin_dato")
        for campo in ("u15", "u60", "n15", "n60", "racha"):
            valor = getattr(self, campo)
            if not isinstance(valor, int) or isinstance(valor, bool) or valor < 0:
                raise ValueError(f"senal.{campo} invalido: {valor!r}")


@dataclass(frozen=True)
class CotizacionVerificada:
    """Una cotizacion del motor ya ocurrida (maquina de `objetivo.py`)."""

    precio: Importe
    detalles: tuple[DetalleFee, ...]
    fee_total: Decimal
    estado: str
    error_code: str | None = None

    def __post_init__(self) -> None:
        exigir_decimal(self.fee_total, campo="cotizacion.fee_total")
        if self.estado not in ("success", "error"):
            raise ValueError(f"cotizacion.estado invalido: {self.estado!r}")


@dataclass(frozen=True)
class PideCotizacion:
    """La maquina pide cotizar a este precio; nunca una tercera (S4 #3)."""

    precio: Importe
    intento: int

    def __post_init__(self) -> None:
        if self.intento not in (1, 2):
            raise ValueError(f"pide_cotizacion.intento invalido: {self.intento!r}")


@dataclass(frozen=True)
class EntradaDecision:
    """Todo lo que `reglas.py` necesita, ya leido y tipado.

    `buy_box_is_own` (S4 #7) solo se REGISTRA: perder la Buy Box no frena
    ni revierte (eso es A.6 con `buy_box_perdida` en flanco). En MeLi sin
    equivalente observable queda nula y se declara, no se inventa.
    """

    listing_id: int
    platform: str
    product_id: int
    canal: str
    mode: str
    goal: Decimal
    escenario: Escenario
    pricing: ObservacionPricing | None
    senal: SenalVentas
    ingreso_60d: Importe | None
    cambios: tuple[CambioPrevio, ...]
    historial: tuple[HistorialMargen, ...]
    goal_vigente_desde: date
    motivo_estimacion: str | None = None
    buy_box_is_own: bool | None = None

    def __post_init__(self) -> None:
        exigir_decimal(self.goal, campo="entrada.goal")
        if self.mode not in ("shadow", "live"):
            raise ValueError(f"entrada.mode invalido: {self.mode!r}")


@dataclass(frozen=True)
class Decision:
    """Lo que la fila `precio_decision` necesita (la escribe A.5, no A.2)."""

    resultado: str
    motivo: str | None
    m_actual: Decimal | None
    goal: Decimal
    p_actual: Importe | None
    p_objetivo: Importe | None
    p_aplicado: Importe | None
    componentes: Componentes | None
    u15: int | None
    u60: int | None
    n15: int | None
    n60: int | None
    racha: int | None
    perdiendo: bool | None
    prioridad: Decimal | None
    aplicado: bool
    mode: str
    buy_box_is_own: bool | None = None
    diagnostico: str = ""

    def __post_init__(self) -> None:
        if self.resultado not in RESULTADOS:
            raise ValueError(f"decision.resultado invalido: {self.resultado!r}")
        if not motivo_permitido(self.resultado, self.motivo):
            raise ValueError(f"motivo {self.motivo!r} fuera de vocabulario para {self.resultado}")


def motivo_permitido(resultado: str, motivo: str | None) -> bool:
    """Vocabulario cerrado de S4: base exacta y submotivo exacto."""
    if resultado in ("subir", "bajar"):
        return motivo is None
    if not isinstance(motivo, str) or not motivo:
        return False
    base, _, sub = motivo.partition(":")
    if resultado == "mantener":
        if base not in MOTIVOS_MANTENER:
            return False
        if base == "ventas_sin_dato":
            return sub in SUBMOTIVOS_SIN_DATO
        return not sub
    if resultado == "no_evaluado":
        if base not in MOTIVOS_NO_EVALUADO:
            return False
        if base == "fee_error":
            return bool(sub)
        return not sub
    if resultado == "goal_inalcanzable":
        return motivo in MOTIVOS_GOAL_INALCANZABLE
    if resultado == "frenado":
        return motivo in MOTIVOS_FRENADO
    return False
