"""Candados de arquitectura del carril G de REPRICING 02.

0.a crea el archivo para que el patron del paso de guardas del job `rapido`
tenga donde encontrar; el carril escribe aqui sus candados (los de 0.a y 0.b
viven en `tests/test_architecture.py`).
"""

from __future__ import annotations

from pathlib import Path


def test_archivo_del_carril():
    """El carril existe y este es su archivo de candados."""
    assert Path(__file__).name == "test_arq_precio_g.py"
