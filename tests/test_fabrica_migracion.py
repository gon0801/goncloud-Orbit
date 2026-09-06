"""Migracion 0018 (FABRICA 01, spec §8): tablas del grupo, biblioteca por
tipo_producto, ledger de creacion y v_margen_producto (tarea 3).

(a) ESTATICO (pglast): el DDL parsea y trae los invariantes del spec.
(b) POSTGRES REAL: CHECKs, UNIQUEs, trigger de kinds y GRANTs muerden;
    la vista mide contra un ledger sembrado (tarea 3). Skip fail-closed
    sin Postgres (misma condicion que test_schema)."""

from __future__ import annotations

import datetime as dt
import os
import socket
from contextlib import contextmanager
from decimal import Decimal
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


def _ledger_producto(
    conn,
    pid,
    *,
    hoy,
    platform="amazon_mx",
    ventas=70,
    precio=100,
    costo=50,
    costo_desde=None,
    fee_desfase=0,
):
    """`ventas` dias consecutivos con UNA venta de `precio` y costo `costo`
    (misma moneda), todas con order_id propio; 7 cargos de plataforma sin
    orden (-100) y 1 cargo ads (-9999, EXCLUIDO). Cobertura 1, margen por
    producto = 100 x (ventas*precio + cargos_sin_orden - ventas*costo) / (ventas*precio).

    `fee_desfase` corre los dias de los 7 cargos sin orden: el indice real
    ledger_dedupe_sin_orden (0001) revienta si dos llamadas siembran el MISMO
    cargo (plataforma, fee_type, fecha, monto, moneda, sin order_id) — los
    cargos de plataforma no llevan producto y colisionan entre productos."""
    conn.execute(
        "INSERT INTO ingest_run (source, finished_at, ok) VALUES"
        " ('accounting_ledger_events', now(), true)"
    )
    run = conn.execute("INSERT INTO ingest_run (source) VALUES ('t') RETURNING id").fetchone()[0]
    conn.execute(
        "INSERT INTO sku_cost (product_id, cost_amount, cost_currency, includes_tax, valid_from)"
        " VALUES (%s, %s, 'MXN', true, %s)",
        (pid, costo, costo_desde or hoy - dt.timedelta(days=200)),
    )
    for i in range(ventas):
        conn.execute(
            "INSERT INTO ledger_event (platform, kind, event_date, order_id, product_id, quantity,"
            " amount, amount_currency, ingest_run_id)"
            " VALUES (%s, 'sale', %s, %s, %s, 1, %s, 'MXN', %s)",
            (platform, hoy - dt.timedelta(days=100 - i), f"o-{pid}-{i}", pid, precio, run),
        )
    for i in range(7):
        conn.execute(
            "INSERT INTO ledger_event (platform, kind, event_date, amount, amount_currency,"
            " fee_type, ingest_run_id) VALUES (%s, 'fee', %s, -100, 'MXN', 'closing', %s)",
            (platform, hoy - dt.timedelta(days=90 - i - fee_desfase), run),
        )
    conn.execute(
        "INSERT INTO ledger_event (platform, kind, event_date, amount, amount_currency,"
        " fee_type, ingest_run_id) VALUES (%s, 'fee', %s, -9999, 'MXN', 'ads', %s)",
        (platform, hoy - dt.timedelta(days=50 - fee_desfase), run),
    )
    return run


@_skip_db
def test_v_margen_producto_un_producto_reproduce_la_plataforma():
    """Con UN solo producto, el margen por producto es EXACTAMENTE el de
    v_target_margen_plataforma (misma maquinaria, regla 2): 70 ventas x100,
    costo 50, 7 cargos sin orden -100 prorrateados por cobertura 1 ->
    100 x (7000 - 700 - 3500) / 7000 = 40.0."""
    hoy = dt.date.today()
    with db_fabrica() as conn:
        pid, _ = _producto(conn)
        _ledger_producto(conn, pid, hoy=hoy)
        prod = conn.execute(
            "SELECT margen_neto_pct, cobertura, dias_con_venta, moneda, cargos_sin_orden"
            " FROM v_margen_producto WHERE platform = 'amazon_mx' AND product_id = %s",
            (pid,),
        ).fetchone()
        plat = conn.execute(
            "SELECT margen_neto_pct FROM v_target_margen_plataforma WHERE platform = 'amazon_mx'"
        ).fetchone()
        assert prod is not None and plat is not None
        assert prod[0] == Decimal("40") == plat[0]
        assert prod[1] == 1 and prod[2] == 70 and prod[3] == "MXN"
        assert prod[4] == Decimal("-700")


