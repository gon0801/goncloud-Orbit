#!/usr/bin/env python3
"""Candado de frescura del contexto de chat (guardrails-01 task 1.4).

El Project de Claude Chat sincroniza docs/CHAT-CONTEXT.md desde master: si un
PR marca tareas completadas en un plan (lineas nuevas con el marker cc:完了 en
plans/*.md) pero no toca ese archivo, el chat del dueño queda contando
historia vieja. Este candado REVISA y truena; jamas escribe contenido solo
(decision sellada: un robot que redacta estado sin review contradice la
disciplina del repo).

Uso: python tools/check_chat_context_fresh.py [base-ref]   (default origin/master)
Corre en CI solo en pull_request; exit 1 = el PR debe actualizar el contexto.
"""

from __future__ import annotations

import subprocess
import sys

CONTEXTO_CHAT = "docs/CHAT-CONTEXT.md"
MARKER_COMPLETADA = "cc:完了"


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        check=True,
        encoding="utf-8",
        errors="replace",
    ).stdout


def evaluar(archivos: list[str], diff_planes: str) -> tuple[int, str]:
    """Logica pura del candado (testeable sin repo): exit code + mensaje."""
    planes = [a for a in archivos if a.startswith("plans/") and a.endswith(".md")]
    if not planes:
        return 0, "candado chat-context: sin cambios de planes, nada que revisar"
    completadas = [
        linea
        for linea in diff_planes.splitlines()
        if linea.startswith("+") and MARKER_COMPLETADA in linea
    ]
    if not completadas:
        return 0, "candado chat-context: planes cambiados pero sin tareas nuevas completadas"
    if CONTEXTO_CHAT in archivos:
        return 0, (
            f"candado chat-context: OK ({len(completadas)} tarea(s) completada(s) "
            f"y {CONTEXTO_CHAT} actualizado en el mismo PR)"
        )
    return 1, (
        f"candado chat-context: FALLO — este PR marca {len(completadas)} tarea(s) "
        f"como {MARKER_COMPLETADA} en {', '.join(planes)} pero NO toca {CONTEXTO_CHAT}. "
        "El Project de Claude Chat lee ese archivo desde master: actualiza su "
        "seccion 'Estado actual' y su fecha en este mismo PR (el candado revisa, "
        "no escribe — la redaccion es del humano/lead)."
    )


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "origin/master"
    rango = f"{base}...HEAD"
    archivos = _git("diff", "--name-only", rango).split()
    planes = [a for a in archivos if a.startswith("plans/") and a.endswith(".md")]
    diff_planes = _git("diff", rango, "--", *planes) if planes else ""
    codigo, mensaje = evaluar(archivos, diff_planes)
    print(mensaje)
    return codigo


if __name__ == "__main__":
    sys.exit(main())
