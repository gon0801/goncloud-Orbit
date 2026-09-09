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

Orders sin PII: no se pide ni se imprime comprador ni direccion; si Amazon
exige Restricted Data Token se declara y se sale.

Paginacion (Orders e Inventario): se sigue NextToken hasta --max-paginas;
token repetido o pagina vacia con token paran y se marcan (bugs conocidos).

Salida: una linea JSON por GET mas un resumen final por fuente, todo por
scrub() de app/redaction.py. Campos por linea: fuente, mercado, endpoint,
version, status, claves_top, conteo, paginas, rate_limit (headers
x-amzn-RateLimit-*), aviso_deprecacion, errores.

Uso desde el contenedor (patron de tools/fabrica_campanas.py):

    docker exec -i orbit-app-1 python3 - --fuente sellers --mercado amazon_mx \\
        < tools/sonda_spapi.py
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

import httpx

from app.redaction import install_scrub_filter, register_secret, scrub

install_scrub_filter(logging.getLogger())

logger = logging.getLogger(__name__)

SP_API_HOST = "sellingpartnerapi-na.amazon.com"
SP_API_BASE = f"https://{SP_API_HOST}"
LWA_TOKEN_URL = "https://api.amazon.com/auth/o2/token"
TOKEN_MARGEN_SEGUNDOS = 60.0
ESPERA_429_DEFAULT = 1.0
ESPERA_429_MAX = 60.0

MERCADOS = {"amazon_mx": "A1AM78C64UM0Y8", "amazon_us": "ATVPDKIKX0DER"}

# URL oficial pineada por fuente (dominio developer-docs.amazon, plan 0.1-0.4;
# sellers sigue el mismo patron de dominio, ya verificado en 0.5).
DOCS = {
    "orders": "https://developer-docs.amazon/sp-api/docs/orders-api-v0-reference",
    "pricing": "https://developer-docs.amazon/sp-api/docs/product-pricing-api-v0-reference",
    "listings": (
        "https://developer-docs.amazon/sp-api/docs/listings-items-api-v2021-08-01-reference"
    ),
    "inventario": "https://developer-docs.amazon/sp-api/docs/fbainventory-api-v1-reference",
    "sellers": "https://developer-docs.amazon/sp-api/docs/sellers-api-v1-reference",
}
VERSION_POR_FUENTE = {
    "orders": "v0",
    "pricing": "v0",
    "listings": "2021-08-01",
    "inventario": "v1",
    "sellers": "v1",
}

# Allowlist de GETs por igualdad literal de path (sin query).
RUTAS_FIJAS = frozenset(
    {
        "/sellers/v1/account",
        "/sellers/v1/marketplaceParticipations",
        "/orders/v0/orders",
        "/products/pricing/v0/price",
        "/products/pricing/v0/competitivePrice",
        "/fba/inventory/v1/summaries",
    }
)
_PREFIJO_LISTINGS = "/listings/2021-08-01/items/"
_PREFIJO_OFERTAS = "/products/pricing/v0/items/"
_SUFIJO_OFERTAS = "/offers"

_ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")
_SELLER_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{1,63}$")

# PII de comprador: jamas se imprime. El prefijo buyer cubre BuyerEmail,
# BuyerName, BuyerPhoneNumber, BuyerInfo y BuyerTaxInfo; la direccion viaja
# en ShippingAddress. Los fragmentos de direccion solo se filtran en orders
# (en otras fuentes podrian ser ubicacion de almacen, no PII).
_PII_GLOBAL = frozenset({"shippingaddress", "shipaddress"})
_PII_ORDERS = frozenset(
    {
        "addressline1",
        "addressline2",
        "addressline3",
        "city",
        "county",
        "district",
        "stateorregion",
        "postalcode",
        "countrycode",
        "phone",
        "phonenumber",
    }
)


class SondaError(Exception):
    """Fallo de la sonda; mensaje ya scrubbado o sin secretos por diseno."""


class SondaNoPermitida(SondaError):
    """El guard default-deny bloqueo metodo/host/path antes de red."""


def _es_pii(clave: str, fuente: str) -> bool:
    baja = clave.lower()
    if baja in _PII_GLOBAL or baja.startswith("buyer"):
        return True
    return fuente == "orders" and baja in _PII_ORDERS


