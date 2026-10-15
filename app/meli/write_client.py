"""Cliente de escritura MeLi (REPRICING 02 M.4): la unica puerta de PUT.

Espejo de `app/spapi/write_client.py`: NO hereda de `ClienteMeli` (que
sigue GET-only), compone el refresco publico `refrescar_tokens_meli` y
hace su propio HTTP. Nunca lee credenciales (vienen inyectadas) ni
cachea nada: una instancia por operacion.

`modo_confirmado == "live"` exacto o `MutationNotAllowedError` antes de
construir nada. Un solo metodo publico. La forma del cuerpo (precio en
la raiz y repetido por variacion) es la CANDIDATA que concluyo M.0
(conclusion 3: el item replica el precio en cada variacion). Mientras
`FORMA_ESCRITURA_MELI` no sea la sellada, `poner_precio` levanta
`EscrituraNoDisponible` sin tocar la red, salvo `sonda=True` (solo
`tools/precio_sonda.py`). La sonda de +-0.01 la prueba; si pasa, un PR
cambia solo la constante a `items_price_put_v1`.

Reintentos (nunca un loop): 401 -> un refresh forzado y un reintento;
429 -> un reintento honrando `Retry-After` (espejo SP-API: default 1s,
tope 60s); lo demas, tal cual como `Acuse` (nunca levanta por un
estado HTTP: el llamador sella con el acuse).
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable, Sequence
from decimal import Decimal

import httpx

from app.precio.puerto import Acuse, EscrituraNoDisponible
from app.reputacion_clientes import (
    MELI_BASE,
    MeliCredentials,
    ReputacionError,
    refrescar_tokens_meli,
)

__all__ = [
    "FORMA_ESCRITURA_MELI",
    "MutationNotAllowedError",
    "MeliWriteClient",
    "validar_put_item",
]

logger = logging.getLogger(__name__)

FORMA_ESCRITURA_MELI = "pendiente_sonda"

_FORMA_SELLADA = "items_price_put_v1"
_MODO_LIVE = "live"
_ITEM_ID = re.compile(r"^MLM\d+$")
_MAX_PRECIO = Decimal(10) ** 10
_ESPERA_429_DEFAULT = 1.0
_ESPERA_429_MAX = 60.0


class MutationNotAllowedError(ReputacionError):
    """El cliente de escritura no se abre por defecto (fail-closed)."""


def validar_put_item(path: str, item_id: str) -> str:
    """Valida el unico PUT permitido (falla antes de red).

    Ruta relativa exacta `/items/<id>` e id `MLM<digitos>` (vendedor MX;
    otro prefijo es error del llamador, no adivinanza). Sin query ni
    fragment: el verbo es estructural (el unico metodo publico manda PUT).
    """
    if not _ITEM_ID.match(item_id or ""):
        raise ValueError(f"item_id invalido para PUT: {item_id!r}")
    esperada = f"/items/{item_id}"
    if path != esperada:
        raise ValueError(f"PUT fuera de allowlist: {path!r}")
    return path


def _texto_precio(precio: Decimal) -> str:
    """Centavos exactos como literal (el JSON lleva numero, nunca float)."""
    if not isinstance(precio, Decimal) or not precio.is_finite():
        raise ValueError(f"precio invalido: {precio!r}")
    if precio <= 0 or abs(precio) >= _MAX_PRECIO:
        raise ValueError(f"precio fuera de rango: {precio!r}")
    if precio != precio.quantize(Decimal("0.01")):
        raise ValueError(f"precio excede centavos: {precio!r}")
    return format(precio, ".2f")


def _cuerpo_put_precio(precio_txt: str, variantes: Sequence[str]) -> bytes:
    """Cuerpo candidato M.0-3: precio en raiz y repetido por variacion."""
    for vid in variantes:
        if not isinstance(vid, str) or not vid:
            raise ValueError(f"variante invalida: {vid!r}")
    if len(set(variantes)) != len(variantes):
        raise ValueError(f"variantes duplicadas: {list(variantes)!r}")
    partes = [f'"price": {precio_txt}']
    if variantes:
        vars_txt = ",".join(f'{{"id": {json.dumps(v)}, "price": {precio_txt}}}' for v in variantes)
        partes.append(f'"variations": [{vars_txt}]')
    return ("{" + ",".join(partes) + "}").encode("utf-8")


def _espera_retry_after(resp: httpx.Response) -> float:
    try:
        segundos = float(resp.headers.get("Retry-After", str(_ESPERA_429_DEFAULT)))
    except ValueError:
        segundos = _ESPERA_429_DEFAULT
    return min(max(0.0, segundos), _ESPERA_429_MAX)


class MeliWriteClient:
    """PUT de precio a items MeLi con scope sellado a la instancia."""

    def __init__(
        self,
        *,
        modo_confirmado: str,
        creds: MeliCredentials,
        refresco: Callable[[MeliCredentials, httpx.Client], None] = refrescar_tokens_meli,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        timeout: float = 30.0,
    ) -> None:
        if modo_confirmado != _MODO_LIVE:
            raise MutationNotAllowedError(
                f"modo_confirmado={modo_confirmado!r}: el cliente de escritura "
                f"SOLO se construye en '{_MODO_LIVE}'"
            )
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        self._creds = creds
        self._refresco = refresco
        self._transport = transport
        self._sleep = sleep
        self._timeout = timeout

    def exigir_sello(self) -> None:
        """Levanta `EscrituraNoDisponible` si un PUT saldria sin sello.

        Pre-chequeo para el adaptador (inyectado, sin importar este
        modulo): cero red sin sello, antes de verificar el vivo. Sin
        parametro `sonda` a proposito: quien llama ramifica, y el
        candado `test_sonda_true_solo_en_precio_sonda` no ve reenvios.
        """
        if FORMA_ESCRITURA_MELI != _FORMA_SELLADA:
            raise EscrituraNoDisponible(
                f"forma de escritura MeLi sin sellar ({FORMA_ESCRITURA_MELI}): "
                "solo sonda=True la prueba"
            )

    def close(self) -> None:
        """Sin estado que cerrar (el HTTP se abre por llamada)."""

    def poner_precio(
        self,
        item_id: str,
        variantes: Sequence[str],
        precio: Decimal,
        moneda: str,
        *,
        sonda: bool = False,
    ) -> Acuse:
        """PUT del precio (raiz + variaciones). Sin forma sellada levanta
        `EscrituraNoDisponible` sin tocar la red, salvo `sonda=True`."""
        ruta = validar_put_item(f"/items/{item_id}", item_id)
        if moneda != "MXN":
            raise ValueError(f"moneda fuera del universo meli/meli: {moneda!r}")
        cuerpo = _cuerpo_put_precio(_texto_precio(precio), variantes)
        if not sonda:
            self.exigir_sello()
        forzados = 0
        reintentos = 0
        with httpx.Client(
            base_url=MELI_BASE, transport=self._transport, timeout=self._timeout
        ) as client:
            while True:
                try:
                    resp = client.put(
                        ruta,
                        content=cuerpo,
                        headers={
                            "Authorization": "Bearer " + self._creds.access_token,
                            "Content-Type": "application/json",
                        },
                    )
                except httpx.HTTPError as exc:
                    logger.info("poner_precio %s red (%s)", ruta, type(exc).__name__)
                    return Acuse(aceptada=False, codigo=f"PUT {ruta} red", cuerpo={})
                if resp.status_code == 401 and forzados < 1:
                    forzados += 1
                    try:
                        self._refresco(self._creds, client)
                    except ReputacionError:
                        logger.info("poner_precio %s auth", ruta)
                        return Acuse(aceptada=False, codigo=f"PUT {ruta} auth", cuerpo={})
                    continue
                if resp.status_code == 429 and reintentos < 1:
                    reintentos += 1
                    self._sleep(_espera_retry_after(resp))
                    continue
                return self._acuse(resp, ruta, item_id, precio, len(variantes))

    @staticmethod
    def _acuse(
        resp: httpx.Response, ruta: str, item_id: str, precio: Decimal, n_vars: int
    ) -> Acuse:
        if resp.status_code == 200:
            return Acuse(
                aceptada=True,
                codigo=None,
                cuerpo={
                    "item_id": item_id,
                    "price": format(precio, ".2f"),
                    "variantes": n_vars,
                },
            )
        return Acuse(aceptada=False, codigo=f"PUT {ruta} {resp.status_code}", cuerpo={})
