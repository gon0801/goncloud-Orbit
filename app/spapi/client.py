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
Fees 1/s burst 2. Con `limitador`, cada intento (incluido el reintento por
429) consume cuota y la tasa sigue a `x-amzn-RateLimit-Limit` cuando viene;
sin el, solo reacciona al 429 sin espera proactiva (comportamiento A.1,
que fees conserva).

Redaccion: errores y logs llevan metodo + path + status, jamas headers ni
cuerpo (el cuerpo de LWA puede ecoar el client_id); `scrub()` como ultima
linea. Sin PII: Orders se pide sin `includedData=BUYER` y `sanear()` filtra
comprador/direccion antes de cualquier salida.
"""

from __future__ import annotations

import json
import logging
import math
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

# Seller propio por marketplace (IDs publicos, no secretos): un solo
# merchant en MX/US/CA verificado el 2026-09-09 con Ads GET /v2/profiles
# (accountInfo.id, type seller; acta 0.5 cito el de MX). Vivia en
# pricing.py; A.4 lo sube aqui para listings (mismo sellerId en la ruta).
VENDEDORES_PROPIOS = {
    "A1AM78C64UM0Y8": "A29XRL07YRN0L",
    "ATVPDKIKX0DER": "A29XRL07YRN0L",
}

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


# Taxonomia del motivo de fallo (SP-API 01 A.5): las 4 ingestas sellan la
# rama except con uno de estos prefijos + ": " + detalle scrubbeado. Solo
# salud.py los lee (ultima_429/ultimo_lwa) y evaluar_alertas (flancos).
# Clases: fallo autenticando LWA, 429 que sobrevivio al reintento del
# cliente, 5xx, red, y contrato (cajon por defecto: incluye umbral, 401
# persistente y cualquier error no clasificado — el detalle tras el
# prefijo conserva la causa real).
MOTIVO_LWA_FALLIDO = "lwa_fallido"
MOTIVO_HTTP_429 = "http_429"
MOTIVO_HTTP_5XX = "http_5xx"
MOTIVO_RED = "red"
MOTIVO_CONTRATO = "contrato"


def prefijo_motivo(exc: BaseException) -> str:
    """Prefijo taxonomico para el motivo del sello ok=false.

    Clasifica por TIPO cuando hay tipo (LWA y red), y por el formato de
    mensaje que genera nuestro propio codigo (`status=NNN` en los fatales
    HTTP: formato pineado en los tests de cada ingesta, no texto libre
    externo). Todo lo demas es "contrato" por defecto.
    """
    if isinstance(exc, SpapiAuthError):
        return MOTIVO_LWA_FALLIDO
    if isinstance(exc, httpx.HTTPError):
        return MOTIVO_RED
    texto = str(exc)
    if "status=429" in texto:
        return MOTIVO_HTTP_429
    if re.search(r"status=5\d\d", texto):
        return MOTIVO_HTTP_5XX
    return MOTIVO_CONTRATO


def tasa_anunciada(headers: Any) -> float | None:
    """Tasa (req/s) de `x-amzn-RateLimit-Limit`, o None si ausente/ilegible.

    El brief exige el limitador local A PARTIR de este header: el servidor
    manda (cuentas con mas throughput ven un numero mayor). Solo se acepta
    un numero finito > 0; cualquier otra cosa se ignora sin fallar.
    """
    try:
        valor = rate_limit_de(headers).get("x-amzn-ratelimit-limit")
        tasa = float(valor) if valor is not None else None
    except (TypeError, ValueError):
        return None
    if tasa is None or not math.isfinite(tasa) or tasa <= 0:
        return None
    return tasa


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

    def _honrar_tasa(self, limitador: CuboTasa | None, resp: httpx.Response) -> None:
        # Honra x-amzn-RateLimit-Limit cuando viene (el servidor manda).
        if limitador is None:
            return
        tasa = tasa_anunciada(resp.headers)
        if tasa is not None:
            limitador.fijar_tasa(tasa)

    def get(
        self,
        path: str,
        params: dict | None = None,
        limitador: CuboTasa | None = None,
    ) -> httpx.Response:
        """GET validado: 401 -> un refresh forzado; 429 -> un reintento; nunca loop.

        Devuelve la respuesta tal cual (incluso 4xx/5xx): el llamador decide.
        Falla antes de red si el path no esta en la allowlist. Con
        `limitador`, cada intento consume cuota (el reintento por 429 tambien)
        y la tasa se ajusta a `x-amzn-RateLimit-Limit` (contrato del brief).
        Sin `limitador` el comportamiento es el sellado en A.1.
        """
        solo = validar_get(path)
        url = f"{SP_API_BASE}{solo}"
        token = self._acceso()
        forzados = 0
        reintentos = 0
        with httpx.Client(transport=self._transport, timeout=self._timeout) as client:
            while True:
                if limitador is not None:
                    limitador.consumir()
                resp = client.get(url, params=params, headers={"x-amz-access-token": token})
                self._honrar_tasa(limitador, resp)
                if resp.status_code == 401 and forzados < 1:
                    forzados += 1
                    token = self._acceso(forzar=True, rechazado=token)
                    continue
                if resp.status_code == 429 and reintentos < 1:
                    reintentos += 1
                    self._sleep(_espera_retry(resp))
                    continue
                return resp

    def post_fees(
        self,
        seller_sku: str,
        content: bytes,
        limitador: CuboTasa | None = None,
    ) -> httpx.Response:
        """Unico POST permitido: feesEstimate (lectura implementada como POST).

        Valida seller_sku + ruta antes de red. 401 -> un refresh forzado;
        429 -> un reintento acotado; 5xx/red -> sin retry (el llamador, fees,
        reintenta en su capa). Devuelve la respuesta tal cual. Con
        `limitador`, igual que `get`; sin el, comportamiento A.1 intacto.
        """
        ruta = validar_post_fees(construir_ruta_fees(seller_sku), seller_sku)
        url = f"{SP_API_BASE}{ruta}"
        token = self._acceso()
        forzados = 0
        reintentos = 0
        with httpx.Client(transport=self._transport, timeout=self._timeout) as client:
            while True:
                if limitador is not None:
                    limitador.consumir()
                resp = client.post(
                    url,
                    content=content,
                    headers={
                        "x-amz-access-token": token,
                        "Content-Type": "application/json",
                    },
                )
                self._honrar_tasa(limitador, resp)
                if resp.status_code == 401 and forzados < 1:
                    forzados += 1
                    token = self._acceso(forzar=True, rechazado=token)
                    continue
                if resp.status_code == 429 and reintentos < 1:
                    reintentos += 1
                    self._sleep(_espera_retry(resp))
                    continue
                return resp


class CuboTasa:
    """Token bucket local por proceso (sin Redis ni colas, decision de stack).

    Reloj y espera inyectables (los del cliente en produccion, falsos en
    tests). Orders lo usa con (20, 0.0056/s); Pricing con (1, 0.5/s).
    """

    def __init__(
        self,
        *,
        sleep,
        clock,
        capacidad: int,
        tasa: float,
    ) -> None:
        self._sleep = sleep
        self._clock = clock
        self._capacidad = capacidad
        self._tasa = tasa
        self._tokens = float(capacidad)
        self._ultimo = clock()

    def fijar_tasa(self, tasa: float) -> None:
        # El servidor manda via x-amzn-RateLimit-Limit (contrato del
        # brief): ajusta el ritmo sin tocar la capacidad. Solo numeros
        # finitos > 0; lo demas se ignora sin fallar.
        if isinstance(tasa, bool):
            return
        try:
            nueva = float(tasa)
        except (TypeError, ValueError):
            return
        if math.isfinite(nueva) and nueva > 0:
            self._tasa = nueva

    def consumir(self) -> None:
        ahora = self._clock()
        self._tokens = min(
            float(self._capacidad),
            self._tokens + max(0.0, ahora - self._ultimo) * self._tasa,
        )
        self._ultimo = ahora
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return
        espera = max(0.0, (1.0 - self._tokens) / self._tasa)
        self._sleep(espera)
        self._tokens = min(float(self._capacidad), self._tokens + espera * self._tasa)
        self._tokens -= 1.0
        self._ultimo = self._clock()


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
