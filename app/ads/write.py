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
  — `app/apply.py`, `tools/smoke_apply.py` y `app/ads/archivar.py` (r2
  codex 5; archivar = limpieza operada, decision 2026-08-30).
- Errores >=400 (ORBIT 04 2.1, hallazgo r1 del brief §13): AdsApiError perdia
  el body de Amazon; el `resultado` del ledger lo heredaria. `_mutate` lanza
  `AdsApiErrorMutacion` con `cuerpo` = snippet del body JSON SANEADO
  (scrub() + ~500 chars) + status + method + path.
- `list_sellado(path, body)`: LIST v3 de READBACK con el scope SELLADO de la
  instancia — desde el probe 2.5 (2026-08-26, corrida autorizada del dueno,
  ledger apply_attempt ids 1-20, log out/smoke-apply-20260826.log) es LA
  puerta de lectura de entidad: el GET directo /sp/keywords responde 403
  (retirado como todo GET de sp; apply_attempt 4-5). `get_sellado` SOLO
  queda para el PENDIENTE-DE-REGLA-8 de bid_sugerido
  (out/regla8-bidrec.log: en vivo 403/404 → fail-open None), NUNCA para
  readback de entidad.

SELLADO por el probe 2.5 (2026-08-26): paths, vendor types, contenedores,
enums UPPER (matchType EXACT/NEGATIVE_EXACT, state ENABLED en creates), bid
como NUMERO JSON y deletes por POST /sp/{recurso}/delete con filtro de ids
— todo verificado en vivo con las 4 formas neto cero. El state del PUT de
pause/resume quedo SELLADO el 2026-08-27 (ver ESTADO_PUT_PAUSED).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from decimal import ROUND_HALF_EVEN, Decimal
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
from app.redaction import redact_url, scrub

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
# SELLADO por el probe 2.5 (2026-08-26, ledger probe ids 1-20, log
# out/smoke-apply-20260826.log): los cuatro pares de create/update
# respondieron 207 en vivo y los DELETE v3 van por POST /delete con filtro
# (el DELETE directo del collection responde 403 SigV4 — NO existe: los
# pares DELETE viejos se retiraron).
MUTATION_REQUEST_TYPES: MappingProxyType[tuple[str, str], str] = MappingProxyType(
    {
        ("PUT", "/sp/keywords"): "application/vnd.spkeyword.v3+json",
        ("PUT", "/sp/targets"): "application/vnd.sptargetingclause.v3+json",
        ("POST", "/sp/negativeKeywords"): "application/vnd.spnegativekeyword.v3+json",
        ("POST", "/sp/negativeKeywords/delete"): "application/vnd.spnegativekeyword.v3+json",
        ("POST", "/sp/keywords"): "application/vnd.spkeyword.v3+json",
        ("POST", "/sp/keywords/delete"): "application/vnd.spkeyword.v3+json",
        # SELLADO por la limpieza del 2026-08-30 (ver archivar_product_ad).
        ("POST", "/sp/productAds/delete"): "application/vnd.spproductad.v3+json",
        # La REVERSA del archivado (decision del dueno 2026-08-30, invariante
        # 7). PENDIENTE-DE-SONDA: ver crear_product_ad.
        ("POST", "/sp/productAds"): "application/vnd.spproductad.v3+json",
    }
)

# Contenedor del RECURSO que la API v3 exige en el body de las mutaciones de
# coleccion: el objeto viaja como UNICA entrada de una lista bajo esta clave
# (el sello "payload de UN objeto" se mantiene: una entrada, jamas un lote).
# SELLADO por el probe 2.5 (2026-08-26, corrida autorizada del dueno, ledger
# probe ids 1-20, log out/smoke-apply-20260826.log): los TRES contenedores
# respondieron 207 en vivo — keywords (apply_attempt 6-7), targetingClauses
# (19-20; el MISMO del list) y negativeKeywords (13-14).
MUTATION_CONTAINERS: MappingProxyType[str, str] = MappingProxyType(
    {
        "/sp/keywords": "keywords",
        "/sp/targets": "targetingClauses",
        "/sp/negativeKeywords": "negativeKeywords",
        # Misma clave que el list de product ads (_CLAVE_CONTENEDORA de
        # app/ads/structure.py, verificada en vivo desde 2026-08-31).
        "/sp/productAds": "productAds",
    }
)

