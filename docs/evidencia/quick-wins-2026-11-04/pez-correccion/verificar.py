"""Readback PEZ: solo SELECT; firmas excluyen unicamente los nueve product_id."""

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from psycopg.rows import dict_row

from app.db import connect
from app.redaction import redact_dsn, scrub

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("lote", type=Path)
parser.add_argument("--antes", type=Path)
parser.add_argument("--exigir-aplicado", action="store_true")
args = parser.parse_args()
lote = json.loads(args.lote.read_text())["filas"]
assert len(lote) == 9
skus = [f["seller_sku"] for f in lote]
ids = [f["listing_id"] for f in lote]
dsn = os.environ["ORBIT_DSN_READ"]
redact_dsn(dsn)

try:
    with connect(dsn, row_factory=dict_row) as conn:
        conn.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        conn.execute("SET LOCAL statement_timeout=15000")
        out = {"observado_utc": datetime.now(UTC).isoformat(), "miembros": [], "firmas": {}}
        out["conexion"] = conn.execute(
            "SELECT current_user,current_setting('transaction_read_only') AS solo_lectura"
        ).fetchone()
        alias_existe = conn.execute("SELECT to_regclass('public.meli_sku_alias')").fetchone()
        out["aliases"] = (
            conn.execute("SELECT * FROM meli_sku_alias ORDER BY seller_sku").fetchall()
            if alias_existe["to_regclass"] is not None
            else []
        )
        aliases = {r["seller_sku"]: r["product_id"] for r in out["aliases"]}
        for f in lote:
            filas = conn.execute(
                "SELECT l.external_id,m.*,p.odoo_sku,c.id AS costo_id,c.cost_amount,"
                "c.cost_currency,c.includes_tax,c.valid_from,c.valid_to "
                "FROM listing_miembro m JOIN listing l ON l.id=m.listing_id "
                "LEFT JOIN product p ON p.id=m.product_id "
                "LEFT JOIN sku_cost c ON c.product_id=p.id "
                "AND c.valid_from <= (now() AT TIME ZONE 'UTC')::date "
                "AND (c.valid_to IS NULL OR c.valid_to > (now() AT TIME ZONE 'UTC')::date) "
                "WHERE m.listing_id=%s AND m.variante_ext=%s",
                (f["listing_id"], f["variante_ext"]),
            ).fetchall()
            assert len(filas) == 1, f"cardinalidad de miembro {f['listing_id']}"
            r = filas[0]
            assert r["external_id"] == f["item_id"] and r["seller_sku"] == f["seller_sku"]
            assert r["activo"] is f["activo_antes"]
            if args.exigir_aplicado:
                assert r["product_id"] == f["product_id_destino"] == aliases.get(f["seller_sku"])
                assert r["odoo_sku"] == f["odoo_sku"]
                assert r["costo_id"] == f["costo"]["id"]
                assert r["cost_amount"] == Decimal(f["costo"]["importe"])
                assert r["cost_currency"] == f["costo"]["moneda"]
                assert r["includes_tax"] is f["costo"]["includes_tax"]
                assert str(r["valid_from"]) == f["costo"]["valid_from"]
                assert r["valid_to"] is None
            out["miembros"].append(r)
        for tabla in ("product", "sku_cost", "listing", "precio_goal"):
            out["firmas"][tabla] = conn.execute(
                "SELECT count(*) AS filas,md5(coalesce(string_agg(to_jsonb(t)::text,"
                f"E'\\n' ORDER BY id),'')) AS md5 FROM {tabla} t"
            ).fetchone()
        out["firmas"]["listing_miembro_sin_nueve_productos"] = conn.execute(
            "SELECT count(*) AS filas,md5(coalesce(string_agg((to_jsonb(t) || CASE "
            "WHEN seller_sku=ANY(%s) THEN jsonb_build_object('product_id',NULL) "
            "ELSE '{}'::jsonb END)::text,E'\\n' ORDER BY listing_id,variante_ext),'')) "
            "AS md5 FROM listing_miembro t",
            (skus,),
        ).fetchone()
        out["unidades"] = conn.execute(
            "SELECT listing_id,count(*) AS miembros,"
            "count(*) FILTER (WHERE product_id IS NULL) AS sin_producto,"
            "count(*) FILTER (WHERE activo AND product_id IS NULL) AS activos_sin_producto "
            "FROM listing_miembro WHERE listing_id=ANY(%s) GROUP BY listing_id ORDER BY listing_id",
            (ids,),
        ).fetchall()
        out["ingesta_costos"] = conn.execute(
            "SELECT id,started_at,finished_at,ok,rows_written,rows_skipped,skip_reason "
            "FROM ingest_run WHERE source='accounting_sku_costs' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if args.antes:
            antes = json.loads(args.antes.read_text())
            assert out["firmas"] == antes["firmas"], "cambios fuera de los nueve product_id"
            assert len(out["aliases"]) == len(antes["aliases"]) + 9, "cardinalidad de aliases"
            out["sin_cambios_ajenos"] = True
        if args.exigir_aplicado:
            assert all(r["sin_producto"] == 0 for r in out["unidades"])
        out["catalogo_sha256"] = hashlib.sha256(
            Path("app/meli/catalogo.py").read_bytes()
        ).hexdigest()
        out["estado"] = "verificado" if args.exigir_aplicado else "estado_previo"
    print(scrub(json.dumps(out, default=str, ensure_ascii=False, indent=2)))
except Exception as error:
    print(scrub(f"{type(error).__name__}: {error}"))
    raise SystemExit(1) from None
