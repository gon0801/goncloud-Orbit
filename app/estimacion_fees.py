"""Cotizacion Product Fees MARGEN ESTIMADO 01 A.3 — SP-API separada de Ads.

Endpoint permitido: POST
`sellingpartnerapi-na.amazon.com/products/fees/v0/listings/{SellerSKU}/feesEstimate`.
LWA: POST exacto `api.amazon.com/auth/o2/token`. Todo otro host/metodo/path
falla antes de red (default-deny).

Universo v1: amazon_mx / fba / MXN. Persistencia append-only en
`estimacion_fee_observation` (idempotente por source_event_id derivado de
request + outcome normalizado).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlsplit

import httpx
import psycopg
from psycopg.types.json import Json

from app.estimacion_insumos import OfertaResuelta
from app.redaction import install_scrub_filter, register_secret, scrub
from app.spapi.client import SpapiAuthError, SpapiClient

logger = logging.getLogger(__name__)
install_scrub_filter(logger)

SP_API_HOST = "sellingpartnerapi-na.amazon.com"
SP_API_BASE = f"https://{SP_API_HOST}"
LWA_TOKEN_URL = "https://api.amazon.com/auth/o2/token"
FEES_PATH_PREFIX = "/products/fees/v0/listings/"
FEES_PATH_SUFFIX = "/feesEstimate"

MARKETPLACE_MX = "A1AM78C64UM0Y8"

TOKEN_EXPIRY_MARGIN_SECONDS = 60.0
MAX_RETRIES = 4
RETRYABLE_STATUSES = {429}
RETRY_AFTER_MAX_SECONDS = 60.0
REQUEST_TIMEOUT_SECONDS = 30.0

# Cadencia oficial Product Fees: 1 req/s, burst 2 (acta 0.2 / plan A.3).
RATE_INTERVAL_SECONDS = 1.0
RATE_BURST = 2
RATE_MAX_WAITS = 120

_MAX_DECIMALES = Decimal("0.0001")
_MAX_DINERO = Decimal(10) ** 10

_UNIVERSO_SOPORTADO = frozenset({("amazon_mx", "fba", "MXN")})

_ABSOLUTE_URL_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://")
_FEES_PATH_RE = re.compile(r"^/products/fees/v0/listings/[^/]+/feesEstimate$")
_LEGACY_FEE_TS_RE = re.compile(
    r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun) "
    r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) +"
    r"\d{1,2} \d{2}:\d{2}:\d{2} UTC \d{4}$"
)


class FeesClientError(Exception):
    """Error de cotizacion; mensaje scrubbado."""

    def __init__(self, message: str) -> None:
        super().__init__(scrub(message))


class MutationNotAllowedError(FeesClientError):
    """Guard default-deny: metodo/host/path no permitido."""


@dataclass(frozen=True)
class ResultadoCotizacion:
    estado: str  # success | error
    total_fees: Decimal | None
    fee_details: list[dict[str, Any]]
    fees_estimated_at: datetime | None
    error_code: str | None
    fetched_at: datetime


@dataclass(frozen=True)
class ResultadoPersistenciaFee:
    id: int
    observed_at: datetime
    reutilizada: bool


def construir_ruta_fees(seller_sku: str) -> str:
    """Ruta relativa con URL-encoding completo del seller SKU."""
    encoded = quote(seller_sku, safe="")
    return f"{FEES_PATH_PREFIX}{encoded}{FEES_PATH_SUFFIX}"


def construir_fee_canonical_input(oferta: OfertaResuelta) -> dict[str, Any]:
    return {
        "oferta_source_event_id": oferta.source_event_id,
        "marketplace_id": MARKETPLACE_MX,
        "seller_sku": oferta.seller_sku,
        "asin": oferta.asin,
        "platform": oferta.platform,
        "canal": oferta.canal,
        "is_amazon_fulfilled": True,
        "price_amount": str(oferta.price_amount),
        "price_currency": oferta.price_currency,
        "fetched_at": oferta.fetched_at.isoformat(),
    }


def construir_fee_outcome_canonical(
    oferta: OfertaResuelta,
    resultado: ResultadoCotizacion,
    *,
    attempted_at: datetime,
) -> dict[str, Any]:
    """Identidad de persistencia: request + outcome normalizado."""
    base = construir_fee_canonical_input(oferta)
    obs = attempted_at if attempted_at.tzinfo else attempted_at.replace(tzinfo=UTC)
    obs_utc = obs.astimezone(UTC)
    if resultado.estado == "success":
        if resultado.fees_estimated_at is None or resultado.total_fees is None:
            raise FeesClientError("outcome success incompleto")
        fee_at = resultado.fees_estimated_at
        if fee_at.tzinfo is None:
            raise FeesClientError("fees_estimated_at debe ser tz-aware")
        return {
            **base,
            "outcome": "success",
            "fees_estimated_at": fee_at.astimezone(UTC).isoformat(),
            "total_fees": str(resultado.total_fees),
            "fee_details": resultado.fee_details,
        }
    return {
        **base,
        "outcome": "error",
        "error_code": resultado.error_code or "fee_error",
        "attempted_at": obs_utc.isoformat(),
    }


def construir_fee_source_event_id(canonical_outcome: dict[str, Any]) -> str:
    payload = json.dumps(canonical_outcome, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode()).hexdigest()[:32]
    return f"pf-fee:{digest}"


def _decimal_a_json_number(valor: Decimal) -> str:
    """Renderiza Decimal como literal JSON number exacto (sin float)."""
    cuantizado = valor.quantize(_MAX_DECIMALES)
    texto = format(cuantizado, "f")
    if "." in texto:
        texto = texto.rstrip("0").rstrip(".")
    return texto or "0"


def construir_request_body(oferta: OfertaResuelta) -> dict[str, Any]:
    """Vista del cuerpo; Amount permanece como Decimal (no float)."""
    return {
        "FeesEstimateRequest": {
            "MarketplaceId": MARKETPLACE_MX,
            "IsAmazonFulfilled": True,
            "PriceToEstimateFees": {
                "ListingPrice": {
                    "CurrencyCode": oferta.price_currency,
                    "Amount": oferta.price_amount.quantize(_MAX_DECIMALES),
                }
            },
            "Identifier": oferta.source_event_id,
        }
    }


def serializar_request_body(oferta: OfertaResuelta) -> bytes:
    """Bytes JSON deterministicos con Amount exacto desde Decimal."""
    amount = _decimal_a_json_number(oferta.price_amount)
    ident = json.dumps(oferta.source_event_id, separators=(",", ":"))
    moneda = json.dumps(oferta.price_currency, separators=(",", ":"))
    cuerpo = (
        '{"FeesEstimateRequest":{"MarketplaceId":"A1AM78C64UM0Y8",'
        '"IsAmazonFulfilled":true,'
        '"PriceToEstimateFees":{"ListingPrice":{'
        f'"CurrencyCode":{moneda},"Amount":{amount}'
        "}},"
        f'"Identifier":{ident}'
        "}}"
    )
    return cuerpo.encode("utf-8")


def _validar_universo(oferta: OfertaResuelta) -> str | None:
    clave = (oferta.platform, oferta.canal, oferta.price_currency)
    if clave not in _UNIVERSO_SOPORTADO:
        return "fee_universo_no_soportado"
    return None


def _decimal_dinero(valor: Any, *, campo: str) -> Decimal:
    if valor is None:
        raise FeesClientError(f"{campo} ausente")
    try:
        dec = Decimal(str(valor))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise FeesClientError(f"{campo} no numerico") from exc
    if not dec.is_finite():
        raise FeesClientError(f"{campo} no finito")
    if abs(dec) >= _MAX_DINERO:
        raise FeesClientError(f"{campo} fuera de rango NUMERIC(14,4)")
    try:
        cuantizado = dec.quantize(_MAX_DECIMALES)
    except InvalidOperation as exc:
        raise FeesClientError(f"{campo} fuera de rango NUMERIC(14,4)") from exc
    if dec != cuantizado:
        raise FeesClientError(f"{campo} excede 4 decimales")
    return cuantizado


def _montos_equivalentes(a: Decimal, b: Decimal) -> bool:
    return a.quantize(_MAX_DECIMALES) == b.quantize(_MAX_DECIMALES)


def _parsear_dinero_obj(obj: Any, *, campo: str) -> tuple[Decimal, str]:
    if not isinstance(obj, dict):
        raise FeesClientError(f"{campo} invalido")
    moneda = obj.get("CurrencyCode")
    if not isinstance(moneda, str) or not moneda.strip():
        raise FeesClientError(f"{campo}.CurrencyCode invalido")
    monto = _decimal_dinero(obj.get("Amount"), campo=f"{campo}.Amount")
    return monto, moneda.strip()


def _parsear_timestamp(texto: Any, *, campo: str) -> datetime:
    if not isinstance(texto, str) or not texto.strip():
        raise FeesClientError(f"{campo} ausente")
    raw = texto.strip()
    iso_raw = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(iso_raw)
        if parsed.tzinfo is None:
            raise FeesClientError(f"{campo} debe ser tz-aware")
        return parsed.astimezone(UTC)
    except ValueError:
        pass
    if _LEGACY_FEE_TS_RE.match(raw):
        try:
            return datetime.strptime(raw, "%a %b %d %H:%M:%S UTC %Y").replace(tzinfo=UTC)
        except ValueError as exc:
            raise FeesClientError(f"{campo} invalido") from exc
    raise FeesClientError(f"{campo} invalido")


def _codigo_error_item(err: Any) -> str:
    """Contrato oficial Error.Code; ErrorCode solo como respaldo sin Message/Detail."""
    if not isinstance(err, dict):
        return "fee_item_error"
    for campo in ("Code", "ErrorCode"):
        code = err.get(campo)
        if not isinstance(code, str):
            continue
        limpio = code.strip()
        if limpio and scrub(limpio) == limpio and re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", limpio):
            return f"fee_item_{limpio}"
    return "fee_item_error"


def _normalizar_detalle(det: Any, moneda_esperada: str) -> dict[str, Any]:
    if not isinstance(det, dict):
        raise FeesClientError("FeeDetailList elemento invalido")
    fee_type = det.get("FeeType")
    if not isinstance(fee_type, str) or not fee_type.strip():
        raise FeesClientError("FeeType ausente")
    fee_amount, moneda_amount = _parsear_dinero_obj(det.get("FeeAmount"), campo="FeeAmount")
    if moneda_amount != moneda_esperada:
        raise FeesClientError("FeeAmount moneda incompatible")
    promo_monto: Decimal | None = None
    if "FeePromotion" in det and det.get("FeePromotion") is not None:
        promo_monto, moneda_promo = _parsear_dinero_obj(
            det.get("FeePromotion"), campo="FeePromotion"
        )
        if moneda_promo != moneda_esperada:
            raise FeesClientError("FeePromotion moneda incompatible")
    final_monto, moneda_final = _parsear_dinero_obj(det.get("FinalFee"), campo="FinalFee")
    if moneda_final != moneda_esperada:
        raise FeesClientError("FinalFee moneda incompatible")
    esperado = fee_amount - (promo_monto if promo_monto is not None else Decimal("0"))
    if not _montos_equivalentes(final_monto, esperado):
        raise FeesClientError("FinalFee no concilia con FeeAmount - FeePromotion")
    normalizado: dict[str, Any] = {
        "fee_type": fee_type.strip(),
        "fee_amount": str(fee_amount),
        "final_fee": str(final_monto),
        "currency": moneda_esperada,
    }
    if promo_monto is not None:
        normalizado["fee_promotion"] = str(promo_monto)
    if "TaxAmount" in det and det.get("TaxAmount") is not None:
        tax_monto, moneda_tax = _parsear_dinero_obj(det.get("TaxAmount"), campo="TaxAmount")
        if moneda_tax != moneda_esperada:
            raise FeesClientError("TaxAmount moneda incompatible")
        if tax_monto != Decimal("0"):
            normalizado["tax_amount"] = str(tax_monto)
    incluidos = det.get("IncludedFeeDetailList")
    if incluidos is not None:
        if not isinstance(incluidos, list):
            raise FeesClientError("IncludedFeeDetailList invalido")
        normalizado["included_fee_details"] = [
            _normalizar_detalle(incluido, moneda_esperada) for incluido in incluidos
        ]
    return normalizado


def parsear_respuesta_fees(
    body: dict[str, Any],
    oferta: OfertaResuelta,
    *,
    observed_at: datetime,
) -> ResultadoCotizacion:
    """Valida contrato Success y devuelve total = suma de FinalFee."""
    obs = observed_at if observed_at.tzinfo else observed_at.replace(tzinfo=UTC)
    obs_utc = obs.astimezone(UTC)
    if not isinstance(body, dict):
        raise FeesClientError("fee_json_invalido")
    payload = body.get("payload", body)
    if not isinstance(payload, dict):
        raise FeesClientError("fee_json_invalido")
    resultado = payload.get("FeesEstimateResult")
    if not isinstance(resultado, dict):
        raise FeesClientError("fee_contrato_incompatible")
    status = resultado.get("Status")
    if status != "Success":
        codigo = _codigo_error_item(resultado.get("Error"))
        return ResultadoCotizacion(
            estado="error",
            total_fees=None,
            fee_details=[],
            fees_estimated_at=None,
            error_code=codigo,
            fetched_at=obs_utc,
        )

    ident = resultado.get("FeesEstimateIdentifier")
    estimate = resultado.get("FeesEstimate")
    if not isinstance(ident, dict) or not isinstance(estimate, dict):
        raise FeesClientError("fee_contrato_incompatible")

    if ident.get("IdType") != "SellerSKU":
        raise FeesClientError("identificador IdType incompatible")
    if ident.get("IdValue") != oferta.seller_sku:
        raise FeesClientError("identificador IdValue incompatible")
    if ident.get("SellerInputIdentifier") != oferta.source_event_id:
        raise FeesClientError("identificador SellerInputIdentifier incompatible")
    if ident.get("MarketplaceId") != MARKETPLACE_MX:
        raise FeesClientError("identificador MarketplaceId incompatible")
    if ident.get("IsAmazonFulfilled") is not True:
        raise FeesClientError("identificador IsAmazonFulfilled incompatible")
    pte = ident.get("PriceToEstimateFees")
    if not isinstance(pte, dict):
        raise FeesClientError("identificador PriceToEstimateFees incompatible")
    lp = pte.get("ListingPrice")
    if not isinstance(lp, dict):
        raise FeesClientError("identificador ListingPrice incompatible")
    precio_id, moneda_id = _parsear_dinero_obj(lp, campo="ListingPrice")
    if moneda_id != oferta.price_currency:
        raise FeesClientError("identificador moneda incompatible")
    if not _montos_equivalentes(precio_id, oferta.price_amount):
        raise FeesClientError("identificador precio incompatible")

    fees_at = _parsear_timestamp(estimate.get("TimeOfFeesEstimation"), campo="TimeOfFeesEstimation")
    if fees_at < oferta.fetched_at:
        raise FeesClientError("fees_estimated_at anterior a fetched_at oferta")
    if fees_at > obs_utc:
        raise FeesClientError("fees_estimated_at posterior a observed_at")

    total_obj = estimate.get("TotalFeesEstimate")
    if not isinstance(total_obj, dict):
        raise FeesClientError("fee_contrato_incompatible")
    total_api, moneda_total = _parsear_dinero_obj(total_obj, campo="TotalFeesEstimate")
    if moneda_total != oferta.price_currency:
        raise FeesClientError("total moneda incompatible")

    detalles_raw = estimate.get("FeeDetailList")
    if not isinstance(detalles_raw, list) or not detalles_raw:
        raise FeesClientError("FeeDetailList vacio")
    detalles = [_normalizar_detalle(d, oferta.price_currency) for d in detalles_raw]
    suma = sum(Decimal(d["final_fee"]) for d in detalles).quantize(_MAX_DECIMALES)
    if not _montos_equivalentes(suma, total_api):
        raise FeesClientError("total no concilia con suma FinalFee")

    return ResultadoCotizacion(
        estado="success",
        total_fees=suma,
        fee_details=detalles,
        fees_estimated_at=fees_at,
        error_code=None,
        fetched_at=obs_utc,
    )


class _RateLimiter:
    def __init__(
        self,
        *,
        clock: Callable[[], float],
        sleep: Callable[[float], None],
        interval: float = RATE_INTERVAL_SECONDS,
        burst: int = RATE_BURST,
        max_waits: int = RATE_MAX_WAITS,
    ) -> None:
        self._clock = clock
        self._sleep = sleep
        self._interval = interval
        self._burst = burst
        self._max_waits = max_waits
        self._tokens = float(burst)
        self._last = clock()

    def acquire(self) -> None:
        for _ in range(self._max_waits + 1):
            ahora = self._clock()
            elapsed = max(0.0, ahora - self._last)
            self._tokens = min(self._burst, self._tokens + elapsed / self._interval)
            self._last = ahora
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return
            wait = max(0.0, (1.0 - self._tokens) * self._interval)
            prev = ahora
            self._sleep(wait)
            after = self._clock()
            if after - prev < wait and wait > 0:
                # Reloj inyectado que no avanza: acreditar el tiempo esperado.
                faltante = wait - max(0.0, after - prev)
                self._tokens = min(self._burst, self._tokens + faltante / self._interval)
                self._last = prev + wait
        raise FeesClientError("fee_rate_limit_agotado")


class ProductFeesClient:
    """Cliente SP-API Product Fees v0 (solo consulta, separado de Ads).

    El refrescador LWA es el unico de `app/spapi` (D5): el token sale de un
    `SpapiClient` compartido por proceso. El HTTP de fees conserva su
    validacion, rate-limit y retries (comportamiento intacto en A.1).
    """

    def __init__(
        self,
        *,
        secrets_dir: Path | str | None = None,
        credentials: dict[str, str] | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
        spapi_client: SpapiClient | None = None,
    ) -> None:
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        self._transport = transport
        self._sleep = sleep
        self._clock = clock
        self._timeout = timeout
        self._fees_limiter = _RateLimiter(clock=clock, sleep=sleep)
        self._credentials = credentials or self._cargar_credenciales(secrets_dir)
        self._spapi = spapi_client or SpapiClient(
            credentials=dict(self._credentials),
            transport=transport,
            sleep=sleep,
            clock=clock,
            timeout=timeout,
        )

    def _cargar_credenciales(self, secrets_dir: Path | str | None) -> dict[str, str]:
        base = Path(
            secrets_dir or os.environ.get("ORBIT_SECRETS_DIR", "/mnt/data/appdata/orbit/secrets")
        )
        config = json.loads((base / "amazon_credentials.json").read_text())
        campos = {k: config[k] for k in ("lwa_app_id", "lwa_client_secret", "refresh_token")}
        for valor in campos.values():
            if not isinstance(valor, str) or not valor.strip():
                raise ValueError("credenciales incompletas")
            register_secret(valor)
        return campos

    def _validar_url_fees(self, method: str, url: str, *, seller_sku: str) -> None:
        if method != "POST":
            raise MutationNotAllowedError(f"metodo no permitido: {method}")
        if seller_sku in {"", ".", ".."}:
            raise MutationNotAllowedError("SellerSKU invalido en ruta")
        partes = urlsplit(url)
        if partes.scheme != "https":
            raise MutationNotAllowedError("esquema no permitido")
        if partes.netloc != SP_API_HOST:
            raise MutationNotAllowedError("host SP-API no permitido")
        if partes.query or partes.fragment:
            raise MutationNotAllowedError("query o fragment no permitidos")
        if partes.username or partes.password:
            raise MutationNotAllowedError("userinfo no permitido")
        if partes.port is not None:
            raise MutationNotAllowedError("puerto explicito no permitido")
        if url != f"{SP_API_BASE}{partes.path}":
            raise MutationNotAllowedError("URL Product Fees no exacta")
        path = partes.path
        if not _FEES_PATH_RE.match(path):
            raise MutationNotAllowedError("ruta Product Fees no permitida")
        segmento = path[len(FEES_PATH_PREFIX) : -len(FEES_PATH_SUFFIX)]
        if not segmento:
            raise MutationNotAllowedError("SellerSKU vacio en ruta")
        if unquote(segmento) != seller_sku:
            raise MutationNotAllowedError("SellerSKU en ruta no coincide")
        if segmento != quote(seller_sku, safe=""):
            raise MutationNotAllowedError("SellerSKU sin percent-encoding completo")

    def _validar_url_lwa(self, method: str, url: str) -> None:
        if method != "POST":
            raise MutationNotAllowedError(f"metodo no permitido: {method}")
        partes = urlsplit(url)
        if partes.scheme != "https" or partes.netloc != "api.amazon.com":
            raise MutationNotAllowedError("host LWA no permitido")
        if partes.query or partes.fragment:
            raise MutationNotAllowedError("query o fragment no permitidos")
        if partes.username or partes.password:
            raise MutationNotAllowedError("userinfo no permitido")
        if partes.port is not None:
            raise MutationNotAllowedError("puerto explicito no permitido")
        if url != LWA_TOKEN_URL:
            raise MutationNotAllowedError("LWA: solo POST exacto auth/o2/token")

    def _validar_url(self, method: str, url: str, *, seller_sku: str | None = None) -> None:
        partes = urlsplit(url)
        if method == "POST" and partes.scheme == "https" and partes.netloc == "api.amazon.com":
            self._validar_url_lwa(method, url)
            return
        if seller_sku is None:
            raise MutationNotAllowedError("SellerSKU requerido para Product Fees")
        self._validar_url_fees(method, url, seller_sku=seller_sku)

    def _request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: dict | None = None,
        content: bytes | None = None,
        data: dict | None = None,
        seller_sku: str | None = None,
        rate_limit: bool = False,
    ) -> httpx.Response:
        if not _ABSOLUTE_URL_RE.match(url.split("?", 1)[0]):
            raise MutationNotAllowedError("URL relativa no permitida")
        self._validar_url(method, url, seller_sku=seller_sku)
        if rate_limit:
            self._fees_limiter.acquire()
        with httpx.Client(timeout=self._timeout, transport=self._transport) as client:
            if content is not None:
                return client.request(method, url, headers=headers, content=content)
            return client.request(method, url, headers=headers, json=json_body, data=data)

    def _acceso(self) -> str:
        # Refrescador unico de app.spapi (D5): mismo token por proceso cuando
        # se comparte la instancia; HTTP y retries de fees intactos. Los
        # fallos LWA se traducen a los mismos codigos de antes (rechazo ->
        # fee_http_error como el HTTPStatusError previo; token ausente ->
        # fee_error via "token LWA ausente"); la red propaga httpx igual.
        try:
            return self._spapi._acceso()
        except SpapiAuthError as exc:
            if "rechazado" in str(exc):
                raise FeesClientError("fee_http_error") from None
            raise FeesClientError("token LWA ausente") from None

    def _retry_after_seconds(self, response: httpx.Response) -> float:
        raw = response.headers.get("Retry-After", "1")
        try:
            segundos = float(raw)
        except ValueError:
            segundos = 1.0
        return min(max(0.0, segundos), RETRY_AFTER_MAX_SECONDS)

    def cotizar(self, oferta: OfertaResuelta) -> dict[str, Any]:
        """POST feesEstimate; reintenta 429/timeout; devuelve JSON parseado o lanza."""
        ruta = construir_ruta_fees(oferta.seller_sku)
        url = f"{SP_API_BASE}{ruta}"
        cuerpo_bytes = serializar_request_body(oferta)
        ultimo_error: Exception | None = None
        for intento in range(MAX_RETRIES):
            try:
                token = self._acceso()
                response = self._request(
                    "POST",
                    url,
                    headers={
                        "x-amz-access-token": token,
                        "Content-Type": "application/json",
                    },
                    content=cuerpo_bytes,
                    seller_sku=oferta.seller_sku,
                    rate_limit=True,
                )
            except httpx.TimeoutException as exc:
                ultimo_error = exc
                if intento + 1 >= MAX_RETRIES:
                    raise FeesClientError("fee_timeout") from None
                self._sleep(min(2.0**intento, RETRY_AFTER_MAX_SECONDS))
                continue
            except httpx.HTTPError:
                raise FeesClientError("fee_http_error") from None

            if response.status_code == 403:
                raise FeesClientError("fee_http_403")
            if response.status_code in RETRYABLE_STATUSES:
                if intento + 1 >= MAX_RETRIES:
                    raise FeesClientError("fee_http_429_agotado")
                self._sleep(self._retry_after_seconds(response))
                continue
            if response.status_code >= 500:
                if intento + 1 >= MAX_RETRIES:
                    raise FeesClientError("fee_http_5xx")
                self._sleep(min(2.0**intento, RETRY_AFTER_MAX_SECONDS))
                continue
            if response.status_code != 200:
                raise FeesClientError(f"fee_http_{response.status_code}")

            try:
                parsed = json.loads(response.content, parse_float=Decimal, parse_int=Decimal)
                if not isinstance(parsed, dict):
                    raise FeesClientError("fee_json_invalido")
                return parsed
            except ValueError as exc:
                raise FeesClientError("fee_json_invalido") from exc

        raise FeesClientError("fee_timeout") from ultimo_error


def cotizar_oferta(
    client: ProductFeesClient,
    oferta: OfertaResuelta,
    *,
    observed_at: datetime | None = None,
    now_utc: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> ResultadoCotizacion:
    def captura() -> datetime:
        valor = observed_at if observed_at is not None else now_utc()
        if valor.tzinfo is None:
            valor = valor.replace(tzinfo=UTC)
        return valor.astimezone(UTC)

    motivo = _validar_universo(oferta)
    if motivo is not None:
        capturada = captura()
        return ResultadoCotizacion(
            estado="error",
            total_fees=None,
            fee_details=[],
            fees_estimated_at=None,
            error_code=motivo,
            fetched_at=capturada,
        )
    try:
        body = client.cotizar(oferta)
    except FeesClientError as exc:
        capturada = captura()
        codigo = str(exc) if str(exc).startswith("fee_") else "fee_error"
        return ResultadoCotizacion(
            estado="error",
            total_fees=None,
            fee_details=[],
            fees_estimated_at=None,
            error_code=codigo,
            fetched_at=capturada,
        )
    capturada = captura()
    try:
        return parsear_respuesta_fees(body, oferta, observed_at=capturada)
    except FeesClientError as exc:
        codigo = str(exc) if str(exc).startswith("fee_") else "fee_contrato_incompatible"
        return ResultadoCotizacion(
            estado="error",
            total_fees=None,
            fee_details=[],
            fees_estimated_at=None,
            error_code=codigo,
            fetched_at=capturada,
        )


def persistir_fee_observation(
    conn: psycopg.Connection,
    *,
    oferta_observation_id: int,
    oferta: OfertaResuelta,
    resultado: ResultadoCotizacion,
    observed_at: datetime,
    source_event_id: str,
    canonical_input: dict[str, Any],
    ingest_run_id: int | None = None,
) -> ResultadoPersistenciaFee:
    """INSERT idempotente por source_event_id; no commit oculto."""
    obs = observed_at if observed_at.tzinfo else observed_at.replace(tzinfo=UTC)
    fila = conn.execute(
        "INSERT INTO estimacion_fee_observation"
        " (oferta_observation_id, listing_id, platform, seller_sku, asin, canal,"
        " quoted_price_amount, quoted_price_currency, total_fees, fee_details,"
        " fees_estimated_at, fetched_at, observed_at, estado, error_code,"
        " source_event_id, canonical_input, context_fingerprint, ingest_run_id)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
        " ON CONFLICT (source_event_id) DO NOTHING"
        " RETURNING id, observed_at",
        (
            oferta_observation_id,
            oferta.listing_id,
            oferta.platform,
            oferta.seller_sku,
            oferta.asin,
            oferta.canal,
            oferta.price_amount,
            oferta.price_currency,
            resultado.total_fees,
            Json(resultado.fee_details),
            resultado.fees_estimated_at,
            resultado.fetched_at,
            obs.astimezone(UTC),
            resultado.estado,
            resultado.error_code,
            source_event_id,
            Json(canonical_input),
            oferta.context_fingerprint,
            ingest_run_id,
        ),
    ).fetchone()
    if fila is not None:
        return ResultadoPersistenciaFee(id=fila[0], observed_at=fila[1], reutilizada=False)
    existente = conn.execute(
        "SELECT id, observed_at FROM estimacion_fee_observation WHERE source_event_id = %s",
        (source_event_id,),
    ).fetchone()
    if existente is None:
        raise FeesClientError(f"conflicto sin fila previa para source_event_id={source_event_id}")
    return ResultadoPersistenciaFee(id=existente[0], observed_at=existente[1], reutilizada=True)
