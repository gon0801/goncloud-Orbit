"""Tests del sync de estructura Amazon Ads (`app.ads.structure`).

(a) UNITARIOS: transporte 100% mock (`httpx.MockTransport`, patron de
    tests/test_ads_client.py) para fetch/perfiles/listas v3 paginadas, y la
    planificacion pura `_plan_items` para items incoherentes. Corren siempre.
    Las fixtures replican las formas REALES verificadas en la corrida del
    2026-08-22: perfiles con countryCode/accountInfo (sin country/valid),
    POST /sp/*/list con vendor content-types, paginacion por nextToken,
    ids string planos, keywords/targets con y sin bid, contenedor
    "targetingClauses".
(b) INTEGRACION: aplican la migracion entera en un Postgres temporal
    (patron de tests/test_schema.py) y prueban sync + re-sync reales.
    Skip automatico sin Postgres utilizable en ORBIT_TEST_DSN.

Regla 9: los tests de skip AFIRMAN el skip y su motivo (no solo que no
reviente): quitar la validacion de moneda de perfil o el pre-check de padre
incoherente tiene que voltear una asercion.
"""

from __future__ import annotations

import json
import os
import socket
from collections import Counter
from decimal import Decimal

import httpx
import pytest
from test_schema import SQL, _hay_postgres_local, _test_dsn

from app.ads.client import LIST_REQUEST_TYPES, AdsClient
from app.ads.config import AdsCredentials
from app.ads.structure import (
    MAX_PAGINAS,
    AdsStructureError,
    EstructuraAds,
    EstructuraPerfil,
    PerfilAds,
    _plan_items,
    fetch_structure,
    main,
    sync_structure,
)

FAKE_CLIENT_ID = "fake-client-id-123"
FAKE_CLIENT_SECRET = "fake-client-secret-XYZ"
FAKE_REFRESH_TOKEN = "fake-refresh-token-ABC"

# ---------------------------------------------------------------------------
# Fixtures de payload (formas REALES de la corrida 2026-08-22)
# ---------------------------------------------------------------------------

PERFILES_OK = [
    {
        "profileId": 101,
        "countryCode": "US",
        "currencyCode": "USD",
        "dailyBudget": 10.0,
        "timezone": "America/Los_Angeles",
        "accountInfo": {
            "marketplaceStringId": "ATVPDKIKX0DER",
            "id": "1",
            "type": "seller",
            "name": "Goncloud US",
            "validPaymentMethod": True,
        },
    },
    {
        "profileId": 202,
        "countryCode": "MX",
        "currencyCode": "MXN",
        "dailyBudget": 200.0,
        "timezone": "America/Mexico_City",
        "accountInfo": {
            "marketplaceStringId": "A1AM78C64UM0Y8",
            "id": "2",
            "type": "seller",
            "name": "Goncloud MX",
            "validPaymentMethod": True,
        },
    },
]

CAMPANAS_US = [
    {
        "campaignId": "9001",
        "name": "Camp US Uno",
        "state": "ENABLED",
        "targetingType": "MANUAL",
        "budget": {"budget": 10.0, "budgetType": "DAILY"},
    },
    {
        "campaignId": "9002",
        "name": "Camp US Auto",
        "state": "ARCHIVED",
        "targetingType": "AUTO",
        "budget": {"budget": 5.0, "budgetType": "DAILY"},
    },
]
AD_GROUPS_US = [
    {
        "adGroupId": "9101",
        "campaignId": "9001",
        "name": "AG Uno",
        "state": "ENABLED",
        "defaultBid": 0.75,
    }
]
KEYWORDS_US = [
    {
        "keywordId": "9201",
        "adGroupId": "9101",
        "campaignId": "9001",
        "keywordText": "zapato",
        "matchType": "EXACT",
        "state": "ENABLED",
        "bid": 0.5,
    },
    # keyword de campana AUTO: v3 NO trae bid -> item valido con bid NULL
    # (campaignId coherente con la campana de su ad group; cross-review ronda 3)
    {
        "keywordId": "9205",
        "adGroupId": "9101",
        "campaignId": "9001",
        "keywordText": "auto kw",
        "matchType": "BROAD",
        "state": "ENABLED",
    },
]
TARGETS_US = [
    {
        "targetId": "9301",
        "adGroupId": "9101",
        "campaignId": "9001",
        "expression": [{"type": "asinSameAs", "value": "B0TEST1234"}],
        "expressionType": "MANUAL",
        "resolvedExpression": [{"type": "asinSameAs", "value": "B0TEST1234"}],
        "state": "ENABLED",
        "bid": 0.9,
    },
    # target sin bid (en la corrida real 513/549 lo traen): bid NULL
    {
        "targetId": "9305",
        "adGroupId": "9101",
        "campaignId": "9001",
        "expression": [{"type": "queryHighRelMatches", "value": "zapato"}],
        "expressionType": "AUTO",
        "state": "PAUSED",
    },
]

CAMPANAS_MX = [
    {
        "campaignId": "7001",
        "name": "Camp MX Uno",
        "state": "ENABLED",
        "targetingType": "AUTO",
        "budget": {"budget": 200.0, "budgetType": "DAILY"},
    },
    {
        "campaignId": "7002",
        "name": "Camp MX Manual",
        "state": "ENABLED",
        "targetingType": "MANUAL",
        "budget": {"budget": 150.0, "budgetType": "DAILY"},
    },
]
AD_GROUPS_MX = [
    {
        "adGroupId": "7101",
        "campaignId": "7001",
        "name": "AG MX",
        "state": "ENABLED",
        "defaultBid": 15.5,
    }
]
KEYWORDS_MX = [
    {
        "keywordId": "7201",
        "adGroupId": "7101",
        "campaignId": "7001",
        "keywordText": "tenis",
        "matchType": "PHRASE",
        "state": "PAUSED",
        "bid": 12,
    }
]
TARGETS_MX: list[dict] = []