def sanear(obj: Any, fuente: str) -> Any:
    """Devuelve copia sin claves PII (orders filtra tambien direccion)."""
    if isinstance(obj, dict):
        return {
            clave: sanear(valor, fuente)
            for clave, valor in obj.items()
            if not (isinstance(clave, str) and _es_pii(clave, fuente))
        }
    if isinstance(obj, list):
        return [sanear(item, fuente) for item in obj]
    return obj


def validar_get(path: str) -> str:
    """Valida un path GET contra la allowlist; devuelve el path sin query.

    Solo paths relativos que calzan literal con RUTAS_FIJAS o con las dos
    plantillas (listings por sellerId+sku, ofertas por ASIN). Rechaza
    traversal (..), encodings de barra (%2f), query/fragment embebidos (los
    params viajan por el argumento params) y parametros malformados.
    """
    if not path or not path.startswith("/") or "://" in path:
        raise SondaNoPermitida(f"path invalido (relativo con '/' inicial): {path!r}")
    if "?" in path or "#" in path:
        raise SondaNoPermitida("path invalido: query o fragment embebidos no permitidos")
    if "%2f" in path.lower():
        raise SondaNoPermitida("path invalido: encoding de barra no permitido")
    solo = path.split("?", 1)[0]
    if ".." in solo.split("/"):
        raise SondaNoPermitida("path invalido: traversal ('..') no permitido")
    if solo in RUTAS_FIJAS:
        return solo
    if solo.startswith(_PREFIJO_LISTINGS):
        resto = solo[len(_PREFIJO_LISTINGS) :]
        partes = resto.split("/")
        if len(partes) != 2 or not all(partes):
            raise SondaNoPermitida("ruta listings sin sellerId+sku")
        vendedor, sku = partes
        if not _SELLER_RE.match(vendedor):
            raise SondaNoPermitida("sellerId invalido en ruta listings")
        _validar_segmento_sku(sku)
        return solo
    if solo.startswith(_PREFIJO_OFERTAS) and solo.endswith(_SUFIJO_OFERTAS):
        asin = solo[len(_PREFIJO_OFERTAS) : -len(_SUFIJO_OFERTAS)]
        if not _ASIN_RE.match(asin):
            raise SondaNoPermitida("ASIN invalido en ruta de ofertas")
        return solo
    raise SondaNoPermitida(f"GET fuera de allowlist: {solo}")


def _validar_segmento_sku(segmento: str) -> str:
    """Valida el segmento percent-encoded del seller SKU (espejo fees)."""
    if not segmento or segmento in {".", ".."} or "/" in segmento:
        raise SondaNoPermitida("seller SKU invalido en ruta")
    decodificado = unquote(segmento)
    if not decodificado or decodificado in {".", ".."}:
        raise SondaNoPermitida("seller SKU invalido en ruta")
    if segmento != quote(decodificado, safe=""):
        raise SondaNoPermitida("seller SKU sin percent-encoding completo")
    return decodificado


def construir_ruta_listings(seller_id: str, sku: str) -> str:
    """Ruta del item por sellerId + sku con validacion estricta."""
    if not _SELLER_RE.match(seller_id or ""):
        raise SondaNoPermitida("sellerId invalido para ruta listings")
    limpio = (sku or "").strip()
    if not limpio or len(limpio) > 128 or "/" in limpio:
        raise SondaNoPermitida("seller SKU invalido para ruta listings")
    _validar_segmento_sku(quote(limpio, safe=""))
    return f"{_PREFIJO_LISTINGS}{seller_id}/{quote(limpio, safe='')}"


def construir_ruta_ofertas(asin: str) -> str:
    """Ruta de ofertas por ASIN (la que trae Buy Box)."""
    if not _ASIN_RE.match(asin or ""):
        raise SondaNoPermitida(f"ASIN invalido: {asin!r}")
    return f"{_PREFIJO_OFERTAS}{asin}{_SUFIJO_OFERTAS}"


def _contenedor(carga: Any) -> Any:
    if isinstance(carga, dict) and isinstance(carga.get("payload"), (dict, list)):
        return carga["payload"]
    return carga


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


def conteo_items(carga: Any, fuente: str) -> int:
    """Conteo de elementos segun la fuente (generico, no valida negocio)."""
    cont = _contenedor(carga)
    if fuente == "orders" and isinstance(cont, dict):
        ordenes = cont.get("Orders")
        return len(ordenes) if isinstance(ordenes, list) else 0
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


