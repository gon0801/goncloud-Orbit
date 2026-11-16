"""Boceto documental; firmas propuestas, sin implementacion ni efectos al importarlo."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Literal
if TYPE_CHECKING:
    from app.estimacion_venta import DetalleFee
    from app.precio.puerto import Cotizacion
    from app.precio.tipos import Importe

Plataforma = Literal["amazon_mx", "amazon_us"]

@dataclass(frozen=True)
class Claim:
    plataforma: Plataforma
    owner: str  # Identificador distinto por adquisicion; validar contra DB en cada frontera.
    vence: datetime

@dataclass(frozen=True)
class CicloResultado:
    cycle_id: int | None  # None si el claim ocupado impide iniciar o elegir ciclo.
    pendientes: int  # Derivado de registros originales, no contador persistido paralelo.
    estado: Literal["ocupado", "sin_datos", "pendiente_apply", "completo"]

def continuar_ciclo(*, plataforma: Plataforma, ahora: datetime) -> CicloResultado:
    """Claim -> conciliar -> decidir solo sin TX3 previo -> retomar -> sellar; lease perdido aborta."""
    raise NotImplementedError

@dataclass(frozen=True)
class IdentidadKeyword:
    perfil: int
    campana: str
    grupo: str
    texto: str
    match: str

@dataclass(frozen=True)
class ObjetoAds:
    external_id: str
    identidad: IdentidadKeyword
    estado: str

@dataclass(frozen=True)
class ListaCompleta:
    objetos: tuple[ObjetoAds, ...]
    observada_en: datetime
    alcance: str  # Filtro validado de la consulta, no ruta HTTP publica.

@dataclass(frozen=True)
class LecturaDesconocida:
    motivo: str

@dataclass(frozen=True)
class CreacionConAck:
    intento_id: int
    objeto: ObjetoAds  # ID inmutable procedente del ACK, no reemplazable por identidad.

@dataclass(frozen=True)
class CreacionConciliada:
    intento_id: int
    ausencia_previa: ListaCompleta
    confirmacion: ListaCompleta
    objeto: ObjetoAds  # Solo emite el owner al comprobar causalidad y unicidad compatibles.

@dataclass(frozen=True)
class Adoptado:
    objeto: ObjetoAds  # Nunca elegible para reversa de una creacion propia.

CreacionPropia = CreacionConAck | CreacionConciliada

def objeto_reversible(decision_id: int) -> CreacionPropia | LecturaDesconocida:
    """Owner apply/harvest lee evidencia durable; rechaza solo-identidad, sustitucion y adopcion."""
    raise NotImplementedError

@dataclass(frozen=True)
class Confirmada:
    intento_id: int
    external_id: str

@dataclass(frozen=True)
class PendienteLectura:
    intento_id: int
    motivo: str

@dataclass(frozen=True)
class Rechazada:
    motivo: str

def reponer_keyword(*, archivo_id: int) -> Confirmada | PendienteLectura | Rechazada:
    """Owner CLI: claim fila, intencion durable, POST unico, ACK durable, readback y sello."""
    raise NotImplementedError

@dataclass(frozen=True)
class ScanCompleto:
    ids: frozenset[str]
    total: int

@dataclass(frozen=True)
class ScanParcial:
    ids: frozenset[str]
    motivo: str

@dataclass(frozen=True)
class DeltaCatalogo:
    observadas: int
    nuevas: int
    cambiadas: int  # Fusionar despues de savepoint exitoso, junto con cambios de mapas.

@dataclass(frozen=True)
class TargetBase:
    pct: Decimal
    cycle_id: int
    medicion: Literal["margen_vigente", "ledger_rancio", "setting"]

@dataclass(frozen=True)
class TargetEfectivo:
    base: TargetBase
    pct: Decimal
    overlay: Literal["ninguno", "temporada_alta", "temporada_baja"]
    version: int  # Version del snapshot; no reescribe ni reinterpreta los viejos.

def avanza_base(anterior: TargetBase, *, setting: Decimal, cycle_id: int) -> TargetBase:
    """Aplicar convergencia aprobada; recibe base, nunca TargetEfectivo estacional."""
    raise NotImplementedError

def _cotizacion_de_arbol_meli(
    precio: Importe, total: Decimal, detalles: tuple[DetalleFee, ...], estimada_en: datetime,
) -> Cotizacion:
    """Adaptador existente ampliado: suma/moneda/fecha/detalle; no inventar tasa ni impuesto cero."""
    raise NotImplementedError

@dataclass(frozen=True)
class CupoOrdinario:
    cycle_id_origen: int
    consumidos: int  # Derivado de intentos comprometidos, incluidos ambiguos; maximo 150.

def reservar_bid(claim: Claim, *, decision_id: int) -> int | Rechazada:
    """Owner apply: clasifica marcador congelado y reserva cupo+intento atomico; retorna intento_id."""
    raise NotImplementedError
