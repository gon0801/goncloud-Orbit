"""Base temporal de Postgres desde un template unico por sesion.

Evita recrear y migrar 90+ archivos SQL en cada test: construye UNA base
template con el esquema canonico (`migrations/*.sql` menos `0011_*` y
`*_reversa_*`, misma regla que `verify/Launch.md`) y clona cada test con
`CREATE DATABASE ... TEMPLATE` en decenas de milisegundos.
"""

from __future__ import annotations

import atexit
import os
import socket
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg import sql

RAIZ = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = RAIZ / "migrations"
DSN_DEFAULT = "postgresql://orbit:orbit@127.0.0.1:5432/postgres"

_LOCK = threading.Lock()
_TEMPLATES: dict[str, str] = {}


def dsn_base() -> str:
    return os.environ.get("ORBIT_TEST_DSN", DSN_DEFAULT)


def dsn_de_db(base: str, nombre_db: str) -> str:
    partes = urlsplit(base)
    return urlunsplit(
        (partes.scheme, partes.netloc, f"/{nombre_db}", partes.query, partes.fragment)
    )


def pg_disponible(base: str | None = None) -> bool:
    url = urlsplit(base or dsn_base())
    host = url.hostname or "127.0.0.1"
    port = url.port or 5432
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


def migraciones_canonicas() -> tuple[Path, ...]:
    """Migraciones aplicadas sobre una base vacia (regla de verify/Launch.md)."""
    rutas: list[Path] = []
    for ruta in sorted(MIGRATIONS_DIR.glob("*.sql")):
        nombre = ruta.name
        if "0011_" in nombre or "_reversa_" in nombre:
            continue
        rutas.append(ruta)
    return tuple(rutas)


def _aplicar_migracion(conn: psycopg.Connection, contenido: str) -> None:
    """Ejecuta una migracion en autocommit para tolerar `ALTER TYPE ... ADD VALUE`."""
    conn.execute(contenido)


def asegurar_template(base: str | None = None) -> str:
    """Devuelve el nombre de la DB template canonica para `base`, creandola una vez."""
    dsn = base or dsn_base()
    with _LOCK:
        existente = _TEMPLATES.get(dsn)
        if existente is not None:
            return existente

        nombre = f"orbit_tpl_{os.getpid()}_{uuid.uuid4().hex[:8]}"
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(nombre)))
        try:
            with psycopg.connect(dsn_de_db(dsn, nombre), autocommit=True) as tpl_conn:
                for ruta in migraciones_canonicas():
                    _aplicar_migracion(tpl_conn, ruta.read_text(encoding="utf-8"))
            with psycopg.connect(dsn, autocommit=True) as admin:
                admin.execute(
                    sql.SQL("ALTER DATABASE {} WITH is_template = true").format(
                        sql.Identifier(nombre)
                    )
                )
        except BaseException:
            _borrar_db(dsn, nombre, es_template=True)
            raise

        _TEMPLATES[dsn] = nombre
        return nombre


def _borrar_db(dsn: str, nombre: str, *, es_template: bool = False) -> None:
    try:
        with psycopg.connect(dsn, autocommit=True) as admin:
            if es_template:
                admin.execute(
                    sql.SQL("ALTER DATABASE {} WITH is_template = false").format(
                        sql.Identifier(nombre)
                    )
                )
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(nombre))
            )
    except psycopg.Error:
        pass


def limpiar_templates() -> None:
    """Elimina todas las DB template creadas por este proceso."""
    with _LOCK:
        pendientes = list(_TEMPLATES.items())
        _TEMPLATES.clear()
    for dsn, nombre in pendientes:
        _borrar_db(dsn, nombre, es_template=True)


atexit.register(limpiar_templates)


@contextmanager
def db_desde_template(
    prefijo: str = "orbit_t",
    *,
    base: str | None = None,
    autocommit: bool = False,
) -> Iterator[tuple[psycopg.Connection, str]]:
    """Crea una DB aislada desde el template canonico, entrega `(conn, dsn)` y la borra."""
    dsn = base or dsn_base()
    template = asegurar_template(dsn)
    nombre = f"{prefijo}_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
                sql.Identifier(nombre), sql.Identifier(template)
            )
        )
    dsn_hija = dsn_de_db(dsn, nombre)
    conn = psycopg.connect(dsn_hija, autocommit=autocommit)
    try:
        yield conn, dsn_hija
    finally:
        conn.close()
        _borrar_db(dsn, nombre)
