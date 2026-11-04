"""Las herramientas de sonda y reversa abren el mercado con el cotizador.

`MercadoMeli` exige lector, escritor Y `fees`: sin el cotizador cae a
"mercado sin clientes: vitrina de solo lectura" y la sonda de X.4 no corre
(prod, 2026-10-17). El motor (`app.cli_precio`) ya lo pasaba.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

import app.cli
from tools import precio_reversa, precio_sonda

_LECTOR, _ESCRITOR, _FEES, _LIMITADOR = object(), object(), object(), object()


class _Conn:
    def close(self) -> None:
        pass


class _Alto(Exception):
    pass


@pytest.fixture
def abierto(monkeypatch):
    visto: dict = {}

    @contextmanager
    def abrir(plataforma, **kwargs):
        visto.update(kwargs, plataforma=plataforma)
        raise _Alto
        yield

    monkeypatch.setenv("ORBIT_DSN_DECIDE", "postgresql://no-se-usa")
    monkeypatch.setattr(
        app.cli, "_clientes_precio", lambda _p: (_LECTOR, _ESCRITOR, _FEES, _LIMITADOR)
    )
    for tool in (precio_sonda, precio_reversa):
        monkeypatch.setattr(tool, "connect", lambda *_a, **_k: _Conn())
        monkeypatch.setattr(tool.mercados, "abrir", abrir)
    return visto


def _esperado(plataforma: str) -> dict:
    return {
        "plataforma": plataforma,
        "lector": _LECTOR,
        "escritor": _ESCRITOR,
        "fees": _FEES,
        "limitador": _LIMITADOR,
    }


def test_sonda_abre_meli_con_fees(abierto, monkeypatch):
    monkeypatch.setattr(precio_sonda, "_unidad", lambda *_a, **_k: None)
    with pytest.raises(_Alto):
        precio_sonda.main(["--platform", "meli", "--listing-id", "1", "--lee-vivo"])
    assert abierto == _esperado("meli")


def test_reversa_abre_meli_con_fees(abierto, monkeypatch):
    monkeypatch.setattr(precio_reversa, "_platform_de_ids", lambda *_a: "meli")
    with pytest.raises(_Alto):
        precio_reversa.main(["--cambio-id", "7"])
    assert abierto == _esperado("meli")
