"""Matriz comercial revisable de nueve variantes; sin escrituras productivas."""

import html
import json
from decimal import Decimal
from pathlib import Path

BASE = Path(__file__).resolve().parent
SETS = json.loads((BASE / "sets.json").read_text())
MELI = json.loads((BASE / "meli.json").read_text())
ANTERIOR = json.loads((BASE.parents[1] / "costos-meli/orbit.json").read_text())
FOTOS = json.loads((BASE / "fotos-meli.json").read_text())
ACCOUNTING = json.loads((BASE / "accounting.json").read_text())
PRODUCTOS = {r["id"]: r for r in SETS["productos"]}
LINEAS = {r["id"]: r for r in SETS["lineas"]}
COMPONENTES = {r["id"]: r for r in SETS["componentes"]}
MAPA = {
    "MLM2163407464": 7106,
    "MLM1890653227": 7112,
    "MLM4735255462": 7070,
    "MLM2724304859": 7208,
    "MLM2727437671": 7184,
    "MLM2727363007": 7190,
    "MLM2727328203": 7178,
    "MLM4749600210": 7196,
    "MLM4763716652": 7232,
}
filas = []
for item, pid in MAPA.items():
    meli = next(r for r in MELI["filas"] if r["item_id"] == item)
    sku = next(a["value_name"] for a in meli["atributos"] if a["id"] == "SELLER_SKU")
    producto = PRODUCTOS[pid]
    (bom,) = [b for b in SETS["boms"] if b["product_id"][0] == pid]
    assert producto["active"] and bom["active"]
    assert not bom["byproduct_ids"] and not bom["operation_ids"]
    modelo = [
        a for a in SETS["atributos"] if a["id"] in producto["product_template_attribute_value_ids"]
    ]
    assert [a["name"] for a in modelo] == ["Sello Cristiano"]
    total = Decimal(0)
    componentes = []
    for lid in bom["bom_line_ids"]:
        linea = LINEAS[lid]
        c = COMPONENTES[linea["product_id"][0]]
        assert linea["product_uom_id"][0] == c["uom_id"][0] == bom["product_uom_id"][0] == 1
        assert not linea["bom_product_template_attribute_value_ids"]
        assert c["currency_id"][1] == producto["currency_id"][1] == "MXN"
        cantidad = Decimal(linea["product_qty"]) / Decimal(bom["product_qty"])
        importe = cantidad * Decimal(c["standard_price"])
        total += importe
        componentes.append(
            {
                "id": c["id"],
                "sku": c["default_code"],
                "cantidad_por_unidad": str(cantidad),
                "costo_unitario_texto": c["standard_price"],
                "importe": str(importe),
                "moneda": "MXN",
            }
        )
    listing = next(r["listing"] for r in ANTERIOR["filas"] if r["listing"]["external_id"] == item)
    foto = next(r for r in FOTOS if r["item_id"] == item)
    final = Decimal(producto["standard_price"])
    (costo_accounting,) = [r for r in ACCOUNTING["filas"] if r["sku"] == producto["default_code"]]
    assert Decimal(costo_accounting["cost"]).quantize(Decimal("0.0001")) == final.quantize(
        Decimal("0.0001")
    )
    assert costo_accounting["currency"] == "MXN"
    assert abs(final - final.quantize(Decimal("0.0001"))) < Decimal("0.00001")
    filas.append(
        {
            "listing_id": listing["id"],
            "item_id": item,
            "variante_ext": meli["variante_ext"],
            "seller_sku_meli": sku,
            "odoo_product_id": pid,
            "odoo_sku": producto["default_code"],
            "odoo_nombre": producto["name"],
            "odoo_template_id": producto["product_tmpl_id"][0],
            "bom_id": bom["id"],
            "producto_activo": producto["active"],
            "bom_activo": bom["active"],
            "modelo_odoo": modelo,
            "modelo_meli": meli["combinaciones"],
            "costo_final_odoo_texto": producto["standard_price"],
            "costo_accounting_vigente": costo_accounting,
            "costo_accounting_conciliado_con_odoo": True,
            "observado_accounting_utc": ACCOUNTING["fecha_utc"],
            "costo_final_para_orbit": str(final.quantize(Decimal("0.0001"))),
            "normalizacion_costo": "Precision de app/costs.py::_costo_decimal; "
            "solo ruido binario por debajo de 0.00001",
            "costo_material_calculado": str(total),
            "diferencia_final_menos_material": str(final - total),
            "moneda": "MXN",
            "producto_write_date": producto["write_date"],
            "observado_odoo_utc": SETS["fecha_utc"],
            "observado_meli_utc": meli["observado_utc"],
            "componentes": componentes,
            "foto_meli": foto,
            "foto_odoo_archivo": f"odoo-{pid}.jpg",
            "evidencia_comercial": "Foto especifica de la variante MeLi y foto del producto Odoo "
            "muestran el mismo tipo/color de estuche, cofre y arras; "
            "modelo Sello cristiano corroborado en atributos de ambas fuentes.",
            "limite_visual": "Italia muestra otro angulo del mismo tipo de set."
            if pid == 7208
            else "Coincidencia visual de la presentacion; no es un ensayo fisico de medidas.",
            "discrepancia": "Descripcion generica MeLi indica diametro16/19mm, mientras plantilla "
            "SET Odoo indica22mm; no confundir esa descripcion comun con variante exacta.",
            "ventas_odoo_corroborantes": [
                v["id"] for v in SETS["ventas"] if v["product_id"][0] == pid
            ],
            "dictamen": "Correspondencia comercial sustentada por "
            "variante/fotos/modelo/composicion; "
            "candidato para lote de mapping, sin mutaciones ejecutadas por este agente.",
        }
    )
