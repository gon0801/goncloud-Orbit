"""P13: identidad y medidas digitales; solo read/search_read de XML-RPC."""

import json
import shlex
import socket
import xmlrpc.client
from datetime import UTC, datetime
from pathlib import Path


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
url = config["ODOO_URL"].rstrip("/")
db = config["ODOO_DB"]
pwd = config["ODOO_PASSWORD"]
common = xmlrpc.client.ServerProxy(url + "/xmlrpc/2/common", allow_none=True)
uid = common.authenticate(db, config["ODOO_USER"], pwd, {})
if not uid:
    raise RuntimeError("autenticacion no disponible")
models = xmlrpc.client.ServerProxy(url + "/xmlrpc/2/object", allow_none=True)


def consultar(modelo, metodo, args, kwargs=None):
    if metodo not in {"search_read", "read"}:
        raise ValueError("metodo fuera de allowlist de lectura")
    return models.execute_kw(db, uid, pwd, modelo, metodo, args, kwargs or {})


SET_IDS = [7070, 7106, 7112, 7178, 7184, 7190, 7196, 7208, 7232]
FIELDS = [
    "id",
    "default_code",
    "name",
    "active",
    "product_tmpl_id",
    "uom_id",
    "write_date",
    "product_template_attribute_value_ids",
    "description",
    "description_sale",
    "description_purchase",
    "description_picking",
    "weight",
    "volume",
    "meli_dimensions",
]
productos = consultar("product.product", "read", [SET_IDS], {"fields": FIELDS})
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
componentes = consultar("product.product", "read", [cids], {"fields": FIELDS})
# Familia completa: determina si 16/19/22 son clases distintas de arras/cofres.
familia = consultar(
    "product.product",
    "search_read",
    [["|", ("default_code", "=ilike", "ARR-%"), ("default_code", "=ilike", "COF-%")]],
    {"fields": FIELDS, "context": {"active_test": False}, "limit": 500, "order": "id"},
)
variantes_set = consultar(
    "product.product",
    "search_read",
    [
        [
            ("active", "=", True),
            ("product_tmpl_id", "in", sorted({r["product_tmpl_id"][0] for r in productos})),
        ]
    ],
    {"fields": FIELDS, "limit": 200, "order": "id"},
)
boms_familia = consultar(
    "mrp.bom",
    "search_read",
    [[("active", "=", True), ("product_id", "in", [r["id"] for r in variantes_set])]],
    {
        "fields": ["id", "product_id", "product_tmpl_id", "bom_line_ids", "write_date"],
        "limit": 200,
        "order": "id",
    },
)
lineas_familia = consultar(
    "mrp.bom.line",
    "read",
    [sorted({lid for b in boms_familia for lid in b["bom_line_ids"]})],
    {"fields": ["id", "bom_id", "product_id", "product_qty", "product_uom_id"]},
)
aids = sorted(
    {
        a
        for r in productos + componentes + familia
        for a in r["product_template_attribute_value_ids"]
    }
)
attrs = consultar(
    "product.template.attribute.value",
    "read",
    [aids],
    {"fields": ["id", "name", "attribute_id", "product_attribute_value_id", "product_tmpl_id"]},
)
plantillas = consultar(
    "product.template",
    "read",
    [sorted({r["product_tmpl_id"][0] for r in productos + componentes})],
    {
        "fields": [
            "id",
            "name",
            "description",
            "description_sale",
            "description_purchase",
            "description_picking",
            "attribute_line_ids",
            "write_date",
        ]
    },
)
campos = consultar(
    "ir.model.fields",
    "search_read",
    [[("model", "in", ["product.product", "product.template"])]],
    {
        "fields": ["model", "name", "field_description", "ttype"],
        "limit": 2000,
        "order": "model,name",
    },
)
campos_medida = [
    r
    for r in campos
    if any(
        t in (r["name"] + " " + r["field_description"]).lower()
        for t in [
            "diam",
            "width",
            "height",
            "length",
            "dimension",
            "medida",
            "ancho",
            "alto",
            "largo",
            "espesor",
            "thickness",
        ]
    )
]
proveedores = consultar(
    "product.supplierinfo",
    "search_read",
    [[("product_tmpl_id", "in", sorted({r["product_tmpl_id"][0] for r in componentes}))]],
    {
        "fields": [
            "id",
            "product_tmpl_id",
            "product_id",
            "product_name",
            "product_code",
            "write_date",
        ],
        "limit": 200,
        "order": "id",
    },
)
ids_productos = sorted({r["id"] for r in productos + componentes})
ids_plantillas = sorted({r["product_tmpl_id"][0] for r in productos + componentes})
# Solo metadatos de adjuntos y mensajes de medida de estos productos; sin binarios ni personas.
adjuntos = consultar(
    "ir.attachment",
    "search_read",
    [
        [
            ("res_model", "in", ["product.product", "product.template"]),
            ("res_id", "in", sorted(set(ids_productos + ids_plantillas))),
        ]
    ],
    {
        "fields": [
            "id",
            "name",
            "res_model",
            "res_id",
            "mimetype",
            "description",
            "file_size",
            "write_date",
        ],
        "limit": 200,
        "order": "id",
    },
)
mensajes_medida = consultar(
    "mail.message",
    "search_read",
    [
        [
            ("model", "in", ["product.product", "product.template"]),
            ("res_id", "in", sorted(set(ids_productos + ids_plantillas))),
            ("body", "ilike", "mm"),
        ]
    ],
    {"fields": ["id", "model", "res_id", "subject", "body", "date"], "limit": 100, "order": "id"},
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
                "familia_arras_cofres": familia,
                "variantes_plantillas_set": variantes_set,
                "boms_familia": boms_familia,
                "lineas_familia": lineas_familia,
                "atributos": attrs,
                "plantillas": plantillas,
                "campos_medida": campos_medida,
                "campos_inspeccionados": len(campos),
                "proveedor_referencias": proveedores,
                "adjuntos_metadatos": adjuntos,
                "mensajes_medida": mensajes_medida,
            }
        ),
        ensure_ascii=False,
        indent=2,
    )
)
