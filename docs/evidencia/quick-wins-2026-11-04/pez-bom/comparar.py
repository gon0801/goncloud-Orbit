"""Compara composiciones materiales completas de BOM activos, sin usar nombres como identidad."""

import json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

BASE = Path(__file__).resolve().parent
old = json.loads((BASE / "desglose.json").read_text())
active = json.loads((BASE / "activos-auditoria.json").read_text())
lineas = {linea["id"]: linea for linea in active["lineas"]}
productos = {p["id"]: p for p in active["productos"]}
uoms = {u["id"]: u for u in active["uoms"]}


def composicion(bom):
    producto = productos[bom["product_id"][0]]
    attrs = set(producto["product_template_attribute_value_ids"])
    unidad_salida = uoms[bom["product_uom_id"][0]]
    salida = Decimal(bom["product_qty"]) / Decimal(unidad_salida["factor"])
    partes = defaultdict(Decimal)
    for lid in bom["bom_line_ids"]:
        linea = lineas[lid]
        if not set(linea["bom_product_template_attribute_value_ids"]) <= attrs:
            continue
        uom = uoms[linea["product_uom_id"][0]]
        if uom["category_id"][0] != 1 or unidad_salida["category_id"][0] != 1:
            raise ValueError("la comparacion requiere categoria Unit")
        partes[linea["product_id"][0]] += (
            Decimal(linea["product_qty"]) / Decimal(uom["factor"]) / salida
        )
    return dict(partes)


resultados = []
for r in old["filas"]:
    if not r["es_variante_con_atributo_modelo"]:
        continue
    referencia = next(
        b for b in r["recetas"] if b["bom_id"] == r["receta_mas_reciente_por_creacion"]
    )
    objetivo = {
        linea["componente_id"]: Decimal(linea["cantidad_por_unidad_final"])
        for linea in referencia["lineas"]
        if linea["aplica"]
    }
    iguales = []
    relacionados = []
    objetivo_ids = set(objetivo)
    for b in active["boms_activos"]:
        partes = composicion(b)
        p = productos[b["product_id"][0]]
        fila = {
            "bom_id": b["id"],
            "producto_id": p["id"],
            "sku": p["default_code"],
            "producto_activo": p["active"],
            "composicion": {str(k): str(v) for k, v in partes.items()},
            "operaciones": b["operation_ids"],
            "subproductos": b["byproduct_ids"],
        }
        if partes == objetivo:
            iguales.append(fila)
        elif objetivo_ids <= set(partes):
            fila["componentes_adicionales"] = [
                {
                    "id": pid,
                    "sku": productos[pid]["default_code"],
                    "nombre": productos[pid]["name"],
                    "cantidad": str(partes[pid]),
                    "costo_vigente": productos[pid]["standard_price"],
                    "moneda": productos[pid]["currency_id"][1],
                }
                for pid in sorted(set(partes) - objetivo_ids)
            ]
            relacionados.append(fila)
    resultados.append(
        {
            "sku_pez": r["sku"],
            "bom_referencia": referencia["bom_id"],
            "composicion_igual_activos": iguales,
            "activos_con_misma_base_y_componentes_extra": relacionados,
        }
    )
(BASE / "comparacion-activos.json").write_text(
    json.dumps(
        {
            "observado_utc": active["fecha_utc"],
            "universo": "BOM activos que contienen cualquier componente de las recetas PEZ "
            "o pertenecen "
            "a los 28 productos NH/PEZ activos ya descubiertos; busqueda paginada completa",
            "boms_activos_examinados": len(active["boms_activos"]),
            "filas": resultados,
        },
        ensure_ascii=False,
        indent=2,
    )
    + "\n"
)
for r in resultados:
    print(
        r["sku_pez"],
        "iguales",
        len(r["composicion_igual_activos"]),
        "base_con_extras",
        len(r["activos_con_misma_base_y_componentes_extra"]),
    )
