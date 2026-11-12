"""Compara la pagina Decisiones de A.1 (antes) con la nueva (DISEÑOS 01 A).

Uso local, sin red:  python docs/evidencia/disenos-01/A/compara.py
Contra produccion:   python docs/evidencia/disenos-01/A/compara.py --base http://10.13.13.1:8010

Sale 0 solo si el total, las 50 decisiones de las paginas 1 y 2 con sus
valores, el JSON del feed y el encabezado del dia partido coinciden. En local
exige el total exacto; contra produccion permite decisiones nuevas porque la
data crece y exige que cada decision de A.1 conserve sus valores. No escribe
nada.
"""

from __future__ import annotations

import argparse
import contextlib
import html as _html
import json
import pathlib
import re
import sys
import urllib.request

RAIZ = pathlib.Path(__file__).resolve().parent
ANTES = RAIZ / "antes"
DESPUES = RAIZ / "despues"

for _flujo in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError):
        _flujo.reconfigure(encoding="utf-8", errors="replace")


def _descarga(base: str, ruta: str) -> str:
    with urllib.request.urlopen(base.rstrip("/") + ruta, timeout=60) as respuesta:
        return respuesta.read().decode("utf-8")


def _texto(fragmento: str) -> str:
    """Texto visible de un fragmento HTML, con espacios normalizados."""
    return " ".join(_html.unescape(re.sub(r"<[^>]+>", " ", fragmento)).split())


def _bloques(fragmento: str, etiqueta: str, clase: str):
    """Inicio y fin de cada `<etiqueta ... class=... clase ...>` con su cierre."""
    patron = re.compile(rf'<{etiqueta}\b[^>]*class="[^"]*\b{re.escape(clase)}\b[^"]*"[^>]*>')
    for encontrado in patron.finditer(fragmento):
        profundidad = 1
        pos = encontrado.end()
        while profundidad and pos < len(fragmento):
            abre = fragmento.find(f"<{etiqueta}", pos)
            cierra = fragmento.find(f"</{etiqueta}>", pos)
            if cierra < 0:
                break
            if 0 <= abre < cierra:
                profundidad += 1
                pos = abre + len(etiqueta) + 1
            else:
                profundidad -= 1
                pos = cierra + len(etiqueta) + 3
        if profundidad == 0:
            yield encontrado.start(), pos


def _ids(html: str) -> list[tuple[str, str]]:
    return re.findall(r"#(\d+) · decision #(\d+)", html)


def _total(html: str) -> int | None:
    encontrado = re.search(r"([\d,]+) decisiones", html)
    return int(encontrado.group(1).replace(",", "")) if encontrado else None


def _dias(html: str) -> list[str]:
    return re.findall(r'<div class="decisiones-dia-cab">\s*<h2 class="num">([^<]+)</h2>', html)


def _filas_viejas(html: str) -> dict[int, dict[str, str]]:
    """Valores visibles de cada decision en la tabla anterior, por id."""
    filas: dict[int, dict[str, str]] = {}
    for encontrado in re.finditer(r"<tr\b[^>]*>(.*?)</tr>", html, re.S):
        celdas = re.findall(r"<td\b[^>]*>(.*?)</td>", encontrado.group(1), re.S)
        if len(celdas) < 7:
            continue
        cabecera = _texto(celdas[0])
        ident = re.search(r"#(\d+) · decision #(\d+)", cabecera)
        if not ident:
            continue
        filas[int(ident.group(2))] = {
            "entidad": re.sub(r"\s*#\d+ · decision #\d+\s*$", "", cabecera),
            "kind": _texto(celdas[1]).split(" ")[0],
            "search": _texto(celdas[2]),
            "motivo": _texto(celdas[3]),
            "target": _texto(celdas[4]),
            "bid": _texto(celdas[5]),
            "fecha": _texto(celdas[6]),
        }
    return filas


def _contenedores(html: str) -> dict[int, str]:
    """Texto de cada decision en la pagina nueva, por id; incluye el resumen del grupo."""
    mapa: dict[int, str] = {}
    for inicio, fin in _bloques(html, "details", "decisiones-grupo"):
        grupo = html[inicio:fin]
        corte = grupo.find("</summary>")
        resumen = _texto(grupo[: corte + len("</summary>")]) if corte >= 0 else ""
        for i2, f2 in _bloques(grupo, "div", "decisiones-grupo-item"):
            texto = _texto(grupo[i2:f2])
            ident = re.search(r"decision #(\d+)", texto)
            if ident:
                mapa[int(ident.group(1))] = f"{resumen} {texto}"
    for inicio, fin in _bloques(html, "div", "decisiones-fila"):
        texto = _texto(html[inicio:fin])
        ident = re.search(r"decision #(\d+)", texto)
        if ident:
            mapa[int(ident.group(1))] = texto
    return mapa


