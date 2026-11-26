"""Tests de la migracion `migrations/0104_auto04_ubicacion.sql` (AUTO-04).

Cubre la seccion 5 de la especificacion: las tablas append-only
`ubicacion_evaluacion` y `ubicacion_sin_aplicar`, la coherencia del veredicto,
la frontera de roles de `campana_ajuste` (que `app_decide` solo escriba
movimientos nacidos de una evaluacion `live` con veredicto `mover` y que el
camino `app_admin` nunca apunte a una evaluacion), la cuota `ubicacion`
fail-closed en `apply_cap_de_config` y la reversa.

Los tests de rol usan `SET ROLE app_decide` / `SET ROLE app_admin`: el
superusuario de la base de prueba es miembro implicito de ambos roles y sin
`SET ROLE` caeria en el camino `app_admin` (la rama que exige
`evaluacion_id IS NULL`).
"""

from __future__ import annotations

import datetime as dt
import json
from contextlib import contextmanager

import psycopg
import pytest
from test_schema import _postgres_obligatorio_ausente

from tests.helpers.pg import db_desde_template

_ANCLA = dt.datetime(2026, 11, 26, 8, 0, tzinfo=dt.UTC)
SONDA = "SELECT to_regclass('public.ubicacion_evaluacion') IS NOT NULL"

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN",
)


@contextmanager
def db_0104():
    """DB aislada desde el template canonico (incluye 0104); yields (conn, dsn)."""
    with db_desde_template("orbit_0104", autocommit=True) as (conn, dsn):
        yield conn, dsn


def _sonda(conn) -> bool:
    return conn.execute(SONDA).fetchone()[0]


def _campana(conn, external="c-0104", platform="amazon_mx") -> int:
    return conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id) VALUES (%s, 'campaign', %s)"
        " RETURNING id",
        (platform, external),
    ).fetchone()[0]


def _config_version(conn) -> int:
    return conn.execute(
        "INSERT INTO config_version (label, settings) VALUES ('0104', '{}'::jsonb) RETURNING id"
    ).fetchone()[0]


def _antes_config(conn, campana: int, observed_at=None) -> int:
    return conn.execute(
        "INSERT INTO ads_campana_config_observation (ad_entity_id, observed_at,"
        " presupuesto_diario, presupuesto_moneda, estrategia_puja, ajuste_top_pct)"
        " VALUES (%s, %s, 100, 'MXN', 'MANUAL', 10) RETURNING id",
        (campana, observed_at or _ANCLA),
    ).fetchone()[0]


def _antes_config_us(conn, campana: int) -> int:
    return conn.execute(
        "INSERT INTO ads_campana_config_observation (ad_entity_id, observed_at,"
        " presupuesto_diario, presupuesto_moneda, estrategia_puja, ajuste_top_pct)"
        " VALUES (%s, %s, 100, 'USD', 'MANUAL', 10) RETURNING id",
        (campana, _ANCLA),
    ).fetchone()[0]


def _ciclo(conn, platform="amazon_mx", mode="live") -> int:
    return conn.execute(
        "INSERT INTO optimizer_cycle (mode, platform) VALUES (%s, %s) RETURNING id",
        (mode, platform),
    ).fetchone()[0]


def _evaluacion(
    conn,
    *,
    ciclo: int,
    campana: int,
    antes: int,
    config_version: int,
    platform="amazon_mx",
    modo="live",
    veredicto="mover",
    motivo="bajo_meta_con_ventaja",
    ubicacion="arriba_de_busqueda",
    de_pct=10,
    a_pct=25,
) -> int:
    return conn.execute(
        "INSERT INTO ubicacion_evaluacion (cycle_id, campana_id, platform, modo,"
        " politica, config_version_id, antes_config_id, decided_at, ventana_desde,"
        " ventana_hasta, moneda, entrada, veredicto, motivo, ubicacion, de_pct, a_pct)"
        " VALUES (%s, %s, %s, %s, 'ubicacion_v1', %s, %s, %s, %s, %s, %s, %s,"
        " %s, %s, %s, %s, %s) RETURNING id",
        (
            ciclo,
            campana,
            platform,
            modo,
            config_version,
            antes,
            _ANCLA,
            _ANCLA.date() - dt.timedelta(days=20),
            _ANCLA.date() - dt.timedelta(days=3),
            "USD" if platform == "amazon_us" else "MXN",
            json.dumps({"campana_id": campana}),
            veredicto,
            motivo,
            ubicacion,
            de_pct,
            a_pct,
        ),
    ).fetchone()[0]


def _ajuste(conn, campana: int, antes: int, **extra) -> int:
    campos = {
        "campana_id": campana,
        "platform": "amazon_mx",
        "clase": "ajuste_ubicacion",
        "antes_config_id": antes,
        "despues": json.dumps({"clase": "ajuste_ubicacion"}),
        "huella": f"auto:{extra.get('evaluacion_id', 'x')}:h",
        "actor": "orbit_ubicacion",
        "go_literal": "AUTO_UBICACION",
        "regresa_a": None,
        "evaluacion_id": None,
    }
    campos.update(extra)
    columnas = ", ".join(campos)
    marcadores = ", ".join(["%s"] * len(campos))
    return conn.execute(
        f"INSERT INTO campana_ajuste ({columnas}) VALUES ({marcadores}) RETURNING id",
        tuple(campos.values()),
    ).fetchone()[0]


