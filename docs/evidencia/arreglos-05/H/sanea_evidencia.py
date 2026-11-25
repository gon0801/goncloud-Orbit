"""Sanea las salidas capturadas de H/ (sin rutas locales, usuario, host ni espacios finales).

Uso desde la raiz, despues de capturar las salidas:

    uv run --frozen --python 3.12 python docs/evidencia/arreglos-05/H/sanea_evidencia.py [prefijo-extra ...]

Reemplaza la raiz del checkout, el nombre del usuario, el host de esta PC, la
ruta del venv y los prefijos extra que se pasen por argumento por marcadores, y
quita los espacios al final de cada linea. No toca el contenido de las pruebas.
"""

import os
import pathlib
import sys

RAIZ = pathlib.Path(__file__).resolve().parent
REPO = RAIZ.parents[3]
USUARIO = pathlib.Path.home().name
# El host de la PC se lee del entorno: no queda escrito en la evidencia.
HOST = os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME") or ""

prefijos = set(sys.argv[1:])
for forma in (str(REPO), REPO.as_posix(), str(REPO).replace("/", "\\")):
    prefijos.add(forma)
    prefijos.add(forma + "\\")
    prefijos.add(forma + "/")

# Restos de una pasada anterior con la raiz mal calculada y la ruta del venv.
restos = [
    ("<checkout>goncloud-orbit-45\\.venv", "<venv>"),
    ("<checkout>goncloud-orbit-45/.venv", "<venv>"),
    ("<checkout>goncloud-orbit-45", "<checkout>"),
    ("<checkout>\\.venv", "<venv>"),
    ("<checkout>/.venv", "<venv>"),
    (".venv\\Lib", "<venv>\\Lib"),
    (".venv/Lib", "<venv>/Lib"),
    (".venv\\Scripts", "<venv>\\Scripts"),
    (".venv/Scripts", "<venv>/Scripts"),
    ("goncloud-orbit-45", "<checkout>"),
]

for archivo in sorted(RAIZ.glob("*.txt")):
    texto = archivo.read_text(encoding="utf-8", errors="replace")
    nuevo = texto
    for prefijo in sorted(prefijos, key=len, reverse=True):
        if prefijo:
            nuevo = nuevo.replace(prefijo, "<checkout>")
    for viejo, reemplazo in restos:
        nuevo = nuevo.replace(viejo, reemplazo)
    if USUARIO:
        nuevo = nuevo.replace(f"Users\\{USUARIO}", "Users\\<usuario>")
        nuevo = nuevo.replace(f"Users/{USUARIO}", "Users/<usuario>")
        nuevo = nuevo.replace(USUARIO, "<usuario>")
    if HOST:
        nuevo = nuevo.replace(HOST, "<host>")
    nuevo = "\n".join(linea.rstrip() for linea in nuevo.splitlines()) + "\n"
    if nuevo != texto:
        archivo.write_text(nuevo, encoding="utf-8")
        print(f"saneado: {archivo.name}")
