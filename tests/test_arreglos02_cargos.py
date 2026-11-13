"""ARREGLOS 02 D: la comision estimada de Amazon no se suma a la real.

El libro guarda una comision estimada por pedido (source_event_id
`amazon|fee_estimate|referral_fee|<pedido>`) y despues la real
(`amazon|finance|fee|...|Commission`). Las cuatro vistas de margen restaban
las dos, y el margen de Mexico y Estados Unidos salia mas bajo, con el la
meta de ACoS. La migracion 0086 crea v_ledger_cargo, que ignora la estimada
cuando ya llego la real del mismo pedido, y las cuatro vistas leen de ella.

Son INTEGRACION: base temporal con TODAS las migraciones en orden, sin la
0011 y sin las reversas, como tests/test_bids02_vistas.py:48.
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
from psycopg import sql as pgsql
from test_schema import _postgres_obligatorio_ausente, _test_dsn

ROOT = Path(__file__).resolve().parents[1]

FALTA_POSTGRES = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)


@contextmanager
def _db_arreglos02(prefijo: str):
    """DB temporal con todas las migraciones en orden, sin la 0011 y sin las
    reversas (igual que tests/test_bids02_vistas.py:48)."""
    dsn = _test_dsn()
    db = f"{prefijo}_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        for ruta in sorted((ROOT / "migrations").glob("*.sql")):
            if "0011_" in ruta.name or "_reversa_" in ruta.name:
                continue
            conn.execute(ruta.read_text(encoding="utf-8"))
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def _siembra(conn):
    """Un producto de amazon_mx con costo de 300 MXN desde hoy-200, dos
    pedidos de 1,000 MXN cantidad 1 (O1 hoy-40, O2 hoy-39) y su comision
    referral_fee: O1 trae la real (-150) y la estimada (-125); O2 solo la
    estimada (-100). El producto pertenece a una familia
    (tests/test_cycle_target_ciclo.py:349)."""
    hoy = conn.execute("SELECT CURRENT_DATE").fetchone()[0]
    run = conn.execute(
        "INSERT INTO ingest_run (source, finished_at, ok) VALUES (%s, now(), true) RETURNING id",
        ("accounting_ledger_events",),
    ).fetchone()[0]
    pid = conn.execute(
        "INSERT INTO product (odoo_sku, name) VALUES (%s, %s) RETURNING id",
        ("SKU-D", "Producto D"),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO sku_cost (product_id, cost_amount, cost_currency, includes_tax,"
        " valid_from) VALUES (%s, %s, %s, true, %s)",
        (pid, Decimal("300"), "MXN", hoy - dt.timedelta(days=200)),
    )
    pedidos = {"O1": 40, "O2": 39}
    for orden, atras in pedidos.items():
        conn.execute(
            "INSERT INTO ledger_event (platform, kind, event_date, order_id, product_id,"
            " quantity, amount, amount_currency, ingest_run_id)"
            " VALUES ('amazon_mx', 'sale', %s, %s, %s, 1, %s, 'MXN', %s)",
            (hoy - dt.timedelta(days=atras), orden, pid, Decimal("1000"), run),
        )
    cargos = [
        ("O1", "amazon|finance|fee|O1|SKU-D|Commission", Decimal("-150")),
        ("O1", "amazon|fee_estimate|referral_fee|O1", Decimal("-125")),
        ("O2", "amazon|fee_estimate|referral_fee|O2", Decimal("-100")),
    ]
    for orden, source_event_id, monto in cargos:
        conn.execute(
            "INSERT INTO ledger_event (platform, kind, event_date, order_id, amount,"
            " amount_currency, fee_type, source_event_id, ingest_run_id)"
            " VALUES ('amazon_mx', 'fee', %s, %s, %s, 'MXN', 'referral_fee', %s, %s)",
            (hoy - dt.timedelta(days=pedidos[orden]), orden, monto, source_event_id, run),
        )
    fid = conn.execute(
        "INSERT INTO familia (platform, nombre, slug) VALUES ('amazon_mx', %s, %s) RETURNING id",
        ("Familia D", "familia_d"),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO producto_familia (product_id, platform, familia_id)"
        " VALUES (%s, 'amazon_mx', %s)",
        (pid, fid),
    )
    return pid, fid


def _cargos_con_orden(conn, pid, fid) -> dict:
    """cargos_con_orden de las cuatro vistas para el producto y su familia."""
    return {
        "v_target_margen_plataforma": conn.execute(
            "SELECT cargos_con_orden FROM v_target_margen_plataforma WHERE platform = 'amazon_mx'"
        ).fetchone()[0],
        "v_margen_producto": conn.execute(
            "SELECT cargos_con_orden FROM v_margen_producto"
            " WHERE platform = 'amazon_mx' AND product_id = %s",
            (pid,),
        ).fetchone()[0],
        "v_economia_producto": conn.execute(
            "SELECT cargos_con_orden FROM v_economia_producto"
            " WHERE platform = 'amazon_mx' AND product_id = %s",
            (pid,),
        ).fetchone()[0],
        "v_margen_familia": conn.execute(
            "SELECT cargos_con_orden FROM v_margen_familia"
            " WHERE platform = 'amazon_mx' AND familia_id = %s",
            (fid,),
        ).fetchone()[0],
    }


def _muestra_margen(conn, pid) -> Decimal:
    return conn.execute(
        "SELECT muestra_margen_neto_pct FROM v_economia_producto"
        " WHERE platform = 'amazon_mx' AND product_id = %s",
        (pid,),
    ).fetchone()[0]


@FALTA_POSTGRES
def test_arreglos02_d_la_estimada_no_se_suma_a_la_comision_real():
    with _db_arreglos02("orbit_arreglos02_d1") as conn:
        pid, fid = _siembra(conn)

        cargos = _cargos_con_orden(conn, pid, fid)
        assert cargos == {
            "v_target_margen_plataforma": Decimal("-250"),
            "v_margen_producto": Decimal("-250"),
            "v_economia_producto": Decimal("-250"),
            "v_margen_familia": Decimal("-250"),
        }, cargos

        muestra = _muestra_margen(conn, pid)
        assert muestra == Decimal("57.5"), muestra


@FALTA_POSTGRES
def test_arreglos02_d_el_cargo_sin_source_event_id_no_se_descarta():
    """Un cargo con order_id pero SIN source_event_id (el re-sync de fees del
    indice ledger_dedupe_con_orden, 0001) no esta identificado como estimada:
    nunca se descarta, aunque exista la comision real del mismo pedido. En SQL,
    NULL = 'fee_estimate' da NULL y con contraparte real la fila se caia en
    silencio (F1 de la revision del 2026-11-13)."""
    with _db_arreglos02("orbit_arreglos02_d3") as conn:
        pid, fid = _siembra(conn)
        hoy = conn.execute("SELECT CURRENT_DATE").fetchone()[0]
        run = conn.execute("SELECT id FROM ingest_run ORDER BY id DESC LIMIT 1").fetchone()[0]
        conn.execute(
            "INSERT INTO ledger_event (platform, kind, event_date, order_id, amount,"
            " amount_currency, fee_type, source_event_id, ingest_run_id)"
            " VALUES ('amazon_mx', 'fee', %s, 'O1', %s, 'MXN', 'referral_fee', NULL, %s)",
            (hoy - dt.timedelta(days=40), Decimal("-10"), run),
        )

        cargos = _cargos_con_orden(conn, pid, fid)
        assert cargos == {
            "v_target_margen_plataforma": Decimal("-260"),
            "v_margen_producto": Decimal("-260"),
            "v_economia_producto": Decimal("-260"),
            "v_margen_familia": Decimal("-260"),
        }, cargos


@FALTA_POSTGRES
def test_arreglos02_d_la_reversa_regresa_las_vistas():
    with _db_arreglos02("orbit_arreglos02_d2") as conn:
        pid, fid = _siembra(conn)

        con_migracion = _cargos_con_orden(conn, pid, fid)
        assert con_migracion["v_margen_producto"] == Decimal("-250"), con_migracion

        reversa = ROOT / "migrations" / "0086_reversa_arreglos02_cargos.sql"
        conn.execute(reversa.read_text(encoding="utf-8"))

        assert conn.execute("SELECT to_regclass('public.v_ledger_cargo')").fetchone()[0] is None
        cargos = _cargos_con_orden(conn, pid, fid)
        assert cargos == {
            "v_target_margen_plataforma": Decimal("-375"),
            "v_margen_producto": Decimal("-375"),
            "v_economia_producto": Decimal("-375"),
            "v_margen_familia": Decimal("-375"),
        }, cargos

        muestra = _muestra_margen(conn, pid)
        assert muestra == Decimal("51.25"), muestra
