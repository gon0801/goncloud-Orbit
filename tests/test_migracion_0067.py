"""Tests de la migracion `migrations/0067_bids02_campana_ajuste.sql` (BIDS 02,
V.3, seccion 4): ajustes del dueno (`campana_ajuste`, append-only salvo el
sello `confirmado_el`) y tercera rama de `v_cambio_bid` (origen
`ajuste_de_campana`).

`ORDEN67` es el subconjunto minimo que toca 0067 (0001 + 0002 + 0060 + 0061 +
0067): 0067 necesita de 0001 `ad_entity`, `prohibir_mutacion()` y los roles
`app_*`, de 0002 `applied_cycle_id`, de 0060 las vistas y de 0061 la tabla de
configuracion (`antes_config_id`).
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
ORDEN67 = (
    "0001_initial.sql",
    "0002_apply.sql",
    "0060_bids02_base_lectura.sql",
    "0061_bids02_campana_config.sql",
    "0067_bids02_campana_ajuste.sql",
)
REVERSA67 = "0067_reversa_bids02_campana_ajuste.sql"

_ANCLA = dt.datetime(2026, 10, 11, 8, 0, tzinfo=dt.UTC)

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)


@contextmanager
def db_67(prefijo: str, *, solo_base: bool = False):
    """DB temporal con ORDEN67 (o sin 0067 con `solo_base`); yields conn
    autocommit (patron `db_61`)."""
    from psycopg import sql as pgsql

    dsn = _test_dsn()
    db = f"{prefijo}_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        nombres = ORDEN67 if not solo_base else ORDEN67[:4]
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


def _campana(conn, external="c-0067") -> int:
    return conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id) VALUES ('amazon_mx',"
        " 'campaign', %s) RETURNING id",
        (external,),
    ).fetchone()[0]


def _hoja(conn, campana: int, external="k-0067") -> int:
    ag = conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id, parent_id) VALUES"
        " ('amazon_mx', 'ad_group', 'g-0067', %s) RETURNING id",
        (campana,),
    ).fetchone()[0]
    kw = conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id, parent_id, match_type,"
        " keyword_text) VALUES ('amazon_mx', 'keyword', %s, %s, 'EXACT', 't') RETURNING id",
        (external, ag),
    ).fetchone()[0]
    for e in (campana, ag, kw):
        conn.execute(
            "INSERT INTO ad_entity_state (ad_entity_id, status, synced_at) VALUES"
            " (%s, 'ENABLED', now())",
            (e,),
        )
    conn.execute(
        "UPDATE ad_entity_state SET current_bid = 2.50, bid_currency = 'MXN'"
        " WHERE ad_entity_id = %s",
        (kw,),
    )
    return kw


def _config(conn, campana: int) -> int:
    return conn.execute(
        "INSERT INTO ads_campana_config_observation (ad_entity_id, observed_at,"
        " presupuesto_diario, presupuesto_moneda, estrategia_puja) VALUES (%s, %s,"
        " 100, 'MXN', 'MANUAL') RETURNING id",
        (campana, _ANCLA),
    ).fetchone()[0]


def _ajuste(
    conn,
    campana: int,
    config: int,
    clase="ajuste_ubicacion",
    huella="h-0067",
    confirmado: dt.datetime | None = None,
    regresa_a: int | None = None,
) -> int:
    import json

    return conn.execute(
        "INSERT INTO campana_ajuste (campana_id, platform, clase, antes_config_id,"
        " despues, huella, actor, go_literal, regresa_a, confirmado_el)"
        " VALUES (%s, 'amazon_mx', %s, %s, %s, %s, 'dueno', 'APLICAR AJUSTE', %s, %s)"
        " RETURNING id",
        (campana, clase, config, json.dumps({"clase": clase}), huella, regresa_a, confirmado),
    ).fetchone()[0]


@_skip_db
def test_0067_update_solo_confirmado_el():
    """UPDATE que toca otra columna revienta; sellar confirmado_el pasa."""
    with db_67("orbit_67_upd") as conn:
        camp = _campana(conn)
        aj = _ajuste(conn, camp, _config(conn, camp))
        with pytest.raises(psycopg.Error, match="SOLO-CONFIRMADO"):
            conn.execute("UPDATE campana_ajuste SET actor = 'otro' WHERE id = %s", (aj,))
        conn.execute("UPDATE campana_ajuste SET confirmado_el = %s WHERE id = %s", (_ANCLA, aj))
        assert (
            conn.execute(
                "SELECT confirmado_el FROM campana_ajuste WHERE id = %s", (aj,)
            ).fetchone()[0]
            == _ANCLA
        )
        with pytest.raises(psycopg.Error, match="SOLO-CONFIRMADO"):
            conn.execute(
                "UPDATE campana_ajuste SET confirmado_el = %s WHERE id = %s",
                (_ANCLA + dt.timedelta(hours=1), aj),
            )


@_skip_db
def test_0067_delete_y_truncate_rechazados():
    with db_67("orbit_67_del") as conn:
        camp = _campana(conn)
        _ajuste(conn, camp, _config(conn, camp))
        with pytest.raises(psycopg.Error, match="APPEND-ONLY"):
            conn.execute("DELETE FROM campana_ajuste")
        with pytest.raises(psycopg.Error, match="APPEND-ONLY"):
            conn.execute("TRUNCATE campana_ajuste")


@_skip_db
def test_0067_rama_ajuste_ubicacion_y_presupuesto_fuera():
    """Ajuste confirmado de clase ajuste_ubicacion: una fila por hoja activa
    con origen ajuste_de_campana; clase presupuesto: ninguna."""
    with db_67("orbit_67_rama") as conn:
        camp = _campana(conn)
        kw = _hoja(conn, camp)
        cfg = _config(conn, camp)
        _ajuste(conn, camp, cfg, clase="presupuesto", huella="h-pre", confirmado=_ANCLA)
        filas = conn.execute(
            "SELECT hoja_id, bid_antes, bid_despues, moneda, origen, decision_id"
            " FROM v_cambio_bid WHERE origen = 'ajuste_de_campana'"
        ).fetchall()
        assert filas == []
        _ajuste(conn, camp, cfg, huella="h-ubi", confirmado=_ANCLA)
        filas = conn.execute(
            "SELECT hoja_id, bid_antes, bid_despues, moneda, origen, decision_id"
            " FROM v_cambio_bid WHERE origen = 'ajuste_de_campana'"
        ).fetchall()
        assert filas == [(kw, Decimal("2.50"), Decimal("2.50"), "MXN", "ajuste_de_campana", None)]


@_skip_db
def test_r07m_rama3_fuera_regreso_y_fecha_de_confirmado():
    """R07-M (V04, V12, V13): fuera_de_amazon no entra a la rama 3; el
    regreso de un ajuste de ubicacion si entra; la fecha de la fila es
    confirmado_el, no creado_el."""
    import json

    with db_67("orbit_67_r07m") as conn:
        camp = _campana(conn)
        kw = _hoja(conn, camp)
        cfg = _config(conn, camp)
        _ajuste(conn, camp, cfg, clase="fuera_de_amazon", huella="h-fuera", confirmado=_ANCLA)
        assert (
            conn.execute(
                "SELECT count(*) FROM v_cambio_bid WHERE origen = 'ajuste_de_campana'"
            ).fetchone()[0]
            == 0
        )
        padre = _ajuste(conn, camp, cfg, huella="h-padre", confirmado=_ANCLA)
        creado = _ANCLA + dt.timedelta(days=2)
        confirmado = _ANCLA + dt.timedelta(days=5)
        conn.execute(
            "INSERT INTO campana_ajuste (campana_id, platform, clase, antes_config_id,"
            " despues, huella, actor, go_literal, regresa_a, creado_el, confirmado_el)"
            " VALUES (%s, 'amazon_mx', 'ajuste_ubicacion', %s, %s, 'h-regreso', 'dueno',"
            " 'REGRESAR AJUSTE', %s, %s, %s)",
            (camp, cfg, json.dumps({"clase": "ajuste_ubicacion"}), padre, creado, confirmado),
        )
        filas = conn.execute(
            "SELECT hoja_id, confirmado_el FROM v_cambio_bid"
            " WHERE origen = 'ajuste_de_campana' ORDER BY confirmado_el"
        ).fetchall()
        assert (kw, confirmado) in filas
        assert (kw, creado) not in filas


@_skip_db
def test_0067_reversa_deja_vista_de_dos_ramas_y_esquema_igual():
    with db_67("orbit_67_rev", solo_base=True) as conn:
        antes = _foto(conn)
        conn.execute((ROOT / "migrations" / ORDEN67[4]).read_text(encoding="utf-8"))
        conn.execute((ROOT / "migrations" / REVERSA67).read_text(encoding="utf-8"))
        assert _foto(conn) == antes
        definicion = conn.execute("SELECT pg_get_viewdef('v_cambio_bid'::regclass)").fetchone()[0]
        assert "regreso_del_dueno" in definicion
        assert "ajuste_de_campana" not in definicion
        assert (
            conn.execute(
                "SELECT has_table_privilege('app_admin',"
                " 'ads_campana_config_observation', 'INSERT')"
            ).fetchone()[0]
            is False
        )
        assert (
            conn.execute(
                "SELECT has_sequence_privilege('app_admin',"
                " 'ads_campana_config_observation_id_seq', 'USAGE')"
            ).fetchone()[0]
            is False
        )


def _foto(conn) -> dict:
    tablas = [
        r[0]
        for r in conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1"
        ).fetchall()
    ]
    vistas = [
        r[0]
        for r in conn.execute(
            "SELECT viewname FROM pg_views WHERE schemaname = 'public' ORDER BY 1"
        ).fetchall()
    ]
    triggers = [
        r[0]
        for r in conn.execute(
            "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal ORDER BY 1"
        ).fetchall()
    ]
    rutinas = [
        r[0]
        for r in conn.execute(
            "SELECT proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace"
            " WHERE n.nspname = 'public' ORDER BY 1"
        ).fetchall()
    ]
    checks = conn.execute(
        "SELECT con.conname, c.relname, pg_get_constraintdef(con.oid)"
        " FROM pg_constraint con JOIN pg_class c ON c.oid = con.conrelid"
        " WHERE c.relnamespace = 'public'::regnamespace AND con.contype = 'c'"
    ).fetchall()
    return {
        "tablas": sorted(tablas),
        "vistas": sorted(vistas),
        "triggers": sorted(triggers),
        "rutinas": sorted(rutinas),
        "checks": sorted(checks),
    }


@_skip_db
def test_0067_sello_no_permite_cambiar_ninguna_otra_columna():
    """T03-T12: sellar y cambiar a la vez campana, plataforma, clase,
    antes, despues, huella, actor, literal, regresa_a o creado revienta."""
    from psycopg.types.json import Json

    with db_67("orbit_67_t") as conn:
        camp = _campana(conn)
        camp2 = _campana(conn, external="c-0067-b")
        cfg = _config(conn, camp)
        cfg2 = _config(conn, camp2)
        aj3 = _ajuste(conn, camp, cfg, huella="h-destino")
        casos = [
            ("campana_id", camp2),
            ("platform", "amazon_us"),
            ("clase", "presupuesto"),
            ("antes_config_id", cfg2),
            ("despues", Json({"x": 1})),
            ("huella", "h-otra"),
            ("actor", "otro"),
            ("go_literal", "OTRO LITERAL"),
            ("regresa_a", aj3),
            ("creado_el", _ANCLA + dt.timedelta(hours=1)),
        ]
        for i, (columna, valor) in enumerate(casos):
            aj = _ajuste(conn, camp, cfg, huella=f"h-t-{i}")
            with pytest.raises(psycopg.Error, match="SOLO-CONFIRMADO"):
                conn.execute(
                    f"UPDATE campana_ajuste SET {columna} = %s, confirmado_el = %s WHERE id = %s",
                    (valor, _ANCLA, aj),
                )
