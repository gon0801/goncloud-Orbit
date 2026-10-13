"""Tests de la migracion `migrations/0072_bids02_impulso_grupo_tipo.sql` (BIDS 02, R09-J)."""

from __future__ import annotations

from pathlib import Path

import psycopg
import pytest
from test_fabrica_migracion import _grupo, _lote, _producto, db_fabrica
from test_migracion_0065 import SQL64, SQL65, _impulso
from test_schema import _postgres_obligatorio_ausente

ROOT = Path(__file__).resolve().parents[1]
MIGRACIONES = ROOT / "migrations"
SQL72 = (MIGRACIONES / "0072_bids02_impulso_grupo_tipo.sql").read_text(encoding="utf-8")
REVERSA72 = (MIGRACIONES / "0072_reversa_bids02_impulso_grupo_tipo.sql").read_text(encoding="utf-8")

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)


@_skip_db
def test_0072_impulso_sobre_grupo_fabrica5_se_rechaza():
    """R09-J: con la 0072, un impulso sobre un grupo fabrica5 se
    rechaza en la base, venga de donde venga; sobre un grupo impulso
    se acepta, y la reversa quita el candado."""
    with db_fabrica("orbit_72_grupo") as conn:
        conn.execute(SQL64)
        conn.execute(SQL65)
        conn.execute(SQL72)
        pid, lid = _producto(conn)
        lote = _lote(conn, "fabrica-imp-72")
        grupo5 = _grupo(conn, lote)
        lote2 = _lote(conn, "fabrica-imp-72b")
        grupo_imp = conn.execute(
            "INSERT INTO campana_grupo (platform, tipo_producto, nombre_base, lote, tipo,"
            " target_acos_pct, target_derivado_pct, fraccion, target_procedencia, go_literal)"
            " VALUES ('amazon_mx', 'collar_perro', 'Collar', %s, 'impulso', 19.10, 19.1040,"
            " 0.5, 'margen_minimo_grupo', 'go') RETURNING id",
            (lote2,),
        ).fetchone()[0]
        conn.commit()
        with pytest.raises(psycopg.errors.RaiseException, match="no es de tipo impulso"):
            _impulso(conn, lote, grupo5, pid, lid)
        conn.rollback()
        imp = _impulso(conn, lote2, grupo_imp, pid, lid)
        assert conn.execute("SELECT count(*) FROM impulso WHERE id = %s", (imp,)).fetchone() == (1,)
        # El candado tambien cierra el UPDATE: un grupo con impulsos no
        # puede dejar de ser tipo impulso despues.
        with pytest.raises(psycopg.errors.RaiseException, match="no puede dejar de ser"):
            conn.execute("UPDATE campana_grupo SET tipo = 'fabrica5' WHERE id = %s", (grupo_imp,))
        conn.rollback()
        conn.execute(REVERSA72)
        conn.commit()
        imp5 = _impulso(conn, lote, grupo5, pid, lid)
        assert conn.execute("SELECT count(*) FROM impulso WHERE id = %s", (imp5,)).fetchone() == (
            1,
        )
        conn.rollback()


@_skip_db
def test_0072_grupo_sin_impulsos_si_puede_cambiar_de_tipo():
    """R14-r2: la guardia de UPDATE solo cierra el tipo de grupos que
    ya tienen impulsos; un grupo tipo impulso sin impulsos puede
    cambiar de tipo."""
    with db_fabrica("orbit_72_libre") as conn:
        conn.execute(SQL64)
        conn.execute(SQL65)
        conn.execute(SQL72)
        lote = _lote(conn, "fabrica-imp-72c")
        grupo_imp = conn.execute(
            "INSERT INTO campana_grupo (platform, tipo_producto, nombre_base, lote, tipo,"
            " target_acos_pct, target_derivado_pct, fraccion, target_procedencia, go_literal)"
            " VALUES ('amazon_mx', 'collar_perro', 'Collar', %s, 'impulso', 19.10, 19.1040,"
            " 0.5, 'margen_minimo_grupo', 'go') RETURNING id",
            (lote,),
        ).fetchone()[0]
        conn.execute("UPDATE campana_grupo SET tipo = 'fabrica5' WHERE id = %s", (grupo_imp,))
        conn.commit()
        assert conn.execute(
            "SELECT tipo FROM campana_grupo WHERE id = %s", (grupo_imp,)
        ).fetchone() == ("fabrica5",)


@_skip_db
def test_0072_con_impulso_previo_sobre_fabrica5_aborta():
    """R14-r2: si ya existe un impulso sobre un grupo que no es tipo
    impulso, la 0072 aborta en su verificacion en vez de dejar el
    candado aplicado sobre el dato roto."""
    with db_fabrica("orbit_72_previo") as conn:
        conn.execute(SQL64)
        conn.execute(SQL65)
        pid, lid = _producto(conn)
        lote = _lote(conn, "fabrica-imp-72d")
        grupo5 = _grupo(conn, lote)
        _impulso(conn, lote, grupo5, pid, lid)
        conn.commit()
        with pytest.raises(psycopg.errors.RaiseException, match="ya hay impulsos"):
            conn.execute(SQL72)
        conn.rollback()
