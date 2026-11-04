#!/usr/bin/env python3
"""Sondas read-only SP-API Fase 0 (SP-API 01, tareas 0.1-0.5).

Cubre las cinco fuentes con GETs: orders, pricing, listings, inventario y
sellers. Cero mutaciones en Amazon y cero escrituras en base alguna: este
script solo hace GET a SP-API mas el POST de auth LWA (no es mutacion de
negocio). Jamas importa app.ads.write.

Auth: patron desplegado (app/estimacion_fees.py, app/publicacion_fotos.py):
amazon_credentials.json desde ORBIT_SECRETS_DIR (claves lwa_app_id,
lwa_client_secret, refresh_token), refresh LWA contra
https://api.amazon.com/auth/o2/token y header x-amz-access-token contra
sellingpartnerapi-na.amazon.com. Un refresh por corrida; uno forzado extra
solo ante un 401; un reintento acotado ante un 429. Nunca loop.

Guard default-deny: solo GET y solo los paths de RUTAS_FIJAS por igualdad
literal mas dos plantillas con parametro validado (item de listings por
sellerId+sku, ofertas por ASIN). Todo lo demas falla antes de red.

Orders v0 + 2026-01-01 (ronda 1): la fuente corre AMBAS versiones con lineas
separadas. El path de searchOrders (GET /orders/2026-01-01/orders) se tomo
del modelo JSON oficial amzn/selling-partner-api-models
(models/orders-api-model/orders_2026-01-01.json, operacion searchOrders),
no inventado. Referencia:
https://developer-docs.amazon/sp-api/reference/orders-v2026-01-01 y guia:
https://developer-docs.amazon/sp-api/docs/orders-api-migration-guide.
v0 usa MarketplaceIds/CreatedAfter/NextToken; 2026-01-01 usa
marketplaceIds/createdAfter/paginationToken, sin includedData=BUYER.

Orders sin PII: no se pide ni se imprime comprador, recipiente ni direccion;
si Amazon exige Restricted Data Token se declara y se sale.

Listings (ronda 1): GET /sellers/v1/account es solo EU y no trae sellerId,
asi que la sonda no lo usa. --fuente listings exige --seller-id.
Procedencia del valor (para el acta 0.5): el accountInfo.id del perfil de
Ads de Amazon MX, leido con el cliente de SOLO lectura ya existente
(app.ads.client.AdsClient.get("/v2/profiles"), el mismo camino de
app/ads/structure_api.py). Es el ID publico de vendedor, no un secreto.
Sellers usa marketplaceParticipations como primaria; account queda opcional
y un 4xx en NA se declara sin invalidar el veredicto.

Paginacion (Orders en sus dos versiones e Inventario): se sigue el token
(NextToken/nextToken/paginationToken) hasta --max-paginas; token repetido
o pagina vacia con token paran y se marcan (bugs conocidos).

Salida: una linea JSON por GET mas un resumen final por fuente, todo por
scrub() de app/redaction.py. Campos por linea: fuente, mercado, endpoint,
version, status, claves_top, claves_item (claves del primer elemento, ya
saneado), conteo, paginas, rate_limit (headers x-amzn-RateLimit-*),
aviso_deprecacion, errores (con fragmento scrubbado del cuerpo, <=200).

Uso desde el contenedor (patron de tools/fabrica_campanas.py):

    docker exec -i orbit-app-1 python3 - --fuente sellers --mercado amazon_mx \\
        < tools/sonda_spapi.py
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import sys
from typing import Any

import httpx

from app.redaction import install_scrub_filter, scrub

# Cliente unico canonico (SP-API 01 A.1, D5): la sonda importa de app.spapi,
# una sola copia del guard, PII, paginacion y refrescador LWA.
from app.spapi import client as _spapi

install_scrub_filter(logging.getLogger())

logger = logging.getLogger(__name__)

SP_API_HOST = _spapi.SP_API_HOST
SP_API_BASE = _spapi.SP_API_BASE
LWA_TOKEN_URL = _spapi.LWA_TOKEN_URL
TOKEN_MARGEN_SEGUNDOS = _spapi.TOKEN_MARGEN_SEGUNDOS
ESPERA_429_DEFAULT = _spapi.ESPERA_429_DEFAULT
ESPERA_429_MAX = _spapi.ESPERA_429_MAX

MERCADOS = _spapi.MERCADOS

# URL oficial pineada por fuente (dominio developer-docs.amazon, plan 0.1-0.4;
# sellers sigue el mismo patron de dominio, ya verificado en 0.5).
DOCS = {
    "orders": "https://developer-docs.amazon/sp-api/docs/orders-api-v0-reference",
    "orders_2026": "https://developer-docs.amazon/sp-api/reference/orders-v2026-01-01",
    "pricing": "https://developer-docs.amazon/sp-api/docs/product-pricing-api-v0-reference",
    "listings": (
        "https://developer-docs.amazon/sp-api/docs/listings-items-api-v2021-08-01-reference"
    ),
    "inventario": "https://developer-docs.amazon/sp-api/docs/fbainventory-api-v1-reference",
    "sellers": "https://developer-docs.amazon/sp-api/docs/sellers-api-v1-reference",
}
GUIA_MIGRACION_ORDERS = "https://developer-docs.amazon/sp-api/docs/orders-api-migration-guide"
VERSION_POR_FUENTE = {
    "orders": "v0",
    "pricing": "v0",
    "listings": "2021-08-01",
    "inventario": "v1",
    "sellers": "v1",
}

RUTA_ORDERS_V0 = "/orders/v0/orders"
# searchOrders del modelo oficial orders_2026-01-01.json (no inventado).
RUTA_ORDERS_NUEVA = "/orders/2026-01-01/orders"
VERSION_ORDERS_NUEVA = "2026-01-01"
# Metadato fijo en cada linea v0 aunque Amazon no lo mande en headers.
AVISO_V0_DEPRECADA = "v0 deprecada; retiro 2027-03-27; sucesora 2026-01-01"

# Allowlist canonica en app.spapi (una sola copia; la sonda re-exporta).
# Nota: el canonico admite ademas Catalog Items 2022-04-01 (A.1 lo exige);
# el resto es identico a la Fase 0.
RUTAS_FIJAS = _spapi.RUTAS_FIJAS
_PREFIJO_LISTINGS = _spapi._PREFIJO_LISTINGS
_PREFIJO_OFERTAS = _spapi._PREFIJO_OFERTAS
_SUFIJO_OFERTAS = _spapi._SUFIJO_OFERTAS
_PREFIJO_CATALOGO = _spapi._PREFIJO_CATALOGO

_ASIN_RE = _spapi._ASIN_RE
_SELLER_RE = _spapi._SELLER_RE

_PII_GLOBAL = _spapi._PII_GLOBAL
_PII_ORDERS = _spapi._PII_ORDERS


SondaError = _spapi.SpapiError
SondaNoPermitida = _spapi.SpapiNoPermitida


def _es_pii(clave: str, fuente: str) -> bool:
    return _spapi._es_pii(clave, fuente)


def sanear(obj: Any, fuente: str) -> Any:
    """Copia sin PII (canonica en app.spapi)."""
    return _spapi.sanear(obj, fuente)


def validar_get(path: str) -> str:
    """Guard GET canonico en app.spapi (mas Catalog Items desde A.1)."""
    try:
        return _spapi.validar_get(path)
    except _spapi.SpapiNoPermitida as exc:
        raise SondaNoPermitida(str(exc)) from None


def _validar_segmento_sku(segmento: str) -> str:
    try:
        return _spapi._validar_segmento_sku(segmento)
    except _spapi.SpapiNoPermitida as exc:
        raise SondaNoPermitida(str(exc)) from None


def construir_ruta_listings(seller_id: str, sku: str) -> str:
    """Ruta listings canonica en app.spapi."""
    try:
        return _spapi.construir_ruta_listings(seller_id, sku)
    except _spapi.SpapiNoPermitida as exc:
        raise SondaNoPermitida(str(exc)) from None


def construir_ruta_ofertas(asin: str) -> str:
    """Ruta de ofertas canonica en app.spapi."""
    try:
        return _spapi.construir_ruta_ofertas(asin)
    except _spapi.SpapiNoPermitida as exc:
        raise SondaNoPermitida(str(exc)) from None


def construir_ruta_catalogo(asin: str) -> str:
    """Ruta de catalogo canonica en app.spapi (nueva en A.1)."""
    try:
        return _spapi.construir_ruta_catalogo(asin)
    except _spapi.SpapiNoPermitida as exc:
        raise SondaNoPermitida(str(exc)) from None


def _contenedor(carga: Any) -> Any:
    return _spapi._contenedor(carga)


def claves_top(carga: Any, tope: int = 15) -> list[str]:
    """Claves de primer nivel para el acta (sin validar contrato)."""
    cont = _contenedor(carga)
    if isinstance(cont, dict):
        return sorted(str(k) for k in cont)[:tope]
    if isinstance(cont, list) and cont and isinstance(cont[0], dict):
        return sorted(str(k) for k in cont[0])[:tope]
    if isinstance(cont, list):
        return [f"lista_n={len(cont)}"]
    return ["escalar"]


_LISTAS_CONOCIDAS = (
    "Orders",
    "orders",
    "inventorySummaries",
    "Offers",
    "CompetitivePrices",
    "Prices",
)


def claves_item(carga: Any, tope: int = 15) -> list[str]:
    """Claves del primer elemento de la lista principal (ya saneado).

    Heuristica para el acta sin validar contrato: la lista es el contenedor
    directo o la primera de las claves conocidas (y como respaldo, la primera
    lista que aparezca en el dict).
    """
    cont = _contenedor(carga)
    lista = cont if isinstance(cont, list) else None
    if lista is None and isinstance(cont, dict):
        for clave in _LISTAS_CONOCIDAS:
            valor = cont.get(clave)
            if isinstance(valor, list):
                lista = valor
                break
        else:
            for valor in cont.values():
                if isinstance(valor, list):
                    lista = valor
                    break
    if lista and isinstance(lista[0], dict):
        return sorted(str(k) for k in lista[0])[:tope]
    return []


def conteo_items(carga: Any, fuente: str) -> int:
    """Conteo de elementos segun la fuente (generico, no valida negocio)."""
    cont = _contenedor(carga)
    if fuente == "orders" and isinstance(cont, dict):
        # v0 responde Orders (mayuscula); 2026-01-01 responde orders.
        for clave in ("Orders", "orders"):
            ordenes = cont.get(clave)
            if isinstance(ordenes, list):
                return len(ordenes)
        return 0
    if fuente == "inventario" and isinstance(cont, dict):
        resumenes = cont.get("inventorySummaries")
        return len(resumenes) if isinstance(resumenes, list) else 0
    if fuente == "pricing":
        if isinstance(cont, dict):
            for clave in ("Offers", "CompetitivePrices", "Prices"):
                valor = cont.get(clave)
                if isinstance(valor, list):
                    return len(valor)
            return 1 if cont else 0
        return len(cont) if isinstance(cont, list) else 0
    if isinstance(cont, list):
        if fuente == "sellers" and cont and isinstance(cont[0], dict):
            return len(cont)
        return len(cont)
    if isinstance(cont, dict):
        for valor in cont.values():
            if isinstance(valor, list):
                return len(valor)
        return 1 if cont else 0
    return 0


def _token_de_paginacion(obj: Any) -> str | None:
    return _spapi._token_de_paginacion(obj)


def siguiente_token(carga: Any) -> str | None:
    """Paginacion canonica en app.spapi (NextToken/nextToken/paginationToken)."""
    return _spapi.siguiente_token(carga)


def rate_limit_de(headers: Any) -> dict[str, str]:
    """RateLimit canonico en app.spapi."""
    return _spapi.rate_limit_de(headers)


def aviso_deprecacion(resp: httpx.Response, carga: Any) -> str | None:
    """Avisos de deprecacion en headers o cuerpo (heuristicos, se pinean)."""
    avisos = [
        f"{nombre}={valor}"
        for nombre, valor in dict(resp.headers).items()
        if "deprecat" in str(nombre).lower() or "sunset" in str(nombre).lower()
    ]
    cont = _contenedor(carga)
    if isinstance(cont, dict):
        avisos.extend(str(k) for k in cont if "deprecat" in str(k).lower())
    return "; ".join(avisos) if avisos else None


def _fragmento(texto: str, tope: int = 200) -> str:
    """Fragmento de cuerpo de error para errores, siempre scrubbado."""
    return scrub(texto.strip()[:tope])


def _espera_retry(resp: httpx.Response) -> float:
    return _spapi._espera_retry(resp)


def _log(evento: str, **campos: Any) -> None:
    print(scrub(json.dumps({"evento": evento, **campos}, default=str)), flush=True)


class SondaClient(_spapi.SpapiClient):
    """Sonda read-only: subclase fina del cliente unico (compat Fase 0).

    Hereda guard, PII, paginacion y refrescador de app.spapi; `get()` ahora
    admite ademas Catalog Items (A.1). Los sondeos solo usan los paths de
    Fase 0, asi que el comportamiento es identico.
    """


def _linea_error(
    *,
    fuente: str,
    mercado: str,
    endpoint: str,
    version: str,
    pagina: int,
    errores: list[str],
) -> None:
    _log(
        "detalle",
        fuente=fuente,
        mercado=mercado,
        endpoint=endpoint,
        version=version,
        status=None,
        claves_top=[],
        claves_item=[],
        conteo=0,
        paginas=pagina,
        rate_limit={},
        aviso_deprecacion=None,
        errores=errores,
    )


def _get_y_linea(
    client: SondaClient,
    *,
    fuente: str,
    mercado: str,
    endpoint: str,
    params: dict | None,
    pagina: int,
    version: str | None = None,
    aviso_fijo: str | None = None,
) -> tuple[int | None, int, str | None, str | None, list[str]]:
    """Un GET + su linea JSON. Devuelve (status, conteo, token, aviso, errores).

    version sobreescribe la de VERSION_POR_FUENTE (orders dual); aviso_fijo
    se antepone al aviso de headers (deprecacion pineada de v0).
    """
    version = version or VERSION_POR_FUENTE[fuente]
    try:
        resp = client.get(endpoint, params=params)
    except (SondaError, httpx.HTTPError) as exc:
        # str(exc) tambien pasa por scrub: un mensaje de red podria ecoar
        # datos sensibles (F4).
        errores = [f"{type(exc).__name__}:{scrub(str(exc))[:200]}"]
        _linea_error(
            fuente=fuente,
            mercado=mercado,
            endpoint=endpoint,
            version=version,
            pagina=pagina,
            errores=errores,
        )
        return None, 0, None, None, errores
    try:
        carga = resp.json()
    except ValueError:
        carga = {}
    fragmento = _fragmento(resp.text)
    if resp.status_code == 403 and "restricteddatatoken" in resp.text.lower():
        errores = ["rdt_requerido"]
        if fragmento:
            errores.append(f"fragmento:{fragmento}")
        _log(
            "detalle",
            fuente=fuente,
            mercado=mercado,
            endpoint=endpoint,
            version=version,
            status=resp.status_code,
            claves_top=[],
            claves_item=[],
            conteo=0,
            paginas=pagina,
            rate_limit=rate_limit_de(resp.headers),
            aviso_deprecacion=aviso_deprecacion(resp, {}),
            errores=errores,
        )
        return resp.status_code, 0, None, None, errores
    carga = sanear(carga, fuente)
    errores = []
    if resp.status_code != 200:
        errores.append(f"http_{resp.status_code}")
        if fragmento:
            errores.append(f"fragmento:{fragmento}")
    aviso = aviso_deprecacion(resp, carga)
    if aviso_fijo and aviso:
        aviso = f"{aviso_fijo}; {aviso}"
    elif aviso_fijo:
        aviso = aviso_fijo
    _log(
        "detalle",
        fuente=fuente,
        mercado=mercado,
        endpoint=endpoint,
        version=version,
        status=resp.status_code,
        claves_top=claves_top(carga),
        claves_item=claves_item(carga),
        conteo=conteo_items(carga, fuente),
        paginas=pagina,
        rate_limit=rate_limit_de(resp.headers),
        aviso_deprecacion=aviso,
        errores=errores,
    )
    return (
        resp.status_code,
        conteo_items(carga, fuente),
        siguiente_token(carga),
        aviso,
        errores,
    )


def _resumen_base(
    fuente: str, mercado: str, *, version: str | None = None, doc: str | None = None
) -> dict[str, Any]:
    return {
        "fuente": fuente,
        "mercado": mercado,
        "version": version or VERSION_POR_FUENTE[fuente],
        "doc": doc or DOCS[fuente],
        "paginas": 0,
        "conteo_total": 0,
        "aviso_paginacion": None,
        "aviso_deprecacion": None,
        "errores": [],
    }


def sondear_sellers(client: SondaClient, mercado: str) -> dict[str, Any]:
    """0.5: identidad con marketplaceParticipations como primaria.

    account queda opcional: GET /sellers/v1/account es solo EU y en NA un
    4xx se declara en notas sin invalidar el veredicto.
    """
    resumen = _resumen_base("sellers", mercado)
    resumen["endpoints"] = [
        "/sellers/v1/marketplaceParticipations",
        "/sellers/v1/account",
    ]
    resumen["notas"] = []
    status, conteo, _, aviso, errores = _get_y_linea(
        client,
        fuente="sellers",
        mercado=mercado,
        endpoint="/sellers/v1/marketplaceParticipations",
        params=None,
        pagina=1,
    )
    resumen["paginas"] = 1
    resumen["conteo_total"] = conteo
    resumen["aviso_deprecacion"] = aviso
    resumen["errores"].extend(errores)
    ok = status == 200
    status_a, _, _, aviso_a, errores_a = _get_y_linea(
        client,
        fuente="sellers",
        mercado=mercado,
        endpoint="/sellers/v1/account",
        params=None,
        pagina=2,
    )
    resumen["paginas"] = 2
    if aviso_a and resumen["aviso_deprecacion"] is None:
        resumen["aviso_deprecacion"] = aviso_a
    if status_a != 200:
        resumen["notas"].append(f"account status={status_a} declarado (EU; no invalida)")
        resumen["notas"].extend(errores_a)
    resumen["veredicto"] = "verificada" if ok else "no_verificada"
    return resumen


def sondear_listings(
    client: SondaClient, mercado: str, *, sku: str, seller_id: str | None
) -> dict[str, Any]:
    """0.3: estado del listing por sellerId (parametro) + sku.

    El sellerId NO sale de getAccount (solo EU y sin sellerId): llega por
    --seller-id (accountInfo.id del perfil Ads MX, ver docstring).
    """
    resumen = _resumen_base("listings", mercado)
    if not (sku or "").strip():
        resumen["errores"] = ["sin_sku"]
        resumen["veredicto"] = "no_verificada"
        return resumen
    if not _SELLER_RE.match(seller_id or ""):
        # Sin sellerId no se llama al item: falla antes de red.
        resumen["errores"] = ["sin_seller_id"]
        resumen["veredicto"] = "no_verificada"
        return resumen
    endpoint = construir_ruta_listings((seller_id or "").strip(), (sku or "").strip())
    resumen["endpoints"] = [endpoint]
    status, conteo, _, aviso, errores_item = _get_y_linea(
        client,
        fuente="listings",
        mercado=mercado,
        endpoint=endpoint,
        params={"marketplaceIds": MERCADOS[mercado]},
        pagina=1,
    )
    resumen["paginas"] = 1
    resumen["conteo_total"] = conteo
    resumen["aviso_deprecacion"] = aviso
    resumen["errores"].extend(errores_item)
    resumen["veredicto"] = "verificada" if status == 200 and not errores_item else "no_verificada"
    return resumen


def sondear_pricing(client: SondaClient, mercado: str, *, asin: str) -> dict[str, Any]:
    """0.2: ofertas por ASIN (Buy Box) + precios competitivos."""
    if not _ASIN_RE.match(asin or ""):
        raise SondaError(f"ASIN invalido (10 caracteres A-Z0-9): {asin!r}")
    resumen = _resumen_base("pricing", mercado)
    mid = MERCADOS[mercado]
    llamadas = [
        (construir_ruta_ofertas(asin), {"MarketplaceId": mid, "ItemCondition": "New"}),
        (
            "/products/pricing/v0/competitivePrice",
            {"MarketplaceId": mid, "ItemType": "Asin", "Asins": asin},
        ),
    ]
    resumen["endpoints"] = [endpoint for endpoint, _ in llamadas]
    ok = True
    for pagina, (endpoint, params) in enumerate(llamadas, start=1):
        status, conteo, _, aviso, errores = _get_y_linea(
            client,
            fuente="pricing",
            mercado=mercado,
            endpoint=endpoint,
            params=params,
            pagina=pagina,
        )
        resumen["paginas"] += 1
        resumen["conteo_total"] += conteo
        resumen["errores"].extend(errores)
        if aviso and resumen["aviso_deprecacion"] is None:
            resumen["aviso_deprecacion"] = aviso
        if status != 200:
            ok = False
    resumen["veredicto"] = "verificada" if ok else "no_verificada"
    return resumen


def _paginar(
    client: SondaClient,
    *,
    fuente: str,
    mercado: str,
    endpoint: str,
    params_base: dict,
    param_token: str,
    max_paginas: int,
    version: str | None = None,
    doc: str | None = None,
    aviso_fijo: str | None = None,
) -> dict[str, Any]:
    """Bucle de token con los dos bugs conocidos como condicion de paro.

    Las guardas (token repetido, pagina vacia con token) aplican a los tres
    nombres de token: NextToken (v0), nextToken y paginationToken (2026-01-01).
    """
    resumen = _resumen_base(fuente, mercado, version=version, doc=doc)
    resumen["endpoints"] = [endpoint]
    vistos: set[str] = set()
    token: str | None = None
    for pagina in range(1, max_paginas + 1):
        params = dict(params_base)
        if token is not None:
            params[param_token] = token
        status, conteo, siguiente, aviso, errores = _get_y_linea(
            client,
            fuente=fuente,
            mercado=mercado,
            endpoint=endpoint,
            params=params,
            pagina=pagina,
            version=version,
            aviso_fijo=aviso_fijo,
        )
        resumen["paginas"] = pagina
        resumen["conteo_total"] += conteo
        resumen["errores"].extend(errores)
        if aviso and resumen["aviso_deprecacion"] is None:
            resumen["aviso_deprecacion"] = aviso
        if status != 200:
            break
        if "rdt_requerido" in errores:
            break
        if siguiente is None:
            break
        if siguiente in vistos:
            resumen["aviso_paginacion"] = "next_token_repetido"
            break
        if conteo == 0:
            resumen["aviso_paginacion"] = "pagina_vacia_con_token"
            break
        vistos.add(siguiente)
        token = siguiente
    else:
        # Se agoto max_paginas con token pendiente: recorrido parcial.
        if siguiente is not None:
            resumen["aviso_paginacion"] = "limite_max_paginas"
    resumen["veredicto"] = "verificada" if not resumen["errores"] else "no_verificada"
    return resumen


def _ventana_orders(dias: int, ahora: datetime.datetime | None) -> str:
    # v0 exige Zulu (400 InvalidInput con +00:00, sonda 2026-09-09); Z vale
    # para ambas versiones.
    if dias < 1:
        raise SondaError("--dias debe ser >= 1")
    base = ahora or datetime.datetime.now(datetime.UTC)
    if base.tzinfo is None:
        base = base.replace(tzinfo=datetime.UTC)
    return (
        (base - datetime.timedelta(days=dias))
        .astimezone(datetime.UTC)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def sondear_orders_v0(
    client: SondaClient,
    mercado: str,
    *,
    dias: int = 7,
    max_paginas: int = 3,
    ahora: datetime.datetime | None = None,
) -> dict[str, Any]:
    """0.1 (v0): recorrido con CreatedAfter reciente y MaxResults bajo.

    Aqui se reproducen los dos bugs de TRASPASO-1 (NextToken repetido y
    pagina vacia con token). Cada linea lleva el aviso fijo de deprecacion.
    """
    if max_paginas < 1:
        raise SondaError("--max-paginas debe ser >= 1")
    return _paginar(
        client,
        fuente="orders",
        mercado=mercado,
        endpoint=RUTA_ORDERS_V0,
        params_base={
            "MarketplaceIds": MERCADOS[mercado],
            "CreatedAfter": _ventana_orders(dias, ahora),
            "MaxResultsPerPage": "10",
        },
        param_token="NextToken",
        max_paginas=max_paginas,
        version="v0",
        aviso_fijo=AVISO_V0_DEPRECADA,
    )


def sondear_orders_nueva(
    client: SondaClient,
    mercado: str,
    *,
    dias: int = 7,
    max_paginas: int = 3,
    ahora: datetime.datetime | None = None,
) -> dict[str, Any]:
    """0.1 (2026-01-01): searchOrders con minusculas, sin includedData=BUYER."""
    if max_paginas < 1:
        raise SondaError("--max-paginas debe ser >= 1")
    return _paginar(
        client,
        fuente="orders",
        mercado=mercado,
        endpoint=RUTA_ORDERS_NUEVA,
        params_base={
            "marketplaceIds": MERCADOS[mercado],
            "createdAfter": _ventana_orders(dias, ahora),
            "maxResultsPerPage": "10",
        },
        param_token="paginationToken",
        max_paginas=max_paginas,
        version=VERSION_ORDERS_NUEVA,
        doc=DOCS["orders_2026"],
    )


def sondear_orders(
    client: SondaClient,
    mercado: str,
    *,
    dias: int = 7,
    max_paginas: int = 3,
    ahora: datetime.datetime | None = None,
) -> dict[str, Any]:
    """0.1: corre AMBAS versiones y agrega el resumen por fuente."""
    r0 = sondear_orders_v0(client, mercado, dias=dias, max_paginas=max_paginas, ahora=ahora)
    rn = sondear_orders_nueva(client, mercado, dias=dias, max_paginas=max_paginas, ahora=ahora)
    avisos_pag = [a for a in (r0["aviso_paginacion"], rn["aviso_paginacion"]) if a]
    avisos_dep = [a for a in (r0["aviso_deprecacion"], rn["aviso_deprecacion"]) if a]
    return {
        "fuente": "orders",
        "mercado": mercado,
        "version": f"v0+{VERSION_ORDERS_NUEVA}",
        "doc": DOCS["orders_2026"],
        "endpoints": [RUTA_ORDERS_V0, RUTA_ORDERS_NUEVA],
        "versiones": {"v0": r0, VERSION_ORDERS_NUEVA: rn},
        "paginas": r0["paginas"] + rn["paginas"],
        "conteo_total": r0["conteo_total"] + rn["conteo_total"],
        "aviso_paginacion": "; ".join(avisos_pag) if avisos_pag else None,
        "aviso_deprecacion": "; ".join(avisos_dep) if avisos_dep else None,
        "errores": r0["errores"] + rn["errores"],
        "veredicto": (
            "verificada"
            if r0["veredicto"] == "verificada" and rn["veredicto"] == "verificada"
            else "no_verificada"
        ),
    }


def sondear_inventario(
    client: SondaClient, mercado: str, *, max_paginas: int = 3
) -> dict[str, Any]:
    """0.4: resumenes FBA por marketplace con paginacion."""
    if max_paginas < 1:
        raise SondaError("--max-paginas debe ser >= 1")
    mid = MERCADOS[mercado]
    return _paginar(
        client,
        fuente="inventario",
        mercado=mercado,
        endpoint="/fba/inventory/v1/summaries",
        params_base={
            "granularityType": "Marketplace",
            "granularityId": mid,
            "marketplaceIds": mid,
        },
        param_token="nextToken",
        max_paginas=max_paginas,
    )


SONDEOS = {
    "orders": "sonda Orders v0 + 2026-01-01 (recorrido con guardas en ambas)",
    "pricing": "sonda Pricing v0 (ofertas por ASIN + competitivos)",
    "listings": "sonda Listings 2021-08-01 (item por --seller-id + --sku)",
    "inventario": "sonda Inventario FBA v1 (resumenes por marketplace)",
    "sellers": "sonda Sellers v1 (participations primaria; account opcional)",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sonda_spapi", description="Sondas read-only SP-API Fase 0 (solo GET)."
    )
    parser.add_argument("--fuente", required=True, choices=sorted(SONDEOS))
    parser.add_argument("--mercado", required=True, choices=sorted(MERCADOS))
    parser.add_argument("--asin", default=None, help="ASIN de 10 caracteres (pricing)")
    parser.add_argument("--sku", default=None, help="seller SKU (listings)")
    parser.add_argument(
        "--seller-id",
        default=None,
        help="ID publico de vendedor, accountInfo.id del perfil Ads MX (listings)",
    )
    parser.add_argument("--max-paginas", type=int, default=3)
    parser.add_argument("--dias", type=int, default=7, help="ventana CreatedAfter (orders)")
    args = parser.parse_args(argv)

    try:
        client = SondaClient()
    except SondaError as exc:
        _log("error_fatal", errores=[scrub(str(exc))])
        return 1

    try:
        if args.fuente == "orders":
            resumen = sondear_orders(
                client, args.mercado, dias=args.dias, max_paginas=args.max_paginas
            )
        elif args.fuente == "pricing":
            if not args.asin:
                raise SondaError("pricing exige --asin")
            resumen = sondear_pricing(client, args.mercado, asin=args.asin)
        elif args.fuente == "listings":
            if not args.sku:
                raise SondaError("listings exige --sku")
            if not args.seller_id:
                raise SondaError("listings exige --seller-id")
            resumen = sondear_listings(client, args.mercado, sku=args.sku, seller_id=args.seller_id)
        elif args.fuente == "inventario":
            resumen = sondear_inventario(client, args.mercado, max_paginas=args.max_paginas)
        else:
            resumen = sondear_sellers(client, args.mercado)
    except SondaError as exc:
        _log(
            "error_fatal",
            fuente=args.fuente,
            mercado=args.mercado,
            errores=[scrub(str(exc))],
        )
        return 1
    _log("resumen", **resumen, refreshes_lwa=client.refreshes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