# SELLADO el 2026-08-27 por la corrida de reactivacion (autorizada por el
# dueno, evidencia out/reactiva-campanas-20260827.log): el state del REQUEST
# en el PUT v3 de pause/resume es UPPER — 'paused' minuscula respondio 400
# con el enum exacto [ENABLED, PROPOSED, PAUSED] y 'PAUSED' respondio 207
# success con readback PAUSED (la hipotesis 'userPaused'/'enabled' quedo
# REFUTADA). El READBACK compara contra el wire VERIFICADO del list
# (ESTADO_WIRE_* de app/apply.py: ENABLED/PAUSED/ARCHIVED UPPER).
ESTADO_PUT_PAUSED = "PAUSED"
ESTADO_PUT_ENABLED = "ENABLED"
# Mismo enum UPPER del PUT v3 (sello 2026-08-27: [ENABLED, PROPOSED, PAUSED]).
ESTADO_PUT_PROPOSED = "PROPOSED"
# Create de product ad: solo estados vivos del wire; ARCHIVED no se crea.
ESTADOS_CREATE_PRODUCT_AD = frozenset({ESTADO_PUT_ENABLED, ESTADO_PUT_PAUSED, ESTADO_PUT_PROPOSED})


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


def _bid_wire(bid: Decimal) -> float:
    """Bid para el PAYLOAD JSON: NUMERO, no string (evidencia probe 2.5,
    apply_attempt id=3: "STRING_VALUE is not an expected Json type"). La
    regla 4 (jamás float) gobierna el ALMACENAMIENTO y la aritmética de
    decisiones — este es el encoding FINAL del valor YA cuantizado a 2
    decimales: float(Decimal("0.76")) serializa como 0.76 exacto (repr de
    round-trip), sin inventar precisión."""
    return float(_bid_payload(bid))


def _bid_payload(bid: Decimal) -> str:
    """Bid quantizado a 2 decimales (presentacion sellada; el NUMERIC de
    origen trae 4) y serializado como string: float prohibido para dinero
    (regla 4) y json.dumps no serializa Decimal. El modo de redondeo va
    EXPLICITO (hallazgo reviewer r1): ROUND_HALF_EVEN, el default del
    contexto Decimal — declararlo evita que el modo dependa de un contexto
    global que nadie mira; cambiarlo es decision del dueno en el probe 2.5."""
    if not isinstance(bid, Decimal):
        raise TypeError(
            f"bid debe ser Decimal, no {type(bid).__name__} (float prohibido para dinero)"
        )
    return str(bid.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))


class AdsApiErrorMutacion(AdsApiError):
    """La API rechazo una mutacion (>=400) y el RECHAZO trae cuerpo.

    Hoy AdsApiError pierde el body (redaccion del read client: solo status +
    metodo + path) y el `resultado` del ledger heredaria esa perdida (r1 del
    brief §13). `cuerpo` lleva un snippet del body JSON SANEADO (scrub: los
    errores de Amazon pueden ecoar tokens) y truncado a ~500 chars — evidencia
    para el ledger, no un volcado."""

    def __init__(
        self,
        message: str,
        *,
        cuerpo: str,
        status: int,
        method: str,
        path: str,
    ) -> None:
        super().__init__(message)
        self.cuerpo = scrub(cuerpo)
        self.status = status
        self.method = method
        self.path = path


def _snippet_cuerpo(resp: httpx.Response, tope: int = 500) -> str:
    """Snippet saneado del body de una respuesta de error: JSON compacto si
    parsea, texto crudo si no; scrub() SIEMPRE (defensa en profundidad: un
    body puede ecoar credenciales) y truncado a `tope` chars."""
    try:
        cuerpo = json.dumps(resp.json(), ensure_ascii=False)
    except ValueError:
        cuerpo = resp.text
    return scrub(cuerpo)[:tope]


