"""Migracion 0018 (FABRICA 01, spec §8): tablas del grupo, biblioteca por
tipo_producto, ledger de creacion y v_margen_producto (tarea 3).

(a) ESTATICO (pglast): el DDL parsea y trae los invariantes del spec.
(b) POSTGRES REAL: CHECKs, UNIQUEs, trigger de kinds y GRANTs muerden;
    la vista mide contra un ledger sembrado (tarea 3). Skip fail-closed
    sin Postgres (misma condicion que test_schema)."""

from __future__ import annotations

import os
import socket
from contextlib import contextmanager
from pathlib import Path

import pglast
import psycopg
import pytest
from psycopg import sql as pgsql
from test_schema import _postgres_obligatorio_ausente, _test_dsn

ROOT = Path(__file__).resolve().parents[1]
MIGRACIONES = ROOT / "migrations"
# Orden minimo para que 0018 aplique: enum product_ad (0004), first_seen_at
# (0017) y la vista de plataforma (0015/0016) cuya maquinaria copia 0018.
ORDEN = (
    "0001_initial.sql",
    "0002_apply.sql",
    "0003_goal_bounds_explicit.sql",
    "0004_ad_entity_kind_product_ad.sql",
    "0013_entidad_inerte.sql",
    "0014_keyword_archivo_manual.sql",
    "0015_target_margen_plataforma.sql",
    "0016_target_margen_correcciones.sql",
    "0017_first_seen_at.sql",
    "0018_fabrica_campanas.sql",
)
SQL18 = (MIGRACIONES / "0018_fabrica_campanas.sql").read_text(encoding="utf-8")

_skip_db = pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres")


@contextmanager
def db_fabrica(prefijo: str = "orbit_fabrica"):
    """DB temporal con las migraciones de ORDEN; yields conn autocommit."""
    dsn = _test_dsn()
    db = f"{prefijo}_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        for nombre in ORDEN:
            conn.execute((MIGRACIONES / nombre).read_text(encoding="utf-8"))
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def _entidad(conn, platform, kind, external, parent=None, listing_id=None) -> int:
    return conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id, parent_id, listing_id)"
        " VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (platform, kind, external, parent, listing_id),
    ).fetchone()[0]


def _producto(conn, sku="SKU-1", asin="B0FABRICA01", platform="amazon_mx", seller_sku="SS-1"):
    pid = conn.execute(
        "INSERT INTO product (odoo_sku, name) VALUES (%s, %s) RETURNING id", (sku, sku)
    ).fetchone()[0]
    lid = conn.execute(
        "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
        " VALUES (%s, %s, %s, %s) RETURNING id",
        (pid, platform, asin, seller_sku),
    ).fetchone()[0]
    return pid, lid


def _lote(conn, lote="fabrica-amazon_mx-collar_perro-20260905-120000"):
    conn.execute(
        "INSERT INTO fabrica_lote (lote, platform, tipo_producto, nombre_base, go_literal,"
        " huella, plan, modo_goal, estado) VALUES (%s, 'amazon_mx', 'collar_perro', 'Collar',"
        " 'go', 'h', '{}'::jsonb, 'shadow', 'planeado')",
        (lote,),
    )
    return lote


def _grupo(conn, lote) -> int:
    return conn.execute(
        "INSERT INTO campana_grupo (platform, tipo_producto, nombre_base, lote,"
        " target_acos_pct, target_derivado_pct, fraccion, target_procedencia, go_literal)"
        " VALUES ('amazon_mx', 'collar_perro', 'Collar', %s, 19.10, 19.1040, 0.5,"
        " 'margen_minimo_grupo', 'go') RETURNING id",
        (lote,),
    ).fetchone()[0]


# ---------------------------------------------------------------------------
# (a) Estatico
# ---------------------------------------------------------------------------


