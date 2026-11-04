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

from app.estimacion_venta import MOTIVOS_ESTIMACION, DetalleFee

__all__ = [
    "RESULTADOS",
    "MOTIVOS_MANTENER",
    "MOTIVOS_NO_EVALUADO",
    "MOTIVOS_GOAL_INALCANZABLE",
    "MOTIVOS_FRENADO",
    "SUBMOTIVOS_SIN_DATO",
    "CAUSAS_RETENCION",
    "EVIDENCIAS_SENAL",
    "VERIFICACIONES",
    "MonedasMezcladas",
    "Importe",
    "TasaFx",
    "Convertido",
    "convertir",
    "MuestraEnvio",
    "EnvioIncluido",
    "EnvioPropio",
    "EnvioImputado",
    "Fees",
    "Tasas",
    "Cuenta",
    "Componentes",
    "Escenario",
    "ObservacionPricing",
    "CambioPrevio",
    "HistorialMargen",
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
        "cooldown",
    }
)

_MOTIVOS_PROPIOS_DEL_MOTOR = frozenset(
    {
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
        # REPRICING 02: universos y envio (F y M traen los datos).
        "envio_sin_historia",
        "variante_sin_mapear",
        "fx_desactualizado",
        # REPRICING 02 (S.1): el goal cambio a media tarde y la cotizacion
        # guardada ya no corresponde a esta decision.
        "cotizacion_de_otro_goal",
    }
)

# REPRICING 02 0.a: la estimacion es duena de sus motivos
# (`app.estimacion_venta.MOTIVOS_ESTIMACION`); el motor aporta los suyos.
MOTIVOS_NO_EVALUADO = frozenset(MOTIVOS_ESTIMACION) | _MOTIVOS_PROPIOS_DEL_MOTOR

CAUSAS_RETENCION = frozenset(
    {
        "movimiento_masivo",
        "insumo_sistemico",
        "apagador",
        "corte_errores",
        "goal_cambiado",
    }
)

EVIDENCIAS_SENAL = frozenset({"unidades", "trafico"})

VERIFICACIONES = frozenset({"cotizada", "lineal"})

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
        # r1-A (S6): los frenos que decide la corrida (A.2 no los trajo
        # porque esos frenos los decide la corrida, no las reglas puras).
        "api_error",
        "no_confirmado",
    }
)

