"""Conexion a Postgres con DSN redactado en caso de fallo.

Un fallo de conexion (host muerto, timeout, credenciales invalidas) jamas
debe filtrar el DSN crudo -- ni en el mensaje, ni en la cadena
`__cause__`/`__context__` de la excepcion.
"""

from __future__ import annotations

import logging

import psycopg

from app.redaction import install_scrub_filter, redact_dsn, scrub

logger = logging.getLogger(__name__)
install_scrub_filter(logger)


class OrbitDbError(Exception):
    """Error conectando a Postgres. El mensaje NUNCA lleva el DSN crudo."""

    def __init__(self, message: str) -> None:
        super().__init__(scrub(message))


def connect(dsn: str, **kw) -> psycopg.Connection:
    """Conecta a Postgres; ante fallo, re-lanza `OrbitDbError` con el DSN redactado.

    El `except` no se re-lanza dentro de si mismo: se guarda el error y se
    lanza DESPUES de que el bloque `try/except` termina, para que la
    excepcion original (que puede traer el DSN crudo en su propio mensaje)
    no quede enganchada como `__context__` de `OrbitDbError`.
    """
    error: OrbitDbError | None = None
    try:
        return psycopg.connect(dsn, **kw)
    except psycopg.Error:
        error = OrbitDbError(f"no se pudo conectar a la base de datos: {redact_dsn(dsn)}")
    raise error from None
