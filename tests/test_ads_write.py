"""Tests del cliente de ESCRITURA Amazon Ads (`app.ads.write`) — ORBIT 04 1.3.

Transporte de la API de Ads 100% mock (`httpx.MockTransport`): a Amazon no
sale ninguna llamada real. Los paths/vendor types/containers quedaron
SELLADOS por el probe 2.5 (corrida autorizada del dueno 2026-08-26, ledger
apply_attempt ids 1-20, log out/smoke-apply-20260826.log). Los "secretos"
son SIEMPRE valores falsos (`fake-...`).

Cubre el DoD 1.3 (decision 9 sellada):
- constructor fail-closed: `modo_confirmado` debe ser EXACTAMENTE 'live'
  (shadow/off/None revientan ANTES de construir nada) y es keyword-only SIN
  default (falta -> TypeError);
- `platform` vocabulario cerrado {amazon_us, amazon_mx}: otra cosa ->
  ValueError ruidoso;
- allowlist default-deny `MUTATION_REQUEST_TYPES`: par (METODO, path) por
  igualdad literal EXACTA; metodo correcto con path inexistente, path
  correcto con metodo torcido y GET sobre un path de mutacion revientan
  SIN emitir ni un request (ni siquiera el token LWA);
- scope SELLADO a la instancia: toda mutacion sale con
  Amazon-Advertising-API-Scope del profile del constructor; ningun metodo
  publico acepta profile/platform/scope (fijado con inspect) ni **kwargs;
- payload de UN objeto: keyword_id como lista/dict -> ValueError antes de
  tocar la red;
- presentacion sellada: bid Decimal quantizado a 2 decimales (0.7550 ->
  0.76) y moneda verificada contra PLATAFORMA_MONEDA[platform] ANTES del
  HTTP (regla 4: transport espia cuenta 0 requests);
- retries espejo de create_report (sellado 8): 429 reintenta (2 requests, 1
  resultado), 5xx y fallo de red lanzan tras 1 request SIN reintento;
- 401 -> UN refresh forzado -> re-emision con el MISMO scope/vendor;
- superficie publica EXACTA (10 metodos, nada generico request/post).
"""

from __future__ import annotations

import inspect
import json
from decimal import Decimal

import httpx
import pytest

from app.ads.client import AdsApiError, AdsClient, MutationNotAllowedError
from app.ads.config import AdsCredentials
from app.ads.write import (
    MUTATION_CONTAINERS,
    MUTATION_REQUEST_TYPES,
    PLATAFORMA_MONEDA,
    AdsWriteClient,
)

FAKE_CLIENT_ID = "fake-client-id-123"
FAKE_CLIENT_SECRET = "fake-client-secret-XYZ"
FAKE_REFRESH_TOKEN = "fake-refresh-token-ABC"

FAKE_PROFILE_MX = 303030
FAKE_PROFILE_US = 404040


def _fake_credentials() -> AdsCredentials:
    return AdsCredentials(
        client_id=FAKE_CLIENT_ID,
        client_secret=FAKE_CLIENT_SECRET,
        refresh_token=FAKE_REFRESH_TOKEN,
    )


def _token_response(n: int = 1, *, expires_in: int = 3600) -> httpx.Response:
    return httpx.Response(200, json={"access_token": f"fake-access-{n}", "expires_in": expires_in})