_SP_DATOS = {
    "101": {
        "campaigns": CAMPANAS_US,
        "adGroups": AD_GROUPS_US,
        "keywords": KEYWORDS_US,
        "targetingClauses": TARGETS_US,
    },
    "202": {
        "campaigns": CAMPANAS_MX,
        "adGroups": AD_GROUPS_MX,
        "keywords": KEYWORDS_MX,
        "targetingClauses": TARGETS_MX,
    },
}

_PATH_CLAVE = {
    "/sp/campaigns/list": "campaigns",
    "/sp/adGroups/list": "adGroups",
    "/sp/keywords/list": "keywords",
    "/sp/targets/list": "targetingClauses",
}


def _cliente(handler) -> AdsClient:
    return AdsClient(
        AdsCredentials(
            client_id=FAKE_CLIENT_ID,
            client_secret=FAKE_CLIENT_SECRET,
            refresh_token=FAKE_REFRESH_TOKEN,
        ),
        transport=httpx.MockTransport(handler),
        sleep=lambda seconds: None,
    )


def _handler_sp(perfiles: list[dict], sp_datos: dict, registro: list[dict]):
    """Sirve GET /v2/profiles + los 4 POST list por scope.

    campaigns pagina de a 1 item via nextToken (las fixtures traen 2 campanas
    por perfil -> 2 paginas); el resto responde en una sola pagina. Registra
    metodo, scope, vendor Content-Type/Accept y body de cada llamada.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return httpx.Response(200, json={"access_token": "fake-access", "expires_in": 3600})
        if request.url.path == "/v2/profiles":
            registro.append(
                {
                    "path": "/v2/profiles",
                    "metodo": request.method,
                    "scope": request.headers.get("Amazon-Advertising-API-Scope"),
                }
            )
            return httpx.Response(200, json=perfiles)
        registro.append(
            {
                "path": request.url.path,
                "metodo": request.method,
                "scope": request.headers.get("Amazon-Advertising-API-Scope"),
                "content_type": request.headers.get("Content-Type"),
                "accept": request.headers.get("Accept"),
                "body": json.loads(request.content) if request.content else None,
            }
        )
        clave = _PATH_CLAVE[request.url.path]
        datos = sp_datos[request.headers.get("Amazon-Advertising-API-Scope")][clave]
        if clave != "campaigns":
            return httpx.Response(200, json={clave: datos, "totalResults": len(datos)})
        cuerpo = json.loads(request.content) if request.content else {}
        indice = int(str(cuerpo.get("nextToken", "p0")).removeprefix("p"))
        siguiente = f"p{indice + 1}" if indice + 1 < len(datos) else None
        respuesta = {clave: datos[indice : indice + 1], "totalResults": len(datos)}
        if siguiente is not None:
            respuesta["nextToken"] = siguiente
        return httpx.Response(200, json=respuesta)

    return handler


# ---------------------------------------------------------------------------
# (a) UNITARIOS - fetch
# ---------------------------------------------------------------------------


def test_fetch_usa_get_de_perfiles_y_post_list_con_scope_y_vendor_types():
    registro: list[dict] = []
    client = _cliente(_handler_sp(PERFILES_OK, _SP_DATOS, registro))
    estructura = fetch_structure(client)

    # /v2/profiles: un solo GET, sin scope (unico GET v2 que sigue vivo)
    assert [r for r in registro if r["path"] == "/v2/profiles"] == [
        {"path": "/v2/profiles", "metodo": "GET", "scope": None}
    ]

    for path in LIST_REQUEST_TYPES:
        llamadas = [r for r in registro if r["path"] == path]
        scopes = sorted(str(r["scope"]) for r in llamadas)
        if path == "/sp/campaigns/list":
            # 2 paginas por perfil via nextToken
            assert scopes == ["101", "101", "202", "202"]
        else:
            assert scopes == ["101", "202"]
        for llamada in llamadas:
            assert llamada["metodo"] == "POST"
            assert llamada["content_type"] == LIST_REQUEST_TYPES[path]
            assert llamada["accept"] == LIST_REQUEST_TYPES[path]

    # primera pagina sin body de paginacion, segunda pidiendo el nextToken
    camp_us = [r for r in registro if r["path"] == "/sp/campaigns/list" and r["scope"] == "101"]
    assert camp_us[0]["body"] == {}
    assert camp_us[1]["body"] == {"nextToken": "p1"}

    # la estructura reensamblo TODAS las paginas y parseo las formas v3
    assert [est.perfil.profile_id for est in estructura.estructuras] == [101, 202]
    est_us = estructura.estructuras[0]
    assert est_us.perfil.platform == "amazon_us"
    assert est_us.campanas == CAMPANAS_US
    assert est_us.ad_groups == AD_GROUPS_US
    assert est_us.keywords == KEYWORDS_US
    assert est_us.targets == TARGETS_US
    est_mx = estructura.estructuras[1]
    assert est_mx.perfil.platform == "amazon_mx"
    assert est_mx.campanas == CAMPANAS_MX
    assert est_mx.targets == []


def test_perfiles_rechazados_quedan_fuera_con_motivo_y_sin_llamadas_de_lista():
    perfiles = PERFILES_OK + [
        # pais fuera de {US, MX}
        {
            "profileId": 303,
            "countryCode": "CA",
            "currencyCode": "CAD",
            "dailyBudget": 5.0,
            "timezone": "America/Toronto",
            "accountInfo": {
                "marketplaceStringId": "A2EUQ1WTGCTBG2",
                "id": "3",
                "type": "seller",
                "name": "Goncloud CA",
                "validPaymentMethod": True,
            },
        },
        # moneda inconsistente con el pais: perfil entero RECHAZADO
        {
            "profileId": 404,
            "countryCode": "MX",
            "currencyCode": "USD",
            "dailyBudget": 1.0,
            "timezone": "America/Mexico_City",
            "accountInfo": {
                "marketplaceStringId": "A1AM78C64UM0Y8",
                "id": "4",
                "type": "seller",
                "name": "Raro",
                "validPaymentMethod": True,
            },
        },
        # no seller
        {
            "profileId": 505,
            "countryCode": "US",
            "currencyCode": "USD",
            "dailyBudget": 2.0,
            "timezone": "America/Los_Angeles",
            "accountInfo": {
                "marketplaceStringId": "ATVPDKIKX0DER",
                "id": "5",
                "type": "vendor",
                "name": "Vendor US",
                "validPaymentMethod": True,
            },
        },
    ]
    registro: list[dict] = []
    client = _cliente(_handler_sp(perfiles, _SP_DATOS, registro))
    estructura = fetch_structure(client)

    # evidencia de TODOS los perfiles vistos, con su tratamiento
    assert len(estructura.perfiles) == 5
    por_id = {perfil.profile_id: perfil for perfil in estructura.perfiles}
    assert {p.profile_id for p in estructura.perfiles if p.aceptado} == {101, 202}
    # Regla 9: si se quita la validacion de moneda, 404 entra y esto truena.
    assert "moneda" in por_id[404].motivo
    assert "MXN" in por_id[404].motivo
    assert "pais no soportado" in por_id[303].motivo
    assert "seller" in por_id[505].motivo
    assert por_id[101].motivo is None and por_id[202].motivo is None
    # validPaymentMethod queda como evidencia, sin gatear
    assert por_id[101].valid_payment_method is True

    # solo los aceptados generaron llamadas de lista
    assert [est.perfil.profile_id for est in estructura.estructuras] == [101, 202]
    scopes_llamados = {r["scope"] for r in registro if r["scope"] is not None}
    assert scopes_llamados == {"101", "202"}


def test_fetch_respuesta_inesperada_da_error_claro():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            # token falso LARGO: el cliente lo registra como secreto y scrub()
            # lo reemplazaria en cualquier mensaje posterior (un token de 1
            # letra romperia mensajes de otros tests: contaminacion real).
            return httpx.Response(
                200, json={"access_token": "fake-access-token-largo", "expires_in": 3600}
            )
        return httpx.Response(200, json={"code": 404})

    client = _cliente(handler)
    with pytest.raises(AdsStructureError) as excinfo:
        fetch_structure(client)
    assert "/v2/profiles" in str(excinfo.value)


def test_paginacion_sin_fin_da_error_claro():
    """Un nextToken que nunca falta no debe colgar la corrida: tope MAX_PAGINAS
    con error claro (y verificado que el tope se alcanza, no se ignora)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return httpx.Response(
                200, json={"access_token": "fake-access-token-largo", "expires_in": 3600}
            )
        if request.url.path == "/v2/profiles":
            return httpx.Response(200, json=[PERFILES_OK[0]])
        return httpx.Response(200, json={"campaigns": [], "totalResults": 0, "nextToken": "bucle"})

    client = _cliente(handler)
    with pytest.raises(AdsStructureError) as excinfo:
        fetch_structure(client)
    assert str(MAX_PAGINAS) in str(excinfo.value)
    assert "paginacion" in str(excinfo.value)