class AdsWriteClient(AdsClient):
    """Cliente de escritura Amazon Ads: SOLO las mutaciones del allowlist.

    La superficie publica es EXACTA (las 10 mutaciones selladas + las dos
    puertas de lectura sellada: list_sellado para el readback de entidad y
    get_sellado solo para el PENDIENTE-DE-REGLA-8; ningun metodo generico
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
                "bid": _bid_wire(bid),
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
                "bid": _bid_wire(bid),
            },
        )

    def pausar_keyword(self, keyword_id: str | int) -> httpx.Response:
        """PUT /sp/keywords: keyword a PAUSED (sello 2026-08-27 del PUT: ver
        ESTADO_PUT_PAUSED)."""
        return self._cambiar_estado("keyword", keyword_id, ESTADO_PUT_PAUSED)

    def reanudar_keyword(self, keyword_id: str | int) -> httpx.Response:
        """PUT /sp/keywords: keyword a ENABLED (reversa del pause; sello
        2026-08-27 del PUT: ver ESTADO_PUT_ENABLED)."""
        return self._cambiar_estado("keyword", keyword_id, ESTADO_PUT_ENABLED)

    def pausar_target(self, target_id: str | int) -> httpx.Response:
        """PUT /sp/targets: product_target a PAUSED (sello 2026-08-27 del
        PUT: ver ESTADO_PUT_PAUSED)."""
        return self._cambiar_estado("target", target_id, ESTADO_PUT_PAUSED)

    def reanudar_target(self, target_id: str | int) -> httpx.Response:
        """PUT /sp/targets: product_target a ENABLED (reversa del pause;
        sello 2026-08-27 del PUT: ver ESTADO_PUT_ENABLED)."""
        return self._cambiar_estado("target", target_id, ESTADO_PUT_ENABLED)

    def crear_negative_exacto(
        self, ad_group_id: str | int, campaign_id: str | int, keyword_text: str
    ) -> httpx.Response:
        """POST /sp/negativeKeywords: negative EXACT de UN termino.

        Shapes fijados por el probe 2.5 (apply_attempt id=8): matchType es
        el enum NEGATIVE_* (NEGATIVE_EXACT — NO el 'exact' de las keywords)
        y `state` es OBLIGATORIO en el POST (enum UPPER: ENABLED/PROPOSED/PAUSED)."""
        return self._mutate(
            "POST",
            "/sp/negativeKeywords",
            {
                "adGroupId": _un_objeto(ad_group_id, "ad_group_id"),
                "campaignId": _un_objeto(campaign_id, "campaign_id"),
                "keywordText": _un_objeto(keyword_text, "keyword_text"),
                "matchType": "NEGATIVE_EXACT",
                "state": "ENABLED",  # enum UPPER (apply_attempt 9: ENABLED/PROPOSED/PAUSED)
            },
        )

    def borrar_negative(self, negative_id: str | int) -> httpx.Response:
        """POST /sp/negativeKeywords/delete: reversa del negative.

        Shape REAL del probe 2.5: el DELETE directo del collection responde
        403 (superficie que exige firma AWS); el camino v3 es POST al
        sub-path /delete con un FILTRO de ids ({"negativeKeywordIdFilter":
        {"include": [id]}} — 207 success, verificado en vivo). El "delete"
        de Amazon ARCHIVA (state=ARCHIVED): operativamente muerto, la fila
        sigue apareciendo en el list con ese estado."""
        return self._mutate(
            "POST",
            "/sp/negativeKeywords/delete",
            {"negativeKeywordIdFilter": {"include": [_un_objeto(negative_id, "negative_id")]}},
            envolver=False,
        )

    def crear_keyword_exacta(
        self,
        ad_group_id: str | int,
        campaign_id: str | int,
        keyword_text: str,
        bid: Decimal,
        moneda: str,
    ) -> httpx.Response:
        """POST /sp/keywords: keyword EXACT nueva (el corazon del harvest).

        Shapes del probe 2.5 (apply_attempt 15): matchType enum UPPER
        (EXACT/PHRASE/BROAD) y state OBLIGATORIO (ENABLED)."""
        self._verificar_moneda(moneda)
        return self._mutate(
            "POST",
            "/sp/keywords",
            {
                "adGroupId": _un_objeto(ad_group_id, "ad_group_id"),
                "campaignId": _un_objeto(campaign_id, "campaign_id"),
                "keywordText": _un_objeto(keyword_text, "keyword_text"),
                "matchType": "EXACT",
                "state": "ENABLED",
                "bid": _bid_wire(bid),
            },
        )

    def borrar_keyword(self, keyword_id: str | int) -> httpx.Response:
        """POST /sp/keywords/delete: reversa del harvest (keyword primero).

        Shape simetrico al del negative (probe 2.5): POST /sp/keywords/delete
        con {"keywordIdFilter": {"include": [id]}}; el DELETE directo del
        collection no existe en la superficie Bearer (403 SigV4)."""
        return self._mutate(
            "POST",
            "/sp/keywords/delete",
            {"keywordIdFilter": {"include": [_un_objeto(keyword_id, "keyword_id")]}},
            envolver=False,
        )

    def crear_product_ad(
        self, ad_group_id: str | int, campaign_id: str | int, sku: str, estado: str
    ) -> httpx.Response:
        """POST /sp/productAds: LA REVERSA del archivado (invariante 7).

        Existe porque archivar NO se deshace: Amazon no des-archiva, asi que
        la unica vuelta atras es volver a crear el anuncio en su mismo ad
        group. Decision del dueno del 2026-08-30, tomada a sabiendas de que
        crear un anuncio habilita GASTO — el mismo precio que ya paga el
        harvest con crear_keyword_exacta.

        `sku` y no `asin` A PROPOSITO: la guia oficial de Sponsored Products
        pide ASIN para vendors/KDP y SKU para SELLERS, y el gate de perfiles
        de este repo solo acepta cuentas seller (accountInfo.type). El sku
        viaja en el mismo payload del list del que salio el anuncio.

        `estado` restaura el que tenia ANTES de archivarse (UPPER, mismo
        vocabulario que el resto): una reversa que revive en ENABLED algo
        que estaba PAUSED no es una reversa, es un cambio.

        PENDIENTE-DE-SONDA: a diferencia del delete —sellado en vivo por la
        corrida del 2026-08-30— este create todavia NO se probo contra
        Amazon. La sonda segura es un sku INEXISTENTE: si el shape es
        correcto Amazon rechaza por-item y no crea nada, y si alguna clave
        estuviera mal (esta API las ignora en silencio) el payload queda sin
        producto y tambien tiene que rechazar. Un SUCCESS ahi seria la
        senal de alarma.
        """
        estado_wire = _un_objeto(estado, "estado")
        if estado_wire not in ESTADOS_CREATE_PRODUCT_AD:
            raise ValueError(
                f"estado de create debe ser UPPER wire "
                f"{sorted(ESTADOS_CREATE_PRODUCT_AD)}; llego {estado_wire!r}"
            )
        return self._mutate(
            "POST",
            "/sp/productAds",
            {
                "adGroupId": _un_objeto(ad_group_id, "ad_group_id"),
                "campaignId": _un_objeto(campaign_id, "campaign_id"),
                "sku": _un_objeto(sku, "sku"),
                "state": estado_wire,
            },
        )

    def archivar_product_ad(self, ad_id: str | int) -> httpx.Response:
        """POST /sp/productAds/delete: saca de circulacion UN anuncio.

        SELLADO EN VIVO por la limpieza del 2026-08-30 (perfil amazon_mx,
        corrida autorizada por el dueno con el ensayo a la vista): 203
        anuncios archivados, los 203 confirmados ARCHIVED por readback, cero
        fallos. El vendor Content-Type de arriba fue aceptado (ningun 415) y
        el filtro `adIdFilter` se HONRO — no es solo el nombre del openapi.

        CERO COLATERAL, y esto es lo que lo prueba (la pregunta de la
        cross-review: el readback solo relee los ids pedidos, asi que no
        veria un archivado de mas). El sync de estructura siguiente recorre
        la cuenta ENTERA y movio TRES contadores independientes por
        exactamente 203 -- escritas 18.426 -> 18.223, archivados saltados
        25.454 -> 25.657, "sin listing" 3.901 -> 3.698 -- mientras "con
        listing" quedo clavado en 8.626. Si el filtro se hubiera ignorado
        habrian caido miles; y ni un solo anuncio CON producto mapeado fue
        tocado.

        El id del filtro es `adId` — asi lo devuelve el list de product ads.
        OJO: otra API de Amazon (Retail Ad Service, DELETE /productAds) borra
        product ads con `productAdIdFilter`, y esta API IGNORA EN SILENCIO
        los filtros que no reconoce: la clave equivocada viaja SIN FILTRO.
        Clavado en tests/test_ads_write.py.

        "Borrar" en Amazon ARCHIVA (state=ARCHIVED): el anuncio queda
        operativamente muerto y su fila sigue saliendo en el list con ese
        estado. NO HAY REVERSA — no existe des-archivar.
        """
        return self._mutate(
            "POST",
            "/sp/productAds/delete",
            {"adIdFilter": {"include": [_un_objeto(ad_id, "ad_id")]}},
            envolver=False,
        )

    def get_sellado(self, path: str, *, params: dict | None = None) -> httpx.Response:
        """GET con el scope SELLADO de la instancia.

        El re-check "ya estaba" del aplicador JAMAS pasa un profile a mano
        (hallazgo r1 del brief §13: los metodos de lectura heredados aceptan
        profile_id arbitrario — un readback con scope de otra plataforma
        validaria la cuenta equivocada). Desde el probe 2.5 (apply_attempt
        4-5) NO sirve readback de entidad sp: el GET directo responde 403
        (retirado). Su UNICO caller es `bid_sugerido` (PENDIENTE-DE-REGLA-8,
        out/regla8-bidrec.log: en vivo 403/404 → None fail-open)."""
        return self._request("GET", path, params=params, profile_id=self._profile_id)

    def list_sellado(self, path: str, body: dict) -> httpx.Response:
        """LIST v3 de READBACK con el scope SELLADO de la instancia.

        Desde el probe 2.5 (2026-08-26, apply_attempt 4-5) es LA puerta de
        lectura de entidad del motor: el GET directo /sp/keywords responde
        403 (retirado) y el UNICO camino de lectura es el POST de lista (el
        mismo del sync de estructura, verificado en vivo desde 2026-08-22).
        `body` es el filtro de paginacion ({} primera pagina). Misma regla
        que get_sellado: el profile JAMAS viaja a mano — vive en la instancia."""
        return self.list_objects(path, body, profile_id=self._profile_id)

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
        """PUT de estado (PAUSED/ENABLED, sello 2026-08-27) para keyword o product_target."""
        campo = "keywordId" if entidad == "keyword" else "targetId"
        path = "/sp/keywords" if entidad == "keyword" else "/sp/targets"
        return self._mutate(
            "PUT",
            path,
            {campo: _un_objeto(entity_id, f"{entidad}_id"), "state": estado},
        )

    def _mutate(
        self, method: str, path: str, payload: dict, *, envolver: bool = True
    ) -> httpx.Response:
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
        # El objeto viaja como UNICA entrada de la lista bajo el contenedor
        # del recurso (evidencia probe 2.5 arriba): objeto desnudo = 400
        # INVALID_ARGUMENT "Member must not be null". Los DELETE v3 van por
        # POST /delete con body de FILTRO (envolver=False): shape propio.
        body = payload if not envolver else {MUTATION_CONTAINERS[path]: [payload]}
        token = self._ensure_token()
        resp = self._send_with_retries(
            method,
            url,
            headers=self._headers_mutacion(token, vendor),
            json=body,
            idempotent=False,
        )
        if resp.status_code == 401:
            token = self._ensure_token(force=True)
            resp = self._send_with_retries(
                method,
                url,
                headers=self._headers_mutacion(token, vendor),
                json=body,
                idempotent=False,
            )
        if resp.status_code >= 400:
            # El rechazo determinista (>=400) lleva su cuerpo: el ledger del
            # aplicador conserva la RAZON de Amazon (AdsApiError la perdia).
            raise AdsApiErrorMutacion(
                f"status={resp.status_code}: {method} {redact_url(url)}",
                cuerpo=_snippet_cuerpo(resp),
                status=resp.status_code,
                method=method,
                path=_clean_path(path),
            )
        return resp

    def _headers_mutacion(self, token: str, vendor: str) -> dict[str, str]:
        """Headers de mutacion: base autenticada del padre + scope sellado
        (via `_build_headers`) + vendor Content-Type/Accept del allowlist.
        Vive aqui para que la re-emision tras 401 tambien los lleve."""
        headers = self._build_headers(token, self._profile_id)
        headers["Content-Type"] = vendor
        headers["Accept"] = vendor
        return headers
