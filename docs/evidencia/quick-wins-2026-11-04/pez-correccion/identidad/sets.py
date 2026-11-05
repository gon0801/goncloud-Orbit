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


SET_IDS = [7070, 7106, 7112, 7178, 7184, 7190, 7196, 7208, 7232]
productos = consultar(
    "product.product",
    "read",
    [SET_IDS],
    {
        "fields": [
            "id",
            "default_code",
            "name",
            "active",
            "standard_price",
            "currency_id",
            "product_tmpl_id",
            "uom_id",
            "write_date",
            "product_template_attribute_value_ids",
            "description",
            "description_sale",
        ]
    },
)
boms = consultar(
    "mrp.bom",
    "search_read",
    [[("active", "=", True), ("product_id", "in", SET_IDS)]],
    {
        "fields": [
            "id",
            "active",
            "product_id",
            "product_tmpl_id",
            "product_qty",
            "product_uom_id",
            "bom_line_ids",
            "byproduct_ids",
            "operation_ids",
            "write_date",
        ],
        "limit": 100,
        "order": "id",
    },
)
lids = sorted({lid for b in boms for lid in b["bom_line_ids"]})
lineas = consultar(
    "mrp.bom.line",
    "read",
    [lids],
    {
        "fields": [
            "id",
            "bom_id",
            "product_id",
            "product_qty",
            "product_uom_id",
            "bom_product_template_attribute_value_ids",
        ]
    },
)
cids = sorted({r["product_id"][0] for r in lineas})
componentes = consultar(
    "product.product",
    "read",
    [cids],
    {
        "fields": [
            "id",
            "default_code",
            "name",
            "active",
            "standard_price",
            "currency_id",
            "product_tmpl_id",
            "uom_id",
            "write_date",
        ]
    },
)
tids = sorted({r["product_tmpl_id"][0] for r in componentes})
subboms = consultar(
    "mrp.bom",
    "search_read",
    [
        [
            ("product_tmpl_id", "in", tids),
            "|",
            ("product_id", "in", cids),
            ("product_id", "=", False),
        ]
    ],
    {
        "fields": ["id", "active", "product_id", "product_tmpl_id", "bom_line_ids"],
        "context": {"active_test": False},
        "limit": 100,
    },
)
aids = sorted({a for r in productos for a in r["product_template_attribute_value_ids"]})
attrs = consultar(
    "product.template.attribute.value",
    "read",
    [aids],
    {"fields": ["id", "name", "attribute_id", "product_attribute_value_id", "product_tmpl_id"]},
)
ventas = consultar(
    "sale.order.line",
    "search_read",
    [[("product_id", "in", SET_IDS), ("state", "in", ["sale", "done"])]],
    {
        "fields": ["id", "product_id", "name", "product_uom_qty", "qty_delivered", "write_date"],
        "limit": 100,
        "order": "write_date desc,id desc",
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
                "productos": productos,
                "boms": boms,
                "lineas": lineas,
                "componentes": componentes,
                "sub_boms_componentes": subboms,
                "atributos": attrs,
                "ventas": ventas,
            }
        ),
        ensure_ascii=False,
        indent=2,
    )
)
