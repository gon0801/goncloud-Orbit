"""Captura de recetas PEZ y componentes; Odoo solo lectura, sin recalcular ni guardar."""

import ast
import hashlib
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


IDS = [7106, 7112, 7070, 7208, 7184, 7190, 7178, 7196, 7232, 7, 8]
meta = consultar("product.product", "fields_get", [], {"attributes": ["type"]})
fields = [
    f
    for f in [
        "id",
        "default_code",
        "categ_id",
        "type",
        "detailed_type",
        "cost_method",
        "valuation",
        "route_ids",
        "company_id",
    ]
    if f in meta
]
productos = consultar("product.product", "read", [IDS], {"fields": fields})
cids = sorted({p["categ_id"][0] for p in productos})
meta_cat = consultar("product.category", "fields_get", [], {"attributes": ["type"]})
categorias = consultar(
    "product.category",
    "read",
    [cids],
    {
        "fields": [
            f
            for f in ["id", "name", "parent_id", "property_cost_method", "property_valuation"]
            if f in meta_cat
        ]
    },
)
modulos = consultar(
    "ir.module.module",
    "search_read",
    [
        [
            (
                "name",
                "in",
                [
                    "base_automation",
                    "mrp_account",
                    "bom_total_cost",
                    "stock_account",
                    "kits_product_bundle_pack",
                ],
            )
        ]
    ],
    {"fields": ["id", "name", "state", "installed_version"], "limit": 20},
)
actions = consultar(
    "ir.actions.server",
    "search_read",
    [
        [
            ("state", "=", "code"),
            "|",
            "|",
            "|",
            ("code", "ilike", "button_bom_cost"),
            ("code", "ilike", "_set_price_from_bom"),
            ("code", "ilike", "standard_price"),
            ("code", "ilike", "compute_price"),
        ]
    ],
    {"fields": ["id", "name", "model_id", "code"], "limit": 100, "order": "id"},
)
# Codigo arbitrario de acciones queda solo en memoria; emitir nombres de llamadas y hash.
resumen_actions = []
for a in actions:
    code = a.pop("code") or ""
    try:
        tree = ast.parse(code)
        calls = sorted({ast.unparse(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)})
    except SyntaxError:
        calls = ["codigo no parseable por AST"]
    a["codigo_sha256"] = hashlib.sha256(code.encode()).hexdigest()
    a["llamadas"] = calls
    resumen_actions.append(a)
aids = [a["id"] for a in actions]
crons = (
    consultar(
        "ir.cron",
        "search_read",
        [[("ir_actions_server_id", "in", aids)]],
        {
            "fields": ["id", "name", "active", "ir_actions_server_id", "lastcall", "nextcall"],
            "limit": 100,
            "order": "id",
        },
    )
    if aids
    else []
)
automations = []
if any(m["name"] == "base_automation" and m["state"] == "installed" for m in modulos):
    mb = consultar("base.automation", "fields_get", [], {"attributes": ["type"]})
    f = [
        f
        for f in [
            "id",
            "name",
            "active",
            "model_id",
            "trigger",
            "trigger_field_ids",
            "action_server_ids",
        ]
        if f in mb
    ]
    models_ids = consultar(
        "ir.model",
        "search_read",
        [
            [
                (
                    "model",
                    "in",
                    [
                        "product.product",
                        "product.template",
                        "mrp.bom",
                        "mrp.bom.line",
                        "ir.property",
                    ],
                )
            ]
        ],
        {"fields": ["id", "model"], "limit": 20},
    )
    automations = consultar(
        "base.automation",
        "search_read",
        [[("model_id", "in", [m["id"] for m in models_ids])]],
        {"fields": f, "context": {"active_test": False}, "limit": 200},
    )
print(
    json.dumps(
        {
            "fecha_utc": datetime.now(UTC).isoformat(),
            "productos": productos,
            "categorias": categorias,
            "modulos": modulos,
            "acciones_costos": resumen_actions,
            "crons_acciones_costos": crons,
            "automatizaciones_modelos_costos": automations,
        },
        ensure_ascii=False,
        indent=2,
    )
)
