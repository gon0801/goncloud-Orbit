"""Candado atomico compartido sobre `ads_optimizer_lock` (+ advisory opcional).

Unico dueno del claim/heartbeat/release usado por `app/cycle.py`,
`app/precio/corrida.py` y `app/precio/cambios.py`. La expiracion por TTL vive
siempre en el WHERE del UPSERT (jamas SELECT-luego-INSERT).
"""

from __future__ import annotations

import logging
from datetime import datetime

import psycopg
from psycopg import sql

from app.redaction import install_scrub_filter, scrub

logger = logging.getLogger(__name__)
install_scrub_filter(logger)

SQL_CLAIM = """
INSERT INTO ads_optimizer_lock (job_key, owner)
VALUES (%s, %s)
ON CONFLICT (job_key) DO UPDATE
   SET owner = EXCLUDED.owner, claimed_at = now(), heartbeat_at = now()
 WHERE ads_optimizer_lock.heartbeat_at
       + make_interval(secs => ads_optimizer_lock.ttl_seconds) <= now()
RETURNING claimed_at
"""

SQL_CLAIM_CON_TTL = """
INSERT INTO ads_optimizer_lock (job_key, owner, claimed_at, heartbeat_at, ttl_seconds)
VALUES (%s, %s, now(), now(), %s)
ON CONFLICT (job_key) DO UPDATE
   SET owner = EXCLUDED.owner, claimed_at = now(), heartbeat_at = now(),
       ttl_seconds = EXCLUDED.ttl_seconds
 WHERE ads_optimizer_lock.heartbeat_at
       + make_interval(secs => ads_optimizer_lock.ttl_seconds) <= now()
RETURNING claimed_at
"""

SQL_LIBERAR = "DELETE FROM ads_optimizer_lock WHERE job_key = %s AND owner = %s"
SQL_HEARTBEAT = (
    "UPDATE ads_optimizer_lock SET heartbeat_at = now() WHERE job_key = %s AND owner = %s"
)
SQL_OWNER = "SELECT owner FROM ads_optimizer_lock WHERE job_key = %s"
SQL_ADVISORY_TOMAR = "SELECT pg_try_advisory_lock(hashtext(%s))"
SQL_ADVISORY_SOLTAR = "SELECT pg_advisory_unlock(hashtext(%s))"


def claim_lock(
    conn: psycopg.Connection,
    job_key: str,
    owner: str,
    *,
    ttl_seconds: int | None = None,
) -> datetime | None:
    """Intenta tomar `ads_optimizer_lock`; devuelve `claimed_at` o `None` si ocupado."""
    if ttl_seconds is None:
        fila = conn.execute(SQL_CLAIM, (job_key, owner)).fetchone()
    else:
        fila = conn.execute(SQL_CLAIM_CON_TTL, (job_key, owner, ttl_seconds)).fetchone()
    return None if fila is None else fila[0]


def liberar_lock(conn: psycopg.Connection, job_key: str, owner: str) -> None:
    """Libera `ads_optimizer_lock` solo si sigue perteneciendo a `owner`."""
    conn.execute(SQL_LIBERAR, (job_key, owner))


def latir_lock(
    conn: psycopg.Connection,
    job_key: str,
    owner: str,
    *,
    etiqueta: str = "lock",
) -> None:
    """Actualiza `heartbeat_at = now()` fail-open (el TTL es el backstop)."""
    try:
        conn.execute(SQL_HEARTBEAT, (job_key, owner))
    except psycopg.Error as exc:
        logger.warning("%s latido fallo (TTL es el backstop): %s", etiqueta, scrub(str(exc)))


def tomar_lock_con_advisory(
    conn: psycopg.Connection,
    job_key: str,
    owner: str,
    *,
    ttl_seconds: int,
    error_cls: type[Exception],
    mensaje_ocupado: str,
    mensaje_advisory: str | None = None,
) -> None:
    """Toma claim en tabla + session advisory lock `hashtext(job_key)` sin fugar claim."""
    if claim_lock(conn, job_key, owner, ttl_seconds=ttl_seconds) is None:
        raise error_cls(mensaje_ocupado)
    try:
        obtenido = bool(conn.execute(SQL_ADVISORY_TOMAR, (job_key,)).fetchone()[0])
    except BaseException:
        liberar_lock(conn, job_key, owner)
        raise
    if not obtenido:
        liberar_lock(conn, job_key, owner)
        raise error_cls(mensaje_advisory or mensaje_ocupado)


def soltar_lock_con_advisory(conn: psycopg.Connection, job_key: str, owner: str) -> None:
    """Libera advisory lock de sesion y garantiza liberar el claim de tabla."""
    try:
        conn.execute(SQL_ADVISORY_SOLTAR, (job_key,)).fetchone()
    finally:
        liberar_lock(conn, job_key, owner)


# Tope de espera de los cerrojos del claim (ARREGLOS 05 A, revision): una
# conexion que quede con la transaccion abierta reteniendo la fila del claim
# no puede colgar un horario. Quien no consigue el cerrojo a tiempo se trata
# como si no fuera dueno: no claima, no compromete, no sella.
TIEMPO_MAXIMO_CLAIM = "5s"
TIEMPO_MAXIMO_LATIDO = "3s"


def acota_espera_de_cerrojos(
    conn: psycopg.Connection, tope: str = TIEMPO_MAXIMO_CLAIM, *, transaccional: bool = True
) -> None:
    """`lock_timeout` hasta el fin de la transaccion actual (`transaccional`,
    para el claim, la TX3 y la cerca) o de la sesion (el latido)."""
    conn.execute(
        sql.SQL("SET {}lock_timeout = {}").format(
            sql.SQL("LOCAL " if transaccional else ""), sql.Literal(tope)
        )
    )
