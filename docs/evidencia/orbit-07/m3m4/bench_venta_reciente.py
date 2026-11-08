"""ORBIT 07 M4: costo en CPU de la espera de venta reciente.

Compara la resolucion v2 (base) y v3 (candidato) sobre las mismas entradas
congeladas de AUTO-03 E.4. No mide SQL: la lectura de productos es la misma y
la regla nueva solo recorre los `dias_halo` ya leidos, una vez por producto.
No toca produccion ni Amazon.

    uv run --frozen python docs/evidencia/orbit-07/m3m4/bench_venta_reciente.py
"""

from __future__ import annotations

import json
import statistics
import time
from pathlib import Path

from app.optimizer import productos as p

RAIZ = Path(__file__).resolve().parents[4]
E4_MX = RAIZ / "docs/evidencia/auto-03/ejecucion/E/elegibilidad-mx.json"
E4_US = RAIZ / "docs/evidencia/auto-03/ejecucion/E/elegibilidad-us.json"
RONDAS = 25


def _entradas() -> list[p.EntradaProducto]:
    entradas: list[p.EntradaProducto] = []
    for ruta in (E4_MX, E4_US):
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        entradas.extend(p._de_json(e, p.EntradaProducto) for e in datos["entradas"])
    return entradas


def _mide(entradas: list[p.EntradaProducto], politica: str) -> dict:
    tiempos = []
    for _ in range(RONDAS):
        inicio = time.perf_counter()
        for entrada in entradas:
            p.resolver_producto(entrada, politica=politica)
        tiempos.append((time.perf_counter() - inicio) * 1000)
    return {
        "rondas": RONDAS,
        "mediana_ms": round(statistics.median(tiempos), 3),
        "minima_ms": round(min(tiempos), 3),
        "maxima_ms": round(max(tiempos), 3),
    }


def main() -> dict:
    entradas = _entradas()
    dias = sum(len(e.dias_halo) for e in entradas)
    v2 = _mide(entradas, "v2")
    v3 = _mide(entradas, "v3")
    return {
        "referencia": "ORBIT 07 M4: regla v2 (base) contra v3 (candidato)",
        "carga": {
            "productos": len(entradas),
            "dias_halo": dias,
            "fuente": "docs/evidencia/auto-03/ejecucion/E/elegibilidad-{mx,us}.json",
        },
        "v2_base": v2,
        "v3_candidato": v3,
        "diferencia_mediana_ms": round(v3["mediana_ms"] - v2["mediana_ms"], 3),
        "presupuesto_m0_ms": 150,
        "nota": "La regla nueva no agrega SQL: usa los dias recientes ya leidos "
        "(o dias_halo en una entrada congelada), por producto y no por hoja. La "
        "comparacion usa las mismas 368 entradas congeladas para v2 y v3.",
    }


if __name__ == "__main__":
    import sys

    salida = None
    if "--salida" in sys.argv:
        salida = Path(sys.argv[sys.argv.index("--salida") + 1])
    texto = json.dumps(main(), indent=2, ensure_ascii=False)
    if salida is not None:
        salida.write_text(texto + "\n", encoding="utf-8")
    print(texto)
