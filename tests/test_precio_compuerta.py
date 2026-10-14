"""Apagador del motor de precios (REPRICING 02, S.2).

`app/precio/compuerta.py::modo_efectivo`: el menor de los tres modos en
`off < shadow < live`. Puro, sin DB.
"""

from __future__ import annotations

import pytest

from app.precio.compuerta import modo_efectivo


@pytest.mark.parametrize(
    "global_, universo, goal, esperado",
    [
        ("live", "live", "live", "live"),
        ("shadow", "live", "live", "shadow"),
        ("live", "shadow", "live", "shadow"),
        ("live", "live", "shadow", "shadow"),
        ("shadow", "shadow", "live", "shadow"),
        ("off", "live", "live", "off"),
        ("live", "off", "live", "off"),
        ("live", "live", "off", "off"),
        ("off", "off", "off", "off"),
        ("shadow", "off", "live", "off"),
    ],
)
def test_s2_modo_efectivo_es_el_menor_de_los_tres(global_, universo, goal, esperado):
    """S.2: el efectivo es el minimo en off < shadow < live, gane quien gane."""
    assert modo_efectivo(global_, universo, goal) == esperado


@pytest.mark.parametrize("lugar", ["global", "universo", "goal"])
@pytest.mark.parametrize("malo", ["turbo", "", None, "LIVE", "apagado"])
def test_s2_modo_efectivo_vocabulario_cerrado(lugar, malo):
    """S.2: fuera de off/shadow/live, ValueError que nombra el lugar."""
    modos = {"global": "live", "universo": "live", "goal": "live"}
    modos[lugar] = malo
    with pytest.raises(ValueError, match=lugar):
        modo_efectivo(modos["global"], modos["universo"], modos["goal"])
