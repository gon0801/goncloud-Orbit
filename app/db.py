"""Conexion a Postgres con DSN redactado en caso de fallo.

Un fallo de conexion (host muerto, timeout, credenciales invalidas) jamas
debe filtrar el DSN crudo -- ni en el mensaje, ni en la cadena
`__cause__`/`__context__` de la excepcion.
"""

from __future__ import annotations

import logging
import os

import psycopg

from app.redaction import install_scrub_filter, redact_dsn, scrub

logger = logging.getLogger(__name__)
install_scrub_filter(logger)


class OrbitDbError(Exception):
    """Error conectando a Postgres. El mensaje NUNCA lleva el DSN crudo."""

    def __init__(self, message: str) -> None:
        super().__init__(scrub(message))


def aplicar_host_override(dsn: str, host: str | None = None) -> str:
    """Reescribe el host de loopback del DSN cuando corre DENTRO del compose.

    Los DSN del `.env` del server apuntan a `127.0.0.1` (bind del host). Dentro
    del contenedor `app` ese address es el propio contenedor, no Postgres: el
    servicio compose se llama `db`. `ORBIT_PG_HOST=db` (compose, 4.1) activa
    el rewrite. En el host y en CI la var no existe y el DSN no se toca.

    Solo sustituye `@127.0.0.1:` y `@localhost:` (separador userinfo/host del
    URI). No inventa un host si el DSN ya apunta a otro.
    """
    if not host:
        return dsn
    for loopback in ("127.0.0.1", "localhost"):
        dsn = dsn.replace(f"@{loopback}:", f"@{host}:")
        dsn = dsn.replace(f"@{loopback}/", f"@{host}/")
    return dsn


def connect(dsn: str, **kw) -> psycopg.Connection:
    """Conecta a Postgres; ante fallo, re-lanza `OrbitDbError` con el DSN redactado.

    El `except` no se re-lanza dentro de si mismo: se guarda el error y se
    lanza DESPUES de que el bloque `try/except` termina, para que la
    excepcion original (que puede traer el DSN crudo en su propio mensaje)
    no quede enganchada como `__context__` de `OrbitDbError`.
    """
    dsn = aplicar_host_override(dsn, os.environ.get("ORBIT_PG_HOST"))
    error: OrbitDbError | None = None
    try:
        return psycopg.connect(dsn, **kw)
    except psycopg.Error:
        error = OrbitDbError(f"no se pudo conectar a la base de datos: {redact_dsn(dsn)}")
    raise error from None


def preparar_lectura_contribucion(conn: psycopg.Connection) -> None:
    """Hash Join solo en ESTA transaccion (SET LOCAL, no session).

    El planner estima los CTE de v_contribucion_entidad / cobertura en
    rows=1 y elige Nested Loop. Medido 2026-09-01 (200 hojas x 90d):
    cogs_diario hace 18k x 18k = 324M filas (~60s la vista). Con
    enable_nestloop=off la misma vista baja a ~2.5s (Hash Join). USERSET:
    orbit_read puede cambiarlo. SET LOCAL muere al COMMIT: un pool no
    hereda nestloop=off. Lo usan el tablero (/contribucion) y el digest
    del ciclo (notifica), que leen las mismas vistas.
    """
    conn.execute("SET LOCAL enable_nestloop = off")
