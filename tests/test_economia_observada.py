"""ORBIT 19 B.2: economia observada por producto (v_economia_producto, 0021).

(a) ESTATICO (pglast): 0021 parsea, crea SOLO la vista nueva (sin tocar
    v_margen_producto) y da SELECT a los roles de lectura.
(b) POSTGRES REAL: sobre fixture sembrado --
    1. v_margen_producto conserva valores previos tras aplicar 0021;
    2. muestra limitada solo con integridad valida;
    3. dos listings del mismo producto comparten grano (total 100, no 200);
    4. producto sin ventas -> None, no 0;
    5. margen negativo se conserva.
    Skip fail-closed sin Postgres (misma condicion que test_schema)."""

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
from test_fabrica_migracion import ORDEN, _producto
from test_schema import _postgres_obligatorio_ausente, _test_dsn

ROOT_MIGRACIONES = Path(__file__).resolve().parents[1] / "migrations"
SQL21 = (ROOT_MIGRACIONES / "0021_economia_observada.sql").read_text(encoding="utf-8")

_skip_db = pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres")


@contextmanager
def db_economia(con_0021: bool = True):
    """DB temporal con ORDEN (hasta 0019) y, opcionalmente, 0021."""
    dsn = _test_dsn()
    db = f"orbit_b2_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        orden = ORDEN if not con_0021 else (*ORDEN, "0021_economia_observada.sql")
        for nombre in orden:
            conn.execute((ROOT_MIGRACIONES / nombre).read_text(encoding="utf-8"))
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


# ---------------------------------------------------------------------------
# (a) Estatico
# ---------------------------------------------------------------------------


def test_0021_crea_solo_la_vista_y_no_toca_v_margen_producto():
    """El DDL parsea y su huella es exactamente: 1 CREATE VIEW
    (v_economia_producto), COMMENT y GRANT SELECT. Sin CREATE OR REPLACE /
    ALTER / DROP sobre v_margen_producto (regla dura de B.2)."""
    from pglast import ast, enums

    stmts = [s.stmt for s in pglast.parse_sql(SQL21)]
    vistas = [s.view.relname for s in stmts if isinstance(s, ast.ViewStmt)]
    assert vistas == ["v_economia_producto"]
    # Ninguna otra sentencia muta estructura: solo COMMENT y GRANT
    muta = (ast.AlterTableStmt, ast.DropStmt, ast.CreateStmt)
    assert not [s for s in stmts if isinstance(s, muta)]
    comentadas = {
        o.sval
        for s in stmts
        if isinstance(s, ast.CommentStmt) and s.objtype == enums.ObjectType.OBJECT_VIEW
        for o in s.object
    }
    assert comentadas == {"v_economia_producto"}
    grants = [s for s in stmts if isinstance(s, ast.GrantStmt)]
    assert grants and all(
        {p.priv_name for p in (g.privileges or [])} == {"select"} for g in grants
    ), "solo SELECT: la economia observada es de lectura"
    assert all(
        {r.rolename for r in g.grantees} == {"app_read", "app_ingest", "app_decide", "app_admin"}
        for g in grants
    )
    # La vista madura se LEE (aparece como rango del SELECT del cuerpo); como
    # no hay otro ViewStmt ni ALTER, no se redefine
    cuerpo = next(s for s in stmts if isinstance(s, ast.ViewStmt)).query
    leidos = set()

    def _rango(nodo):
        if nodo is None:
            return
        if isinstance(nodo, ast.RangeVar):
            leidos.add(nodo.relname)
            return
        if isinstance(nodo, (list, tuple)):
            for x in nodo:
                _rango(x)
            return
        for attr in getattr(nodo, "__slots__", ()):
            _rango(getattr(nodo, attr, None))

    _rango(cuerpo)
    assert "v_margen_producto" in leidos, "la vista nueva debe LEER a la madura"


# ---------------------------------------------------------------------------
# (b) Postgres real — fixture
# ---------------------------------------------------------------------------


def _run(conn) -> int:
    conn.execute(
        "INSERT INTO ingest_run (source, finished_at, ok) VALUES"
        " ('accounting_ledger_events', now(), true)"
    )
    return conn.execute("INSERT INTO ingest_run (source) VALUES ('t') RETURNING id").fetchone()[0]


