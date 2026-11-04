"""Tests de la migracion `migrations/0062_bids02_placement.sql` (BIDS 02, V.2,
seccion 3): metricas por placement (`ads_placement_observation`).

`ORDEN62` es el subconjunto minimo que toca 0062 (0001 + 0062): ninguna
migracion entre 0002 y 0061 menciona `ads_placement_observation`, y 0062
solo necesita de 0001 `ad_entity`, el tipo `platform`,
`prohibir_mutacion()` y los roles `app_*` (verificado por grep al escribir
el modulo). Todos los tests
corren con `ORBIT_TEST_DSN` apuntado (0 skipped); sin Postgres skipean
en verde fuera de CI (patron `test_apply_schema`).
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
ORDEN62 = (
    "0001_initial.sql",
    "0062_bids02_placement.sql",
)
REVERSA62 = "0062_reversa_bids02_placement.sql"

_DIA = dt.date(2026, 10, 4)
_ANCLA = dt.datetime(2026, 10, 5, 7, 25, tzinfo=dt.UTC)
_ANCLA_MAS_TARDE = dt.datetime(2026, 10, 5, 9, 0, tzinfo=dt.UTC)

_UBICACIONES = (
    "arriba_de_busqueda",
    "resto_de_busqueda",
    "paginas_de_producto",
    "fuera_de_amazon",
)

_COLUMNAS = (
    "platform",
    "ad_entity_id",
    "placement",
    "metric_date",
    "observed_at",
    "metric_currency",
    "impressions",
    "clicks",
    "cost",
    "orders",
    "ad_revenue",
    "source_report_id",
)

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)


@contextmanager
def db_62(prefijo: str, *, solo_base: bool = False):
    """DB temporal con ORDEN62 (o solo 0001 con `solo_base`); yields conn
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
        nombres = ORDEN62 if not solo_base else ORDEN62[:1]
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


def _campana(conn, external="c-0062") -> int:
    return conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id) VALUES ('amazon_mx',"
        " 'campaign', %s) RETURNING id",
        (external,),
    ).fetchone()[0]


def _observa(conn, campana: int, observado: dt.datetime = _ANCLA, **campos) -> None:
    base = {
        "platform": "amazon_mx",
        "placement": "arriba_de_busqueda",
        "metric_date": _DIA,
        "metric_currency": "MXN",
        "impressions": 100,
        "clicks": 5,
        "cost": Decimal("12.50"),
        "orders": 1,
        "ad_revenue": Decimal("200.00"),
        "source_report_id": "rep-0062-a",
    }
    base.update(campos)
    columnas = ", ".join(base)
    marcas = ", ".join(["%s"] * len(base))
    conn.execute(
        f"INSERT INTO ads_placement_observation (ad_entity_id, observed_at,"
        f" {columnas}) VALUES (%s, %s, {marcas})",
        (campana, observado, *base.values()),
    )


@_skip_db
def test_0062_rechaza_placement_fuera_de_ubicacion():
    """Un `placement` fuera de los cuatro de `Ubicacion` revienta en
    `placement_vocabulario`; los cuatro pasan. Rojo pre-0062: la tabla no
    existe."""
    with db_62("orbit_62_vocab") as conn:
        camp = _campana(conn)
        for malo in ("top_de_busqueda", "TOP", ""):
            with pytest.raises(psycopg.errors.CheckViolation, match="placement_vocabulario"):
                _observa(conn, camp, placement=malo)
        for i, bueno in enumerate(_UBICACIONES):
            _observa(conn, camp, placement=bueno, source_report_id=f"rep-0062-{i}")
        assert conn.execute("SELECT count(*) FROM ads_placement_observation").fetchone()[0] == 4


@_skip_db
def test_0062_columnas_sin_top_of_search_is():
    """La tabla trae las columnas de `datos.sql` menos `top_of_search_is`
    (Amazon no la entrega): sin `id`, sin `ingest_run_id` y con
    `source_report_id` NOT NULL. Rojo pre-0062: la tabla no existe."""
    with db_62("orbit_62_cols") as conn:
        filas = conn.execute(
            "SELECT column_name, is_nullable FROM information_schema.columns"
            " WHERE table_schema = 'public'"
            " AND table_name = 'ads_placement_observation' ORDER BY ordinal_position"
        ).fetchall()
        assert [c for c, _ in filas] == list(_COLUMNAS)
        assert dict(filas)["source_report_id"] == "NO"


@_skip_db
def test_0062_dedupe_absorbe_reingesta():
    """El mismo reporte ingerido dos veces deja una fila (ON CONFLICT contra
    el indice parcial, aunque cambie `observed_at`); dos reportes distintos
    del mismo dia dejan dos. Rojo pre-0062: la tabla no existe."""
    with db_62("orbit_62_dedupe") as conn:
        camp = _campana(conn)
        _observa(conn, camp)
        conn.execute(
            "INSERT INTO ads_placement_observation (platform, ad_entity_id,"
            " placement, metric_date, observed_at, metric_currency, impressions,"
            " clicks, cost, orders, ad_revenue, source_report_id)"
            " VALUES ('amazon_mx', %s, 'arriba_de_busqueda', %s, %s, 'MXN', 100,"
            " 5, 12.50, 1, 200.00, 'rep-0062-a') ON CONFLICT (platform, ad_entity_id,"
            " placement, metric_date, source_report_id)"
            " WHERE source_report_id IS NOT NULL DO NOTHING",
            (camp, _DIA, _ANCLA_MAS_TARDE),
        )
        assert conn.execute("SELECT count(*) FROM ads_placement_observation").fetchone()[0] == 1
        _observa(conn, camp, _ANCLA_MAS_TARDE, source_report_id="rep-0062-b")
        assert conn.execute("SELECT count(*) FROM ads_placement_observation").fetchone()[0] == 2


