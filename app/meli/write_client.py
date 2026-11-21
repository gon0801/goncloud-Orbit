"""Cliente de escritura MeLi (REPRICING 02 M.4): la unica puerta de PUT.

Espejo de `app/spapi/write_client.py`: NO hereda de `ClienteMeli` (que
sigue GET-only) y hace su propio HTTP. Nunca lee credenciales (vienen
inyectadas) ni cachea nada: una instancia por operacion.

El token es un pase del bridge (opcion 1): Orbit NUNCA refresca (no hay
`refresh_token` ni `/oauth/token` en Orbit; lo renueva el cron del
bridge). Un 401 es acuse no aceptada con el motivo claro `pase del
bridge vencido`, sin refresh ni reintento.

`modo_confirmado == "live"` exacto o `MutationNotAllowedError` antes de
construir nada. Un solo metodo publico. La forma del cuerpo (con
variaciones, el precio solo en cada una; sin ellas, en la raiz) quedo
SELLADA por la sonda X.4 del 2026-10-17 (ida y vuelta aceptadas, vivo y
control intactos).

Reintentos (nunca un loop): 401 -> acuse no aceptada `pase del bridge
vencido` (sin refresh ni reintento: Orbit nunca refresca); 429 -> un
reintento honrando `Retry-After` (espejo SP-API: default 1s, tope 60s);
lo demas, tal cual como `Acuse` (nunca levanta por un estado HTTP: el
llamador sella con el acuse).
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable, Sequence
from decimal import Decimal

import httpx

from app.http_base import parse_retry_after_finito
from app.meli.client import MELI_BASE, MeliCredentials, ReputacionError
from app.precio.puerto import Acuse
from app.redaction import install_scrub_filter

__all__ = [
    "MutationNotAllowedError",
    "MeliWriteClient",
    "validar_put_item",
]

logger = logging.getLogger(__name__)
install_scrub_filter(logger)

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
    """Con variaciones, el precio va SOLO en cada una; sin ellas, en la raiz.

    Sonda X.4 (2026-10-17): con variaciones, `price` en la raiz da 400
    `item.price.not_modifiable`. El id numerico viaja como numero (forma
    de la API); otro id viaja como texto.
    """
    for vid in variantes:
        if not isinstance(vid, str) or not vid:
            raise ValueError(f"variante invalida: {vid!r}")
    if len(set(variantes)) != len(variantes):
        raise ValueError(f"variantes duplicadas: {list(variantes)!r}")
    if not variantes:
        return f'{{"price": {precio_txt}}}'.encode()
    ids = (v if v.isdigit() else json.dumps(v) for v in variantes)
    vars_txt = ",".join(f'{{"id": {i}, "price": {precio_txt}}}' for i in ids)
    return f'{{"variations": [{vars_txt}]}}'.encode()


def _espera_retry_after(resp: httpx.Response) -> float:
    segundos = parse_retry_after_finito(
        resp.headers.get("Retry-After"), techo=_ESPERA_429_MAX, ahora_ts=time.time()
    )
    return _ESPERA_429_DEFAULT if segundos is None else segundos


class MeliWriteClient:
    """PUT de precio a items MeLi con scope sellado a la instancia."""

    def __init__(
        self,
        *,
        modo_confirmado: str,
        creds: MeliCredentials,
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
        self._transport = transport
        self._sleep = sleep
        self._timeout = timeout

    def close(self) -> None:
        """Sin estado que cerrar (el HTTP se abre por llamada)."""

    def poner_precio(
        self,
        item_id: str,
        variantes: Sequence[str],
        precio: Decimal,
        moneda: str,
    ) -> Acuse:
        """PUT del precio (raiz + variaciones)."""
        ruta = validar_put_item(f"/items/{item_id}", item_id)
        if moneda != "MXN":
            raise ValueError(f"moneda fuera del universo meli/meli: {moneda!r}")
        cuerpo = _cuerpo_put_precio(_texto_precio(precio), variantes)
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
                if resp.status_code == 401:
                    logger.info("poner_precio %s auth (pase del bridge vencido)", ruta)
                    return Acuse(
                        aceptada=False,
                        codigo=f"PUT {ruta} 401 pase del bridge vencido",
                        cuerpo={},
                    )
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
