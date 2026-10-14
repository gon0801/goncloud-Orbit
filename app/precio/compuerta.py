"""Compuerta del motor de precios (REPRICING 02, bosquejo E).

S.2 trae SOLO el apagador (`modo_efectivo`); S.3 suma el fusible, el
insumo sistemico y el corte. Puro: sin I/O, sin reloj, sin red.
"""

from __future__ import annotations

__all__ = ["modo_efectivo"]

_ORDEN_MODO = {"off": 0, "shadow": 1, "live": 2}


def modo_efectivo(global_: str, universo: str, goal: str) -> str:
    """El menor de los tres en `off < shadow < live`.

    Copia del reticulo de Ads (`app/optimizer/goals.py::modo_efectivo`),
    no importado. Vocabulario cerrado: fuera de el, `ValueError` que
    nombra el lugar.
    """
    for nombre, modo in (("global", global_), ("universo", universo), ("goal", goal)):
        if modo not in _ORDEN_MODO:
            raise ValueError(
                f"{nombre} fuera del vocabulario sellado {{off, shadow, live}}: {modo!r}"
            )
    return min((global_, universo, goal), key=_ORDEN_MODO.__getitem__)
