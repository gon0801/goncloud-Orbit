"""Migracion 0068 (BIDS 02 R09-r4): actor de la pausa y hecho de graduacion.

fabrica_lote.pausado_por (actor que dejo el lote en pausado) e
impulso_lectura.hecho 'graduado_sin_reencender'."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_fabrica_migracion import db_fabrica
from test_migracion_0065 import SQL64, SQL65
from test_schema import _postgres_obligatorio_ausente

MIGRACIONES = Path(__file__).resolve().parents[1] / "migrations"
SQL66 = (MIGRACIONES / "0066_bids02_retiro_anuncios.sql").read_text(encoding="utf-8")
SQL68 = (MIGRACIONES / "0068_bids02_sellos.sql").read_text(encoding="utf-8")

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)


@_skip_db
def test_0068_pausado_por_nace_nulo():
    """0068: fabrica_lote.pausado_por existe y nace NULL."""
    with db_fabrica("orbit_m68b") as conn:
        conn.execute(SQL64)
        conn.execute(SQL65)
        conn.execute(SQL66)
        conn.execute(SQL68)
        conn.commit()
        conn.execute(
            "INSERT INTO fabrica_lote (lote, platform, tipo_producto, nombre_base, go_literal,"
            " huella, plan, modo_goal, estado) VALUES ('l-68', 'amazon_mx', 'aros', 'Base',"
            " 'go', 'h', '{}'::jsonb, 'shadow', 'applied')"
        )
        assert (
            conn.execute("SELECT pausado_por FROM fabrica_lote WHERE lote = 'l-68'").fetchone()[0]
            is None
        )


@_skip_db
def test_0068_hecho_graduado_sin_reencender():
    """0068: impulso_lectura acepta el hecho 'graduado_sin_reencender'."""
    with db_fabrica("orbit_m68c") as conn:
        conn.execute(SQL64)
        conn.execute(SQL65)
        conn.execute(SQL66)
        conn.execute(SQL68)
        conn.commit()
        defin = conn.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'lectura_hecho'"
        ).fetchone()[0]
        assert "graduado_sin_reencender" in defin
