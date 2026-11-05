"""P13: concilia diferencias con historia externa usando Decimal, sin escrituras remotas."""

import json
from decimal import Decimal
from pathlib import Path

BASE = Path(__file__).resolve().parent
historia = json.loads((BASE / "historial.json").read_text())
sets = json.loads((BASE.parent / "costos/odoo.json").read_text())
componentes = {p["id"]: p for p in sets["productos"]}
boms = {b["product_id"][0]: b for b in sets["boms"]}
lineas = {linea["id"]: linea for linea in sets["lineas"]}
assert len(boms) == 9


def dinero(valor):
    return Decimal(valor).quantize(Decimal("0.0001"))


salida = []
for product_id, bom in boms.items():
    p = componentes[product_id]
    assert bom["active"] and not bom["operation_ids"] and not bom["byproduct_ids"]
    costos_set = [
        r for r in historia["historial"] if r["sku"] == p["default_code"] and r["valid_to"] is None
    ]
    assert len(costos_set) == 1
    final = costos_set[0]
    fecha = final["valid_from"] or final["created_at"]
    filas = []
    for linea_id in boms[p["id"]]["bom_line_ids"]:
        linea = lineas[linea_id]
        c = componentes[linea["product_id"][0]]
        assert (
            linea["product_uom_id"][0] == c["uom_id"][0] == boms[p["id"]]["product_uom_id"][0] == 1
        )
        assert not linea["bom_product_template_attribute_value_ids"]
        assert not linea["child_bom_id"]
        h = [r for r in historia["historial"] if r["sku"] == c["default_code"]]
        actual = [r for r in h if r["valid_to"] is None]
        anterior = [
            r
            for r in h
            if (r["valid_from"] or r["created_at"]) <= fecha
            and (r["valid_to"] is None or r["valid_to"] > fecha)
        ]
        assert len(actual) == len(anterior) == 1, c["default_code"]
        assert actual[0]["currency"] == anterior[0]["currency"] == final["currency"] == "MXN"
        assert dinero(actual[0]["cost"]) == dinero(c["standard_price"])
        cantidad = Decimal(linea["product_qty"]) / Decimal(boms[p["id"]]["product_qty"])
        filas.append(
            {
                "sku": c["default_code"],
                "cantidad": str(cantidad),
                "costo_en_fecha_set": str(dinero(anterior[0]["cost"])),
                "costo_actual": str(dinero(actual[0]["cost"])),
                "accounting_id_anterior": anterior[0]["id"],
                "accounting_id_actual": actual[0]["id"],
                "vigencia_actual_desde": actual[0]["valid_from"] or actual[0]["created_at"],
            }
        )
    total_antes = sum(Decimal(r["cantidad"]) * Decimal(r["costo_en_fecha_set"]) for r in filas)
    total_hoy = sum(Decimal(r["cantidad"]) * Decimal(r["costo_actual"]) for r in filas)
    assert total_antes == dinero(final["cost"]) == dinero(p["standard_price"])
    cambiados = [r for r in filas if r["costo_en_fecha_set"] != r["costo_actual"]]
    assert len(cambiados) == 1 and cambiados[0]["sku"] in ("COF-22-DOR", "COF-22-PLA")
    salida.append(
        {
            "sku": p["default_code"],
            "producto_odoo": p["id"],
            "costo_publicado": str(dinero(final["cost"])),
            "vigencia_publicada_desde": fecha,
            "componentes": filas,
            "suma_con_precios_historicos": str(total_antes),
            "suma_actual": str(total_hoy),
            "diferencia": str(dinero(final["cost"]) - total_hoy),
            "unico_componente_con_cambio": cambiados[0]["sku"],
            "diferencia_conciliada_con_cofre": True,
        }
    )
print(
    json.dumps(
        {
            "origen_historial_utc": historia["observado_utc"],
            "origen_bom_utc": sets["fecha_utc"],
            "filas": salida,
            "conciliadas": len(salida),
            "limite": "Se valora el BOM actual con precios historicos; no prueba su composicion "
            "original ni la accion o autor del calculo de agosto.",
        },
        ensure_ascii=False,
        indent=2,
    )
)
