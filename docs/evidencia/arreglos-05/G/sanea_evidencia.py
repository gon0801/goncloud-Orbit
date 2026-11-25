"""Sanea las salidas capturadas de G/ (sin rutas locales ni usuario).

Uso desde la raiz, despues de capturar acepta-1..3.txt:

    uv run --frozen --python 3.12 python docs/evidencia/arreglos-05/G/sanea_evidencia.py [prefijo-extra ...]

Reemplaza la raiz del checkout, el nombre del usuario y los prefijos extra que
se pasen por argumento por marcadores. Las salidas de pytest traen la ruta
absoluta del venv en el aviso de FastAPI.
"""

import pathlib
import sys

RAIZ = pathlib.Path(__file__).resolve().parent
REPO = RAIZ.parents[4]
USUARIO = pathlib.Path.home().name

prefijos = set(sys.argv[1:])
for forma in (str(REPO), REPO.as_posix(), str(REPO).replace("/", "\\")):
    prefijos.add(forma)
    prefijos.add(forma + "\\")
    prefijos.add(forma + "/")

for archivo in sorted(RAIZ.glob("*.txt")):
    texto = archivo.read_text(encoding="utf-8", errors="replace")
    nuevo = texto
    for prefijo in sorted(prefijos, key=len, reverse=True):
        if prefijo:
            nuevo = nuevo.replace(prefijo, "<checkout>")
    if USUARIO:
        nuevo = nuevo.replace(USUARIO, "<usuario>")
    if nuevo != texto:
        archivo.write_text(nuevo, encoding="utf-8")
        print(f"saneado: {archivo.name}")
