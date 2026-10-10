#!/usr/bin/env python3
"""P.1: el contrato da los mismos numeros que la consulta de control.

Sobre la copia local de prod (`orbit_copia_p1`, solo lectura en la
practica: este script solo hace SELECT), corre `lee_dinero` con
`--hasta 2026-10-04` y `dias=90` para MX y US, y compara por tipo el
gasto, los pedidos y la venta contra la consulta de control de la guia
P.1 (tablas base, literal). La fila con tipo vacio es el total. Si
`sin_dato` > 0, el contrato da None en esa suma. Un tipo fuera de los
cinco seria una hoja sin clasificar.

Uso: desde la raiz del repo,
  ORBIT_COPIA_DSN=postgresql://orbit:orbit@127.0.0.1:5433/orbit_copia_p1 \
    .venv/bin/python docs/evidencia/bids-02/ejecucion/P.1/numeros.py
Sale 0 con "NUMEROS OK" si todo coincide, 1 con el diff si no.
"""

from __future__ import annotations

import datetime as dt
import os
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[5]))

import psycopg

from app.pantalla_dinero import TIPOS, lee_dinero

HASTA = dt.date(2026, 10, 4)
DIAS = 90
PLATAFORMAS = ("amazon_mx", "amazon_us")

CONTROL = """
WITH hojas AS (
    SELECT k.id, CASE WHEN sc.targeting_type = 'AUTO' THEN 'automatica'
                      WHEN k.kind = 'product_target' THEN 'product_targeting'
                      ELSE lower(k.match_type) END AS tipo
      FROM ad_entity k JOIN ad_entity ag ON ag.id = k.parent_id
      JOIN ad_entity c ON c.id = ag.parent_id
      JOIN ad_entity_state sk ON sk.ad_entity_id = k.id AND sk.status = 'ENABLED'
      JOIN ad_entity_state sg ON sg.ad_entity_id = ag.id AND sg.status = 'ENABLED'
      JOIN ad_entity_state sc ON sc.ad_entity_id = c.id AND sc.status = 'ENABLED'
     WHERE k.platform = %s AND k.kind IN ('keyword', 'product_target')
), ultima AS (
    SELECT DISTINCT ON (m.ad_entity_id, m.metric_date)
           m.ad_entity_id, m.cost, m.orders, m.ad_revenue
      FROM ads_metric_observation m JOIN hojas h ON h.id = m.ad_entity_id
     WHERE m.metric_date BETWEEN %s AND %s
     ORDER BY m.ad_entity_id, m.metric_date, m.observed_at DESC)
    SELECT h.tipo, sum(u.cost) AS gasto, sum(u.orders) AS pedidos, sum(u.ad_revenue) AS venta,
           count(*) - count(u.cost) AS sin_dato_cost,
           count(*) - count(u.orders) AS sin_dato_pedidos,
           count(*) - count(u.ad_revenue) AS sin_dato_venta
      FROM hojas h JOIN ultima u ON u.ad_entity_id = h.id GROUP BY ROLLUP (h.tipo) ORDER BY 1
"""


def _norm(valor):
    if valor is None:
        return None
    if isinstance(valor, Decimal):
        return valor
    return valor


def compara(plataforma: str, conn) -> list[str]:
    diffs: list[str] = []
    pantalla = lee_dinero(conn, plataforma=plataforma, dias=DIAS, hasta=HASTA)  # type: ignore[arg-type]
    control = {
        fila[0] or "": fila[1:]
        for fila in conn.execute(CONTROL, (plataforma, pantalla.desde, pantalla.hasta)).fetchall()
    }
    print(f"== {plataforma} {pantalla.desde}..{pantalla.hasta} moneda={pantalla.moneda}")
    print(f"   target={pantalla.target_acos_pct} sin_clasificar={pantalla.hojas_sin_clasificar}")
    filas = {f.tipo: f for f in pantalla.filas}
    filas[""] = pantalla.total
    for tipo in (*TIPOS, ""):
        fila = filas[tipo]
        ctrl = control.get(tipo)
        nombre = tipo or "TOTAL"
        if ctrl is None:
            if fila.gasto is not None or fila.pedidos is not None or fila.venta is not None:
                diffs.append(f"{plataforma} {nombre}: control sin fila pero contrato {fila}")
            print(f"   {nombre:>18}: contrato vacio, control vacio")
            continue
        gasto_c, pedidos_c, venta_c, sin_c, sin_p, sin_v = ctrl
        esperan_none = (
            (fila.gasto is None) == (gasto_c is None or sin_c > 0),
            (fila.pedidos is None) == (pedidos_c is None or sin_p > 0),
            (fila.venta is None) == (venta_c is None or sin_v > 0),
        )
        iguales = (
            _norm(fila.gasto) == _norm(gasto_c) if fila.gasto is not None else esperan_none[0],
            _norm(fila.pedidos) == _norm(pedidos_c)
            if fila.pedidos is not None
            else esperan_none[1],
            _norm(fila.venta) == _norm(venta_c) if fila.venta is not None else esperan_none[2],
        )
        marca = "ok" if all(iguales) else "DIFF"
        print(
            f"   {nombre:>18}: gasto {fila.gasto} vs {gasto_c} | pedidos {fila.pedidos} vs"
            f" {pedidos_c} | venta {fila.venta} vs {venta_c} | sin_dato {sin_c}/{sin_p}/{sin_v}"
            f" [{marca}]"
        )
        if not all(iguales):
            diffs.append(f"{plataforma} {nombre}: contrato {fila} vs control {ctrl}")
    for tipo in control:
        if tipo not in filas and tipo != "":
            diffs.append(f"{plataforma}: control trae tipo fuera de vocabulario: {tipo!r}")
    return diffs


def main() -> int:
    dsn = os.environ.get(
        "ORBIT_COPIA_DSN", "postgresql://orbit:orbit@127.0.0.1:5433/orbit_copia_p1"
    )
    diffs: list[str] = []
    with psycopg.connect(dsn) as conn:
        conn.execute("SET TIME ZONE 'UTC'")
        for plataforma in PLATAFORMAS:
            diffs.extend(compara(plataforma, conn))
    if diffs:
        print("DIFFS:")
        for diff in diffs:
            print(" -", diff)
        return 1
    print("NUMEROS OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
