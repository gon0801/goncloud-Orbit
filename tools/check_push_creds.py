#!/usr/bin/env python3
"""Fail-fast de credenciales antes del push (incidente 2026-10-16).

Sin credencial de escritura, `git push` se cuelga esperando un login
invisible y el push muere horas despues sin decir por que. Este chequeo
pide la credencial con timeout corto: si no hay, frena con el arreglo.
"""

from __future__ import annotations

import subprocess
import sys

TIMEOUT_S = 15


def _hay_credencial() -> bool:
    """True si `git credential fill` devuelve usuario+password rapido."""
    try:
        proc = subprocess.run(
            ["git", "credential", "fill"],
            input="protocol=https\nhost=github.com\n\n",
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    valores = dict(linea.split("=", 1) for linea in proc.stdout.splitlines() if "=" in linea)
    return bool(valores.get("username") and valores.get("password"))


def main() -> int:
    if _hay_credencial():
        return 0
    print(
        "sin credencial de escritura para github.com "
        f"(nada en {TIMEOUT_S}s): el push se colgaria. "
        "Arreglo: gh auth setup-git (o gh auth login --hostname github.com) "
        "y reintenta.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
