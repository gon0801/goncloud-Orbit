"""Candado sembrado de 0.b: prueba que falla para demostrar que el paso de
guardas del job `rapido` corre `tests/test_arq_bids_*.py`. Se quita con el
siguiente commit (guia, 0.b Comprueba)."""

import pytest


def test_candado_sembrado_falla_en_rapido():
    pytest.fail("candado sembrado de 0.b: rapido debe fallar y nombrar este archivo")