def _diferencias_valores(
    filas: dict[int, dict[str, str]],
    contenedores: dict[int, str],
    dias: set[str],
) -> list[str]:
    """Compara los valores visibles de cada decision entre A.1 y la pagina nueva."""
    diferencias: list[str] = []
    for id_decision, fila in filas.items():
        texto = contenedores.get(id_decision)
        if texto is None:
            diferencias.append(f"decision #{id_decision}: no aparece en la pagina nueva")
            continue
        entidad = fila["entidad"]
        if entidad and entidad not in texto:
            diferencias.append(f"decision #{id_decision}: entidad {entidad!r} no aparece")
        kind = fila["kind"]
        if kind == "Puja":
            if "Puja" not in texto:
                diferencias.append(f"decision #{id_decision}: falta el chip Puja")
        elif kind and kind not in texto:
            diferencias.append(f"decision #{id_decision}: falta el chip {kind}")
        search = fila["search"]
        if search not in ("", "—") and search not in texto:
            diferencias.append(f"decision #{id_decision}: search term {search!r} no aparece")
        motivo, _, evidencia = fila["motivo"].partition("Cálculo con evidencia:")
        motivo = motivo.strip()
        if motivo and motivo != "—" and motivo not in texto:
            diferencias.append(f"decision #{id_decision}: motivo {motivo!r} no aparece")
        evidencia = evidencia.strip()
        if evidencia and f"Cálculo con evidencia: {evidencia}" not in texto:
            diferencias.append(f"decision #{id_decision}: evidencia {evidencia!r} no aparece")
        target = fila["target"]
        if target in ("", "—"):
            if "Target usado —" not in texto:
                diferencias.append(f"decision #{id_decision}: falta Target usado vacio")
        elif target not in texto:
            diferencias.append(f"decision #{id_decision}: target {target!r} no aparece")
        bid = fila["bid"]
        if bid and bid not in ("— → — —", "—") and bid not in texto:
            diferencias.append(f"decision #{id_decision}: bid {bid!r} no aparece")
        fecha = fila["fecha"]
        if ", " in fecha:
            dia, hora = fecha.split(", ", 1)
            if dia not in dias:
                diferencias.append(f"decision #{id_decision}: dia {dia!r} sin encabezado")
            if hora not in texto:
                diferencias.append(f"decision #{id_decision}: hora {hora!r} no aparece")
    return diferencias


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
    total_antes, total_despues = _total(antes_1), _total(nueva_1)
    if total_despues is None or (total_antes is not None and total_despues < total_antes):
        diferencias.append(f"total: antes {total_antes} > despues {total_despues} (datos perdidos)")
    elif not args.base and total_antes != total_despues:
        diferencias.append(f"total: antes {total_antes} != despues {total_despues}")
    if args.base:
        filas_base = {**_filas_viejas(antes_1), **_filas_viejas(antes_2)}
        contenedores_base: dict[int, str] = {}
        for html_nuevo in (nueva_1, nueva_2, nueva_3):
            contenedores_base.update(_contenedores(html_nuevo))
        dias_base = set(_dias(nueva_1)) | set(_dias(nueva_2)) | set(_dias(nueva_3))
        diferencias.extend(_diferencias_valores(filas_base, contenedores_base, dias_base))
    else:
        for pagina, viejo, nuevo in (("1", antes_1, nueva_1), ("2", antes_2, nueva_2)):
            ids_viejos, ids_nuevos = _ids(viejo), _ids(nuevo)
            if len(ids_viejos) != len(ids_nuevos):
                diferencias.append(
                    f"pagina {pagina}: {len(ids_viejos)} decisiones antes != "
                    f"{len(ids_nuevos)} despues"
                )
            if sorted(ids_viejos) != sorted(ids_nuevos):
                diferencias.append(f"pagina {pagina}: ids/entidades distintos")
            diferencias.extend(
                f"pagina {pagina} {texto}"
                for texto in _diferencias_valores(
                    _filas_viejas(viejo), _contenedores(nuevo), set(_dias(nuevo))
                )
            )

    antes_api = json.loads((ANTES / "api-decisiones.json").read_text(encoding="utf-8"))
    after_api = (
        json.loads(_descarga(args.base, "/api/dashboard/decisiones"))
        if args.base
        else json.loads((DESPUES / "api-decisiones.json").read_text(encoding="utf-8"))
    )
    if args.base:
        nuevos = {int(item["id"]): item for item in after_api.get("items", [])}
        for item in antes_api.get("items", []):
            if int(item["id"]) in nuevos and nuevos[int(item["id"])] != item:
                diferencias.append(f"JSON decision #{item['id']} distinto")
    elif antes_api != after_api:
        diferencias.append("JSON /api/dashboard/decisiones distinto")

    dias_2, dias_3 = _dias(nueva_2), _dias(nueva_3)
    if not dias_2 or dias_2[-1] != "9 oct" or not dias_3 or dias_3[0] != "9 oct":
        diferencias.append("el dia 9 oct no encabeza el corte entre las paginas 2 y 3")

    print(f"total antes: {_total(antes_1)} · total despues: {_total(nueva_1)}")
    if (
        args.base
        and total_despues is not None
        and total_antes is not None
        and total_despues > total_antes
    ):
        print(f"nuevas decisiones desde A.1: {total_despues - total_antes}")
    print(
        f"pagina 1: {len(_ids(antes_1))} antes · {len(_ids(nueva_1))} despues; "
        f"pagina 2: {len(_ids(antes_2))} antes · {len(_ids(nueva_2))} despues"
    )
    print(
        "valores por decision: pagina 1 "
        f"{len(_filas_viejas(antes_1))} · pagina 2 {len(_filas_viejas(antes_2))}"
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
