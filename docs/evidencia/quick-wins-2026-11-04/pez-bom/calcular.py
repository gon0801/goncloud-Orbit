"""Desglose Decimal de los BOM observados; no selecciona ni modifica BOM en Odoo."""

import json
from decimal import Decimal
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATOS = json.loads((BASE / "odoo.json").read_text())
PRODUCTOS = {p["id"]: p for p in DATOS["productos"]}
LINEAS = {linea["id"]: linea for linea in DATOS["lineas"]}
UOMS = {u["id"]: u for u in DATOS["uoms"]}


def decimal(valor):
    return Decimal(str(valor))


def convertir(cantidad, origen, destino):
    a, b = UOMS[origen], UOMS[destino]
    if a["category_id"][0] != b["category_id"][0]:
        raise ValueError("categorias de unidades incompatibles")
    return cantidad / decimal(a["factor"]) * decimal(b["factor"])


def calcular(bom, producto):
    if bom["byproduct_ids"] or bom["operation_ids"]:
        raise ValueError("este desglose requiere revisar operaciones/subproductos")
    salida_en_ud_producto = convertir(
        decimal(bom["product_qty"]), bom["product_uom_id"][0], producto["uom_id"][0]
    )
    if salida_en_ud_producto <= 0:
        raise ValueError("rendimiento de BOM no positivo")
    desglose = []
    total = Decimal(0)
    atributos = set(producto["product_template_attribute_value_ids"])
    for lid in bom["bom_line_ids"]:
        linea = LINEAS[lid]
        requeridos = set(linea["bom_product_template_attribute_value_ids"])
        aplica = requeridos <= atributos
        if not aplica:
            desglose.append(
                {"linea_id": lid, "aplica": False, "atributos_requeridos": sorted(requeridos)}
            )
            continue
        componente = PRODUCTOS[linea["product_id"][0]]
        subboms = [
            b["id"]
            for b in DATOS["boms"]
            if (b["product_id"] and b["product_id"][0] == componente["id"])
            or (not b["product_id"] and b["product_tmpl_id"][0] == componente["product_tmpl_id"][0])
        ]
        if subboms:
            raise ValueError(f"componente {componente['id']} requiere revisar sub-BOM {subboms}")
        costo = decimal(componente["standard_price"])
        if costo <= 0:
            raise ValueError(f"componente {componente['id']} sin costo positivo")
        if componente["currency_id"][1] != "MXN":
            raise ValueError("moneda de componente inesperada")
        cantidad = (
            convertir(
                decimal(linea["product_qty"]), linea["product_uom_id"][0], componente["uom_id"][0]
            )
            / salida_en_ud_producto
        )
        importe = cantidad * costo
        total += importe
        desglose.append(
            {
                "linea_id": lid,
                "aplica": True,
                "atributos_requeridos": sorted(requeridos),
                "componente_id": componente["id"],
                "sku": componente["default_code"],
                "nombre": componente["name"],
                "cantidad_linea": linea["product_qty"],
                "uom_linea_id": linea["product_uom_id"][0],
                "uom_costo_id": componente["uom_id"][0],
                "cantidad_por_unidad_final": str(cantidad),
                "costo_unitario_vigente_observado": str(costo),
                "importe": str(importe),
                "moneda": "MXN",
                "costo_write_date": componente["write_date"],
                "sub_boms": subboms,
            }
        )
    return {
        "bom_id": bom["id"],
        "active": bom["active"],
        "sequence": bom["sequence"],
        "product_id": producto["id"],
        "create_date": bom["create_date"],
        "write_date": bom["write_date"],
        "salida_por_lote": bom["product_qty"],
        "salida_uom_id": bom["product_uom_id"][0],
        "salida_en_unidad_producto": str(salida_en_ud_producto),
        "costo_material_por_unidad": str(total) if any(l["aplica"] for l in desglose) else None,
        "moneda": "MXN",
        "lineas": desglose,
    }


filas = []
for producto in PRODUCTOS.values():
    if producto["id"] not in DATOS["raices"]:
        continue
    boms = [
        b
        for b in DATOS["boms"]
        if (b["product_id"] and b["product_id"][0] == producto["id"])
        or (not b["product_id"] and b["product_tmpl_id"][0] == producto["product_tmpl_id"][0])
    ]
    recetas = [calcular(b, producto) for b in boms]
    if recetas:
        recientes = sorted(recetas, key=lambda r: (r["create_date"], r["bom_id"]), reverse=True)
        actual_atributo = bool(producto["product_template_attribute_value_ids"])
        filas.append(
            {
                "sku": producto["default_code"],
                "product_id": producto["id"],
                "template_id": producto["product_tmpl_id"][0],
                "atributos": producto["product_template_attribute_value_ids"],
                "es_variante_con_atributo_modelo": actual_atributo,
                "recetas": recetas,
                "receta_mas_reciente_por_creacion": recientes[0]["bom_id"],
                "costo_receta_mas_reciente": recientes[0]["costo_material_por_unidad"],
                "totales_distintos": sorted(
                    {
                        r["costo_material_por_unidad"]
                        for r in recetas
                        if r["costo_material_por_unidad"] is not None
                    }
                ),
                "seleccion_operativa_en_odoo": None,
            }
        )

(BASE / "desglose.json").write_text(
    json.dumps(
        {
            "observado_odoo_utc": DATOS["fecha_utc"],
            "modo": "calculo local Decimal, sin mutaciones",
            "metodo": "Cada BOM se calcula por separado. La mas reciente por creacion es una "
            "referencia explicita de comparacion, no una seleccion activa en Odoo.",
            "formulas": {
                "conversion_uom": "cantidad / factor_origen * factor_destino",
                "costo_unidad": "SUM(cantidad_en_ud_componente * costo_vigente_componente) "
                "/ salida_BOM_en_ud_producto",
            },
            "filas": filas,
        },
        ensure_ascii=False,
        indent=2,
    )
    + "\n"
)
for r in filas:
    if r["es_variante_con_atributo_modelo"]:
        print(
            r["sku"],
            r["product_id"],
            r["costo_receta_mas_reciente"],
            "MXN",
            "BOM",
            r["receta_mas_reciente_por_creacion"],
            "alternativas",
            r["totales_distintos"],
        )