def _ventas(
    conn,
    run,
    pid,
    *,
    dias,
    precio,
    costo,
    inicio=None,
    moneda="MXN",
    platform="amazon_mx",
):
    """`dias` fechas consecutivas con UNA venta de `precio` (order_id propio)
    y sku_cost `costo` vigente desde hace 200 dias. Sin cargos: el margen
    queda 100*(dias*precio - dias*costo)/(dias*precio)."""
    hoy = dt.date.today()
    conn.execute(
        "INSERT INTO sku_cost (product_id, cost_amount, cost_currency, includes_tax, valid_from)"
        " VALUES (%s, %s, %s, true, %s)",
        (pid, costo, moneda, hoy - dt.timedelta(days=200)),
    )
    for i in range(dias):
        conn.execute(
            "INSERT INTO ledger_event (platform, kind, event_date, order_id, product_id,"
            " quantity, amount, amount_currency, ingest_run_id)"
            " VALUES (%s, 'sale', %s, %s, %s, 1, %s, %s, %s)",
            (
                platform,
                (inicio or hoy - dt.timedelta(days=100)) - dt.timedelta(days=-i),
                f"o-b2-{pid}-{i}",
                pid,
                precio,
                moneda,
                run,
            ),
        )


def _fee_orden(conn, run, pid, fecha, monto, fee_type, moneda="MXN"):
    conn.execute(
        "INSERT INTO ledger_event (platform, kind, event_date, order_id, amount,"
        " amount_currency, fee_type, ingest_run_id)"
        " VALUES ('amazon_mx', 'fee', %s, %s, %s, %s, %s, %s)",
        (fecha, f"o-b2-{pid}-0", monto, moneda, fee_type, run),
    )


def _semilla(conn):
    """Cuatro productos amazon_mx:
    - maduro: 70 dias, precio 100, costo 50 -> margen 50.0 integro.
    - muestra: 5 dias, precio 100, costo 40 -> muestra_margen 60.0.
    - rota: 5 dias integridad rota (fee sin tipo en su orden) -> muestra NULL.
    - negativo: 40 dias, precio 100, costo 120 -> margen -20.0.
    - un_dia: 1 venta de 100, costo 40 -> muestra_venta 100, muestra_margen 60.0
      (dos listings del mismo producto)."""
    run = _run(conn)
    p = {}
    d40 = dt.date.today() - dt.timedelta(days=40)
    d50 = dt.date.today() - dt.timedelta(days=50)
    for clave, sku in (
        ("maduro", "SKU-MAD"),
        ("muestra", "SKU-MUE"),
        ("rota", "SKU-ROT"),
        ("neg", "SKU-NEG"),
        ("un_dia", "SKU-UNO"),
    ):
        pid, _ = _producto(
            conn, sku=sku, asin=f"B0B2{clave[:5].upper()}0001", seller_sku=f"S-{clave}"
        )
        p[clave] = pid
    _ventas(conn, run, p["maduro"], dias=70, precio=100, costo=50)
    _ventas(conn, run, p["muestra"], dias=5, precio=100, costo=40, inicio=d40)
    _ventas(conn, run, p["rota"], dias=5, precio=100, costo=40, inicio=d50)
    _fee_orden(conn, run, p["rota"], d50, -10, None)  # fee SIN tipo: fees_sin_tipo > 0
    _ventas(conn, run, p["neg"], dias=40, precio=100, costo=120)
    _ventas(conn, run, p["un_dia"], dias=1, precio=100, costo=40, inicio=d40)
    return p


# ---------------------------------------------------------------------------
# (b) Postgres real — pruebas
# ---------------------------------------------------------------------------


@_skip_db
def test_v_margen_producto_conserva_valores_previos_tras_0021():
    """Regla dura de B.2: snapshot de v_margen_producto sobre el fixture
    ANTES de 0021 (hasta 0019) == despues de aplicar 0021, fila por fila."""
    with db_economia(con_0021=False) as conn:
        p = _semilla(conn)
        cols = "product_id, dias_con_venta, margen_neto_pct, venta_total, cobertura, moneda"
        antes = conn.execute(f"SELECT {cols} FROM v_margen_producto ORDER BY product_id").fetchall()
        conn.execute(SQL21)
        despues = conn.execute(
            f"SELECT {cols} FROM v_margen_producto ORDER BY product_id"
        ).fetchall()
    assert antes == despues
    assert len(antes) == 5
    # El fixture si mide: el maduro trae margen (colateral de sanidad)
    por_pid = {r[0]: r for r in despues}
    assert por_pid[p["maduro"]][2] == Decimal("50")


