"""Tests del pipeline de metricas Amazon Ads reporting v3 (`app.ads.reports`).

(a) UNITARIOS: transporte 100% mock (`httpx.MockTransport`, patron de
    tests/test_ads_client.py) para el ciclo crear -> poll -> descargar, y la
    planificacion pura `_planea_filas` para los gates de fila. Corren siempre.
    Las fixtures replican las formas REALES verificadas en vivo el
    2026-08-22: groupBy array, columns strings, filters de keywordType, filas
    con ids NUMERO y sin moneda, descarga gzip (o sin gzip).
(b) INTEGRACION: aplican la migracion entera en un Postgres temporal (patron
    de tests/test_structure_sync.py) y prueban el orquestador `sync_metrics`
    completo contra el mock: corrida 1, re-reporte con el MISMO
    source_report_id (dedupe del indice parcial), moneda cruzada (trigger),
    privilegio negativo de app_ingest y fallo a mitad (rollback atomico).
    Skip automatico solo si ORBIT_TEST_DSN NO esta explicito (fail-closed).

Regla 9: los tests de skip AFIRMAN el skip y su motivo (no solo que no
reviente): quitar la guarda de fecha futura o el pre-check de same_sku tiene
que voltar una asercion.
"""

from __future__ import annotations

import datetime as dt
import gzip
import json
import os
import socket
from collections import Counter
from decimal import Decimal

import httpx
import pytest
from test_schema import SQL, _hay_postgres_local, _test_dsn

from app.ads.client import AdsApiError, AdsClient
from app.ads.config import AdsCredentials
from app.ads.reports import (
    CAMPANAS_CFG,
    KEYWORDS_CFG,
    MAX_BYTES_REPORTE,
    SEARCH_TERMS_CFG,
    SOURCE,
    AdsReportsError,
    _FilaPlano,
    _FilaTermino,
    _planea_filas,
    _planea_filas_terminos,
    _rango_desde_args,
    descargar_filas,
    es_asin_like,
    esperar_reporte,
    main,
    solicitar_reporte,
    sync_metrics,
)
from app.ads.structure import PerfilAds

FAKE_CLIENT_ID = "fake-client-id-123"
FAKE_CLIENT_SECRET = "fake-client-secret-XYZ"
FAKE_REFRESH_TOKEN = "fake-refresh-token-ABC"

# Columnas de metricas verificadas en vivo (mismas en los tres reportes);
# "salesSameSku30d" NO existe: es attributedSalesSameSku30d.
_METRICAS = [
    "impressions",
    "clicks",
    "cost",
    "purchases30d",
    "sales30d",
    "attributedSalesSameSku30d",
]

