"""Sanea las salidas capturadas de F/ (sin rutas locales ni usuario).

Uso desde la raiz, despues de capturar acepta-1..3.txt:

    uv run --frozen --python 3.12 python docs/evidencia/arreglos-05/F/sanea_evidencia.py

Reemplaza el prefijo del checkout y el nombre de usuario por marcadores. Solo
toca los .txt de esta carpeta; las salidas de pytest traen la ruta absoluta del
venv en el aviso de FastAPI.
"""

import pathlib

RAIZ = pathlib.Path(__file__).resolve().parent
PREFIJOS = [
    r"C:\Users\ehven\.ao\data\worktrees\goncloud-orbit\goncloud-orbit-43",
    "C:/Users/ehven/.ao/data/worktrees/goncloud-orbit/goncloud-orbit-43",
    r"C:\Users\ehven\AppData\Local\Temp\opencode\f-work\padre",
    "C:/Users/ehven/AppData/Local/Temp/opencode/f-work/padre",
]
for archivo in sorted(RAIZ.glob("*.txt")):
    texto = archivo.read_text(encoding="utf-8", errors="replace")
    nuevo = texto
    for prefijo in PREFIJOS:
        nuevo = nuevo.replace(prefijo, "<checkout>")
    nuevo = nuevo.replace("ehven", "<usuario>")
    if nuevo != texto:
        archivo.write_text(nuevo, encoding="utf-8")
        print(f"saneado: {archivo.name}")
