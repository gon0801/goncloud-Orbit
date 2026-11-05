"""Consulta acotada de costos MeLi, sin escrituras."""

import json
import os
from datetime import UTC, datetime

from app.db import connect

SQL = """
WITH ultimo AS (
 SELECT DISTINCT ON (listing_id) * FROM estimacion_escenario
 WHERE platform='meli'
 ORDER BY listing_id,valoracion_date DESC,observed_at DESC,id DESC
)
SELECT to_jsonb(l) AS listing, to_jsonb(p) AS producto,
       to_jsonb(u) AS escenario,
       (SELECT jsonb_agg(to_jsonb(c)) FROM sku_cost c WHERE c.product_id=l.product_id) AS costos
FROM ultimo u JOIN listing l ON l.id=u.listing_id
JOIN product p ON p.id=l.product_id
WHERE u.motivos::text LIKE '%costo_ausente%'
ORDER BY l.id
"""
with connect(os.environ["ORBIT_DSN_READ"]) as conn:
    conn.execute("SET TRANSACTION READ ONLY")
    conn.execute("SET LOCAL statement_timeout=15000")
    with conn.cursor() as cur:
        cur.execute(SQL)
        nombres = [x.name for x in cur.description]
        filas = [dict(zip(nombres, fila, strict=True)) for fila in cur.fetchall()]
    runs = conn.execute(
        "SELECT id,finished_at,ok,rows_skipped,skip_reason FROM "
        "ingest_run WHERE source='accounting_sku_costs' ORDER BY id DESC LIMIT 3"
    ).fetchall()
    extra = {}
    for tabla, filtro in [
        ("listing_miembro", "listing_id IN (SELECT id FROM listing WHERE platform='meli')"),
        (
            "meli_publicacion_observation",
            "item_id IN (SELECT external_id FROM listing WHERE platform='meli')",
        ),
    ]:
        sql = f"SELECT to_jsonb(t) FROM {tabla} t WHERE {filtro}"
        if tabla == "meli_publicacion_observation":
            sql = (
                f"SELECT DISTINCT ON (item_id) to_jsonb(t) FROM {tabla} t WHERE "
                f"{filtro} ORDER BY item_id,observed_at DESC"
            )
        resultados = [r[0] for r in conn.execute(sql).fetchall()]
        ids = {r["listing"]["id"] for r in filas}
        exts = {r["listing"]["external_id"] for r in filas}
        extra[tabla] = [
            r for r in resultados if r.get("listing_id") in ids or r.get("item_id") in exts
        ]
    print(
        json.dumps(
            {
                "extra": extra,
                "fecha_utc": datetime.now(UTC).isoformat(),
                "sql": SQL,
                "filas": filas,
                "runs": runs,
            },
            default=str,
            ensure_ascii=False,
            indent=2,
        )
    )
