#!/usr/bin/env python3
"""Compara la pagina Contribucion anterior con la nueva (DISENOS 01, bloque C).

Modo local: compara el HTML nuevo ya renderizado en `despues/contribucion.html`
con el JSON y la pagina de antes. Modo `--base`: baja el JSON y el HTML de una
instancia viva; el JSON es la pagina de antes (no cambia) y el HTML, la nueva.

Solo usa la biblioteca estandar: no necesita el entorno del proyecto ni psycopg.

Uso:
    python docs/evidencia/disenos-01/C/compara.py
    python docs/evidencia/disenos-01/C/compara.py --html <html-nuevo>
    python docs/evidencia/disenos-01/C/compara.py --base http://10.13.13.1:8010
"""

from __future__ import annotations

import argparse
import contextlib
import html
import json
import re
import sys
import urllib.request
from decimal import Decimal
from pathlib import Path

AQUI = Path(__file__).resolve().parent
ANTES = AQUI / "antes" / "api-contribucion.json"
ANTES_HTML = AQUI / "antes" / "contribucion.html"
DESPUES = AQUI / "despues" / "contribucion.html"
RECORTE = AQUI / "despues" / "contribucion-recorte.html"
NOMBRES_RECORTE = ("Arras Manual", "AC - Category Exact - MX")
NUMERO = re.compile(r"-?[\d,]+\.\d{2}")
FECHA = re.compile(r"\d{4}-\d{2}-\d{2}")
FILA_VIEJA = re.compile(
    r"<tr>\s*<td data-dato-libre>(.*?)</td>\s*<td[^>]*>(.*?)</td>\s*"
    r"<td[^>]*>(.*?)</td>\s*</tr>",
    re.S,
)
CHIP_MULTILISTING = "precio min multilisting"
FX_NEAREST_PRIOR = "del último día con dato"
ETIQUETA_CLASE = {"pos": "Positiva", "cruza": "Cruza 0", "neg": "Negativa", "sin": "Sin dato"}


def _limpiar(fragmento: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragmento)).split())


def _dinero(valor) -> str:
    if valor is None:
        return "—"
    return f"{Decimal(str(valor)):,.2f}"


def _seccion(texto: str, plataforma: str) -> str:
    patron = re.compile(
        rf'<section class="tarjeta contribucion-tabla-tarjeta" '
        rf'aria-labelledby="h-contribucion-{re.escape(plataforma)}".*?</section>',
        re.S,
    )
    encontrada = patron.search(texto)
    if not encontrada:
        raise SystemExit(f"no encontre la tabla de {plataforma} en el HTML")
    return encontrada.group(0)


def _tarjeta(texto: str, plataforma: str) -> str:
    patron = re.compile(
        rf'<article class="tarjeta contribucion-tarjeta" '
        rf'id="contribucion-card-{re.escape(plataforma)}".*?</article>',
        re.S,
    )
    encontrada = patron.search(texto)
    if not encontrada:
        raise SystemExit(f"no encontre la tarjeta de {plataforma} en el HTML")
    return encontrada.group(0)


def _filas_nuevas(seccion: str) -> dict[str, str]:
    filas: dict[str, str] = {}
    for fila in re.findall(r"<tr>.*?</tr>", seccion, re.S):
        nombre = re.search(r"<td data-dato-libre>(.*?)</td>", fila, re.S)
        if nombre:
            filas[_limpiar(nombre.group(1))] = fila
    return filas


def _filas_viejas() -> dict[str, dict[str, str]]:
    if not ANTES_HTML.is_file():
        return {}
    filas: dict[str, dict[str, str]] = {}
    for nombre, contribucion, datos in FILA_VIEJA.findall(ANTES_HTML.read_text(encoding="utf-8")):
        limpio = _limpiar(nombre)
        filas[limpio] = {
            "contribucion": _limpiar(contribucion),
            "datos": _limpiar(datos),
        }
    return filas


def _textos_viejos(fila: dict[str, str]) -> list[str]:
    contribucion = fila["contribucion"]
    datos = fila["datos"]
    tokens = NUMERO.findall(contribucion) + FECHA.findall(datos)
    if CHIP_MULTILISTING in contribucion or CHIP_MULTILISTING in datos:
        tokens.append(CHIP_MULTILISTING)
    if "Tipo de cambio aproximado" in datos:
        tokens.append("Tipo de cambio aproximado:")
    if "parcial:" in contribucion:
        tokens.append("parcial:")
    if contribucion.startswith("—"):
        tokens.append("—")
    return tokens


def _bajar(base: str) -> tuple[dict, str]:
    with urllib.request.urlopen(f"{base.rstrip('/')}/api/dashboard/contribucion", timeout=60) as r:
        datos = json.loads(r.read().decode("utf-8"))
    with urllib.request.urlopen(f"{base.rstrip('/')}/contribucion", timeout=60) as r:
        html_pagina = r.read().decode("utf-8")
    return datos, html_pagina


def _clase(lo: Decimal, hi: Decimal) -> str:
    if lo > 0:
        return "pos"
    if hi < 0:
        return "neg"
    return "cruza"


