"""Tests de la migracion `migrations/0065_bids02_impulso.sql` (BIDS 02, I.3)."""

from __future__ import annotations

from pathlib import Path

import psycopg
import pytest
from test_fabrica_migracion import (
    ORDEN,
    _grupo,
    _lote,
    _producto,
    db_fabrica,
)
from test_schema import _postgres_obligatorio_ausente

ROOT = Path(__file__).resolve().parents[1]
MIGRACIONES = ROOT / "migrations"
SQL64 = (MIGRACIONES / "0064_bids02_grupo_tipo.sql").read_text(encoding="utf-8")
SQL65 = (MIGRACIONES / "0065_bids02_impulso.sql").read_text(encoding="utf-8")
REVERSA65 = (MIGRACIONES / "0065_reversa_bids02_impulso.sql").read_text(encoding="utf-8")
ORDEN65 = ORDEN + ("0064_bids02_grupo_tipo.sql", "0065_bids02_impulso.sql")

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)


def _impulso(conn, lote, grupo, producto, listing, *, moneda="MXN", platform="amazon_mx"):
    return conn.execute(
        "INSERT INTO impulso (platform, listing_id, product_id, grupo_id, lote, subfamilia,"
        " tope_monto, tope_moneda, presupuesto_diario, plazo_dias, lanzado_el, actor,"
        " go_literal) VALUES (%s::platform, %s, %s, %s, %s, 'aros', 350, %s, 25, 14,"
        " now(), 'dueno', 'go') RETURNING id",
        (platform, listing, producto, grupo, lote, moneda),
    ).fetchone()[0]


@_skip_db
def test_dinero_con_dominio_y_enum_sellados():
    """I.3 (panel): montos en money_amount y moneda en currency (regla
    4 por schema), no NUMERIC pelado ni TEXT."""
    with db_fabrica("orbit_65_dinero") as conn:
        conn.execute(SQL64)
        conn.execute(SQL65)
        tipos = {
            r[0]: (r[1], r[2])
            for r in conn.execute(
                "SELECT column_name, udt_name, domain_name FROM information_schema.columns"
                " WHERE table_name IN ('impulso', 'impulso_lectura')"
                " AND column_name IN ('tope_monto', 'tope_moneda', 'presupuesto_diario', 'gasto')"
            ).fetchall()
        }
        assert {c: d for c, (_u, d) in tipos.items() if c != "tope_moneda"} == {
            "tope_monto": "money_amount",
            "presupuesto_diario": "money_amount",
            "gasto": "money_amount",
        }
        assert tipos["tope_moneda"] == ("currency", None)


@_skip_db
def test_impulso_coherente_se_inserta_y_lote_acepta_pausado():
    """I.3: impulso coherente se inserta, el estado deriva sin lectura y el
    lote acepta 'pausado'."""
    with db_fabrica("orbit_i3_ok") as conn:
        conn.execute(SQL64)
        conn.execute(SQL65)
        pid, lid = _producto(conn)
        lote = _lote(conn, "fabrica-imp-1")
        grupo = _grupo(conn, lote)
        imp = _impulso(conn, lote, grupo, pid, lid)
        fila = conn.execute(
            "SELECT veredicto, es_final, estado_lote FROM v_impulso_estado WHERE id = %s", (imp,)
        ).fetchone()
        assert fila == (None, None, "planeado")
        conn.execute("UPDATE fabrica_lote SET estado = 'pausado' WHERE lote = %s", (lote,))
        assert conn.execute(
            "SELECT estado FROM fabrica_lote WHERE lote = %s", (lote,)
        ).fetchone() == ("pausado",)


@_skip_db
def test_impulso_moneda_o_listing_incoherente_se_rechaza():
    """I.3: tope en otra moneda, o listing de otro producto o plataforma,
    se rechaza."""
    with db_fabrica("orbit_i3_no") as conn:
        conn.execute(SQL64)
        conn.execute(SQL65)
        pid, lid = _producto(conn)
        lote = _lote(conn, "fabrica-imp-2")
        grupo = _grupo(conn, lote)
        with pytest.raises(psycopg.errors.RaiseException, match="moneda"):
            _impulso(conn, lote, grupo, pid, lid, moneda="USD")
        pid2, lid2 = _producto(conn, sku="B", asin="B0BBBBBBBB", seller_sku="SB")
        with pytest.raises(psycopg.errors.RaiseException, match="listing"):
            _impulso(conn, lote, grupo, pid, lid2)
        assert (pid2, lid2) != (pid, lid)


@_skip_db
def test_nadie_hace_update_ni_delete():
    """I.3: UPDATE y DELETE se rechazan en impulso e impulso_lectura."""
    with db_fabrica("orbit_i3_inmut") as conn:
        conn.execute(SQL64)
        conn.execute(SQL65)
        pid, lid = _producto(conn)
        lote = _lote(conn, "fabrica-imp-4")
        grupo = _grupo(conn, lote)
        imp = _impulso(conn, lote, grupo, pid, lid)
        conn.execute(
            "INSERT INTO impulso_lectura (impulso_id, corte, leido_el, dias_observados,"
            " veredicto, es_final, hecho) VALUES (%s, '2026-10-09', now(), 1,"
            " 'en_curso', false, 'nada')",
            (imp,),
        )
        with pytest.raises(psycopg.errors.RaiseException, match="inmutable"):
            conn.execute("UPDATE impulso SET subfamilia = 'x' WHERE id = %s", (imp,))
        with pytest.raises(psycopg.errors.RaiseException, match="inmutable"):
            conn.execute("DELETE FROM impulso_lectura WHERE impulso_id = %s", (imp,))


@_skip_db
def test_reversa_aborta_con_fila_y_sin_fila_restaura():
    """I.3: la reversa aborta si impulso tiene una fila; sin filas quita
    vista/tablas/triggers y devuelve el CHECK de cuatro estados."""
    with db_fabrica("orbit_i3_rev") as conn:
        conn.execute(SQL64)
        conn.execute(SQL65)
        pid, lid = _producto(conn)
        lote = _lote(conn, "fabrica-imp-3")
        grupo = _grupo(conn, lote)
        _impulso(conn, lote, grupo, pid, lid)
        with pytest.raises(psycopg.errors.RaiseException, match="tiene filas"):
            conn.execute(REVERSA65)
        conn.rollback()
        conn.execute("TRUNCATE impulso_lectura, impulso CASCADE")
        conn.execute(REVERSA65)
        assert conn.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = 'impulso'"
        ).fetchone() == (0,)
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("UPDATE fabrica_lote SET estado = 'pausado' WHERE lote = %s", (lote,))
