"""Ceremonia CLI canonica para mutaciones reales (`--acepto-mutacion-real`).

Unifica el parseo y la validacion fail-closed de `--acepto-mutacion-real`,
`--huella`, `--esperado` y `--go` repetidos antes en los scripts de `tools/`.
"""

from __future__ import annotations

import argparse


class ErrorCeremonia(ValueError):
    """Ceremonia de mutacion incompleta, contradictoria o desfasada del plan."""


def agrega_flags_ceremonia(
    ap: argparse.ArgumentParser,
    *,
    con_huella: bool = True,
    con_esperado: bool = False,
    dest_go: str = "go",
    ayuda_acepto: str = "obligatorio para mutar; sin el = dry-run",
) -> None:
    """Agrega el juego estandar de flags de ceremonia a un ArgumentParser."""
    ap.add_argument("--acepto-mutacion-real", action="store_true", help=ayuda_acepto)
    if con_esperado:
        ap.add_argument("--esperado", type=int, default=None, help="conteo autorizado del dry-run")
    if con_huella:
        ap.add_argument("--huella", default=None, help="huella exacta del dry-run")
    ap.add_argument("--go", dest=dest_go, default=None, help="literal del dueno (no vacio)")


def valida_ceremonia(
    *,
    acepto: bool,
    go: str | None = None,
    huella: str | None = None,
    huella_plan: str | None = None,
    esperado: int | None = None,
    real: int | None = None,
    exige_go: bool = True,
    rechaza_go_si_no_exige: bool = False,
    exige_huella: bool = True,
    exige_esperado: bool = False,
    rechaza_sin_acepto: bool = True,
    etiqueta_real: str = "candidatas",
    nota_rechazo_go: str = "este modo es sin go literal: --go no aplica",
) -> bool:
    """Valida la ceremonia y devuelve `False` en dry-run limpio, `True` para mutar.

    Lanza `ErrorCeremonia` ante flags sin `--acepto-mutacion-real` (si
    `rechaza_sin_acepto=True`), faltantes o divergencias de `--esperado`/`--huella`.
    """
    if not acepto:
        if rechaza_sin_acepto and (go is not None or huella is not None or esperado is not None):
            raise ErrorCeremonia(
                "falto --acepto-mutacion-real: --go/--huella sin"
                " --acepto-mutacion-real no muta (dry-run enganoso)"
            )
        return False

    if exige_esperado:
        if esperado is None:
            raise ErrorCeremonia("mutacion real exige --esperado N (anti-typo del plan)")
        if real is not None and real != esperado:
            raise ErrorCeremonia(
                f"--esperado {esperado} != {real} {etiqueta_real}:"
                " el plan cambio, se re-autoriza con el dueno"
            )

    if exige_go:
        if not go or not go.strip():
            raise ErrorCeremonia(
                "falta --go: mutacion real exige --go con el literal del dueno (no vacio)"
            )
    elif rechaza_go_si_no_exige and go is not None:
        raise ErrorCeremonia(nota_rechazo_go)

    if exige_huella:
        if not huella:
            raise ErrorCeremonia("falta --huella: el go exige la huella del dry-run")
        if huella_plan is not None and huella != huella_plan:
            raise ErrorCeremonia(
                f"--huella {huella} != huella del plan {huella_plan}:"
                " el plan cambio, re-corre el dry-run"
            )

    return True
