"""Migracion del bloque H (0099): el vocabulario cerrado de desenlaces admite
`tope_ciclo`, con sonda, ida y reversa probadas sobre el PostgreSQL desechable
del laboratorio y los roles reales.

La reversa restaura la lista de 0094 solo si no hay desenlaces `tope_ciclo`; si
los hay, rehusa sin reescribir historia.
"""

from __future__ import annotations

from pathlib import Path

import psycopg
import pytest
from test_apply import _decision_bid, _semilla
from test_schema import _postgres_obligatorio_ausente

from tests.helpers.pg import db_desde_template

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)

MIGRACIONES = Path(__file__).resolve().parents[1] / "migrations"
IDA = MIGRACIONES / "0099_arreglos05_tope_ciclo.sql"
REVERSA = MIGRACIONES / "0099_reversa_arreglos05_tope_ciclo.sql"

MOTIVOS_PREVIOS = (
    "modo_no_live",
    "ya_aplicada",
    "bid_incompleto",
    "entidad_no_decisora",
    "tope_intentos",
    "fuera_de_cap",
    "fallo_http",
    "sin_quota",
    "espera_target",
    "sin_respuesta",
    "perdida",
    "choque_clave",
    "espera_proteccion",
    "bid_cambiado_fuera",
    "vigencia_vencida",
)


def _sql(ruta: Path) -> str:
    assert ruta.exists(), f"falta la migracion {ruta.name}"
    return ruta.read_text(encoding="utf-8")


def _sonda() -> str:
    """La primera linea `-- sonda-aplicada:` de las primeras 10, como la lee el instalador."""
    for linea in _sql(IDA).splitlines()[:10]:
        if linea.startswith("-- sonda-aplicada:"):
            return linea.split("-- sonda-aplicada:", 1)[1].strip().rstrip(";")
    raise AssertionError("0099 sin cabecera '-- sonda-aplicada:'")


def _sonda_da(conn) -> bool:
    return conn.execute(_sonda()).fetchone() == (True,)


def _definicion(conn) -> str:
    return conn.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
        " WHERE conrelid = 'decision_sin_aplicar'::regclass"
        " AND conname = 'decision_sin_aplicar_motivo_check'"
    ).fetchone()[0]


@_skip_db
def test_arreglos05_h_migracion_ida_agrega_el_motivo_y_la_sonda_da_true():
    """La sonda da t recien aplicada y el CHECK vivo lleva el motivo al final,
    con la lista previa intacta."""
    with db_desde_template("orbit_a05h_mig_ida", autocommit=True) as (conn, _dsn):
        assert _sonda_da(conn), "la sonda de 0099 debe dar true recien aplicada"
        definicion = _definicion(conn)
        assert "'tope_ciclo'" in definicion
        posiciones = [definicion.index(f"'{motivo}'") for motivo in MOTIVOS_PREVIOS]
        assert posiciones == sorted(posiciones), "la lista previa conserva su orden"
        assert definicion.index("'tope_ciclo'") > max(posiciones), "el motivo nuevo va al final"


@_skip_db
def test_arreglos05_h_migracion_reversa_restaura_la_lista_y_la_ida_vuelve():
    """Sin desenlaces nuevos, la reversa quita el motivo y la sonda da f; la
    ida se puede aplicar otra vez."""
    with db_desde_template("orbit_a05h_mig_reversa", autocommit=True) as (conn, _dsn):
        conn.execute(_sql(REVERSA))

        assert not _sonda_da(conn), "tras la reversa la sonda debe dar false"
        definicion = _definicion(conn)
        assert "'tope_ciclo'" not in definicion
        for motivo in MOTIVOS_PREVIOS:
            assert f"'{motivo}'" in definicion

        conn.execute(_sql(IDA))
        assert _sonda_da(conn), "la migracion es aplicable otra vez tras su reversa"
        assert "'tope_ciclo'" in _definicion(conn)


@_skip_db
def test_arreglos05_h_migracion_reversa_rehusa_con_desenlaces_del_motivo_nuevo():
    """Con un desenlace `tope_ciclo`, la reversa rehusa y no reescribe historia."""
    with db_desde_template("orbit_a05h_mig_rehusa", autocommit=True) as (conn, _dsn):
        ids = _semilla(conn)
        decision = _decision_bid(conn, ids["ciclo_dec"], ids["config"], ids["kw"])
        conn.execute(
            "INSERT INTO decision_sin_aplicar (decision_id, cycle_id, motivo)"
            " VALUES (%s, %s, 'tope_ciclo')",
            (decision, ids["ciclo_ejec"]),
        )

        with pytest.raises(psycopg.errors.RestrictViolation, match="tope_ciclo"):
            conn.execute(_sql(REVERSA))

        assert _sonda_da(conn), "el esquema sigue como lo dejo 0099"
        assert (
            conn.execute(
                "SELECT count(*) FROM decision_sin_aplicar WHERE motivo = 'tope_ciclo'"
            ).fetchone()[0]
            == 1
        )


@_skip_db
def test_arreglos05_h_migracion_roles_reales_insertan_el_motivo_y_read_no():
    """app_decide registra el desenlace nuevo; app_read sigue sin poder escribir."""
    with db_desde_template("orbit_a05h_mig_roles", autocommit=True) as (conn, _dsn):
        ids = _semilla(conn)
        decision = _decision_bid(conn, ids["ciclo_dec"], ids["config"], ids["kw"])

        assert conn.execute(
            "SELECT has_table_privilege('app_decide', 'public.decision_sin_aplicar', 'INSERT'),"
            " has_table_privilege('app_read', 'public.decision_sin_aplicar', 'INSERT')"
        ).fetchone() == (True, False)

        conn.execute("SET ROLE app_decide")
        try:
            conn.execute(
                "INSERT INTO decision_sin_aplicar (decision_id, cycle_id, motivo, detalle)"
                " VALUES (%s, %s, 'tope_ciclo', %s)",
                (
                    decision,
                    ids["ciclo_ejec"],
                    psycopg.types.json.Json({"usados": 150, "tope": 150}),
                ),
            )
        finally:
            conn.execute("RESET ROLE")
        assert conn.execute(
            "SELECT motivo FROM decision_sin_aplicar WHERE decision_id = %s", (decision,)
        ).fetchone() == ("tope_ciclo",)

        conn.execute("SET ROLE app_read")
        try:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(
                    "INSERT INTO decision_sin_aplicar (decision_id, cycle_id, motivo)"
                    " VALUES (%s, %s, 'tope_ciclo')",
                    (decision, ids["ciclo_ejec"]),
                )
        finally:
            conn.execute("RESET ROLE")
