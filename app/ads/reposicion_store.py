"""Persistencia del ledger de reposicion de keywords (ARREGLOS 05 C).

Modulo hoja: SQL y hechos de `keyword_reposicion_intento`. No decide estados ni
acciones; eso vive en `app.apply_estados` y en `app.ads.archivar`.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import psycopg
from psycopg.types.json import Json

from app.apply_estados import (
    TOPE_INTENTOS_CAMBIO,
)

# Un lease mas largo que cualquier POST+readback del CLI (timeout de lectura de
# 20 s mas reintentos). Un sucesor solo toma el intento vencido y lo CONCILIA:
# nunca emite otro POST. Fuente unica del plazo.
LEASE_SEGUNDOS = 300
TOPE_INTENTOS_REPOSICION = TOPE_INTENTOS_CAMBIO

_SQL_FILAS = """
SELECT id, lote, platform::text, campaign_external, ad_group_external,
       keyword_external, keyword_text, match_type, bid, bid_currency::text,
       estado, repuesto_external
  FROM keyword_archivo_manual
 WHERE lote = %s AND {condicion}
 ORDER BY id
"""

_SQL_ARCHIVO = """
SELECT estado, repuesto_external FROM keyword_archivo_manual WHERE id = %s
"""

_SQL_INTENTOS_TIPO = """
SELECT seq, resultado, finished_at FROM keyword_reposicion_intento
 WHERE archivo_id = %s AND tipo = %s
 ORDER BY seq
"""

_SQL_MAX_SEQ = """
SELECT coalesce(max(seq), 0) + 1 FROM keyword_reposicion_intento WHERE archivo_id = %s
"""

_SQL_INSERTA = """
INSERT INTO keyword_reposicion_intento
       (archivo_id, seq, tipo, owner, lease_hasta, request_payload)
VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT (archivo_id, tipo) WHERE finished_at IS NULL DO NOTHING
RETURNING id
"""

_SQL_ABIERTO = """
SELECT id, archivo_id, seq, tipo, owner, lease_hasta, ack, external_id,
       resultado, started_at
  FROM keyword_reposicion_intento
 WHERE archivo_id = %s AND tipo = %s AND finished_at IS NULL
 ORDER BY seq DESC
 LIMIT 1
"""

_SQL_ABIERTOS_LOTE = """
SELECT i.id, i.archivo_id, i.seq, i.tipo, i.owner, i.lease_hasta, i.ack,
       i.external_id, i.resultado, i.started_at,
       f.lote, f.platform::text, f.campaign_external, f.ad_group_external,
       f.keyword_external, f.keyword_text, f.match_type, f.bid,
       f.bid_currency::text, f.estado, f.repuesto_external
  FROM keyword_reposicion_intento i
  JOIN keyword_archivo_manual f ON f.id = i.archivo_id
 WHERE i.finished_at IS NULL AND (%s::text IS NULL OR f.lote = %s)
   AND i.tipo = %s
 ORDER BY i.id
"""

_SQL_OWNER_VENCIDO = """
SELECT owner FROM keyword_reposicion_intento
 WHERE id = %s AND finished_at IS NULL AND lease_hasta < now()
"""

# Toma un intento abandonado: solo si sigue abierto y su lease vencio. La
# comparacion de owner evita que dos sucesores se pisen.
_SQL_TOMA = """
UPDATE keyword_reposicion_intento
   SET owner = %s, lease_hasta = %s, heartbeat_at = now()
 WHERE id = %s AND owner = %s AND finished_at IS NULL AND lease_hasta < now()
 RETURNING id
"""

_SQL_RENUEVA = """
UPDATE keyword_reposicion_intento
   SET heartbeat_at = now(), lease_hasta = %s
 WHERE id = %s AND owner = %s AND finished_at IS NULL
 RETURNING id
"""

# ACK e ID se guardan una sola vez y antes del readback. Solo desde NULL.
_SQL_GUARDA_ACK = """
UPDATE keyword_reposicion_intento
   SET ack = %s
 WHERE id = %s AND finished_at IS NULL AND ack IS NULL
 RETURNING id
"""

_SQL_GUARDA_EXTERNAL = """
UPDATE keyword_reposicion_intento
   SET external_id = %s
 WHERE id = %s AND finished_at IS NULL AND external_id IS NULL
 RETURNING id
