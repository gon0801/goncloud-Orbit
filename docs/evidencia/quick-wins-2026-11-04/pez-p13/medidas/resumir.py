"""Compone matriz de medidas desde capturas; no consulta ni escribe fuentes externas."""

import hashlib
import json
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent
IDENTIDAD = BASE.parents[1] / "pez-correccion" / "identidad"
PRODUCTO = {
    1562: 7106,
    1563: 7112,
    1578: 7070,
    1579: 7208,
    1582: 7184,
    1583: 7190,
    1584: 7178,
    1585: 7196,
    1590: 7232,
}


def leer(nombre):
    return json.loads((BASE / nombre).read_text())


def guardar(nombre, datos):
    (BASE / nombre).write_text(json.dumps(datos, ensure_ascii=False, indent=2) + "\n")


meli = leer("meli-actual.json")
odoo = leer("odoo-actual.json")
ocr = leer("ocr.json")
fotos = leer("fotos.json")
ocr_por_nombre = {Path(r["archivo"]).name: r["texto"] for r in ocr}
for foto in fotos:
    foto["ocr"] = ocr_por_nombre[Path(foto["archivo"]).name]
    foto["cota_detectada"] = bool(re.search(r"\d+\s*(?:mm|cm)\b", " ".join(foto["ocr"]), re.I))
    foto["conservada"] = foto["cota_detectada"]
    if not foto["conservada"]:
        (BASE / foto["archivo"]).unlink(missing_ok=True)
guardar("fotos.json", fotos)
for r in ocr:
    r["archivo"] = "fotos/" + Path(r["archivo"]).name
guardar("ocr.json", ocr)

anterior_meli = json.loads((IDENTIDAD / "meli.json").read_text())
anterior_set = json.loads((IDENTIDAD / "sets.json").read_text())
productos = {r["id"]: r for r in odoo["productos"]}
familia = {r["default_code"]: r for r in odoo["variantes_plantillas_set"]}
componentes = {r["id"]: r for r in odoo["componentes"] + odoo["familia_arras_cofres"]}
boms = {r["product_id"][0]: r for r in odoo["boms_familia"]}
filas = []
todas_variantes = []
for item in meli["filas"]:
    pid = PRODUCTO[item["listing_id"]]
    producto = productos[pid]
    bom = boms[pid]
    v = next(v for v in item["variantes"] if str(v["id"]) == item["variante_ext"])
    sku = next(a["value_name"] for a in v["attributes"] if a["id"] == "SELLER_SKU")
    lineas = [linea for linea in odoo["lineas_familia"] if linea["bom_id"][0] == bom["id"]]
    componentes_set = [
        {
            "id": linea["product_id"][0],
            "sku": componentes[linea["product_id"][0]]["default_code"],
            "nombre": componentes[linea["product_id"][0]]["name"],
            "cantidad": linea["product_qty"],
            "unidad": linea["product_uom_id"][1],
        }
        for linea in lineas
    ]
    attrs = {a["id"]: a.get("value_name") for a in item["atributos_item"]}
    estuche = next(
        linea.strip()
        for linea in item["descripcion"].splitlines()
        if "Medidas del estuche:" in linea
    )
    foto_cota = next(
        (f for f in fotos if f["listing_id"] == item["listing_id"] and f["cota_detectada"]), None
    )
    prev = next(r for r in anterior_meli["filas"] if r["item_id"] == item["item_id"])
    bom_previo = next(b for b in anterior_set["boms"] if b["product_id"][0] == pid)
    recetas_previas = sorted(
        (linea["product_id"][0], str(linea["product_qty"]), linea["product_uom_id"][0])
        for linea in anterior_set["lineas"]
        if linea["bom_id"][0] == bom_previo["id"]
    )
    recetas_actuales = sorted(
        (linea["product_id"][0], str(linea["product_qty"]), linea["product_uom_id"][0])
        for linea in lineas
    )
    filas.append(
        {
            "listing_id": item["listing_id"],
            "item_id": item["item_id"],
            "variante_ext": item["variante_ext"],
            "seller_sku": sku,
            "modelo_variante": v["attribute_combinations"],
            "estado_meli": item["status"],
            "diametros_texto_comun_mm": sorted(
                set(re.findall(r"\b(\d+)\s*mm\b", item["descripcion"]))
            ),
            "alcance_descripcion": (
                "item completo; seis variantes de diseno, sin atributo diametro por variante"
            ),
            "medidas_estuche_texto": estuche,
            "foto_cotas": foto_cota,
            "dimensiones_logisticas": {
                k: val for k, val in attrs.items() if k.startswith("SELLER_PACKAGE_")
            },
            "shipping_dimensions": item["shipping_dimensions"],
            "odoo_product_id": pid,
            "odoo_sku": producto["default_code"],
            "odoo_nombre": producto["name"],
            "bom_id": bom["id"],
            "componentes": componentes_set,
            "diametro_fisico_confirmado_mm": None,
            "tamano_nominal_odoo": (
                "Familia 22: nombre SET 22mm, componente ARR-22 y cofre COF-22. "
                "Sin campo de diametro ni ficha tecnica dimensional."
            ),
            "misma_descripcion_que_evidencia_previa": item["descripcion"] == prev["descripcion"],
            "misma_composicion_bom_que_evidencia_previa": recetas_actuales == recetas_previas,
            "observado_meli_utc": item["observado_utc"],
            "odoo_write_date": producto["write_date"],
            "causa_documental": (
                "Descripcion comun de publicacion fija diametro16/19 frente a familias de "
                "componentes vigentes; no consta causa historica ni medicion fisica."
            ),
        }
    )
    for variante in item["variantes"]:
        vs = next(a["value_name"] for a in variante["attributes"] if a["id"] == "SELLER_SKU")
        homologo = familia.get(vs.replace("NH-", "SET-", 1))
        receta = boms.get(homologo["id"]) if homologo else None
        cc = [
            componentes[linea["product_id"][0]]["default_code"]
            for linea in odoo["lineas_familia"]
            if receta and linea["bom_id"][0] == receta["id"]
        ]
        todas_variantes.append(
            {
                "listing_id": item["listing_id"],
                "item_id": item["item_id"],
                "variante_ext": str(variante["id"]),
                "seller_sku": vs,
                "diseno": variante["attribute_combinations"],
                "contraste_set_por_codigo": homologo["default_code"] if homologo else None,
                "bom_set": receta["id"] if receta else None,
                "componentes_set": cc,
                "alcance": (
                    "Contraste de familia dimensional; no constituye nuevo mapping autorizado"
                ),
            }
        )

