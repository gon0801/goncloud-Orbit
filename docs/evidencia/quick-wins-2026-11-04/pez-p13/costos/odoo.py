"""Captura de recetas PEZ y componentes; Odoo solo lectura, sin recalcular ni guardar."""

import json
import shlex
import socket
import xmlrpc.client
from datetime import UTC, datetime
from pathlib import Path

SKUS = [
    "NH-CAR-BLA-PEZ-DOR",
    "NH-CAR-BLA-PEZ-PLA",
    "NH-BLA-BRO-PEZ-DOR",
    "NH-ITA-PEZ-PLA",
    "NH-GAM-AZU-PEZ-PLA",
    "NH-GAM-NEG-PEZ-DOR",
    "NH-GAM-AZU-PEZ-DOR",
    "NH-GAM-NEG-PEZ-PLA",
    "NH-PLA-CH-PEZ-PLA",
]


def cargar(path):
    datos = {}
    for linea in Path(path).read_text().splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, valor = linea.removeprefix("export ").split("=", 1)
        tokens = shlex.split(valor, comments=True)
        datos[clave.strip()] = tokens[0] if tokens else ""
    return datos


socket.setdefaulttimeout(15)
config = cargar("/mnt/data/appdata/accounting/config/accounting.env")
claves = ["ODOO_URL", "ODOO_DB", "ODOO_USER", "ODOO_PASSWORD"]
if not all(config.get(k) for k in claves):
    print(
        json.dumps(
            {
                "error": "configuracion Odoo incompleta",
                "claves_presentes": [k for k in claves if config.get(k)],
            }
        )
    )
    raise SystemExit(2)
url = config["ODOO_URL"].rstrip("/")
db = config["ODOO_DB"]
pwd = config["ODOO_PASSWORD"]
common = xmlrpc.client.ServerProxy(url + "/xmlrpc/2/common", allow_none=True)
uid = common.authenticate(db, config["ODOO_USER"], pwd, {})
if not uid:
    print(json.dumps({"error": "autenticacion no disponible"}))
    raise SystemExit(2)
models = xmlrpc.client.ServerProxy(url + "/xmlrpc/2/object", allow_none=True)


def consultar(modelo, metodo, args, kwargs=None):
    if metodo not in {"fields_get", "search_read", "read"}:
        raise ValueError("metodo fuera de allowlist de lectura")
    return models.execute_kw(db, uid, pwd, modelo, metodo, args, kwargs or {})


SET_IDS = [7106, 7112, 7070, 7208, 7184, 7190, 7178, 7196, 7232]
COMP_IDS = [5, 6, 7, 8, 156, 158, 159, 163, 168, 169]
BOM_IDS = [1824, 1825, 1788, 1921, 1897, 1908, 1896, 1909, 1945]
meta_product = consultar(
    "product.product",
    "fields_get",
    [],
    {"attributes": ["type", "company_dependent", "store", "related"]},
)
meta_bom = consultar("mrp.bom", "fields_get", [], {"attributes": ["type", "store"]})
meta_linea = consultar("mrp.bom.line", "fields_get", [], {"attributes": ["type", "store"]})
usuario = consultar("res.users", "read", [[uid]], {"fields": ["id", "company_id", "company_ids"]})
company_ids = usuario[0]["company_ids"]
companies = consultar("res.company", "read", [company_ids], {"fields": ["id", "currency_id"]})
productos = consultar(
    "product.product",
    "read",
    [SET_IDS + COMP_IDS],
    {
        "fields": [
            "id",
            "default_code",
            "active",
            "standard_price",
            "currency_id",
            "company_id",
            "product_tmpl_id",
            "uom_id",
            "write_date",
        ]
    },
)
boms = consultar(
    "mrp.bom",
    "read",
    [BOM_IDS],
    {
        "fields": [
            x
            for x in [
                "id",
                "active",
                "sequence",
                "product_id",
                "product_tmpl_id",
                "company_id",
                "product_qty",
                "product_uom_id",
                "bom_line_ids",
                "operation_ids",
                "byproduct_ids",
                "bom_cost",
                "total_bom_cost",
                "currency_id",
                "create_date",
                "write_date",
            ]
            if x in meta_bom
        ]
    },
)
lids = sorted({i for b in boms for i in b["bom_line_ids"]})
lineas = consultar(
    "mrp.bom.line",
    "read",
    [lids],
    {
        "fields": [
            x
            for x in [
                "id",
                "bom_id",
                "product_id",
                "product_qty",
                "product_uom_id",
                "bom_product_template_attribute_value_ids",
                "child_bom_id",
                "cost",
                "create_date",
                "write_date",
            ]
            if x in meta_linea
        ]
    },
)
precios_company = []
for cid in company_ids:
    precios_company.append(
        {
            "company_id": cid,
            "filas": consultar(
                "product.product",
                "read",
                [SET_IDS + [7, 8]],
                {
                    "fields": ["id", "default_code", "standard_price", "currency_id"],
                    "context": {"allowed_company_ids": [cid], "force_company": cid},
                },
            ),
        }
    )
props = consultar(
    "ir.property",
    "search_read",
    [
        [
            ("fields_id.name", "=", "standard_price"),
            "|",
            ("res_id", "in", [f"product.product,{p}" for p in SET_IDS + COMP_IDS]),
            ("res_id", "=", False),
        ]
    ],
    {
        "fields": [
            "id",
            "name",
            "res_id",
            "company_id",
            "value_float",
            "create_date",
            "write_date",
        ],
        "limit": 200,
        "order": "id",
    },
)
# Inventario de crons de costos. No exponer codigo arbitrario ni secretos de otros jobs.
crons = consultar(
    "ir.cron",
    "search_read",
    [
        [
            "|",
            "|",
            "|",
            ("name", "ilike", "cost"),
            ("name", "ilike", "costo"),
            ("name", "ilike", "bom"),
            ("name", "ilike", "price"),
        ]
    ],
    {
        "fields": ["id", "name", "active", "ir_actions_server_id", "lastcall", "nextcall"],
        "limit": 100,
        "order": "id",
    },
)


def exacto(x):
    if isinstance(x, float):
        return str(x)
    if isinstance(x, list):
        return [exacto(v) for v in x]
    if isinstance(x, dict):
        return {k: exacto(v) for k, v in x.items()}
    return x


print(
    json.dumps(
        exacto(
            {
                "fecha_utc": datetime.now(UTC).isoformat(),
                "usuario_companies": usuario,
                "companies": companies,
                "productos": productos,
                "boms": boms,
                "lineas": lineas,
                "precios_por_compania": precios_company,
                "propiedades_precio": props,
                "crons_costos": crons,
                "meta_standard_price": meta_product.get("standard_price"),
                "meta_cost_linea": meta_linea.get("cost"),
                "meta_cost_bom": meta_bom.get("bom_cost"),
            }
        ),
        ensure_ascii=False,
        indent=2,
    )
)
