"""Conciliacion RO de costos SET vigentes en accounting; una consulta para nueve SKU."""

import json
import sqlite3
from datetime import UTC, datetime

SKUS = [
    "SET-CAR-BLA-PEZ-DOR",
    "SET-CAR-BLA-PEZ-PLA",
    "SET-BLA-BRO-PEZ-DOR",
    "SET-ITA-PEZ-PLA",
    "SET-GAM-AZU-PEZ-PLA",
    "SET-GAM-NEG-PEZ-DOR",
    "SET-GAM-AZU-PEZ-DOR",
    "SET-GAM-NEG-PEZ-PLA",
    "SET-PLA-CH-PEZ-PLA",
]
SQL = """SELECT id,sku,cost,currency,valid_from,valid_to,source,created_at
FROM sku_costs WHERE sku IN ({marcas})
AND COALESCE(valid_from,created_at) <= datetime('now')
AND (valid_to IS NULL OR valid_to > datetime('now'))
ORDER BY sku,COALESCE(valid_from,created_at),id""".format(marcas=",".join("?" for _ in SKUS))
conn = sqlite3.connect(
    "file:/mnt/data/appdata/accounting/data/accounting.db?mode=ro", uri=True, timeout=15
)
conn.row_factory = sqlite3.Row
try:
    conn.execute("PRAGMA query_only=ON")
    conn.execute("BEGIN")
    filas = [dict(r) for r in conn.execute(SQL, SKUS)]
    for r in filas:
        r["cost"] = str(r["cost"])
finally:
    conn.close()
print(
    json.dumps(
        {
            "fecha_utc": datetime.now(UTC).isoformat(),
            "modo": "mode=ro, query_only, transaccion de lectura, una consulta de datos",
            "sql": SQL,
            "skus": SKUS,
            "filas": filas,
        },
        ensure_ascii=False,
        indent=2,
    )
)
