# ruff: noqa
"""Conciliacion de solo lectura: Orbit (Postgres desechable) vs accounting.

Uso: python conciliar.py <snapshot.db> <dsn_postgres_desechable>
"""

from __future__ import annotations

import datetime as dt
import sqlite3
import sys
from decimal import Decimal

import psycopg

RUTA = sys.argv[1]
DSN = sys.argv[2]

con = sqlite3.connect(f"file:{RUTA}?mode=ro", uri=True)
con.row_factory = sqlite3.Row
origen = {
    (row["campaign_id"], row["date"]): (
        Decimal(str(row["spend"] if row["spend"] is not None else "0")).quantize(
            Decimal("0.0001")
        ),
        Decimal(str(row["ad_sales"] if row["ad_sales"] is not None else "0")).quantize(
            Decimal("0.0001")
        ),
        row["impressions"],
        row["clicks"],
    )
    for row in con.execute(
        "SELECT campaign_id, date, spend, ad_sales, impressions, clicks"
        " FROM meli_ads_campaigns_daily"
    )
}
dias = {
    row["date"]: (row["clicks"], row["impressions"], row["spend"], row["ad_sales"])
    for row in con.execute(
        "SELECT date, clicks, impressions, spend, ad_sales FROM meli_ads_metrics_daily"
    )
}
con.close()

with psycopg.connect(DSN) as conn:
    filas = conn.execute(
        "SELECT e.external_id, l.metric_date, l.cost, l.ad_revenue, l.impressions,"
        " l.clicks, l.metric_currency"
        " FROM v_metric_latest l JOIN ad_entity e ON e.id = l.ad_entity_id"
        " WHERE e.platform = 'meli' AND e.kind = 'campaign'"
    ).fetchall()

orbit = {}
for ext, fecha, cost, revenue, impressions, clicks, moneda in filas:
    assert moneda == "MXN", moneda
    orbit[(ext, fecha.isoformat())] = (cost, revenue, impressions, clicks)

print(f"filas origen: {len(origen)}")
print(f"filas Orbit:  {len(orbit)}")
print(f"faltantes en Orbit: {sorted(set(origen) - set(orbit))[:10]}")
print(f"sobrantes en Orbit: {sorted(set(orbit) - set(origen))[:10]}")
diferencias = sum(1 for clave in set(origen) & set(orbit) if origen[clave] != orbit[clave])
for clave in sorted(set(origen) & set(orbit)):
    if origen[clave] != orbit[clave]:
        print(f"DIFIERE {clave}: origen={origen[clave]} orbit={orbit[clave]}")
print(f"campana/dia comparables: {len(set(origen) & set(orbit))}, diferencias: {diferencias}")

sumas: dict[str, list] = {}
for (_ext, fecha), (cost, revenue, impressions, clicks) in orbit.items():
    acc = sumas.setdefault(fecha, [0, 0, Decimal(0), Decimal(0)])
    acc[0] += clicks or 0
    acc[1] += impressions or 0
    acc[2] += cost or Decimal(0)
    acc[3] += revenue or Decimal(0)

dias_diferentes = 0
for fecha, (clicks, impressions, spend, ad_sales) in sorted(dias.items()):
    if fecha not in sumas:
        continue
    orbit_dia = sumas[fecha]
    if (
        orbit_dia[0] != clicks
        or orbit_dia[1] != impressions
        or orbit_dia[2] != Decimal(str(spend)).quantize(Decimal("0.0001"))
        or orbit_dia[3] != Decimal(str(ad_sales)).quantize(Decimal("0.0001"))
    ):
        dias_diferentes += 1
        print(
            f"DIA DIFIERE {fecha}: origen={(clicks, impressions, spend, ad_sales)}"
            f" orbit={tuple(str(v) for v in orbit_dia)}"
        )
print(f"dias comparables: {len(sumas)}, diferencias: {dias_diferentes}")
print(f"fecha de conciliacion: {dt.datetime.now(dt.UTC).isoformat()}")
