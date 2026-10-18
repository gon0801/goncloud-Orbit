"""Sorteo de la revision a ciegas de JEV ADS 03 (2026-10-17).

Entrada: la salida de muestra.sql, un JSON por linea. Salida: preguntas.json
(sin la lectura de Jev, para la pagina) y clave.json (pregunta -> senal_id y
lectura, fuera de la pagina). Los senal_id elegidos quedan en
revision-ciega.csv; las senales vigentes cambian, asi que el sorteo se
reproduce sobre la misma entrada, no sobre una lectura nueva.
"""

import json
import random
import sys
from pathlib import Path

filas = [json.loads(linea) for linea in Path(sys.argv[1]).read_text().splitlines() if linea.strip()]
rng = random.Random(20261017)


def sortea(plataforma, lectura, n):
    candidatas = sorted(
        (f for f in filas if f["plataforma"] == plataforma and f["lectura"] == lectura),
        key=lambda f: f["id"],
    )
    rng.shuffle(candidatas)
    return candidatas[:n]


sirve = (
    sortea("amazon_mx", "ajena", 30)
    + sortea("amazon_mx", "relevante_sin_venta", 20)
    + sortea("amazon_us", "ajena", 24)
    + sortea("amazon_us", "relevante_sin_venta", 26)
)
mover = sortea("amazon_us", "vende_en_otro", 13) + sortea("amazon_mx", "vende_en_otro", 22)
items = [("corresponde", f) for f in sirve] + [("ruteo", f) for f in mover]
rng.shuffle(items)

preguntas, clave = [], {}
for i, (tipo, f) in enumerate(items, 1):
    qid = f"q{i:03d}"
    preguntas.append({"id": qid, "tipo": tipo, "termino": f["termino"], "grupo": f["grupo"]})
    clave[qid] = {"senal_id": f["id"], "lectura": f["lectura"], "plataforma": f["plataforma"]}
Path("preguntas.json").write_text(json.dumps(preguntas, ensure_ascii=False, indent=1))
Path("clave.json").write_text(json.dumps(clave, ensure_ascii=False, indent=1))
