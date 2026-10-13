"""Tests de la migracion `migrations/0071_bids02_grant_vista_impulso.sql` (BIDS 02, R09-E)."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_fabrica_migracion import db_fabrica
from test_migracion_0064 import SQL64
from test_migracion_0065 import SQL65
from test_schema import _postgres_obligatorio_ausente

ROOT = Path(__file__).resolve().parents[1]
MIGRACIONES = ROOT / "migrations"
SQL71 = (MIGRACIONES / "0071_bids02_grant_vista_impulso.sql").read_text(encoding="utf-8")
REVERSA71 = (MIGRACIONES / "0071_reversa_bids02_grant_vista_impulso.sql").read_text(
    encoding="utf-8"
)

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)


def _select_vista(conn, rol):
    return conn.execute(
        "SELECT has_table_privilege(%s, 'public.v_impulso_estado', 'SELECT')", (rol,)
    ).fetchone()[0]


def _marca_0071(conn):
    return conn.execute(
        "SELECT obj_description('public.v_impulso_estado'::regclass, 'pg_class')"
    ).fetchone()[0]


@_skip_db
def test_0071_grant_explicito_sobre_la_vista_y_su_reversa():
    """R09-E: la 0065 crea la vista sin GRANT propio; la 0071 declara
    el SELECT de app_read y app_admin, y su reversa lo quita."""
    with db_fabrica("orbit_71_grant") as conn:
        conn.execute(SQL64)
        conn.execute(SQL65)
        # Estado sin el GRANT explicito de la 0071, sin depender de los
        # default privileges del esquema.
        conn.execute("REVOKE SELECT ON v_impulso_estado FROM app_read, app_admin")
        conn.commit()
        assert _select_vista(conn, "app_read") is False
        assert _select_vista(conn, "app_admin") is False
        conn.execute(SQL71)
        conn.commit()
        assert _select_vista(conn, "app_read") is True
        assert _select_vista(conn, "app_admin") is True
        # La marca distingue la 0071 aplicada del privilegio que ya
        # venia de los default privileges.
        assert _marca_0071(conn) == "GRANT SELECT declarado por la migracion 0071 (BIDS 02 R09-E)"
        for rol in ("app_read", "app_admin"):
            for privilegio in ("INSERT", "UPDATE", "DELETE"):
                assert (
                    conn.execute(
                        "SELECT has_table_privilege(%s, 'public.v_impulso_estado', %s)",
                        (rol, privilegio),
                    ).fetchone()[0]
                    is False
                )
        conn.execute(REVERSA71)
        conn.commit()
        assert _select_vista(conn, "app_read") is False
        assert _select_vista(conn, "app_admin") is False
        assert _marca_0071(conn) is None