def siguiente_token(carga: Any) -> str | None:
    """NextToken/nextToken de la pagina (ambas grafias oficiales)."""
    cont = _contenedor(carga)
    if not isinstance(cont, dict):
        return None
    for clave in ("NextToken", "nextToken"):
        valor = cont.get(clave)
        if isinstance(valor, str) and valor.strip():
            return valor
    return None


def rate_limit_de(headers: Any) -> dict[str, str]:
    """Headers x-amzn-RateLimit-* tal cual los manda Amazon."""
    return {
        str(nombre).lower(): str(valor)
        for nombre, valor in dict(headers).items()
        if str(nombre).lower().startswith("x-amzn-ratelimit")
    }


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


def _espera_retry(resp: httpx.Response) -> float:
    try:
        segundos = float(resp.headers.get("Retry-After", str(ESPERA_429_DEFAULT)))
    except ValueError:
        segundos = ESPERA_429_DEFAULT
    return min(max(0.0, segundos), ESPERA_429_MAX)


def _log(evento: str, **campos: Any) -> None:
    print(scrub(json.dumps({"evento": evento, **campos}, default=str)), flush=True)


class SondaClient:
    """HTTP GET read-only contra SP-API con un solo refrescador LWA."""

    def __init__(
        self,
        *,
        secrets_dir: str | Path | None = None,
        credentials: dict[str, str] | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep=time.sleep,
        clock=time.monotonic,
        timeout: float = 30.0,
    ) -> None:
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        self._sleep = sleep
        self._clock = clock
        self._transport = transport
        self._timeout = timeout
        self._cred = credentials or self._cargar_credenciales(secrets_dir)
        self._token: str | None = None
        self._vence = 0.0
        self.refreshes = 0

    def _cargar_credenciales(self, secrets_dir: str | Path | None) -> dict[str, str]:
        base = Path(
            secrets_dir or os.environ.get("ORBIT_SECRETS_DIR", "/mnt/data/appdata/orbit/secrets")
        )
        try:
            config = json.loads((base / "amazon_credentials.json").read_text())
        except (OSError, ValueError) as exc:
            raise SondaError("sin amazon_credentials.json legible en ORBIT_SECRETS_DIR") from exc
        try:
            campos = {k: config[k] for k in ("lwa_app_id", "lwa_client_secret", "refresh_token")}
        except (KeyError, TypeError) as exc:
            raise SondaError("credenciales LWA incompletas") from exc
        if not all(isinstance(v, str) and v.strip() for v in campos.values()):
            raise SondaError("credenciales LWA incompletas")
        for valor in campos.values():
            register_secret(valor)
        return campos

    def _acceso(self, *, forzar: bool = False) -> str:
        if (
            not forzar
            and self._token is not None
            and self._clock() < self._vence - TOKEN_MARGEN_SEGUNDOS
        ):
            return self._token
        with httpx.Client(transport=self._transport, timeout=self._timeout) as client:
            resp = client.post(
                LWA_TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "client_id": self._cred["lwa_app_id"],
                    "client_secret": self._cred["lwa_client_secret"],
                    "refresh_token": self._cred["refresh_token"],
                },
            )
        if resp.status_code >= 400:
            raise SondaError(f"refresh LWA rechazado: status={resp.status_code}")
        try:
            datos = resp.json()
            token = datos["access_token"]
            expira = float(datos["expires_in"])
        except (ValueError, KeyError, TypeError) as exc:
            raise SondaError("respuesta LWA sin access_token/expires_in") from exc
        if not isinstance(token, str) or not token:
            raise SondaError("respuesta LWA sin access_token/expires_in")
        register_secret(token)
        self._token = token
        self._vence = self._clock() + max(0.0, expira)
        self.refreshes += 1
        return token

    def get(self, path: str, params: dict | None = None) -> httpx.Response:
        """GET validado: 401 -> un refresh forzado; 429 -> un reintento; nunca loop."""
        solo = validar_get(path)
        url = f"{SP_API_BASE}{solo}"
        token = self._acceso()
        forzados = 0
        reintentos = 0
        with httpx.Client(transport=self._transport, timeout=self._timeout) as client:
            while True:
                resp = client.get(url, params=params, headers={"x-amz-access-token": token})
                if resp.status_code == 401 and forzados < 1:
                    forzados += 1
                    token = self._acceso(forzar=True)
                    continue
                if resp.status_code == 429 and reintentos < 1:
                    reintentos += 1
                    self._sleep(_espera_retry(resp))
                    continue
                return resp


