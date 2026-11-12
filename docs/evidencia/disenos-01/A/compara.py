"""Compara la pagina Decisiones de A.1 (antes) con la nueva (DISEÑOS 01 A).

Uso local, sin red:  python docs/evidencia/disenos-01/A/compara.py
Contra produccion:   python docs/evidencia/disenos-01/A/compara.py --base http://10.13.13.1:8010

Sale 0 solo si el total, las 50 decisiones de las paginas 1 y 2, el JSON del
feed y el encabezado del dia partido coinciden. No escribe nada.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import urllib.request

RAIZ = pathlib.Path(__file__).resolve().parent
ANTES = RAIZ / "antes"
DESPUES = RAIZ / "despues"


def _descarga(base: str, ruta: str) -> str:
    with urllib.request.urlopen(base.rstrip("/") + ruta, timeout=60) as respuesta:
        return respuesta.read().decode("utf-8")


def _ids(html: str) -> list[tuple[str, str]]:
    return re.findall(r"#(\d+) · decision #(\d+)", html)


def _total(html: str) -> int | None:
    encontrado = re.search(r"([\d,]+) decisiones", html)
    return int(encontrado.group(1).replace(",", "")) if encontrado else None


def _dias(html: str) -> list[str]:
    return re.findall(r'<div class="decisiones-dia-cab">\s*<h2 class="num">([^<]+)</h2>', html)


def _fuente(base: str | None, nombre: str) -> str:
    if base:
        return _descarga(base, "/decisiones" + nombre)
    archivo = {
        "": "pagina-1.html",
        "?page=2": "pagina-2.html",
        "?page=3": "pagina-3.html",
    }[nombre]
    return (DESPUES / archivo).read_text(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", help="raiz de produccion; sin el, lee despues/")
    args = ap.parse_args(argv)

    antes_1 = (ANTES / "decisiones-pagina-1.html").read_text(encoding="utf-8")
    antes_2 = (ANTES / "decisiones-pagina-2.html").read_text(encoding="utf-8")
    nuevas = {nombre: _fuente(args.base, nombre) for nombre in ("", "?page=2", "?page=3")}
    nueva_1, nueva_2, nueva_3 = nuevas[""], nuevas["?page=2"], nuevas["?page=3"]

    diferencias: list[str] = []
    if _total(antes_1) != _total(nueva_1):
        diferencias.append(f"total: antes {_total(antes_1)} != despues {_total(nueva_1)}")
    for pagina, viejo, nuevo in (("1", antes_1, nueva_1), ("2", antes_2, nueva_2)):
        ids_viejos, ids_nuevos = _ids(viejo), _ids(nuevo)
        if len(ids_viejos) != len(ids_nuevos):
            diferencias.append(
                f"pagina {pagina}: {len(ids_viejos)} decisiones antes != {len(ids_nuevos)} despues"
            )
        if sorted(ids_viejos) != sorted(ids_nuevos):
            diferencias.append(f"pagina {pagina}: ids/entidades distintos")

    antes_api = json.loads((ANTES / "api-decisiones.json").read_text(encoding="utf-8"))
    after_api = (
        json.loads(_descarga(args.base, "/api/dashboard/decisiones"))
        if args.base
        else json.loads((DESPUES / "api-decisiones.json").read_text(encoding="utf-8"))
    )
    if antes_api != after_api:
        diferencias.append("JSON /api/dashboard/decisiones distinto")

    dias_2, dias_3 = _dias(nueva_2), _dias(nueva_3)
    if not dias_2 or dias_2[-1] != "9 oct" or not dias_3 or dias_3[0] != "9 oct":
        diferencias.append("el dia 9 oct no encabeza el corte entre las paginas 2 y 3")

    print(f"total antes: {_total(antes_1)} · total despues: {_total(nueva_1)}")
    print(
        f"pagina 1: {len(_ids(antes_1))} antes · {len(_ids(nueva_1))} despues; "
        f"pagina 2: {len(_ids(antes_2))} antes · {len(_ids(nueva_2))} despues"
    )
    print(
        "dia partido 9 oct: pagina 2 "
        f"{dias_2[-1] if dias_2 else '—'} · pagina 3 {dias_3[0] if dias_3 else '—'}"
    )
    if diferencias:
        for diferencia in diferencias:
            print("DIFERENCIA:", diferencia)
        return 1
    print("sin diferencias")
    return 0


if __name__ == "__main__":
    sys.exit(main())
