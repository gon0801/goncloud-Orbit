"""Arma el manual: junta paginas/*.html, numera hojas, revisa cobertura y desborde, imprime el PDF.

Uso: python3 docs/manual-usuario/build.py [--borrador] [salida.pdf]
Necesita agent-browser (Chromium) para medir las hojas e imprimir.
--borrador omite la revision de cobertura del menu.
"""
import json
import os
import pathlib
import re
import subprocess
import sys

RAIZ = pathlib.Path(__file__).resolve().parent
REPO = RAIZ.parents[1]
FECHA = "8 de noviembre de 2026"
PIE = f"ORBIT | Entrenamiento completo | {FECHA}"
MARCA = "ORBIT / SISTEMA COMPLETO"
SESION = f"manual-orbit-build-{os.getpid()}"

SECCION = re.compile(r"<section\b([^>]*)>(.*?)</section>", re.S)
ATRIBUTO = re.compile(r'([\w-]+)="([^"]*)"')


def pantallas_del_menu():
    base = (REPO / "app/templates/base.html").read_text()
    bloque = base.split("{% set paginas = {", 1)[1].split("} %}", 1)[0]
    return re.findall(r"'([\w-]+)':\s*\('([^']+)'", bloque)


def secciones():
    for archivo in sorted((RAIZ / "paginas").glob("*.html")):
        for attrs, cuerpo in SECCION.findall(archivo.read_text()):
            yield archivo.name, dict(ATRIBUTO.findall(attrs)), cuerpo


def main():
    args = [a for a in sys.argv[1:] if a != "--borrador"]
    borrador = "--borrador" in sys.argv
    salida = pathlib.Path(args[0]) if args else RAIZ / "manual.pdf"
    hojas = list(secciones())
    total = len(hojas)

    anclas = {}
    for n, (_, attrs, _) in enumerate(hojas, 1):
        for clave in (attrs.get("data-pantalla"), attrs.get("data-ancla")):
            if clave:
                anclas.setdefault(clave, n)

    faltan = [f"{pid} ({nombre})" for pid, nombre in pantallas_del_menu() if pid not in anclas]
    if faltan and not borrador:
        sys.exit("Pantallas del menu sin pagina en el manual: " + ", ".join(faltan))

    def pagina_de(m):
        clave = m.group(1)
        if clave not in anclas:
            if borrador:
                return "?"
            sys.exit(f"Referencia a pagina inexistente: {clave}")
        return str(anclas[clave])

    html = []
    for n, (archivo, attrs, cuerpo) in enumerate(hojas, 1):
        cuerpo = re.sub(r"\{\{pag:([\w-]+)\}\}", pagina_de, cuerpo)
        clase = attrs.get("class", "")
        html.append(
            f'<div class="hoja {clase}" data-archivo="{archivo}" data-n="{n}">'
            f'<div class="encabezado"><span class="marca">{MARCA}</span>'
            f'<span class="seccion">{attrs.get("data-etiqueta", "")}</span></div>'
            f'<div class="cuerpo">{cuerpo}</div>'
            f'<div class="pie"><span>{PIE}</span><span>{n} / {total}</span></div></div>'
        )
    doc = (
        '<!doctype html><html lang="es"><head><meta charset="utf-8">'
        "<title>Orbit - Entrenamiento completo</title>"
        '<link rel="stylesheet" href="estilo.css"></head><body>'
        + "\n".join(html)
        + "</body></html>"
    )
    destino = RAIZ / f"manual-{os.getpid()}.html" if borrador else RAIZ / "manual.html"
    destino.write_text(doc)

    def ab(*args):
        r = subprocess.run(["agent-browser", "--session", SESION, *args], capture_output=True, text=True)
        if r.returncode:
            sys.exit(f"agent-browser {args[0]} fallo: {r.stderr or r.stdout}")
        return r.stdout

    ab("open", destino.as_uri())
    ab("wait", "800")
    medida = ab(
        "eval",
        "JSON.stringify([...document.querySelectorAll('.hoja')].map(h=>{const c=h.querySelector('.cuerpo');"
        "return [h.dataset.n,h.dataset.archivo,c.scrollHeight-c.clientHeight,"
        "[...h.querySelectorAll('img')].filter(i=>!i.complete||!i.naturalWidth).map(i=>i.getAttribute('src'))]}))",
    )
    filas = json.loads(json.loads(medida.strip()))
    desbordes = [f"hoja {n} ({a}) +{px}px" for n, a, px, _ in filas if px > 0]
    rotas = [f"hoja {n} ({a}): {src}" for n, a, _, imgs in filas for src in imgs]
    ab("pdf", str(salida))
    ab("close")
    if borrador:
        destino.unlink()
    print(f"{total} hojas -> {salida}")
    for linea in desbordes:
        print("DESBORDA", linea)
    for linea in rotas:
        print("IMAGEN ROTA", linea)
    if desbordes or rotas:
        sys.exit(1)


if __name__ == "__main__":
    main()
