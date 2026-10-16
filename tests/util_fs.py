"""Utilidades de filesystem portables para pruebas.

`hacer_ilegible` es el unico camino permitido para simular un archivo
ilegible en tests: en Windows `chmod 0o000` no quita la lectura y el test
fallaria por el OS, no por el codigo. La sonda de capacidad decide.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path


def hacer_ilegible(path: Path) -> bool:
    """Intenta dejar `path` ilegible. Devuelve True si quedo ilegible.

    Si el OS no lo soporta (Windows), restaura y devuelve False para que
    el test se salte con razon en vez de fallar por el entorno.
    """
    path.chmod(0o000)
    if os.access(path, os.R_OK):
        hacer_legible(path)
        return False
    return True


def hacer_legible(path: Path) -> None:
    """Restaura lectura/escritura al dueno. Best-effort, nunca levanta."""
    with contextlib.suppress(OSError):
        path.chmod(0o644)
