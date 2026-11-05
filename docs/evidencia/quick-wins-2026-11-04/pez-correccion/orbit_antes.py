"""Inventario PEZ en Orbit: ejecutar por stdin en el contenedor, solo lectura."""

import json
import os
from datetime import UTC, datetime

from psycopg.rows import dict_row

from app.db import connect
from app.redaction import redact_dsn, register_secret, scrub

CONSULTAS = {
    "columnas": """
        SELECT table_name,column_name,data_type FROM information_schema.columns
        WHERE table_schema='public' AND (table_name IN
          ('product','sku_cost','listing','listing_miembro','precio_goal',
           'precio_corrida','precio_cambio') OR table_name LIKE '%mapping%'
           OR table_name LIKE '%alias%')
        ORDER BY table_name,ordinal_position
    """,
    "productos": """
        SELECT * FROM product WHERE odoo_sku ILIKE '%PEZ%' ORDER BY id
    """,
    "costos": """
        SELECT * FROM sku_cost WHERE product_id IN
          (SELECT id FROM product WHERE odoo_sku ILIKE '%PEZ%')
        ORDER BY product_id,valid_from
    """,
    "miembros": """
        SELECT to_jsonb(l) AS listing,to_jsonb(m) AS miembro
        FROM listing_miembro m JOIN listing l ON l.id=m.listing_id
        WHERE l.platform='meli' AND m.seller_sku ILIKE '%PEZ%'
        ORDER BY l.id,m.variante_ext
    """,
    "goals": """
        SELECT to_jsonb(g) AS goal FROM precio_goal g
        WHERE to_jsonb(g)->>'platform'='meli'
        ORDER BY g.id
    """,
    "corridas": """
        SELECT to_jsonb(c) AS corrida FROM precio_corrida c
        WHERE to_jsonb(c)->>'platform'='meli' ORDER BY c.id DESC LIMIT 4
    """,
    "ingestas": """
        SELECT id,source,started_at,finished_at,ok,rows_written,rows_skipped,skip_reason
        FROM ingest_run WHERE source IN ('accounting_sku_costs','meli_catalogo')
        ORDER BY id DESC LIMIT 8
    """,
}

dsn = os.environ["ORBIT_DSN_READ"]
register_secret(dsn)
redact_dsn(dsn)
try:
    with connect(dsn, row_factory=dict_row) as conn:
        conn.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        conn.execute("SET LOCAL statement_timeout=15000")
        resultado = {"observado_utc": datetime.now(UTC).isoformat()}
        for nombre, sql in CONSULTAS.items():
            resultado[nombre] = conn.execute(sql).fetchall()
    print(scrub(json.dumps(resultado, default=str, ensure_ascii=False, indent=2)))
except Exception as error:
    print(scrub(f"{type(error).__name__}: {error}"))
    raise SystemExit(1) from None