@_skip_db
def test_v_margen_producto_prorratea_cargos_con_orden_por_monto():
    """Orden multi-producto (spec §8): un cargo -30 con order_id de una orden
    donde A vendio 100 y B vendio 200 se reparte 1/3 a A y 2/3 a B. Solo una
    venta por producto -> dias < 30 -> margen NULL (guard), pero las columnas
    de cargos si se publican (la vista MIDE)."""
    hoy = dt.date.today()
    with db_fabrica() as conn:
        pa, _ = _producto(conn, sku="A", asin="B0AAAAAAAA", seller_sku="SA")
        pb, _ = _producto(conn, sku="B", asin="B0BBBBBBBB", seller_sku="SB")
        run = conn.execute("INSERT INTO ingest_run (source) VALUES ('t') RETURNING id").fetchone()[
            0
        ]
        conn.execute(
            "INSERT INTO ingest_run (source, finished_at, ok) VALUES"
            " ('accounting_ledger_events', now(), true)"
        )
        fecha = hoy - dt.timedelta(days=40)
        for pid in (pa, pb):
            conn.execute(
                "INSERT INTO sku_cost (product_id, cost_amount, cost_currency, includes_tax,"
                " valid_from) VALUES (%s, 10, 'MXN', true, %s)",
                (pid, hoy - dt.timedelta(days=200)),
            )
        for pid, monto in ((pa, 100), (pb, 200)):
            conn.execute(
                "INSERT INTO ledger_event (platform, kind, event_date, order_id, product_id,"
                " quantity, amount, amount_currency, ingest_run_id)"
                " VALUES ('amazon_mx', 'sale', %s, 'o-multi', %s, 1, %s, 'MXN', %s)",
                (fecha, pid, monto, run),
            )
        conn.execute(
            "INSERT INTO ledger_event (platform, kind, event_date, order_id, amount,"
            " amount_currency, fee_type, ingest_run_id)"
            " VALUES ('amazon_mx', 'fee', %s, 'o-multi', -30, 'MXN', 'closing', %s)",
            (fecha, run),
        )
        filas = dict(
            conn.execute(
                "SELECT product_id, cargos_con_orden FROM v_margen_producto"
                " WHERE platform = 'amazon_mx'"
            ).fetchall()
        )
        assert filas[pa] == Decimal("-10") and filas[pb] == Decimal("-20")
        margenes = dict(
            conn.execute("SELECT product_id, margen_neto_pct FROM v_margen_producto").fetchall()
        )
        assert margenes[pa] is None and margenes[pb] is None  # dias_con_venta = 1 < 30


@_skip_db
def test_v_margen_producto_guard_30_dias_y_arranque_fijo_de_ventana():
    """Decision escrita del dueno (tarea 1, 2026-09-05): guard
    `dias_con_venta < 30` y ventana `[2026-02-20, D-15)` con arranque FIJO.
    A: 29 ventas recientes + una del 2026-02-20 -> 30 dias -> margen medible
    (discrimina el 30 Y el arranque fijo: con [D-105, D-15) esa venta no
    contaria). B: 29 recientes + una del 2026-02-19 -> 29 -> NULL (discrimina
    el borde). Costo vigente desde 2026-01-01 para que ambas esten cubiertas."""
    hoy = dt.date.today()
    with db_fabrica() as conn:
        pa, _ = _producto(conn, sku="A", asin="B0AAAAAAAA", seller_sku="SA")
        pb, _ = _producto(conn, sku="B", asin="B0BBBBBBBB", seller_sku="SB")
        run = _ledger_producto(conn, pa, hoy=hoy, ventas=29, costo_desde=dt.date(2026, 1, 1))
        _ledger_producto(
            conn, pb, hoy=hoy, ventas=29, costo_desde=dt.date(2026, 1, 1), fee_desfase=7
        )
        for pid, fecha in ((pa, dt.date(2026, 2, 20)), (pb, dt.date(2026, 2, 19))):
            conn.execute(
                "INSERT INTO ledger_event (platform, kind, event_date, order_id, product_id,"
                " quantity,"
                " amount, amount_currency, ingest_run_id)"
                " VALUES ('amazon_mx', 'sale', %s, %s, %s, 1, 100, 'MXN', %s)",
                (fecha, f"o-borde-{pid}", pid, run),
            )
        filas = {
            r[0]: (r[1], r[2])
            for r in conn.execute(
                "SELECT product_id, dias_con_venta, margen_neto_pct FROM v_margen_producto"
                " WHERE product_id = ANY(%s)",
                ([pa, pb],),
            ).fetchall()
        }
        assert filas[pa][0] == 30 and filas[pa][1] is not None  # 2026-02-20 cuenta; 30 pasa
        assert filas[pb][0] == 29 and filas[pb][1] is None  # 2026-02-19 no cuenta; 29 < 30


