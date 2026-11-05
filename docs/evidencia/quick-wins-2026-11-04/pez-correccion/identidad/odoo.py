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


CANDIDATOS = [8513, 8520, 8681, 8688, 8709, 8716, 8576, 8744]
FIELDS = [
    "id",
    "default_code",
    "name",
    "active",
    "standard_price",
    "currency_id",
    "product_tmpl_id",
    "product_template_attribute_value_ids",
    "description",
    "description_sale",
    "write_date",
    "uom_id",
    "barcode",
    "sale_ok",
    "purchase_ok",
]
activos = consultar(
    "product.product",
    "search_read",
    [
        [
            ("active", "=", True),
            ("product_template_attribute_value_ids.product_attribute_value_id", "=", 9),
        ]
    ],
    {"fields": FIELDS, "limit": 1000, "order": "id"},
)
blanco = consultar(
    "product.product",
    "search_read",
    [
        [
            ("active", "=", True),
            "|",
            "|",
            ("default_code", "ilike", "BLA-BRO"),
            ("name", "ilike", "blanco con broche"),
            ("name", "ilike", "blanco broche"),
        ]
    ],
    {"fields": FIELDS, "limit": 300, "order": "id"},
)
ids = sorted(set(CANDIDATOS) | {r["id"] for r in blanco})
productos = consultar("product.product", "read", [ids], {"fields": FIELDS})
tids = sorted({r["product_tmpl_id"][0] for r in productos})
plantillas = consultar(
    "product.template",
    "read",
    [tids],
    {
        "fields": [
            "id",
            "name",
            "active",
            "description",
            "description_sale",
            "attribute_line_ids",
            "product_variant_ids",
            "write_date",
        ]
    },
)
attr_ids = sorted(
    {v for r in productos + activos for v in r["product_template_attribute_value_ids"]}
)
attrs = consultar(
    "product.template.attribute.value",
    "read",
    [attr_ids],
    {"fields": ["id", "name", "attribute_id", "product_attribute_value_id", "product_tmpl_id"]},
)
# Evidencia de ventas: no precios ni clientes; solo identidad y cantidad entregada.
ventas = consultar(
    "sale.order.line",
    "search_read",
    [[("product_id", "in", CANDIDATOS), ("state", "in", ["sale", "done"])]],
    {
        "fields": ["id", "product_id", "product_uom_qty", "qty_delivered", "state", "write_date"],
        "limit": 150,
        "order": "write_date desc,id desc",
    },
)
# No sacar binarios de imagen al contexto: informar existencia/tamano para comparar despues.
imagenes = consultar(
    "product.product",
    "read",
    [CANDIDATOS],
    {"fields": ["id", "image_1920"], "context": {"bin_size": True}},
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
                "activos_modelo_sello_cristiano": activos,
                "activos_blanco_broche": blanco,
                "productos_candidatos": productos,
                "plantillas": plantillas,
                "atributos": attrs,
                "ventas_candidatos": ventas,
                "imagenes_disponibles": imagenes,
            }
        ),
        ensure_ascii=False,
        indent=2,
    )
)
