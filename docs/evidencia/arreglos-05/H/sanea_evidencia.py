"""Sanea las salidas capturadas de H/ (sin rutas locales, usuario ni espacios finales).

Uso desde la raiz, despues de capturar rojo.txt, previa.txt y acepta*.txt:

    uv run --frozen --python 3.12 python docs/evidencia/arreglos-05/H/sanea_evidencia.py [prefijo-extra ...]

Reemplaza la raiz del checkout, el nombre del usuario y los prefijos extra que
se pasen por argumento (p. ej. el worktree temporal del padre) por marcadores, y
quita los espacios al final de cada linea (las salidas de pytest los traen y el
chequeo de `git diff --check` los rechaza). No toca el contenido de las pruebas.
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
    nuevo = "\n".join(linea.rstrip() for linea in nuevo.splitlines()) + "\n"
    if nuevo != texto:
        archivo.write_text(nuevo, encoding="utf-8")
        print(f"saneado: {archivo.name}")
