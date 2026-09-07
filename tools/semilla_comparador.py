"""Siembra local para el comparador UI de la fabrica (ORBIT 19 B.5).

Crea una DB temporal (por defecto `orbit_b5_comparador`) con las migraciones
minimas (mismo orden que tests/test_evaluacion_catalogo.py) y datos que
reproducen EXACTAMENTE los casos que el lead debe ver en navegador:

- AC6: dos publicaciones con ACoS 25% e IGUALDAD al objetivo 25 (cuenta
  Dentro), muestras 1 fecha/1 compra vs 10 fechas/100 compras; ausencia
  (Sin datos) vs cero observado (Gasto sin ventas, ACoS null).
- AC8: stock cero observado (FBA 0), positivo con frescura VIEJA y
  desconocido (sin filas); Featured Offer siempre Sin verificar.
- AC10: orden por las 8 metricas y filtros sin perder la seleccion.
- 0.4 seccion 8: gasto sin ventas; margen negativo seleccionable con motivo;
  dos listings del mismo producto compartiendo economia; muestra limitada
  visible aparte (D4); objetivos distintos por campana => ACoS sin etiqueta;
  evidencia inmadura => provisional.

Solo escribe en la DB local de pruebas; NUNCA toca Amazon ni produccion.
Re-ejecutable: borra y recrea la DB.

Uso:
  PYTHONPATH=. .venv/bin/python tools/semilla_comparador.py \
      --dsn postgresql://orbit:orbit@localhost:5432/postgres
  ORBIT_DSN_READ=postgresql://...orbit_b5_comparador... uvicorn app.main:app --port 8765
  abrir http://localhost:8765/campanas/nuevas#fabrica-comparador
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg import sql

RAIZ = Path(__file__).resolve().parents[1]
# Mismo orden minimo que tests/test_evaluacion_catalogo.py (_ORDEN_DB).
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
    "0019_fabrica_grupo_publicacion_v2.sql",
    "0020_ads_producto_metrica.sql",
    "0021_economia_observada.sql",
    "0022_disponibilidad_snapshot.sql",
)
UTC = dt.UTC


def producto(conn, sku: str, asin: str, seller_sku: str, pid: int | None = None) -> tuple[int, int]:
    """Crea (o reutiliza) el producto y su listing; reutilizar el producto es
    el caso 'dos listings del mismo producto' (0.4 §8)."""
    if pid is None:
        pid = conn.execute(
            "INSERT INTO product (odoo_sku, name) VALUES (%s, %s) RETURNING id", (sku, sku)
        ).fetchone()[0]
    lid = conn.execute(
        "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
        " VALUES (%s, 'amazon_mx', %s, %s) RETURNING id",
        (pid, asin, seller_sku),
    ).fetchone()[0]
    return pid, lid


def ledger_producto(conn, pid: int, *, dias: int, precio: Decimal, costo: Decimal, desfase: int):
    """`dias` ventas consecutivas de `precio` con costo `costo` + 7 cargos sin
    orden -100 (prorrateo) + 1 cargo ads excluido. Mismo patrón verificado de
    tests/test_fabrica_migracion.py::_ledger_producto; `desfase` evita la
    colision del indice dedupe de cargos entre productos."""
    hoy = dt.datetime.now(UTC).date()
    conn.execute("INSERT INTO ingest_run (source, finished_at, ok) VALUES ('t', now(), true)")
    run = conn.execute("INSERT INTO ingest_run (source) VALUES ('t') RETURNING id").fetchone()[0]
    conn.execute(
        "INSERT INTO sku_cost (product_id, cost_amount, cost_currency, includes_tax, valid_from)"
        " VALUES (%s, %s, 'MXN', true, %s)",
        (pid, costo, hoy - dt.timedelta(days=200)),
    )
    for i in range(dias):
        conn.execute(
            "INSERT INTO ledger_event (platform, kind, event_date, order_id, product_id,"
            " quantity, amount, amount_currency, ingest_run_id)"
            " VALUES ('amazon_mx', 'sale', %s, %s, %s, 1, %s, 'MXN', %s)",
            (hoy - dt.timedelta(days=100 - i), f"b5-{pid}-{i}", pid, precio, run),
        )
    for i in range(7):
        conn.execute(
            "INSERT INTO ledger_event (platform, kind, event_date, amount, amount_currency,"
            " fee_type, ingest_run_id) VALUES ('amazon_mx', 'fee', %s, -100, 'MXN', 'closing', %s)",
            (hoy - dt.timedelta(days=90 - i - desfase), run),
        )
    conn.execute(
        "INSERT INTO ledger_event (platform, kind, event_date, amount, amount_currency,"
        " fee_type, ingest_run_id) VALUES ('amazon_mx', 'fee', %s, -9999, 'MXN', 'ads', %s)",
        (hoy - dt.timedelta(days=50 - desfase), run),
    )


def fila_ads(
    conn,
    run: int,
    asin: str,
    seller_sku: str,
    fecha: dt.date,
    observed: dt.datetime,
    cost: Decimal,
    sales: Decimal,
    clicks: int,
    compras: Decimal,
):
    conn.execute(
        "INSERT INTO ads_product_metric_observation (platform, advertised_asin, advertised_sku,"
        " metric_date, observed_at, metric_currency, clicks, cost, purchases30d, sales30d,"
        " ingest_run_id) VALUES ('amazon_mx', %s, %s, %s, %s, 'MXN', %s, %s, %s, %s, %s)",
        (asin, seller_sku, fecha, observed, clicks, cost, compras, sales, run),
    )


def grupo_planeado(conn, lote: str, listing_id: int, seller_sku: str, target: Decimal | None):
    conn.execute(
        "INSERT INTO fabrica_lote (lote, platform, tipo_producto, nombre_base, go_literal,"
        " huella, plan, modo_goal, estado) VALUES (%s, 'amazon_mx', 'gorras', 'B5', 'GO', %s,"
        " '{}'::jsonb, 'shadow', 'planeado')",
        (lote, "0" * 64),
    )
    grupo = conn.execute(
        "INSERT INTO campana_grupo (platform, tipo_producto, nombre_base, lote, target_acos_pct,"
        " target_procedencia, go_literal, target_origen) VALUES ('amazon_mx', 'gorras', 'B5', %s,"
        " %s, 'manual', 'GO', 'manual_lanzamiento') RETURNING id",
        (lote, target),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO campana_grupo_producto (grupo_id, product_id, listing_id, seller_sku)"
        " SELECT %s, product_id, id, seller_sku FROM listing WHERE id = %s",
        (grupo, listing_id),
    )


def disponibilidad(conn, seller_sku: str, quantity: int | None, fetched_at: dt.datetime):
    hoy = dt.datetime.now(UTC).date()
    conn.execute(
        "INSERT INTO disponibilidad_observation (platform, seller_sku, metric_date, fuente,"
        " quantity, fetched_at, observed_at)"
        " VALUES ('amazon_mx', %s, %s, 'fba', %s, %s, %s)",
        (seller_sku, hoy, quantity, fetched_at, dt.datetime.now(UTC)),
    )


def sembrar(conn) -> dict:
    hoy = dt.datetime.now(UTC).date()
    hasta = hoy - dt.timedelta(days=1)
    desde = hasta - dt.timedelta(days=30)
    observado = dt.datetime.combine(hoy, dt.time.min, UTC)  # observed_at fresco
    resumen: dict[str, str] = {}

    # P1: dos listings del MISMO producto (0.4 §8: economia compartida).
    # L1 en dos grupos con targets distintos => objetivo None => ACoS SIN
    # etiqueta (distintos objetivos de campana). Ads provisional (fecha D-1).
    p1, l1 = producto(conn, "GORRA-DOTACION", "B0AAAAAA01", "SKU-CMP-A")
    _, l2 = producto(conn, "GORRA-DOTACION", "B0AAAAAA02", "SKU-CMP-A2", pid=p1)
    ledger_producto(conn, p1, dias=70, precio=Decimal("100"), costo=Decimal("50"), desfase=0)
    run = conn.execute(
        "INSERT INTO ingest_run (source) VALUES ('b5-semilla') RETURNING id"
    ).fetchone()[0]
    fila_ads(
        conn,
        run,
        "B0AAAAAA01",
        "SKU-CMP-A",
        hasta,
        observado,
        Decimal("40"),
        Decimal("80"),
        200,
        Decimal("8"),
    )  # ACoS 50, provisional
    grupo_planeado(conn, "b5-grupo-25a", l1, "SKU-CMP-A", Decimal("25"))
    grupo_planeado(conn, "b5-grupo-30", l1, "SKU-CMP-A", Decimal("30"))
    resumen["L1"] = (
        "ACoS 50% SIN etiqueta (dos grupos, targets 25 y 30); "
        "provisional; economia compartida con L2"
    )

    # L2: ASIN ausente del gzip => Sin datos (ausencia, no 0).
    resumen["L2"] = "Sin datos (ausencia de Ads); mismo producto que L1"

    # P2: AC6 igual ACoS 25% con muestras 1 y 10; objetivo 25 => igualdad = Dentro.
    p2, l3 = producto(conn, "GORRA-LANZAMIENTO", "B0BBBBBB01", "SKU-CMP-B")
    _, l4 = producto(conn, "GORRA-LANZAMIENTO", "B0BBBBBB02", "SKU-CMP-B2", pid=p2)
    ledger_producto(conn, p2, dias=70, precio=Decimal("100"), costo=Decimal("50"), desfase=10)
    fila_ads(
        conn,
        run,
        "B0BBBBBB01",
        "SKU-CMP-B",
        desde,
        observado,
        Decimal("2.5"),
        Decimal("10"),
        10,
        Decimal("1"),
    )  # muestra 1, maduro
    for i in range(10):  # muestra 10, 100 compras, maduro
        fila_ads(
            conn,
            run,
            "B0BBBBBB02",
            "SKU-CMP-B2",
            desde + dt.timedelta(days=i),
            observado,
            Decimal("25"),
            Decimal("100"),
            40,
            Decimal("10"),
        )
    grupo_planeado(conn, "b5-grupo-25b", l3, "SKU-CMP-B", Decimal("25"))
    grupo_planeado(conn, "b5-grupo-25b2", l4, "SKU-CMP-B2", Decimal("25"))
    resumen["L3"] = "ACoS 25% = objetivo 25% => Dentro del objetivo; muestra 1 fecha / 1 compra"
    resumen["L4"] = (
        "ACoS 25% = objetivo 25% => Dentro del objetivo; muestra 10 fechas / 100 compras"
    )

    # P3: gasto sin ventas (cero observado) + margen NEGATIVO seleccionable.
    p3, l5 = producto(conn, "GORRA-QUEMADA", "B0CCCCCC01", "SKU-CMP-C")
    ledger_producto(conn, p3, dias=70, precio=Decimal("100"), costo=Decimal("130"), desfase=20)
    fila_ads(
        conn,
        run,
        "B0CCCCCC01",
        "SKU-CMP-C",
        desde,
        observado,
        Decimal("10"),
        Decimal("0"),
        50,
        Decimal("0"),
    )
    grupo_planeado(conn, "b5-grupo-25c", l5, "SKU-CMP-C", Decimal("25"))
    resumen["L5"] = "Gasto sin ventas (sales30d=0 OBSERVADO, ACoS null); margen negativo con motivo"

    # P4: muestra limitada de margen (12 dias), visible aparte (D4).
    p4, l6 = producto(conn, "GORRA-NUEVA", "B0DDDDDD01", "SKU-CMP-D")
    ledger_producto(conn, p4, dias=12, precio=Decimal("100"), costo=Decimal("80"), desfase=30)
    fila_ads(
        conn,
        run,
        "B0DDDDDD01",
        "SKU-CMP-D",
        desde,
        observado,
        Decimal("30"),
        Decimal("100"),
        60,
        Decimal("3"),
    )  # ACoS 30 > 25 => Por encima
    grupo_planeado(conn, "b5-grupo-25d", l6, "SKU-CMP-D", Decimal("25"))
    resumen["L6"] = (
        "Muestra limitada de margen (12 dias) aparte; ACoS 30% => Por encima del objetivo"
    )

    # AC8: cero observado (L4), positivo VIEJO (L3), desconocido (L1/L5/L6 sin filas).
    disponibilidad(conn, "SKU-CMP-B2", 0, dt.datetime.now(UTC))  # cero OBSERVADO
    disponibilidad(conn, "SKU-CMP-B", 12, dt.datetime.now(UTC) - dt.timedelta(days=30))  # viejo
    disponibilidad(conn, "SKU-CMP-A", 5, dt.datetime.now(UTC))  # positivo fresco
    resumen["disponibilidad"] = (
        "L4 stock 0 OBSERVADO; L3 positivo con frescura vieja (30d);"
        " L1 positivo fresco; L2/L5/L6 desconocido;"
        " Featured Offer Sin verificar"
    )
    return resumen


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dsn",
        default="postgresql://orbit:orbit@localhost:5432/postgres",
        help="DSN administrativo de Postgres local (default: %(default)s)",
    )
    parser.add_argument("--dbname", default="orbit_b5_comparador")
    args = parser.parse_args()

    admin = psycopg.connect(args.dsn, autocommit=True)
    try:
        admin.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(args.dbname))
        )
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(args.dbname)))
        conn = psycopg.connect(args.dsn, dbname=args.dbname, autocommit=True)
        try:
            conn.execute("SET TIME ZONE 'UTC'")
            for nombre in ORDEN:
                conn.execute((RAIZ / "migrations" / nombre).read_text(encoding="utf-8"))
            resumen = sembrar(conn)
        finally:
            conn.close()
    finally:
        admin.close()

    print(f"DB lista: {args.dbname}")
    for clave, texto in resumen.items():
        print(f"  {clave}: {texto}")
    print(
        "\nLevantar la app:\n"
        f"  ORBIT_DSN_READ=postgresql://orbit:orbit@localhost:5432/{args.dbname} \\\n"
        "    PYTHONPATH=. .venv/bin/python -m uvicorn app.main:app --port 8765\n"
        "  abrir http://localhost:8765/campanas/nuevas#fabrica-comparador"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