def test_0018_parsea_y_trae_el_ddl_del_spec():
    pglast.parse_sql(SQL18)  # revienta si no parsea
    for tabla in (
        "fabrica_lote",
        "fabrica_lote_paso",
        "campana_grupo",
        "campana_grupo_rol",
        "campana_grupo_producto",
        "keyword_biblioteca",
        "negative_biblioteca",
        "harvest_excepcion",
    ):
        assert f"CREATE TABLE {tabla}" in SQL18, tabla
        assert f"COMMENT ON TABLE {tabla}" in SQL18, f"{tabla} sin COMMENT"
    assert "CREATE TYPE campana_rol AS ENUM" in SQL18
    for rol in (
        "auto_discovery",
        "category_phrase",
        "product_targeting",
        "category_broad",
        "category_exact",
    ):
        assert f"'{rol}'" in SQL18
    assert "paso_evidencia_applied" in SQL18, "applied exige external_id+ack+readback"
    assert "grupo_rol_kinds" in SQL18, "trigger: campana y ad group con kind correcto"
    assert "campana_grupo_producto_listing" in SQL18, "trigger: listing DEL producto y plataforma"
    assert "harvest_excepcion_kind" in SQL18, "trigger: excepcion solo sobre kind=campaign"
    for rol in ("app_read", "app_ingest", "app_decide", "app_admin"):
        assert rol in SQL18
    assert "GRANT INSERT, UPDATE ON" in SQL18 and "TO app_admin" in SQL18


# ---------------------------------------------------------------------------
# (b) Postgres real
# ---------------------------------------------------------------------------


@_skip_db
def test_0018_aplica_sobre_el_esquema_vivo():
    with db_fabrica() as conn:
        n = conn.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name IN"
            " ('fabrica_lote','fabrica_lote_paso','campana_grupo','campana_grupo_rol',"
            " 'campana_grupo_producto','keyword_biblioteca','negative_biblioteca',"
            " 'harvest_excepcion')"
        ).fetchone()[0]
        assert n == 8


@_skip_db
def test_paso_applied_exige_evidencia_completa():
    """Espejo de archivo_evidencia_applied: applied sin external/ack/readback
    revienta; con los tres, pasa."""
    with db_fabrica() as conn:
        lote = _lote(conn)
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO fabrica_lote_paso (lote, orden, rol, recurso, request_payload,"
                " estado) VALUES (%s, 1, 'category_exact', 'campaign', '{}'::jsonb, 'applied')",
                (lote,),
            )
        conn.execute(
            "INSERT INTO fabrica_lote_paso (lote, orden, rol, recurso, request_payload,"
            " external_id, ack, readback_estado, estado) VALUES (%s, 1, 'category_exact',"
            " 'campaign', '{}'::jsonb, '111', '{}'::jsonb, 'ENABLED', 'applied')",
            (lote,),
        )


@_skip_db
def test_grupo_rol_exige_campana_y_ad_group_reales():
    """Trigger campana_grupo_rol_kinds (patron goal_scope_campana_real): la
    campana debe ser kind='campaign' y el ad group kind='ad_group' hijo de
    ESA campana; cualquier otra combinacion revienta."""
    with db_fabrica() as conn:
        lote = _lote(conn)
        grupo = _grupo(conn, lote)
        camp = _entidad(conn, "amazon_mx", "campaign", "c1")
        ag = _entidad(conn, "amazon_mx", "ad_group", "ag1", parent=camp)
        otra = _entidad(conn, "amazon_mx", "campaign", "c2")
        ag_otra = _entidad(conn, "amazon_mx", "ad_group", "ag2", parent=otra)
        with pytest.raises(psycopg.errors.CheckViolation):  # ad_group como campana
            conn.execute(
                "INSERT INTO campana_grupo_rol VALUES (%s, 'category_exact', %s, %s)",
                (grupo, ag, ag),
            )
        with pytest.raises(psycopg.errors.CheckViolation):  # ad group de OTRA campana
            conn.execute(
                "INSERT INTO campana_grupo_rol VALUES (%s, 'category_exact', %s, %s)",
                (grupo, camp, ag_otra),
            )
        conn.execute(
            "INSERT INTO campana_grupo_rol VALUES (%s, 'category_exact', %s, %s)",
            (grupo, camp, ag),
        )
        # UNIQUE(ad_entity_id): una campana pertenece a lo sumo a un grupo
        grupo2 = _grupo(conn, _lote(conn, "fabrica-amazon_mx-collar_perro-20260905-130000"))
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                "INSERT INTO campana_grupo_rol VALUES (%s, 'category_exact', %s, %s)",
                (grupo2, camp, ag),
            )