(BASE / "matriz.json").write_text(
    json.dumps(
        {
            "fecha_utc": SETS["fecha_utc"],
            "filas": filas,
            "conclusion": "Las nueve variantes corresponden al set con cofre. "
            "NH-SOLO excluye el cofre "
            "y no es la referencia respaldada por las fotos comerciales observadas.",
            "costo": "Conservar costo final Odoo y suma material separados. No escoger ni publicar "
            "el menor de ambos por inferencia.",
        },
        ensure_ascii=False,
        indent=2,
    )
    + "\n"
)
html_rows = []
for r in filas:
    titulo = html.escape(
        f"{r['item_id']} / {r['seller_sku_meli']} → {r['odoo_sku']} ({r['odoo_product_id']})"
    )
    html_rows.append(
        f"<section><h2>{titulo}</h2><p>Variante {r['variante_ext']}; BOM activo "
        f"{r['bom_id']}; costo final Odoo {r['costo_final_odoo_texto']} MXN; "
        f"material {r['costo_material_calculado']} MXN.</p><div>"
        f'<figure><img src="imagenes/{r["foto_meli"]["archivo"]}"><figcaption>'
        "Foto especifica PEZ en MeLi</figcaption></figure>"
        f'<figure><img src="imagenes/{r["foto_odoo_archivo"]}"><figcaption>'
        "Producto SET activo Odoo</figcaption></figure></div></section>"
    )
(BASE / "comparacion.html").write_text(
    '<!doctype html><html lang="es"><meta charset="utf-8">'
    "<title>Identidad comercial de nueve variantes PEZ</title><style>body{font:16px system-ui;"
    "max-width:1100px;margin:auto;padding:24px}section{border-top:1px solid #ddd;padding:20px 0}"
    "div{display:flex;gap:24px}figure{margin:0;width:48%}img{max-width:100%;height:320px;"
    "object-fit:contain}h2{font-size:18px}</style>"
    "<h1>PEZ: set con cofre frente a SKU antiguo MeLi</h1>"
    "<p>Revision visual de imagenes originales, sin editarlas. Las nueve fotos de variante "
    "incluyen cofre; NH-SOLO no lo incluye. El modelo de ambas fuentes es Sello cristiano. "
    "La descripcion comun MeLi conserva medidas16/19mm que difieren del nombre Odoo22mm.</p>"
    + "".join(html_rows)
    + "<section><h2>Control: NH-SOLO es otra composicion</h2>"
    '<img src="imagenes/odoo-8513.jpg"><p>NH-SOLO-CAR-BLC-PEZ-DOR, producto8513: '
    "estuche y arras, sin cofre. No es la presentacion observada en las variantes MeLi.</p>"
    "</section></html>"
)
