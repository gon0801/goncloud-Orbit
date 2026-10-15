"""Registro de universos de estimacion (REPRICING 02, seccion M, corte 0).

La frontera de ABAJO de la estimacion: un universo es un `platform/canal`
con su moneda de venta, de donde sale su envio (L) y si tiene cotizador
real. Cuatro entradas; agregar un universo es una linea aqui y una fila en
`estimacion_politica_version`.

En 0.a el registro existe y el cotizador de todas las entradas es None: F.3
cablea el de Amazon y M.3 el de MeLi. `logistica_del_universo` tambien llega
con F.3.

`cotizador` guarda la CLASE (factoria), no una instancia: construir un
cotizador pide credenciales y transporte, y el registro se importa sin
ellos. `None` sigue significando "este universo no cotiza de verdad".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Literal

import psycopg

from app.estimacion_amazon import CotizadorAmazon
from app.precio.tipos import (
    Convertido,
    EnvioImputado,
    EnvioIncluido,
    EnvioPropio,
    Importe,
    MuestraEnvio,
    TasaFx,
)

__all__ = [
    "Envio",
    "LogisticaResuelta",
    "Universo",
    "envio_a_logistica",
    "logistica_del_universo",
    "universo_de",
]

Cotizador = Any
Envio = EnvioIncluido | EnvioPropio | EnvioImputado


@dataclass(frozen=True)
class LogisticaResuelta:
    """Envio desarmado para la entrada del calculo y las columnas."""

    logistica: Decimal | None
    origen: str | None
    muestra_id: int | None
    original: Decimal | None
    moneda_original: str | None
    tasa: Decimal | None
    tasa_fecha: date | None


def envio_a_logistica(envio: Envio) -> LogisticaResuelta:
    """Envio a logistica con su sobre para la entrada y las columnas."""
    if isinstance(envio, EnvioIncluido):
        return LogisticaResuelta(envio.importe.valor, "politica", None, None, None, None, None)
    conversion = envio.conversion
    if conversion is None:
        return LogisticaResuelta(
            envio.importe.valor, envio.muestra.origen, envio.muestra.id, None, None, None, None
        )
    return LogisticaResuelta(
        envio.importe.valor,
        envio.muestra.origen,
        envio.muestra.id,
        conversion.original.valor,
        conversion.original.moneda,
        conversion.tasa.tasa,
        conversion.tasa.fecha,
    )


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
        cotizador=CotizadorAmazon,
    ),
    ("amazon_mx", "fbm"): Universo(
        plataforma="amazon_mx",
        canal="fbm",
        moneda_venta="MXN",
        envio="muestra",
        fx_max_dias=None,
        cotizador=CotizadorAmazon,
    ),
    ("amazon_us", "fbm"): Universo(
        plataforma="amazon_us",
        canal="fbm",
        moneda_venta="USD",
        envio="muestra",
        fx_max_dias=3,
        cotizador=CotizadorAmazon,
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


_SQL_MUESTRA = (
    "SELECT id, product_id, platform, canal, origen, valor, valor_currency,"
    " envios, donantes, familia_id, ventana_desde, ventana_hasta, regla_version"
    " FROM precio_envio_muestra"
    " WHERE product_id = %s AND platform = %s AND canal = %s"
    " ORDER BY calculada_at DESC, id DESC LIMIT 1"
)


def logistica_del_universo(
    conn: psycopg.Connection,
    universo: Universo,
    *,
    product_id: int,
    politica_settings: dict,
    fx: TasaFx | None,
) -> Envio | str:
    """`L` ya resuelta para el producto, o el motivo que la frena.

    `politica`: la constante sellada, en moneda de venta. `muestra`: la
    ultima fila del job; sin fila, `envio_sin_historia`. Muestra en otra
    moneda: convierte con `fx` (el llamador ya valido edad y tasa); sin
    tasa usable, `fx_ausente`. La ultima muestra no caduca en F.3.
    """
    if universo.envio == "politica":
        return EnvioIncluido(
            Importe(Decimal(politica_settings["logistica"]), universo.moneda_venta)
        )
    fila = conn.execute(_SQL_MUESTRA, (product_id, universo.plataforma, universo.canal)).fetchone()
    if fila is None:
        return "envio_sin_historia"
    (mid, pid, plat, canal, origen, valor, moneda, envios, donantes) = fila[:9]
    fid, desde, hasta, regla = fila[9:13]
    muestra = MuestraEnvio(
        id=mid,
        product_id=pid,
        plataforma=str(plat),
        canal=str(canal),
        origen=origen,
        valor=Importe(valor, moneda),
        envios=envios,
        donantes=donantes,
        ventana=(desde, hasta),
        regla_version=regla,
    )
    if muestra.valor.moneda == universo.moneda_venta:
        if origen == "propio":
            return EnvioPropio(muestra.valor, muestra, None)
        return EnvioImputado(muestra.valor, muestra, None)
    if fx is None or set((fx.base, fx.quote)) != {universo.moneda_venta, moneda}:
        return "fx_ausente"
    if fx.tasa == 0:
        return "fx_tasa_invalida"
    if fx.base == universo.moneda_venta:
        en = Importe(muestra.valor.valor / fx.tasa, universo.moneda_venta)
    else:
        en = Importe(muestra.valor.valor * fx.tasa, universo.moneda_venta)
    conversion = Convertido(muestra.valor, en, fx)
    if origen == "propio":
        return EnvioPropio(en, muestra, conversion)
    return EnvioImputado(en, muestra, conversion)
