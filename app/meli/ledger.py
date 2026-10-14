"""Atribuidor de MeLi y mapeo de sus tipos de cargo (REPRICING 02, M.2).

En 0.a devuelve None: M.2 lo llena.
"""

from __future__ import annotations

from typing import Any


class AtribuidorMeliVacio:
    def atribuir(self, _fila: Any) -> None:
        return None


atribuidor_meli = AtribuidorMeliVacio()
