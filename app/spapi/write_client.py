"""Cliente de escritura SP-API (REPRICING 01, A.3): la unica puerta de PATCH.

Compone un `SpapiClient` solo para el token (patron D5 de
`ProductFeesClient`: `_acceso()` + `invalidar_token()`) y hace su propio
HTTP. No hereda de `SpapiClient`, no usa `cliente_compartido`, no se
cachea: una instancia por operacion, con `platform` en vocabulario
cerrado y `seller_id` sellado a la instancia desde `VENDEDORES_PROPIOS`
(ningun metodo acepta otro). `modo_confirmado == "live"` exacto en el
constructor o `MutationNotAllowedError` antes de construir nada.

Reintentos (nunca un loop): 401 -> un refresh forzado y un reintento;
429 -> un reintento honrando `Retry-After`; 5xx y error de red -> sin
reintento (una escritura repetida a ciegas es peor que un `error`).
Devuelve la respuesta tal cual (incluso 4xx/5xx): el llamador
(`app/spapi/precio_write.py`) sella `enviado | error`.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

import httpx

from app.spapi.client import (
    ESPERA_429_DEFAULT,
    ESPERA_429_MAX,
    MERCADOS,
    SP_API_BASE,
    VENDEDORES_PROPIOS,
    SpapiClient,
    SpapiError,
    SpapiNoPermitida,
    construir_ruta_listings,
)

PLATAFORMAS_ESCRITURA = frozenset(MERCADOS)
MODO_CONFIRMADO_LIVE = "live"


class MutationNotAllowedError(SpapiError):
    """El cliente de escritura no se abre por defecto (fail-closed)."""


def validar_patch_listings(path: str, seller_id: str, sku: str) -> str:
    """Valida el unico PATCH permitido (falla antes de red).

    Mismas defensas que `validar_post_fees`: ruta relativa con `/`
    inicial, sin `://`, `?`, `#`, `%2f` ni `..`; prefijo exacto
    `/listings/2021-08-01/items/`; `seller_id` en `VENDEDORES_PROPIOS`;
    segmento del SKU exacto (percent-encoding completo). El verbo no se
    valida aqui: es estructural (el unico metodo publico manda PATCH).
    """
    if not path or not path.startswith("/") or "://" in path:
        raise SpapiNoPermitida("path listings invalido: relativo con '/' inicial")
    if "?" in path or "#" in path:
        raise SpapiNoPermitida("path listings invalido: query o fragment no permitidos")
    if "%2f" in path.lower():
        raise SpapiNoPermitida("path listings invalido: encoding de barra no permitido")
    if ".." in path.split("/"):
        raise SpapiNoPermitida("path listings invalido: traversal ('..') no permitido")
    if seller_id not in VENDEDORES_PROPIOS.values():
        raise SpapiNoPermitida(f"seller_id fuera de VENDEDORES_PROPIOS: {seller_id!r}")
    esperada = construir_ruta_listings(seller_id, sku)
    if path != esperada:
        raise SpapiNoPermitida(f"PATCH fuera de allowlist: {path}")
    return path


def _espera_retry_after(resp: httpx.Response) -> float:
    try:
        segundos = float(resp.headers.get("Retry-After", str(ESPERA_429_DEFAULT)))
    except ValueError:
        segundos = ESPERA_429_DEFAULT
    return min(max(0.0, segundos), ESPERA_429_MAX)


class SpapiWriteClient:
    """PATCH a Listings Items con scope sellado a la instancia."""

    def __init__(
        self,
        *,
        platform: str,
        modo_confirmado: str,
        lector: SpapiClient,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        timeout: float = 30.0,
    ) -> None:
        if platform not in PLATAFORMAS_ESCRITURA:
            raise ValueError(
                f"platform invalida: {platform!r} "
                f"(vocabulario cerrado: {sorted(PLATAFORMAS_ESCRITURA)})"
            )
        if modo_confirmado != MODO_CONFIRMADO_LIVE:
            raise MutationNotAllowedError(
                f"modo_confirmado={modo_confirmado!r}: el cliente de escritura "
                f"SOLO se construye en '{MODO_CONFIRMADO_LIVE}'"
            )
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        self._platform = platform
        self._seller_id = VENDEDORES_PROPIOS[MERCADOS[platform]]
        self._lector = lector
        self._transport = transport
        self._sleep = sleep
        self._timeout = timeout

    @property
    def seller_id(self) -> str:
        """Seller sellado a la instancia (solo lectura, r1-A7a)."""
        return self._seller_id

    def patch_listing(self, sku: str, cuerpo: dict) -> httpx.Response:
        """PATCH al item del seller sellado + SKU. Falla antes de red si la
        ruta no es la allowlist. 401/429: un reintento; lo demas, tal cual."""
        ruta = validar_patch_listings(
            construir_ruta_listings(self._seller_id, sku), self._seller_id, sku
        )
        url = f"{SP_API_BASE}{ruta}"
        token = self._lector._acceso()
        forzados = 0
        reintentos = 0
        with httpx.Client(transport=self._transport, timeout=self._timeout) as client:
            while True:
                resp = client.patch(url, json=cuerpo, headers={"x-amz-access-token": token})
                if resp.status_code == 401 and forzados < 1:
                    forzados += 1
                    token = self._lector._acceso(forzar=True, rechazado=token)
                    continue
                if resp.status_code == 429 and reintentos < 1:
                    reintentos += 1
                    self._sleep(_espera_retry_after(resp))
                    continue
                return resp