def _get_y_linea(
    client: SondaClient,
    *,
    fuente: str,
    mercado: str,
    endpoint: str,
    params: dict | None,
    pagina: int,
) -> tuple[int | None, int, str | None, str | None, list[str]]:
    """Un GET + su linea JSON. Devuelve (status, conteo, token, aviso, errores)."""
    version = VERSION_POR_FUENTE[fuente]
    try:
        resp = client.get(endpoint, params=params)
    except SondaNoPermitida as exc:
        _log(
            "detalle",
            fuente=fuente,
            mercado=mercado,
            endpoint=endpoint,
            version=version,
            status=None,
            claves_top=[],
            conteo=0,
            paginas=pagina,
            rate_limit={},
            aviso_deprecacion=None,
            errores=[str(exc)],
        )
        return None, 0, None, None, [str(exc)]
    except (SondaError, httpx.HTTPError) as exc:
        _log(
            "detalle",
            fuente=fuente,
            mercado=mercado,
            endpoint=endpoint,
            version=version,
            status=None,
            claves_top=[],
            conteo=0,
            paginas=pagina,
            rate_limit={},
            aviso_deprecacion=None,
            errores=[type(exc).__name__],
        )
        return None, 0, None, None, [type(exc).__name__]
    try:
        carga = resp.json()
    except ValueError:
        carga = {}
    if resp.status_code == 403 and "restricteddatatoken" in resp.text.lower():
        _log(
            "detalle",
            fuente=fuente,
            mercado=mercado,
            endpoint=endpoint,
            version=version,
            status=resp.status_code,
            claves_top=[],
            conteo=0,
            paginas=pagina,
            rate_limit=rate_limit_de(resp.headers),
            aviso_deprecacion=aviso_deprecacion(resp, {}),
            errores=["rdt_requerido"],
        )
        return resp.status_code, 0, None, None, ["rdt_requerido"]
    carga = sanear(carga, fuente)
    errores = [] if resp.status_code == 200 else [f"http_{resp.status_code}"]
    _log(
        "detalle",
        fuente=fuente,
        mercado=mercado,
        endpoint=endpoint,
        version=version,
        status=resp.status_code,
        claves_top=claves_top(carga),
        conteo=conteo_items(carga, fuente),
        paginas=pagina,
        rate_limit=rate_limit_de(resp.headers),
        aviso_deprecacion=aviso_deprecacion(resp, carga),
        errores=errores,
    )
    return (
        resp.status_code,
        conteo_items(carga, fuente),
        siguiente_token(carga),
        aviso_deprecacion(resp, carga),
        errores,
    )


def _resumen_base(fuente: str, mercado: str) -> dict[str, Any]:
    return {
        "fuente": fuente,
        "mercado": mercado,
        "version": VERSION_POR_FUENTE[fuente],
        "doc": DOCS[fuente],
        "paginas": 0,
        "conteo_total": 0,
        "aviso_paginacion": None,
        "aviso_deprecacion": None,
        "errores": [],
    }


