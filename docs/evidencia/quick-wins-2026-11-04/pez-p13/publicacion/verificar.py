"""Compara snapshots GET de MeLi con el lote autorizado, sin llamadas externas.

python verificar.py despues-canario.json --item MLM2163407464
python verificar.py despues.json
"""

import argparse
import json
from decimal import Decimal
from pathlib import Path

BASE = Path(__file__).resolve().parent


def leer(nombre):
    return json.loads((BASE / nombre).read_text(), parse_float=Decimal)


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("snapshot")
parser.add_argument("--item", action="append")
args = parser.parse_args()
lote = leer("lote.json")["filas"]
seleccion = {f["item_id"] for f in lote} if args.item is None else set(args.item)
antes = leer("antes.json")
despues = leer(args.snapshot)
previos = {r["item"]["id"]: r for r in antes["filas"]}
actuales = {r["item"]["id"]: r for r in despues["filas"]}
assert set(previos) == set(actuales) == {f["item_id"] for f in lote}
assert seleccion and seleccion <= set(previos)
assert antes["seller_id"] == despues["seller_id"]
filas = []
for fila in lote:
    item = fila["item_id"]
    a, b = previos[item], actuales[item]
    assert a["descripcion"]["plain_text"] == fila["antes"], item
    esperado = fila["despues"] if item in seleccion else fila["antes"]
    leido = b["descripcion"]["plain_text"]
    assert leido in (esperado, esperado.replace("\t", " ")), item
    cambios = {
        campo: {"antes": a["item"].get(campo), "despues": b["item"].get(campo)}
        for campo in sorted(set(a["item"]) | set(b["item"]))
        if a["item"].get(campo) != b["item"].get(campo)
    }
    # Se conservan y muestran las dos fechas que MeLi renovo en el canario.
    # Ningun otro campo del item, incluidas todas sus variantes, se descarta.
    assert set(cambios) <= {"last_updated", "expiration_time"}, (item, cambios)
    filas.append(
        {
            "item_id": item,
            "texto": "aplicado" if item in seleccion else "original",
            "tabs_convertidos_a_espacio": esperado.count("\t") if leido != esperado else 0,
            "status": b["item"]["status"],
            "sub_status": b["item"]["sub_status"],
            "variantes_intactas": len(b["item"]["variations"]),
            "cambios_item": cambios,
        }
    )
print(
    json.dumps(
        {
            "antes_utc": antes["observado_utc"],
            "despues_utc": despues["observado_utc"],
            "descripciones_aplicadas": len(seleccion),
            "descripciones_originales": len(lote) - len(seleccion),
            "precios_existencias_estados_skus_variantes_intactos": True,
            "filas": filas,
        },
        ensure_ascii=False,
        indent=2,
    )
)
