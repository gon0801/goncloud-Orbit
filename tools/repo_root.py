"""Resolvedor unico de rutas de repos y worktrees (PC Gon <-> Mac).

Elimina por codigo una clase de atoron repetido: instrucciones con rutas
literales de un host que no existen en el otro (`/Users/dn/dev/...` en
Windows, `C:/dev/...` en la Mac). Los scripts resuelven aqui; las
instrucciones nombran el repo, nunca la ruta.
"""

from __future__ import annotations

import os
from pathlib import Path


def _bases() -> list[Path]:
    """Bases candidatas donde viven los checkouts, en orden de preferencia."""
    env = os.environ.get("ORBIT_DEV")
    bases = [Path(env)] if env else []
    bases += [
        Path("C:/dev"),  # PC Gon (Windows)
        Path("/Users/dn/dev"),  # Mac (principal de David)
        Path.home() / "dev",  # espejo generico
    ]
    return bases


def repo_path(nombre: str) -> Path:
    """Ruta del checkout principal de `nombre` en este host.

    Lanza FileNotFoundError (con las bases probadas) si no existe en
    ningun lado: fallar rapido aqui es mejor que un error raro despues.
    """
    probadas: list[str] = []
    for base in _bases():
        candidata = base / nombre
        probadas.append(str(candidata))
        if candidata.is_dir():
            return candidata
    raise FileNotFoundError(
        f"repo {nombre!r} no existe en este host; probado: {', '.join(probadas)}"
    )


def worktree_path(nombre: str) -> Path:
    """Ruta del worktree `<base>/wt/<nombre>` en este host."""
    probadas: list[str] = []
    for base in _bases():
        candidata = base / "wt" / nombre
        probadas.append(str(candidata))
        if candidata.is_dir():
            return candidata
    raise FileNotFoundError(
        f"worktree {nombre!r} no existe en este host; probado: {', '.join(probadas)}"
    )