def sondear_sellers(client: SondaClient, mercado: str) -> dict[str, Any]:
    """0.5: identidad (account + participations). Reutilizada por listings."""
    resumen = _resumen_base("sellers", mercado)
    resumen["endpoints"] = [
        "/sellers/v1/account",
        "/sellers/v1/marketplaceParticipations",
    ]
    ok = True
    for endpoint in resumen["endpoints"]:
        status, conteo, _, aviso, errores = _get_y_linea(
            client, fuente="sellers", mercado=mercado, endpoint=endpoint, params=None, pagina=1
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


def _extraer_seller_id(client: SondaClient, mercado: str) -> tuple[str | None, list[str]]:
    """Lee el sellerId desde account sin imprimir secretos."""
    try:
        resp = client.get("/sellers/v1/account")
    except (SondaError, httpx.HTTPError) as exc:
        _log(
            "detalle",
            fuente="listings",
            mercado=mercado,
            endpoint="/sellers/v1/account",
            version=VERSION_POR_FUENTE["listings"],
            status=None,
            claves_top=[],
            conteo=0,
            paginas=1,
            rate_limit={},
            aviso_deprecacion=None,
            errores=[type(exc).__name__],
        )
        return None, [type(exc).__name__]
    try:
        carga = sanear(resp.json(), "listings")
    except ValueError:
        carga = {}
    cont = _contenedor(carga)
    vendedor = cont.get("sellerId") if isinstance(cont, dict) else None
    _log(
        "detalle",
        fuente="listings",
        mercado=mercado,
        endpoint="/sellers/v1/account",
        version=VERSION_POR_FUENTE["listings"],
        status=resp.status_code,
        claves_top=claves_top(carga),
        conteo=0,
        paginas=1,
        rate_limit=rate_limit_de(resp.headers),
        aviso_deprecacion=aviso_deprecacion(resp, carga),
        errores=[] if resp.status_code == 200 and vendedor else ["sin_seller_id"],
    )
    if resp.status_code != 200 or not isinstance(vendedor, str) or not vendedor.strip():
        return None, ["sin_seller_id"]
    return vendedor.strip(), []


def sondear_listings(client: SondaClient, mercado: str, *, sku: str) -> dict[str, Any]:
    """0.3: estado del listing por sellerId (de account) + sku."""
    resumen = _resumen_base("listings", mercado)
    if not (sku or "").strip():
        resumen["errores"] = ["sin_sku"]
        resumen["veredicto"] = "no_verificada"
        return resumen
    vendedor, errores = _extraer_seller_id(client, mercado)
    resumen["paginas"] = 1
    if vendedor is None:
        # Sin sellerId no se llama al item: falla antes de red util.
        resumen["errores"].extend(errores)
        resumen["veredicto"] = "no_verificada"
        return resumen
    endpoint = construir_ruta_listings(vendedor, (sku or "").strip())
    resumen["endpoints"] = ["/sellers/v1/account", endpoint]
    status, conteo, _, aviso, errores_item = _get_y_linea(
        client,
        fuente="listings",
        mercado=mercado,
        endpoint=endpoint,
        params={"marketplaceIds": MERCADOS[mercado]},
        pagina=2,
    )
    resumen["paginas"] = 2
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
) -> dict[str, Any]:
    """Bucle NextToken con los dos bugs conocidos como condicion de paro."""
    resumen = _resumen_base(fuente, mercado)
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


def sondear_orders(
    client: SondaClient,
    mercado: str,
    *,
    dias: int = 7,
    max_paginas: int = 3,
    ahora: datetime.datetime | None = None,
) -> dict[str, Any]:
    """0.1: una pagina + recorrido con CreatedAfter reciente y MaxResults bajo."""
    if dias < 1 or max_paginas < 1:
        raise SondaError("--dias y --max-paginas deben ser >= 1")
    base = ahora or datetime.datetime.now(datetime.UTC)
    creada = (base - datetime.timedelta(days=dias)).isoformat()
    return _paginar(
        client,
        fuente="orders",
        mercado=mercado,
        endpoint="/orders/v0/orders",
        params_base={
            "MarketplaceIds": MERCADOS[mercado],
            "CreatedAfter": creada,
            "MaxResultsPerPage": "10",
        },
        param_token="NextToken",
        max_paginas=max_paginas,
    )


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
    "orders": "sonda Orders v0 (una pagina + recorrido NextToken)",
    "pricing": "sonda Pricing v0 (ofertas por ASIN + competitivos)",
    "listings": "sonda Listings 2021-08-01 (item por sellerId + sku)",
    "inventario": "sonda Inventario FBA v1 (resumenes por marketplace)",
    "sellers": "sonda Sellers v1 (account + participations)",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sonda_spapi", description="Sondas read-only SP-API Fase 0 (solo GET)."
    )
    parser.add_argument("--fuente", required=True, choices=sorted(SONDEOS))
    parser.add_argument("--mercado", required=True, choices=sorted(MERCADOS))
    parser.add_argument("--asin", default=None, help="ASIN de 10 caracteres (pricing)")
    parser.add_argument("--sku", default=None, help="seller SKU (listings)")
    parser.add_argument("--max-paginas", type=int, default=3)
    parser.add_argument("--dias", type=int, default=7, help="ventana CreatedAfter (orders)")
    args = parser.parse_args(argv)

    try:
        client = SondaClient()
    except SondaError as exc:
        _log("error_fatal", errores=[str(exc)])
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
            resumen = sondear_listings(client, args.mercado, sku=args.sku)
        elif args.fuente == "inventario":
            resumen = sondear_inventario(client, args.mercado, max_paginas=args.max_paginas)
        else:
            resumen = sondear_sellers(client, args.mercado)
    except SondaError as exc:
        _log("error_fatal", fuente=args.fuente, mercado=args.mercado, errores=[str(exc)])
        return 1
    _log("resumen", **resumen, refreshes_lwa=client.refreshes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
