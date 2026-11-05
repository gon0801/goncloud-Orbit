"""Lectura acotada de las fuentes externas: ejecutar por stdin en goncloud."""

import json
import sqlite3
from datetime import UTC, datetime

PARES = [
    ("MLM2163407464", "NH-CAR-BLA-CEN-DOR"),
    ("MLM1890653227", "NH-CAR-BLA-CEN-PLA"),
    ("MLM4735255462", "NH-BLA-BRO-CEN-DOR"),
    ("MLM2724304859", "NH-ITA-CEN-PLA"),
    ("MLM2727437671", "NH-GAM-AZU-CEN-PLA"),
    ("MLM2727363007", "NH-GAM-NEG-CEN-DOR"),
    ("MLM2727328203", "NH-GAM-AZU-CEN-DOR"),
    ("MLM4749600210", "NH-GAM-NEG-CEN-PLA"),
    ("MLM4763716652", "NH-PLA-CH-CEN-PLA"),
]


def abrir(nombre):
    conn = sqlite3.connect(f"file:/mnt/data/appdata/{nombre}/data/{nombre}.db?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("BEGIN")
    return conn


def filas(conn, sql, args):
    return [dict(x) for x in conn.execute(sql, args)]


b = abrir("bridge")
a = abrir("accounting")
salida = []
for item, sku in PARES:
    cache = filas(
        b,
        "SELECT item_id,variation_id,sku,qty,status,updated_at,raw_json "
        "FROM meli_listings_cache WHERE item_id=?",
        (item,),
    )
    for r in cache:
        raw = json.loads(r.pop("raw_json") or "{}")
        r["detalle_fuente"] = {
            k: raw.get(k)
            for k in [
                "status",
                "sub_status",
                "available_quantity",
                "catalog_listing",
                "catalog_product_id",
                "last_updated",
                "date_created",
            ]
        }
    salida.append(
        {
            "item_id": item,
            "sku": sku,
            "bridge_cache": cache,
            "bridge_mapping": filas(
                b,
                "SELECT seller_sku,odoo_product_id,odoo_default_code FROM "
                "meli_sku_mapping WHERE seller_sku=? OR odoo_default_code=?",
                (sku, sku),
            ),
            "faltante": {
                "sku": sku.replace("-CEN-", "-PEZ-"),
                "costos": filas(
                    a,
                    "SELECT id,sku,CAST(cost AS TEXT) AS "
                    "cost,currency,valid_from,valid_to,source,created_at "
                    "FROM sku_costs WHERE sku=?",
                    (sku.replace("-CEN-", "-PEZ-"),),
                ),
                "bom": filas(
                    a,
                    "SELECT id,odoo_bom_id,product_sku,product_name,updated_at FROM "
                    "bom_headers WHERE product_sku=?",
                    (sku.replace("-CEN-", "-PEZ-"),),
                ),
                "mapping": filas(
                    b,
                    "SELECT seller_sku,odoo_product_id,odoo_default_code FROM "
                    "meli_sku_mapping WHERE seller_sku=? OR odoo_default_code=?",
                    (sku.replace("-CEN-", "-PEZ-"), sku.replace("-CEN-", "-PEZ-")),
                ),
            },
            "accounting_costos": filas(
                a,
                "SELECT id,sku,CAST(cost AS TEXT) AS "
                "cost,currency,valid_from,valid_to,source,created_at "
                "FROM sku_costs WHERE sku=? ORDER BY id",
                (sku,),
            ),
            "accounting_bom": filas(
                a,
                "SELECT id,odoo_bom_id,product_sku,product_name,updated_at FROM "
                "bom_headers WHERE product_sku=?",
                (sku,),
            ),
        }
    )
print(
    json.dumps(
        {
            "fecha_utc": datetime.now(UTC).isoformat(),
            "modo": (
                "SQLite mode=ro, query_only, transacciones de lectura "
                "por fuente; no snapshot conjunto"
            ),
            "filas": salida,
        },
        ensure_ascii=False,
        indent=2,
    )
)
b.close()
a.close()
