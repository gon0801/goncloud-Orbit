"""Cliente HTTP Amazon Ads READ-ONLY (LWA + Advertising API v3).

Un solo modulo encapsula el refresh de LWA: el `refresh_token` de LWA NO
rota (decision sellada), asi que un refresco por proceso -- cacheado en
esta instancia -- es seguro (la leccion "un solo refrescador" era por los
tokens ROTATIVOS de MeLi; no aplica aqui).

Guard read-only CENTRAL en `_request`: default-deny. GET siempre permitido
(tras validar que el path es seguro); POST SOLO para crear un reporte
(`/reporting/reports`, la unica escritura permitida); PUT/PATCH/DELETE y
cualquier otro metodo se rechazan siempre. La superficie publica expone
solo `get`, `create_report`, `get_report` y `download` -- nunca un metodo
generico `request`/`post`.

Redaccion: las excepciones propias llevan SOLO metodo + path (sin query) +
status; nunca headers ni body (los errores de LWA pueden ecoar el
client_id). El logger del modulo hace lo mismo. `httpx`/`httpcore` se
silencian a WARNING porque emiten la URL completa (con query firmada) a
INFO/DEBUG.
"""

from __future__ import annotations

import logging
import random
import re
import time
from collections.abc import Callable
from urllib.parse import urljoin

import httpx

from app.ads.config import AdsCredentials
from app.redaction import install_scrub_filter, redact_url, register_secret, scrub

logger = logging.getLogger(__name__)
install_scrub_filter(logger)

DEFAULT_BASE_URL = "https://advertising-api.amazon.com"
LWA_TOKEN_URL = "https://api.amazon.com/auth/o2/token"
TOKEN_EXPIRY_MARGIN_SECONDS = 60.0
MAX_RETRIES = 4
MAX_REDIRECTS = 5
REPORT_REQUEST_PATH = "/reporting/reports"
RETRYABLE_STATUSES = {429}
RETRY_AFTER_MAX_SECONDS = 60.0

_ABSOLUTE_URL_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://")


class AdsClientError(Exception):
    """Base de los errores propios del cliente de Amazon Ads.

    El mensaje se pasa siempre por `scrub()` como ultima linea de defensa;
    el diseno del cliente ya evita tocar secretos al construir el mensaje.
    """

    def __init__(self, message: str) -> None:
        super().__init__(scrub(message))


class AdsApiError(AdsClientError):
    """La API de Amazon Ads (o la descarga de un reporte) fallo tras agotar retries."""


class AdsAuthError(AdsClientError):
    """Fallo autenticando/refrescando el token LWA."""


class MutationNotAllowedError(AdsClientError):
    """El guard read-only bloqueo un metodo o una ruta no permitidos."""


def _clean_path(path: str) -> str:
    return path.split("?", 1)[0]


def _validate_relative_path(path: str) -> None:
    """Valida que `path` sea un path relativo seguro (sin resolverlo).

    Rechaza URLs absolutas, traversal (`..`) y encodings de barra (`%2f`,
    case-insensitive). No colapsa `//`: el allow-check de creacion de
    reportes exige igualdad literal exacta contra `/reporting/reports`, asi
    que cualquier variante (incluida una doble barra que colapsaria al path
    permitido) se rechaza por diseno conservador (default-deny estricto).
    """
    if not path or not path.startswith("/") or _ABSOLUTE_URL_RE.match(path):
        raise MutationNotAllowedError("path invalido: debe ser relativo y empezar con '/'")
    if "%2f" in path.lower():
        raise MutationNotAllowedError("path invalido: encoding de barra no permitido")
    if ".." in _clean_path(path).split("/"):
        raise MutationNotAllowedError("path invalido: traversal ('..') no permitido")


