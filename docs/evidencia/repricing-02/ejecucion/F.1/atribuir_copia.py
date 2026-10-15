#!/usr/bin/env python3
"""Atribucion en seco sobre una copia de produccion (REPRICING 02 F.1).

Lee cada venta de Amazon de `ledger_event`, la pasa por
`AtribuidorAmazon.atribuir` y cuenta por resultado. No escribe atribuciones:
es un conteo. Para las no atribuidas clasifica el motivo con los mismos
metodos del atribuidor. Uso: `PYTHONPATH=<repo> python atribuir_copia.py
<dsn> [--reintento]`; con `--reintento` borra y restaura la observacion de
una orden `canal_orden` para probar que lo indecidible se reintenta.
"""

import sys
from collections import Counter
from types import SimpleNamespace

import psycopg

from app.ledger_atribucion import AtribuidorAmazon

CANAL = {"fba": "canal_orden", "fbm": "canal_orden"}


def _ventas(conn):
    return conn.execute(
        "SELECT platform, kind, order_id, product_id FROM ledger_event"
        " WHERE kind = 'sale' AND platform IN ('amazon_mx', 'amazon_us')"
        " ORDER BY id"
    ).fetchall()


def _motivo(atr, db, plataforma, product_id, order_id):
    if product_id is None:
        return "sin_producto"
    candidatos = atr._candidatos(db, plataforma, product_id)
    if not candidatos:
        return "sin_listing"
    if len(candidatos) == 1:
        return "inattribuible?"  # no pasa: un candidato siempre atribuye
    if not order_id:
        return "sin_orden"
    canal = atr._canal_de_orden(db, plataforma, order_id)
    if canal is None:
        return "sin_canal"
    empatan = sum(1 for _, sku in candidatos if atr._canal_de_listing(db, plataforma, sku) == canal)
    return "empate" if empatan != 1 else "inattribuible?"


def contar(conn):
    atr = AtribuidorAmazon(conn)
    conteo: Counter = Counter()
    motivos: Counter = Counter()
    total = 0
    for plataforma, kind, order_id, product_id in _ventas(conn):
        total += 1
        fila = SimpleNamespace(
            platform=plataforma, kind=kind, order_id=order_id, product_id=product_id
        )
        atrib = atr.atribuir(fila)
        if atrib is None:
            conteo["sin_atribuir"] += 1
            motivos[_motivo(atr, conn, plataforma, product_id, order_id)] += 1
        else:
            conteo[atrib.resuelto_por] += 1
    return total, conteo, motivos


def reintento(conn):
    """Borra la observacion de una orden `canal_orden`, re-corre (debe dar
    None) y la restaura (debe volver a atribuir). Solo usa datos reales."""
    atr = AtribuidorAmazon(conn)
    objetivo = None
    for plataforma, kind, order_id, product_id in _ventas(conn):
        fila = SimpleNamespace(
            platform=plataforma, kind=kind, order_id=order_id, product_id=product_id
        )
        if (a := atr.atribuir(fila)) is not None and a.resuelto_por == "canal_orden":
            objetivo = (plataforma, order_id, product_id, a.listing_id)
            break
    assert objetivo is not None, "sin ventas canal_orden en la copia"
    plataforma, order_id, product_id, lid = objetivo
    obs = conn.execute(
        "SELECT marketplace_id, last_updated_time, fulfillment_channel FROM"
        " spapi_order_observation WHERE platform = %s AND amazon_order_id = %s",
        (plataforma, order_id),
    ).fetchall()
    assert obs, "la orden objetivo no trae observacion"
    fila = SimpleNamespace(
        platform=plataforma, kind="sale", order_id=order_id, product_id=product_id
    )
    conn.execute("SET session_replication_role = replica")
    conn.execute(
        "DELETE FROM spapi_order_observation WHERE platform = %s AND amazon_order_id = %s",
        (plataforma, order_id),
    )
    conn.execute("SET session_replication_role = origin")
    sin_dato = AtribuidorAmazon(conn).atribuir(fila)
    for marketplace, actualizado, canal in obs:
        conn.execute(
            "INSERT INTO spapi_order_observation (amazon_order_id, platform,"
            " marketplace_id, last_updated_time, fulfillment_channel, observed_at)"
            " VALUES (%s, %s, %s, %s, %s, now())",
            (order_id, plataforma, marketplace, actualizado, canal),
        )
    con_dato = AtribuidorAmazon(conn).atribuir(fila)
    print(f"orden=<redactada> listing={lid} sin_dato={sin_dato} con_dato={con_dato}")
    assert sin_dato is None, "sin observacion debio dar None"
    assert (
        con_dato is not None
        and con_dato.listing_id == lid
        and con_dato.resuelto_por == "canal_orden"
    ), "con el dato debio atribuir igual"
    print("reintento: OK")


def main() -> int:
    conn = psycopg.connect(sys.argv[1], autocommit=True)
    conn.execute("SET TIME ZONE 'UTC'")
    total, conteo, motivos = contar(conn)
    print(f"ventas={total}")
    for clave in ("sku", "canal_orden", "sin_atribuir"):
        print(f"{clave}={conteo[clave]}")
    for motivo, n in sorted(motivos.items()):
        print(f"motivo {motivo}={n}")
    if len(sys.argv) > 2 and sys.argv[2] == "--reintento":
        reintento(conn)
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