@_skip_db
def test_0104_ubicacion_evaluacion_append_only_y_coherencia():
    with db_0104() as (conn, _dsn):
        camp = _campana(conn)
        antes = _antes_config(conn, camp)
        cv = _config_version(conn)
        ciclo = _ciclo(conn)

        with pytest.raises(psycopg.Error, match="veredicto_coherente"):
            _evaluacion(
                conn,
                ciclo=ciclo,
                campana=camp,
                antes=antes,
                config_version=cv,
                veredicto="mantener",
            )
        with pytest.raises(psycopg.Error, match="veredicto_coherente"):
            _evaluacion(
                conn, ciclo=ciclo, campana=camp, antes=antes, config_version=cv, de_pct=15, a_pct=15
            )
        eval_id = _evaluacion(conn, ciclo=ciclo, campana=camp, antes=antes, config_version=cv)
        camp2 = _campana(conn, external="c-0104-b")
        antes2 = _antes_config(conn, camp2)
        with pytest.raises(psycopg.errors.UniqueViolation):
            _evaluacion(conn, ciclo=ciclo, campana=camp, antes=antes2, config_version=cv)

        conn.execute(
            "INSERT INTO ubicacion_sin_aplicar (evaluacion_id, motivo, registrado_el)"
            " VALUES (%s, 'cuota_agotada', %s)",
            (eval_id, _ANCLA),
        )
        with pytest.raises(psycopg.Error, match="APPEND-ONLY"):
            conn.execute(
                "UPDATE ubicacion_evaluacion SET motivo = 'otro' WHERE id = %s", (eval_id,)
            )
        with pytest.raises(psycopg.Error, match="APPEND-ONLY"):
            conn.execute("DELETE FROM ubicacion_evaluacion WHERE id = %s", (eval_id,))
        with pytest.raises(psycopg.Error, match="APPEND-ONLY"):
            conn.execute("UPDATE ubicacion_sin_aplicar SET motivo = 'error_put'")
        with pytest.raises(psycopg.Error, match="APPEND-ONLY"):
            conn.execute("TRUNCATE ubicacion_sin_aplicar")


@_skip_db
def test_0104_frontera_roles_bloquea_shadow_y_fuerza_admin_sin_evaluacion():
    with db_0104() as (conn, _dsn):
        camp = _campana(conn)
        antes = _antes_config(conn, camp)
        cv = _config_version(conn)
        ciclo = _ciclo(conn)
        shadow = _evaluacion(
            conn, ciclo=ciclo, campana=camp, antes=antes, config_version=cv, modo="shadow"
        )
        camp2 = _campana(conn, external="c-0104-mantener")
        antes2 = _antes_config(conn, camp2)
        mantener = _evaluacion(
            conn,
            ciclo=_ciclo(conn),
            campana=camp2,
            antes=antes2,
            config_version=cv,
            veredicto="mantener",
            ubicacion=None,
            de_pct=None,
            a_pct=None,
        )
        camp3 = _campana(conn, external="c-0104-live")
        antes3 = _antes_config(conn, camp3)
        live = _evaluacion(conn, ciclo=_ciclo(conn), campana=camp3, antes=antes3, config_version=cv)

        camp4 = _campana(conn, external="c-0104-otra")
        antes4 = _antes_config(conn, camp4)
        live4 = _evaluacion(
            conn, ciclo=_ciclo(conn), campana=camp4, antes=antes4, config_version=cv
        )
        camp5 = _campana(conn, external="c-0104-us", platform="amazon_us")
        antes5 = _antes_config_us(conn, camp5)
        live5 = _evaluacion(
            conn,
            ciclo=_ciclo(conn, platform="amazon_us"),
            campana=camp5,
            antes=antes5,
            config_version=cv,
            platform="amazon_us",
        )
        antes_otra = _antes_config(conn, camp3, _ANCLA + dt.timedelta(hours=1))

        conn.execute("SET ROLE app_decide")
        with pytest.raises(psycopg.Error, match="FRONTERA-ROLES"):
            _ajuste(conn, camp, antes, evaluacion_id=shadow)
        with pytest.raises(psycopg.Error, match="FRONTERA-ROLES"):
            _ajuste(conn, camp2, antes2, evaluacion_id=mantener)
        auto = _ajuste(conn, camp3, antes3, evaluacion_id=live)
        conn.execute("UPDATE campana_ajuste SET confirmado_el = %s WHERE id = %s", (_ANCLA, auto))

        # La evaluacion de OTRA campana no autoriza este movimiento.
        with pytest.raises(psycopg.Error, match="FRONTERA-ROLES"):
            _ajuste(conn, camp3, antes3, evaluacion_id=live4, huella="h-cruce-campana")
        # Platform distinta de la evaluacion.
        with pytest.raises(psycopg.Error, match="FRONTERA-ROLES"):
            _ajuste(
                conn,
                camp5,
                antes5,
                platform="amazon_mx",
                evaluacion_id=live5,
                huella="h-cruce-platform",
            )
        # antes_config distinto del juzgado.
        with pytest.raises(psycopg.Error, match="FRONTERA-ROLES"):
            _ajuste(conn, camp3, antes_otra, evaluacion_id=live, huella="h-cruce-antes")
        conn.execute("RESET ROLE")

        conn.execute("SET ROLE app_admin")
        with pytest.raises(psycopg.Error, match="FRONTERA-ROLES"):
            _ajuste(
                conn,
                camp3,
                antes3,
                evaluacion_id=live,
                huella="h-admin-con-eval",
                actor="dueno",
                go_literal="APLICAR AJUSTE",
            )
        regreso = _ajuste(
            conn,
            camp3,
            antes3,
            huella="h-admin-regreso",
            actor="dueno",
            go_literal="REGRESAR AJUSTE",
            regresa_a=auto,
        )
        assert regreso


