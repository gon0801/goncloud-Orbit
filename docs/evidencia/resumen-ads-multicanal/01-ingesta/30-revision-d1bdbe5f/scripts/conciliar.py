# ruff: noqa
"""Conciliacion de solo lectura: Orbit (Postgres desechable) vs accounting.

Uso: python conciliar.py <snapshot.db> <dsn_postgres_desechable>
Sale 1 si hay cualquier diferencia o si un NULL se convirtio en cero.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
import sys
from decimal import Decimal

import psycopg

RUTA = sys.argv[1]
DSN = sys.argv[2]
CENTIMOS = Decimal("0.0001")


def _dinero(valor):
    return None if valor is None else Decimal(str(valor)).quantize(CENTIMOS)


con = sqlite3.connect(f"file:{RUTA}?mode=ro", uri=True)
con.row_factory = sqlite3.Row
origen = {
    (row["campaign_id"], row["date"]): (
        _dinero(row["spend"]),
        _dinero(row["ad_sales"]),
        row["impressions"],
        row["clicks"],
    )
    for row in con.execute(
        "SELECT campaign_id, date, spend, ad_sales, impressions, clicks"
        " FROM meli_ads_campaigns_daily"
    )
}
dias = {
    row["date"]: (
        row["clicks"],
        row["impressions"],
        _dinero(row["spend"]),
        _dinero(row["ad_sales"]),
    )
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

sumas: dict[str, dict] = {}
for (_ext, fecha), (cost, revenue, impressions, clicks) in orbit.items():
    dia = sumas.setdefault(
        fecha,
        {
            "clicks": 0,
            "clicks_null": False,
            "impressions": 0,
            "impressions_null": False,
            "cost": Decimal(0),
            "cost_null": False,
            "revenue": Decimal(0),
            "revenue_null": False,
        },
    )
    if clicks is None:
        dia["clicks_null"] = True
    else:
        dia["clicks"] += clicks
    if impressions is None:
        dia["impressions_null"] = True
    else:
        dia["impressions"] += impressions
    if cost is None:
        dia["cost_null"] = True
    else:
        dia["cost"] += cost
    if revenue is None:
        dia["revenue_null"] = True
    else:
        dia["revenue"] += revenue

dias_diferentes = 0
for fecha, (clicks, impressions, spend, ad_sales) in sorted(dias.items()):
    if fecha not in sumas:
        continue
    dia = sumas[fecha]
    orbit_dia = (
        None if dia["clicks_null"] else dia["clicks"],
        None if dia["impressions_null"] else dia["impressions"],
        None if dia["cost_null"] else dia["cost"],
        None if dia["revenue_null"] else dia["revenue"],
    )
    if (clicks, impressions, spend, ad_sales) != orbit_dia:
        dias_diferentes += 1
        print(
            f"DIA DIFIERE {fecha}: origen={(clicks, impressions, spend, ad_sales)}"
            f" orbit={orbit_dia}"
        )
print(f"dias comparables: {len(sumas)}, diferencias: {dias_diferentes}")
print(f"fecha de conciliacion: {dt.datetime.now(dt.UTC).isoformat()}")
if diferencias or dias_diferentes:
    print("CONCILIACION FALLA")
    raise SystemExit(1)
print("CONCILIACION OK")
