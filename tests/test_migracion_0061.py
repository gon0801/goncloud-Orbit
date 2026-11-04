"""Tests de la migracion `migrations/0061_bids02_campana_config.sql` (BIDS 02,
V.1, seccion 3): historial append-only de la configuracion de campana
(`ads_campana_config_observation`) y su vista vigente
(`v_campana_config_vigente`).

`ORDEN61` es el subconjunto minimo que toca 0061 (0001 + 0061): ninguna
migracion entre 0002 y 0060 menciona `ads_campana_config_observation`,
`v_campana_config_vigente` ni `ads_campana_config_0061_kind`, y 0061 solo
necesita de 0001 `ad_entity`, `prohibir_mutacion()` y los roles `app_*`
(verificado por grep al escribir el modulo). Todos los tests corren con
`ORBIT_TEST_DSN` apuntado (0 skipped); sin Postgres skipean en verde fuera
de CI (patron `test_apply_schema`).
"""

from __future__ import annotations

import datetime as dt
import os
import socket
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from test_schema import _postgres_obligatorio_ausente, _test_dsn

ROOT = Path(__file__).resolve().parents[1]
ORDEN61 = (
    "0001_initial.sql",
    "0061_bids02_campana_config.sql",
)
REVERSA61 = "0061_reversa_bids02_campana_config.sql"

_ANCLA = dt.datetime(2026, 10, 1, 8, 0, tzinfo=dt.UTC)
_ANCLA_MAS_TARDE = dt.datetime(2026, 10, 2, 8, 0, tzinfo=dt.UTC)

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)


@contextmanager
def db_61(prefijo: str, *, solo_base: bool = False):
    """DB temporal con ORDEN61 (o solo 0001 con `solo_base`); yields conn
    autocommit (patron `db_39`)."""
    from psycopg import sql as pgsql

    dsn = _test_dsn()
    db = f"{prefijo}_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        nombres = ORDEN61 if not solo_base else ORDEN61[:1]
        for nombre in nombres:
            conn.execute((ROOT / "migrations" / nombre).read_text(encoding="utf-8"))
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def _campana(conn, external="c-0061") -> int:
    return conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id) VALUES ('amazon_mx',"
        " 'campaign', %s) RETURNING id",
        (external,),
    ).fetchone()[0]


def _grupo(conn, campana: int, external="g-0061") -> int:
    return conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id, parent_id) VALUES"
        " ('amazon_mx', 'ad_group', %s, %s) RETURNING id",
        (external, campana),
    ).fetchone()[0]


def _observa(conn, campana: int, observado: dt.datetime = _ANCLA, **campos) -> int:
    base = {
        "presupuesto_diario": Decimal("10.08"),
        "presupuesto_moneda": "MXN",
        "estrategia_puja": "LEGACY_FOR_SALES",
        "ajuste_top_pct": 40,
        "ajuste_resto_pct": 0,
        "ajuste_producto_pct": 0,
        "fuera_de_amazon": None,
    }
    base.update(campos)
    columnas = ", ".join(base)
    marcas = ", ".join(["%s"] * len(base))
    return conn.execute(
        f"INSERT INTO ads_campana_config_observation (ad_entity_id, observed_at,"
        f" {columnas}) VALUES (%s, %s, {marcas}) RETURNING id",
        (campana, observado, *base.values()),
    ).fetchone()[0]


@_skip_db
def test_0061_rechaza_presupuesto_sin_moneda():
    """Presupuesto sin moneda (o moneda sin presupuesto) revienta en
    `config_presupuesto_con_moneda`; el par junto pasa y el par ausente
    tambien. Rojo pre-0061: la tabla no existe."""
    with db_61("orbit_61_moneda") as conn:
        camp = _campana(conn)
        with pytest.raises(psycopg.errors.CheckViolation, match="config_presupuesto_con_moneda"):
            _observa(conn, camp, presupuesto_moneda=None)
        with pytest.raises(psycopg.errors.CheckViolation, match="config_presupuesto_con_moneda"):
            _observa(
                conn,
                camp,
                observado=_ANCLA_MAS_TARDE,
                presupuesto_diario=None,
                presupuesto_moneda="MXN",
            )
        assert _observa(conn, camp, observado=_ANCLA_MAS_TARDE)
        otra = _campana(conn, external="c-0061-sin-presupuesto")
        assert _observa(conn, otra, presupuesto_diario=None, presupuesto_moneda=None)


@_skip_db
def test_0061_rechaza_ajuste_901():
    """Un ajuste de 901 revienta en `config_ajustes_en_rango`, en cualquiera
    de los tres; 900, 0 y NULL pasan. Rojo pre-0061: la tabla no existe."""
    with db_61("orbit_61_ajuste") as conn:
        camp = _campana(conn)
        for columna in ("ajuste_top_pct", "ajuste_resto_pct", "ajuste_producto_pct"):
            with pytest.raises(psycopg.errors.CheckViolation, match="config_ajustes_en_rango"):
                _observa(conn, camp, **{columna: 901})
        assert _observa(conn, camp)
        otra = _campana(conn, external="c-0061-bordes")
        assert _observa(
            conn,
            otra,
            ajuste_top_pct=900,
            ajuste_resto_pct=0,
            ajuste_producto_pct=None,
        )


@_skip_db
def test_0061_rechaza_entidad_que_no_es_campana():
    """Una observacion sobre un `ad_entity` que no es campana revienta en el
    trigger de kind; sobre una campana pasa. Rojo pre-0061: la tabla no
    existe."""
    with db_61("orbit_61_kind") as conn:
        camp = _campana(conn)
        grupo = _grupo(conn, camp)
        with pytest.raises(psycopg.errors.CheckViolation, match="kind=campaign"):
            _observa(conn, grupo)
        assert _observa(conn, camp)