def test_paginacion_incompleta_segun_totalresults_da_error():
    """[cross-review codex r3] "falta nextToken" ya no basta como prueba de
    lista completa: si la ultima pagina declara totalResults y el acumulado
    no cuadra, error claro. Sin esta guarda, una pagina truncada (nextToken
    perdido por la API) entraria como lista completa en silencio."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return httpx.Response(
                200, json={"access_token": "fake-access-token-largo", "expires_in": 3600}
            )
        if request.url.path == "/v2/profiles":
            return httpx.Response(200, json=[PERFILES_OK[0]])
        # una sola pagina SIN nextToken, pero declarando 5 totales: incompleta
        return httpx.Response(200, json={"campaigns": [CAMPANAS_US[0]], "totalResults": 5})

    client = _cliente(handler)
    with pytest.raises(AdsStructureError) as excinfo:
        fetch_structure(client)
    assert "paginacion incompleta" in str(excinfo.value)
    assert "1 acumulados de 5" in str(excinfo.value)


def test_pais_duplicado_rechaza_al_segundo_perfil():
    """[cross-review codex r3] Un perfil por pais es GUARD, no supuesto: el
    segundo perfil aceptable del mismo pais se rechaza con motivo y no
    genera llamadas de lista (la platform es una sola por pais)."""
    perfiles = PERFILES_OK + [
        {
            "profileId": 606,
            "countryCode": "US",
            "currencyCode": "USD",
            "dailyBudget": 7.0,
            "timezone": "America/Los_Angeles",
            "accountInfo": {
                "marketplaceStringId": "ATVPDKIKX0DER",
                "id": "6",
                "type": "seller",
                "name": "Goncloud US Dos",
                "validPaymentMethod": True,
            },
        },
    ]
    registro: list[dict] = []
    client = _cliente(_handler_sp(perfiles, _SP_DATOS, registro))
    estructura = fetch_structure(client)

    por_id = {perfil.profile_id: perfil for perfil in estructura.perfiles}
    assert len(estructura.perfiles) == 3
    assert {p.profile_id for p in estructura.perfiles if p.aceptado} == {101, 202}
    # Regla 9: gana el PRIMERO; el duplicado queda con su motivo explicito.
    # Sin el guard, 606 entraria como aceptado y esto truena.
    assert por_id[606].motivo == "pais duplicado: ya se acepto otro perfil US"
    assert por_id[101].motivo is None

    assert [est.perfil.profile_id for est in estructura.estructuras] == [101, 202]
    scopes_llamados = {r["scope"] for r in registro if r["scope"] is not None}
    assert scopes_llamados == {"101", "202"}


# ---------------------------------------------------------------------------
# (a) UNITARIOS - planificacion pura (items incoherentes)
# ---------------------------------------------------------------------------


def _perfil_us_aceptado() -> PerfilAds:
    return PerfilAds(
        profile_id=101,
        country="US",
        currency_code="USD",
        account_type="seller",
        valid_payment_method=True,
        account_name="Goncloud US",
        aceptado=True,
        platform="amazon_us",
        moneda="USD",
    )


def test_items_incoherentes_se_saltan_con_motivo():
    """Regla 9: el test afirma cada skip y su motivo. Sin el pre-check de
    padre, el ad group huerfano y sus hijos entrarian con parent NULL; sin el
    pre-check de campaignId, keywords/targets incoherentes entrarian igual:
    el conteo de skips daria menos -> truena."""
    estructura = EstructuraAds(
        perfiles=[_perfil_us_aceptado()],
        estructuras=[
            EstructuraPerfil(
                perfil=_perfil_us_aceptado(),
                campanas=[
                    {
                        "campaignId": "9001",
                        "name": "C",
                        "targetingType": "MANUAL",
                        "state": "ENABLED",
                    },
                ],
                ad_groups=[
                    # campana "9999" NO vino -> ad group huerfano
                    {
                        "adGroupId": "9101",
                        "campaignId": "9999",
                        "defaultBid": 0.5,
                        "state": "ENABLED",
                    },
                    # ad group COHERENTE (campana 9001): padre valido para los
                    # casos de campaignId incoherente de abajo
                    {
                        "adGroupId": "9150",
                        "campaignId": "9001",
                        "defaultBid": 0.4,
                        "state": "ENABLED",
                    },
                ],
                keywords=[
                    # ad group 9101 fue saltado -> keyword sin padre escrito
                    {
                        "keywordId": "9201",
                        "adGroupId": "9101",
                        "keywordText": "zapato",
                        "matchType": "EXACT",
                        "bid": 0.4,
                    },
                    # sin matchType (el CHECK ad_entity_keyword_coherente lo rechazaria)
                    {"keywordId": "9202", "adGroupId": "9101", "keywordText": "x", "bid": 0.4},
                    # campaignId contradice al de su ad group (9001): incoherente
                    {
                        "keywordId": "9203",
                        "adGroupId": "9150",
                        "campaignId": "8888",
                        "keywordText": "rara",
                        "matchType": "PHRASE",
                        "bid": 0.4,
                    },
                ],
                targets=[
                    # ad group "8888" no existe en el payload
                    {"targetId": "9301", "adGroupId": "8888", "bid": 0.9, "expression": []},
                    # campaignId contradice al de su ad group (9001): incoherente
                    {
                        "targetId": "9302",
                        "adGroupId": "9150",
                        "campaignId": "7777",
                        "bid": 0.9,
                        "expression": [],
                    },
                ],
            )
        ],
    )

    items, skips = _plan_items(estructura)

    assert [item.kind for item in items] == ["campaign", "ad_group"]
    assert skips == Counter(
        {
            "ad group sin campana planificada": 1,
            "keyword sin ad group planificado": 1,
            "keyword sin matchType": 1,
            "keyword con campaignId distinto al de su ad group (payload incoherente)": 1,
            "target sin ad group planificado": 1,
            "target con campaignId distinto al de su ad group (payload incoherente)": 1,
        }
    )


def test_items_validos_llevan_bid_decimal_y_moneda_del_perfil():
    estructura = EstructuraAds(
        perfiles=[_perfil_us_aceptado()],
        estructuras=[
            EstructuraPerfil(
                perfil=_perfil_us_aceptado(),
                campanas=[
                    {
                        "campaignId": "9001",
                        "name": "C",
                        "targetingType": "MANUAL",
                        "state": "ENABLED",
                        "budget": {"budget": 10.0, "budgetType": "DAILY"},
                    }
                ],
                ad_groups=[
                    {
                        "adGroupId": "9101",
                        "campaignId": "9001",
                        "defaultBid": 0.75,
                        "state": "ENABLED",
                    },
                    # defaultBid ausente -> bid NULL con moneda NULL (regla 3)
                    {"adGroupId": "9102", "campaignId": "9001", "state": "PAUSED"},
                ],
                keywords=[
                    {
                        "keywordId": "9201",
                        "adGroupId": "9101",
                        "campaignId": "9001",
                        "keywordText": "z",
                        "matchType": "EXACT",
                        "bid": 0.5,
                    },
                    # keyword sin bid (campana AUTO): item VALIDO, bid NULL
                    {
                        "keywordId": "9202",
                        "adGroupId": "9101",
                        "keywordText": "y",
                        "matchType": "BROAD",
                    },
                ],
                targets=[
                    {
                        "targetId": "9301",
                        "adGroupId": "9101",
                        "campaignId": "9001",
                        "expression": [{"type": "asinSameAs", "value": "B01"}],
                        "bid": 0.9,
                    },
                    {
                        "targetId": "9302",
                        "adGroupId": "9101",
                        "expression": [{"type": "queryHighRelMatches", "value": "z"}],
                    },
                ],
            )
        ],
    )

    items, skips = _plan_items(estructura)

    assert skips == Counter()
    campana = next(i for i in items if i.kind == "campaign")
    kws = [i for i in items if i.kind == "keyword"]
    tgs = [i for i in items if i.kind == "product_target"]
    ags = [i for i in items if i.kind == "ad_group"]
    # budget de campana sin moneda: NO se guarda como bid
    assert campana.bid is None and campana.bid_currency is None
    assert (ags[0].bid, ags[0].bid_currency) == (Decimal("0.75"), "USD")
    assert (ags[1].bid, ags[1].bid_currency) == (None, None)
    # keyword sin name (keyword_text es la fuente unica) y bid opcional
    assert all(kw.name is None for kw in kws)
    assert (kws[0].match_type, kws[0].bid, kws[0].bid_currency) == ("EXACT", Decimal("0.5"), "USD")
    assert (kws[1].match_type, kws[1].bid, kws[1].bid_currency) == ("BROAD", None, None)
    # target con JSON compacto de expression (el segundo, sin bid)
    assert tgs[0].name == json.dumps(
        [{"type": "asinSameAs", "value": "B01"}], separators=(",", ":"), sort_keys=True
    )
    assert (tgs[1].bid, tgs[1].bid_currency) == (None, None)


def test_items_con_bid_corrupto_se_saltan():
    estructura = EstructuraAds(
        perfiles=[_perfil_us_aceptado()],
        estructuras=[
            EstructuraPerfil(
                perfil=_perfil_us_aceptado(),
                campanas=[{"campaignId": "9001", "targetingType": "MANUAL", "state": "ENABLED"}],
                ad_groups=[
                    {
                        "adGroupId": "9101",
                        "campaignId": "9001",
                        "defaultBid": "mucho",
                        "state": "ENABLED",
                    }
                ],
                keywords=[],
                targets=[],
            )
        ],
    )

    items, skips = _plan_items(estructura)

    assert [item.kind for item in items] == ["campaign"]
    assert skips == Counter({"ad group con defaultBid no numerico": 1})


# ---------------------------------------------------------------------------
# (a) UNITARIOS - fail-closed del __main__ y guarda SQL
# ---------------------------------------------------------------------------


def test_main_sin_orbit_dsn_ingest_falla_cerrado(monkeypatch, capsys):
    monkeypatch.delenv("ORBIT_DSN_INGEST", raising=False)
    assert main() != 0
    assert "ORBIT_DSN_INGEST" in capsys.readouterr().err


def test_sql_del_modulo_parsea_como_postgres():
    """Guarda barata que SIEMPRE corre: la sintaxis SQL de los upserts es
    Postgres valida segun el parser real (pglast, mismo que usa test_schema).
    La integracion en vivo skipea sin ORBIT_TEST_DSN (y en CI corre contra el
    service postgres:16); esto atrapa typos de SQL antes de llegar ahi."""
    pglast = pytest.importorskip("pglast")
    import app.ads.structure as estructura_modulo

    for nombre in (
        "_SQL_ABRIR_RUN",
        "_SQL_SELLAR_RUN",
        "_SQL_UPSERT_ENTIDAD",
        "_SQL_UPSERT_STATE",
    ):
        sql = getattr(estructura_modulo, nombre).replace("%s", "NULL")
        assert pglast.parse_sql(sql), f"{nombre} no parseo"


# ---------------------------------------------------------------------------
# (b) INTEGRACION - patron de tests/test_schema.py
# ---------------------------------------------------------------------------

# indices del SELECT de _entidad:
# 0=id 1=parent_id 2=name 3=match_type 4=keyword_text 5=current_bid
# 6=bid_currency 7=status 8=targeting_type 9=acos_target 10=synced_at
_SQL_ENTIDAD = (
    "SELECT e.id, e.parent_id, e.name, e.match_type, e.keyword_text,"
    " s.current_bid, s.bid_currency, s.status, s.targeting_type,"
    " s.acos_target, s.synced_at"
    " FROM ad_entity e LEFT JOIN ad_entity_state s ON s.ad_entity_id = e.id"
    " WHERE e.platform = %s AND e.kind = %s AND e.external_id = %s"
)


def _entidad(conn, platform: str, kind: str, external_id: str):
    return conn.execute(_SQL_ENTIDAD, (platform, kind, external_id)).fetchone()


def _perfil_aceptado(profile_id: int, country: str) -> PerfilAds:
    platform, moneda = ("amazon_us", "USD") if country == "US" else ("amazon_mx", "MXN")
    return PerfilAds(
        profile_id=profile_id,
        country=country,
        currency_code=moneda,
        account_type="seller",
        valid_payment_method=True,
        account_name="Cuenta Test",
        aceptado=True,
        platform=platform,
        moneda=moneda,
    )


def _estructura_sync1() -> EstructuraAds:
    us, mx = _perfil_aceptado(101, "US"), _perfil_aceptado(202, "MX")
    return EstructuraAds(
        perfiles=[us, mx],
        estructuras=[
            EstructuraPerfil(
                perfil=us,
                campanas=[
                    {
                        "campaignId": "9001",
                        "name": "Camp US Uno",
                        "targetingType": "MANUAL",
                        "state": "ENABLED",
                        "budget": {"budget": 10.0, "budgetType": "DAILY"},
                    }
                ],
                ad_groups=[
                    {
                        "adGroupId": "9101",
                        "name": "AG Uno",
                        "campaignId": "9001",
                        "state": "ENABLED",
                        "defaultBid": 0.75,
                    },
                    {
                        "adGroupId": "9199",
                        "name": "AG Movido",
                        "campaignId": "9001",
                        "state": "ENABLED",
                        "defaultBid": 0.5,
                    },
                ],
                keywords=[
                    {
                        "keywordId": "9201",
                        "adGroupId": "9101",
                        "campaignId": "9001",
                        "keywordText": "zapato",
                        "matchType": "EXACT",
                        "state": "ENABLED",
                        "bid": 0.5,
                    },
                    # sin matchType -> skip (el CHECK lo rechazaria)
                    {
                        "keywordId": "9202",
                        "adGroupId": "9101",
                        "keywordText": "sin match",
                        "state": "ENABLED",
                        "bid": 0.4,
                    },
                    # sin bid (campana AUTO): item VALIDO con bid NULL (regla 3)
                    {
                        "keywordId": "9205",
                        "adGroupId": "9101",
                        "campaignId": "9001",
                        "keywordText": "auto kw",
                        "matchType": "BROAD",
                        "state": "ENABLED",
                    },
                ],
                targets=[
                    {
                        "targetId": "9301",
                        "adGroupId": "9101",
                        "campaignId": "9001",
                        "expression": [{"type": "asinSameAs", "value": "B0TEST1234"}],
                        "expressionType": "MANUAL",
                        "state": "ENABLED",
                        "bid": 0.9,
                    },
                    # ad group "8888" no existe -> skip
                    {"targetId": "9399", "adGroupId": "8888", "state": "ENABLED", "bid": 0.8},
                ],
            ),
            EstructuraPerfil(
                perfil=mx,
                campanas=[
                    {
                        "campaignId": "7001",
                        "name": "Camp MX Uno",
                        "targetingType": "AUTO",
                        "state": "ENABLED",
                        "budget": {"budget": 200.0, "budgetType": "DAILY"},
                    }
                ],
                ad_groups=[
                    {
                        "adGroupId": "7101",
                        "name": "AG MX",
                        "campaignId": "7001",
                        "state": "ENABLED",
                        "defaultBid": 15.5,
                    }
                ],
                keywords=[
                    {
                        "keywordId": "7201",
                        "adGroupId": "7101",
                        "campaignId": "7001",
                        "keywordText": "tenis",
                        "matchType": "PHRASE",
                        "state": "PAUSED",
                        "bid": 12,
                    }
                ],
                targets=[],
            ),
        ],
    )


def _estructura_resync() -> EstructuraAds:
    """Estado cambiado + entidades nuevas + un padre que se movio de campana."""
    us, mx = _perfil_aceptado(101, "US"), _perfil_aceptado(202, "MX")
    return EstructuraAds(
        perfiles=[us, mx],
        estructuras=[
            EstructuraPerfil(
                perfil=us,
                campanas=[
                    # 9001 cambia de estado; 9002 es NUEVA
                    {
                        "campaignId": "9001",
                        "name": "Camp US Uno",
                        "targetingType": "MANUAL",
                        "state": "PAUSED",
                        "budget": {"budget": 12.0, "budgetType": "DAILY"},
                    },
                    {
                        "campaignId": "9002",
                        "name": "Camp US Dos",
                        "targetingType": "AUTO",
                        "state": "ENABLED",
                        "budget": {"budget": 5.0, "budgetType": "DAILY"},
                    },
                ],
                ad_groups=[
                    {
                        "adGroupId": "9101",
                        "name": "AG Uno",
                        "campaignId": "9001",
                        "state": "ENABLED",
                        "defaultBid": 1.25,
                    },
                    # 9199 EXISTE con padre 9001; el payload ahora dice 9002 ->
                    # parent_id es inmutable: skip, jamas UPDATE
                    {
                        "adGroupId": "9199",
                        "name": "AG Movido",
                        "campaignId": "9002",
                        "state": "ENABLED",
                        "defaultBid": 0.66,
                    },
                ],
                keywords=[
                    # [cross-review codex r3] keywordText DISTINTO al existente
                    # (misma keywordId): inmutable divergente -> skip, jamas
                    # UPDATE silencioso del texto viejo
                    {
                        "keywordId": "9201",
                        "adGroupId": "9101",
                        "campaignId": "9001",
                        "keywordText": "zapatos",
                        "matchType": "EXACT",
                        "state": "ENABLED",
                        "bid": 0.55,
                    },
                    # NUEVA bajo 9101 -> debe insertarse
                    {
                        "keywordId": "9203",
                        "adGroupId": "9101",
                        "campaignId": "9001",
                        "keywordText": "nuevo",
                        "matchType": "BROAD",
                        "state": "ENABLED",
                        "bid": 0.6,
                    },
                    # NUEVA bajo 9199, que se salto en runtime -> cascada
                    {
                        "keywordId": "9204",
                        "adGroupId": "9199",
                        "campaignId": "9002",
                        "keywordText": "colgando",
                        "matchType": "BROAD",
                        "state": "ENABLED",
                        "bid": 0.3,
                    },
                    # la keyword sin bid de la corrida 1 AHORA trae bid
                    {
                        "keywordId": "9205",
                        "adGroupId": "9101",
                        "campaignId": "9001",
                        "keywordText": "auto kw",
                        "matchType": "BROAD",
                        "state": "ENABLED",
                        "bid": 0.42,
                    },
                ],
                targets=[
                    {
                        "targetId": "9301",
                        "adGroupId": "9101",
                        "campaignId": "9001",
                        "expression": [{"type": "asinSameAs", "value": "B0TEST1234"}],
                        "expressionType": "MANUAL",
                        "state": "ENABLED",
                        "bid": 0.9,
                    },
                ],
            ),
            EstructuraPerfil(
                perfil=mx,
                campanas=[
                    {
                        "campaignId": "7001",
                        "name": "Camp MX Uno",
                        "targetingType": "AUTO",
                        "state": "ENABLED",
                        "budget": {"budget": 200.0, "budgetType": "DAILY"},
                    }
                ],
                ad_groups=[
                    {
                        "adGroupId": "7101",
                        "name": "AG MX",
                        "campaignId": "7001",
                        "state": "ENABLED",
                        "defaultBid": 16.0,
                    }
                ],
                keywords=[
                    {
                        "keywordId": "7201",
                        "adGroupId": "7101",
                        "campaignId": "7001",
                        "keywordText": "tenis",
                        "matchType": "PHRASE",
                        "state": "PAUSED",
                        "bid": 12,
                    }
                ],
                targets=[],
            ),
        ],
    )


def _monedas_de_plataforma(conn, platform: str) -> set[str]:
    return {
        fila[0]
        for fila in conn.execute(
            "SELECT DISTINCT s.bid_currency FROM ad_entity_state s"
            " JOIN ad_entity e ON e.id = s.ad_entity_id"
            " WHERE e.platform = %s AND s.bid_currency IS NOT NULL",
            (platform,),
        )
    }


@pytest.mark.skipif(
    not _hay_postgres_local(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_sync_y_resync_estructura_en_vivo(monkeypatch):
    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql as pgsql

    dsn = _test_dsn()
    db = f"orbit_struct_test_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        conn.execute(SQL)  # la migracion entera

        # ------------------------------------------------------------------
        # SYNC 1
        # ------------------------------------------------------------------
        res1 = sync_structure(conn, _estructura_sync1())

        # contabilidad: 9 items validos (6 us + 3 mx), 2 saltados
        assert res1.ok is True
        assert res1.rows_written == 9
        assert res1.rows_skipped == 2
        assert res1.entidades_nuevas == 9
        assert res1.counts == {
            ("amazon_us", "campaign"): 1,
            ("amazon_us", "ad_group"): 2,
            ("amazon_us", "keyword"): 2,
            ("amazon_us", "product_target"): 1,
            ("amazon_mx", "campaign"): 1,
            ("amazon_mx", "ad_group"): 1,
            ("amazon_mx", "keyword"): 1,
        }

        run = conn.execute(
            "SELECT source, ok, rows_written, rows_skipped, skip_reason, finished_at"
            " FROM ingest_run WHERE id = %s",
            (res1.run_id,),
        ).fetchone()
        assert run[0] == "amazon_ads_structure_v2"
        assert run[1] is True and run[2] == 9 and run[3] == 2
        assert run[4] is not None and run[5] is not None
        # Regla 9: el skip queda AFIRMADO con su motivo y contador. Sin el
        # pre-check de padre, el target huerfano entraria con parent NULL y
        # rows_skipped seria 0 -> truena.
        assert "1x keyword sin matchType" in run[4]
        assert "1x target sin ad group planificado" in run[4]

        assert conn.execute("SELECT count(*) FROM ad_entity").fetchone()[0] == 9
        # los saltados NO dejaron fila
        assert _entidad(conn, "amazon_us", "keyword", "9202") is None
        assert _entidad(conn, "amazon_us", "product_target", "9399") is None

        c_us = _entidad(conn, "amazon_us", "campaign", "9001")
        ag = _entidad(conn, "amazon_us", "ad_group", "9101")
        ag_movido = _entidad(conn, "amazon_us", "ad_group", "9199")
        kw = _entidad(conn, "amazon_us", "keyword", "9201")
        kw_sin_bid = _entidad(conn, "amazon_us", "keyword", "9205")
        tg = _entidad(conn, "amazon_us", "product_target", "9301")
        c_mx = _entidad(conn, "amazon_mx", "campaign", "7001")
        ag_mx = _entidad(conn, "amazon_mx", "ad_group", "7101")
        kw_mx = _entidad(conn, "amazon_mx", "keyword", "7201")

        # campana: sin padre, sin bid (budget no se guarda), con targeting/status
        assert c_us[1] is None
        assert c_us[2] == "Camp US Uno"
        assert c_us[5] is None and c_us[6] is None
        assert c_us[7] == "ENABLED" and c_us[8] == "MANUAL"
        assert c_us[9] is None  # acos_target SIEMPRE None (confirmado en corrida real)
        # jerarquia: ad_group -> campana, keyword/target -> ad_group
        assert ag[1] == c_us[0]
        assert kw[1] == ag[0] and tg[1] == ag[0]
        # keyword sellada por columnas propias; target con nombre JSON compacto
        assert kw[3] == "EXACT" and kw[4] == "zapato" and kw[2] is None
        assert tg[2] == json.dumps(
            [{"type": "asinSameAs", "value": "B0TEST1234"}],
            separators=(",", ":"),
            sort_keys=True,
        )
        # dinero exacto por plataforma
        assert ag[5] == Decimal("0.75") and ag[6] == "USD"
        assert kw[5] == Decimal("0.5") and kw[6] == "USD"
        assert tg[5] == Decimal("0.9") and tg[6] == "USD"
        assert ag_mx[5] == Decimal("15.5") and ag_mx[6] == "MXN"
        assert kw_mx[5] == Decimal("12") and kw_mx[6] == "MXN"
        assert c_mx[8] == "AUTO" and kw_mx[7] == "PAUSED"
        # keyword sin bid: fila ESCRITA con bid y moneda NULL (regla 3)
        assert kw_sin_bid is not None
        assert kw_sin_bid[5] is None and kw_sin_bid[6] is None
        assert kw_sin_bid[10] is not None
        # sello de moneda a nivel DB (antecedente: metric_moneda_de_plataforma
        # solo cubre metricas; para state la validacion vive en _evaluar_perfil)
        assert _monedas_de_plataforma(conn, "amazon_us") == {"USD"}
        assert _monedas_de_plataforma(conn, "amazon_mx") == {"MXN"}
        assert (
            conn.execute(
                "SELECT count(*) FROM ad_entity_state WHERE acos_target IS NOT NULL"
            ).fetchone()[0]
            == 0
        )

        # ------------------------------------------------------------------
        # RE-SYNC (DoD): estado cambiado, entidad nueva, padre movido y
        # keyword con texto divergente (inmutables)
        # ------------------------------------------------------------------
        synced_ag_movido = ag_movido[10]
        synced_c_us = c_us[10]
        synced_kw = kw[10]
        id_campana_9001 = c_us[0]

        res2 = sync_structure(conn, _estructura_resync())

        # 9 escritos (6 us + 3 mx), 3 saltados: padre movido, keyword con
        # keyword_text divergente (inmutable) y su hijo en cascada
        assert res2.ok is True
        assert res2.rows_written == 9
        assert res2.rows_skipped == 3
        assert res2.entidades_nuevas == 2  # campana 9002 + keyword 9203

        run2 = conn.execute(
            "SELECT ok, rows_written, rows_skipped, skip_reason FROM ingest_run WHERE id = %s",
            (res2.run_id,),
        ).fetchone()
        assert run2[0] is True and run2[1] == 9 and run2[2] == 3
        # Regla 9: los rechazos por inmutabilidad quedan afirmados con motivo
        assert "ad group con parent_id distinto al existente (inmutable)" in run2[3]
        assert "keyword sin ad group escrito en esta corrida" in run2[3]
        # [cross-review codex r3] la keyword divergente tambien, y su estado
        # NO se escribio: conserva texto, bid y synced_at de la corrida 1
        assert "keyword con keyword_text/match_type distinto al existente (inmutable)" in run2[3]

        # ad_entity NO se duplica: 9 + exactamente las 2 nuevas
        assert conn.execute("SELECT count(*) FROM ad_entity").fetchone()[0] == 11
        assert conn.execute("SELECT count(*) FROM ingest_run").fetchone()[0] == 2

        c_us2 = _entidad(conn, "amazon_us", "campaign", "9001")
        ag2 = _entidad(conn, "amazon_us", "ad_group", "9101")
        ag_movido2 = _entidad(conn, "amazon_us", "ad_group", "9199")
        kw2 = _entidad(conn, "amazon_us", "keyword", "9201")
        kw_nueva = _entidad(conn, "amazon_us", "keyword", "9203")
        kw_sin_bid2 = _entidad(conn, "amazon_us", "keyword", "9205")

        # el estado nuevo se refleja y synced_at se renueva
        assert c_us2[7] == "PAUSED"
        assert c_us2[10] >= synced_c_us
        assert ag2[5] == Decimal("1.25")
        assert kw_nueva is not None and kw_nueva[3] == "BROAD"
        # la keyword que no traia bid ahora lo trae: NULL -> valor con moneda
        assert kw_sin_bid2[5] == Decimal("0.42") and kw_sin_bid2[6] == "USD"
        # la keyword divergente conserva TODO lo de la corrida 1: el texto
        # viejo ("zapato", no "zapatos"), el bid viejo (0.5, no 0.55) y su
        # synced_at sin renovar (mismo patron que el padre movido, regla 9)
        assert kw2[4] == "zapato"
        assert kw2[5] == Decimal("0.5")
        assert kw2[10] == synced_kw

        # el padre movido conserva TODO su estado de la corrida 1: parent
        # original, bid original y synced_at sin renovar
        assert ag_movido2[1] == id_campana_9001
        assert ag_movido2[5] == Decimal("0.5")
        assert ag_movido2[10] == synced_ag_movido
        # la keyword colgante del padre saltado NO existe (cascada fail-closed)
        assert _entidad(conn, "amazon_us", "keyword", "9204") is None

        assert _monedas_de_plataforma(conn, "amazon_us") == {"USD"}
        assert _monedas_de_plataforma(conn, "amazon_mx") == {"MXN"}

        # ------------------------------------------------------------------
        # FALLO A MITAD DEL TRABAJO: rollback atomico + sello ok=false
        # (hallazgo verifier: la rama de fallo no tenia test). Se simula que
        # la base revienta en medio del loop rompiendo el SQL de estado; sin
        # la rama de sello en fallo, la run quedaria abierta para siempre.
        # ------------------------------------------------------------------
        import app.ads.structure as estructura_modulo

        monkeypatch.setattr(
            estructura_modulo,
            "_SQL_UPSERT_STATE",
            # 5 placeholders = misma firma que el SQL real, para que el fallo
            # sea del SERVIDOR (tabla inexistente), no validacion de cliente
            "INSERT INTO tabla_inexistente_para_fallar VALUES (%s, %s, %s, %s, %s)",
        )
        with pytest.raises(psycopg.errors.UndefinedTable):
            sync_structure(conn, _estructura_resync())
        monkeypatch.undo()

        run_fallida = conn.execute(
            "SELECT ok, rows_written, finished_at, skip_reason FROM ingest_run WHERE id = %s",
            (res2.run_id + 1,),
        ).fetchone()
        assert run_fallida[0] is False
        assert run_fallida[1] is None  # nunca llego a contar
        assert run_fallida[2] is not None  # quedo SELLADA, no abierta
        assert run_fallida[3] is not None  # el fallo dejo rastro
        # rollback atomico: nada de lo que escribio la corrida fallida quedo
        assert conn.execute("SELECT count(*) FROM ad_entity").fetchone()[0] == 11
        assert conn.execute("SELECT count(*) FROM ingest_run").fetchone()[0] == 3
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()
