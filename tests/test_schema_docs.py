"""Coherencia Launch/Doctor: mismo conteo estricto, igual al esquema (A7 B6)."""

import os
import re
import socket
from pathlib import Path

import psycopg
import pytest
from psycopg import sql as pgsql
from test_schema import _postgres_obligatorio_ausente, _test_dsn

ROOT = Path(__file__).resolve().parents[1]
_CHEQUEO = re.compile(r'test "\$TABS" = (\d+)')


def _conteo_declarado(nombre: str) -> int:
    texto = (ROOT / "verify" / nombre).read_text(encoding="utf-8")
    hallado = _CHEQUEO.search(texto)
    assert hallado is not None, f"{nombre} sin chequeo test $TABS = N"
    return int(hallado.group(1))


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_launch_doctor_mismo_conteo_que_esquema():
    """A7 B6 (CodeRabbit: un numero, una fuente): Launch.md y Doctor.md
    declaran el MISMO conteo estricto de tablas BASE, y coincide con
    migrar todo de cero (salvo 0011/reversas, como Launch)."""
    n_launch = _conteo_declarado("Launch.md")
    assert _conteo_declarado("Doctor.md") == n_launch
    dsn = _test_dsn()
    db = f"orbit_docs_conteo_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        try:
            for ruta in sorted((ROOT / "migrations").glob("*.sql")):
                if "0011_" in ruta.name or "_reversa_" in ruta.name:
                    continue
                conn.execute(ruta.read_text(encoding="utf-8"))
            (n_real,) = conn.execute(
                "select count(*) from information_schema.tables"
                " where table_schema = 'public' and table_type = 'BASE TABLE'"
            ).fetchone()
        finally:
            conn.close()
    finally:
        admin.execute(pgsql.SQL("DROP DATABASE {}").format(pgsql.Identifier(db)))
        admin.close()
    assert n_launch == n_real
