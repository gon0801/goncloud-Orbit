"""P13: historial de kits y componentes; SQLite solo lectura, no sincroniza."""

import json
import sqlite3
import time
from datetime import UTC, datetime

SKUS = [
    "4405-BG",
    "4587-BK",
    "4608",
    "ARR-22-DOR-PEZ",
    "ARR-22-PLA-PEZ",
    "COF-22-DOR",
    "COF-22-PLA",
    "EST-CAR-BLC",
    "EST-GAM-AZU",
    "EST-GAM-NEG",
    "SET-BLA-BRO-PEZ-DOR",
    "SET-CAR-BLA-PEZ-DOR",
    "SET-CAR-BLA-PEZ-PLA",
    "SET-GAM-AZU-PEZ-DOR",
    "SET-GAM-AZU-PEZ-PLA",
    "SET-GAM-NEG-PEZ-DOR",
    "SET-GAM-NEG-PEZ-PLA",
    "SET-ITA-PEZ-PLA",
    "SET-PLA-CH-PEZ-PLA",
]
conn = sqlite3.connect(
    "file:/mnt/data/appdata/accounting/data/accounting.db?mode=ro", uri=True, timeout=15
)
conn.row_factory = sqlite3.Row
inicio = time.monotonic()
conn.set_progress_handler(lambda: int(time.monotonic() - inicio > 15), 10000)
try:
    conn.execute("PRAGMA query_only=ON")
    conn.execute("BEGIN")
    sql = (
        "SELECT id,sku,cost,currency,valid_from,valid_to,source,created_at "
        "FROM sku_costs WHERE sku IN ("
        + ",".join("?" for _ in SKUS)
        + ") ORDER BY sku,COALESCE(valid_from,created_at),id"
    )
    filas = [dict(r) for r in conn.execute(sql, SKUS)]
    for r in filas:
        r["cost"] = str(r["cost"]) if r["cost"] is not None else None
    columnas_bom = [dict(r) for r in conn.execute("PRAGMA table_info(bom_lines)")]
finally:
    conn.close()
print(
    json.dumps(
        {
            "observado_utc": datetime.now(UTC).isoformat(),
            "modo": "mode=ro/query_only, transaccion de lectura, timeout15s",
            "sql": sql,
            "skus": SKUS,
            "historial": filas,
            "columnas_bom_lines": columnas_bom,
        },
        ensure_ascii=False,
        indent=2,
    )
)
