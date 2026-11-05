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


CONTEXTO = {"active_test": False}
metadatos = {}


def campos(modelo, deseados):
    if modelo not in metadatos:
        metadatos[modelo] = consultar(
            modelo, "fields_get", [], {"attributes": ["type", "relation"]}
        )
    return [x for x in deseados if x in metadatos[modelo]]


PRODUCTO = campos(
    "product.product",
    [
        "id",
        "name",
        "default_code",
        "active",
        "standard_price",
        "currency_id",
        "company_id",
        "product_tmpl_id",
        "uom_id",
        "product_template_attribute_value_ids",
        "write_date",
        "cost_method",
    ],
)
BOM = campos(
    "mrp.bom",
    [
        "id",
        "code",
        "active",
        "sequence",
        "type",
        "product_tmpl_id",
        "product_id",
        "product_qty",
        "product_uom_id",
        "bom_line_ids",
        "byproduct_ids",
        "operation_ids",
        "company_id",
        "create_date",
        "write_date",
        "consumption",
    ],
)
LINEA = campos(
    "mrp.bom.line",
    [
        "id",
        "bom_id",
        "product_id",
        "product_qty",
        "product_uom_id",
        "bom_product_template_attribute_value_ids",
        "operation_id",
        "write_date",
    ],
)
productos = consultar(
    "product.product",
    "search_read",
    [[("default_code", "in", SKUS)]],
    {"fields": PRODUCTO, "context": CONTEXTO, "limit": 100, "order": "id"},
)
raices = [x["id"] for x in productos]
por_id = {x["id"]: x for x in productos}
boms = {}
lineas = {}
consultados = set()
capas = []
for profundidad in range(8):
    actuales = sorted(set(por_id) - consultados)
    if not actuales:
        break
    tids = sorted({por_id[x]["product_tmpl_id"][0] for x in actuales})
    nuevos_boms = []
    for offset in range(0, 2000, 200):
        pagina = consultar(
            "mrp.bom",
            "search_read",
            [
                [
                    ("product_tmpl_id", "in", tids),
                    "|",
                    ("product_id", "=", False),
                    ("product_id", "in", actuales),
                ]
            ],
            {"fields": BOM, "context": CONTEXTO, "limit": 200, "offset": offset, "order": "id"},
        )
        nuevos_boms.extend(pagina)
        if len(pagina) < 200:
            break
    else:
        raise ValueError("limite de BOM alcanzado")
    for b in nuevos_boms:
        boms[b["id"]] = b
    lids = sorted({lid for b in nuevos_boms for lid in b["bom_line_ids"]} - set(lineas))
    nuevas_lineas = (
        consultar("mrp.bom.line", "read", [lids], {"fields": LINEA, "context": CONTEXTO})
        if lids
        else []
    )
    for linea in nuevas_lineas:
        lineas[linea["id"]] = linea
    pids = sorted({linea["product_id"][0] for linea in nuevas_lineas} - set(por_id))
    nuevos_productos = (
        consultar("product.product", "read", [pids], {"fields": PRODUCTO, "context": CONTEXTO})
        if pids
        else []
    )
    for producto in nuevos_productos:
        por_id[producto["id"]] = producto
    consultados.update(actuales)
    capas.append(
        {
            "profundidad": profundidad,
            "productos_consultados": actuales,
            "boms_encontrados": [b["id"] for b in nuevos_boms],
        }
    )
else:
    raise ValueError("profundidad maxima alcanzada")

uom_ids = sorted(
    {r["uom_id"][0] for r in por_id.values()}
    | {b["product_uom_id"][0] for b in boms.values()}
    | {linea["product_uom_id"][0] for linea in lineas.values()}
)
uoms = consultar(
    "uom.uom",
    "read",
    [uom_ids],
    {
        "fields": [
            "id",
            "name",
            "category_id",
            "factor",
            "factor_inv",
            "rounding",
            "uom_type",
            "active",
        ],
        "context": CONTEXTO,
    },
)
attr_ids = sorted(
    {v for p in por_id.values() for v in p["product_template_attribute_value_ids"]}
    | {v for linea in lineas.values() for v in linea["bom_product_template_attribute_value_ids"]}
)
attrs = consultar(
    "product.template.attribute.value",
    "read",
    [attr_ids],
    {
        "fields": campos(
            "product.template.attribute.value",
            [
                "id",
                "name",
                "product_tmpl_id",
                "attribute_id",
                "product_attribute_value_id",
                "ptav_active",
            ],
        ),
        "context": CONTEXTO,
    },
)
by_ids = sorted({x for b in boms.values() for x in b.get("byproduct_ids", [])})
byproducts = (
    consultar(
        "mrp.bom.byproduct",
        "read",
        [by_ids],
        {
            "fields": campos(
                "mrp.bom.byproduct",
                [
                    "id",
                    "bom_id",
                    "product_id",
                    "product_qty",
                    "product_uom_id",
                    "cost_share",
                    "bom_product_template_attribute_value_ids",
                ],
            ),
            "context": CONTEXTO,
        },
    )
    if by_ids
    else []
)
op_ids = sorted({x for b in boms.values() for x in b.get("operation_ids", [])})
operations = (
    consultar(
        "mrp.routing.workcenter",
        "read",
        [op_ids],
        {
            "fields": campos(
                "mrp.routing.workcenter",
                [
                    "id",
                    "name",
                    "bom_id",
                    "workcenter_id",
                    "time_cycle_manual",
                    "time_cycle",
                    "time_mode",
                ],
            ),
            "context": CONTEXTO,
        },
    )
    if op_ids
    else []
)
ids_bom = sorted(boms)
defecto = consultar(
    "mrp.bom",
    "search_read",
    [[("id", "in", ids_bom)]],
    {"fields": ["id", "active"], "limit": 1000, "order": "id"},
)


# Representar numeros de dinero/cantidad como texto; no efectuar calculos con float.
def exacto(valor):
    if isinstance(valor, float):
        return str(valor)
    if isinstance(valor, list):
        return [exacto(x) for x in valor]
    if isinstance(valor, dict):
        return {k: exacto(v) for k, v in valor.items()}
    return valor


print(
    json.dumps(
        exacto(
            {
                "fecha_utc": datetime.now(UTC).isoformat(),
                "fuente": "Odoo XML-RPC existente, authenticate/fields_get/search_read/read",
                "contexto": CONTEXTO,
                "raices": raices,
                "productos": list(por_id.values()),
                "boms": list(boms.values()),
                "lineas": list(lineas.values()),
                "uoms": uoms,
                "atributos": attrs,
                "byproducts": byproducts,
                "operaciones": operations,
                "capas": capas,
                "boms_contexto_defecto": defecto,
                "campos_consultados": {
                    "product.product": PRODUCTO,
                    "mrp.bom": BOM,
                    "mrp.bom.line": LINEA,
                },
            }
        ),
        ensure_ascii=False,
        indent=2,
    )
)