def _nice(valor: Decimal) -> Decimal:
    if valor <= 0:
        return Decimal(0)
    base = Decimal(10) ** valor.adjusted()
    for paso in (Decimal(1), Decimal(2), Decimal("2.5"), Decimal(5), Decimal(10)):
        if paso * base >= valor:
            return paso * base
    return Decimal(10) * base


def _escala(filas: list[tuple[Decimal, Decimal]]) -> tuple[Decimal, Decimal, Decimal]:
    lows = [lo for lo, _ in filas]
    highs = sorted((hi for _, hi in filas), reverse=True)
    minimo = -_nice(max(Decimal(0), -min(Decimal(0), min(lows))))
    tope = highs[0]
    if len(highs) > 1 and highs[0] > highs[1] * 3:
        tope = highs[1] * Decimal("1.1")
    maximo = _nice(max(Decimal(0), tope))
    if maximo == minimo:
        maximo = minimo + Decimal(1)
    cero = (Decimal(0) - minimo) / (maximo - minimo) * 100
    return minimo, maximo, cero


def _texto_eje(valor: Decimal) -> str:
    return _dinero(valor).replace(".00", "") or "0"


def _comparar(datos: dict, pagina: str) -> tuple[int, list[str]]:  # noqa: PLR0915
    viejas = _filas_viejas()
    total = 0
    faltan: list[str] = []

    def exige(cond: bool, descripcion: str) -> None:
        nonlocal total
        total += 1
        if not cond:
            faltan.append(descripcion)

    for plataforma, p in (datos.get("plataformas") or {}).items():
        filas = p.get("filas") or []
        seccion = _seccion(pagina, plataforma)
        tarjeta = _tarjeta(pagina, plataforma)
        nuevas = _filas_nuevas(seccion)
        exige(f"h-contribucion-{plataforma}" in pagina, f"{plataforma}: cabecera")
        if not filas:
            exige(
                "Sin campañas con actividad madura en la ventana." in seccion,
                f"{plataforma}: vacio",
            )
            continue
        exige('class="contribucion-tabla"' in seccion, f"{plataforma}: tabla")
        exige("contribucion-eje" in seccion, f"{plataforma}: eje")
        exige(len(nuevas) == len(filas), f"{plataforma}: numero de filas")
        rangos = [
            (Decimal(f["contrib_sin_halo"]), Decimal(f["contrib_con_halo"]))
            for f in filas
            if f.get("contrib_sin_halo") is not None and f.get("contrib_con_halo") is not None
        ]
        minimo, maximo, cero = _escala(rangos)
        exige(_texto_eje(maximo) in seccion, f"{plataforma}: eje maximo")
        if Decimal(cero).quantize(Decimal(1)) > 12:
            exige(_texto_eje(minimo) in seccion, f"{plataforma}: eje minimo")
        conteos = {"pos": 0, "cruza": 0, "neg": 0, "sin": 0}
        for fila in filas:
            nombre = fila.get("nombre") or "—"
            nueva = nuevas.get(nombre)
            exige(nueva is not None, f"{plataforma}: fila {nombre}")
            if nueva is None:
                continue
            texto_nuevo = _limpiar(nueva)
            sin_halo = fila.get("contrib_sin_halo")
            con_halo = fila.get("contrib_con_halo")
            motivo = fila.get("motivo_ausencia")
            if sin_halo is None or con_halo is None:
                conteos["sin"] += 1
                esperado = f"— ({motivo})" if motivo else "—"
                exige(esperado in texto_nuevo, f"{plataforma}: ausencia {nombre}")
                exige(
                    "contribucion-barra" not in nueva,
                    f"{plataforma}: fila sin dato no dibuja barra ({nombre})",
                )
            else:
                lo = Decimal(sin_halo)
                hi = Decimal(con_halo)
                clase = _clase(lo, hi)
                conteos[clase] += 1
                rango = f"{_dinero(lo)} .. {_dinero(hi)}"
                exige(rango in texto_nuevo, f"{plataforma}: rango {nombre}")
                exige(
                    f"contribucion-barra-{clase}" in nueva,
                    f"{plataforma}: clase {clase} ({nombre})",
                )
                if lo == hi:
                    exige("contribucion-punto" in nueva, f"{plataforma}: punto {nombre}")
                elif hi > maximo and lo >= maximo:
                    exige(
                        "contribucion-fuera" in nueva,
                        f"{plataforma}: marca de borde {nombre}",
                    )
                    exige(
                        "contribucion-rango" not in nueva,
                        f"{plataforma}: rango fuera de escala {nombre}",
                    )
                else:
                    exige("contribucion-rango" in nueva, f"{plataforma}: barra {nombre}")
                if hi > maximo:
                    exige(
                        f"▸ valor real: {_dinero(hi)}" in nueva,
                        f"{plataforma}: valor real {nombre}",
                    )
                else:
                    exige(
                        "▸ valor real" not in nueva,
                        f"{plataforma}: recorte indebido {nombre}",
                    )
                if fila.get("precio_min_multilisting"):
                    exige(CHIP_MULTILISTING in texto_nuevo, f"{plataforma}: multilisting {nombre}")
                if fila.get("fx_source") == "nearest_prior":
                    fx = "Tipo de cambio aproximado: " + FX_NEAREST_PRIOR
                    exige(fx in texto_nuevo, f"{plataforma}: fx {nombre}")
                if motivo:
                    exige(f"parcial: {motivo}" in texto_nuevo, f"{plataforma}: parcial {nombre}")
            desde = fila.get("metric_date_from")
            hasta = fila.get("metric_date_to")
            if desde and hasta:
                exige(f"{desde} .. {hasta}" in texto_nuevo, f"{plataforma}: fechas {nombre}")
            vieja = viejas.get(nombre)
            if vieja is not None:
                for token in _textos_viejos(vieja):
                    exige(token in texto_nuevo, f"{plataforma}: antes {nombre}: {token!r}")
            else:
                exige(False, f"{plataforma}: fila vieja {nombre}")
        for clase, n in conteos.items():
            if n:
                exige(
                    f"contribucion-seg-{clase}" in tarjeta,
                    f"{plataforma}: segmento {clase}",
                )
                exige(
                    f'{ETIQUETA_CLASE[clase]} <span class="num">{n}</span>' in tarjeta,
                    f"{plataforma}: conteo {clase} = {n}",
                )
        if not conteos["sin"]:
            exige("contribucion-seg-sin" not in tarjeta, f"{plataforma}: seg sin dato")
    if viejas:
        plataformas = (datos.get("plataformas") or {}).values()
        total_json = sum(len(p.get("filas") or []) for p in plataformas)
        exige(total_json == len(viejas), "total de filas contra antes")
    return total, faltan