def make_write_client(
    handler,
    *,
    platform: str = "amazon_mx",
    profile_id: str | int = FAKE_PROFILE_MX,
    modo_confirmado: str = "live",
    sleep=None,
) -> AdsWriteClient:
    transport = httpx.MockTransport(handler)
    kwargs: dict = {"sleep": sleep if sleep is not None else (lambda seconds: None)}
    return AdsWriteClient(
        _fake_credentials(),
        platform=platform,
        profile_id=profile_id,
        modo_confirmado=modo_confirmado,
        transport=transport,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# 1. Constructor fail-closed: modo y plataforma
# ---------------------------------------------------------------------------


def test_modo_confirmado_distinto_de_live_revierta_antes_de_construir():
    """La escalera se re-resuelve POR DECISION en el caller ANTES de
    construir: cualquier modo que no sea exactamente 'live' revienta
    fail-closed (shadow NO escribe, off tampoco, None tampoco)."""
    for modo in ("shadow", "off", "live ", "LIVE", "", None):
        with pytest.raises(MutationNotAllowedError):
            make_write_client(lambda request: httpx.Response(200), modo_confirmado=modo)


def test_modo_confirmado_es_obligatorio_sin_default():
    """Falta `modo_confirmado` -> TypeError: no existe default que pueda
    'decidir' por el caller (ni None ni 'shadow')."""
    with pytest.raises(TypeError):
        AdsWriteClient(
            _fake_credentials(),
            platform="amazon_mx",
            profile_id=FAKE_PROFILE_MX,
            transport=httpx.MockTransport(lambda request: httpx.Response(200)),
        )


def test_plataforma_vocabulario_cerrado():
    """Solo {amazon_us, amazon_mx}: dos construcciones sanas y ValueError
    ruidoso para cualquier otra cosa."""
    assert PLATAFORMA_MONEDA == {"amazon_us": "USD", "amazon_mx": "MXN"}
    handler = lambda request: httpx.Response(200)  # noqa: E731
    for platform in ("amazon_us", "amazon_mx"):
        client = make_write_client(handler, platform=platform)
        assert isinstance(client, AdsClient), "subclase del read client"
    for platform in ("meli", "amazon_es", "", None, "AMAZON_MX"):
        with pytest.raises(ValueError):
            make_write_client(handler, platform=platform)


# ---------------------------------------------------------------------------
# 2. Superficie publica EXACTA y scope inexpresable
# ---------------------------------------------------------------------------

# Las 10 MUTACIONES selladas + las DOS puertas de lectura sellada (ORBIT 04
# 2.1/probe 2.5: list_sellado es EL readback de entidad — el GET directo sp
# esta retirado, 403; get_sellado queda SOLO para el PENDIENTE-DE-REGLA-8 de
# bid_sugerido). Ningun metodo pasa un profile a mano.
METODOS_PUBLICOS = {
    "actualizar_bid_keyword",
    "actualizar_bid_target",
    "pausar_keyword",
    "reanudar_keyword",
    "pausar_target",
    "reanudar_target",
    "crear_negative_exacto",
    "borrar_negative",
    "crear_keyword_exacta",
    "borrar_keyword",
    "archivar_product_ad",
    "get_sellado",
    "list_sellado",
}


def test_superficie_publica_exacta():
    """Las 11 mutaciones selladas + list_sellado (readback de entidad, probe
    2.5) + get_sellado (solo bidrec PENDIENTE-DE-REGLA-8), NADA mas; ningun
    metodo generico request/post y ninguna ampliacion silenciosa (vars() de
    la clase: lo heredado del read client no se re-sella aqui)."""
    public = {
        name
        for name, value in vars(AdsWriteClient).items()
        if not name.startswith("_") and callable(value)
    }
    assert public == METODOS_PUBLICOS


def test_ningun_metodo_publico_acepta_profile_platform_ni_scope():
    """El scope esta SELLADO al constructor: un metodo que aceptara
    profile_id/platform/scope (o **kwargs para colarlos) romperia este
    assert — un apply MX con scope US escribe en la cuenta equivocada
    (r2 grok 18) y debe ser INEXPRESABLE en la superficie publica."""
    for name in METODOS_PUBLICOS:
        firma = inspect.signature(getattr(AdsWriteClient, name))
        for prohibido in ("profile_id", "profile", "platform", "scope"):
            assert prohibido not in firma.parameters, f"{name} no debe aceptar {prohibido}"
        assert not any(
            p.kind is inspect.Parameter.VAR_KEYWORD for p in firma.parameters.values()
        ), f"{name} no debe aceptar **kwargs: colaria un scope ajeno"


# ---------------------------------------------------------------------------
# 3. Cada mutacion: method + path + payload de UN objeto + vendor + scope
# ---------------------------------------------------------------------------

CASOS_MUTACION = [
    pytest.param(
        "actualizar_bid_keyword",
        (101, Decimal("0.7550"), "MXN"),
        "PUT",
        "/sp/keywords",
        "application/vnd.spkeyword.v3+json",
        {"keywordId": 101, "bid": 0.76},
        id="bid-keyword",
    ),
    pytest.param(
        "actualizar_bid_target",
        (202, Decimal("1.005"), "MXN"),
        "PUT",
        "/sp/targets",
        "application/vnd.sptargetingclause.v3+json",
        {"targetId": 202, "bid": 1.0},
        id="bid-target",
    ),
    pytest.param(
        "pausar_keyword",
        (101,),
        "PUT",
        "/sp/keywords",
        "application/vnd.spkeyword.v3+json",
        {"keywordId": 101, "state": "PAUSED"},
        id="pausar-keyword",
    ),
    pytest.param(
        "reanudar_keyword",
        (101,),
        "PUT",
        "/sp/keywords",
        "application/vnd.spkeyword.v3+json",
        {"keywordId": 101, "state": "ENABLED"},
        id="reanudar-keyword",
    ),
    pytest.param(
        "pausar_target",
        (202,),
        "PUT",
        "/sp/targets",
        "application/vnd.sptargetingclause.v3+json",
        {"targetId": 202, "state": "PAUSED"},
        id="pausar-target",
    ),
    pytest.param(
        "reanudar_target",
        (202,),
        "PUT",
        "/sp/targets",
        "application/vnd.sptargetingclause.v3+json",
        {"targetId": 202, "state": "ENABLED"},
        id="reanudar-target",
    ),
    pytest.param(
        "crear_negative_exacto",
        (31, 21, "zapato roto"),
        "POST",
        "/sp/negativeKeywords",
        "application/vnd.spnegativekeyword.v3+json",
        {
            "adGroupId": 31,
            "campaignId": 21,
            "keywordText": "zapato roto",
            "matchType": "NEGATIVE_EXACT",
            "state": "ENABLED",
        },
        id="crear-negative",
    ),
    pytest.param(
        "borrar_negative",
        (99,),
        "POST",
        "/sp/negativeKeywords/delete",
        "application/vnd.spnegativekeyword.v3+json",
        {"negativeKeywordIdFilter": {"include": [99]}},
        id="borrar-negative",
    ),
    pytest.param(
        "crear_keyword_exacta",
        (31, 21, "zapato bueno", Decimal("10"), "MXN"),
        "POST",
        "/sp/keywords",
        "application/vnd.spkeyword.v3+json",
        {
            "adGroupId": 31,
            "campaignId": 21,
            "keywordText": "zapato bueno",
            "matchType": "EXACT",
            "state": "ENABLED",
            "bid": 10.0,
        },
        id="crear-keyword",
    ),
    pytest.param(
        "borrar_keyword",
        (101,),
        "POST",
        "/sp/keywords/delete",
        "application/vnd.spkeyword.v3+json",
        {"keywordIdFilter": {"include": [101]}},
        id="borrar-keyword",
    ),
    pytest.param(
        "archivar_product_ad",
        (7001,),
        "POST",
        "/sp/productAds/delete",
        "application/vnd.spproductad.v3+json",
        {"adIdFilter": {"include": [7001]}},
        id="archivar-product-ad",
    ),
]


@pytest.mark.parametrize("metodo,args,method,path,vendor,payload", CASOS_MUTACION)
def test_mutacion_envia_method_path_payload_vendor_y_scope_sellados(
    metodo, args, method, path, vendor, payload
):
    llamadas: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_response()
        llamadas.append(request)
        return httpx.Response(200, json={"ack": "ok"})

    client = make_write_client(handler)
    resp = getattr(client, metodo)(*args)

    assert resp.status_code == 200
    assert len(llamadas) == 1, "una mutacion = UN request"
    enviado = llamadas[0]
    assert enviado.method == method
    assert enviado.url.path == path
    assert enviado.headers["Content-Type"] == vendor
    assert enviado.headers["Accept"] == vendor
    assert enviado.headers["Amazon-Advertising-API-Scope"] == str(FAKE_PROFILE_MX)
    # Evidencia probe 2.5 (2026-08-26): la API v3 exige el objeto como
    # UNICA entrada de la lista bajo el contenedor del recurso (PUT con
    # objeto desnudo -> 400 "Value null at 'keywords'") — EXCEPTO los
    # DELETE v3, que van por POST /delete con body de FILTRO sin contenedor.
    if path.endswith("/delete"):
        assert json.loads(enviado.content) == payload
    else:
        assert json.loads(enviado.content) == {MUTATION_CONTAINERS[path]: [payload]}


def test_bid_quantizado_a_dos_decimales_en_el_body():
    """0.7550 -> 0.76 en el body: sin quantize el payload llevaria los 4
    decimales del NUMERIC de origen (presentacion sellada). El bid viaja
    como numero JSON tras cuantizar el Decimal (regla 4)."""
    bodies: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_response()
        bodies.append(request.content)
        return httpx.Response(200, json={"ack": "ok"})

    client = make_write_client(handler)
    client.actualizar_bid_keyword(101, Decimal("0.7550"), "MXN")

    body = json.loads(bodies[0])
    assert body == {"keywords": [{"keywordId": 101, "bid": 0.76}]}


def test_bid_que_no_es_decimal_revierta_typeerror():
    """Un float en el bid es la violacion clasica de la regla 4: TypeError
    ruidoso, no un AttributeError tarde."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("la validacion es ANTES del HTTP: 0 requests")

    client = make_write_client(handler)
    with pytest.raises(TypeError):
        client.actualizar_bid_keyword(101, 0.755, "MXN")


def test_payload_multi_objeto_rechazado():
    """Payload de UN objeto sellado: un parametro multi (lista/dict) revienta
    ValueError ANTES de armar el payload — sin HTTP."""
    requests_vistos: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_vistos.append(request)
        raise AssertionError("jamas debe salir a la red")

    client = make_write_client(handler)
    with pytest.raises(ValueError, match="unico objeto"):
        client.actualizar_bid_keyword([101, 102], Decimal("0.5"), "MXN")
    with pytest.raises(ValueError, match="unico objeto"):
        client.crear_keyword_exacta(31, 21, {"a": 1}, Decimal("1"), "MXN")
    with pytest.raises(ValueError, match="unico objeto"):
        client.pausar_keyword((101, 102))
    assert requests_vistos == []


# ---------------------------------------------------------------------------
# 4. Allowlist default-deny: pares fuera del allowlist, cero HTTP
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method,path",
    [
        ("PUT", "/sp/keywordsx"),  # metodo correcto, path inexistente
        ("PATCH", "/sp/keywords"),  # path correcto, metodo torcido
        ("POST", "/sp/targets"),  # /sp/targets solo PUT en el allowlist
        ("GET", "/sp/keywords"),  # GET sobre un path de mutacion
        ("DELETE", "/sp/campaigns"),  # par cualquiera fuera
        ("PUT", "/sp/keywords?x=1"),  # igualdad literal: query rompe el match
        ("POST", "/sp/negativeKeywords/list"),  # path de LECTURA: en write no
    ],
)
def test_par_fuera_del_allowlist_rechazado_sin_http(method, path):
    """Default-deny de DOS capas como list_objects: el par (METODO, path)
    debe ser EXACTAMENTE una entrada del allowlist. El rechazo ocurre antes
    del token LWA: cero requests en el transporte espia."""
    requests_vistos: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_vistos.append(request)
        raise AssertionError("jamas debe salir a la red")

    client = make_write_client(handler)
    with pytest.raises(MutationNotAllowedError):
        client._mutate(method, path, {"keywordId": 1})
    assert requests_vistos == []


def test_allowlist_sellada_contenido_y_congelamiento():
    """El allowlist ES la superficie de mutacion: contenido exacto y
    MappingProxyType congelado (una mutacion accidental del mapa revienta)."""
    # Contenido sellado tras el probe 2.5: los DELETE v3 son POST /delete
    # (el DELETE directo del collection responde 403 SigV4 — retirado).
    assert set(MUTATION_REQUEST_TYPES) == {
        ("PUT", "/sp/keywords"),
        ("PUT", "/sp/targets"),
        ("POST", "/sp/negativeKeywords"),
        ("POST", "/sp/negativeKeywords/delete"),
        ("POST", "/sp/keywords"),
        ("POST", "/sp/keywords/delete"),
        ("POST", "/sp/productAds/delete"),
    }
    assert MUTATION_REQUEST_TYPES[("PUT", "/sp/keywords")] == ("application/vnd.spkeyword.v3+json")
    assert MUTATION_REQUEST_TYPES[("POST", "/sp/negativeKeywords/delete")] == (
        "application/vnd.spnegativekeyword.v3+json"
    )
    assert MUTATION_REQUEST_TYPES[("POST", "/sp/productAds/delete")] == (
        "application/vnd.spproductad.v3+json"
    )
    with pytest.raises(TypeError):
        MUTATION_REQUEST_TYPES[("POST", "/sp/campaigns")] = "application/vnd.sp.v3+json"


def test_el_filtro_del_archivado_de_product_ads_se_llama_exactamente_adIdFilter():
    """El nombre del filtro NO es cosmetico: es la diferencia entre archivar
    UN anuncio y archivar la cuenta entera.

    Sonda de lectura del 2026-08-30 (POST /sp/productAds/list en vivo, perfil
    amazon_mx): con `adIdFilter` la API devolvio EXACTAMENTE el anuncio
    pedido; con una clave inventada devolvio 1000 (la pagina completa). O
    sea: Amazon IGNORA EN SILENCIO los filtros que no reconoce, no los
    rechaza. Un delete con la clave equivocada viaja SIN FILTRO.

    Y la clave equivocada es facil de agarrar: otra API de Amazon (Retail Ad
    Service, DELETE /productAds) borra product ads con `productAdIdFilter`.
    Para /sp/productAds/delete el openapi oficial exige `adIdFilter`.

    Este test falla si alguien renombra el filtro, y falla si el filtro
    llegara vacio."""
    enviados: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_response()
        enviados.append(request)
        return httpx.Response(207, json={"productAds": {"success": []}})

    make_write_client(handler).archivar_product_ad(7001)

    cuerpo = json.loads(enviados[-1].content)
    assert set(cuerpo) == {"adIdFilter"}, (
        f"el body debe llevar SOLO adIdFilter (llego {sorted(cuerpo)}): una clave "
        "que Amazon no reconoce se ignora y el borrado queda SIN filtro"
    )
    assert cuerpo["adIdFilter"]["include"], "un include vacio es un borrado sin filtro"


# ---------------------------------------------------------------------------
# 5. Moneda verificada ANTES del HTTP (regla 4)
# ---------------------------------------------------------------------------


def test_moneda_equivocada_revierta_antes_de_cualquier_http():
    """El caller pasa goal.bid_currency: si no coincide con la moneda de la
    plataforma, algo esta podrido y NO se escribe. Transport espia: cuenta
    0 requests (ni token LWA)."""
    requests_vistos: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_vistos.append(request)
        raise AssertionError("jamas debe salir a la red")

    client_mx = make_write_client(handler)  # amazon_mx -> MXN
    with pytest.raises(MutationNotAllowedError, match="MXN"):
        client_mx.actualizar_bid_keyword(101, Decimal("0.5"), "USD")

    client_us = make_write_client(handler, platform="amazon_us", profile_id=FAKE_PROFILE_US)
    with pytest.raises(MutationNotAllowedError, match="USD"):
        client_us.actualizar_bid_target(202, Decimal("0.5"), "MXN")
    with pytest.raises(MutationNotAllowedError, match="USD"):
        client_us.crear_keyword_exacta(31, 21, "x", Decimal("1"), "MXN")

    assert requests_vistos == []


def test_scope_sellado_header_del_profile_de_la_instancia():
    """TODA mutacion sale con el Scope del constructor (aqui amazon_mx con
    profile int): el header viaja en todas y cada una."""
    scopes: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_response()
        scopes.append(request.headers["Amazon-Advertising-API-Scope"])
        return httpx.Response(200, json={"ack": "ok"})

    client = make_write_client(handler)
    client.pausar_keyword(101)
    client.borrar_negative(99)
    assert scopes == [str(FAKE_PROFILE_MX), str(FAKE_PROFILE_MX)]


# ---------------------------------------------------------------------------
# 6. Retries: espejo de create_report (sellado 8)
# ---------------------------------------------------------------------------


def test_429_reintenta_hasta_exito():
    """429 = rechazado SIN procesar: reintenta (2 requests, 1 resultado)."""
    api_calls: list[httpx.Request] = []
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_response()
        api_calls.append(request)
        if len(api_calls) == 1:
            return httpx.Response(429, json={"error": "throttled"})
        return httpx.Response(200, json={"ack": "ok"})

    client = make_write_client(handler, sleep=sleeps.append)
    resp = client.pausar_keyword(101)

    assert resp.status_code == 200
    assert len(api_calls) == 2
    assert len(sleeps) == 1


def test_500_lanza_tras_un_request_sin_reintentar():
    """5xx = resultado AMBIGUO con un PUT ya emitido: fail-closed, lanza tras
    1 request SIN reintento (reintentar podria duplicar la mutacion)."""
    api_calls: list[httpx.Request] = []
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_response()
        api_calls.append(request)
        return httpx.Response(500, json={"error": "boom"})

    client = make_write_client(handler, sleep=sleeps.append)
    with pytest.raises(AdsApiError):
        client.pausar_keyword(101)

    assert len(api_calls) == 1
    assert sleeps == []


def test_fallo_de_red_lanza_sin_reintentar():
    """Fallo de red = ambiguo: sin reintento (espejo de create_report)."""
    api_calls: list[httpx.Request] = []
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_response()
        api_calls.append(request)
        raise httpx.ConnectError("boom", request=request)

    client = make_write_client(handler, sleep=sleeps.append)
    with pytest.raises(AdsApiError):
        client.pausar_keyword(101)

    assert len(api_calls) == 1
    assert sleeps == []


def test_401_un_refresh_forzado_y_reemision_con_mismo_scope_vendor():
    """401 = rechazado sin procesar: UN refresh forzado y re-emision. Ambas
    emisiones llevan el MISMO scope sellado y el MISMO vendor (sin el
    vendor, Amazon responde 415 y el retry moriria); el bearer SI cambia."""
    token_calls: list[httpx.Request] = []
    api_calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            token_calls.append(request)
            return _token_response(len(token_calls))
        api_calls.append(request)
        if len(api_calls) == 1:
            return httpx.Response(401, json={"error": "Unauthorized"})
        return httpx.Response(200, json={"ack": "ok"})

    client = make_write_client(handler)
    resp = client.actualizar_bid_keyword(101, Decimal("0.75"), "MXN")

    assert resp.status_code == 200
    assert len(token_calls) == 2, "token inicial + UN refresh forzado, nada mas"
    assert len(api_calls) == 2, "emision original + UNA re-emision"
    vendor = MUTATION_REQUEST_TYPES[("PUT", "/sp/keywords")]
    bearers = []
    for request in api_calls:
        assert request.headers["Amazon-Advertising-API-Scope"] == str(FAKE_PROFILE_MX)
        assert request.headers["Content-Type"] == vendor
        assert request.headers["Accept"] == vendor
        bearers.append(request.headers["Authorization"])
    assert bearers[0] != bearers[1], "la re-emision usa el token refrescado"


def test_plataforma_moneda_consistente_con_el_mapa_del_motor():
    """Hallazgo reviewer r1 (1.3): PLATAFORMA_MONEDA (capa HTTP) y
    PLATAFORMAS_MONEDA (capa motor) son dos mapas con dueños distintos (el
    cliente de ads NO importa el optimizer) — la duplicacion de VOCABULARIO
    es tolerable, pero nada pineaba que sigan IGUALES: crecer a una tercera
    plataforma se editaria en dos lugares sin que nada los compare. Este
    test vuelve la divergencia una decision visible (regla 2)."""
    from app.optimizer.bid import PLATAFORMAS_MONEDA

    assert dict(PLATAFORMA_MONEDA) == dict(PLATAFORMAS_MONEDA), (
        "PLATAFORMA_MONEDA (write) y PLATAFORMAS_MONEDA (motor) divergieron: "
        "unificar a proposito o explicar la razon de la divergencia aqui"
    )
