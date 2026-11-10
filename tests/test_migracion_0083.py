"""Migracion 0083 (ARREGLOS 01 F.2): la validez del costo ya no es global.

Una corrida ok de costos con skips por fila (costo cero, una copia truncada de
otro SKU) no puede dejar a todos los productos en costo_desactualizado. La
reversa restaura la definicion exacta de la 0073 y vuelve a exigir
rows_skipped = 0.
"""

from __future__ import annotations

import itertools
import os
import socket
from contextlib import contextmanager
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from test_migracion_0073 import (
    MIGRACIONES,
    ORDEN_73,
    _minimo,
    _producto,
    _skip_db,
)

ORDEN_83 = ORDEN_73 + ("0083_arreglos01_costos_sin_compuerta.sql",)
_CONTADOR = itertools.count()


@contextmanager
def _db83():
    from psycopg import sql as pgsql

    dsn = os.environ.get("ORBIT_TEST_DSN", "postgresql://orbit:orbit@localhost:5432/postgres")
    partes = urlsplit(dsn)
    db = f"arreglos01f83_{socket.gethostname().lower()}_{os.getpid()}_{next(_CONTADOR)}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(
            urlunsplit((partes.scheme, partes.netloc, "/" + db, partes.query, partes.fragment)),
            autocommit=True,
        )
        conn.execute("SET TIME ZONE 'UTC'")
        for nombre in ORDEN_83:
            conn.execute((MIGRACIONES / nombre).read_text(encoding="utf-8"))
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


@_skip_db
def test_0083_una_corrida_con_skips_si_valida_el_costo():
    with _db83() as conn:
        pa = _producto(conn, "SKU-83-A")
        _minimo(conn, (pa,), plataforma="amazon_mx", miembro=None, costo=pa, rows_skipped=1)


@_skip_db
def test_0083_reversa_restaura_la_compuerta_de_skips():
    reversa = (MIGRACIONES / "0083_reversa_arreglos01_costos_sin_compuerta.sql").read_text(
        encoding="utf-8"
    )
    with _db83() as conn:
        conn.execute(reversa)
        pa = _producto(conn, "SKU-83-A")
        with pytest.raises(
            psycopg.errors.CheckViolation,
            match="no es accounting_sku_costs ok",
        ):
            _minimo(conn, (pa,), plataforma="amazon_mx", miembro=None, costo=pa, rows_skipped=1)
