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


IDS_ATRIBUTO = [2549, 2556, 2612, 2717, 2724, 2745, 2752, 2780]

filas = consultar(
    "product.template.attribute.value",
    "read",
    [IDS_ATRIBUTO],
    {
        "fields": [
            "id",
            "name",
            "attribute_id",
            "product_attribute_value_id",
            "product_tmpl_id",
            "ptav_active",
        ]
    },
)
boms_arras = consultar(
    "mrp.bom",
    "search_read",
    [
        [
            ("product_tmpl_id", "in", [6, 163]),
            "|",
            ("product_id", "=", False),
            ("product_id", "in", [6, 156]),
        ]
    ],
    {
        "fields": ["id", "active", "product_id", "product_tmpl_id", "bom_line_ids"],
        "context": {"active_test": False},
        "limit": 100,
    },
)
print(
    json.dumps(
        {
            "fecha_utc": datetime.now(UTC).isoformat(),
            "filas": filas,
            "sub_boms_arras_pez": boms_arras,
        },
        ensure_ascii=False,
        indent=2,
    )
)
