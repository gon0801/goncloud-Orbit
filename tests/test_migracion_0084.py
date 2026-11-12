"""Migracion 0084 (ARREGLOS 01 Q.2): impulso_lectura admite 'desarmado'.

Un impulso cuyo grupo se pauso desde la fabrica queda con lote terminal
'desarmado'; el vigia debe cerrarlo con una lectura final 'desarmado'. La
reversa no bloquea el rollback: borra esas lecturas (solo las que escribio
este codigo) y restaura el CHECK y el trigger append-only de la 0065, para que
el codigo anterior no se encuentre un veredicto que no sabe mapear.
"""

from __future__ import annotations

from pathlib import Path

import psycopg
import pytest
from test_fabrica_migracion import _grupo, _lote, _producto, db_fabrica
from test_migracion_0065 import SQL64, SQL65, _impulso
from test_schema import _postgres_obligatorio_ausente

MIGRACIONES = Path(__file__).resolve().parents[1] / "migrations"
SQL84 = (MIGRACIONES / "0084_arreglos01_impulso_desarmado.sql").read_text(encoding="utf-8")
REVERSA84 = (MIGRACIONES / "0084_reversa_arreglos01_impulso_desarmado.sql").read_text(
    encoding="utf-8"
)

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)


def _impulso_nuevo(conn) -> int:
    pid, lid = _producto(conn)
    lote = _lote(conn, "fabrica-imp-84")
    grupo = _grupo(conn, lote)
    return _impulso(conn, lote, grupo, pid, lid)


def _lectura(conn, imp: int, veredicto: str, corte: str = "2026-10-09"):
    conn.execute(
        "INSERT INTO impulso_lectura (impulso_id, corte, leido_el, dias_observados,"
        " veredicto, es_final, hecho) VALUES (%s, %s, now(), 19, %s, true, 'pausado')",
        (imp, corte, veredicto),
    )


@_skip_db
def test_0084_lectura_desarmado_se_inserta():
    """Con la 0084, el vigia puede guardar la lectura final 'desarmado'."""
    with db_fabrica("arreglos01q84_ok") as conn:
        conn.execute(SQL64)
        conn.execute(SQL65)
        conn.execute(SQL84)
        _lectura(conn, _impulso_nuevo(conn), "desarmado")
        assert conn.execute(
            "SELECT veredicto, es_final, hecho FROM impulso_lectura"
        ).fetchone() == ("desarmado", True, "pausado")


@_skip_db
def test_0084_reversa_no_bloquea_el_rollback_y_borra_solo_desarmado():
    """La reversa no falla con filas 'desarmado': las borra (solo esas),
    restaura el CHECK de la 0065 y vuelve a encender el trigger append-only."""
    with db_fabrica("arreglos01q84_rev") as conn:
        conn.execute(SQL64)
        conn.execute(SQL65)
        conn.execute(SQL84)
        imp = _impulso_nuevo(conn)
        _lectura(conn, imp, "sin_impresiones", corte="2026-10-08")
        _lectura(conn, imp, "desarmado", corte="2026-10-09")
        conn.execute(REVERSA84)
        assert conn.execute("SELECT veredicto FROM impulso_lectura ORDER BY corte").fetchall() == [
            ("sin_impresiones",)
        ]
        with pytest.raises(psycopg.errors.CheckViolation, match="lectura_veredicto"):
            _lectura(conn, imp, "desarmado", corte="2026-10-10")
        with pytest.raises(psycopg.errors.RaiseException, match="inmutable"):
            conn.execute("DELETE FROM impulso_lectura")


@_skip_db
def test_0084_reversa_sin_filas_desarmado_vuelve_al_check_de_la_0065():
    """La reversa restaura el vocabulario de la 0065."""
    with db_fabrica("arreglos01q84_rev2") as conn:
        conn.execute(SQL64)
        conn.execute(SQL65)
        conn.execute(SQL84)
        conn.execute(REVERSA84)
        with pytest.raises(psycopg.errors.CheckViolation, match="lectura_veredicto"):
            _lectura(conn, _impulso_nuevo(conn), "desarmado")
