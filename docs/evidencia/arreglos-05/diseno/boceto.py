"""Contrato documental propuesto; no importar en la app ni ejecutar como producto."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from psycopg import Connection

    from app.precio.puerto import Cotizacion
    from app.precio.tipos import Fees, Importe


@dataclass(frozen=True)
class Ejecucion:
    origen_id: int
    ejecutor_id: int
    plataforma: Literal["amazon_mx", "amazon_us"]
    owner: str


@dataclass(frozen=True)
class Pendiente:
    motivo: str
    referencia: int | None


@dataclass(frozen=True)
class Confirmado:
    referencia: int
    confirmado_el: datetime


@dataclass(frozen=True)
class ResultadoCiclo:
    ejecucion: Ejecucion
    pendientes: tuple[Pendiente, ...]


def atender_plataforma(
    conn: Connection, plataforma: str, *, ahora: datetime
) -> ResultadoCiclo | Pendiente:
    """cycle: reclamar; decidir o retomar; aplicar y cerrar sin duplicar decisiones."""
    raise NotImplementedError


@dataclass(frozen=True)
class IdentidadKeyword:
    perfil: int
    campana_id: str
    grupo_id: str
    texto: str
    match: str


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
    intento_id: int
    observado_el: datetime
    # No pertenece a CreacionPropia ni habilita limpieza automatica.


def revertir_harvest(conn: Connection, decision_id: int) -> Confirmado | Pendiente:
    """harvest: resuelve evidencia propia durable; no acepta un flag del caller."""
    raise NotImplementedError


def revertir_negative(
    conn: Connection, decision_id: int, *, actor: str
) -> Confirmado | Pendiente:
    """apply: reversa manual del efecto confirmado, con procedencia y readback por ID."""
    raise NotImplementedError


def reponer_keyword(conn: Connection, archivo_id: int, *, actor: str) -> Confirmado | Pendiente:
    """ads.archivar: claim, intencion, POST, ACK durable, readback; incierto solo lee."""
    raise NotImplementedError


@dataclass(frozen=True)
class BaseMeta:
    porcentaje: Decimal
    ciclo_id: int
    version: int


@dataclass(frozen=True)
class MetaEfectiva:
    base: BaseMeta
    porcentaje: Decimal
    overlay: Literal["ninguno", "alta", "baja"]


def base_anterior(conn: Connection, plataforma: str) -> BaseMeta | None:
    """cycle: ultima base live comprometida; no overlay ni ancla de practica."""
    raise NotImplementedError


def reservar_intento_bid(
    conn: Connection, ejecucion: Ejecucion, decision_id: int
) -> int | Pendiente:
    """apply interno: deriva cupo de ledger; reserva e intencion atomicas antes del HTTP."""
    raise NotImplementedError


@dataclass(frozen=True)
class EvidenciaFee:
    fees: Fees
    estimada_en: datetime
    fuente: str


def cotizacion_desde_evidencia(precio: Importe, evidencia: EvidenciaFee) -> Cotizacion:
    """precio_mercado: conserva moneda, fecha y detalle en fresca/cache; no inventa tasas."""
    raise NotImplementedError
