"""Cliente unico SP-API solo lectura (SP-API 01 A.1, D5).

Un solo refrescador LWA por proceso: el token se cachea con 60 s de margen
y se comparte pasando la MISMA instancia entre modulos (fees + fotos); el
contador `refreshes` lo demuestra en tests. En produccion se crea una
instancia por proceso y se reutiliza en las cuatro ingestas diarias.

Guard default-deny por igualdad literal (espejo de `app/ads/client.py`):
solo los GET de las cinco fuentes (Orders v0 + 2026-01-01, Pricing ofertas
y competitivos, Listings 2021-08-01, Inventario FBA v1, Sellers v1) mas
Catalog Items 2022-04-01, y un unico POST de lectura implementada como
POST (`/products/fees/v0/listings/{sku}/feesEstimate`, mismo criterio que
`recommend_bids` en Ads). Todo lo demas falla ANTES de red, sin HTTP.

Politica de reintentos (sellada A.1): 401 -> un refresh forzado y una
re-emision; 429 -> una espera acotada (`Retry-After`, tope 60 s) y un
reintento; 5xx/red -> sin retry (el llamador decide: fees reintenta en su
capa sin cambiar comportamiento). Nunca loop.

Limites oficiales (actas 0.1-0.5, se respetan con limitador local en la
capa de ingesta, sin Redis/colas por decision de stack):
Orders 0.0056 req/s burst 20; Pricing 0.5/s; Listings 5/s; Inventario 2/s;
Fees 1/s burst 2. Este cliente solo reacciona al 429; no impone espera
proactiva (fees conserva su token-bucket propio).

Redaccion: errores y logs llevan metodo + path + status, jamas headers ni
cuerpo (el cuerpo de LWA puede ecoar el client_id); `scrub()` como ultima
linea. Sin PII: Orders se pide sin `includedData=BUYER` y `sanear()` filtra
comprador/direccion antes de cualquier salida.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

import httpx

from app.redaction import install_scrub_filter, register_secret, scrub

logger = logging.getLogger(__name__)
install_scrub_filter(logger)

SP_API_HOST = "sellingpartnerapi-na.amazon.com"
SP_API_BASE = f"https://{SP_API_HOST}"
LWA_TOKEN_URL = "https://api.amazon.com/auth/o2/token"
TOKEN_MARGEN_SEGUNDOS = 60.0
ESPERA_429_DEFAULT = 1.0
ESPERA_429_MAX = 60.0

MERCADOS = {"amazon_mx": "A1AM78C64UM0Y8", "amazon_us": "ATVPDKIKX0DER"}

RUTA_ORDERS_V0 = "/orders/v0/orders"
RUTA_ORDERS_NUEVA = "/orders/2026-01-01/orders"
RUTA_PRECIO_COMPETITIVO = "/products/pricing/v0/competitivePrice"
RUTA_PRECIO = "/products/pricing/v0/price"
RUTA_INVENTARIO = "/fba/inventory/v1/summaries"
RUTA_SELLERS_CUENTA = "/sellers/v1/account"
RUTA_SELLERS_PARTICIPACIONES = "/sellers/v1/marketplaceParticipations"

RUTAS_FIJAS = frozenset(
    {
        RUTA_SELLERS_CUENTA,
        RUTA_SELLERS_PARTICIPACIONES,
        RUTA_ORDERS_V0,
        RUTA_ORDERS_NUEVA,
        RUTA_PRECIO,
        RUTA_PRECIO_COMPETITIVO,
        RUTA_INVENTARIO,
    }
)

_PREFIJO_LISTINGS = "/listings/2021-08-01/items/"
_PREFIJO_OFERTAS = "/products/pricing/v0/items/"
_SUFIJO_OFERTAS = "/offers"
_PREFIJO_CATALOGO = "/catalog/2022-04-01/items/"
FEES_PATH_PREFIX = "/products/fees/v0/listings/"
FEES_PATH_SUFFIX = "/feesEstimate"

_ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")
_SELLER_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{1,63}$")


class SpapiError(Exception):
    """Fallo del cliente SP-API; mensaje ya scrubbado o sin secretos."""

    def __init__(self, message: str) -> None:
        super().__init__(scrub(message))


class SpapiNoPermitida(SpapiError):
    """El guard default-deny bloqueo metodo/host/path antes de red."""


class SpapiApiError(SpapiError):
    """SP-API fallo tras agotar la politica acotada (mensaje minimo)."""


class SpapiAuthError(SpapiError):
    """Fallo autenticando/refrescando el token LWA."""


class SpapiRechazoLWA(SpapiAuthError):
    """LWA rechazo el refresh (4xx): distinto de token ausente o red.

    Existe para que los llamadores clasifiquen sin leer el texto del
    mensaje (hallazgo cross-review: el `in` sobre el mensaje acopla).
    """

    def __init__(self, status: int) -> None:
        self.status = status
        super().__init__(f"refresh LWA rechazado: status={status}")


# Alias de compatibilidad: la sonda nacio con estos nombres y sus tests los
# usan; el canonico es Spapi*. Una sola copia vive aqui.
SondaError = SpapiError
SondaNoPermitida = SpapiNoPermitida

_PII_GLOBAL = frozenset({"shippingaddress", "shipaddress", "recipient"})
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


def _es_pii(clave: str, fuente: str) -> bool:
    baja = clave.lower()
    if baja in _PII_GLOBAL or baja.startswith("buyer"):
        return True
    return fuente == "orders" and baja in _PII_ORDERS


def sanear(obj: Any, fuente: str) -> Any:
    """Copia sin claves PII (orders filtra tambien direccion)."""
    if isinstance(obj, dict):
        return {
            clave: sanear(valor, fuente)
            for clave, valor in obj.items()
            if not (isinstance(clave, str) and _es_pii(clave, fuente))
        }
    if isinstance(obj, list):
        return [sanear(item, fuente) for item in obj]
    return obj


def _validar_segmento_sku(segmento: str) -> str:
    """Valida el segmento percent-encoded del seller SKU (espejo fees)."""
    if not segmento or segmento in {".", ".."} or "/" in segmento:
        raise SpapiNoPermitida("seller SKU invalido en ruta")
    decodificado = unquote(segmento)
    if not decodificado or decodificado in {".", ".."}:
        raise SpapiNoPermitida("seller SKU invalido en ruta")
    if segmento != quote(decodificado, safe=""):
        raise SpapiNoPermitida("seller SKU sin percent-encoding completo")
    return decodificado


def validar_get(path: str) -> str:
    """Valida un path GET contra la allowlist; devuelve el path sin query.

    Solo paths relativos que calzan literal con RUTAS_FIJAS o con las tres
    plantillas (listings por sellerId+sku, ofertas por ASIN, catalogo por
    ASIN). Rechaza traversal (..), encodings de barra (%2f), query/fragment
    embebidos y parametros malformados. Falla antes de red.
    """
    if not path or not path.startswith("/") or "://" in path:
        raise SpapiNoPermitida(f"path invalido (relativo con '/' inicial): {path!r}")
    if "?" in path or "#" in path:
        raise SpapiNoPermitida("path invalido: query o fragment embebidos no permitidos")
    if "%2f" in path.lower():
        raise SpapiNoPermitida("path invalido: encoding de barra no permitido")
    if ".." in path.split("/"):
        raise SpapiNoPermitida("path invalido: traversal ('..') no permitido")
    solo = path
    if solo in RUTAS_FIJAS:
        return solo
    if solo.startswith(_PREFIJO_LISTINGS):
        resto = solo[len(_PREFIJO_LISTINGS) :]
        partes = resto.split("/")
        if len(partes) != 2 or not all(partes):
            raise SpapiNoPermitida("ruta listings sin sellerId+sku")
        vendedor, sku = partes
        if not _SELLER_RE.match(vendedor):
            raise SpapiNoPermitida("sellerId invalido en ruta listings")
        _validar_segmento_sku(sku)
        return solo
    if solo.startswith(_PREFIJO_OFERTAS) and solo.endswith(_SUFIJO_OFERTAS):
        asin = solo[len(_PREFIJO_OFERTAS) : -len(_SUFIJO_OFERTAS)]
        if not _ASIN_RE.match(asin):
            raise SpapiNoPermitida("ASIN invalido en ruta de ofertas")
        return solo
    if solo.startswith(_PREFIJO_CATALOGO):
        asin = solo[len(_PREFIJO_CATALOGO) :]
        if "/" in asin or not _ASIN_RE.match(asin):
            raise SpapiNoPermitida("ASIN invalido en ruta de catalogo")
        return solo
    raise SpapiNoPermitida(f"GET fuera de allowlist: {solo}")


def construir_ruta_listings(seller_id: str, sku: str) -> str:
    """Ruta del item por sellerId + sku con validacion estricta."""
    if not _SELLER_RE.match(seller_id or ""):
        raise SpapiNoPermitida("sellerId invalido para ruta listings")
    limpio = (sku or "").strip()
    if not limpio or len(limpio) > 128 or "/" in limpio:
        raise SpapiNoPermitida("seller SKU invalido para ruta listings")
    _validar_segmento_sku(quote(limpio, safe=""))
    return f"{_PREFIJO_LISTINGS}{seller_id}/{quote(limpio, safe='')}"


def construir_ruta_ofertas(asin: str) -> str:
    """Ruta de ofertas por ASIN (la que trae Buy Box)."""
    if not _ASIN_RE.match(asin or ""):
        raise SpapiNoPermitida(f"ASIN invalido: {asin!r}")
    return f"{_PREFIJO_OFERTAS}{asin}{_SUFIJO_OFERTAS}"


def construir_ruta_catalogo(asin: str) -> str:
    """Ruta de Catalog Items 2022-04-01 por ASIN (solo `images`)."""
    if not _ASIN_RE.match(asin or ""):
        raise SpapiNoPermitida(f"ASIN invalido: {asin!r}")
    return f"{_PREFIJO_CATALOGO}{asin}"


def construir_ruta_fees(seller_sku: str) -> str:
    """Ruta relativa del POST feesEstimate con encoding completo."""
    if not seller_sku or seller_sku in {".", ".."} or "/" in seller_sku:
        raise SpapiNoPermitida("seller SKU invalido para ruta fees")
    return f"{FEES_PATH_PREFIX}{quote(seller_sku, safe='')}{FEES_PATH_SUFFIX}"


def validar_post_fees(path: str, seller_sku: str) -> str:
    """Valida el unico POST permitido contra la allowlist (falla antes de red)."""
    if not path or not path.startswith("/") or "://" in path:
        raise SpapiNoPermitida("path fees invalido: relativo con '/' inicial")
    if "?" in path or "#" in path:
        raise SpapiNoPermitida("path fees invalido: query o fragment no permitidos")
    if "%2f" in path.lower():
        raise SpapiNoPermitida("path fees invalido: encoding de barra no permitido")
    if ".." in path.split("/"):
        raise SpapiNoPermitida("path fees invalido: traversal ('..') no permitido")
    if not path.startswith(FEES_PATH_PREFIX) or not path.endswith(FEES_PATH_SUFFIX):
        raise SpapiNoPermitida(f"POST fuera de allowlist: {path}")
    segmento = path[len(FEES_PATH_PREFIX) : -len(FEES_PATH_SUFFIX)]
    if not segmento:
        raise SpapiNoPermitida("SellerSKU vacio en ruta fees")
    if unquote(segmento) != seller_sku:
        raise SpapiNoPermitida("SellerSKU en ruta no coincide")
    if segmento != quote(seller_sku, safe=""):
        raise SpapiNoPermitida("SellerSKU sin percent-encoding completo")
    return path


def _token_de_paginacion(obj: Any) -> str | None:
    if isinstance(obj, dict):
        for clave in ("nextToken", "paginationToken"):
            valor = obj.get(clave)
            if isinstance(valor, str) and valor.strip():
                return valor
    return None


def _contenedor(carga: Any) -> Any:
    if isinstance(carga, dict) and isinstance(carga.get("payload"), (dict, list)):
        return carga["payload"]
    return carga


def siguiente_token(carga: Any) -> str | None:
    """Token de paginacion: NextToken (v0), nextToken/paginationToken (2026).

    FBA Inventory trae `pagination` como HERMANA de `payload`, asi que se
    busca en el nivel superior ANTES de desenvolver (E/0.4).
    """
    if isinstance(carga, dict):
        hermano = _token_de_paginacion(carga.get("pagination"))
        if hermano is not None:
            return hermano
    cont = _contenedor(carga)
    if not isinstance(cont, dict):
        return None
    for clave in ("NextToken", "nextToken", "paginationToken"):
        valor = cont.get(clave)
        if isinstance(valor, str) and valor.strip():
            return valor
    return _token_de_paginacion(cont.get("pagination"))


def rate_limit_de(headers: Any) -> dict[str, str]:
    """Headers x-amzn-RateLimit-* tal cual los manda Amazon."""
    return {
        str(nombre).lower(): str(valor)
        for nombre, valor in dict(headers).items()
        if str(nombre).lower().startswith("x-amzn-ratelimit")
    }


def _espera_retry(resp: httpx.Response) -> float:
    try:
        segundos = float(resp.headers.get("Retry-After", str(ESPERA_429_DEFAULT)))
    except ValueError:
        segundos = ESPERA_429_DEFAULT
    return min(max(0.0, segundos), ESPERA_429_MAX)


class SpapiClient:
    """HTTP solo lectura contra SP-API con un solo refrescador LWA."""

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
        self._lock = threading.Lock()
        self.refreshes = 0

    def _cargar_credenciales(self, secrets_dir: str | Path | None) -> dict[str, str]:
        base = Path(
            secrets_dir or os.environ.get("ORBIT_SECRETS_DIR", "/mnt/data/appdata/orbit/secrets")
        )
        try:
            config = json.loads((base / "amazon_credentials.json").read_text())
        except (OSError, ValueError) as exc:
            raise SpapiAuthError(
                "sin amazon_credentials.json legible en ORBIT_SECRETS_DIR"
            ) from exc
        try:
            campos = {k: config[k] for k in ("lwa_app_id", "lwa_client_secret", "refresh_token")}
        except (KeyError, TypeError) as exc:
            raise SpapiAuthError("credenciales LWA incompletas") from exc
        if not all(isinstance(v, str) and v.strip() for v in campos.values()):
            raise SpapiAuthError("credenciales LWA incompletas")
        for valor in campos.values():
            register_secret(valor)
        return campos

    def _acceso(self, *, forzar: bool = False, rechazado: str | None = None) -> str:
        with self._lock:
            if (
                not forzar
                and self._token is not None
                and self._clock() < self._vence - TOKEN_MARGEN_SEGUNDOS
            ):
                return self._token
            if (
                forzar
                and rechazado is not None
                and self._token is not None
                and self._token != rechazado
            ):
                # Otro consumidor ya refresco tras nuestro 401: el instalado
                # vale, sin POST nuevo (F2).
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
                raise SpapiRechazoLWA(resp.status_code)
            try:
                datos = resp.json()
                token = datos["access_token"]
                expira = float(datos["expires_in"])
            except (ValueError, KeyError, TypeError) as exc:
                raise SpapiAuthError("respuesta LWA sin access_token/expires_in") from exc
            if not isinstance(token, str) or not token:
                raise SpapiAuthError("respuesta LWA sin access_token/expires_in")
            register_secret(token)
            self._token = token
            self._vence = self._clock() + max(0.0, expira)
            self.refreshes += 1
            return token

    def invalidar_token(self) -> None:
        """Descarta el token cacheado (un 401/403 lo volvio inutil)."""
        with self._lock:
            self._token = None
            self._vence = 0.0

    def get(self, path: str, params: dict | None = None) -> httpx.Response:
        """GET validado: 401 -> un refresh forzado; 429 -> un reintento; nunca loop.

        Devuelve la respuesta tal cual (incluso 4xx/5xx): el llamador decide.
        Falla antes de red si el path no esta en la allowlist.
        """
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
                    token = self._acceso(forzar=True, rechazado=token)
                    continue
                if resp.status_code == 429 and reintentos < 1:
                    reintentos += 1
                    self._sleep(_espera_retry(resp))
                    continue
                return resp

    def post_fees(self, seller_sku: str, content: bytes) -> httpx.Response:
        """Unico POST permitido: feesEstimate (lectura implementada como POST).

        Valida seller_sku + ruta antes de red. 401 -> un refresh forzado;
        429 -> un reintento acotado; 5xx/red -> sin retry (el llamador, fees,
        reintenta en su capa). Devuelve la respuesta tal cual.
        """
        ruta = validar_post_fees(construir_ruta_fees(seller_sku), seller_sku)
        url = f"{SP_API_BASE}{ruta}"
        token = self._acceso()
        forzados = 0
        reintentos = 0
        with httpx.Client(transport=self._transport, timeout=self._timeout) as client:
            while True:
                resp = client.post(
                    url,
                    content=content,
                    headers={
                        "x-amz-access-token": token,
                        "Content-Type": "application/json",
                    },
                )
                if resp.status_code == 401 and forzados < 1:
                    forzados += 1
                    token = self._acceso(forzar=True, rechazado=token)
                    continue
                if resp.status_code == 429 and reintentos < 1:
                    reintentos += 1
                    self._sleep(_espera_retry(resp))
                    continue
                return resp


_COMPARTIDOS: dict[tuple[tuple[str, str], ...], SpapiClient] = {}
_COMPARTIDOS_LOCK = threading.Lock()


def cliente_compartido(
    *,
    credentials: dict[str, str] | None = None,
    secrets_dir: str | Path | None = None,
    timeout: float = 30.0,
) -> SpapiClient:
    """Instancia compartida por proceso y credenciales (D5 en produccion).

    Los consumidores que usan red y reloj reales (sin transporte mock ni
    reloj inyectado) obtienen la MISMA instancia: un solo POST a LWA por
    proceso aunque fees y fotos convivan en el. Los tests pasan transporte
    o reloj propios y siempre reciben instancia privada (sin fuga de
    estado entre tests).
    """
    if credentials is None:
        credentials = SpapiClient(secrets_dir=secrets_dir, timeout=timeout)._cred
    clave = tuple(sorted(credentials.items()))
    with _COMPARTIDOS_LOCK:
        cliente = _COMPARTIDOS.get(clave)
        if cliente is None:
            cliente = SpapiClient(
                credentials=dict(credentials),
                transport=None,
                timeout=timeout,
            )
            _COMPARTIDOS[clave] = cliente
        return cliente