@_skip_db
def test_muestra_limitada_solo_con_integridad_valida():
    """D4/0.4 §5: la muestra (1-29 dias) se publica SOLO con integridad OK.
    Con integridad rota (fee sin tipo) todo el bloque muestra va NULL."""
    from app.economia_observada import por_producto

    with db_economia() as conn:
        p = _semilla(conn)
        eco = por_producto(conn, "amazon_mx")
        buena = eco[("amazon_mx", p["muestra"])]
        rota = eco[("amazon_mx", p["rota"])]
        maduro = eco[("amazon_mx", p["maduro"])]
    # 5 dias limpios: muestra visible con sus numeros exactos (50% y 60%)
    assert buena.dias_con_venta == 5 and buena.integridad_ok is True
    assert buena.muestra_limitada is True
    assert buena.muestra_venta == Decimal("500")
    assert buena.muestra_margen_neto_pct == Decimal("60")
    assert buena.margen_neto_pct is None  # < 30 dias: el maduro sigue NULL
    # 5 dias con fee sin tipo: integridad rota -> muestra NULL, jamas 0
    assert rota.integridad_ok is False and rota.muestra_limitada is False
    assert rota.muestra_venta is None and rota.muestra_margen_neto_pct is None
    # 70 dias integro: muestra_limitada False (fuera de 1-29) y maduro presente
    assert maduro.muestra_limitada is False
    assert maduro.muestra_venta is None and maduro.muestra_margen_neto_pct is None
    assert maduro.margen_neto_pct == Decimal("50")


@_skip_db
def test_dos_listings_del_mismo_producto_comparten_grano_sin_duplicar():
    """0.4 §5 / fixture "Mismo producto": dos ASIN del mismo producto ven la
    MISMA economia; el total financiero del producto es 100, no 200."""
    from app.economia_observada import por_listing, por_producto

    with db_economia() as conn:
        pid, lid1 = _producto(conn, sku="SKU-DUP", asin="B0DUPA0001", seller_sku="SD-A")
        lid2 = conn.execute(
            "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
            " VALUES (%s, 'amazon_mx', 'B0DUPB0002', 'SD-B') RETURNING id",
            (pid,),
        ).fetchone()[0]
        run = _run(conn)
        _ventas(
            conn,
            run,
            pid,
            dias=1,
            precio=100,
            costo=40,
            inicio=dt.date.today() - dt.timedelta(days=40),
        )
        por_l = por_listing(conn, "amazon_mx")
        por_p = por_producto(conn, "amazon_mx")
    a, b = por_l[lid1], por_l[lid2]
    assert a is not None and b is not None
    assert a.muestra_venta == b.muestra_venta == Decimal("100")
    assert a.muestra_margen_neto_pct == b.muestra_margen_neto_pct == Decimal("60")
    # El total del PRODUCTO (grano compartido) es 100, no la suma por listing
    assert sum(e.muestra_venta for e in por_p.values() if e.muestra_venta is not None) == Decimal(
        "100"
    )
    assert ("amazon_mx", pid) in por_p


@_skip_db
def test_producto_sin_ventas_es_none_no_cero():
    """Regla 3: sin ventas en la ventana no hay fila en la vista; el
    consumidor la reporta con todo en None, jamas 0."""
    from app.economia_observada import por_listing, por_producto

    with db_economia() as conn:
        pid, lid = _producto(conn, sku="SKU-VAC", asin="B0VACIO0001", seller_sku="SV")
        por_l = por_listing(conn, "amazon_mx")
        por_p = por_producto(conn, "amazon_mx")
    eco = por_l[lid]
    assert eco.product_id == pid
    assert eco.venta_total is None and eco.margen_neto_pct is None
    assert eco.dias_con_venta is None and eco.muestra_venta is None
    assert eco.muestra_limitada is None and eco.integridad_ok is None
    assert eco.moneda is None
    assert ("amazon_mx", pid) not in por_p  # la vista no inventa fila


@_skip_db
def test_margen_negativo_se_conserva():
    """Regla 0.4 §8 (Margen vs objetivo): el negativo se MUESTRA, no se
    recorta a 0 — en la proyeccion madura y en la muestra."""
    from app.economia_observada import por_producto

    with db_economia() as conn:
        p = _semilla(conn)
        eco = por_producto(conn, "amazon_mx")
    assert eco[("amazon_mx", p["neg"])].margen_neto_pct == Decimal("-20")
    # Colateral de la misma regla en muestra: un producto de 5 dias con costo
    # por encima del precio tampoco se recorta
    with db_economia() as conn:
        pid, _ = _producto(conn, sku="SKU-NEGM", asin="B0NEGMO0001", seller_sku="SNM")
        run = _run(conn)
        _ventas(
            conn,
            run,
            pid,
            dias=5,
            precio=100,
            costo=150,
            inicio=dt.date.today() - dt.timedelta(days=40),
        )
        eco_m = por_producto(conn, "amazon_mx")
    assert eco_m[("amazon_mx", pid)].muestra_limitada is True
    assert eco_m[("amazon_mx", pid)].muestra_margen_neto_pct == Decimal("-50")