"""

_SQL_GUARDA_ACK_E_ID = """
UPDATE keyword_reposicion_intento
   SET ack = %s, external_id = %s
 WHERE id = %s AND finished_at IS NULL AND ack IS NULL AND external_id IS NULL
 RETURNING id
"""

_SQL_SELLA = """
UPDATE keyword_reposicion_intento
   SET resultado = %s, finished_at = now()
 WHERE id = %s AND finished_at IS NULL AND resultado IS NULL
 RETURNING id
"""

_SQL_HECHOS = """
SELECT count(*),
       max(seq),
       (array_agg(resultado ORDER BY seq DESC)
            FILTER (WHERE resultado IS NOT NULL))[1],
       (array_agg(external_id ORDER BY seq DESC)
            FILTER (WHERE external_id IS NOT NULL))[1],
       (array_agg(ack ORDER BY seq DESC)
            FILTER (WHERE ack IS NOT NULL))[1],
       bool_or(resultado = 'ok'),
       (array_agg(id ORDER BY seq DESC)
            FILTER (WHERE finished_at IS NULL))[1]
  FROM keyword_reposicion_intento
 WHERE archivo_id = %s AND tipo = %s
"""

_SQL_SELLA_ARCHIVO = """
UPDATE keyword_archivo_manual
   SET estado = 'repuesto', repuesto_at = now(),
       repuesto_external = %s, repuesto_ack = %s
 WHERE id = %s
   AND (estado = 'applied' OR (estado = 'repuesto' AND repuesto_external = %s))
 RETURNING id