guardar(
    "matriz.json",
    {
        "meli_fecha_utc": meli["fecha_utc"],
        "odoo_fecha_utc": odoo["fecha_utc"],
        "filas": filas,
        "contraste_variantes": todas_variantes,
    },
)
borradores = []
for item in meli["filas"]:
    original = item["descripcion"]
    cambios = []
    patrones = [
        r"(?m)^.*Medidas: Arras de \d+ mm de di[a\u00e1]metro[^\n]*\n",
        r"(?m)^.*Di[a\u00e1]metro de las arras: \d+ mm[^\n]*\n",
        r"Su tama[n\u00f1]o de \d+ mm de di[a\u00e1]metro ofrece una presencia visual "
        r"equilibrada y acorde al ritual\.",
    ]
    neutro = original
    for patron in patrones:
        coincidencias = re.findall(patron, neutro)
        if len(coincidencias) != 1:
            raise ValueError(f"{item['item_id']}: no hay una coincidencia unica para el cambio")
        cambios.append({"antes": coincidencias[0], "despues": ""})
        neutro = re.sub(patron, "", neutro, count=1)
    borradores.append(
        {
            "listing_id": item["listing_id"],
            "item_id": item["item_id"],
            "variante_pez": item["variante_ext"],
            "estado": "BORRADOR NO PUBLICADO",
            "variantes_afectadas_por_descripcion_comun": len(item["variantes"]),
            "cambios_neutros": cambios,
            "descripcion_neutra_completa": neutro,
            "nota_solo_si_se_confirma_diametro_fisico_pez_22mm": (
                "Variante Sello cristiano (PEZ): arras de 22 mm de diametro."
            ),
            "condicion": (
                "No agregar la nota de22mm hasta confirmacion explicita del diametro fisico PEZ. "
                "No asigna tamano a los otros cinco disenos."
            ),
        }
    )
guardar("borradores.json", {"version": 1, "publicado": False, "filas": borradores})
tabla = [
    "| Listing / variante | SET / BOM Odoo | Diametro MeLi comun | Familia arras / cofre | "
    "Estuche: texto y cota digital | Logistica: alto/largo/ancho; peso | Estado |",
    "|---|---|---|---|---|---|---|",
]
for r in filas:
    cc = [c["sku"] for c in r["componentes"] if c["sku"].startswith(("ARR-", "COF-"))]
    dd = r["dimensiones_logisticas"]
    logist = (
        "/".join(
            dd.get(k, "sin dato")
            for k in ["SELLER_PACKAGE_HEIGHT", "SELLER_PACKAGE_LENGTH", "SELLER_PACKAGE_WIDTH"]
        )
        + "; "
        + dd.get("SELLER_PACKAGE_WEIGHT", "sin dato")
    )
    foto = f"[cota]({r['foto_cotas']['archivo']})" if r["foto_cotas"] else "sin cota detectada"
    tabla.append(
        f"| {r['listing_id']} / {r['variante_ext']} | {r['odoo_sku']} / {r['bom_id']} | "
        f"{'/'.join(r['diametros_texto_comun_mm'])} mm | {' + '.join(cc)} | "
        f"{r['medidas_estuche_texto'].lstrip('• ')} {foto} | {logist} | {r['estado_meli']} |"
    )
(BASE / "matriz.md").write_text(
    "# Matriz de las nueve variantes PEZ\n\n"
    + "\n".join(tabla)
    + "\n\nNinguna fila acredita un diametro fisico medido. La cota fotografica es una "
    "anotacion comercial del estuche, no una medicion realizada por el agente. "
    "Logistica es SELLER_PACKAGE_*, no diametro ni medida del estuche.\n"
)
huellas = {}
for nombre in [
    "meli-actual.json",
    "odoo-actual.json",
    "matriz.json",
    "fotos.json",
    "ocr.json",
    "borradores.json",
]:
    huellas[nombre] = hashlib.sha256((BASE / nombre).read_bytes()).hexdigest()
guardar("huellas.json", huellas)
print(
    f"Matriz: {len(filas)} PEZ, {len(todas_variantes)} variantes; "
    f"fotos conservadas: {sum(f['conservada'] for f in fotos)}"
)
