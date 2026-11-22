"""TEMPORADA 01 H.3: las fechas de las transiciones del calendario guardado.

Uso (desde la raiz del repo):
    PYTHONPATH=. uv run --frozen --python 3.12 \
        python docs/evidencia/temporada-01/H/fechas.py < settings.json

`settings.json` es la config guardada tal como la devuelve lee_prod.sh (las claves
`ads_temporada` y los modos por plataforma). Solo lee: no toca la base ni el disco.
"""

from __future__ import annotations

import datetime as dt
import json
import sys

from app.optimizer import temporada

DESDE = dt.date(2026, 11, 22)  # el dia del encendido
HASTA = dt.date(2027, 11, 22)  # los 12 meses siguientes


def main() -> None:
    settings = json.load(sys.stdin)
    config = temporada.desde_settings(settings)
    if config is None:
        raise SystemExit("sin meses guardados no hay calendario que calcular")
    for ev in temporada.eventos(config, DESDE, HASTA):
        print(ev.fecha.isoformat(), ev.tipo, "|", ev.frase)


if __name__ == "__main__":
    main()
