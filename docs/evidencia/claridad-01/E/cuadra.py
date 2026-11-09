"""CLARIDAD 01 E.6: la lista «No evaluados» de Precios cuadra con la Cobertura.

Uso: python3 cuadra.py <json de /api/dashboard/precios>

Por plataforma comprueba que `no_evaluados_filas` tenga tantas filas como
suma `cobertura.no_evaluadas`, con los mismos motivos, y que ninguna fila de
`unidades` traiga `tacos` (el TACoS va una vez por plataforma). Sale 1 si
algo no cuadra.
"""

from __future__ import annotations

import json
import sys
from collections import Counter


def problemas(datos: dict) -> list[str]:
    salida = []
    for plataforma, bloque in datos["plataformas"].items():
        filas = Counter(fila["motivo"] for fila in bloque["no_evaluados_filas"])
        cobertura = Counter()
        for item in bloque["cobertura"]["no_evaluadas"]:
            cobertura[item["motivo"]] += item["count"]
        if filas != cobertura:
            salida.append(
                f"{plataforma}: lista {sum(filas.values())} {dict(filas)}"
                f" != cobertura {sum(cobertura.values())} {dict(cobertura)}"
            )
        con_tacos = [u["sku"] for u in bloque["unidades"] if "tacos" in u]
        if con_tacos:
            salida.append(f"{plataforma}: {len(con_tacos)} filas de unidades traen tacos")
    return salida


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("uso: cuadra.py <json de /api/dashboard/precios>", file=sys.stderr)
        return 2
    with open(argv[0], encoding="utf-8") as archivo:
        datos = json.load(archivo)
    errores = problemas(datos)
    for plataforma, bloque in datos["plataformas"].items():
        cobertura = sum(item["count"] for item in bloque["cobertura"]["no_evaluadas"])
        print(
            f"{plataforma}: no_evaluados_filas={len(bloque['no_evaluados_filas'])}"
            f" cobertura.no_evaluadas={cobertura} no_a_la_venta={bloque.get('no_a_la_venta')}"
        )
    for error in errores:
        print(f"NO CUADRA {error}")
    return 1 if errores else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
