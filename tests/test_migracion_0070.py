"""Migracion 0070 (BIDS 02 R09-r5 B1b): app_admin puede DELETE en
fabrica_lote (H6: archivar el lote vacio fallido libera el nombre
canonico para el reintento; sin este permiso el producto queda
bloqueado en produccion)."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_fabrica_migracion import db_fabrica
from test_migracion_0065 import SQL64, SQL65
from test_schema import _postgres_obligatorio_ausente

MIGRACIONES = Path(__file__).resolve().parents[1] / "migrations"
SQL66 = (MIGRACIONES / "0066_bids02_retiro_anuncios.sql").read_text(encoding="utf-8")
SQL68 = (MIGRACIONES / "0068_bids02_sellos.sql").read_text(encoding="utf-8")
SQL69 = (MIGRACIONES / "0069_bids02_retiro_intencion.sql").read_text(encoding="utf-8")
SQL70 = (MIGRACIONES / "0070_bids02_permisos_lanza.sql").read_text(encoding="utf-8")

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)


@_skip_db
def test_0070_app_admin_borra_lote():
    """0070: como app_admin (rol de la app, no superusuario) se puede
    DELETE una fila de fabrica_lote; sin el GRANT levanta
    InsufficientPrivilege."""
    import psycopg

    with db_fabrica("orbit_m70") as conn:
        conn.execute(SQL64)
        conn.execute(SQL65)
        conn.execute(SQL66)
        conn.execute(SQL68)
        conn.execute(SQL69)
        conn.execute(SQL70)
        conn.execute(
            "INSERT INTO fabrica_lote (lote, platform, tipo_producto, nombre_base, go_literal,"
            " huella, plan, modo_goal, estado) VALUES ('l-70', 'amazon_mx', 'aros', 'Base',"
            " 'go', 'h', '{}'::jsonb, 'shadow', 'failed')"
        )
        conn.execute("SET ROLE app_admin")
        try:
            conn.execute("DELETE FROM fabrica_lote WHERE lote = 'l-70'")
        except psycopg.errors.InsufficientPrivilege as exc:
            raise AssertionError("0070: app_admin no puede DELETE en fabrica_lote") from exc
        finally:
            conn.execute("RESET ROLE")
        assert conn.execute("SELECT count(*) FROM fabrica_lote WHERE lote = 'l-70'").fetchone() == (
            0,
        )
        conn.execute("SET ROLE app_admin")
        try:
            conn.execute(
                "INSERT INTO ad_entity (platform, kind, external_id, name)"
                " VALUES ('amazon_mx', 'campaign', 'c-70', 'C70')"
            )
        except psycopg.errors.InsufficientPrivilege as exc:
            raise AssertionError("0070: app_admin no puede INSERT en ad_entity") from exc
        finally:
            conn.execute("RESET ROLE")
        assert conn.execute(
            "SELECT count(*) FROM ad_entity WHERE external_id = 'c-70'"
        ).fetchone() == (1,)
