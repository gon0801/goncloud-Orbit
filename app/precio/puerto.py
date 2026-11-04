"""Puerto de plataforma del motor de precios (REPRICING 02, seccion B).

La frontera: la corrida recibe UN `Mercado` y deja de conocer Amazon. El
puerto es angosto (leer catalogo e insumos, cotizar, leer y escribir el
precio vivo) y devuelve tipos de dominio; nada de `httpx.Response`, dicts de
SP-API ni JSON de MeLi sale de los adaptadores.

Puro: protocolos y contenedores sin I/O. `Conexion` es la conexion psycopg
que pasa quien llama.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal, Protocol, runtime_checkable

from app.precio.objetivo import precio_estrella
from app.precio.tipos import Cuenta, EntradaDecision, Fees, Importe, ObservacionPricing

__all__ = [
    "Miembro",
    "UnidadPrecio",
    "UnidadActiva",
    "DiaUnidad",
    "HistoriaUnidad",
    "RefsAuditoria",
    "Insumos",
    "SinInsumos",
    "Cotizacion",
    "SinCotizador",
    "Acuse",
    "PrecioVivoAusente",
    "EscrituraNoDisponible",
    "Vitrina",
    "Mercado",
    "miembro_que_manda",
    "MetaGoal",
    "Lectura",
]

Plataforma = str
Canal = str
Moneda = str
Conexion = Any


@dataclass(frozen=True)
class Miembro:
    """Un producto que se vende por una unidad de precio.

    `product_id is None` = variante sin mapear a `product` (se ve, no se
    inventa). `variante` es el id de la variacion en la plataforma; None si
    no hay.
    """

    product_id: int | None
    seller_sku: str
    variante: str | None


@dataclass(frozen=True)
class UnidadPrecio:
    """LA unidad del motor: lo que recibe UN precio, UN goal y UNA decision
    al dia. Es una fila de `listing` (Amazon: un ASIN; MeLi: una publicacion).

    `miembros` tiene siempre al menos uno; en Amazon exactamente uno.
    `producto_ancla` es `listing.product_id`: identidad, no costo.
    """

    listing_id: int
    plataforma: Plataforma
    externo: str
    canal: Canal | None
    producto_ancla: int
    miembros: tuple[Miembro, ...]


@dataclass(frozen=True)
class UnidadActiva:
    """Una activa canonica del catalogo, con lo que la cobertura necesita."""

    unidad: UnidadPrecio | None
    seller_sku: str
    precio: Importe | None
    dias_sin_reportar: int


Disponibilidad = Literal["disponible", "no_disponible", "sin_observacion"]


@dataclass(frozen=True)
class DiaUnidad:
    """Un dia de una unidad, ya normalizado por el adaptador.

    `disponible` = ese dia se podia comprar. `exposiciones`/`clics` son la
    senal temprana; None = no hay dato de ese dia (nunca cero inventado).
    `buy_box_propia` se conserva solo para el aviso; ninguna regla la
    consulta.
    """

    dia: date
    unidades: int
    disponibilidad: Disponibilidad
    buy_box_propia: bool | None
    exposiciones: int | None
    clics: int | None


@dataclass(frozen=True)
class HistoriaUnidad:
    """Ventas y disponibilidad atribuidas a ESTA unidad, no al producto."""

    dias: tuple[DiaUnidad, ...]
    primera_venta: date | None
    ledger_cubierto_hasta: date | None
    sin_atribuir: int


@dataclass(frozen=True)
class RefsAuditoria:
    """Ids para reproducir la decision sin releer insumos."""

    escenario_id: int | None
    fee_observation_id: int | None
    oferta_observation_id: int | None
    envio_muestra_id: int | None
    product_id: int | None


@dataclass(frozen=True)
class Insumos:
    """Todo lo que la plataforma aporta a una decision, leido y tipado.

    `cuenta` es la del miembro que manda; `miembros` trae la de todos.
    `cotizable` es un token opaco del adaptador: el motor lo devuelve tal
    cual en `Mercado.cotizar`.
    """

    unidad: UnidadPrecio
    cuenta: Cuenta
    miembros: tuple[tuple[Miembro, Cuenta], ...]
    tasas: Any
    oferta_observada_en: datetime
    pricing: ObservacionPricing | None
    historia: HistoriaUnidad
    refs: RefsAuditoria
    cotizable: object


@dataclass(frozen=True)
class SinInsumos:
    """No hay cuenta. `motivo` ya esta en vocabulario; el crudo va en
    `diagnostico`."""

    unidad: UnidadPrecio
    motivo: str
    diagnostico: str
    refs: RefsAuditoria


@dataclass(frozen=True)
class Cotizacion:
    """Fees reales a un precio. `fees is None` exactamente cuando
    `error_code is not None`."""

    precio: Importe
    fees: Fees | None
    estimada_en: datetime | None
    error_code: str | None

    def __post_init__(self) -> None:
        if (self.fees is None) == (self.error_code is None):
            raise ValueError("cotizacion exige fees o error_code, no ambos ni ninguno")


@dataclass(frozen=True)
class SinCotizador:
    """Este universo no tiene cotizacion real de fees (lo dice su registro).

    No es un error: la decision se cierra con la formula
    (`verificacion = "lineal"`) y lo declara en la fila.
    """

    motivo: str


@dataclass(frozen=True)
class Acuse:
    """Respuesta de la plataforma a una escritura, ya saneada."""

    aceptada: bool
    codigo: str | None
    cuerpo: Mapping[str, Any]


class PrecioVivoAusente(Exception):
    """No hay precio propio legible: no se escribe nada."""


class EscrituraNoDisponible(Exception):
    """El camino de escritura de esta plataforma aun no tiene forma sellada
    por sonda."""


@runtime_checkable
class Vitrina(Protocol):
    """Cara de LECTURA de una plataforma: solo base, sin red ni credenciales."""

    clave: Plataforma
    moneda: Moneda

    def catalogo(self, conn: Conexion, *, hoy: date) -> tuple[UnidadActiva, ...]:
        """Activas canonicas. UNA definicion por plataforma, usada por la
        cobertura y por el denominador del fusible."""
        ...

    def insumos(
        self, conn: Conexion, unidades: Sequence[UnidadPrecio], *, hoy: date, ahora: datetime
    ) -> Mapping[int, Insumos | SinInsumos]:
        """La cuenta del dia y la historia de CADA unidad, por `listing_id`.

        Una llamada por plataforma (lecturas por lote). Toda unidad pedida
        viene en la respuesta. Nunca levanta por un dato faltante: lo devuelve
        como `SinInsumos(motivo)`.
        """
        ...

    def observado(
        self, conn: Conexion, unidad: UnidadPrecio, *, despues_de: date, hasta: date
    ) -> ObservacionPricing | None:
        """La observacion propia mas reciente en `(despues_de, hasta]`: es lo
        que cierra un cambio enviado. None = todavia no hay."""
        ...


@runtime_checkable
class Mercado(Vitrina, Protocol):
    """Una plataforma completa: `Vitrina` mas lo que cruza la red.

    `escribir` es la UNICA mutacion externa del motor.
    """

    grupo_tasa: str

    def cotizar(
        self, conn: Conexion, insumos: Insumos, precio: Importe
    ) -> Cotizacion | SinCotizador:
        """Fees reales a `precio`. Un error de la plataforma es un
        `Cotizacion(error_code=...)`, no una excepcion. `SinCotizador` solo si
        el universo de la unidad esta registrado sin cotizador."""
        ...

    def precio_vivo(self, unidad: UnidadPrecio) -> Importe:
        """Precio propio leido ahora. Levanta `PrecioVivoAusente`."""
        ...

    def escribir(self, unidad: UnidadPrecio, precio: Importe, *, sonda: bool = False) -> Acuse:
        """Pide a la plataforma que el precio sea `precio`. No toca la base.
        Levanta `EscrituraNoDisponible` si la forma de escritura no esta
        sellada, salvo con `sonda=True` (solo `tools/precio_sonda.py`)."""
        ...

    def cerrar(self) -> None: ...


@dataclass(frozen=True)
class MetaGoal:
    """Lo unico que la corrida sabe y el escenario no: el goal del dia."""

    goal: Decimal
    mode: str
    goal_vigente_desde: date


@dataclass(frozen=True)
class Lectura:
    """El dia de una unidad ya resuelto por el adaptador.

    `entrada` es `None` cuando no hay escenario disponible (el motivo viaja
    en `motivo`); `cotizable` es la oferta resuelta para cotizar (token
    opaco que `Mercado.cotizar` entiende); `fees_escenario` trae los fees
    del escenario ya partidos o el motivo cuando partirlos fallo.
    `historia` la arma el adaptador y la consume S.7; `None` sin cuenta
    disponible.
    """

    entrada: EntradaDecision | None
    motivo: str | None
    diagnostico: str
    ids: dict | None
    cotizable: object
    fees_escenario: Fees | str
    historia: HistoriaUnidad | None = None


def miembro_que_manda(
    cuentas: Sequence[tuple[Miembro, Cuenta]], goal_de: Callable[[Miembro], Decimal]
) -> tuple[Miembro, Cuenta]:
    """El miembro cuyo precio estrella es el mas alto: fija el precio de toda
    la unidad. Empate -> `seller_sku` ascendente (determinista)."""
    mejor: tuple[Miembro, Cuenta] | None = None
    mejor_p = None
    for miembro, cuenta in sorted(cuentas, key=lambda par: par[0].seller_sku):
        ref = cuenta.fees.variable.valor / cuenta.ingreso.valor
        iva_divisor = cuenta.precio.valor / cuenta.ingreso.valor
        estrella = precio_estrella(
            cuenta.costo.valor,
            cuenta.fees.fijo.valor,
            cuenta.envio.importe.valor,
            cuenta.retencion.valor / cuenta.ingreso.valor,
            goal_de(miembro),
            iva_divisor,
            iva_divisor != 1,
            ref,
        )
        if mejor_p is None or estrella > mejor_p:
            mejor, mejor_p = (miembro, cuenta), estrella
    if mejor is None:
        raise ValueError("miembro_que_manda sin miembros")
    return mejor
