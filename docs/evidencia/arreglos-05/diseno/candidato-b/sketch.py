"""Boceto documental; distribuir entre duenos existentes. No importar en producto."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Literal
from uuid import UUID

if TYPE_CHECKING:
    from psycopg import Connection
    from app.apply import Aplicador
    from app.meli.catalogo import PlanItem
    from app.precio.puerto import Cotizacion
    from app.precio.tipos import Importe, DetalleFee

Plataforma = Literal["amazon_mx", "amazon_us"]

@dataclass(frozen=True)
class OrigenCiclo:
    ciclo_id: int
    plataforma: Plataforma

@dataclass(frozen=True)
class Ejecucion:
    origen: OrigenCiclo
    ciclo_ejecutor: int
    owner: UUID

@dataclass(frozen=True)
class Pendiente:
    motivo: str
    referencia: int

@dataclass(frozen=True)
class Confirmado:
    referencia: int
    confirmado_el: datetime

@dataclass(frozen=True)
class ResultadoCiclo:
    ejecucion: Ejecucion
    pendientes: tuple[Pendiente, ...]

def atender_plataforma(conn: Connection, plataforma: Plataforma, *, ahora: datetime) -> ResultadoCiclo:
    """cycle: reclamar, decidir o retomar, aplicar y cerrar; origen estable entre retomadas."""
    raise NotImplementedError

@dataclass(frozen=True)
class ReservaOrdinaria:
    origen: OrigenCiclo
    decision_id: int
    intento_id: int

@dataclass(frozen=True)
class TransicionEstacional:
    decision_id: int
    episodio_id: int
    clase: Literal["entrada", "regreso"]
    version: str

def reservar_bid(conn: Connection, ejecucion: Ejecucion, decision_id: int) -> ReservaOrdinaria | TransicionEstacional | Pendiente:
    """apply, interno: clasifica inputs sellados; reserva y ledger en TX, maximo 150 ordinarias."""
    raise NotImplementedError

@dataclass(frozen=True)
class MetaBase:
    valor_pct: Decimal
    ciclo_id: int
    version: str

def base_anterior(conn: Connection, plataforma: Plataforma) -> MetaBase | None:
    """cycle: nunca toma el overlay estacional como ancla; conserva historia versionada."""
    raise NotImplementedError

@dataclass(frozen=True)
class IdentidadKeyword:
    plataforma: Plataforma
    campana_id: str
    grupo_id: str
    texto: str
    match: Literal["EXACT", "PHRASE", "BROAD", "NEGATIVE_EXACT", "NEGATIVE_PHRASE"]

@dataclass(frozen=True)
class CreacionPropia:
    identidad: IdentidadKeyword
    externo_id: str
    intento_id: int
    ack_guardado_el: datetime

@dataclass(frozen=True)
class EfectoReconciliado:
    identidad: IdentidadKeyword
    externo_id: str
    decision_id: int
    observado_el: datetime

@dataclass(frozen=True)
class KeywordLeida:
    identidad: IdentidadKeyword
    externo_id: str
    estado: Literal["ENABLED", "PAUSED", "ARCHIVED", "PROPOSED"]

@dataclass(frozen=True)
class LecturaCompleta:
    keywords: tuple[KeywordLeida, ...]
    observada_el: datetime

@dataclass(frozen=True)
class LecturaIncierta:
    motivo: str

def leer_keywords(aplicador: Aplicador, grupo_id: str) -> LecturaCompleta | LecturaIncierta:
    """harvest: validar paginas con lector existente; nunca ausencia sobre lectura parcial."""
    raise NotImplementedError

def revertir_creacion(conn: Connection, propia: CreacionPropia) -> Confirmado | Pendiente:
    """harvest: revalidar prueba almacenada e identidad por ID; solo recurso propio."""
    raise NotImplementedError

def revertir_negative(conn: Connection, decision_id: int, *, actor: str) -> Confirmado | Pendiente:
    """apply: resolver efecto confirmado en ledger/resumen; manual, idempotente, no autoria."""
    raise NotImplementedError

def reponer_keyword(conn: Connection, archivo_id: int, *, actor: str) -> Confirmado | Pendiente:
    """ads.archivar: claim, intencion, POST unico, ACK durable, readback; incierto solo lee."""
    raise NotImplementedError

@dataclass(frozen=True)
class ScanCompleto:
    ids: frozenset[str]
    observado_el: datetime

@dataclass(frozen=True)
class ScanParcial:
    ids: frozenset[str]
    motivo: str

@dataclass(frozen=True)
class DeltaItem:
    observadas: int
    bajas: int
    skips: tuple[str, ...]

def aplicar_item(conn: Connection, plan: PlanItem) -> DeltaItem:
    """catalogo, interno: retorna solo tras savepoint; caller suma tras exito y sella en TX."""
    raise NotImplementedError

@dataclass(frozen=True)
class EvidenciaFee:
    total: Importe
    detalles: tuple[DetalleFee, ...]
    estimada_en: datetime
    referencia_fuente: str

def cotizacion_desde_evidencia(precio: Importe, evidencia: EvidenciaFee) -> Cotizacion:
    """precio_mercado: misma moneda, suma exacta, fecha y detalle conservados; sin tasas nuevas."""
    raise NotImplementedError

@dataclass(frozen=True)
class FeeSinConciliar:
    motivo: str

@dataclass(frozen=True)
class FeeConciliada:
    costo_total: Importe
    version: str
    fuente: str

def conciliar_fee(cotizada: EvidenciaFee, cargo: EvidenciaFee) -> FeeConciliada | FeeSinConciliar:
    """estimacion: boceto de comparacion, no permiso fiscal; sin evidencia no emite precio."""
    raise NotImplementedError
