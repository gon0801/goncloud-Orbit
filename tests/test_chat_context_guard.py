"""Tests del candado de frescura del contexto de chat (guardrails-01 / 1.4).

Prueban la logica pura `evaluar()` — regla 9: el caso que muerde (tareas
completadas sin tocar CHAT-CONTEXT.md) se afirma FALLANDO con exit 1.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from check_chat_context_fresh import evaluar  # noqa: E402

DIFF_CON_COMPLETADA = (
    "--- a/plans/orbit-03.md\n"
    "+++ b/plans/orbit-03.md\n"
    "-| 3.1 | Orquestador ... | ... | 2.2, 2.3, 2.4 | cc:WIP |\n"
    "+| 3.1 | Orquestador ... | ... | 2.2, 2.3, 2.4 | cc:完了 |\n"
)

DIFF_SIN_COMPLETADA = (
    "--- a/plans/orbit-03.md\n"
    "+++ b/plans/orbit-03.md\n"
    "-| 3.2 | Router ... | ... | 3.1 | cc:TODO |\n"
    "+| 3.2 | Router ... | ... | 3.1 | cc:WIP |\n"
)


def test_muerde_tarea_completada_sin_contexto_actualizado():
    codigo, mensaje = evaluar(["plans/orbit-03.md"], DIFF_CON_COMPLETADA)
    assert codigo == 1
    assert "FALLO" in mensaje
    assert "docs/CHAT-CONTEXT.md" in mensaje


def test_pasa_si_el_contexto_viene_en_el_mismo_pr():
    codigo, mensaje = evaluar(["plans/orbit-03.md", "docs/CHAT-CONTEXT.md"], DIFF_CON_COMPLETADA)
    assert codigo == 0
    assert "OK" in mensaje


def test_pasa_sin_cambios_de_planes():
    codigo, _ = evaluar(["app/cycle.py", "docs/DEPLOY.md"], "")
    assert codigo == 0


def test_pasa_con_planes_cambiados_pero_sin_completadas():
    codigo, _ = evaluar(["plans/orbit-03.md"], DIFF_SIN_COMPLETADA)
    assert codigo == 0


def test_linea_borrada_con_marker_no_dispara():
    diff_solo_borrado = (
        "-| 1.1 | vieja | ... | - | cc:完了 |\n+| 1.1 | vieja | ... | - | cc:TODO |\n"
    )
    codigo, _ = evaluar(["plans/orbit-03.md"], diff_solo_borrado)
    assert codigo == 0