@_skip_db
def test_v_margen_producto_sin_costo_no_cubre_y_sin_venta_no_existe():
    """Regla 3: venta sin sku_cost -> venta_cubierta 0 -> margen NULL; un
    producto sin ventas en ventana NO tiene fila (jamas cero)."""
    hoy = dt.date.today()
    with db_fabrica() as conn:
        pid, _ = _producto(conn)
        run = conn.execute("INSERT INTO ingest_run (source) VALUES ('t') RETURNING id").fetchone()[
            0
        ]
        conn.execute(
            "INSERT INTO ledger_event (platform, kind, event_date, order_id, product_id,"
            " quantity, amount, amount_currency, ingest_run_id)"
            " VALUES ('amazon_mx', 'sale', %s, 'o1', %s, 1, 100, 'MXN', %s)",
            (hoy - dt.timedelta(days=40), pid, run),
        )
        fila = conn.execute(
            "SELECT margen_neto_pct, venta_cubierta FROM v_margen_producto WHERE product_id = %s",
            (pid,),
        ).fetchone()
        # NULL EXIGIDO en ambas (regla 3): venta_cubierta es SUM(...) FILTER de
        # lineas cubiertas -> NULL si ninguna; un COALESCE a 0 seria un cero inventado.
        assert fila == (None, None)
        otro, _ = _producto(conn, sku="X", asin="B0XXXXXXXX", seller_sku="SX")
        assert (
            conn.execute(
                "SELECT count(*) FROM v_margen_producto WHERE product_id = %s", (otro,)
            ).fetchone()[0]
            == 0
        )


@_skip_db
def test_v_margen_producto_mezcla_de_moneda_en_denominadores_es_null():
    """r3 codex 2 (regla 4): los DENOMINADORES del prorrateo tambien hacen
    guard. (i) una unica venta USD de OTRO producto en la plataforma NULLea
    el margen del producto puro MXN (venta_plataforma quedo en 2 monedas);
    (ii) una orden con lineas cubiertas en dos monedas NULLea por
    n_monedas_orden (la misma linea USD tambien enciende el guard de
    plataforma: ambos guards son fail-closed sobre el mismo hecho)."""
    hoy = dt.date.today()
    with db_fabrica() as conn:  # (i) denominador de la plataforma
        pa, _ = _producto(conn)
        _ledger_producto(conn, pa, hoy=hoy)  # puro MXN: margen 40 sin la mezcla
        pb, _ = _producto(conn, sku="USD", asin="B0USDUSDUS", seller_sku="SUSD")
        run = conn.execute("INSERT INTO ingest_run (source) VALUES ('t') RETURNING id").fetchone()[
            0
        ]
        conn.execute(
            "INSERT INTO ledger_event (platform, kind, event_date, order_id, product_id,"
            " quantity, amount, amount_currency, ingest_run_id)"
            " VALUES ('amazon_mx', 'sale', %s, 'o-usd', %s, 1, 25, 'USD', %s)",
            (hoy - dt.timedelta(days=50), pb, run),
        )
        fila = conn.execute(
            "SELECT margen_neto_pct FROM v_margen_producto WHERE product_id = %s", (pa,)
        ).fetchone()
        assert fila[0] is None  # venta_plataforma en 2 monedas
    with db_fabrica() as conn:  # (ii) denominador de la orden
        pa, _ = _producto(conn)
        _ledger_producto(conn, pa, hoy=hoy)
        pb, _ = _producto(conn, sku="USD", asin="B0USDUSDUS", seller_sku="SUSD")
        run = conn.execute("INSERT INTO ingest_run (source) VALUES ('t') RETURNING id").fetchone()[
            0
        ]
        conn.execute(
            "INSERT INTO sku_cost (product_id, cost_amount, cost_currency, includes_tax,"
            " valid_from) VALUES (%s, 10, 'USD', true, %s)",
            (pb, hoy - dt.timedelta(days=200)),
        )
        for pid, monto, moneda in ((pb, 25, "USD"), (pa, 100, "MXN")):
            conn.execute(
                "INSERT INTO ledger_event (platform, kind, event_date, order_id, product_id,"
                " quantity, amount, amount_currency, ingest_run_id)"
                " VALUES ('amazon_mx', 'sale', %s, 'o-mixta', %s, 1, %s, %s, %s)",
                (hoy - dt.timedelta(days=50), pid, monto, moneda, run),
            )
        fila = conn.execute(
            "SELECT margen_neto_pct FROM v_margen_producto WHERE product_id = %s", (pa,)
        ).fetchone()
        assert fila[0] is None  # orden cubierta en 2 monedas (n_monedas_orden)


