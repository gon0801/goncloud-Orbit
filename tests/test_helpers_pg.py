"""Tests del helper compartido de base template por sesion (`tests/helpers/pg.py`)."""

from __future__ import annotations

import pytest

from tests.helpers.pg import db_desde_template, migraciones_canonicas, pg_disponible


def test_migraciones_canonicas_omite_0011_y_reversas():
    nombres = [p.name for p in migraciones_canonicas()]
    assert "0001_initial.sql" in nombres
    assert all("0011_" not in n for n in nombres)
    assert all("_reversa_" not in n for n in nombres)


@pytest.mark.skipif(not pg_disponible(), reason="sin Postgres local")
def test_db_desde_template_crea_esquema_completo_y_aisla():
    with db_desde_template("orbit_tpl_a") as (conn_a, _dsn_a):
        tablas = conn_a.execute(
            "SELECT count(*) FROM information_schema.tables"
            " WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
        ).fetchone()[0]
        assert tablas >= 92
        conn_a.execute("INSERT INTO config_version (label, settings) VALUES ('test', '{}'::jsonb)")
        conn_a.commit()
        total_a = conn_a.execute("SELECT count(*) FROM config_version").fetchone()[0]

    with db_desde_template("orbit_tpl_b") as (conn_b, _dsn_b):
        total_b = conn_b.execute("SELECT count(*) FROM config_version").fetchone()[0]

    assert total_b == total_a - 1
