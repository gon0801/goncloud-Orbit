"""Tests de la migracion `migrations/0064_bids02_grupo_tipo.sql` (BIDS 02, I.1).

ORDEN64 es la lista ORDEN de tests/test_fabrica_migracion.py mas la 0064:
un grupo que ya existia queda en `fabrica5`, un grupo `impulso` admite solo
exact y auto, y la reversa deja el esquema como estaba.
"""

from __future__ import annotations

from pathlib import Path

import psycopg
import pytest
from test_fabrica_migracion import ORDEN, _grupo, _lote, db_fabrica
from test_schema import _postgres_obligatorio_ausente

ROOT = Path(__file__).resolve().parents[1]
MIGRACIONES = ROOT / "migrations"
ORDEN64 = ORDEN + ("0064_bids02_grupo_tipo.sql",)
SQL64 = (MIGRACIONES / "0064_bids02_grupo_tipo.sql").read_text(encoding="utf-8")
REVERSA64 = (MIGRACIONES / "0064_reversa_bids02_grupo_tipo.sql").read_text(encoding="utf-8")

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)


def _entidad(conn, kind, external, parent=None) -> int:
    return conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id, parent_id)"
        " VALUES ('amazon_mx', %s, %s, %s) RETURNING id",
        (kind, external, parent),
    ).fetchone()[0]


def _rol(conn, grupo, rol, camp, ag) -> None:
    conn.execute(
        "INSERT INTO campana_grupo_rol (grupo_id, rol, ad_entity_id, ad_group_ad_entity_id)"
        " VALUES (%s, %s::campana_rol, %s, %s)",
        (grupo, rol, camp, ag),
    )


@_skip_db
def test_grupo_existente_queda_fabrica5():
    """I.1: con la 0064 aplicada, un grupo creado sin tipo queda fabrica5."""
    with db_fabrica("orbit_i1_tipo") as conn:
        lote = _lote(conn)
        grupo = _grupo(conn, lote)
        conn.execute(SQL64)
        assert conn.execute(
            "SELECT tipo FROM campana_grupo WHERE id = %s", (grupo,)
        ).fetchone() == ("fabrica5",)


@_skip_db
def test_impulso_rechaza_tercer_rol():
    """I.1: un grupo impulso admite exact y auto; un tercer rol lo rechaza
    la base."""
    with db_fabrica("orbit_i1_tercer") as conn:
        conn.execute(SQL64)
        lote = _lote(conn, "fabrica-impulso-1")
        grupo = conn.execute(
            "INSERT INTO campana_grupo (platform, tipo_producto, nombre_base, lote,"
            " target_acos_pct, target_derivado_pct, fraccion, target_procedencia,"
            " go_literal, tipo) VALUES ('amazon_mx', 'collar_perro', 'Collar', %s,"
            " 19.10, 19.1040, 0.5, 'm', 'go', 'impulso') RETURNING id",
            (lote,),
        ).fetchone()[0]
        camp_e = _entidad(conn, "campaign", "c-exact")
        ag_e = _entidad(conn, "ad_group", "g-exact", camp_e)
        camp_a = _entidad(conn, "campaign", "c-auto")
        ag_a = _entidad(conn, "ad_group", "g-auto", camp_a)
        _rol(conn, grupo, "category_exact", camp_e, ag_e)
        _rol(conn, grupo, "auto_discovery", camp_a, ag_a)
        camp_p = _entidad(conn, "campaign", "c-phrase")
        ag_p = _entidad(conn, "ad_group", "g-phrase", camp_p)
        with pytest.raises(psycopg.errors.RaiseException, match="impulso"):
            conn.execute(
                "INSERT INTO campana_grupo_rol (grupo_id, rol, ad_entity_id,"
                " ad_group_ad_entity_id) VALUES (%s, 'category_phrase'::campana_rol, %s, %s)",
                (grupo, camp_p, ag_p),
            )


@_skip_db
def test_tipo_null_o_fuera_de_vocabulario_se_rechaza():
    """I.1: tipo NULL o fuera de fabrica5/impulso se rechaza."""
    with db_fabrica("orbit_i1_vocab") as conn:
        conn.execute(SQL64)
        lote = _lote(conn, "fabrica-vocab-1")
        with pytest.raises(psycopg.errors.NotNullViolation):
            conn.execute(
                "INSERT INTO campana_grupo (platform, tipo_producto, nombre_base, lote,"
                " target_acos_pct, target_derivado_pct, fraccion, target_procedencia,"
                " go_literal, tipo) VALUES ('amazon_mx', 'c', 'C', %s,"
                " 19.10, 19.1040, 0.5, 'm', 'go', NULL)",
                (lote,),
            )
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO campana_grupo (platform, tipo_producto, nombre_base, lote,"
                " target_acos_pct, target_derivado_pct, fraccion, target_procedencia,"
                " go_literal, tipo) VALUES ('amazon_mx', 'c', 'C', %s,"
                " 19.10, 19.1040, 0.5, 'm', 'go', 'otro')",
                (lote,),
            )


@_skip_db
def test_reversa_deja_esquema_como_estaba():
    """I.1: la reversa quita columna, trigger y CHECK; el esquema queda como
    estaba (sin tipo, tercer rol admitido)."""
    with db_fabrica("orbit_i1_rev") as conn:
        conn.execute(SQL64)
        conn.execute(REVERSA64)
        assert conn.execute(
            "SELECT count(*) FROM information_schema.columns"
            " WHERE table_name = 'campana_grupo' AND column_name = 'tipo'"
        ).fetchone() == (0,)
        assert conn.execute(
            "SELECT count(*) FROM pg_trigger WHERE tgname = 'campana_grupo_rol_roles_de_impulso'"
        ).fetchone() == (0,)
