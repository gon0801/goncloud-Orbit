"""Resume BOM activos PEZ relacionados, con diferencias materiales explicitas."""

import json
from decimal import Decimal
from pathlib import Path

BASE = Path(__file__).resolve().parent
viejo = json.loads((BASE / "desglose.json").read_text())
nuevo = json.loads((BASE / "activos-auditoria.json").read_text())
productos = {p["id"]: p for p in nuevo["productos"]}
lineas = {linea["id"]: linea for linea in nuevo["lineas"]}
filas = []
for r in viejo["filas"]:
    if not r["es_variante_con_atributo_modelo"]:
        continue
    antigua = next(b for b in r["recetas"] if b["bom_id"] == r["receta_mas_reciente_por_creacion"])
    antiguos = {linea["componente_id"]: linea for linea in antigua["lineas"] if linea["aplica"]}
    sku_candidato = ("NH-SOLO-" + r["sku"].removeprefix("NH-")).replace("CAR-BLA-", "CAR-BLC-")
    encontrados = []
    for b in nuevo["boms_activos"]:
        producto = productos[b["product_id"][0]]
        if producto["default_code"] != sku_candidato:
            continue
        componentes = []
        total = Decimal(0)
        attrs = set(producto["product_template_attribute_value_ids"])
        for lid in b["bom_line_ids"]:
            linea = lineas[lid]
            assert linea["product_uom_id"][0] == b["product_uom_id"][0] == 1
            if not set(linea["bom_product_template_attribute_value_ids"]) <= attrs:
                continue
            p = productos[linea["product_id"][0]]
            assert p["currency_id"][1] == "MXN"
            assert p["uom_id"][0] == 1
            cantidad = Decimal(linea["product_qty"]) / Decimal(b["product_qty"])
            costo = Decimal(p["standard_price"])
            assert costo > 0
            importe = cantidad * costo
            total += importe
            componentes.append(
                {
                    "linea_id": lid,
                    "id": p["id"],
                    "sku": p["default_code"],
                    "cantidad_por_unidad": str(cantidad),
                    "costo_unitario_observado": str(costo),
                    "importe": str(importe),
                    "moneda": "MXN",
                    "atributos_aplicabilidad": linea["bom_product_template_attribute_value_ids"],
                }
            )
        nuevos_ids = {c["id"] for c in componentes}
        encontrados.append(
            {
                "producto_id": producto["id"],
                "sku": producto["default_code"],
                "nombre": producto["name"],
                "producto_activo": producto["active"],
                "atributos_producto_ids": producto["product_template_attribute_value_ids"],
                "bom_id": b["id"],
                "bom_activo": b["active"],
                "componentes": componentes,
                "costo_material_directo": str(total),
                "standard_price_producto": producto["standard_price"],
                "moneda": "MXN",
                "componentes_nuevos": [c["id"] for c in componentes if c["id"] not in antiguos],
                "componentes_que_no_aparecen": sorted(set(antiguos) - nuevos_ids),
            }
        )
    filas.append(
        {
            "sku_meli": r["sku"],
            "bom_historico_comparado": antigua["bom_id"],
            "costo_historico_a_costos_componentes_actuales": antigua["costo_material_por_unidad"],
            "bom_activo_relacionado": encontrados,
            "equivalencia_comercial_confirmada": False,
        }
    )
(BASE / "candidatos-activos.json").write_text(
    json.dumps(
        {
            "observado_utc": nuevo["fecha_utc"],
            "nota": "Si existen BOM activos PEZ de referencias distintas. "
            "Estos son candidatos por familia; la composicion se compara por ID/cantidad/unidad. "
            "No son un mapping comercial probado.",
            "filas": filas,
        },
        ensure_ascii=False,
        indent=2,
    )
    + "\n"
)
for r in filas:
    for c in r["bom_activo_relacionado"]:
        print(
            r["sku_meli"],
            c["sku"],
            c["producto_id"],
            c["bom_id"],
            c["costo_material_directo"],
            "componentes",
            [(x["sku"], x["importe"]) for x in c["componentes"]],
        )
