"""Conexion a Postgres con DSN redactado en caso de fallo.

Un fallo de conexion (host muerto, timeout, credenciales invalidas) jamas
debe filtrar el DSN crudo -- ni en el mensaje, ni en la cadena
`__cause__`/`__context__` de la excepcion.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg

from app.redaction import install_scrub_filter, redact_dsn, scrub

logger = logging.getLogger(__name__)
install_scrub_filter(logger)

_HOST_KV_RE = re.compile(r"(?i)(\bhost\s*=\s*)(?:127\.0\.0\.1|localhost)(?=\s|$)")


class OrbitDbError(Exception):
    """Error conectando a Postgres. El mensaje NUNCA lleva el DSN crudo."""

    def __init__(self, message: str) -> None:
        super().__init__(scrub(message))


def aplicar_host_override(dsn: str, host: str | None = None) -> str:
    """Reescribe el host de loopback del DSN cuando corre DENTRO del compose.

    Soporta formato URI (`@127.0.0.1:` / `@localhost/`) y formato conninfo
    (`host=127.0.0.1` / `host=localhost`). No inventa un host si el DSN ya
    apunta a otro.
    """
    if not host:
        return dsn
    for loopback in ("127.0.0.1", "localhost"):
        dsn = dsn.replace(f"@{loopback}:", f"@{host}:")
        dsn = dsn.replace(f"@{loopback}/", f"@{host}/")
    return _HOST_KV_RE.sub(rf"\g<1>{host}", dsn)


def connect(dsn: str, **kw) -> psycopg.Connection:
    """Conecta a Postgres; ante fallo, re-lanza `OrbitDbError` con el DSN redactado.

    Registra siempre la password del DSN en `redaction` antes de conectar
    (defensa en profundidad incluso cuando la conexion tiene exito).
    """
    dsn = aplicar_host_override(dsn, os.environ.get("ORBIT_PG_HOST"))
    dsn_seguro = redact_dsn(dsn)
    error: OrbitDbError | None = None
    try:
        return psycopg.connect(dsn, **kw)
    except psycopg.Error:
        error = OrbitDbError(f"no se pudo conectar a la base de datos: {dsn_seguro}")
    raise error from None


@contextmanager
def tx_durable(conn: psycopg.Connection) -> Iterator[None]:
    """Transaccion que garantiza COMMIT real al salir sin error.

    Evita el bug de SAVEPOINT fantasma en psycopg3 cuando una lectura previa
    dejo `conn` en `INTRANS` (`autocommit=False`): al salir limpiamente del
    bloque hace `conn.commit()` para que los sellos durables queden realmente
    persistidos antes del siguiente paso HTTP o de un rollback posterior.
    """
    with conn.transaction():
        yield
    if not conn.autocommit:
        conn.commit()


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


_SQL_CONFIG_VIGENTE = "SELECT id, settings FROM config_version ORDER BY id DESC LIMIT 1"


def config_vigente(conn: psycopg.Connection) -> tuple[int, dict] | None:
    """Devuelve `(id, settings)` de la `config_version` mas reciente, o `None`."""
    fila = conn.execute(_SQL_CONFIG_VIGENTE).fetchone()
    if fila is None:
        return None
    if isinstance(fila, dict):
        cid, settings = fila["id"], fila["settings"]
    else:
        cid, settings = fila[0], fila[1]
    if settings is None:
        return None
    return (int(cid), settings)


def settings_vigentes(conn: psycopg.Connection) -> dict | None:
    """Devuelve `settings` de la `config_version` mas reciente, o `None`."""
    vigente = config_vigente(conn)
    return None if vigente is None else vigente[1]
