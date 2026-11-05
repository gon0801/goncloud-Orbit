"""Conciliacion Decimal de magnitudes distintas: costo publicado y material actual del BOM."""

import json
from decimal import Decimal
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATOS = json.loads((BASE / "odoo.json").read_text())
POLITICA = json.loads((BASE / "politica.json").read_text())
VALORACION = json.loads((BASE / "valoracion.json").read_text())
productos = {r["id"]: r for r in DATOS["productos"]}
lineas = {r["id"]: r for r in DATOS["lineas"]}
props = {r["res_id"]: r for r in DATOS["propiedades_precio"] if r["res_id"]}
filas = []
for bom in DATOS["boms"]:
    producto = productos[bom["product_id"][0]]
    assert bom["active"]
    assert not bom["operation_ids"] and not bom["byproduct_ids"]
    assert Decimal(bom["product_qty"]) == 1
    assert bom["product_uom_id"][0] == producto["uom_id"][0] == 1
    total = Decimal(0)
    sin_cofre = Decimal(0)
    cofre = None
    for lid in bom["bom_line_ids"]:
        linea = lineas[lid]
        componente = productos[linea["product_id"][0]]
        assert linea["product_uom_id"][0] == componente["uom_id"][0] == 1
        assert not linea["bom_product_template_attribute_value_ids"]
        assert not linea.get("child_bom_id")
        assert componente["currency_id"][1] == producto["currency_id"][1] == "MXN"
        costo = Decimal(componente["standard_price"]) * Decimal(linea["product_qty"])
        total += costo
        if componente["id"] in [7, 8]:
            cofre = componente
            assert Decimal(linea["product_qty"]) == 1
        else:
            sin_cofre += costo
    assert cofre is not None
    publicado = Decimal(producto["standard_price"]).quantize(Decimal("0.0001"))
    implicito = publicado - sin_cofre
    delta = publicado - total
    assert implicito == Decimal("48.74")
    assert delta == Decimal("48.74") - Decimal(cofre["standard_price"])
    prop_set = props[f"product.product,{producto['id']}"]
    prop_cofre = props[f"product.product,{cofre['id']}"]
    capas = [r for r in VALORACION["capas_cofres_23_24agosto"] if r["product_id"][0] == cofre["id"]]
    assert len(capas) == 1 and capas[0]["create_date"] == prop_cofre["write_date"]
    assert prop_set["write_date"] < prop_cofre["write_date"]
    filas.append(
        {
            "producto_id": producto["id"],
            "sku": producto["default_code"],
            "bom_id": bom["id"],
            "publicado_odoo": str(publicado),
            "material_actual": str(total),
            "diferencia": str(delta),
            "moneda": "MXN",
            "costo_cofre_implicito_en_publicado": str(implicito),
            "cofre_id": cofre["id"],
            "costo_cofre_actual": cofre["standard_price"],
            "propiedad_set_id": prop_set["id"],
            "fecha_escritura_costo_set": prop_set["write_date"],
            "propiedad_cofre_id": prop_cofre["id"],
            "fecha_escritura_costo_cofre": prop_cofre["write_date"],
            "capa_recepcion_cofre_id": capas[0]["id"],
            "politica_configurada": "average / manual_periodic",
            "capas_valoracion_set_observadas": len(
                [
                    r
                    for r in VALORACION["capas_sets_ultimas30"]
                    if r["product_id"][0] == producto["id"]
                ]
            ),
        }
    )
assert len(filas) == 9
(BASE / "resumen.json").write_text(
    json.dumps(
        {
            "observado_odoo_utc": DATOS["fecha_utc"],
            "filas": filas,
            "explicacion": "Los SET conservan su valor publicado de18ago. "
            "El cofre bajo AVCO cambia "
            "tras la recepcion24ago. El costo publicado y la suma actual de materiales "
            "no son la misma magnitud ni se sincronizan automaticamente por el campo.",
            "limite": "AVCO es la politica configurada de SET, "
            "no prueba que su valor retenido venga "
            "de movimientos propios: no hay capas observadas de los nueve SET phantom. "
            "No se atribuye autor ni mecanismo de la asignacion inicial18ago. "
            "El BOM observado tiene write_date12sept: valorarlo con precios18ago "
            "no demuestra la version de receta que existia entonces.",
            "tratamiento": "Conservar standard_price publicado y sus vigencias "
            "en el camino actual; "
            "no invocar recosteo ni forzar igualdad con materiales sin cambiar "
            "explicitamente el contrato de esa magnitud.",
        },
        ensure_ascii=False,
        indent=2,
    )
    + "\n"
)
for r in filas:
    print(r["sku"], r["publicado_odoo"], r["material_actual"], r["diferencia"], "MXN")