def _comparar_recorte() -> tuple[int, list[str]]:
    """Caso bloqueante h_c4b8e201: todo el rango queda fuera de la escala."""
    if not RECORTE.is_file():
        raise SystemExit(f"no existe {RECORTE}")
    datos = json.loads(ANTES.read_text(encoding="utf-8"))
    plataforma = datos["plataformas"]["amazon_mx"]
    plataforma["filas"] = [
        fila for fila in plataforma["filas"] if fila["nombre"] in NOMBRES_RECORTE
    ]
    filas = plataforma["filas"]
    rangos = [
        (Decimal(f["contrib_sin_halo"]), Decimal(f["contrib_con_halo"]))
        for f in filas
        if f.get("contrib_sin_halo") is not None and f.get("contrib_con_halo") is not None
    ]
    _, maximo, _ = _escala(rangos)
    pagina = RECORTE.read_text(encoding="utf-8")
    seccion = _seccion(pagina, "amazon_mx")
    nuevas = _filas_nuevas(seccion)
    total = 0
    faltan: list[str] = []

    def exige(cond: bool, descripcion: str) -> None:
        nonlocal total
        total += 1
        if not cond:
            faltan.append(descripcion)

    arras = nuevas.get("Arras Manual") or ""
    ac = nuevas.get("AC - Category Exact - MX") or ""
    exige(_texto_eje(maximo) in seccion, "eje maximo recortado")
    exige("contribucion-rango" in arras, "Arras Manual conserva su barra")
    exige("contribucion-fuera" not in arras, "Arras Manual no es marca de borde")
    exige("contribucion-fuera" in ac, "AC conserva la marca de borde")
    exige("contribucion-rango" not in ac, "AC no finge un rango dentro de la escala")
    exige("contribucion-linea-cero" in ac, "AC conserva la linea en cero")
    exige("▸ valor real: 122,380.55" in ac, "AC conserva el valor real")
    exige("34,765.96 .. 122,380.55" in _limpiar(ac), "AC conserva el rango textual")
    return total, faltan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", help="instancia viva; sin el, usa antes/ y despues/")
    parser.add_argument("--html", help="HTML nuevo ya renderizado")
    parser.add_argument(
        "--recorte",
        action="store_true",
        help="caso de una campana cuyo rango completo queda fuera de la escala",
    )
    args = parser.parse_args(argv)
    with contextlib.suppress(AttributeError):
        sys.stdout.reconfigure(encoding="utf-8")

    if args.recorte:
        total, faltan = _comparar_recorte()
        origen = str(RECORTE)
    elif args.base:
        datos, pagina = _bajar(args.base)
        origen = f"vivo {args.base}"
        total, faltan = _comparar(datos, pagina)
    else:
        datos = json.loads(ANTES.read_text(encoding="utf-8"))
        ruta = Path(args.html) if args.html else DESPUES
        if not ruta.is_file():
            raise SystemExit(f"no existe {ruta}: genera el HTML nuevo o pasa --html/--base")
        pagina = ruta.read_text(encoding="utf-8")
        origen = str(ruta)
        total, faltan = _comparar(datos, pagina)

    print(f"origen del payload: {origen}")
    print(f"filas comprobadas: {total}")
    if faltan:
        print(f"DIFERENCIAS: {len(faltan)}")
        for linea in faltan[:40]:
            print(f"  - {linea}")
        return 1
    print("sin diferencias")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