SUBMOTIVOS_SIN_DATO = frozenset(
    {
        "ledger_hueco",
        "u60_bajo_minimo",
        "n15_insuficiente",
        "historia_corta",
        "dia_no_disponible",
        "dia_sin_observacion",
        "venta_sin_publicacion",
        "evidencia_insuficiente",
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


class MonedasMezcladas(TypeError):
    """Se intento operar dos importes de moneda distinta. Nunca se convierte
    solo: la unica puerta entre monedas es `convertir`."""


@dataclass(frozen=True)
class Importe:
    """Dinero con su moneda (regla 4 de Orbit).

    La aritmetica revienta con `MonedasMezcladas` si las monedas difieren;
    no hay conversion implicita ni multiplicacion entre importes.
    """

    valor: Decimal
    moneda: str

    def __post_init__(self) -> None:
        exigir_decimal(self.valor, campo="importe.valor")
        if not isinstance(self.moneda, str) or not self.moneda.strip():
            raise ValueError("importe.moneda vacia")

    def _misma_moneda(self, otro: Importe) -> None:
        if self.moneda != otro.moneda:
            raise MonedasMezcladas(f"monedas distintas: {self.moneda} y {otro.moneda}")

    def __add__(self, otro: Importe) -> Importe:
        self._misma_moneda(otro)
        return Importe(self.valor + otro.valor, self.moneda)

    def __sub__(self, otro: Importe) -> Importe:
        self._misma_moneda(otro)
        return Importe(self.valor - otro.valor, self.moneda)

    def __lt__(self, otro: Importe) -> bool:
        self._misma_moneda(otro)
        return self.valor < otro.valor

    def __le__(self, otro: Importe) -> bool:
        self._misma_moneda(otro)
        return self.valor <= otro.valor

    def __gt__(self, otro: Importe) -> bool:
        self._misma_moneda(otro)
        return self.valor > otro.valor

    def __ge__(self, otro: Importe) -> bool:
        self._misma_moneda(otro)
        return self.valor >= otro.valor

    def escalar(self, factor: Decimal) -> Importe:
        exigir_decimal(factor, campo="importe.factor")
        return Importe(self.valor * factor, self.moneda)


@dataclass(frozen=True)
class TasaFx:
    """Tasa usada, con su fecha y fuente. `quote` por 1 `base`."""

    base: str
    quote: str
    tasa: Decimal
    fecha: date
    fuente: str

    def __post_init__(self) -> None:
        exigir_decimal(self.tasa, campo="tasa.tasa")
        if self.tasa <= 0:
            raise ValueError("tasa.tasa no positiva")
        if not self.base or not self.quote or self.base == self.quote:
            raise ValueError("tasa con par de monedas invalido")


@dataclass(frozen=True)
class Convertido:
    """Un importe medido en una moneda y llevado a otra. Guarda las dos
    puntas y la tasa: es el unico tipo que cruza monedas."""

    original: Importe
    en: Importe
    tasa: TasaFx

    def __post_init__(self) -> None:
        if self.original.moneda == self.en.moneda:
            raise ValueError("convertido sin cruzar monedas")
        if {self.tasa.base, self.tasa.quote} != {self.original.moneda, self.en.moneda}:
            raise ValueError("convertido con tasa que no une las dos puntas")


def convertir(importe: Importe, tasa: TasaFx) -> Convertido:
    """La unica puerta entre monedas. Sin tasa no hay llamada: `fx_ausente`."""
    if importe.moneda != tasa.base:
        raise ValueError(f"tasa {tasa.base}->{tasa.quote} no aplica a {importe.moneda}")
    en = Importe((importe.valor * tasa.tasa).quantize(Decimal("0.0001")), tasa.quote)
    return Convertido(original=importe, en=en, tasa=tasa)


# --- Envio (L) con origen: un imputado no se puede confundir con un medido ---


@dataclass(frozen=True)
class MuestraEnvio:
    """Fila de `precio_envio_muestra` ya leida (evidencia de `L`)."""

    id: int
    product_id: int
    plataforma: str
    canal: str
    origen: str
    valor: Importe
    envios: int
    donantes: int | None
    ventana: tuple[date, date]
    regla_version: str

    def __post_init__(self) -> None:
        if self.origen not in ("propio", "familia", "marketplace"):
            raise ValueError(f"muestra.origen invalido: {self.origen!r}")
        if self.origen == "propio" and self.donantes is not None:
            raise ValueError("muestra propia no trae donantes")
        if self.origen in ("familia", "marketplace") and (
            self.donantes is None or self.donantes < 1
        ):
            raise ValueError("muestra imputada exige donantes")
        if self.envios < 1:
            raise ValueError("muestra.envios no positivo")


@dataclass(frozen=True)
class EnvioIncluido:
    """FBA: la logistica ya va dentro del fee; `L` es la constante de la
    politica."""

    importe: Importe
    origen: str = "politica"


@dataclass(frozen=True)
class EnvioPropio:
    """Medido con envios de ESTE producto."""

    importe: Importe
    muestra: MuestraEnvio
    conversion: Convertido | None = None

    def __post_init__(self) -> None:
        if self.muestra.origen != "propio":
            raise ValueError("envio propio exige muestra propia")


@dataclass(frozen=True)
class EnvioImputado:
    """NO es de este producto: mediana de hermanos (`familia`) o del
    marketplace. Clase distinta a `EnvioPropio` a proposito: quien quiera
    tratarlo como medido tiene que escribir el `isinstance`."""

    importe: Importe
    muestra: MuestraEnvio
    conversion: Convertido | None = None

    def __post_init__(self) -> None:
        if self.muestra.origen not in ("familia", "marketplace"):
            raise ValueError("envio imputado exige muestra familia o marketplace")


@dataclass(frozen=True)
class Fees:
    """Fees a UN precio, ya partidos por el adaptador en parte variable y
    fija. Invariante: `total == variable + fijo`, misma moneda."""

    total: Importe
    variable: Importe
    fijo: Importe
    impuesto_pendiente: bool
    detalle: tuple[DetalleFee, ...] = ()

    def __post_init__(self) -> None:
        if self.total != self.variable + self.fijo:
            raise ValueError(
                f"fees incoherentes: total {self.total} != variable + fijo "
                f"({self.variable} + {self.fijo})"
            )


@dataclass(frozen=True)
class Tasas:
    """Tasas de la politica del universo, tal como las uso el escenario."""

    iva_divisor: Decimal
    precio_incluye_iva: bool
    retencion_tasa: Decimal

    def __post_init__(self) -> None:
        exigir_decimal(self.iva_divisor, campo="tasas.iva_divisor")
        exigir_decimal(self.retencion_tasa, campo="tasas.retencion_tasa")
        if self.iva_divisor <= 0:
            raise ValueError("tasas.iva_divisor no positiva")
        if self.retencion_tasa < 0:
            raise ValueError("tasas.retencion_tasa negativa")


@dataclass(frozen=True)
class Cuenta:
    """P, I, C, F, L, R de UN miembro a UN precio. Los seis importes van en
    la MISMA moneda, la de venta: monedas mezcladas no construyen."""

    precio: Importe
    ingreso: Importe
    costo: Importe
    fees: Fees
    envio: EnvioIncluido | EnvioPropio | EnvioImputado
    retencion: Importe

    def __post_init__(self) -> None:
        monedas = {
            self.precio.moneda,
            self.ingreso.moneda,
            self.costo.moneda,
            self.fees.total.moneda,
            self.envio.importe.moneda,
            self.retencion.moneda,
        }
        if len(monedas) != 1:
            raise MonedasMezcladas(f"cuenta con monedas mezcladas: {sorted(monedas)}")
        if self.ingreso.valor <= 0:
            raise ValueError("cuenta.ingreso no positivo")

    @property
    def margen(self) -> Decimal:
        """`(I - C - F - L - R) / I`."""
        neto = self.ingreso - self.costo - self.fees.total - self.envio.importe - self.retencion
        return neto.valor / self.ingreso.valor


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
    sin_efecto: bool = False

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
class SenalVentas:
    """Salida de `ventas.py`: `perdiendo` solo con racha completa.

    S.7: `evidencia` dice con que se probo (`None` solo en `sin_dato`).
    """

    estado: str
    submotivo: str | None
    evidencia: str | None
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
            if self.evidencia is not None:
                raise ValueError("senal sin_dato con evidencia")
        else:
            if self.submotivo is not None:
                raise ValueError("senal con submotivo fuera de sin_dato")
            if self.evidencia not in EVIDENCIAS_SENAL:
                raise ValueError(f"senal.evidencia fuera de vocabulario: {self.evidencia!r}")
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
    aplicado: bool
    mode: str
    buy_box_is_own: bool | None = None
    diagnostico: str = ""
    verificacion: str | None = None

    def __post_init__(self) -> None:
        if self.resultado not in RESULTADOS:
            raise ValueError(f"decision.resultado invalido: {self.resultado!r}")
        if not motivo_permitido(self.resultado, self.motivo):
            raise ValueError(f"motivo {self.motivo!r} fuera de vocabulario para {self.resultado}")
        if self.resultado not in ("subir", "bajar") and self.p_aplicado is not None:
            raise ValueError(f"p_aplicado solo en subir/bajar, llego en {self.resultado}")
        if self.verificacion is not None and self.verificacion not in VERIFICACIONES:
            raise ValueError(f"decision.verificacion invalida: {self.verificacion!r}")


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
