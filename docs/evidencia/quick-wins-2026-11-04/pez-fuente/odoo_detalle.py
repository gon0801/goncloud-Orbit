"""Busqueda primaria Odoo de PEZ: solo authenticate, fields_get, search_read y read."""

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


exactos = consultar(
    "product.product",
    "search_read",
    [[("default_code", "in", SKUS)]],
    {
        "fields": [
            "id",
            "default_code",
            "name",
            "active",
            "standard_price",
            "currency_id",
            "product_tmpl_id",
            "product_template_attribute_value_ids",
            "uom_id",
            "write_date",
        ],
        "context": {"active_test": False},
        "limit": 100,
    },
)
tids = sorted({r["product_tmpl_id"][0] for r in exactos})
valores = sorted({v for r in exactos for v in r["product_template_attribute_value_ids"]})
atributos = consultar(
    "product.template.attribute.value",
    "read",
    [valores],
    {
        "fields": [
            "id",
            "name",
            "product_tmpl_id",
            "attribute_id",
            "product_attribute_value_id",
            "ptav_active",
        ]
    },
)
meta_bom = consultar("mrp.bom", "fields_get", [], {"attributes": ["type"]})
campos_bom = [
    x
    for x in [
        "id",
        "code",
        "active",
        "type",
        "product_tmpl_id",
        "product_id",
        "product_qty",
        "product_uom_id",
        "bom_line_ids",
        "write_date",
        "company_id",
    ]
    if x in meta_bom
]
boms = []
for offset_bom in range(0, 2000, 200):
    pagina = consultar(
        "mrp.bom",
        "search_read",
        [[("product_tmpl_id", "in", tids)]],
        {
            "fields": campos_bom,
            "context": {"active_test": False},
            "limit": 200,
            "offset": offset_bom,
            "order": "id",
        },
    )
    boms.extend(pagina)
    if len(pagina) < 200:
        break
else:
    raise ValueError("limite de paginas BOM alcanzado")
boms_consultados = len(boms)
ids_pez = {r["id"] for r in exactos}
boms = [b for b in boms if not b["product_id"] or b["product_id"][0] in ids_pez]
lids = sorted({x for b in boms for x in b["bom_line_ids"]})
meta_lineas = consultar("mrp.bom.line", "fields_get", [], {"attributes": ["type"]})
campos_lineas = [
    x
    for x in [
        "id",
        "bom_id",
        "product_id",
        "product_qty",
        "product_uom_id",
        "bom_product_template_attribute_value_ids",
        "write_date",
    ]
    if x in meta_lineas
]
lineas = consultar("mrp.bom.line", "read", [lids], {"fields": campos_lineas}) if lids else []
cids = sorted({r["product_id"][0] for r in lineas if r["product_id"]})
componentes = (
    consultar(
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
                "uom_id",
                "write_date",
            ],
            "context": {"active_test": False},
        },
    )
    if cids
    else []
)
# Dominio acotado a NH; sin truncar silenciosamente los alias de la familia.
alias = []
offset = 0
while True:
    pagina = consultar(
        "product.product",
        "search_read",
        [
            [
                ("default_code", "ilike", "NH-"),
                "|",
                ("default_code", "ilike", "PEZ"),
                ("name", "ilike", "pez"),
            ]
        ],
        {
            "fields": [
                "id",
                "default_code",
                "name",
                "active",
                "standard_price",
                "currency_id",
                "product_tmpl_id",
            ],
            "context": {"active_test": False},
            "limit": 200,
            "offset": offset,
            "order": "id",
        },
    )
    alias.extend(pagina)
    if len(pagina) < 200:
        break
    offset += 200
    if offset >= 2000:
        raise ValueError("limite de paginas alcanzado")
ids_exactos = [r["id"] for r in exactos]
ventas = consultar(
    "sale.order.line",
    "search_read",
    [[("product_id", "in", ids_exactos), ("state", "in", ["sale", "done"])]],
    {
        "fields": ["id", "product_id", "product_uom_qty", "qty_delivered", "state", "write_date"],
        "context": {"active_test": False},
        "limit": 50,
        "order": "write_date desc,id desc",
    },
)
plantillas_actuales = consultar(
    "product.template",
    "read",
    [tids],
    {
        "fields": ["id", "name", "active", "product_variant_ids", "write_date"],
        "context": {"active_test": False},
    },
)
variantes_relacionadas = consultar(
    "product.product",
    "search_read",
    [[("product_tmpl_id", "in", tids), ("active", "=", True)]],
    {
        "fields": [
            "id",
            "default_code",
            "name",
            "active",
            "standard_price",
            "currency_id",
            "product_template_attribute_value_ids",
        ],
        "limit": 500,
        "order": "id",
    },
)
print(
    json.dumps(
        {
            "fecha_utc": datetime.now(UTC).isoformat(),
            "productos_exactos": exactos,
            "ventas_confirmadas_ultimas50": ventas,
            "plantillas_actuales": plantillas_actuales,
            "variantes_activas_mismas_plantillas": variantes_relacionadas,
            "boms_consultados": boms_consultados,
            "atributos": atributos,
            "boms": boms,
            "lineas": lineas,
            "componentes": componentes,
            "aliases_nh_pez": alias,
            "metodos": ["authenticate", "fields_get", "read", "search_read"],
            "limites": {"exactos": 100, "boms_completo": True, "alias_completo": True},
        },
        ensure_ascii=False,
        indent=2,
        default=str,
    )
)
