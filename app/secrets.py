"""Resolucion canonica de `ORBIT_SECRETS_DIR`.

Modulo hoja compartido por todas las integraciones (Amazon Ads, SP-API,
Mercado Libre, Telegram, JEV y fees) para que ninguna importe `app.ads.config`
solo para leer el directorio de secretos.
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_SECRETS_DIR = "/mnt/data/appdata/orbit/secrets"


def directorio_secretos() -> Path:
    """`ORBIT_SECRETS_DIR`, o la ruta canonica si falta o viene VACIA.

    `os.environ.get(var, default)` devuelve "" con la variable vacia y
    `Path("")` es el directorio actual: un secreto plantado ahi se leia (R11).
    """
    return Path(os.environ.get("ORBIT_SECRETS_DIR") or DEFAULT_SECRETS_DIR)