@_skip_db
def test_0104_apply_cap_de_config_ubicacion_fail_closed():
    with db_0104() as (conn, _dsn):
        _config_version(conn)
        assert (
            conn.execute(
                "SELECT apply_cap_de_config('ads_optimizer:amazon_mx:ubicacion')"
            ).fetchone()[0]
            is None
        )
        assert (
            conn.execute(
                "SELECT apply_cap_de_config('ads_optimizer:amazon_us:ubicacion')"
            ).fetchone()[0]
            is None
        )
        conn.execute(
            "INSERT INTO config_version (label, settings) VALUES ('caps', %s::jsonb)",
            (
                json.dumps(
                    {
                        "ads_apply_cap_amazon_mx_ubicacion": "2",
                        "ads_apply_cap_amazon_us_ubicacion": "1",
                    }
                ),
            ),
        )
        assert (
            conn.execute(
                "SELECT apply_cap_de_config('ads_optimizer:amazon_mx:ubicacion')"
            ).fetchone()[0]
            == 2
        )
        assert (
            conn.execute(
                "SELECT apply_cap_de_config('ads_optimizer:amazon_us:ubicacion')"
            ).fetchone()[0]
            == 1
        )
        # Un motor fuera del vocabulario sigue devolviendo NULL.
        assert (
            conn.execute("SELECT apply_cap_de_config('ads_optimizer:amazon_mx:otro')").fetchone()[0]
            is None
        )


@_skip_db
def test_0104_reversa_restaura_esquema_y_sonda_aplicada():
    from pathlib import Path

    raiz = Path(__file__).resolve().parents[1]
    reversa = (raiz / "migrations" / "0104_reversa_auto04_ubicacion.sql").read_text(
        encoding="utf-8"
    )
    with db_0104() as (conn, dsn):
        assert _sonda(conn) is True
        conn.execute(reversa)
        assert _sonda(conn) is False
        assert (
            conn.execute("SELECT to_regclass('public.ubicacion_sin_aplicar')").fetchone()[0] is None
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM information_schema.columns"
                " WHERE table_name = 'campana_ajuste' AND column_name = 'evaluacion_id'"
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT has_table_privilege('app_decide', 'campana_ajuste', 'INSERT')"
            ).fetchone()[0]
            is False
        )
        assert (
            conn.execute(
                "SELECT has_column_privilege('app_decide', 'campana_ajuste', 'confirmado_el',"
                " 'UPDATE')"
            ).fetchone()[0]
            is False
        )
        # `precio_cap_*` sigue borrada (reversa de 0091) y `ubicacion` deja de
        # resolver: la funcion vuelve a los ocho mapeos.
        assert (
            conn.execute(
                "SELECT apply_cap_de_config('ads_optimizer:amazon_mx:ubicacion')"
            ).fetchone()[0]
            is None
        )
        assert (
            conn.execute("SELECT pg_get_functiondef('apply_cap_de_config(text)'::regprocedure)")
            .fetchone()[0]
            .count("WHEN 'ads_optimizer:")
            == 8
        )
        # La base vuelve a aceptar el insert manual sin evaluacion.
        camp = _campana(conn, external="c-0104-rev")
        antes = _antes_config(conn, camp)
        conn.execute("SET ROLE app_admin")
        conn.execute(
            "INSERT INTO campana_ajuste (campana_id, platform, clase, antes_config_id,"
            " despues, huella, actor, go_literal)"
            " VALUES (%s, 'amazon_mx', 'ajuste_ubicacion', %s, %s, 'h-rev', 'dueno',"
            " 'APLICAR AJUSTE')",
            (camp, antes, json.dumps({"clase": "ajuste_ubicacion"})),
        )