class AdsClient:
    """Cliente HTTP read-only para Amazon Ads Advertising API v3."""

    def __init__(
        self,
        credentials: AdsCredentials,
        *,
        base_url: str = DEFAULT_BASE_URL,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        timeout: httpx.Timeout | None = None,
    ) -> None:
        # httpx/httpcore emiten la URL completa (query firmada incluida) a
        # INFO/DEBUG; se silencian al construir el cliente (nunca a nivel
        # de import) para no depender de que alguien mas lo haga.
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)

        register_secret(credentials.client_secret)
        register_secret(credentials.refresh_token)

        self._credentials = credentials
        self._base_url = base_url.rstrip("/")
        self._sleep = sleep
        self._clock = clock
        effective_timeout = timeout or httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0)
        self._client = httpx.Client(
            transport=transport,
            timeout=effective_timeout,
            follow_redirects=False,
        )

        self._access_token: str | None = None
        self._token_expires_at: float | None = None  # timestamp en la escala de `clock`

    # ------------------------------------------------------------------
    # Superficie publica (EXACTA: get, create_report, get_report, download)
    # ------------------------------------------------------------------

    def get(
        self,
        path: str,
        *,
        params: dict | None = None,
        profile_id: str | int | None = None,
    ) -> httpx.Response:
        return self._request("GET", path, params=params, profile_id=profile_id)

    def create_report(self, body: dict, *, profile_id: str | int) -> httpx.Response:
        """Crea un reporte. Unica escritura permitida por el guard read-only."""
        return self._request("POST", REPORT_REQUEST_PATH, json=body, profile_id=profile_id)

    def get_report(self, report_id: str, *, profile_id: str | int) -> httpx.Response:
        path = f"{REPORT_REQUEST_PATH}/{report_id}"
        return self._request("GET", path, profile_id=profile_id)

    def download(self, url: str) -> httpx.Response:
        """Descarga desde una URL (tipicamente firmada, fuera de `base_url`).

        `follow_redirects=False` esta seteado en el cliente httpx interno:
        los redirects se resuelven aqui a mano, re-emitiendo SIEMPRE GET
        (el metodo jamas cambia en un redirect), con tope de saltos.
        """
        current_url = url
        for _ in range(MAX_REDIRECTS + 1):
            resp = self._send_with_retries("GET", current_url, headers={})
            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get("location")
                if not location:
                    raise AdsApiError(f"redirect sin Location: GET {redact_url(current_url)}")
                current_url = urljoin(current_url, location)
                continue
            if resp.status_code >= 400:
                raise AdsApiError(f"status={resp.status_code}: GET {redact_url(current_url)}")
            return resp
        raise AdsApiError(f"demasiados redirects (> {MAX_REDIRECTS}): GET {redact_url(url)}")

    # ------------------------------------------------------------------
    # Guard read-only central + despacho autenticado
    # ------------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: dict | None = None,
        profile_id: str | int | None = None,
    ) -> httpx.Response:
        # Default-deny: solo GET y el POST de creacion de reportes sobreviven.
        if method not in ("GET", "POST"):
            raise MutationNotAllowedError(f"metodo no permitido: {method} {_clean_path(path)}")
        _validate_relative_path(path)
        if method == "POST" and path != REPORT_REQUEST_PATH:
            raise MutationNotAllowedError(
                f"POST no permitido fuera de {REPORT_REQUEST_PATH}: {_clean_path(path)}"
            )

        url = f"{self._base_url}{path}"
        token = self._ensure_token()
        headers = self._build_headers(token, profile_id)
        resp = self._send_with_retries(method, url, headers=headers, params=params, json=json)

        if resp.status_code == 401:
            # UN refresh forzado + UN retry, nada mas (no se compone con 429/5xx).
            token = self._ensure_token(force=True)
            headers = self._build_headers(token, profile_id)
            resp = self._send_with_retries(method, url, headers=headers, params=params, json=json)

        if resp.status_code >= 400:
            raise AdsApiError(f"status={resp.status_code}: {method} {redact_url(url)}")
        return resp

    def _build_headers(self, token: str, profile_id: str | int | None) -> dict[str, str]:
        headers = {
            "Amazon-Advertising-API-ClientId": self._credentials.client_id,
            "Authorization": f"Bearer {token}",
        }
        if profile_id is not None:
            headers["Amazon-Advertising-API-Scope"] = str(profile_id)
        return headers

    # ------------------------------------------------------------------
    # Retries: 429/5xx/errores de red, backoff exponencial + jitter
    # ------------------------------------------------------------------

    def _send_with_retries(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        params: dict | None = None,
        json: dict | None = None,
    ) -> httpx.Response:
        attempt = 0
        while True:
            attempt += 1
            network_failed = False
            resp: httpx.Response | None = None
            try:
                resp = self._client.request(method, url, headers=headers, params=params, json=json)
            except httpx.HTTPError:
                network_failed = True

            if network_failed:
                if attempt >= MAX_RETRIES:
                    raise AdsApiError(
                        f"fallo de red tras {attempt} intentos: {method} {redact_url(url)}"
                    )
                self._sleep(self._backoff_delay(attempt))
                continue

            logger.debug("%s %s status=%s", method, redact_url(url), resp.status_code)

            if resp.status_code in RETRYABLE_STATUSES or resp.status_code >= 500:
                if attempt >= MAX_RETRIES:
                    raise AdsApiError(
                        f"status={resp.status_code} tras {attempt} intentos: "
                        f"{method} {redact_url(url)}"
                    )
                self._sleep(self._retry_delay(resp, attempt))
                continue

            return resp

    def _backoff_delay(self, attempt: int) -> float:
        base = 2 ** (attempt - 1)
        jitter = random.uniform(0, base * 0.25)
        return base + jitter

    def _retry_delay(self, resp: httpx.Response, attempt: int) -> float:
        # Retry-After solo se honra si parsea Y cae en un rango sensato
        # (fail-closed): un valor negativo rompe `time.sleep`, uno
        # absurdamente grande bloquearia el proceso mucho mas de lo
        # razonable. Fuera de rango o no parseable -> backoff normal.
        retry_after = resp.headers.get("Retry-After")
        if retry_after is not None:
            try:
                parsed = float(retry_after)
            except ValueError:
                parsed = None
            if parsed is not None and 0 <= parsed <= RETRY_AFTER_MAX_SECONDS:
                return parsed
        return self._backoff_delay(attempt)

    # ------------------------------------------------------------------
    # LWA: refresh por proceso, cacheado con margen de 60s
    # ------------------------------------------------------------------

    def _ensure_token(self, *, force: bool = False) -> str:
        now = self._clock()
        if (
            not force
            and self._access_token is not None
            and self._token_expires_at is not None
            and now < self._token_expires_at - TOKEN_EXPIRY_MARGIN_SECONDS
        ):
            return self._access_token
        self._refresh_token()
        if self._access_token is None:  # invariante: _refresh_token siempre lo setea o lanza
            raise AdsAuthError("refresh de LWA no produjo access_token")
        return self._access_token

    def _refresh_token(self) -> None:
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": self._credentials.refresh_token,
            "client_id": self._credentials.client_id,
            "client_secret": self._credentials.client_secret,
        }
        error: AdsAuthError | None = None
        resp: httpx.Response | None = None
        try:
            resp = self._client.post(LWA_TOKEN_URL, data=payload)
        except httpx.HTTPError:
            error = AdsAuthError(
                f"fallo de red refrescando el token LWA: POST {redact_url(LWA_TOKEN_URL)}"
            )
        if error is not None:
            raise error from None

        logger.debug("POST %s status=%s", redact_url(LWA_TOKEN_URL), resp.status_code)

        if resp.status_code >= 400:
            raise AdsAuthError(f"LWA rechazo el refresh: status={resp.status_code}")

        try:
            data = resp.json()
            access_token = data["access_token"]
            expires_in = float(data["expires_in"])
        except (ValueError, KeyError, TypeError):
            raise AdsAuthError("respuesta de LWA sin access_token/expires_in validos") from None

        register_secret(access_token)
        self._access_token = access_token
        self._token_expires_at = self._clock() + expires_in
