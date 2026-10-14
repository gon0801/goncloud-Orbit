"""Registro de universos de estimacion (REPRICING 02, seccion M, corte 0).

La frontera de ABAJO de la estimacion: un universo es un `platform/canal`
con su moneda de venta, de donde sale su envio (L) y si tiene cotizador
real. Cuatro entradas; agregar un universo es una linea aqui y una fila en
`estimacion_politica_version`.

En 0.a el registro existe y el cotizador de todas las entradas es None: F.3
cablea el de Amazon y M.3 el de MeLi. `logistica_del_universo` tambien llega
con F.3.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

__all__ = ["Universo", "universo_de"]

Cotizador = Any


@dataclass(frozen=True)
class Universo:
    """Un `platform/canal`, con las constantes de su politica."""

    plataforma: str
    canal: str
    moneda_venta: str
    envio: Literal["politica", "muestra"]
    fx_max_dias: int | None
    cotizador: Cotizador | None = None

    @property
    def clave(self) -> str:
        return f"{self.plataforma}/{self.canal}"


_REGISTRO: dict[tuple[str, str], Universo] = {
    ("amazon_mx", "fba"): Universo(
        plataforma="amazon_mx",
        canal="fba",
        moneda_venta="MXN",
        envio="politica",
        fx_max_dias=None,
    ),
    ("amazon_mx", "fbm"): Universo(
        plataforma="amazon_mx",
        canal="fbm",
        moneda_venta="MXN",
        envio="muestra",
        fx_max_dias=None,
    ),
    ("amazon_us", "fbm"): Universo(
        plataforma="amazon_us",
        canal="fbm",
        moneda_venta="USD",
        envio="muestra",
        fx_max_dias=3,
    ),
    ("meli", "meli"): Universo(
        plataforma="meli",
        canal="meli",
        moneda_venta="MXN",
        envio="muestra",
        fx_max_dias=None,
    ),
}


def universo_de(plataforma: str, canal: str) -> Universo | None:
    """None = `universo_no_soportado` (motivo, no excepcion)."""
    return _REGISTRO.get((plataforma, canal))
