"""Raiz de composicion de mercados (REPRICING 02, seccion S, corte 0).

UNICO modulo que conoce a los dos adaptadores (`app/spapi/precio_mercado.py`
y `app/meli/precio_mercado.py`); un candado de `tests/test_architecture.py`
lo vigila. Solo el CLI, `tools/precio_*.py` y los routers de precios lo
importan.

Plataforma sin adaptador listo -> `ConfigInvalida` (exit 2): nunca un
mercado a medias. En 0.a los adaptadores estan vacios: `abrir` y `vitrina`
revientan para todo; 0.b llena el de Amazon y el carril M el de MeLi.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from app.meli import precio_mercado as _meli
from app.spapi import precio_mercado as _amazon

__all__ = ["ConfigInvalida", "vitrina", "abrir"]


class ConfigInvalida(ValueError):
    """Plataforma sin adaptador listo: no hay mercado que abrir."""


_ADAPTADORES = {
    "amazon_mx": _amazon,
    "amazon_us": _amazon,
    "meli": _meli,
}


def _mercado_de(plataforma: str):
    modulo = _ADAPTADORES.get(plataforma)
    if modulo is None:
        raise ConfigInvalida(f"plataforma sin mercado: {plataforma}")
    mercado = getattr(modulo, "Mercado", None)
    if mercado is None:
        raise ConfigInvalida(f"plataforma {plataforma}: su adaptador aun no declara Mercado")
    return mercado


def vitrina(plataforma: str):
    """Cara de lectura. No abre credenciales ni red: sirve en la pantalla."""
    return _mercado_de(plataforma)


@contextmanager
def abrir(plataforma: str, **kwargs) -> Iterator[object]:
    """Mercado completo (clientes, cubos de tasa, escritor default-deny).

    Sin `kwargs` es vitrina sola (sirve en la pantalla); con clientes
    inyectados (`lector`, `escritor`, `fees`, `limitador`,
    `construir_cuerpo`) es el mercado de la corrida y las herramientas.
    """
    mercado = _mercado_de(plataforma)(plataforma, **kwargs)
    try:
        yield mercado
    finally:
        cerrar = getattr(mercado, "cerrar", None)
        if cerrar is not None:
            cerrar()
