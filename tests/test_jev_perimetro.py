"""Perimetro JEV con login real (JEV ADS 02, S.3 prueba 1).

Como `app_jev`, leer `decision`, `apply_queue` y `search_term_observation`
falla. Como `app_decide` y como `app_ingest`, leer cualquier tabla `jev_*`
y la vista `jev_senal_vigente` falla. Con login real (CREATE ROLE LOGIN +
GRANT del grupo), no con SET ROLE: es la unica evidencia del REVOKE de la
migracion B. No se salta: sin CREATEROLE falla en vez de callar.
"""

from __future__ import annotations

import os
import socket
from contextlib import contextmanager
from pathlib import Path

import psycopg
import pytest
from psycopg import sql as pgsql
from test_jev_catalogo import TABLAS_JEV
from test_schema import _test_dsn

ROOT = Path(__file__).resolve().parents[1]

ORDEN_PERIMETRO = (
    "0001_initial.sql",
    "0002_apply.sql",
    "0004_ad_entity_kind_product_ad.sql",
    "0049_jev_ads.sql",
    "0050_jev_revision_created_at.sql",
    "0052_jev_senales.sql",
)

VISTA_JEV = "jev_senal_vigente"

TABLAS_MOTOR = ("decision", "apply_queue", "search_term_observation")


def _db_perimetro_nombre() -> str:
    return f"orbit_jev_per_{socket.gethostname().lower()}_{os.getpid()}"


@contextmanager
def db_perimetro():
    dsn = _test_dsn()
    db = _db_perimetro_nombre()
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        for nombre in ORDEN_PERIMETRO:
            conn.execute((ROOT / "migrations" / nombre).read_text(encoding="utf-8"))
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def _login(conn, rol: str, grupo: str) -> None:
    try:
        conn.execute(f"CREATE ROLE {rol} LOGIN PASSWORD 'clave' NOSUPERUSER")
    except psycopg.errors.InsufficientPrivilege:
        pytest.fail(
            "ORBIT_TEST_DSN sin CREATEROLE: esta prueba es la unica evidencia del"
            " perimetro Jev/ciclo y no puede saltarse en silencio"
        )
    conn.execute(f"GRANT {grupo} TO {rol}")


def test_perimetro_jev_con_login_real():
    from psycopg.conninfo import make_conninfo

    with db_perimetro() as conn:
        base = _test_dsn().rsplit("/", 1)[0] + "/" + _db_perimetro_nombre()
        roles = {
            "app_jev": f"jev_per_{os.getpid()}_jev",
            "app_decide": f"jev_per_{os.getpid()}_decide",
            "app_ingest": f"jev_per_{os.getpid()}_ingest",
        }
        for grupo, rol in roles.items():
            _login(conn, rol, grupo)
        try:
            dsn_jev = make_conninfo(base, user=roles["app_jev"], password="clave")
            dsn_decide = make_conninfo(base, user=roles["app_decide"], password="clave")
            dsn_ingest = make_conninfo(base, user=roles["app_ingest"], password="clave")
            with psycopg.connect(dsn_jev, autocommit=True) as jev:
                assert jev.execute("SELECT count(*) FROM jev_revision").fetchone()[0] == 0
                assert jev.execute("SELECT count(*) FROM jev_senal").fetchone()[0] == 0
                assert jev.execute(f"SELECT count(*) FROM {VISTA_JEV}").fetchone()[0] == 0
            with psycopg.connect(dsn_decide, autocommit=True) as decide:
                assert decide.execute("SELECT count(*) FROM decision").fetchone()[0] == 0
                assert decide.execute("SELECT count(*) FROM apply_queue").fetchone()[0] == 0
            with psycopg.connect(dsn_ingest, autocommit=True) as ingest:
                assert ingest.execute("SELECT count(*) FROM decision").fetchone()[0] == 0
                assert ingest.execute("SELECT count(*) FROM apply_queue").fetchone()[0] == 0
            with psycopg.connect(dsn_jev, autocommit=True) as jev:
                for tabla in TABLAS_MOTOR:
                    with pytest.raises(psycopg.errors.InsufficientPrivilege):
                        jev.execute(f"SELECT count(*) FROM {tabla}")
            for dsn in (dsn_decide, dsn_ingest):
                with psycopg.connect(dsn, autocommit=True) as ciclo:
                    for tabla in (*TABLAS_JEV, VISTA_JEV):
                        with pytest.raises(psycopg.errors.InsufficientPrivilege):
                            ciclo.execute(f"SELECT count(*) FROM {tabla}")
        finally:
            for rol in roles.values():
                conn.execute(f"DROP OWNED BY {rol}")
                conn.execute(f"DROP ROLE {rol}")
