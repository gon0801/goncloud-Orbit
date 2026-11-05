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


LEAF_IDS = [5, 158, 159, 163, 168, 169, 1022, 1023, 1024, 1025]
ROOT_IDS = [5900, 6442, 6452, 6642, 6652, 6662, 6672, 6692, 6732]
TEMPLATE_IDS = [5911, 6186, 6187, 6206, 6207, 6208, 6209, 6211, 6215]
BOM_IDS = [
    328,
    985,
    994,
    1042,
    1043,
    1126,
    1127,
    1142,
    1143,
    1159,
    1191,
    1284,
    1326,
    1327,
    1404,
    1405,
    1418,
    1419,
    1433,
    1461,
]
ALIAS_IDS = [
    8485,
    8492,
    8513,
    8520,
    8541,
    8548,
    8569,
    8576,
    8597,
    8604,
    8625,
    8632,
    8653,
    8660,
    8681,
    8688,
    8709,
    8716,
    8737,
    8744,
    8765,
    8772,
    8807,
    8814,
    8823,
    8830,
    8837,
    8844,
]


def paginar(modelo, dominio, fields, context=None, maximo=3000):
    filas = []
    for offset in range(0, maximo, 200):
        pagina = consultar(
            modelo,
            "search_read",
            [dominio],
            {
                "fields": fields,
                "context": context or {"active_test": False},
                "limit": 200,
                "offset": offset,
                "order": "id",
            },
        )
        filas.extend(pagina)
        if len(pagina) < 200:
            return filas
    raise ValueError("limite de paginas alcanzado")


boms = paginar(
    "mrp.bom",
    [
        ("active", "=", True),
        "|",
        ("bom_line_ids.product_id", "in", LEAF_IDS),
        ("product_id", "in", ALIAS_IDS),
    ],
    [
        "id",
        "code",
        "active",
        "sequence",
        "product_id",
        "product_tmpl_id",
        "product_qty",
        "product_uom_id",
        "bom_line_ids",
        "byproduct_ids",
        "operation_ids",
        "write_date",
    ],
)
lids = sorted({x for b in boms for x in b["bom_line_ids"]})
lineas = (
    consultar(
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
    if lids
    else []
)
pids = sorted(
    {linea["product_id"][0] for linea in lineas}
    | {b["product_id"][0] for b in boms if b["product_id"]}
)
productos = consultar(
    "product.product",
    "read",
    [pids],
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
        ],
        "context": {"active_test": False},
    },
)
uids = sorted(
    {linea["product_uom_id"][0] for linea in lineas}
    | {b["product_uom_id"][0] for b in boms}
    | {p["uom_id"][0] for p in productos}
)
uoms = consultar(
    "uom.uom",
    "read",
    [uids],
    {
        "fields": ["id", "name", "category_id", "factor", "factor_inv", "rounding"],
        "context": {"active_test": False},
    },
)
audit = {}
dominio = [
    "|",
    "|",
    "&",
    ("model", "=", "product.product"),
    ("res_id", "in", ROOT_IDS),
    "&",
    ("model", "=", "product.template"),
    ("res_id", "in", TEMPLATE_IDS),
    "&",
    ("model", "=", "mrp.bom"),
    ("res_id", "in", BOM_IDS),
]
try:
    mensajes = paginar(
        "mail.message",
        dominio,
        [
            "id",
            "model",
            "res_id",
            "date",
            "author_id",
            "subject",
            "body",
            "tracking_value_ids",
            "subtype_id",
        ],
        maximo=2000,
    )
    tids = sorted({v for m in mensajes for v in m["tracking_value_ids"]})
    meta = consultar("mail.tracking.value", "fields_get", [], {"attributes": ["type"]})
    f = [
        x
        for x in [
            "id",
            "field_id",
            "field_info",
            "old_value_integer",
            "new_value_integer",
            "old_value_char",
            "new_value_char",
            "old_value_text",
            "new_value_text",
            "mail_message_id",
            "create_date",
        ]
        if x in meta
    ]
    tracking = consultar("mail.tracking.value", "read", [tids], {"fields": f}) if tids else []
    fieldids = sorted({t["field_id"][0] for t in tracking if t.get("field_id")})
    fields = (
        consultar(
            "ir.model.fields", "read", [fieldids], {"fields": ["id", "model", "name", "ttype"]}
        )
        if fieldids
        else []
    )
    meta_fields = consultar("ir.model.fields", "fields_get", [], {"attributes": ["type"]})
    active_fields = consultar(
        "ir.model.fields",
        "search_read",
        [
            [
                ("name", "=", "active"),
                ("model", "in", ["product.product", "product.template", "mrp.bom"]),
            ]
        ],
        {
            "fields": [x for x in ["id", "model", "name", "ttype", "tracking"] if x in meta_fields],
            "limit": 20,
        },
    )
    audit = {
        "mensajes": mensajes,
        "tracking": tracking,
        "campos_tracking": fields,
        "configuracion_active": active_fields,
    }
except xmlrpc.client.Fault as exc:
    # No imprimir faultString: respuestas remotas no son una fuente segura de secretos.
    audit = {"error_tipo": "XMLRPCFault", "codigo": exc.faultCode}


def exacto(valor):
    if isinstance(valor, float):
        return str(valor)
    if isinstance(valor, list):
        return [exacto(v) for v in valor]
    if isinstance(valor, dict):
        return {k: exacto(v) for k, v in valor.items()}
    return valor


print(
    json.dumps(
        exacto(
            {
                "fecha_utc": datetime.now(UTC).isoformat(),
                "boms_activos": boms,
                "lineas": lineas,
                "productos": productos,
                "uoms": uoms,
                "auditoria": audit,
            }
        ),
        ensure_ascii=False,
        indent=2,
    )
)