@_skip_db
def test_v_margen_producto_mezcla_de_moneda_en_cargos_del_producto_es_null():
    """D-3 (review PR #172, regla 4): las ordenes de un producto con fees en
    DOS monedas no pueden sumarse en el numerador cargos_con_orden. El guard
    viejo (MAX(moneda)) dejaba pasar 'USD' == moneda_unica con fees MXN+USD
    porque MAX es lexicografico; el conteo DISTINCT de monedas de los cargos
    del producto es el que NULLea."""
    hoy = dt.date.today()
    with db_fabrica() as conn:
        pid, _ = _producto(conn, sku="USFEE", asin="B0USFEEUSF", seller_sku="SUSFEE")
        conn.execute(
            "INSERT INTO ingest_run (source, finished_at, ok) VALUES"
            " ('accounting_ledger_events', now(), true)"
        )
        run = conn.execute("INSERT INTO ingest_run (source) VALUES ('t') RETURNING id").fetchone()[
            0
        ]
        conn.execute(
            "INSERT INTO sku_cost (product_id, cost_amount, cost_currency, includes_tax,"
            " valid_from) VALUES (%s, 10, 'USD', true, %s)",
            (pid, hoy - dt.timedelta(days=200)),
        )
        # 35 dias con venta (pasa el guard de dias), todo en USD: sin la mezcla
        # de fees el margen seria medible.
        for i in range(35):
            conn.execute(
                "INSERT INTO ledger_event (platform, kind, event_date, order_id, product_id,"
                " quantity, amount, amount_currency, ingest_run_id)"
                " VALUES ('amazon_us', 'sale', %s, %s, %s, 1, 100, 'USD', %s)",
                (hoy - dt.timedelta(days=100 - i), f"o-usd-{i}", pid, run),
            )
        # fees de las ordenes del producto en DOS monedas (MXN en la segunda)
        conn.execute(
            "INSERT INTO ledger_event (platform, kind, event_date, order_id, amount,"
            " amount_currency, fee_type, ingest_run_id)"
            " VALUES ('amazon_us', 'fee', %s, 'o-usd-0', -10, 'USD', 'closing', %s)",
            (hoy - dt.timedelta(days=100), run),
        )
        conn.execute(
            "INSERT INTO ledger_event (platform, kind, event_date, order_id, amount,"
            " amount_currency, fee_type, ingest_run_id)"
            " VALUES ('amazon_us', 'fee', %s, 'o-usd-1', -10, 'MXN', 'closing', %s)",
            (hoy - dt.timedelta(days=99), run),
        )
        fila = conn.execute(
            "SELECT margen_neto_pct FROM v_margen_producto WHERE product_id = %s", (pid,)
        ).fetchone()
        assert fila[0] is None
