"""Construye matriz revisable desde respuestas ya capturadas; sin red ni escrituras externas."""

import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
odoo = json.loads((BASE / "odoo-detalle.json").read_text())
meli = json.loads((BASE / "meli-identidad.json").read_text())
filas = []
for item in meli["filas"]:
    sku = next(a["value_name"] for a in item["atributos"] if a["id"] == "SELLER_SKU")
    exactos = [r for r in odoo["productos_exactos"] if r["default_code"] == sku]
    assert len(exactos) == 3
    assert all(not r["active"] and r["standard_price"] == 0 for r in exactos)
    ids = {r["id"] for r in exactos}
    tids = {r["product_tmpl_id"][0] for r in exactos}
    boms = [
        b
        for b in odoo["boms"]
        if (b["product_id"] and b["product_id"][0] in ids)
        or (not b["product_id"] and b["product_tmpl_id"][0] in tids)
    ]
    assert all(not b["active"] for b in boms)
    moderno = next(r for r in exactos if r["product_template_attribute_value_ids"])
    candidato_sku = ("NH-SOLO-" + sku.removeprefix("NH-")).replace("CAR-BLA-", "CAR-BLC-")
    candidatos = [
        r for r in odoo["aliases_nh_pez"] if r["default_code"] == candidato_sku and r["active"]
    ]
    filas.append(
        {
            "item_id": item["item_id"],
            "variante_ext": item["variante_ext"],
            "sku_meli": sku,
            "modelo_meli": item["combinaciones"],
            "estado_meli": item["status"],
            "stock_variante": item["stock_variante"],
            "observado_meli_utc": item["observado_utc"],
            "odoo_ids_misma_referencia": sorted(ids),
            "odoo_variante_con_atributo": moderno["id"],
            "atributo_odoo": "Sello Cristiano",
            "odoo_template_id": moderno["product_tmpl_id"][0],
            "activos_exactos": 0,
            "standard_price_exactos": "0.0",
            "moneda_fuente": "MXN",
            "costo_utilizable": None,
            "vigencia_costo_utilizable": None,
            "boms_aplicables": [
                {"id": b["id"], "active": b["active"], "write_date": b["write_date"]} for b in boms
            ],
            "candidatos_sin_equivalencia_probada": [
                {
                    "id": r["id"],
                    "sku": r["default_code"],
                    "name": r["name"],
                    "costo_fuente_texto": str(r["standard_price"]),
                    "moneda": r["currency_id"][1],
                }
                for r in candidatos
            ],
            "decision_usuario": "PEZ si se vende; conservar variantes",
            "dato_para_desbloquear": "ID Odoo vigente de esta variante o confirmacion de su "
            "reactivacion/receta completa, costo positivo y fecha efectiva",
            "mutacion_autorizada_por_este_lote": False,
        }
    )
(BASE / "matriz.json").write_text(
    json.dumps(
        {
            "observado_odoo_utc": odoo["fecha_utc"],
            "decision_fuente": 'usuario en esta conversacion: "pez si se vende"',
            "filas": filas,
            "operaciones_preparadas": [],
            "motivo_sin_lote": "Identidad comercial confirmada; faltan producto vigente y costo "
            "positivo respaldados. No hay escritura ni reversa financiera segura "
            "que preparar con estos datos.",
        },
        ensure_ascii=False,
        indent=2,
    )
    + "\n"
)