@_skip_db
def test_grupo_producto_snapshot_y_biblioteca_unica():
    with db_fabrica() as conn:
        lote = _lote(conn)
        grupo = _grupo(conn, lote)
        pid, lid = _producto(conn)
        conn.execute(
            "INSERT INTO campana_grupo_producto VALUES (%s, %s, %s, 'SS-1', 38.2000)",
            (grupo, pid, lid),
        )
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                "INSERT INTO campana_grupo_producto VALUES (%s, %s, %s, 'SS-1', 38.2000)",
                (grupo, pid, lid),
            )
        conn.execute(
            "INSERT INTO keyword_biblioteca (tipo_producto, platform, texto, origen, orders,"
            " cost, revenue, moneda) VALUES ('collar_perro', 'amazon_mx', 'collar perro',"
            " 'campana:1', 3, 10.5, 90, 'MXN')"
        )
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                "INSERT INTO keyword_biblioteca (tipo_producto, platform, texto, origen)"
                " VALUES ('collar_perro', 'amazon_mx', 'collar perro', 'campana:2')"
            )
        with pytest.raises(psycopg.errors.CheckViolation):  # dinero sin moneda (regla 4)
            conn.execute(
                "INSERT INTO keyword_biblioteca (tipo_producto, platform, texto, origen, cost)"
                " VALUES ('collar_perro', 'amazon_mx', 'otra', 'campana:1', 1)"
            )


@_skip_db
def test_grupo_producto_exige_listing_del_producto_y_plataforma():
    """Trigger campana_grupo_producto_listing (patron campana_grupo_rol_kinds):
    la FK sola admite un listing de OTRO producto o de OTRA plataforma; el
    trigger exige listing.product_id = NEW.product_id y listing.platform =
    plataforma del grupo."""
    with db_fabrica() as conn:
        grupo = _grupo(conn, _lote(conn))
        pid, lid = _producto(conn)
        _otro_pid, otro_lid = _producto(conn, sku="B", asin="B0BBBBBBBB", seller_sku="SB")
        lid_us = conn.execute(
            "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
            " VALUES (%s, 'amazon_us', 'B0US000001', 'SS-US') RETURNING id",
            (pid,),
        ).fetchone()[0]
        with pytest.raises(psycopg.errors.CheckViolation):  # listing de OTRO producto
            conn.execute(
                "INSERT INTO campana_grupo_producto VALUES (%s, %s, %s, 'SB', 1)",
                (grupo, pid, otro_lid),
            )
        with pytest.raises(psycopg.errors.CheckViolation):  # listing de OTRA plataforma
            conn.execute(
                "INSERT INTO campana_grupo_producto VALUES (%s, %s, %s, 'SS-US', 1)",
                (grupo, pid, lid_us),
            )
        conn.execute(  # listing correcto: pasa
            "INSERT INTO campana_grupo_producto VALUES (%s, %s, %s, 'SS-1', 38.2)",
            (grupo, pid, lid),
        )


@_skip_db
def test_harvest_excepcion_exige_kind_campaign():
    """Trigger harvest_excepcion_kind (patron goal_scope_campana_real): la
    excepcion se congela sobre una CAMPANA; un ad_group (u otra kind) revienta."""
    with db_fabrica() as conn:
        camp = _entidad(conn, "amazon_mx", "campaign", "c-exc")
        ag = _entidad(conn, "amazon_mx", "ad_group", "ag-exc", parent=camp)
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO harvest_excepcion (ad_entity_id, destino_campaign_external,"
                " destino_ad_group_external, go_literal) VALUES (%s, 'c1', 'ag1', 'go')",
                (ag,),
            )
        conn.execute(
            "INSERT INTO harvest_excepcion (ad_entity_id, destino_campaign_external,"
            " destino_ad_group_external, go_literal) VALUES (%s, 'c1', 'ag1', 'go')",
            (camp,),
        )


@_skip_db
def test_grants_0018():
    """app_admin escribe las tablas nuevas; app_decide y app_read solo leen
    (el ruteo de F2 corre como motor y lee campana_grupo_rol)."""
    with db_fabrica() as conn:
        filas = conn.execute(
            "SELECT grantee, table_name, privilege_type FROM information_schema.role_table_grants"
            " WHERE table_name IN ('campana_grupo_rol', 'fabrica_lote_paso', 'keyword_biblioteca')"
        ).fetchall()
        privs = {(g, t, p) for g, t, p in filas}
        assert ("app_admin", "campana_grupo_rol", "INSERT") in privs
        assert ("app_admin", "fabrica_lote_paso", "UPDATE") in privs
        assert ("app_decide", "campana_grupo_rol", "SELECT") in privs
        assert ("app_read", "keyword_biblioteca", "SELECT") in privs
        assert ("app_decide", "campana_grupo_rol", "INSERT") not in privs
        assert ("app_read", "fabrica_lote_paso", "INSERT") not in privs
