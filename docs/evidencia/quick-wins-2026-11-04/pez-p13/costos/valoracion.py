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


IDS = [7106, 7112, 7070, 7208, 7184, 7190, 7178, 7196, 7232]
meta = consultar("stock.valuation.layer", "fields_get", [], {"attributes": ["type"]})
fields = [
    f
    for f in [
        "id",
        "product_id",
        "create_date",
        "description",
        "quantity",
        "unit_cost",
        "value",
        "remaining_qty",
        "remaining_value",
        "stock_move_id",
        "stock_valuation_layer_id",
        "company_id",
    ]
    if f in meta
]
sets = consultar(
    "stock.valuation.layer",
    "search_read",
    [[("product_id", "in", IDS)]],
    {"fields": fields, "limit": 30, "order": "create_date desc,id desc"},
)
cofres = consultar(
    "stock.valuation.layer",
    "search_read",
    [
        [
            ("product_id", "in", [7, 8]),
            ("create_date", ">=", "2026-08-23 00:00:00"),
            ("create_date", "<", "2026-08-25 00:00:00"),
        ]
    ],
    {"fields": fields, "limit": 100, "order": "create_date,id"},
)
stock_routes = consultar("stock.route", "read", [[5]], {"fields": ["id", "name", "active"]})
boms = consultar(
    "mrp.bom",
    "read",
    [[1824, 1825, 1788, 1921, 1897, 1908, 1896, 1909, 1945]],
    {"fields": ["id", "type", "active", "product_id"]},
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
                "capas_sets_ultimas30": sets,
                "capas_cofres_23_24agosto": cofres,
                "ruta": stock_routes,
                "boms": boms,
            }
        ),
        ensure_ascii=False,
        indent=2,
    )
)
