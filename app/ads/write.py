"""Cliente HTTP Amazon Ads de ESCRITURA — mutaciones selladas (ORBIT 04 1.3).

Subclase de `AdsClient`: hereda el refresh de LWA (un solo refrescador por
proceso), `_send_with_retries`, la redaccion y el guard read-only del padre.
El read client queda INTACTO (su guard sigue rechazando PUT/PATCH/DELETE):
las mutaciones de esta clase JAMAS pasan por `_request` — van por
`_send_with_retries` directo con `idempotent=False`, espejo de la semantica
de `create_report`: 429 reintenta (rechazado sin procesar); 5xx/fallo de red
son AMBIGUOS con un PUT/POST/DELETE ya emitido → lanza SIN reintento
(sellado 8 del plan).

Diseno SELLADO (plans/orbit-04.md decision 9; docs/APPLY.md §8):

- El constructor exige `modo_confirmado == 'live'` EXACTAMENTE (cualquier
  otra cosa revienta `MutationNotAllowedError` ANTES de construir nada):
  la escalera de modo se re-resuelve POR DECISION en el caller antes de
  construir. Fail-closed.
- `platform` ∈ {amazon_us, amazon_mx} (vocabulario cerrado; otra cosa →
  ValueError ruidoso) y `profile_id` queda SELLADO a la instancia: TODA
  mutacion sale con `Amazon-Advertising-API-Scope` = ese profile. Ningun
  metodo acepta profile/platform: un scope equivocado (un apply MX con
  scope US escribe en la cuenta equivocada) es INEXPRESABLE en la
  superficie publica (candado por test con inspect).
- Allowlist default-deny `MUTATION_REQUEST_TYPES`: clave (METODO, path),
  valor vendor Content-Type/Accept, por igualdad literal EXACTA (dos capas
  como `list_objects`); cualquier otro par revienta
  `MutationNotAllowedError` ANTES de pedir token.
- Payload de UN objeto por mutacion: un parametro multi (lista/dict/etc)
  revienta ValueError antes de tocar la red.
- Presentacion sellada: el bid viaja quantizado a 2 decimales (sin
  quantize, el payload llevaria los 4 decimales del NUMERIC de origen) y
  como string (float prohibido para dinero, regla 4); la moneda se
  verifica contra `PLATAFORMA_MONEDA[platform]` (el caller pasa
  `goal.bid_currency`) ANTES del HTTP — si no coincide, algo esta podrido
  y NO se escribe.
- Quien puede importar este modulo: candado en tests/test_architecture.py
  — solo `app/apply.py` y `tools/smoke_apply.py` (r2 codex 5).

PENDIENTE del probe autorizado 2.5 (brief APPLY §13, sellado 23): los
paths y vendor types de MUTACION no estan verificados en vivo (solo
`/sp/negativeKeywords/list` lo esta — regla 8, 2026-08-25, log
out/regla8-negkeywords.log) y los shapes de los acks los fija el probe; los
tests de este modulo usan MockTransport y NO dependen de que los endpoints
reales existan. El sello/parseo de acks es del aplicador (2.1).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from decimal import Decimal
from types import MappingProxyType

import httpx

from app.ads.client import (
    DEFAULT_BASE_URL,
    AdsApiError,
    AdsClient,
    MutationNotAllowedError,
    _clean_path,
    _validate_relative_path,
)
from app.ads.config import AdsCredentials
from app.redaction import redact_url

MODO_CONFIRMADO_LIVE = "live"

# Mapa sellado plataforma → moneda. El motor tiene el suyo (capa distinta,
# misma ley): una decision, un camino, un dueño — este mapa es de la capa
# HTTP y el del motor, de la capa de decisiones.
PLATAFORMA_MONEDA: MappingProxyType[str, str] = MappingProxyType(
    {
        "amazon_us": "USD",
        "amazon_mx": "MXN",
    }
)

# Allowlist default-deny de MUTACIONES: clave (METODO, path), valor vendor
# Content-Type/Accept. Igualdad literal EXACTA contra el par completo —
# cualquier otro par revienta. MappingProxyType por la misma razon que
# LIST_REQUEST_TYPES: es una allowlist de SEGURIDAD leida en vivo por el
# guard; congelarla evita que una mutacion accidental amplie la superficie.
#
# PENDIENTES del probe autorizado 2.5 (brief §13, sellado 23): los paths y
# vendor types de mutacion NO estan verificados en vivo — solo el del list
# de negatives lo esta (regla 8, 2026-08-25). El probe fija shapes de acks
# y confirma/corrige estos pares; hasta entonces los tests de este modulo
# corren 100% contra MockTransport.
MUTATION_REQUEST_TYPES: MappingProxyType[tuple[str, str], str] = MappingProxyType(
    {
        ("PUT", "/sp/keywords"): "application/vnd.spkeyword.v3+json",
        ("PUT", "/sp/targets"): "application/vnd.sptargetingclause.v3+json",
        ("POST", "/sp/negativeKeywords"): "application/vnd.spnegativekeyword.v3+json",
        ("DELETE", "/sp/negativeKeywords"): "application/vnd.spnegativekeyword.v3+json",
        ("POST", "/sp/keywords"): "application/vnd.spkeyword.v3+json",
        ("DELETE", "/sp/keywords"): "application/vnd.spkeyword.v3+json",
    }
)


def _un_objeto(valor: object, nombre: str) -> object:
    """Exige un valor ESCALAR: una coleccion (lista/dict/tuple/set) aqui
    significa un payload multi-objeto, sellado FUERA (ValueError antes de
    armar el payload y antes de cualquier HTTP)."""
    if isinstance(valor, (list, tuple, set, dict)):
        raise ValueError(
            f"{nombre} debe ser un unico objeto, no una coleccion: "
            "el payload de mutacion lleva UN objeto por request"
        )
    return valor


def _bid_payload(bid: Decimal) -> str:
    """Bid quantizado a 2 decimales (presentacion sellada; el NUMERIC de
    origen trae 4) y serializado como string: float prohibido para dinero
    (regla 4) y json.dumps no serializa Decimal."""
    if not isinstance(bid, Decimal):
        raise TypeError(
            f"bid debe ser Decimal, no {type(bid).__name__} (float prohibido para dinero)"
        )
    return str(bid.quantize(Decimal("0.01")))


class AdsWriteClient(AdsClient):
    """Cliente de escritura Amazon Ads: SOLO las mutaciones del allowlist.

    La superficie publica es EXACTA (10 metodos, ningun generico
    request/post) y ningun metodo acepta profile/platform: el scope vive en
    la instancia. Devuelven el `httpx.Response` crudo (el ack); el
    sello/parseo es del aplicador (2.1).
    """

    def __init__(
        self,
        credentials: AdsCredentials,
        *,
        platform: str,
        profile_id: str | int,
        modo_confirmado: str,
        base_url: str = DEFAULT_BASE_URL,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        timeout: httpx.Timeout | None = None,
    ) -> None:
        if platform not in PLATAFORMA_MONEDA:
            raise ValueError(
                f"platform invalida: {platform!r} (vocabulario cerrado: "
                f"{sorted(PLATAFORMA_MONEDA)})"
            )
        if modo_confirmado != MODO_CONFIRMADO_LIVE:
            # Fail-closed ANTES de construir nada: la escalera se re-resuelve
            # POR DECISION en el caller (escalera + goal.mode + enabled +
            # existencia); este cliente es la unica puerta de escritura y no
            # abre por defecto.
            raise MutationNotAllowedError(
                f"modo_confirmado={modo_confirmado!r}: el cliente de escritura "
                f"SOLO se construye en '{MODO_CONFIRMADO_LIVE}' (decision 9/22)"
            )
        super().__init__(
            credentials,
            base_url=base_url,
            transport=transport,
            sleep=sleep,
            clock=clock,
            timeout=timeout,
        )
        self._platform = platform
        # Scope SELLADO a la instancia: TODA mutacion sale con este profile.
        self._profile_id = str(profile_id)

    # ------------------------------------------------------------------
    # Superficie publica EXACTA: bids, estados, negatives, keywords
    # ------------------------------------------------------------------

    def actualizar_bid_keyword(
        self, keyword_id: str | int, bid: Decimal, moneda: str
    ) -> httpx.Response:
        """PUT /sp/keywords: update del bid de UNA keyword."""
        self._verificar_moneda(moneda)
        return self._mutate(
            "PUT",
            "/sp/keywords",
            {
                "keywordId": _un_objeto(keyword_id, "keyword_id"),
                "bid": _bid_payload(bid),
            },
        )

    def actualizar_bid_target(
        self, target_id: str | int, bid: Decimal, moneda: str
    ) -> httpx.Response:
        """PUT /sp/targets: update del bid de UN product_target."""
        self._verificar_moneda(moneda)
        return self._mutate(
            "PUT",
            "/sp/targets",
            {
                "targetId": _un_objeto(target_id, "target_id"),
                "bid": _bid_payload(bid),
            },
        )

    def pausar_keyword(self, keyword_id: str | int) -> httpx.Response:
        """PUT /sp/keywords: keyword a userPaused."""
        return self._cambiar_estado("keyword", keyword_id, "userPaused")

    def reanudar_keyword(self, keyword_id: str | int) -> httpx.Response:
        """PUT /sp/keywords: keyword a enabled (reversa del pause)."""
        return self._cambiar_estado("keyword", keyword_id, "enabled")

    def pausar_target(self, target_id: str | int) -> httpx.Response:
        """PUT /sp/targets: product_target a userPaused."""
        return self._cambiar_estado("target", target_id, "userPaused")

    def reanudar_target(self, target_id: str | int) -> httpx.Response:
        """PUT /sp/targets: product_target a enabled (reversa del pause)."""
        return self._cambiar_estado("target", target_id, "enabled")

    def crear_negative_exacto(
        self, ad_group_id: str | int, campaign_id: str | int, keyword_text: str
    ) -> httpx.Response:
        """POST /sp/negativeKeywords: negative EXACT de UN termino."""
        return self._mutate(
            "POST",
            "/sp/negativeKeywords",
            {
                "adGroupId": _un_objeto(ad_group_id, "ad_group_id"),
                "campaignId": _un_objeto(campaign_id, "campaign_id"),
                "keywordText": _un_objeto(keyword_text, "keyword_text"),
                "matchType": "exact",
            },
        )

    def borrar_negative(self, negative_id: str | int) -> httpx.Response:
        """DELETE /sp/negativeKeywords: reversa del negative."""
        return self._mutate(
            "DELETE",
            "/sp/negativeKeywords",
            {"keywordId": _un_objeto(negative_id, "negative_id")},
        )

    def crear_keyword_exacta(
        self,
        ad_group_id: str | int,
        campaign_id: str | int,
        keyword_text: str,
        bid: Decimal,
        moneda: str,
    ) -> httpx.Response:
        """POST /sp/keywords: keyword EXACT nueva (el corazon del harvest)."""
        self._verificar_moneda(moneda)
        return self._mutate(
            "POST",
            "/sp/keywords",
            {
                "adGroupId": _un_objeto(ad_group_id, "ad_group_id"),
                "campaignId": _un_objeto(campaign_id, "campaign_id"),
                "keywordText": _un_objeto(keyword_text, "keyword_text"),
                "matchType": "exact",
                "bid": _bid_payload(bid),
            },
        )

    def borrar_keyword(self, keyword_id: str | int) -> httpx.Response:
        """DELETE /sp/keywords: reversa del harvest (keyword primero)."""
        return self._mutate(
            "DELETE",
            "/sp/keywords",
            {"keywordId": _un_objeto(keyword_id, "keyword_id")},
        )

    # ------------------------------------------------------------------
    # Despacho central: guard allowlist + scope sellado + no-idempotente
    # ------------------------------------------------------------------

    def _verificar_moneda(self, moneda: str) -> None:
        """Regla 4: la moneda del payload se verifica contra la moneda de la
        plataforma ANTES del HTTP. El caller pasa `goal.bid_currency`; si no
        coincide, algo esta podrido y NO se escribe."""
        esperada = PLATAFORMA_MONEDA[self._platform]
        if moneda != esperada:
            raise MutationNotAllowedError(
                f"moneda {moneda!r} != moneda de la plataforma {self._platform} "
                f"({esperada}): no se escribe (verificar contra goal.bid_currency)"
            )

    def _cambiar_estado(self, entidad: str, entity_id: str | int, estado: str) -> httpx.Response:
        """PUT de estado (userPaused/enabled) para keyword o product_target."""
        campo = "keywordId" if entidad == "keyword" else "targetId"
        path = "/sp/keywords" if entidad == "keyword" else "/sp/targets"
        return self._mutate(
            "PUT",
            path,
            {campo: _un_objeto(entity_id, f"{entidad}_id"), "state": estado},
        )

    def _mutate(self, method: str, path: str, payload: dict) -> httpx.Response:
        """Guard central de mutaciones (dos capas, como list_objects):

        1. `_validate_relative_path` (path relativo seguro, sin traversal);
        2. par (METODO, path) por igualdad literal EXACTA contra
           `MUTATION_REQUEST_TYPES` — default-deny.

        Despacha por `_send_with_retries` directo (JAMAS por `_request`,
        cuyo guard read-only rechazaria el metodo): `idempotent=False`
        espeja a create_report — 429 reintenta, 5xx/red lanzan sin retry.
        El 401 hereda la semantica del read client: UN refresh forzado y
        re-emision (rechazado sin procesar), con headers reconstruidos con
        el MISMO vendor y el scope sellado de la instancia.
        """
        _validate_relative_path(path)
        vendor = MUTATION_REQUEST_TYPES.get((method, path))
        if vendor is None:
            raise MutationNotAllowedError(
                f"mutacion no permitida (allowlist default-deny): {method} {_clean_path(path)}"
            )

        url = f"{self._base_url}{path}"
        token = self._ensure_token()
        resp = self._send_with_retries(
            method,
            url,
            headers=self._headers_mutacion(token, vendor),
            json=payload,
            idempotent=False,
        )
        if resp.status_code == 401:
            token = self._ensure_token(force=True)
            resp = self._send_with_retries(
                method,
                url,
                headers=self._headers_mutacion(token, vendor),
                json=payload,
                idempotent=False,
            )
        if resp.status_code >= 400:
            raise AdsApiError(f"status={resp.status_code}: {method} {redact_url(url)}")
        return resp

    def _headers_mutacion(self, token: str, vendor: str) -> dict[str, str]:
        """Headers de mutacion: base autenticada del padre + scope sellado
        (via `_build_headers`) + vendor Content-Type/Accept del allowlist.
        Vive aqui para que la re-emision tras 401 tambien los lleve."""
        headers = self._build_headers(token, self._profile_id)
        headers["Content-Type"] = vendor
        headers["Accept"] = vendor
        return headers