"""


@dataclass(frozen=True)
class FilaArchivo:
    id: int
    lote: str
    plataforma: str
    campaign_external: str
    ad_group_external: str
    keyword_external: str
    keyword_text: str
    match_type: str
    bid: object
    bid_currency: str
    estado: str
    repuesto_external: str | None


@dataclass(frozen=True)
class Intento:
    id: int
    archivo_id: int
    seq: int
    tipo: str
    owner: str
    lease_hasta: dt.datetime
    ack: dict | None
    external_id: str | None
    resultado: str | None
    started_at: dt.datetime


@dataclass(frozen=True)
class IntentoLote:
    """Un intento abierto con la fila del archivo que lo origino."""

    intento: Intento
    fila: FilaArchivo


@dataclass(frozen=True)
class Hechos:
    """Hechos observables del ledger de UNA operacion de una fila."""

    intentos_totales: int
    intento_abierto_id: int | None
    ultimo_resultado: str | None
    ultimo_external_id: str | None
    ultimo_ack: dict | None
    hay_ok: bool


def _fila(f: tuple) -> FilaArchivo:
    return FilaArchivo(*f)


def _fila_de_abierto(f: tuple) -> FilaArchivo:
    return FilaArchivo(
        id=f[1],
        lote=f[10],
        plataforma=f[11],
        campaign_external=f[12],
        ad_group_external=f[13],
        keyword_external=f[14],
        keyword_text=f[15],
        match_type=f[16],
        bid=f[17],
        bid_currency=f[18],
        estado=f[19],
        repuesto_external=f[20],
    )


def _intento(f: tuple) -> Intento:
    return Intento(*f)


def filas_reponibles(conn: psycopg.Connection, lote: str) -> list[FilaArchivo]:
    return [
        _fila(f) for f in conn.execute(_SQL_FILAS.format(condicion="estado = 'applied'"), (lote,))
    ]


def filas_revertibles(conn: psycopg.Connection, lote: str) -> list[FilaArchivo]:
    return [
        _fila(f)
        for f in conn.execute(
            _SQL_FILAS.format(condicion="estado = 'repuesto' AND repuesto_external IS NOT NULL"),
            (lote,),
        )
    ]


def hechos(conn: psycopg.Connection, archivo_id: int, tipo: str) -> Hechos:
    fila = conn.execute(_SQL_HECHOS, (archivo_id, tipo)).fetchone()
    return Hechos(
        intentos_totales=fila[0],
        intento_abierto_id=fila[6],
        ultimo_resultado=fila[2],
        ultimo_external_id=fila[3],
        ultimo_ack=fila[4],
        hay_ok=bool(fila[5]),
    )


def bloquea_reposicion(conn: psycopg.Connection, archivo_id: int, tipo: str) -> None:
    """Cerrojo transaccional por fila y operacion: serializa claim y sello.

    Sin el, un segundo CLI que decidio enviar antes del sello del primero
    podria reservar una secuencia nueva justo despues de ese sello y emitir
    otro CREATE. El cerrojo se libera al terminar la transaccion.
    """
    conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (f"reposicion:{archivo_id}:{tipo}",),
    )


def abre_intento(
    conn: psycopg.Connection,
    *,
    archivo_id: int,
    tipo: str,
    owner: str,
    payload: dict,
    lease_hasta: dt.datetime,
    estado_esperado: str,
    repuesto_external_esperado: str | None = None,
) -> int | None:
    """Intencion durable, revalidando el sello de la fila bajo el cerrojo.

    None si la fila ya no esta en el estado que autorizo el envio (otro CLI
    sello antes), si ya hay un intento abierto de esa operacion o si la
    operacion ya quedo `ok`.
    """
    try:
        with conn.transaction():
            bloquea_reposicion(conn, archivo_id, tipo)
            archivo = conn.execute(_SQL_ARCHIVO, (archivo_id,)).fetchone()
            if archivo is None or archivo[0] != estado_esperado:
                return None
            if tipo == "reversa" and (
                repuesto_external_esperado is None
                or str(archivo[1]) != str(repuesto_external_esperado)
            ):
                return None
            intentos = conn.execute(_SQL_INTENTOS_TIPO, (archivo_id, tipo)).fetchall()
            if any(f[1] == "ok" for f in intentos):
                return None
            if any(f[2] is None for f in intentos):
                return None
            seq = conn.execute(_SQL_MAX_SEQ, (archivo_id,)).fetchone()[0]
            fila = conn.execute(
                _SQL_INSERTA,
                (archivo_id, seq, tipo, owner, lease_hasta, Json(payload)),
            ).fetchone()
    except psycopg.errors.UniqueViolation:
        # Otra sesion gano el mismo seq o el intento abierto entre la lectura
        # y el INSERT: es un intento ajeno, no un error del protocolo.
        return None
    return fila[0] if fila is not None else None


def abierto(conn: psycopg.Connection, archivo_id: int, tipo: str) -> Intento | None:
    fila = conn.execute(_SQL_ABIERTO, (archivo_id, tipo)).fetchone()
    return _intento(fila) if fila is not None else None


def abiertos_del_lote(conn: psycopg.Connection, lote: str | None, tipo: str) -> list[IntentoLote]:
    return [
        IntentoLote(intento=_intento(f[0:10]), fila=_fila_de_abierto(f))
        for f in conn.execute(_SQL_ABIERTOS_LOTE, (lote, lote, tipo)).fetchall()
    ]


def toma_intento(
    conn: psycopg.Connection, intento_id: int, owner: str, lease_hasta: dt.datetime
) -> bool:
    """Toma un intento vencido de otro owner; False si otro se adelanto."""
    fila = conn.execute(_SQL_OWNER_VENCIDO, (intento_id,)).fetchone()
    if fila is None:
        return False
    return conn.execute(_SQL_TOMA, (owner, lease_hasta, intento_id, fila[0])).fetchone() is not None


def renueva(
    conn: psycopg.Connection, intento_id: int, owner: str, lease_hasta: dt.datetime
) -> bool:
    return conn.execute(_SQL_RENUEVA, (lease_hasta, intento_id, owner)).fetchone() is not None


def guarda_ack(conn: psycopg.Connection, intento_id: int, ack: dict) -> bool:
    return conn.execute(_SQL_GUARDA_ACK, (Json(ack), intento_id)).fetchone() is not None


def guarda_external(conn: psycopg.Connection, intento_id: int, external_id: str) -> bool:
    return conn.execute(_SQL_GUARDA_EXTERNAL, (str(external_id), intento_id)).fetchone() is not None


def guarda_ack_e_id(conn: psycopg.Connection, intento_id: int, ack: dict, external_id: str) -> bool:
    return (
        conn.execute(_SQL_GUARDA_ACK_E_ID, (Json(ack), str(external_id), intento_id)).fetchone()
        is not None
    )


def sella(conn: psycopg.Connection, intento_id: int, resultado: str) -> bool:
    return conn.execute(_SQL_SELLA, (resultado, intento_id)).fetchone() is not None


def sella_archivo_repuesto(
    conn: psycopg.Connection, archivo_id: int, external_id: str, ack: dict
) -> bool:
    return (
        conn.execute(
            _SQL_SELLA_ARCHIVO, (str(external_id), Json(ack), archivo_id, str(external_id))
        ).fetchone()
        is not None
    )
