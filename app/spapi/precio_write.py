"""Lo de Amazon de la escritura de precios (REPRICING 01 A.3 + 02 0.b).

El protocolo de `precio_cambio` (aplicar, revertir, cierre por observacion,
huerfanas) vive desde 0.b en `app/precio/cambios.py`; aqui queda la mitad
Amazon: el cuerpo del PATCH, el escritor default-deny y la lectura del
precio vivo. Este sigue siendo el UNICO importador de `write_client`.

La forma del cuerpo del PATCH quedo sellada por la sonda A.4 con ids reales:
`purchasable_offer` de Listings Items 2021-08-01, con el importe como string
decimal. Los tests todavia pueden inyectar `construir_cuerpo=`.

`leer_precio_vivo` usa el GET de ofertas de Pricing con `parsear_precios`
(parser ya probado en produccion). Sin oferta propia o sin moneda ->
`PrecioVivoAusente`: nunca un precio supuesto.

El cuerpo nunca se loguea: ni el del PATCH ni la respuesta cruda; el `ack`
que se guarda pasa por `scrub()` y los errores llevan metodo + ruta (sin
SKU) + status.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import httpx

from app.precio.cambios import (
    CambioNoReversible,
    DecisionSinAccion,
    PrecioVivo,
    PrecioVivoAusente,
    PublicacionSinSku,
    ResultadoCambio,
    ResultadoReversion,
    aplicar,
    cerrar_huerfanas,
    cerrar_por_observacion,
    revertir,
)
from app.redaction import scrub
from app.spapi.client import (
    MERCADOS,
    VENDEDORES_PROPIOS,
    CuboTasa,
    SpapiClient,
    construir_ruta_ofertas,
)
from app.spapi.pricing import RUTA_COMPETITIVO, PrecioOmitido, parsear_precios
from app.spapi.write_client import SpapiWriteClient

FORMA_PARCHE = "listings_items_purchasable_offer_v1"


class FormaParcheSinSellar(Exception):
    """El cuerpo del PATCH aun no tiene forma sellada (A.4 la sella)."""


def construir_escritor(
    lector: SpapiClient,
    platform: str,
    *,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
    timeout: float = 30.0,
) -> SpapiWriteClient:
    """Un escritor por operacion (sin instancia compartida ni cache)."""
    return SpapiWriteClient(
        platform=platform,
        modo_confirmado="live",
        lector=lector,
        transport=transport,
        sleep=sleep,
        timeout=timeout,
    )


def construir_cuerpo_parche(*, platform: str, sku: str, precio: Decimal, moneda: str) -> dict:
    """Cuerpo `purchasable_offer` aceptado por la sonda real A.4."""
    if FORMA_PARCHE != "listings_items_purchasable_offer_v1":
        raise FormaParcheSinSellar(f"forma desconocida: {FORMA_PARCHE}")
    return {
        "productType": "PRODUCT",
        "patches": [
            {
                "op": "replace",
                "path": "/attributes/purchasable_offer",
                "value": [
                    {
                        "currency": moneda,
                        "audience": "ALL",
                        "our_price": [{"schedule": [{"value_with_tax": format(precio, ".2f")}]}],
                        "marketplace_id": MERCADOS[platform],
                    }
                ],
            }
        ],
    }


def leer_precio_vivo(
    lector: SpapiClient,
    *,
    platform: str,
    asin: str,
    limitador: CuboTasa | None = None,
) -> PrecioVivo:
    """Precio propio vivo por el GET de ofertas de Pricing + `parsear_precios`.

    Las ofertas se parsean primero con competitivo vacio: si traen la
    oferta propia, el competitivo ni se pide (r5-L2, cuota Pricing 0.5/s)
    y su falla nunca bloquea. Solo sin propia se pide el competitivo, y
    cualquier falla suya (no-200 o JSON malo) es respaldo vacio. Sin
    oferta propia en ninguno, non-200 u ofertas no JSON ->
    `PrecioVivoAusente`: un dato malo de la fuente no tumba la operacion.
    Con `limitador`, ambas lecturas consumen del mismo cubo (r6-C5).
    """
    if platform not in MERCADOS:
        raise ValueError(f"platform invalida: {platform!r}")
    marketplace = MERCADOS[platform]
    propio = VENDEDORES_PROPIOS[marketplace]
    ruta = construir_ruta_ofertas(asin)
    resp = lector.get(
        ruta,
        params={"MarketplaceId": marketplace, "ItemCondition": "New"},
        limitador=limitador,
    )
    if resp.status_code != 200:
        raise PrecioVivoAusente(f"pricing {asin} status={resp.status_code}")
    try:
        ofertas = resp.json()
    except ValueError:
        raise PrecioVivoAusente(f"pricing {asin} respuesta no JSON") from None
    try:
        parsed = parsear_precios(asin, ofertas, {"payload": []}, vendedor_propio=propio)
    except PrecioOmitido as exc:
        raise PrecioVivoAusente(f"pricing {asin} omitido: {exc.motivo}") from None
    if parsed.own_price is not None and parsed.own_currency is not None:
        return PrecioVivo(precio=parsed.own_price, moneda=parsed.own_currency)
    comp = lector.get(
        RUTA_COMPETITIVO,
        params={"MarketplaceId": marketplace, "ItemType": "Asin", "Asins": asin},
        limitador=limitador,
    )
    if comp.status_code != 200:
        # El competitivo es respaldo: su caida no bloquea; sin propia
        # sigue siendo ausente abajo.
        competitivas = {"payload": []}
    else:
        try:
            competitivas = comp.json()
        except ValueError:
            competitivas = {"payload": []}
    try:
        parsed = parsear_precios(asin, ofertas, competitivas, vendedor_propio=propio)
    except PrecioOmitido as exc:
        raise PrecioVivoAusente(f"pricing {asin} omitido: {exc.motivo}") from None
    if parsed.own_price is None or parsed.own_currency is None:
        raise PrecioVivoAusente(f"pricing {asin} sin oferta propia")
    return PrecioVivo(precio=parsed.own_price, moneda=parsed.own_currency)


__all__ = [
    "FORMA_PARCHE",
    "FormaParcheSinSellar",
    "PrecioVivoAusente",
    "CambioNoReversible",
    "DecisionSinAccion",
    "PublicacionSinSku",
    "PrecioVivo",
    "ResultadoReversion",
    "ResultadoCambio",
    "aplicar",
    "revertir",
    "cerrar_huerfanas",
    "cerrar_por_observacion",
    "construir_escritor",
    "construir_cuerpo_parche",
    "leer_precio_vivo",
    "_validar_escritor",
    "_estado_aceptado",
    "_ruta_sin_sku",
    "_ack_saneado",
]


def _validar_escritor(escritor: SpapiWriteClient, platform: str) -> None:
    """El escritor va al mercado de la fila (r5-L1): con cuenta unificada
    NA un escritor cruzado aplicaria de verdad el PATCH en la otra plaza.
    Mal uso -> `ValueError`, ANTES de cualquier INSERT o red."""
    if escritor.platform != platform:
        raise ValueError(
            f"plataforma_distinta: escritor={escritor.platform} fila={platform}"
            " (el escritor se construye con la platform de la fila)"
        )


def _estado_aceptado(resp: httpx.Response) -> tuple[bool, Any]:
    try:
        cuerpo = resp.json()
    except ValueError:
        return False, None
    if not isinstance(cuerpo, dict):
        return False, cuerpo
    aceptado = (
        200 <= resp.status_code < 300
        and cuerpo.get("submissionId") is not None
        and cuerpo.get("status") == "ACCEPTED"
    )
    return aceptado, cuerpo


def _ack_saneado(resp: httpx.Response) -> dict:
    try:
        texto = resp.text
    except Exception:
        texto = ""
    return {"http_status": resp.status_code, "cuerpo": scrub(texto)[:2000]}


def _ruta_sin_sku(seller_id: str) -> str:
    return f"/listings/2021-08-01/items/{seller_id}"
