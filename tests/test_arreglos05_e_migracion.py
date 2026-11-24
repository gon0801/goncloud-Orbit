"""Migracion del bloque E (0098): separa la unicidad live de la deduplicacion
de practicas shadow vigentes, con sonda, ida y reversa probadas sobre el
PostgreSQL desechable del laboratorio.

La reversa restaura el indice mixto de 0002 solo si no hay colisiones; si las
hay, rehusa sin borrar filas. Los roles son los reales del esquema.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import psycopg
import pytest
from test_apply_cola import (  # noqa: F401 - helpers reutilizados
    _decision_corte,
    _encola_fila,
    _payload_pause,
    _semilla,
)
from test_schema import _postgres_obligatorio_ausente

from tests.helpers.pg import db_desde_template

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)

MIGRACIONES = Path(__file__).resolve().parents[1] / "migrations"
IDA = MIGRACIONES / "0098_arreglos05_indice_shadow.sql"
REVERSA = MIGRACIONES / "0098_reversa_arreglos05_indice_shadow.sql"


def _sql(ruta: Path) -> str:
    assert ruta.exists(), f"falta la migracion {ruta.name}"
    return ruta.read_text(encoding="utf-8")


def _sonda() -> str:
    """La primera linea `-- sonda-aplicada:` de las primeras 10, como la lee el instalador."""
    for linea in _sql(IDA).splitlines()[:10]:
        if linea.startswith("-- sonda-aplicada:"):
            return linea.split("-- sonda-aplicada:", 1)[1].strip().rstrip(";")
    raise AssertionError("0098 sin cabecera '-- sonda-aplicada:'")


def _decision(conn, ids: dict, entidad: int, kind: str, **kw) -> int:
    """Decision de corte en su PROPIO ciclo: `decision` es unica por
    (cycle_id, ad_entity_id)."""
    ciclo = conn.execute(
        "INSERT INTO optimizer_cycle (mode, platform) VALUES ('live', 'amazon_us') RETURNING id"
    ).fetchone()[0]
    return _decision_corte(conn, ciclo, ids["config"], entidad, kind, **kw)


def _indices(conn) -> set[str]:
    return {
        fila[0]
        for fila in conn.execute(
            "SELECT indexname FROM pg_indexes"
            " WHERE schemaname = 'public' AND tablename = 'apply_queue'"
        ).fetchall()
    }


def _trigger_existe(conn) -> bool:
    return bool(
        conn.execute(
            "SELECT count(*) FROM pg_trigger WHERE tgrelid = 'public.apply_queue'::regclass"
            " AND tgname = 'apply_queue_clave_vigente' AND NOT tgisinternal"
        ).fetchone()[0]
    )


def _sonda_da(conn) -> bool:
    return conn.execute(_sonda()).fetchone() == (True,)


def _inserta_live(conn, decision_id: int, entidad: int) -> int:
    return conn.execute(
        "INSERT INTO apply_queue (platform, ad_entity_id, kind, search_term, decision_id,"
        " modo, estado, vence_el, request_payload)"
        " VALUES ('amazon_us', %s, 'pause', NULL, %s, 'live', 'pending_veto', %s, %s)"
        " RETURNING id",
        (
            entidad,
            decision_id,
            dt.datetime.now(dt.UTC) + dt.timedelta(hours=48),
            psycopg.types.json.Json(_payload_pause("7201")),
        ),
    ).fetchone()[0]


@_skip_db
def test_arreglos05_e_migracion_ida_indice_live_y_trigger_sin_indice_mixto():
    """La sonda da t, el indice live y el trigger existen y el indice mixto de
    0002 ya no esta."""
    with db_desde_template("orbit_a05e_mig_ida", autocommit=True) as (conn, _dsn):
        conn.execute("SET TIME ZONE 'UTC'")
        assert _sonda_da(conn), "la sonda de 0098 debe dar true recien aplicada"

        indices = _indices(conn)
        assert "apply_queue_live_efecto_unico" in indices
        assert "apply_queue_clave_efecto_en_vuelo" not in indices, (
            "el indice mixto de 0002 no puede sobrevivir a 0098"
        )
        definicion = conn.execute(
            "SELECT indexdef FROM pg_indexes"
            " WHERE schemaname = 'public' AND tablename = 'apply_queue'"
            " AND indexname = 'apply_queue_live_efecto_unico'"
        ).fetchone()[0]
        assert "modo = 'live'" in definicion, "el unico parcial nuevo cubre solo live"
        for terminal in ("applied", "failed", "vetoed", "discarded"):
            assert f"'{terminal}'" in definicion
        assert _trigger_existe(conn), "falta el trigger de clave vigente en el INSERT"

        cuerpo = conn.execute(
            "SELECT prosrc FROM pg_proc WHERE proname = 'apply_queue_clave_vigente'"
        ).fetchone()[0]
        assert "vence_el" in cuerpo and "unique_violation" in cuerpo, (
            "el trigger decide por vigencia y levanta el choque que el motor ya captura"
        )


@_skip_db
def test_arreglos05_e_migracion_reversa_rehusa_con_colisiones_y_no_borra_filas():
    """Con una practica vencida y una live de la misma clave, la reversa rehusa
    restaurar el indice mixto, no borra filas y deja el esquema de 0098."""
    with db_desde_template("orbit_a05e_mig_colision", autocommit=True) as (conn, _dsn):
        conn.execute("SET TIME ZONE 'UTC'")
        ids = _semilla(conn)
        ahora = ids["ahora"]
        dec_sh = _decision(conn, ids, ids["kw"], "pause")
        _encola_fila(
            conn,
            dec_sh,
            ids["kw"],
            "pause",
            modo="shadow",
            vence=ahora - dt.timedelta(hours=1),
            payload=_payload_pause("7201"),
        )
        dec_live = _decision(conn, ids, ids["kw"], "pause")
        _inserta_live(conn, dec_live, ids["kw"])

        total_antes = conn.execute("SELECT count(*) FROM apply_queue").fetchone()[0]
        assert total_antes == 2

        with pytest.raises(psycopg.errors.RestrictViolation, match="chocaria|no terminal"):
            conn.execute(_sql(REVERSA))

        assert conn.execute("SELECT count(*) FROM apply_queue").fetchone()[0] == total_antes, (
            "la reversa no elimina filas para forzar el indice"
        )
        assert _sonda_da(conn), "el esquema sigue como lo dejo 0098"
        indices = _indices(conn)
        assert "apply_queue_live_efecto_unico" in indices
        assert "apply_queue_clave_efecto_en_vuelo" not in indices
        assert _trigger_existe(conn)


@_skip_db
def test_arreglos05_e_migracion_reversa_restaura_el_mixto_y_la_ida_vuelve():
    """Sin colisiones, la reversa restaura el indice mixto de 0002 y la sonda da
    f; volver a aplicar la ida deja la sonda en t."""
    with db_desde_template("orbit_a05e_mig_reversa", autocommit=True) as (conn, _dsn):
        conn.execute("SET TIME ZONE 'UTC'")
        conn.execute(_sql(REVERSA))

        assert not _sonda_da(conn), "tras la reversa la sonda debe dar false"
        indices = _indices(conn)
        assert "apply_queue_clave_efecto_en_vuelo" in indices, (
            "la reversa restaura el indice mixto de 0002"
        )
        assert "apply_queue_live_efecto_unico" not in indices
        assert not _trigger_existe(conn)

        conn.execute(_sql(IDA))
        assert _sonda_da(conn), "la migracion es aplicable otra vez tras su reversa"
        indices = _indices(conn)
        assert "apply_queue_live_efecto_unico" in indices
        assert "apply_queue_clave_efecto_en_vuelo" not in indices
        assert _trigger_existe(conn)


@_skip_db
def test_arreglos05_e_roles_reales_motor_encola_y_practica_vigente_bloquea():
    """app_decide sigue encolando; la practica vigente lo bloquea y la vencida
    lo deja pasar. app_read sigue sin poder insertar."""
    with db_desde_template("orbit_a05e_mig_roles", autocommit=True) as (conn, _dsn):
        conn.execute("SET TIME ZONE 'UTC'")
        ids = _semilla(conn)
        ahora = ids["ahora"]

        dec_sh = _decision(conn, ids, ids["kw"], "pause")
        _encola_fila(
            conn,
            dec_sh,
            ids["kw"],
            "pause",
            modo="shadow",
            vence=ahora + dt.timedelta(hours=2),
            payload=_payload_pause("7201"),
        )
        dec_kw = _decision(conn, ids, ids["kw"], "pause")
        dec_kw2 = _decision(conn, ids, ids["kw2"], "pause")
        dec_sh2 = _decision(conn, ids, ids["kw2"], "pause")
        _encola_fila(
            conn,
            dec_sh2,
            ids["kw2"],
            "pause",
            modo="shadow",
            vence=ahora - dt.timedelta(hours=1),
            payload=_payload_pause("7202"),
        )

        conn.execute("SET ROLE app_decide")
        try:
            with pytest.raises(psycopg.errors.UniqueViolation):
                _inserta_live(conn, dec_kw, ids["kw"])
            _inserta_live(conn, dec_kw2, ids["kw2"])
        finally:
            conn.execute("RESET ROLE")

        assert conn.execute(
            "SELECT has_table_privilege('app_decide', 'public.apply_queue', 'INSERT'),"
            " has_table_privilege('app_decide', 'public.apply_queue', 'SELECT')"
        ).fetchone() == (True, True)

        conn.execute("SET ROLE app_read")
        try:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                _inserta_live(conn, dec_kw, ids["kw"])
        finally:
            conn.execute("RESET ROLE")
