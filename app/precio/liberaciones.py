"""Liberacion de la compuerta (REPRICING 02, bosquejo J, carril S).

UNICO escritor de `precio_compuerta_liberacion` (candado en
`tests/test_architecture.py`). Se llama con conexion de `app_admin` (la ruta
`POST /api/precios/compuerta/{corrida_id}/liberar` la cablea G.2); la corrida
(`app_decide`) solo LEE la liberacion (vista `v_precio_decision_liberada`):
dos actores, dos tablas, se juntan al leer. Excepcion de pureza por nombre,
como `cambios.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import psycopg
import psycopg.errors

__all__ = ["Liberacion", "CorridaSinRetencion", "liberar"]


@dataclass(frozen=True)
class Liberacion:
    """La fila `precio_compuerta_liberacion`: el dueno solto la corrida."""

    corrida_id: int
    actor: str
    nota: str
    liberada_en: datetime


class CorridaSinRetencion(LookupError):
    """Esa corrida no retuvo nada: no hay que soltar (409 en el router)."""


_SQL_LIBERACION_DE = (
    "SELECT corrida_id, liberado_por, nota, liberado_at"
    " FROM precio_compuerta_liberacion WHERE corrida_id = %s"
)

_SQL_RETUVO = (
    "SELECT 1 FROM precio_compuerta_medicion WHERE corrida_id = %s AND causa IS NOT NULL LIMIT 1"
)

_SQL_LIBERAR = (
    "INSERT INTO precio_compuerta_liberacion (corrida_id, liberado_por, nota)"
    " VALUES (%s, %s, %s) RETURNING liberado_at"
)


def _fila(fila) -> Liberacion:
    return Liberacion(corrida_id=fila[0], actor=fila[1], nota=fila[2], liberada_en=fila[3])


def liberar(conn: psycopg.Connection, *, corrida_id: int, actor: str, nota: str) -> Liberacion:
    """El dueno suelta lo que una corrida retuvo por `movimiento_masivo` o
    `insumo_sistemico`.

    Idempotente: `corrida_id` es la llave primaria; la segunda llamada devuelve
    la fila que ya existe (una carrera de dos duenos resuelve igual:
    re-lee al chocar). Sin medicion con causa (o sin corrida) levanta
    `CorridaSinRetencion`: no se suelta lo que no se retuvo (el trigger
    `precio_liberacion_exige_retencion` lo respalda). No aplica nada por si
    misma: la siguiente corrida de la plataforma ve la liberacion y aplica
    las decisiones de HOY retenidas (o, si ya es otro dia, deja de contar
    esas unidades en el fusible). Soltar NO sirve para `apagador` ni
    `corte_errores`: esas no son estado, se reintentan solas en la siguiente
    corrida si la causa desaparecio.
    """
    if not actor or not actor.strip():
        raise ValueError("liberar exige actor no vacio")
    if not nota or not nota.strip():
        raise ValueError("liberar exige nota no vacia")
    fila = conn.execute(_SQL_LIBERACION_DE, (corrida_id,)).fetchone()
    if fila is not None:
        return _fila(fila)
    if conn.execute(_SQL_RETUVO, (corrida_id,)).fetchone() is None:
        raise CorridaSinRetencion(f"la corrida {corrida_id} no retuvo nada: no hay que soltar")
    try:
        liberada_en = conn.execute(_SQL_LIBERAR, (corrida_id, actor, nota)).fetchone()[0]
    except psycopg.errors.UniqueViolation:
        fila = conn.execute(_SQL_LIBERACION_DE, (corrida_id,)).fetchone()
        if fila is None:  # pragma: no cover - la fila existe: acabamos de chocar con ella
            raise
        return _fila(fila)
    return Liberacion(corrida_id=corrida_id, actor=actor, nota=nota, liberada_en=liberada_en)