# Formas REALES de /v2/profiles (corrida 2026-08-22): countryCode y
# accountInfo, sin country/valid.
PERFILES_API = [
    {
        "profileId": 101,
        "countryCode": "US",
        "currencyCode": "USD",
        "accountInfo": {
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
        "accountInfo": {
            "id": "2",
            "type": "seller",
            "name": "Goncloud MX",
            "validPaymentMethod": True,
        },
    },
    {
        # rechazado (vendor): SIN metricas esta corrida, pero CON evidencia
        # en ResultadoSync.perfiles_rechazados (hallazgo reviewer)
        "profileId": 303,
        "countryCode": "US",
        "currencyCode": "USD",
        "accountInfo": {
            "id": "3",
            "type": "vendor",
            "name": "Vendor US",
            "validPaymentMethod": True,
        },
    },
]

# Fila con la forma EXACTA verificada en la descarga real del 2026-08-22
# (campaignId NUMERO, sin moneda en la fila).
FILA_REAL = {
    "date": "2026-08-20",
    "campaignId": 138625505369345,
    "cost": 9.25,
    "purchases30d": 0,
    "sales30d": 118.0,
    "attributedSalesSameSku30d": 0,
    "clicks": 25,
    "impressions": 1660,
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


def _perfil(profile_id: int, country: str) -> PerfilAds:
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


def _token(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"access_token": "fake-access", "expires_in": 3600})


# ---------------------------------------------------------------------------
# (a) UNITARIOS - ciclo API: solicitar / esperar / descargar
# ---------------------------------------------------------------------------


def test_ciclo_completo_crea_poll_descarga():
    """Crear (200 PENDING) -> poll PENDING/PROCESSING/COMPLETED con url ->
    download gzip -> filas parseadas. El body de create_report se afirma
    campo por campo: groupBy ARRAY, columns STRINGS, filters de keywordType
    solo en keywords/targets (con objetos en columns la API da 400)."""
    registro: list[dict] = []
    polls: list[str] = []
    filas = [FILA_REAL]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token(request)
        if request.method == "POST" and request.url.path == "/reporting/reports":
            registro.append(
                {
                    "scope": request.headers.get("Amazon-Advertising-API-Scope"),
                    "body": json.loads(request.content),
                }
            )
            return httpx.Response(200, json={"reportId": "rep-123", "status": "PENDING"})
        if request.method == "GET" and request.url.path == "/reporting/reports/rep-123":
            polls.append("poll")
            estado = ["PENDING", "PROCESSING", "COMPLETED"][min(len(polls), 3) - 1]
            data = {"reportId": "rep-123", "status": estado}
            if estado == "COMPLETED":
                data["url"] = "https://bucket.example.com/rep-123.gz"
            return httpx.Response(200, json=data)
        if request.url.host == "bucket.example.com":
            return httpx.Response(200, content=gzip.compress(json.dumps(filas).encode("utf-8")))
        raise AssertionError(f"llamada inesperada: {request.method} {request.url}")

    client = _cliente(handler)
    perfil = _perfil(101, "US")

    assert (
        solicitar_reporte(client, perfil, CAMPANAS_CFG, dt.date(2026, 8, 20), dt.date(2026, 8, 20))
        == "rep-123"
    )
    assert (
        solicitar_reporte(client, perfil, KEYWORDS_CFG, dt.date(2026, 8, 20), dt.date(2026, 8, 21))
        == "rep-123"
    )
    assert len(registro) == 2

    # campanas: sin filters, groupBy ["campaign"]
    assert registro[0]["scope"] == "101"
    cuerpo_campanas = registro[0]["body"]
    assert cuerpo_campanas["startDate"] == "2026-08-20"
    assert cuerpo_campanas["endDate"] == "2026-08-20"
    cfg_campanas = cuerpo_campanas["configuration"]
    assert cfg_campanas["adProduct"] == "SPONSORED_PRODUCTS"
    assert cfg_campanas["reportTypeId"] == "spCampaigns"
    assert cfg_campanas["groupBy"] == ["campaign"]  # ARRAY, no string
    assert cfg_campanas["timeUnit"] == "DAILY"
    assert cfg_campanas["format"] == "GZIP_JSON"
    assert cfg_campanas["columns"] == ["date", "campaignId", *_METRICAS]
    assert all(isinstance(columna, str) for columna in cfg_campanas["columns"])  # STRINGS
    assert "filters" not in cfg_campanas

    # keywords: spTargeting con filters de keywordType
    cuerpo_keywords = registro[1]["body"]
    assert cuerpo_keywords["startDate"] == "2026-08-20"
    assert cuerpo_keywords["endDate"] == "2026-08-21"
    cfg_keywords = cuerpo_keywords["configuration"]
    assert cfg_keywords["reportTypeId"] == "spTargeting"
    assert cfg_keywords["groupBy"] == ["targeting"]
    assert cfg_keywords["columns"] == [
        "date",
        "campaignId",
        "adGroupId",
        "keywordId",
        *_METRICAS,
    ]
    assert cfg_keywords["filters"] == [
        {"field": "keywordType", "values": ["BROAD", "PHRASE", "EXACT"]}
    ]

    url = esperar_reporte(client, perfil, "rep-123", sleep=lambda segundos: None)
    assert url == "https://bucket.example.com/rep-123.gz"
    assert len(polls) == 3  # PENDING -> PROCESSING -> COMPLETED

    assert descargar_filas(client, url) == filas


def test_body_reporte_search_terms():
    """El 4to reporte (1.4): reportTypeId "spSearchTerm" (SINGULAR; la forma
    la verifico EN VIVO el sistema viejo goncloud-MCP-2, re-sellado contra
    la API en la corrida real de 1.5), groupBy ["searchTerm"], columns
    STRINGS subconjunto de las verificadas, SIN filters (solo spTargeting
    los tiene) y el resto de la configuracion igual a los otros cfg."""
    registro: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token(request)
        if request.method == "POST" and request.url.path == "/reporting/reports":
            registro.append(
                {
                    "scope": request.headers.get("Amazon-Advertising-API-Scope"),
                    "body": json.loads(request.content),
                }
            )
            return httpx.Response(200, json={"reportId": "rep-st", "status": "PENDING"})
        raise AssertionError(f"llamada inesperada: {request.method} {request.url}")

    client = _cliente(handler)
    perfil = _perfil(101, "US")

    assert (
        solicitar_reporte(
            client, perfil, SEARCH_TERMS_CFG, dt.date(2026, 8, 20), dt.date(2026, 8, 20)
        )
        == "rep-st"
    )
    assert len(registro) == 1

    assert registro[0]["scope"] == "101"
    cuerpo = registro[0]["body"]
    assert cuerpo["startDate"] == "2026-08-20"
    assert cuerpo["endDate"] == "2026-08-20"
    cfg = cuerpo["configuration"]
    assert cfg["adProduct"] == "SPONSORED_PRODUCTS"
    assert cfg["reportTypeId"] == "spSearchTerm"  # SINGULAR, no "spSearchTerms"
    assert cfg["groupBy"] == ["searchTerm"]  # ARRAY, no string
    assert cfg["timeUnit"] == "DAILY"
    assert cfg["format"] == "GZIP_JSON"
    assert cfg["columns"] == [
        "date",
        "adGroupId",
        "searchTerm",
        "clicks",
        "cost",
        "purchases7d",
        "sales7d",
    ]
    assert all(isinstance(columna, str) for columna in cfg["columns"])  # STRINGS
    assert "filters" not in cfg


def test_reporte_failed_da_error_con_failure_reason():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token(request)
        return httpx.Response(
            200, json={"reportId": "rep-x", "status": "FAILED", "failureReason": "INTERNAL_ERROR"}
        )

    client = _cliente(handler)
    with pytest.raises(AdsReportsError) as excinfo:
        esperar_reporte(client, _perfil(101, "US"), "rep-x", sleep=lambda segundos: None)
    assert "FAILED" in str(excinfo.value)
    assert "INTERNAL_ERROR" in str(excinfo.value)


def test_poll_agotado_da_error_claro():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token(request)
        return httpx.Response(200, json={"reportId": "rep-9", "status": "PROCESSING"})

    client = _cliente(handler)
    esperas: list[float] = []
    with pytest.raises(AdsReportsError) as excinfo:
        esperar_reporte(
            client, _perfil(101, "US"), "rep-9", intentos=2, espera=5.0, sleep=esperas.append
        )
    assert "2 intentos" in str(excinfo.value)
    assert "rep-9" in str(excinfo.value)
    # durmio ENTRE los intentos, no despues del ultimo
    assert esperas == [5.0]


def test_descarga_sin_gzip_tambien_parsea():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token(request)
        if request.url.host == "bucket.example.com":
            return httpx.Response(200, content=json.dumps([FILA_REAL]).encode("utf-8"))
        raise AssertionError(f"llamada inesperada: {request.method} {request.url}")

    client = _cliente(handler)
    assert descargar_filas(client, "https://bucket.example.com/plano.json") == [FILA_REAL]


def test_descarga_que_no_es_array_da_error_claro():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token(request)
        return httpx.Response(200, content=json.dumps({"date": "2026-08-20"}).encode("utf-8"))

    client = _cliente(handler)
    with pytest.raises(AdsReportsError) as excinfo:
        descargar_filas(client, "https://bucket.example.com/dict.json")
    assert "array JSON" in str(excinfo.value)


def test_reporte_con_file_size_anormal_da_error_antes_de_descargar():
    """[hallazgo codex] fileSize del poll (campo verificado en vivo) > tope ->
    fail-closed ANTES de meter el gzip anormal a memoria."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token(request)
        return httpx.Response(
            200,
            json={
                "reportId": "rep-grande",
                "status": "COMPLETED",
                "url": "https://bucket.example.com/rep-grande.gz",
                "fileSize": MAX_BYTES_REPORTE + 1,
            },
        )

    client = _cliente(handler)
    with pytest.raises(AdsReportsError) as excinfo:
        esperar_reporte(client, _perfil(101, "US"), "rep-grande", sleep=lambda segundos: None)
    assert "fileSize" in str(excinfo.value)
    assert "fail-closed" in str(excinfo.value)


def test_descarga_sobre_el_tope_de_bytes_da_error(monkeypatch):
    """[hallazgo codex] Segunda guarda: el contenido REAL excede el tope
    aunque el poll no trajera fileSize (o mintiera). El tope se achica via
    monkeypatch para no alocar 512 MB en el test; la logica es la misma."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token(request)
        return httpx.Response(200, content=b"x" * 64)

    import app.ads.reports as reports_modulo

    monkeypatch.setattr(reports_modulo, "MAX_BYTES_REPORTE", 16)
    client = _cliente(handler)
    with pytest.raises(AdsReportsError) as excinfo:
        descargar_filas(client, "https://bucket.example.com/gordo.json")
    assert "excede el tope" in str(excinfo.value)


# ---------------------------------------------------------------------------
# (a) UNITARIOS - planificacion pura (gates de fila, regla 9)
# ---------------------------------------------------------------------------


def test_plan_filas_fecha_futura_se_salta_con_motivo():
    """Regla 9: el skip se AFIRMA con su motivo. Sin la guarda, la fila del
    manana entraria al INSERT y romperia el invariante observado >= hecho en
    silencio (la base NO tiene CHECK: la migracion lo declara responsabilidad
    de la ingesta)."""
    hoy = dt.date(2026, 8, 21)
    filas = [
        {"date": "2026-08-20", "campaignId": 9001, "cost": 9.25},
        {"date": "2026-08-22", "campaignId": 9001, "cost": 1.0},
    ]

    plan, skips = _planea_filas(
        CAMPANAS_CFG,
        filas,
        hoy=hoy,
        fecha_ini=dt.date(2026, 8, 20),
        fecha_fin=dt.date(2026, 8, 20),
    )

    assert [fila.metric_date for fila in plan] == [dt.date(2026, 8, 20)]
    assert skips == Counter({"fila con metric_date futura": 1})


def test_plan_filas_fecha_fuera_del_rango_se_salta():
    """Regla 9 (hallazgo codex): una descarga equivocada (fila de anteayer en
    un reporte de ayer) no puede contaminar fechas ajenas al rango pedido.
    Sin la guarda, la fila entraria al INSERT y este test voltea."""
    filas = [
        {"date": "2026-08-20", "campaignId": 9001, "cost": 9.25},
        {"date": "2026-08-19", "campaignId": 9001, "cost": 1.0},
    ]

    plan, skips = _planea_filas(
        CAMPANAS_CFG,
        filas,
        hoy=dt.date(2026, 8, 21),
        fecha_ini=dt.date(2026, 8, 20),
        fecha_fin=dt.date(2026, 8, 20),
    )

    assert [fila.metric_date for fila in plan] == [dt.date(2026, 8, 20)]
    assert skips == Counter({"fila con metric_date fuera del rango solicitado": 1})


def test_plan_filas_same_sku_mayor_que_ad_revenue_se_salta():
    """Regla 9: sin el pre-check, el INSERT llegaria a la base y el CHECK
    metric_same_sku_cabe abortaria el LOTE entero."""
    filas = [
        {
            "date": "2026-08-20",
            "keywordId": 9201,
            "sales30d": 10.0,
            "attributedSalesSameSku30d": 12.0,
        }
    ]

    plan, skips = _planea_filas(
        KEYWORDS_CFG,
        filas,
        hoy=dt.date(2026, 8, 21),
        fecha_ini=dt.date(2026, 8, 20),
        fecha_fin=dt.date(2026, 8, 20),
    )

    assert plan == []
    assert skips == Counter({"fila con revenue_same_sku > ad_revenue": 1})


def test_plan_filas_sin_ninguna_metrica_se_salta():
    """Regla 9 (hallazgo codex): una fila sin NINGUN dato NO se escribe -- una
    observacion vacia posterior llegaria a v_metric_latest y taparia datos
    completos anteriores. Sin la guarda, la fila entraria con todo NULL y este
    test voltea."""
    filas = [
        {"date": "2026-08-20", "campaignId": 9001},
    ]

    plan, skips = _planea_filas(
        CAMPANAS_CFG,
        filas,
        hoy=dt.date(2026, 8, 21),
        fecha_ini=dt.date(2026, 8, 20),
        fecha_fin=dt.date(2026, 8, 20),
    )

    assert plan == []
    assert skips == Counter({"fila sin ninguna metrica": 1})


def test_plan_filas_metricas_ausentes_none_y_tipos_exactos():
    hoy = dt.date(2026, 8, 21)
    filas = [
        # ids NUMERO de la API y contadores como float enteros -> int
        {
            "date": "2026-08-20",
            "campaignId": 138625505369345,
            "cost": 9.25,
            "sales30d": 118.0,
            "attributedSalesSameSku30d": 0,
            "impressions": 1660.0,
            "clicks": 25,
            "purchases30d": 3,
        },
        # metrica parcial (solo clicks): fila VALIDA, el resto None (regla 3)
        {"date": "2026-08-20", "campaignId": 9001, "clicks": 4},
        # sin id, con date invalida, con contador fraccionario y sin NINGUNA
        # metrica: skips
        {"date": "2026-08-20"},
        {"date": "viernes", "campaignId": 9001},
        {"date": "2026-08-20", "campaignId": 9001, "purchases30d": 2.5},
        {"date": "2026-08-20", "campaignId": 9001},
    ]

    plan, skips = _planea_filas(
        CAMPANAS_CFG,
        filas,
        hoy=hoy,
        fecha_ini=dt.date(2026, 8, 20),
        fecha_fin=dt.date(2026, 8, 20),
    )

    assert [fila.external_id for fila in plan] == ["138625505369345", "9001"]
    completa = plan[0]
    assert isinstance(completa, _FilaPlano)
    assert completa.cost == Decimal("9.25")  # Decimal via str, jamas float
    assert completa.ad_revenue == Decimal("118.0")
    assert completa.revenue_same_sku == Decimal("0")
    assert (completa.impressions, completa.clicks, completa.orders) == (1660, 25, 3)
    parcial = plan[1]
    assert (
        parcial.cost,
        parcial.ad_revenue,
        parcial.revenue_same_sku,
        parcial.impressions,
        parcial.clicks,
        parcial.orders,
    ) == (None, None, None, None, 4, None)
    assert skips == Counter(
        {
            "fila de campanas sin campaignId": 1,
            "fila con date invalida": 1,
            "fila de campanas con metrica no numerica o fraccionaria": 1,
            "fila sin ninguna metrica": 1,
        }
    )


def test_es_asin_like_clasificador():
    """DoD del clasificador (el unico que llena la columna NOT NULL SIN
    DEFAULT): ASIN real, ASIN en minusculas, termino normal, "b0" embebido,
    mas los bordes de longitud, prefijo, strip y ASCII-strict."""
    assert es_asin_like("B0AB12CD34") is True  # ASIN real (mayusculas)
    assert es_asin_like("b0ab12cd34") is True  # minusculas: case-insensitive
    assert es_asin_like("tenis para correr") is False  # termino normal
    assert es_asin_like("usb0cable") is False  # "b0" embebido NO es ASIN
    assert es_asin_like("B0AB12CD3") is False  # 9 caracteres
    assert es_asin_like("B0AB12CD345") is False  # 11 caracteres
    assert es_asin_like("X0AB12CD34") is False  # no empieza con "B0"
    assert es_asin_like("") is False
    assert es_asin_like(" B0AB12CD34 ") is True  # espacios extremos (strip)
    assert es_asin_like("B0ÁBCDEFGH") is False  # ASCII estricto: unicode NO pasa
    # [hallazgo verifier] sin re.ASCII, IGNORECASE case-foldea U+212A (Kelvin)
    # a "k" y el termino pasaba como ASIN: el criterio sellado es ASCII-strict
    assert es_asin_like("b0" + "K" * 8) is False  # K = U+212A, foldea a "k"


def test_plan_terminos_clasifica_y_gates():
    """Regla 9: los skips del plan de terminos se AFIRMAN con su motivo
    (vocabulario CERRADO: mismas claves de fecha/metrica que _planea_filas
    + las dos propias de terminos). La clasificacion is_asin_like corre EN
    la planificacion: toda fila planificada llega clasificada."""
    hoy = dt.date(2026, 8, 21)
    filas = [
        # validas: mezcla de clasificacion
        {
            "date": "2026-08-20",
            "adGroupId": 9101,
            "searchTerm": "B0AB12CD34",
            "clicks": 5,
            "cost": 1.25,
            "purchases7d": 0,
            "sales7d": 0,
        },
        {
            "date": "2026-08-20",
            "adGroupId": 9101,
            "searchTerm": "b0aaaa1111",
            "clicks": 2,
            "cost": 0.5,
        },
        {
            "date": "2026-08-20",
            "adGroupId": 9101,
            "searchTerm": "tenis para correr",
            "clicks": 9,
            "sales7d": 200.0,
            "purchases7d": 3,
        },
        {"date": "2026-08-20", "adGroupId": 9101, "searchTerm": "usb0cable", "clicks": 1},
        # gates: cada fila salta por exactamente un motivo
        {"date": "2026-08-20", "searchTerm": "huerfano", "clicks": 1},  # sin adGroupId
        {"date": "2026-08-20", "adGroupId": 9101, "clicks": 1},  # searchTerm ausente
        {
            "date": "2026-08-20",
            "adGroupId": 9101,
            "searchTerm": "   ",
            "clicks": 1,
        },  # solo espacios
        {"date": "2026-08-22", "adGroupId": 9101, "searchTerm": "futuro", "clicks": 1},
        {"date": "2026-08-19", "adGroupId": 9101, "searchTerm": "anteayer", "clicks": 1},
        {"date": "2026-08-20", "adGroupId": 9101, "searchTerm": "fraccion", "purchases7d": 2.5},
        {"date": "2026-08-20", "adGroupId": 9101, "searchTerm": "vacio"},
    ]

    plan, skips = _planea_filas_terminos(
        SEARCH_TERMS_CFG,
        filas,
        hoy=hoy,
        fecha_ini=dt.date(2026, 8, 20),
        fecha_fin=dt.date(2026, 8, 20),
    )

    assert [fila.search_term for fila in plan] == [
        "B0AB12CD34",
        "b0aaaa1111",
        "tenis para correr",
        "usb0cable",
    ]
    # la clasificacion viaja EN el plan: sin ella, el INSERT romperia la NOT
    # NULL (la columna NO tiene default a proposito)
    assert [fila.is_asin_like for fila in plan] == [True, True, False, False]
    assert all(isinstance(fila, _FilaTermino) for fila in plan)
    # tipos exactos: dinero Decimal via str, contadores int, ausente -> None
    assert plan[0].external_id == "9101"
    assert plan[0].cost == Decimal("1.25")
    assert (plan[0].clicks, plan[0].orders, plan[0].ad_revenue) == (5, 0, Decimal("0"))
    assert (plan[1].cost, plan[1].ad_revenue) == (Decimal("0.5"), None)
    assert (plan[2].ad_revenue, plan[2].orders, plan[2].cost) == (Decimal("200.0"), 3, None)
    assert skips == Counter(
        {
            "fila de search_terms sin adGroupId": 1,
            "fila de search_terms sin searchTerm": 2,
            "fila con metric_date futura": 1,
            "fila con metric_date fuera del rango solicitado": 1,
            "fila de search_terms con metrica no numerica o fraccionaria": 1,
            "fila sin ninguna metrica": 1,
        }
    )


def test_plan_terminos_fusiona_claves_duplicadas():
    """Regla 9 (grano multiple verificado EN VIVO, sondeo 1.5 del 2026-08-23:
    US, reporte del 2026-08-19: 113 filas -> 110 claves, 3 repetidas x2):
    el MISMO (adGroupId, searchTerm, date) llega en varias filas porque el
    termino convierte via keywords distintas del mismo ad group. El
    first-wins anterior perdia las metricas de la segunda fila -- un orders
    subestimado puede generar un NEGATIVE_EXACT erroneo en el motor 2.3 --
    asi que las filas validas de la misma clave se FUSIONAN sumando
    metricas: los aportes por keyword al mismo hecho son disjuntos (sumar
    no inventa dato) hacia la identidad sellada que el motor consume."""
    filas = [
        {
            "date": "2026-08-20",
            "adGroupId": 9101,
            "searchTerm": "arras for wedding ceremony",
            "clicks": 5,
            "cost": 1.25,
            "sales7d": 100.0,
        },
        # misma clave (via otra keyword del mismo ad group): se FUSIONA
        {
            "date": "2026-08-20",
            "adGroupId": 9101,
            "searchTerm": "arras for wedding ceremony",
            "clicks": 7,
            "cost": 2.0,
        },
    ]

    plan, skips = _planea_filas_terminos(
        SEARCH_TERMS_CFG,
        filas,
        hoy=dt.date(2026, 8, 21),
        fecha_ini=dt.date(2026, 8, 20),
        fecha_fin=dt.date(2026, 8, 20),
    )

    # UNA sola fila en el plan: la segunda se agrego a la primera
    assert len(plan) == 1
    fusionada = plan[0]
    assert isinstance(fusionada, _FilaTermino)
    assert fusionada.cost == Decimal("3.25")  # 1.25 + 2.0, Decimal via str
    assert fusionada.clicks == 12  # 5 + 7
    # presente + None: None aporta nada
    assert fusionada.ad_revenue == Decimal("100.0")
    # None + None: metrica que nadie trae queda None (regla 3)
    assert fusionada.orders is None
    # la fila absorbida queda contada con la clave de vocabulario cerrado
    assert skips == Counter({"fila agregada por clave duplicada en el reporte": 1})


# ---------------------------------------------------------------------------
# (a) UNITARIOS - CLI: fail-closed y rango de fechas
# ---------------------------------------------------------------------------


def test_main_sin_orbit_dsn_ingest_falla_cerrado(monkeypatch, capsys):
    monkeypatch.delenv("ORBIT_DSN_INGEST", raising=False)
    assert main([]) == 2
    assert "ORBIT_DSN_INGEST" in capsys.readouterr().err


def test_main_rechaza_rango_mayor_a_31_dias(monkeypatch, capsys):
    monkeypatch.delenv("ORBIT_DSN_INGEST", raising=False)
    assert main(["--fecha", "2026-08-01", "--fecha-fin", "2026-09-01"]) == 2
    error = capsys.readouterr().err
    assert "32 dias" in error
    assert "31" in error


def test_rango_desde_args_casos():
    hoy = dt.date(2026, 8, 22)
    # default: ayer UTC, un solo dia
    assert _rango_desde_args(None, None, hoy) == (dt.date(2026, 8, 21), dt.date(2026, 8, 21))
    # --fecha sola: ese dia
    assert _rango_desde_args("2026-08-01", None, hoy) == (dt.date(2026, 8, 1), dt.date(2026, 8, 1))
    # 31 dias exactos: el tope de la API, permitido
    assert _rango_desde_args("2026-08-01", "2026-08-31", hoy) == (
        dt.date(2026, 8, 1),
        dt.date(2026, 8, 31),
    )

    with pytest.raises(ValueError) as excinfo:
        _rango_desde_args("2026-08-01", "2026-09-01", hoy)
    assert "32 dias" in str(excinfo.value)
    with pytest.raises(ValueError) as excinfo:
        _rango_desde_args("2026-08-05", "2026-08-01", hoy)
    assert "invertido" in str(excinfo.value)
    with pytest.raises(ValueError) as excinfo:
        _rango_desde_args("20-08-2026", None, hoy)
    assert "YYYY-MM-DD" in str(excinfo.value)
    with pytest.raises(ValueError) as excinfo:
        _rango_desde_args(None, "2026-08-01", hoy)
    assert "requiere --fecha" in str(excinfo.value)


def test_sql_del_modulo_parsea_como_postgres():
    """Guarda barata que SIEMPRE corre (patron test_structure_sync): la
    sintaxis SQL del modulo es Postgres valida segun el parser real,
    incluyendo el ON CONFLICT contra el indice parcial. Import directo (NO
    importorskip): pglast es dev-dep declarada y su desaparicion debe FALLAR
    ruidosamente, no saltar en silencio."""
    import pglast

    import app.ads.reports as reports_modulo

    for nombre in (
        "_SQL_ENTIDADES",
        "_SQL_INSERT_METRICA",
        "_SQL_INSERT_TERMINO",
        "_SQL_FECHA_HOY",
    ):
        sql = getattr(reports_modulo, nombre).replace("%s", "NULL")
        assert pglast.parse_sql(sql), f"{nombre} no parseo"


# ---------------------------------------------------------------------------
# (b) INTEGRACION - patron de test_structure_sync con fail-closed del DSN
# ---------------------------------------------------------------------------

# Fail-closed: si ORBIT_TEST_DSN esta EXPLICITO (CI), un Postgres inalcanzable
# debe FALLAR el test, no skipearlo en silencio. El skip automatico queda solo
# para corridas locales sin DSN configurado.
_DSN_EXPLICITO = bool(os.environ.get("ORBIT_TEST_DSN"))


@pytest.mark.skipif(
    not _DSN_EXPLICITO and not _hay_postgres_local(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_pipeline_metricas_en_vivo(monkeypatch):
    """Corrida completa del orquestador contra la migracion real:

    - CORRIDA 1: filas coherentes en ads_metric_observation (fechas, Decimal
      exacto, moneda sellada por plataforma, source_report_id e ingest_run_id).
    - RE-REPORTE (DoD): misma source_report_id + filas nuevas -> viejas todas
      rows_skipped "duplicada", lote mixto cuadra.
    - Moneda cruzada: el TRIGGER metric_moneda_de_plataforma rechaza (no es un
      CHECK simple de tabla).
    - PRIVILEGIO NEGATIVO (DoD): app_ingest no puede escribir decisions.
    - Fallo a mitad: rollback atomico + sello ok=false, nada a medias.
    - Cero perfiles aceptados (grok): lista vacia o solo rechazados -> run
      sellada ok=false CON motivo, jamas ok=true con 0 filas.
    - Fallo de fase API (codex): create 400 -> run sellada ok=false con el
      error y re-raise: toda invocacion deja fila auditable.
    """
    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql as pgsql

    dsn = _test_dsn()
    db = f"orbit_reports_test_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        conn.execute(SQL)  # la migracion entera

        # seed de estructura (en produccion la escribe app.ads.structure):
        # campana/ad group/keyword/target en US y campana en MX
        def entidad(platform, kind, external, parent=None, **extra):
            return conn.execute(
                "INSERT INTO ad_entity (platform, kind, external_id, parent_id,"
                " match_type, keyword_text) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (
                    platform,
                    kind,
                    external,
                    parent,
                    extra.get("match_type"),
                    extra.get("keyword_text"),
                ),
            ).fetchone()[0]

        camp_us = entidad("amazon_us", "campaign", "9001")
        camp_us2 = entidad("amazon_us", "campaign", "9002")
        ag_us = entidad("amazon_us", "ad_group", "9101", parent=camp_us)
        kw_us = entidad(
            "amazon_us", "keyword", "9201", parent=ag_us, match_type="EXACT", keyword_text="zapato"
        )
        tg_us = entidad("amazon_us", "product_target", "9301", parent=ag_us)
        camp_mx = entidad("amazon_mx", "campaign", "7001")

        ahora = dt.datetime.now(dt.UTC)
        ayer = ahora.date() - dt.timedelta(days=1)
        manana = ahora.date() + dt.timedelta(days=1)
        medianoche_ayer = dt.datetime(ayer.year, ayer.month, ayer.day, tzinfo=dt.UTC)

        corrida = {"n": 1}

        def _filas(reporte: str) -> list[dict]:
            """Filas por reporte/corrida (ids NUMERO, formas verificadas)."""
            if reporte == "R-CAMP-101-B":
                # corrida 3: RE-LECTURA del mismo dia con reporte NUEVO ->
                # observacion NUEVA con costo corregido (correccion tipo
                # clawback). La bitemporalidad es el corazon de la regla 5.
                return [
                    {
                        "date": str(ayer),
                        "campaignId": 9001,
                        "cost": 10.5,
                        "purchases30d": 0,
                        "sales30d": 120.0,
                        "attributedSalesSameSku30d": 0,
                        "clicks": 25,
                        "impressions": 1660,
                    }
                ]
            if reporte.endswith("-B"):
                # el resto de los reportes de la corrida 3 reenvia sus filas
                reporte = reporte.removesuffix("-B")
            if reporte == "R-CAMP-101" and corrida["n"] == 1:
                return [
                    {
                        "date": str(ayer),
                        "campaignId": 9001,
                        "cost": 9.25,
                        "purchases30d": 0,
                        "sales30d": 118.0,
                        "attributedSalesSameSku30d": 0,
                        "clicks": 25,
                        "impressions": 1660,
                    },
                    # campana que NO esta en ad_entity: skip con motivo
                    {
                        "date": str(ayer),
                        "campaignId": 9999,
                        "cost": 1.0,
                        "clicks": 1,
                        "impressions": 2,
                    },
                ]
            if reporte == "R-CAMP-101":
                # corrida 2+: las mismas filas validas (dedupe) + UNA nueva
                return [
                    {
                        "date": str(ayer),
                        "campaignId": 9001,
                        "cost": 9.25,
                        "purchases30d": 0,
                        "sales30d": 118.0,
                        "attributedSalesSameSku30d": 0,
                        "clicks": 25,
                        "impressions": 1660,
                    },
                    {
                        "date": str(ayer),
                        "campaignId": 9002,
                        "cost": 7.5,
                        "clicks": 10,
                        "impressions": 300,
                    },
                ]
            if reporte == "R-KW-101":
                base = [
                    {
                        "date": str(ayer),
                        "campaignId": 9001,
                        "adGroupId": 9101,
                        "keywordId": 9201,
                        "cost": 0.5,
                        "purchases30d": 1,
                        "sales30d": 20.0,
                        "attributedSalesSameSku30d": 20.0,
                        "clicks": 2,
                        "impressions": 10,
                    }
                ]
                if corrida["n"] == 1:
                    base += [
                        # same_sku > ad_revenue: skip (el CHECK abortaria el lote)
                        {
                            "date": str(ayer),
                            "campaignId": 9001,
                            "adGroupId": 9101,
                            "keywordId": 9201,
                            "cost": 0.1,
                            "sales30d": 5.0,
                            "attributedSalesSameSku30d": 7.0,
                        },
                        # fecha futura: skip (guarda de la ingesta)
                        {
                            "date": str(manana),
                            "campaignId": 9001,
                            "adGroupId": 9101,
                            "keywordId": 9201,
                            "cost": 0.2,
                        },
                    ]
                return base
            if reporte == "R-TG-101":
                # keywordId ES el targetId (verificado en vivo)
                return [
                    {
                        "date": str(ayer),
                        "campaignId": 9001,
                        "adGroupId": 9101,
                        "keywordId": 9301,
                        "cost": 0.9,
                        "purchases30d": 0,
                        "sales30d": 3.0,
                        "attributedSalesSameSku30d": 1.0,
                        "clicks": 1,
                        "impressions": 5,
                    }
                ]
            if reporte == "R-CAMP-202":
                return [
                    {
                        "date": str(ayer),
                        "campaignId": 7001,
                        "cost": 150.0,
                        "purchases30d": 2,
                        "sales30d": 600.0,
                        "attributedSalesSameSku30d": 600.0,
                        "clicks": 40,
                        "impressions": 900,
                    }
                ]
            return []

        # [fix grok] el mock puede degenerar /v2/profiles (vacio / solo
        # rechazados) y [fix codex] puede romper la fase API (create 400)
        perfiles_respuesta = {"lista": PERFILES_API}
        fallo_create = {"on": False}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "api.amazon.com":
                return _token(request)
            if request.url.path == "/v2/profiles":
                return httpx.Response(200, json=perfiles_respuesta["lista"])
            if request.method == "POST" and request.url.path == "/reporting/reports":
                if fallo_create["on"]:
                    # 400 en create: AdsApiError del cliente (POST no
                    # idempotente, fail-closed sin retries)
                    return httpx.Response(400, json={"code": 400})
                cfg = json.loads(request.content)["configuration"]
                scope = request.headers.get("Amazon-Advertising-API-Scope")
                if cfg["reportTypeId"] == "spCampaigns":
                    reporte = f"R-CAMP-{scope}"
                elif cfg["reportTypeId"] == "spSearchTerm":
                    # search terms NO trae filters (cae al return [] final:
                    # este test sella la tabla de metricas, la de terminos
                    # tiene su propio test en vivo)
                    reporte = f"R-ST-{scope}"
                elif "TARGETING_EXPRESSION" in cfg["filters"][0]["values"]:
                    reporte = f"R-TG-{scope}"
                else:
                    reporte = f"R-KW-{scope}"
                # corrida 3: ids NUEVOS (sufijo -B) -> reportes distintos del
                # mismo dia (NO dedupe: observaciones coexisten, regla 5)
                if corrida["n"] >= 3:
                    reporte += "-B"
                return httpx.Response(200, json={"reportId": reporte, "status": "PENDING"})
            if request.url.path.startswith("/reporting/reports/"):
                reporte = request.url.path.rsplit("/", 1)[1]
                return httpx.Response(
                    200,
                    json={
                        "reportId": reporte,
                        "status": "COMPLETED",
                        "url": f"https://bucket.example.com/{reporte}.json",
                    },
                )
            reporte = request.url.path.removesuffix(".json").removeprefix("/")
            return httpx.Response(
                200, content=gzip.compress(json.dumps(_filas(reporte)).encode("utf-8"))
            )

        client = _cliente(handler)

        # ------------------------------------------------------------------
        # CORRIDA 1
        # ------------------------------------------------------------------
        res1 = sync_metrics(conn, client, fecha_ini=ayer, fecha_fin=ayer, sleep=lambda s: None)

        # 4 escritas (camp/kw/tg US + camp MX) y 3 saltadas (entidad ausente,
        # same_sku > ad_revenue, fecha futura); los reportes MX sin entidades
        assert res1.ok is True
        assert res1.rows_written == 4
        assert res1.rows_skipped == 3
        # el perfil vendor (303) queda como evidencia, sin generar reportes
        assert [p.profile_id for p in res1.perfiles_rechazados] == [303]
        assert all(resumen.profile_id != 303 for resumen in res1.reportes)
        assert [resumen.report_id for resumen in res1.reportes] == [
            "R-CAMP-101",
            "R-KW-101",
            "R-TG-101",
            "R-ST-101",
            "R-CAMP-202",
            "R-KW-202",
            "R-TG-202",
            "R-ST-202",
        ]

        run1 = conn.execute(
            "SELECT source, ok, rows_written, rows_skipped, skip_reason, finished_at"
            " FROM ingest_run WHERE id = %s",
            (res1.run_id,),
        ).fetchone()
        assert run1[0] == SOURCE
        assert (run1[1], run1[2], run1[3]) == (True, 4, 3)
        assert run1[5] is not None
        # Regla 9: cada skip afirmado con su motivo en la run sellada (claves
        # de vocabulario CERRADO: el detalle por fila va al log, no al contador)
        assert "1x entidad ausente en ad_entity (campaign)" in run1[4]
        assert "1x fila con revenue_same_sku > ad_revenue" in run1[4]
        assert "1x fila con metric_date futura" in run1[4]

        # filas coherentes en ads_metric_observation
        _SQL_METRICAS = (
            "SELECT m.ad_entity_id, e.kind, m.metric_date::text, m.observed_at,"
            " m.metric_currency, m.cost, m.ad_revenue, m.revenue_same_sku,"
            " m.impressions, m.clicks, m.orders, m.source_report_id, m.ingest_run_id"
            " FROM ads_metric_observation m JOIN ad_entity e ON e.id = m.ad_entity_id"
        )
        metricas = conn.execute(_SQL_METRICAS).fetchall()
        assert len(metricas) == 4

        fila_camp = next(m for m in metricas if m[0] == camp_us)
        assert fila_camp[1] == "campaign"
        assert fila_camp[2] == str(ayer)
        assert fila_camp[3] >= medianoche_ayer  # observado >= hecho (invariante)
        assert fila_camp[4] == "USD"  # moneda sellada por plataforma
        assert fila_camp[5] == Decimal("9.25")  # Decimal exacto, sin float
        assert fila_camp[6] == Decimal("118.0")
        assert fila_camp[7] == Decimal("0")
        assert (fila_camp[8], fila_camp[9], fila_camp[10]) == (1660, 25, 0)
        assert fila_camp[11] == "R-CAMP-101"
        assert fila_camp[12] == res1.run_id

        fila_kw = next(m for m in metricas if m[0] == kw_us)
        assert fila_kw[1] == "keyword"
        assert (fila_kw[5], fila_kw[6], fila_kw[10]) == (Decimal("0.5"), Decimal("20.0"), 1)
        assert fila_kw[11] == "R-KW-101"

        fila_tg = next(m for m in metricas if m[0] == tg_us)
        assert fila_tg[1] == "product_target"  # keywordId del reporte == targetId
        assert fila_tg[4] == "USD" and fila_tg[5] == Decimal("0.9")
        assert fila_tg[11] == "R-TG-101"

        fila_mx = next(m for m in metricas if m[0] == camp_mx)
        assert fila_mx[4] == "MXN"  # la moneda es la del perfil, no la de la fila
        assert fila_mx[5] == Decimal("150.0")
        assert fila_mx[11] == "R-CAMP-202"

        # ------------------------------------------------------------------
        # RE-REPORTE (DoD): misma source_report_id, filas nuevas + las mismas
        # ------------------------------------------------------------------
        corrida["n"] = 2
        res2 = sync_metrics(conn, client, fecha_ini=ayer, fecha_fin=ayer, sleep=lambda s: None)

        # lote mixto: 1 nueva + 4 duplicadas (las 4 validas de la corrida 1)
        assert res2.ok is True
        assert res2.rows_written == 1
        assert res2.rows_skipped == 4
        for reporte in ("R-CAMP-101", "R-KW-101", "R-TG-101", "R-CAMP-202"):
            assert f"1x fila duplicada del reporte {reporte}" in res2.skip_reason

        assert conn.execute("SELECT count(*) FROM ads_metric_observation").fetchone()[0] == 5
        # append-only: la fila vieja conserva SU corrida y su valor; la nueva
        # lleva la corrida 2
        vieja = conn.execute(
            "SELECT ingest_run_id, cost FROM ads_metric_observation"
            " WHERE ad_entity_id = %s AND metric_date = %s",
            (camp_us, ayer),
        ).fetchone()
        assert vieja == (res1.run_id, Decimal("9.25"))
        nueva = conn.execute(
            "SELECT ingest_run_id, cost FROM ads_metric_observation"
            " WHERE ad_entity_id = %s AND metric_date = %s",
            (camp_us2, ayer),
        ).fetchone()
        assert nueva == (res2.run_id, Decimal("7.5"))

        # ------------------------------------------------------------------
        # BITEMPORALIDAD (hallazgo reviewer, mandatorio): un reporte NUEVO del
        # MISMO dia NO se deduplica (el dedupe es por source_report_id) -- la
        # correccion coexiste con la original y v_metric_latest colapsa a la
        # mas nueva. Una regresion que "optimizara" deduplicando por
        # (entidad, fecha) a secas perderia las correcciones por clawback.
        # ------------------------------------------------------------------
        corrida["n"] = 3
        res3 = sync_metrics(conn, client, fecha_ini=ayer, fecha_fin=ayer, sleep=lambda s: None)
        assert res3.ok is True
        assert res3.rows_written == 4  # camp_us corregida + kw + tg + camp_mx
        assert res3.rows_skipped == 0  # ids NUEVOS: nada deduplicado
        assert conn.execute("SELECT count(*) FROM ads_metric_observation").fetchone()[0] == 9

        # el par (entidad, fecha) tiene DOS observaciones con reportes distintos
        observaciones = conn.execute(
            "SELECT source_report_id, cost FROM ads_metric_observation"
            " WHERE ad_entity_id = %s AND metric_date = %s ORDER BY observed_at",
            (camp_us, ayer),
        ).fetchall()
        assert observaciones == [("R-CAMP-101", Decimal("9.25")), ("R-CAMP-101-B", Decimal("10.5"))]

        # v_metric_latest colapsa a la observacion MAS NUEVA (la correccion)
        latest = conn.execute(
            "SELECT cost FROM v_metric_latest WHERE ad_entity_id = %s AND metric_date = %s",
            (camp_us, ayer),
        ).fetchone()
        assert latest == (Decimal("10.5"),)

        # ------------------------------------------------------------------
        # MONEDA CRUZADA: es el TRIGGER metric_moneda_de_plataforma el que
        # rechaza con ERRCODE check_violation (no un CHECK simple de tabla)
        # ------------------------------------------------------------------
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO ads_metric_observation (ad_entity_id, metric_date,"
                " observed_at, metric_currency, source_report_id, ingest_run_id)"
                " VALUES (%s, %s, now(), 'MXN', 'R-CRUCE', %s)",
                (camp_us, ayer, res1.run_id),
            )

        # ------------------------------------------------------------------
        # FALLO A MITAD DEL TRABAJO: rollback atomico + sello ok=false
        # (patron structure): se rompe el SQL de INSERT; sin la rama de sello
        # en fallo, la run quedaria abierta para siempre.
        # ------------------------------------------------------------------
        import app.ads.reports as reports_modulo

        with monkeypatch.context() as mp:
            mp.setattr(
                reports_modulo,
                "_SQL_INSERT_METRICA",
                # 11 placeholders = misma firma que el SQL real (el 12mo valor
                # es now() literal, no placeholder), para que el fallo sea del
                # SERVIDOR (tabla inexistente), no del cliente
                "INSERT INTO tabla_inexistente_para_fallar VALUES"
                " (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            )
            with pytest.raises(psycopg.errors.UndefinedTable):
                sync_metrics(
                    conn,
                    client,
                    fecha_ini=ayer,
                    fecha_fin=ayer,
                    sleep=lambda s: None,
                )

        # id de la ULTIMA run fallida consultado, no derivado (nitpick
        # CodeRabbit): acopla el test a la secuencia en vez de al hecho
        run_fallida = conn.execute(
            "SELECT ok, rows_written, rows_skipped, finished_at, skip_reason"
            " FROM ingest_run WHERE ok IS FALSE ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert run_fallida[0] is False
        assert run_fallida[1] is None  # nunca llego a contar
        assert run_fallida[2] == 0  # rows_skipped=0 EXPLICITO (NOT NULL en UPDATE)
        assert run_fallida[3] is not None  # quedo SELLADA, no abierta
        assert run_fallida[4] is not None  # el fallo dejo rastro
        # rollback atomico: nada de lo que escribio la corrida fallida quedo
        assert conn.execute("SELECT count(*) FROM ads_metric_observation").fetchone()[0] == 9
        assert conn.execute("SELECT count(*) FROM ingest_run").fetchone()[0] == 4

        # ------------------------------------------------------------------
        # CERO PERFILES ACEPTADOS (hallazgo grok, mandatorio): la corrida se
        # sella ok=false CON motivo, jamas ok=true con 0 filas. Caso lista
        # VACIA de la API.
        # ------------------------------------------------------------------
        perfiles_respuesta["lista"] = []
        res_vacio = sync_metrics(conn, client, fecha_ini=ayer, fecha_fin=ayer, sleep=lambda s: None)
        assert res_vacio.ok is False
        assert (res_vacio.rows_written, res_vacio.rows_skipped) == (0, 0)
        assert res_vacio.reportes == []
        assert res_vacio.skip_reason == "ningun perfil aceptado: /v2/profiles devolvio 0 perfiles"
        run_vacio = conn.execute(
            "SELECT ok, rows_written, rows_skipped, skip_reason, finished_at"
            " FROM ingest_run WHERE id = %s",
            (res_vacio.run_id,),
        ).fetchone()
        assert run_vacio[0] is False  # sellada ok=false en la base (regla 9)
        assert run_vacio[2] == 0
        assert run_vacio[3] == "ningun perfil aceptado: /v2/profiles devolvio 0 perfiles"
        assert run_vacio[4] is not None

        # mismo veredicto con SOLO rechazados: los motivos de rechazo suben a
        # la run (el vendor 303 de la fixture)
        perfiles_respuesta["lista"] = [PERFILES_API[2]]
        res_vendor = sync_metrics(
            conn, client, fecha_ini=ayer, fecha_fin=ayer, sleep=lambda s: None
        )
        assert res_vendor.ok is False
        assert [p.profile_id for p in res_vendor.perfiles_rechazados] == [303]
        assert res_vendor.skip_reason.startswith("ningun perfil aceptado: ")
        assert "no seller" in res_vendor.skip_reason

        # ------------------------------------------------------------------
        # FALLO DE FASE API CON RUN AUDITABLE (hallazgo codex, mandatorio):
        # un 400 en create abre run + sello ok=false y RE-LANZA; antes de este
        # fix, el fallo no dejaba ninguna fila en ingest_run.
        # ------------------------------------------------------------------
        perfiles_respuesta["lista"] = PERFILES_API  # restaurar los perfiles
        fallo_create["on"] = True
        with pytest.raises(AdsApiError):
            sync_metrics(conn, client, fecha_ini=ayer, fecha_fin=ayer, sleep=lambda s: None)
        fallo_create["on"] = False

        run_api = conn.execute(
            "SELECT ok, rows_written, rows_skipped, skip_reason, finished_at"
            " FROM ingest_run WHERE id = %s",
            (res_vendor.run_id + 1,),
        ).fetchone()
        assert run_api[0] is False
        assert run_api[1] is None  # nunca llego a contar
        assert run_api[2] == 0  # rows_skipped=0 explicito
        assert run_api[3] is not None and "status=400" in run_api[3]  # rastro del error
        assert run_api[4] is not None  # sellada, no abierta
        # ni estas corridas ni la fallida escribieron metricas nuevas
        assert conn.execute("SELECT count(*) FROM ads_metric_observation").fetchone()[0] == 9
        assert conn.execute("SELECT count(*) FROM ingest_run").fetchone()[0] == 7

        # ------------------------------------------------------------------
        # PRIVILEGIO NEGATIVO (DoD): app_ingest inserta metricas, JAMAS
        # decisions (esas son del motor, rol app_decide). Va AL FINAL: en CI el
        # DSN es superuser y SET ROLE siempre puede; por tunel el usuario
        # orbit_test no tiene membresia app_ingest y el SET ROLE mismo se
        # deniega -- el skip explicito no debe callar los bloques de arriba.
        # ------------------------------------------------------------------
        config_id = conn.execute(
            "INSERT INTO config_version (settings) VALUES ('{}') RETURNING id"
        ).fetchone()[0]
        ciclo_id = conn.execute(
            "INSERT INTO optimizer_cycle (mode, platform) VALUES ('shadow', 'amazon_us')"
            " RETURNING id"
        ).fetchone()[0]
        try:
            conn.execute("SET ROLE app_ingest")
        except psycopg.errors.InsufficientPrivilege:
            # Hallazgo CodeRabbit: con DSN EXPLICITO (CI) esto no puede ser un
            # skip silencioso -- el DoD se apagaria sin senal si el DSN dejara
            # de ser superuser. Falla salvo en corridas locales sin DSN puesto.
            if _DSN_EXPLICITO:
                raise AssertionError(
                    "ORBIT_TEST_DSN explicito pero sin membresia app_ingest: "
                    "el privilegio negativo del DoD quedo sin ejercer"
                ) from None
            pytest.skip(
                "usuario del DSN sin membresia app_ingest: el privilegio negativo "
                "se ejercita en CI, donde el DSN es superuser"
            )
        try:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(
                    "INSERT INTO decision (cycle_id, ad_entity_id, kind,"
                    " config_version_id, data_observed_at, window_start, window_end, inputs)"
                    " VALUES (%s, %s, 'pause', %s, now(), CURRENT_DATE - 30,"
                    " CURRENT_DATE - 10, '{}')",
                    (ciclo_id, camp_us, config_id),
                )
        finally:
            conn.execute("SET ROLE NONE")
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


@pytest.mark.skipif(
    not _DSN_EXPLICITO and not _hay_postgres_local(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_pipeline_search_terms_en_vivo():
    """Corrida del orquestador con el 4to reporte (spSearchTerm, task 1.4)
    contra la migracion real, en SU propia DB temporal:

    - CORRIDA 1: fixture MIXTA -> 4 escritas con is_asin_like SIEMPRE
      clasificado (ASIN mayusculas/minusculas True; termino normal y "b0"
      embebido False), moneda/platform selladas por el trigger
      st_metric_moneda_sellada, observed_at >= medianoche de ayer, y las
      referencias (ad_entity_id del ad_group, source_report_id,
      ingest_run_id).
    - Skips afirmados (regla 9): entidad ausente, sin adGroupId, searchTerm
      solo espacios y la duplicada intra-reporte, que el planificador
      FUSIONA (grano multiple verificado en vivo: las metricas de las dos
      filas se SUMAN hacia la clave (ad_group, termino, fecha)).
    - RE-CORRIDA con el MISMO report id: las 4 claves validas quedan
      absorbidas por el dedupe del indice parcial st_metric_dedupe_reporte
      (la fusion vuelve a correr y vuelve a contar su skip); la tabla
      sigue en 4 (append-only, sin duplicados).
    """
    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql as pgsql

    dsn = _test_dsn()
    db = f"orbit_st_test_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        conn.execute(SQL)  # la migracion entera

        # seed de estructura (en produccion la escribe app.ads.structure):
        # campana + ad group en US; el reporte de terminos se adjunta al
        # ad_group (kind de SEARCH_TERMS_CFG)
        camp_us = conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id)"
            " VALUES ('amazon_us', 'campaign', '9001') RETURNING id"
        ).fetchone()[0]
        ag_us = conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id, parent_id)"
            " VALUES ('amazon_us', 'ad_group', '9101', %s) RETURNING id",
            (camp_us,),
        ).fetchone()[0]

        ahora = dt.datetime.now(dt.UTC)
        ayer = ahora.date() - dt.timedelta(days=1)
        medianoche_ayer = dt.datetime(ayer.year, ayer.month, ayer.day, tzinfo=dt.UTC)

        # fixture MIXTA: ids NUMERO como en la API real
        filas_st = [
            {
                "date": str(ayer),
                "adGroupId": 9101,
                "searchTerm": "B0AB12CD34",
                "clicks": 5,
                "cost": 1.25,
                "purchases7d": 0,
                "sales7d": 0,
            },
            {
                "date": str(ayer),
                "adGroupId": 9101,
                "searchTerm": "b0aaaa1111",
                "clicks": 2,
                "cost": 0.5,
            },
            {
                "date": str(ayer),
                "adGroupId": 9101,
                "searchTerm": "tenis para correr",
                "clicks": 9,
                "cost": 3.0,
                "purchases7d": 1,
                "sales7d": 200.0,
            },
            {
                "date": str(ayer),
                "adGroupId": 9101,
                "searchTerm": "usb0cable",
                "clicks": 1,
                "cost": 0.2,
            },
            # ad_group que NO esta en ad_entity: skip con motivo
            {
                "date": str(ayer),
                "adGroupId": 9999,
                "searchTerm": "no existe",
                "clicks": 1,
                "cost": 0.1,
            },
            # sin adGroupId: skip
            {"date": str(ayer), "searchTerm": "huerfano", "clicks": 1, "cost": 0.1},
            # searchTerm solo espacios: skip
            {"date": str(ayer), "adGroupId": 9101, "searchTerm": "   ", "clicks": 1, "cost": 0.1},
            # [hallazgo reviewer] duplicada INTRA-reporte: el MISMO (ad_group,
            # termino, fecha) llega dos veces en un reporte (grano multiple
            # verificado en vivo: el termino convierte via keywords distintas
            # del mismo ad group). El planificador FUSIONA las dos filas
            # SUMANDO metricas y cuenta la absorbida como rows_skipped con
            # motivo: sin perdida silenciosa, tal como promete el docstring
            # del modulo.
            {
                "date": str(ayer),
                "adGroupId": 9101,
                "searchTerm": "B0AB12CD34",
                "clicks": 7,
                "cost": 2.0,
            },
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "api.amazon.com":
                return _token(request)
            if request.url.path == "/v2/profiles":
                # solo el perfil US 101: este test sella la tabla de terminos
                return httpx.Response(200, json=[PERFILES_API[0]])
            if request.method == "POST" and request.url.path == "/reporting/reports":
                cfg = json.loads(request.content)["configuration"]
                scope = request.headers.get("Amazon-Advertising-API-Scope")
                if cfg["reportTypeId"] == "spCampaigns":
                    reporte = f"R-CAMP-{scope}"
                elif cfg["reportTypeId"] == "spSearchTerm":
                    reporte = f"R-ST-{scope}"
                elif "TARGETING_EXPRESSION" in cfg["filters"][0]["values"]:
                    reporte = f"R-TG-{scope}"
                else:
                    reporte = f"R-KW-{scope}"
                return httpx.Response(200, json={"reportId": reporte, "status": "PENDING"})
            if request.url.path.startswith("/reporting/reports/"):
                reporte = request.url.path.rsplit("/", 1)[1]
                return httpx.Response(
                    200,
                    json={
                        "reportId": reporte,
                        "status": "COMPLETED",
                        "url": f"https://bucket.example.com/{reporte}.json",
                    },
                )
            reporte = request.url.path.removesuffix(".json").removeprefix("/")
            filas = filas_st if reporte == "R-ST-101" else []
            return httpx.Response(200, content=gzip.compress(json.dumps(filas).encode("utf-8")))

        client = _cliente(handler)

        # ------------------------------------------------------------------
        # CORRIDA 1: 4 escritas (las 4 claves validas, la duplicada ya
        # FUSIONADA) + 4 saltadas (los 3 gates y la fila absorbida por la
        # fusion)
        # ------------------------------------------------------------------
        res1 = sync_metrics(conn, client, fecha_ini=ayer, fecha_fin=ayer, sleep=lambda s: None)

        assert res1.ok is True
        assert res1.rows_written == 4
        assert res1.rows_skipped == 4
        # orden de REPORTES_CFG: los 3 de metricas (0 filas en este test) y
        # el de terminos al final
        assert [resumen.report_id for resumen in res1.reportes] == [
            "R-CAMP-101",
            "R-KW-101",
            "R-TG-101",
            "R-ST-101",
        ]
        # regla 9: cada skip afirmado con su motivo (vocabulario cerrado)
        assert "1x entidad ausente en ad_entity (ad_group)" in res1.skip_reason
        assert "1x fila de search_terms sin adGroupId" in res1.skip_reason
        assert "1x fila de search_terms sin searchTerm" in res1.skip_reason
        # la duplicada intra-reporte se FUSIONO en el planificador: contada
        # como skip con su clave propia, sin perder sus metricas
        assert "1x fila agregada por clave duplicada en el reporte" in res1.skip_reason

        _SQL_TERMINOS = (
            "SELECT search_term, is_asin_like, metric_date::text, observed_at,"
            " metric_currency, platform, ad_entity_id, cost, clicks, orders, ad_revenue,"
            " source_report_id, ingest_run_id FROM search_term_observation"
        )
        terminos = conn.execute(_SQL_TERMINOS).fetchall()
        assert len(terminos) == 4
        # clasificacion correcta por termino (la columna es NOT NULL sin default)
        assert {fila[0]: fila[1] for fila in terminos} == {
            "B0AB12CD34": True,
            "b0aaaa1111": True,
            "tenis para correr": False,
            "usb0cable": False,
        }
        for fila in terminos:
            assert fila[2] == str(ayer)
            assert fila[3] >= medianoche_ayer  # observado >= hecho (invariante)
            assert fila[4] == "USD"  # moneda sellada contra la plataforma
            assert fila[5] == "amazon_us"
            assert fila[6] == ag_us  # attachment al ad_group seedeado
            assert fila[11] == "R-ST-101"
            assert fila[12] == res1.run_id
        asin = next(fila for fila in terminos if fila[0] == "B0AB12CD34")
        # la duplicada se FUSIONO: cost 1.25 + 2.0, clicks 5 + 7 (el
        # first-wins anterior dejaba 1.25/5 y perdia la segunda fila)
        assert (asin[7], asin[8], asin[9], asin[10]) == (Decimal("3.25"), 12, 0, Decimal("0"))

        # ------------------------------------------------------------------
        # RE-CORRIDA (mismo handler -> mismos report ids): las 4 claves
        # validas quedan absorbidas por el dedupe de DB; los 3 gates saltan
        # otra vez y la fusion vuelve a correr (y a contar su skip)
        # ------------------------------------------------------------------
        res2 = sync_metrics(conn, client, fecha_ini=ayer, fecha_fin=ayer, sleep=lambda s: None)

        assert res2.ok is True
        assert res2.rows_written == 0
        # 4 duplicadas por dedupe de DB (las 4 claves validas) + 1 fila
        # agregada por la fusion + los 3 gates
        assert res2.rows_skipped == 8
        assert "4x fila duplicada del reporte R-ST-101" in res2.skip_reason
        assert "1x fila agregada por clave duplicada en el reporte" in res2.skip_reason
        assert conn.execute("SELECT count(*) FROM search_term_observation").fetchone()[0] == 4
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()
