"""Descarga GET de fotos publicas de las nueve variantes para buscar cotas."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import urlopen

BASE = Path(__file__).resolve().parent
datos = json.loads((BASE / "meli-actual.json").read_text())
(BASE / "fotos").mkdir(exist_ok=True)
pendientes = []
for fila in datos["filas"]:
    variante = next(v for v in fila["variantes"] if str(v["id"]) == fila["variante_ext"])
    fotos = {p["id"]: p for p in fila["pictures"]}
    for posicion, pid in enumerate(variante["picture_ids"], 1):
        pendientes.append(
            (
                fila["listing_id"],
                fila["item_id"],
                fila["variante_ext"],
                posicion,
                pid,
                fotos[pid]["secure_url"],
            )
        )


def descargar(fila):
    listing, item, variante, posicion, pid, url = fila
    ruta = BASE / "fotos" / f"{listing}-{posicion}-{pid}.jpg"
    with urlopen(url, timeout=15) as respuesta:
        contenido = respuesta.read()
    ruta.write_bytes(contenido)
    return {
        "listing_id": listing,
        "item_id": item,
        "variante_ext": variante,
        "posicion": posicion,
        "picture_id": pid,
        "url": url,
        "archivo": str(ruta.relative_to(BASE)),
        "sha256": hashlib.sha256(contenido).hexdigest(),
        "bytes": len(contenido),
        "observado_utc": datetime.now(UTC).isoformat(),
    }


with ThreadPoolExecutor(max_workers=4) as pool:
    filas = list(pool.map(descargar, pendientes))
(BASE / "fotos.json").write_text(json.dumps(filas, ensure_ascii=False, indent=2) + "\n")
print(f"Fotos descargadas: {len(filas)}")
