"""Lee funciones instaladas de costo; no importa Odoo ni invoca metodos de negocio."""

import ast
import hashlib
import json
from pathlib import Path

FUENTES = {
    "/usr/lib/python3/dist-packages/odoo/addons/mrp_account/models/product.py": {
        "button_bom_cost",
        "_set_price_from_bom",
        "_compute_bom_price",
    },
    "/usr/lib/python3/dist-packages/odoo/addons/stock_account/models/stock_move.py": {
        "product_price_update_before_done"
    },
    "/usr/lib/python3/dist-packages/odoo/addons/uom/models/uom_uom.py": {"_compute_price"},
    "/mnt/extra-addons/bom_total_cost/models/mrp_bom.py": {"_compute_bom_cost"},
    "/mnt/extra-addons/bom_total_cost/models/mrp_bom_line.py": {"_onchange_product_id"},
}
salida = []
for ruta, funciones in FUENTES.items():
    path = Path(ruta)
    datos = path.read_bytes()
    tree = ast.parse(datos)
    for nodo in ast.walk(tree):
        if isinstance(nodo, ast.FunctionDef) and nodo.name in funciones:
            salida.append(
                {
                    "ruta": ruta,
                    "archivo_sha256": hashlib.sha256(datos).hexdigest(),
                    "metodo": nodo.name,
                    "linea": nodo.lineno,
                    "codigo_ast": ast.unparse(nodo),
                }
            )
path = Path("/usr/lib/python3/dist-packages/odoo/addons/product/models/product_product.py")
datos = path.read_bytes()
for nodo in ast.walk(ast.parse(datos)):
    if isinstance(nodo, ast.Assign) and any(
        isinstance(t, ast.Name) and t.id == "standard_price" for t in nodo.targets
    ):
        salida.append(
            {
                "ruta": str(path),
                "archivo_sha256": hashlib.sha256(datos).hexdigest(),
                "campo": "standard_price",
                "linea": nodo.lineno,
                "codigo_ast": ast.unparse(nodo),
            }
        )
print(json.dumps(salida, ensure_ascii=False, indent=2))