@_skip_db
def test_0061_reversa_deja_esquema_como_estaba():
    """Aplicar 0061 y su reversa deja el catalogo (tablas, vistas, triggers,
    rutinas, CHECKs) identico al de solo-0001. Puro psycopg, sin binarios.
    Rojo pre-0061: la migracion no existe."""
    with db_61("orbit_61_reversa", solo_base=True) as conn:
        antes = _foto(conn)
        conn.execute((ROOT / "migrations" / ORDEN61[1]).read_text(encoding="utf-8"))
        assert ("ads_campana_config_observation", "r") in {(t, k) for t, k in _foto(conn)["tablas"]}
        conn.execute((ROOT / "migrations" / REVERSA61).read_text(encoding="utf-8"))
        assert _foto(conn) == antes


def _foto(conn) -> dict:
    tablas = conn.execute(
        "SELECT relname, relkind FROM pg_class"
        " WHERE relnamespace = 'public'::regnamespace"
        " AND relkind IN ('r', 'v', 'm', 'f', 'p')"
        " AND relname NOT LIKE 'pg_%'"
    ).fetchall()
    triggers = conn.execute(
        "SELECT tg.tgname, c.relname FROM pg_trigger tg"
        " JOIN pg_class c ON c.oid = tg.tgrelid"
        " WHERE c.relnamespace = 'public'::regnamespace AND NOT tg.tgisinternal"
    ).fetchall()
    rutinas = conn.execute(
        "SELECT oid::regprocedure::text FROM pg_proc WHERE pronamespace = 'public'::regnamespace"
    ).fetchall()
    checks = conn.execute(
        "SELECT con.conname, c.relname, pg_get_constraintdef(con.oid)"
        " FROM pg_constraint con JOIN pg_class c ON c.oid = con.conrelid"
        " WHERE c.relnamespace = 'public'::regnamespace AND con.contype = 'c'"
    ).fetchall()
    return {
        "tablas": sorted(tablas),
        "triggers": sorted(triggers),
        "rutinas": sorted(r[0] for r in rutinas),
        "checks": sorted(checks),
    }


@_skip_db
def test_v_campana_config_vigente_trae_ultima_por_campana():
    """La vista trae la observacion mas reciente de cada campana, con su
    fila completa. Rojo pre-0061: la vista no existe."""
    with db_61("orbit_61_vigente") as conn:
        camp = _campana(conn)
        _observa(conn, camp, presupuesto_diario=Decimal("10.08"))
        _observa(
            conn,
            camp,
            observado=_ANCLA_MAS_TARDE,
            presupuesto_diario=Decimal("12.50"),
            estrategia_puja="RULE_BASED",
            ajuste_top_pct=100,
            fuera_de_amazon='{"opt": true}',
        )
        otra = _campana(conn, external="c-0061-otra")
        _observa(conn, otra)
        filas = conn.execute(
            "SELECT ad_entity_id, observed_at, presupuesto_diario,"
            " presupuesto_moneda, estrategia_puja, ajuste_top_pct,"
            " ajuste_resto_pct, ajuste_producto_pct, fuera_de_amazon"
            " FROM v_campana_config_vigente ORDER BY ad_entity_id"
        ).fetchall()
        assert filas == [
            (
                camp,
                _ANCLA_MAS_TARDE,
                Decimal("12.50"),
                "MXN",
                "RULE_BASED",
                100,
                0,
                0,
                '{"opt": true}',
            ),
            (
                otra,
                _ANCLA,
                Decimal("10.08"),
                "MXN",
                "LEGACY_FOR_SALES",
                40,
                0,
                0,
                None,
            ),
        ]


@_skip_db
def test_0061_config_append_only_rechaza_update_delete_truncate():
    """v1-11: la tabla de config es append-only de verdad."""
    with db_61("orbit_61_ao") as conn:
        _observa(conn, _campana(conn))
        for sentencia in (
            "UPDATE ads_campana_config_observation SET observed_at = now()",
            "DELETE FROM ads_campana_config_observation",
            "TRUNCATE ads_campana_config_observation",
        ):
            with pytest.raises(psycopg.Error, match="APPEND-ONLY"):
                conn.execute(sentencia)


@_skip_db
def test_0061_rechaza_presupuesto_cero_y_ajuste_negativo():
    """v1-12/v1-13: presupuesto 0 y ajuste negativo revientan en su CHECK."""
    with db_61("orbit_61_chk") as conn:
        camp = _campana(conn)
        with pytest.raises(psycopg.errors.CheckViolation, match="config_presupuesto_positivo"):
            conn.execute(
                "INSERT INTO ads_campana_config_observation (ad_entity_id, observed_at,"
                " presupuesto_diario, presupuesto_moneda) VALUES (%s, now(), 0, 'MXN')",
                (camp,),
            )
        with pytest.raises(psycopg.errors.CheckViolation, match="config_ajustes_en_rango"):
            conn.execute(
                "INSERT INTO ads_campana_config_observation (ad_entity_id, observed_at,"
                " ajuste_top_pct) VALUES (%s, now(), -1)",
                (camp,),
            )


@_skip_db
def test_0061_sello_moneda_rechaza_mx_en_usd():
    """S1: el presupuesto de una campana MX en USD revienta en el sello."""
    with (
        db_61("orbit_61_sello") as conn,
        pytest.raises(psycopg.errors.CheckViolation, match="presupuesto viene en"),
    ):
        _observa(conn, _campana(conn), presupuesto_moneda="USD")