@_skip_db
def test_0062_reversa_deja_esquema_como_estaba():
    """Aplicar 0062 y su reversa deja el catalogo (tablas, vistas, indices,
    triggers, rutinas, CHECKs) identico al de solo-0001. Puro psycopg, sin
    binarios. Rojo pre-0062: la migracion no existe."""
    with db_62("orbit_62_reversa", solo_base=True) as conn:
        antes = _foto(conn)
        conn.execute((ROOT / "migrations" / ORDEN62[1]).read_text(encoding="utf-8"))
        assert ("ads_placement_observation", "r") in {(t, k) for t, k in _foto(conn)["tablas"]}
        assert "apo_dedupe_reporte" in {t for t, _ in _foto(conn)["indices"]}
        conn.execute((ROOT / "migrations" / REVERSA62).read_text(encoding="utf-8"))
        assert _foto(conn) == antes


def _foto(conn) -> dict:
    tablas = conn.execute(
        "SELECT relname, relkind FROM pg_class"
        " WHERE relnamespace = 'public'::regnamespace"
        " AND relkind IN ('r', 'v', 'm', 'f', 'p')"
        " AND relname NOT LIKE 'pg_%'"
    ).fetchall()
    indices = conn.execute(
        "SELECT relname, relkind FROM pg_class"
        " WHERE relnamespace = 'public'::regnamespace AND relkind = 'i'"
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
        "indices": sorted(indices),
        "triggers": sorted(triggers),
        "rutinas": sorted(r[0] for r in rutinas),
        "checks": sorted(checks),
    }


@_skip_db
def test_0062_placements_append_only_rechaza_update_delete_truncate():
    """v2-16: la tabla de placements es append-only de verdad."""
    with db_62("orbit_62_ao") as conn:
        _observa(conn, _campana(conn))
        for sentencia in (
            "UPDATE ads_placement_observation SET observed_at = now()",
            "DELETE FROM ads_placement_observation",
            "TRUNCATE ads_placement_observation",
        ):
            with pytest.raises(psycopg.Error, match="APPEND-ONLY"):
                conn.execute(sentencia)


@_skip_db
def test_0062_dominio_placement_exactamente_ubicacion():
    """v2-17: el dominio del CHECK es EXACTAMENTE Ubicacion (ni uno mas)."""
    import re
    from typing import get_args

    from app.ads.placements import _UBICACION, Ubicacion

    with db_62("orbit_62_dom") as conn:
        definicion = conn.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
            " WHERE conname = 'placement_vocabulario'"
        ).fetchone()[0]
    en_base = set(re.findall(r"'([^']+)'::text", definicion))
    assert en_base == set(get_args(Ubicacion)) == set(_UBICACION.values())


@_skip_db
def test_0062_sello_moneda_rechaza_mx_en_usd():
    """S2: las metricas de una campana MX en USD revientan en el sello."""
    with (
        db_62("orbit_62_sello") as conn,
        pytest.raises(psycopg.errors.CheckViolation, match="reporta sus metricas"),
    ):
        _observa(conn, _campana(conn), metric_currency="USD")


@_skip_db
def test_0062_rechaza_costo_negativo():
    """S3: costo -1 revienta en placement_no_negativos."""
    with (
        db_62("orbit_62_negs") as conn,
        pytest.raises(psycopg.errors.CheckViolation, match="placement_no_negativos"),
    ):
        _observa(conn, _campana(conn), cost=Decimal("-1"))


@_skip_db
def test_0062_rechaza_fila_con_plataforma_de_otra_campana():
    """S4: la fila que declara US para una campana MX revienta en el sello."""
    with (
        db_62("orbit_62_plat") as conn,
        pytest.raises(psycopg.errors.CheckViolation, match="declara plataforma"),
    ):
        _observa(conn, _campana(conn), platform="amazon_us", metric_currency="USD")


@_skip_db
def test_0062_rechaza_entidad_que_no_es_campana():
    """S5: un ad group revienta en el sello de kind."""
    with db_62("orbit_62_kind") as conn:
        grupo = conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id, parent_id) VALUES"
            " ('amazon_mx', 'ad_group', 'g-0062-s5', %s) RETURNING id",
            (_campana(conn),),
        ).fetchone()[0]
        with pytest.raises(psycopg.errors.CheckViolation, match="no es kind=campaign"):
            _observa(conn, grupo)


@_skip_db
def test_0062_rechaza_metric_date_futura():
    """S6: metric_date de manana revienta en el sello de fecha."""
    with db_62("orbit_62_fecha") as conn:
        manana = dt.datetime.now(dt.UTC).date() + dt.timedelta(days=1)
        with pytest.raises(psycopg.errors.CheckViolation, match="es futura"):
            _observa(conn, _campana(conn), metric_date=manana)
