"""Tests MARGEN ESTIMADO 01 A.3 — cotizacion Product Fees y persistencia.

(a) UNITARIOS: cliente SP-API allowlist, request/response, rate limit, parseo.
(b) INTEGRACION: persistencia idempotente en Postgres (0001 + 0028 + 0029).
(c) ORQUESTACION: pipeline/CLI, transacciones cortas, aislamiento por item.
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
from contextlib import contextmanager, nullcontext
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import unquote

import httpx
import pytest
from test_schema import _postgres_obligatorio_ausente, _test_dsn

from app import cli
from app.estimacion_fees import (
    FeesClientError,
    MutationNotAllowedError,
    ProductFeesClient,
    ResultadoCotizacion,
    construir_fee_outcome_canonical,
    construir_fee_source_event_id,
    construir_request_body,
    construir_ruta_fees,
    cotizar_oferta,
    parsear_respuesta_fees,
    persistir_fee_observation,
    serializar_request_body,
)
from app.estimacion_ingest import (
    SKIP_OFERTA_EXCEPCION,
    _persistir_escenario_item,
    ejecutar_ingesta,
)
from app.estimacion_ingest import (
    main as ingest_main,
)
from app.estimacion_insumos import (
    OfertaResuelta,
    construir_canonical_input,
    construir_context_fingerprint,
    construir_source_event_id,
)
from app.redaction import register_secret

ROOT = Path(__file__).resolve().parents[1]

NOW = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)
FETCH = NOW - timedelta(hours=1)
FEE_TIME = NOW - timedelta(minutes=30)

FAKE_CLIENT_ID = "fake-lwa-client"
FAKE_CLIENT_SECRET = "fake-lwa-secret-XYZ"
FAKE_REFRESH_TOKEN = "fake-refresh-ABC"
FAKE_ACCESS = "fake-access-token-123"

_skip_db = pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres")
ORDEN = ("0001_initial.sql", "0028_estimacion_venta.sql", "0029_estimacion_politica_vigencia.sql")

_DDL_BRIDGE = """
CREATE TABLE amazon_listing_prices (
    id INTEGER PRIMARY KEY,
    seller_sku TEXT NOT NULL,
    asin TEXT,
    listing_id TEXT,
    marketplace_id TEXT NOT NULL,
    marketplace_name TEXT,
    price REAL,
    quantity INTEGER,
    fulfillment_channel TEXT,
    item_name TEXT,
    status TEXT,
    fetched_at TEXT,
    UNIQUE(seller_sku, marketplace_id)
);
"""


def _oferta_resuelta(
    *,
    seller_sku: str = "SS-MX-1",
    price: str = "116.0000",
    listing_id: int = 1,
) -> OfertaResuelta:
    from app.estimacion_insumos import FilaOfertaBridge

    fila = FilaOfertaBridge(
        seller_sku=seller_sku,
        asin="B0EST01",
        marketplace_id="A1AM78C64UM0Y8",
        marketplace_name="amazon_mx",
        price=float(price),
        fulfillment_channel="AMAZON_NA",
        fetched_at=FETCH.isoformat(),
    )
    precio = Decimal(price)
    canon = construir_canonical_input(fila, "amazon_mx", "fba", precio, "MXN")
    return OfertaResuelta(
        listing_id=listing_id,
        platform="amazon_mx",
        seller_sku=seller_sku,
        asin="B0EST01",
        canal="fba",
        price_amount=precio,
        price_currency="MXN",
        fetched_at=FETCH,
        canonical_input=canon,
        context_fingerprint=construir_context_fingerprint("fba", precio, "MXN", FETCH),
        source_event_id=construir_source_event_id(canon),
    )


def _respuesta_exito(
    oferta: OfertaResuelta,
    *,
    fba: str = "10.00",
    referral: str = "5.00",
    fee_time: str | None = None,
    tax_amount: str | None = None,
    seller_input_identifier: str | None = None,
) -> dict:
    total = Decimal(fba) + Decimal(referral)
    t = fee_time or FEE_TIME.isoformat()
    detalle_ref = {
        "FeeType": "ReferralFee",
        "FeeAmount": {"CurrencyCode": "MXN", "Amount": float(referral)},
        "FinalFee": {"CurrencyCode": "MXN", "Amount": float(referral)},
    }
    if tax_amount is not None:
        detalle_ref["TaxAmount"] = {"CurrencyCode": "MXN", "Amount": float(tax_amount)}
    ident_block = {
        "MarketplaceId": "A1AM78C64UM0Y8",
        "IdType": "SellerSKU",
        "IdValue": oferta.seller_sku,
        "SellerInputIdentifier": seller_input_identifier or oferta.source_event_id,
        "IsAmazonFulfilled": True,
        "PriceToEstimateFees": {
            "ListingPrice": {
                "CurrencyCode": oferta.price_currency,
                "Amount": float(oferta.price_amount),
            }
        },
    }
    return {
        "payload": {
            "FeesEstimateResult": {
                "Status": "Success",
                "FeesEstimateIdentifier": ident_block,
                "FeesEstimate": {
                    "TimeOfFeesEstimation": t,
                    "TotalFeesEstimate": {
                        "CurrencyCode": "MXN",
                        "Amount": float(total),
                    },
                    "FeeDetailList": [
                        {
                            "FeeType": "FBAFees",
                            "FeeAmount": {"CurrencyCode": "MXN", "Amount": float(fba)},
                            "FinalFee": {"CurrencyCode": "MXN", "Amount": float(fba)},
                        },
                        detalle_ref,
                    ],
                },
            }
        }
    }


def _token_response() -> httpx.Response:
    return httpx.Response(200, json={"access_token": FAKE_ACCESS, "expires_in": 3600})


def make_client(handler, *, sleep=None, clock=None) -> ProductFeesClient:
    transport = httpx.MockTransport(handler)
    if clock is None:
        tiempo = {"v": 0.0}
        observar_espera = sleep

        def reloj_falso():
            return tiempo["v"]

        def espera_falsa(segundos):
            if observar_espera is not None:
                observar_espera(segundos)
            tiempo["v"] += segundos

        clock = reloj_falso
        sleep = espera_falsa
    kwargs: dict = {"sleep": sleep or (lambda _s: None), "clock": clock}
    return ProductFeesClient(
        secrets_dir=None,
        credentials={
            "lwa_app_id": FAKE_CLIENT_ID,
            "lwa_client_secret": FAKE_CLIENT_SECRET,
            "refresh_token": FAKE_REFRESH_TOKEN,
        },
        transport=transport,
        **kwargs,
    )


def _snapshot_bridge(ruta: Path, filas: list[tuple]) -> Path:
    con = sqlite3.connect(ruta)
    con.executescript(_DDL_BRIDGE)
    con.executemany(
        "INSERT INTO amazon_listing_prices"
        " (seller_sku, asin, marketplace_id, marketplace_name, price,"
        " fulfillment_channel, fetched_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        filas,
    )
    con.commit()
    con.close()
    return ruta


# ---------------------------------------------------------------------------
# (a) UNITARIOS
# ---------------------------------------------------------------------------


def test_construir_ruta_url_encode_seller_sku():
    ruta = construir_ruta_fees("SKU/with space")
    assert ruta == "/products/fees/v0/listings/SKU%2Fwith%20space/feesEstimate"


def test_construir_request_body_fba_mx():
    oferta = _oferta_resuelta()
    cuerpo = construir_request_body(oferta)
    req = cuerpo["FeesEstimateRequest"]
    assert req["MarketplaceId"] == "A1AM78C64UM0Y8"
    assert req["IsAmazonFulfilled"] is True
    assert req["Identifier"] == oferta.source_event_id
    lp = req["PriceToEstimateFees"]["ListingPrice"]
    assert lp["CurrencyCode"] == "MXN"
    assert isinstance(lp["Amount"], Decimal)
    assert lp["Amount"] == Decimal("116.0000")


def test_serializar_request_body_decimal_exacto_sin_float():
    oferta = _oferta_resuelta(price="116.0001")
    contenido = serializar_request_body(oferta)
    assert b'"Amount":116.0001' in contenido
    assert b"116.000099" not in contenido
    assert b'"Amount":"' not in contenido
    assert contenido.count(b'"IsAmazonFulfilled"') == 1
    parsed = json.loads(contenido)
    amount = parsed["FeesEstimateRequest"]["PriceToEstimateFees"]["ListingPrice"]["Amount"]
    assert isinstance(amount, (int, float))
    assert not isinstance(amount, str)


def test_allowlist_rechaza_get_antes_de_red():
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("no debe salir red")

    client = make_client(handler)
    with pytest.raises(MutationNotAllowedError):
        client._request(
            "GET", "https://sellingpartnerapi-na.amazon.com/catalog/2022-04-01/items/B0"
        )


def test_allowlist_rechaza_post_ruta_no_permitida():
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("no debe salir red")

    client = make_client(handler)
    with pytest.raises(MutationNotAllowedError):
        client._request(
            "POST",
            "https://sellingpartnerapi-na.amazon.com/products/fees/v0/feesEstimate",
            json_body={},
            seller_sku="SS-MX-1",
        )


def test_allowlist_rechaza_query_fragment_userinfo_puerto():
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("no debe salir red")

    client = make_client(handler)
    base = "https://sellingpartnerapi-na.amazon.com/products/fees/v0/listings/SS-MX-1/feesEstimate"
    casos = [
        f"{base}?x=1",
        f"{base}#frag",
        "https://user:pass@sellingpartnerapi-na.amazon.com/products/fees/v0/listings/SS-MX-1/feesEstimate",
        "https://sellingpartnerapi-na.amazon.com:443/products/fees/v0/listings/SS-MX-1/feesEstimate",
    ]
    for url in casos:
        with pytest.raises(MutationNotAllowedError):
            client._request("POST", url, json_body={}, seller_sku="SS-MX-1")


def test_allowlist_rechaza_host_ajeno():
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("no debe salir red")

    client = make_client(handler)
    with pytest.raises(MutationNotAllowedError):
        client._request("POST", "https://evil.example.com/auth/o2/token", data={})


def test_allowlist_rechaza_seller_sku_vacio():
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("no debe salir red")

    client = make_client(handler)
    with pytest.raises(MutationNotAllowedError):
        client._request(
            "POST",
            "https://sellingpartnerapi-na.amazon.com/products/fees/v0/listings//feesEstimate",
            json_body={},
            seller_sku="",
        )


def test_allowlist_rechaza_segmento_punto_antes_de_red():
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("no debe salir red")

    client = make_client(handler)
    url = "https://sellingpartnerapi-na.amazon.com/products/fees/v0/listings/../feesEstimate"
    with pytest.raises(MutationNotAllowedError):
        client._request("POST", url, seller_sku="..", content=b"{}")


def test_cotizar_exito_valida_identificador_y_total():
    oferta = _oferta_resuelta()
    payload = _respuesta_exito(oferta)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_response()
        assert request.method == "POST"
        assert unquote(request.url.path.split("/listings/")[1].split("/feesEstimate")[0]) == (
            oferta.seller_sku
        )
        assert request.headers.get("content-type") == "application/json"
        assert b'"Amount":116' in request.content
        return httpx.Response(200, json=payload)

    client = make_client(handler)
    resultado = cotizar_oferta(client, oferta, observed_at=NOW)
    assert resultado.estado == "success"
    assert resultado.total_fees == Decimal("15.0000")
    assert resultado.fees_estimated_at == FEE_TIME
    assert resultado.fees_estimated_at.tzinfo is not None
    assert len(resultado.fee_details) == 2
    assert resultado.error_code is None


def test_cotizar_fecha_la_captura_despues_de_responder():
    oferta = _oferta_resuelta()
    emitida = NOW + timedelta(seconds=2)
    capturada = NOW + timedelta(seconds=3)
    payload = _respuesta_exito(oferta, fee_time=emitida.isoformat())

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_response()
        return httpx.Response(200, json=payload)

    resultado = cotizar_oferta(make_client(handler), oferta, now_utc=lambda: capturada)
    assert resultado.estado == "success"
    assert resultado.fees_estimated_at == emitida
    assert resultado.fetched_at == capturada


def test_cliente_decodifica_importes_json_como_decimal():
    oferta = _oferta_resuelta()
    payload = _respuesta_exito(oferta)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_response()
        return httpx.Response(200, json=payload)

    body = make_client(handler).cotizar(oferta)
    identificador = body["payload"]["FeesEstimateResult"]["FeesEstimateIdentifier"]
    amount = identificador["PriceToEstimateFees"]["ListingPrice"]["Amount"]
    assert isinstance(amount, Decimal)


def test_parsear_rechaza_id_type_incompatible():
    oferta = _oferta_resuelta()
    payload = _respuesta_exito(oferta)
    payload["payload"]["FeesEstimateResult"]["FeesEstimateIdentifier"]["IdType"] = "ASIN"
    with pytest.raises(FeesClientError, match="IdType"):
        parsear_respuesta_fees(payload, oferta, observed_at=NOW)


def test_parsear_rechaza_id_type_ausente():
    oferta = _oferta_resuelta()
    payload = _respuesta_exito(oferta)
    del payload["payload"]["FeesEstimateResult"]["FeesEstimateIdentifier"]["IdType"]
    with pytest.raises(FeesClientError, match="IdType"):
        parsear_respuesta_fees(payload, oferta, observed_at=NOW)


def test_parsear_rechaza_seller_input_identifier_incompatible():
    oferta = _oferta_resuelta()
    payload = _respuesta_exito(oferta, seller_input_identifier="otro-id")
    with pytest.raises(FeesClientError, match="SellerInputIdentifier"):
        parsear_respuesta_fees(payload, oferta, observed_at=NOW)


def test_parsear_rechaza_id_value_incompatible():
    oferta = _oferta_resuelta()
    payload = _respuesta_exito(oferta)
    payload["payload"]["FeesEstimateResult"]["FeesEstimateIdentifier"]["IdValue"] = "OTRO"
    with pytest.raises(FeesClientError, match="IdValue"):
        parsear_respuesta_fees(payload, oferta, observed_at=NOW)


def test_parsear_rechaza_marketplace_id_incompatible():
    oferta = _oferta_resuelta()
    payload = _respuesta_exito(oferta)
    payload["payload"]["FeesEstimateResult"]["FeesEstimateIdentifier"]["MarketplaceId"] = "X"
    with pytest.raises(FeesClientError, match="MarketplaceId"):
        parsear_respuesta_fees(payload, oferta, observed_at=NOW)


def test_parsear_rechaza_is_amazon_fulfilled_incompatible():
    oferta = _oferta_resuelta()
    payload = _respuesta_exito(oferta)
    ident = payload["payload"]["FeesEstimateResult"]["FeesEstimateIdentifier"]
    ident["IsAmazonFulfilled"] = False
    with pytest.raises(FeesClientError, match="IsAmazonFulfilled"):
        parsear_respuesta_fees(payload, oferta, observed_at=NOW)


def test_parsear_rechaza_precio_identificador_incompatible():
    oferta = _oferta_resuelta()
    payload = _respuesta_exito(oferta)
    lp = payload["payload"]["FeesEstimateResult"]["FeesEstimateIdentifier"]["PriceToEstimateFees"][
        "ListingPrice"
    ]
    lp["Amount"] = 999.0
    with pytest.raises(FeesClientError, match="precio"):
        parsear_respuesta_fees(payload, oferta, observed_at=NOW)


def test_parsear_rechaza_time_anterior_a_fetched_at():
    oferta = _oferta_resuelta()
    payload = _respuesta_exito(oferta, fee_time=(FETCH - timedelta(minutes=1)).isoformat())
    with pytest.raises(FeesClientError, match="fees_estimated_at"):
        parsear_respuesta_fees(payload, oferta, observed_at=NOW)


def test_parsear_rechaza_time_posterior_a_observed_at():
    oferta = _oferta_resuelta()
    payload = _respuesta_exito(oferta, fee_time=(NOW + timedelta(minutes=1)).isoformat())
    with pytest.raises(FeesClientError, match="fees_estimated_at"):
        parsear_respuesta_fees(payload, oferta, observed_at=NOW)


def test_parsear_acepta_time_of_fees_estimation_rfc3339():
    oferta = _oferta_resuelta()
    payload = _respuesta_exito(oferta, fee_time="2026-09-08T11:30:00Z")
    resultado = parsear_respuesta_fees(payload, oferta, observed_at=NOW)
    assert resultado.fees_estimated_at == datetime(2026, 9, 8, 11, 30, 0, tzinfo=UTC)


def test_parsear_acepta_time_of_fees_estimation_legacy_swagger():
    oferta = _oferta_resuelta()
    payload = _respuesta_exito(oferta, fee_time="Tue Sep  8 11:30:00 UTC 2026")
    resultado = parsear_respuesta_fees(payload, oferta, observed_at=NOW)
    assert resultado.fees_estimated_at == datetime(2026, 9, 8, 11, 30, 0, tzinfo=UTC)


def test_parsear_rechaza_time_naive():
    oferta = _oferta_resuelta()
    payload = _respuesta_exito(oferta, fee_time="2026-09-08T11:30:00")
    with pytest.raises(FeesClientError, match="TimeOfFeesEstimation"):
        parsear_respuesta_fees(payload, oferta, observed_at=NOW)


def test_parsear_error_item_usa_code_oficial():
    oferta = _oferta_resuelta()
    payload = {
        "payload": {
            "FeesEstimateResult": {
                "Status": "ClientError",
                "Error": {
                    "Code": "InvalidParameterValue",
                    "Message": "detalle externo no debe persistir",
                    "Detail": {"extra": True},
                },
            }
        }
    }
    resultado = parsear_respuesta_fees(payload, oferta, observed_at=NOW)
    assert resultado.estado == "error"
    assert resultado.error_code == "fee_item_InvalidParameterValue"
    assert "detalle" not in (resultado.error_code or "")
    assert "Message" not in (resultado.error_code or "")


def test_parsear_redacta_code_externo_sensible():
    oferta = _oferta_resuelta()
    secreto = "codigo-externo-sensible-123"
    register_secret(secreto)
    payload = {
        "payload": {
            "FeesEstimateResult": {
                "Status": "ClientError",
                "Error": {"Code": secreto, "Message": "ignorado", "Detail": []},
            }
        }
    }
    resultado = parsear_respuesta_fees(payload, oferta, observed_at=NOW)
    assert resultado.error_code == "fee_item_error"
    assert secreto not in resultado.error_code


def test_parsear_final_fee_con_promocion():
    oferta = _oferta_resuelta()
    payload = _respuesta_exito(oferta, referral="5.00")
    det = payload["payload"]["FeesEstimateResult"]["FeesEstimate"]["FeeDetailList"][1]
    det["FeeAmount"]["Amount"] = 6.0
    det["FeePromotion"] = {"CurrencyCode": "MXN", "Amount": 1.0}
    det["FinalFee"]["Amount"] = 5.0
    resultado = parsear_respuesta_fees(payload, oferta, observed_at=NOW)
    assert resultado.total_fees == Decimal("15.0000")
    ref = next(d for d in resultado.fee_details if d["fee_type"] == "ReferralFee")
    assert ref["final_fee"] == "5.0000"
    assert ref["fee_promotion"] == "1.0000"


def test_parsear_conserva_tax_amount_no_cero():
    oferta = _oferta_resuelta()
    payload = _respuesta_exito(oferta, tax_amount="1.50")
    resultado = parsear_respuesta_fees(payload, oferta, observed_at=NOW)
    ref = next(d for d in resultado.fee_details if d["fee_type"] == "ReferralFee")
    assert ref["tax_amount"] == "1.5000"


def test_parsear_rechaza_total_que_no_concilia_con_detalle():
    oferta = _oferta_resuelta()
    payload = _respuesta_exito(oferta)
    payload["payload"]["FeesEstimateResult"]["FeesEstimate"]["TotalFeesEstimate"]["Amount"] = 99.0
    with pytest.raises(FeesClientError, match="total"):
        parsear_respuesta_fees(payload, oferta, observed_at=NOW)


def test_parsear_rechaza_eco_con_precision_mayor_al_schema():
    oferta = _oferta_resuelta()
    payload = _respuesta_exito(oferta)
    identificador = payload["payload"]["FeesEstimateResult"]["FeesEstimateIdentifier"]
    identificador["PriceToEstimateFees"]["ListingPrice"]["Amount"] = 116.00004
    with pytest.raises(FeesClientError, match="decimales"):
        parsear_respuesta_fees(payload, oferta, observed_at=NOW)


def test_parsear_conserva_impuesto_en_detalle_incluido():
    oferta = _oferta_resuelta()
    payload = _respuesta_exito(oferta)
    detalle = payload["payload"]["FeesEstimateResult"]["FeesEstimate"]["FeeDetailList"][0]
    detalle["IncludedFeeDetailList"] = [
        {
            "FeeType": "FBAWeightBasedFee",
            "FeeAmount": {"CurrencyCode": "MXN", "Amount": 10},
            "FinalFee": {"CurrencyCode": "MXN", "Amount": 10},
            "TaxAmount": {"CurrencyCode": "MXN", "Amount": 1.6},
        }
    ]
    resultado = parsear_respuesta_fees(payload, oferta, observed_at=NOW)
    incluido = resultado.fee_details[0]["included_fee_details"][0]
    assert incluido["tax_amount"] == "1.6000"


@pytest.mark.parametrize(
    "handler_factory,error_code",
    [
        (
            lambda oferta: (
                lambda request: (
                    _token_response()
                    if request.url.host == "api.amazon.com"
                    else httpx.Response(403, json={"errors": [{"message": "denied"}]})
                )
            ),
            "fee_http_403",
        ),
        (
            lambda oferta: (
                lambda request: (
                    _token_response()
                    if request.url.host == "api.amazon.com"
                    else httpx.Response(429, headers={"Retry-After": "0"}, json={})
                )
            ),
            "fee_http_429_agotado",
        ),
    ],
)
def test_errores_http_producen_total_null(handler_factory, error_code):
    oferta = _oferta_resuelta()
    client = make_client(handler_factory(oferta), sleep=lambda _s: None)
    if error_code == "fee_http_429_agotado":
        # agotar reintentos
        pass
    resultado = cotizar_oferta(client, oferta, observed_at=NOW)
    assert resultado.estado == "error"
    assert resultado.error_code == error_code
    assert resultado.total_fees is None
    assert resultado.fee_details == []
    assert resultado.fees_estimated_at is None


def test_timeout_produce_total_null():
    oferta = _oferta_resuelta()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_response()
        raise httpx.TimeoutException("timeout")

    client = make_client(handler)
    resultado = cotizar_oferta(client, oferta, observed_at=NOW)
    assert resultado.estado == "error"
    assert resultado.error_code == "fee_timeout"
    assert resultado.total_fees is None
    assert resultado.fee_details == []


def test_json_invalido_produce_total_null():
    oferta = _oferta_resuelta()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_response()
        return httpx.Response(200, content=b"not-json")

    client = make_client(handler)
    resultado = cotizar_oferta(client, oferta, observed_at=NOW)
    assert resultado.estado == "error"
    assert resultado.error_code == "fee_json_invalido"
    assert resultado.total_fees is None


def test_403_persiste_error_sin_retry():
    oferta = _oferta_resuelta()
    llamadas: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        llamadas.append(request)
        if request.url.host == "api.amazon.com":
            return _token_response()
        return httpx.Response(403, json={"errors": [{"message": FAKE_CLIENT_SECRET}]})

    client = make_client(handler)
    resultado = cotizar_oferta(client, oferta, observed_at=NOW)
    assert resultado.estado == "error"
    assert resultado.error_code == "fee_http_403"
    assert resultado.total_fees is None
    assert resultado.fee_details == []
    assert FAKE_CLIENT_SECRET not in (resultado.error_code or "")
    assert sum(1 for r in llamadas if "sellingpartnerapi" in r.url.host) == 1


def test_429_agotado_persiste_error():
    oferta = _oferta_resuelta()
    intentos = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_response()
        intentos["n"] += 1
        return httpx.Response(429, headers={"Retry-After": "0"}, json={})

    sleeps: list[float] = []
    client = make_client(handler, sleep=sleeps.append)
    resultado = cotizar_oferta(client, oferta, observed_at=NOW)
    assert resultado.estado == "error"
    assert resultado.error_code == "fee_http_429_agotado"
    assert intentos["n"] >= 2


def test_429_retry_puede_terminar_success():
    oferta = _oferta_resuelta()
    payload = _respuesta_exito(oferta)
    intentos = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_response()
        intentos["n"] += 1
        if intentos["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={})
        return httpx.Response(200, json=payload)

    client = make_client(handler)
    resultado = cotizar_oferta(client, oferta, observed_at=NOW)
    assert resultado.estado == "success"
    assert intentos["n"] == 2


def test_rate_limit_1_por_segundo_burst_2():
    oferta = _oferta_resuelta()
    payload = _respuesta_exito(oferta)
    t = {"v": 0.0}

    def clock():
        return t["v"]

    def sleep(seg):
        sleeps.append(seg)
        t["v"] += seg

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_response()
        return httpx.Response(200, json=payload)

    sleeps: list[float] = []
    client = make_client(handler, sleep=sleep, clock=clock)
    for _ in range(3):
        cotizar_oferta(client, oferta, observed_at=NOW)
    assert any(s >= 0.9 for s in sleeps)


def test_lwa_no_consume_presupuesto_fees():
    oferta = _oferta_resuelta()
    payload = _respuesta_exito(oferta)
    t = {"v": 0.0}
    fees_calls = {"n": 0}

    def clock():
        return t["v"]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_response()
        fees_calls["n"] += 1
        return httpx.Response(200, json=payload)

    client = make_client(handler, clock=clock)
    cotizar_oferta(client, oferta, observed_at=NOW)
    cotizar_oferta(client, oferta, observed_at=NOW)
    # Dos cotizaciones fees + token LWA compartido: solo 2 fees, no 3 limitadas.
    assert fees_calls["n"] == 2


def test_fee_source_event_id_determinista_por_outcome():
    oferta = _oferta_resuelta()
    parsed = parsear_respuesta_fees(_respuesta_exito(oferta), oferta, observed_at=NOW)
    canon = construir_fee_outcome_canonical(oferta, parsed, attempted_at=NOW)
    e1 = construir_fee_source_event_id(canon)
    e2 = construir_fee_source_event_id(canon)
    assert e1 == e2
    assert e1.startswith("pf-fee:")


def test_fee_source_event_id_cambia_con_time_of_fees():
    oferta = _oferta_resuelta()
    p1 = parsear_respuesta_fees(_respuesta_exito(oferta), oferta, observed_at=NOW)
    p2 = parsear_respuesta_fees(
        _respuesta_exito(oferta, fee_time=(FEE_TIME + timedelta(minutes=5)).isoformat()),
        oferta,
        observed_at=NOW,
    )
    c1 = construir_fee_source_event_id(
        construir_fee_outcome_canonical(oferta, p1, attempted_at=NOW)
    )
    c2 = construir_fee_source_event_id(
        construir_fee_outcome_canonical(oferta, p2, attempted_at=NOW)
    )
    assert c1 != c2


def test_universo_us_rechazado_sin_http():
    oferta = _oferta_resuelta()
    oferta_us = OfertaResuelta(
        listing_id=oferta.listing_id,
        platform="amazon_us",
        seller_sku=oferta.seller_sku,
        asin=oferta.asin,
        canal=oferta.canal,
        price_amount=oferta.price_amount,
        price_currency="USD",
        fetched_at=oferta.fetched_at,
        canonical_input=oferta.canonical_input,
        context_fingerprint=oferta.context_fingerprint,
        source_event_id=oferta.source_event_id,
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("no debe salir red")

    client = make_client(handler)
    resultado = cotizar_oferta(client, oferta_us, observed_at=NOW)
    assert resultado.estado == "error"
    assert resultado.error_code == "fee_universo_no_soportado"


# ---------------------------------------------------------------------------
# (b) INTEGRACION
# ---------------------------------------------------------------------------


@contextmanager
def db_fees(prefijo: str = "orbit_margen_a3"):
    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql as pgsql

    dsn = _test_dsn()
    db = f"{prefijo}_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=False)
        conn.execute("SET TIME ZONE 'UTC'")
        for nombre in ORDEN:
            conn.execute((ROOT / "migrations" / nombre).read_text(encoding="utf-8"))
        conn.commit()
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def _sembrar_oferta(conn, listing_id: int, oferta: OfertaResuelta, *, observed_at=NOW):
    from psycopg.types.json import Json

    return conn.execute(
        "INSERT INTO estimacion_oferta_observation"
        " (listing_id, platform, seller_sku, asin, canal, price_amount, price_currency,"
        " fetched_at, observed_at, source_event_id, canonical_input, context_fingerprint)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (
            listing_id,
            oferta.platform,
            oferta.seller_sku,
            oferta.asin,
            oferta.canal,
            oferta.price_amount,
            oferta.price_currency,
            oferta.fetched_at,
            observed_at,
            oferta.source_event_id,
            Json(oferta.canonical_input),
            oferta.context_fingerprint,
        ),
    ).fetchone()[0]


@_skip_db
def test_persistir_fee_idempotente_por_outcome():
    with db_fees() as conn:
        pid = conn.execute(
            "INSERT INTO product (odoo_sku, name) VALUES ('P', 'P') RETURNING id"
        ).fetchone()[0]
        lid = conn.execute(
            "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
            " VALUES (%s, 'amazon_mx', 'B0EST01', 'SS-MX-1') RETURNING id",
            (pid,),
        ).fetchone()[0]
        conn.commit()
        oferta = _oferta_resuelta(listing_id=lid)
        oid = _sembrar_oferta(conn, lid, oferta)
        conn.commit()
        parsed = parsear_respuesta_fees(_respuesta_exito(oferta), oferta, observed_at=NOW)
        canon = construir_fee_outcome_canonical(oferta, parsed, attempted_at=NOW)
        evento = construir_fee_source_event_id(canon)
        r1 = persistir_fee_observation(
            conn,
            oferta_observation_id=oid,
            oferta=oferta,
            resultado=parsed,
            observed_at=NOW,
            source_event_id=evento,
            canonical_input=canon,
        )
        conn.commit()
        r2 = persistir_fee_observation(
            conn,
            oferta_observation_id=oid,
            oferta=oferta,
            resultado=parsed,
            observed_at=NOW + timedelta(hours=1),
            source_event_id=evento,
            canonical_input=canon,
        )
        conn.commit()
        assert r1.reutilizada is False
        assert r2.reutilizada is True
        assert r1.observed_at == r2.observed_at
        n = conn.execute("SELECT count(*) FROM estimacion_fee_observation").fetchone()[0]
        assert n == 1


@_skip_db
def test_persistir_nueva_cotizacion_mismo_request_otro_time():
    with db_fees() as conn:
        pid = conn.execute(
            "INSERT INTO product (odoo_sku, name) VALUES ('P', 'P') RETURNING id"
        ).fetchone()[0]
        lid = conn.execute(
            "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
            " VALUES (%s, 'amazon_mx', 'B0EST01', 'SS-MX-1') RETURNING id",
            (pid,),
        ).fetchone()[0]
        conn.commit()
        oferta = _oferta_resuelta(listing_id=lid)
        oid = _sembrar_oferta(conn, lid, oferta)
        conn.commit()
        p1 = parsear_respuesta_fees(_respuesta_exito(oferta), oferta, observed_at=NOW)
        p2 = parsear_respuesta_fees(
            _respuesta_exito(oferta, fee_time=(FEE_TIME + timedelta(minutes=1)).isoformat()),
            oferta,
            observed_at=NOW,
        )
        for parsed in (p1, p2):
            canon = construir_fee_outcome_canonical(oferta, parsed, attempted_at=NOW)
            persistir_fee_observation(
                conn,
                oferta_observation_id=oid,
                oferta=oferta,
                resultado=parsed,
                observed_at=NOW,
                source_event_id=construir_fee_source_event_id(canon),
                canonical_input=canon,
            )
        conn.commit()
        n = conn.execute("SELECT count(*) FROM estimacion_fee_observation").fetchone()[0]
        assert n == 2


@_skip_db
def test_persistir_error_intento_distinto_mismo_codigo():
    with db_fees() as conn:
        pid = conn.execute(
            "INSERT INTO product (odoo_sku, name) VALUES ('P', 'P') RETURNING id"
        ).fetchone()[0]
        lid = conn.execute(
            "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
            " VALUES (%s, 'amazon_mx', 'B0EST01', 'SS-MX-1') RETURNING id",
            (pid,),
        ).fetchone()[0]
        conn.commit()
        oferta = _oferta_resuelta(listing_id=lid)
        oid = _sembrar_oferta(conn, lid, oferta)
        conn.commit()
        err = ResultadoCotizacion(
            estado="error",
            total_fees=None,
            fee_details=[],
            fees_estimated_at=None,
            error_code="fee_http_403",
            fetched_at=oferta.fetched_at,
        )
        for observed in (NOW, NOW + timedelta(minutes=1)):
            canon = construir_fee_outcome_canonical(oferta, err, attempted_at=observed)
            persistir_fee_observation(
                conn,
                oferta_observation_id=oid,
                oferta=oferta,
                resultado=err,
                observed_at=observed,
                source_event_id=construir_fee_source_event_id(canon),
                canonical_input=canon,
            )
        conn.commit()
        filas = conn.execute(
            "SELECT estado, error_code, total_fees, fee_details"
            " FROM estimacion_fee_observation ORDER BY observed_at"
        ).fetchall()
        assert len(filas) == 2
        assert all(f[0] == "error" and f[1] == "fee_http_403" for f in filas)
        assert all(f[2] is None and f[3] == [] for f in filas)


@_skip_db
def test_cotizar_oferta_sin_transaccion_abierta():
    psycopg = pytest.importorskip("psycopg")
    with db_fees() as conn:
        oferta = _oferta_resuelta()
        payload = _respuesta_exito(oferta)

        def handler(request: httpx.Request) -> httpx.Response:
            assert conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
            if request.url.host == "api.amazon.com":
                return _token_response()
            return httpx.Response(200, json=payload)

        client = make_client(handler)
        with conn.transaction():
            conn.execute("SELECT 1")
        assert conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
        resultado = cotizar_oferta(client, oferta, observed_at=NOW)
        assert resultado.estado == "success"


# ---------------------------------------------------------------------------
# (c) ORQUESTACION / CLI
# ---------------------------------------------------------------------------


@_skip_db
def test_ejecutar_ingesta_aislamiento_y_sello(tmp_path):
    snap = _snapshot_bridge(
        tmp_path / "bridge.db",
        [
            (
                "SS-MX-1",
                "B0EST01",
                "A1AM78C64UM0Y8",
                "amazon_mx",
                116.0,
                "AMAZON_NA",
                FETCH.isoformat(),
            ),
            (
                "SS-MX-2",
                "B0EST02",
                "A1AM78C64UM0Y8",
                "amazon_mx",
                120.0,
                "AMAZON_NA",
                FETCH.isoformat(),
            ),
        ],
    )
    payload_ok = _respuesta_exito(_oferta_resuelta(seller_sku="SS-MX-1"))
    intentos = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_response()
        sku = unquote(request.url.path.split("/listings/")[1].split("/feesEstimate")[0])
        intentos["n"] += 1
        if sku == "SS-MX-2":
            return httpx.Response(403, json={"errors": []})
        return httpx.Response(200, json=payload_ok)

    client = make_client(handler)

    with db_fees() as conn:
        pid = conn.execute(
            "INSERT INTO product (odoo_sku, name) VALUES ('P1', 'P1') RETURNING id"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
            " VALUES (%s, 'amazon_mx', 'B0EST01', 'SS-MX-1')",
            (pid,),
        )
        pid2 = conn.execute(
            "INSERT INTO product (odoo_sku, name) VALUES ('P2', 'P2') RETURNING id"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
            " VALUES (%s, 'amazon_mx', 'B0EST02', 'SS-MX-2')",
            (pid2,),
        )
        conn.commit()

        resultado = ejecutar_ingesta(conn, ruta_sqlite=snap, now_utc=NOW, client=client)
        conn.commit()

        assert resultado.ok is True
        assert resultado.ofertas_nuevas == 2
        assert resultado.fees_nuevos == 2
        assert resultado.fees_error == 1
        assert "fee_http_403" in (resultado.skip_reason or "")
        run = conn.execute(
            "SELECT rows_written, rows_skipped, skip_reason, ok, finished_at"
            " FROM ingest_run WHERE id = %s",
            (resultado.run_id,),
        ).fetchone()
        assert run[3] is True
        assert run[4] is not None
        fee_ok = conn.execute(
            "SELECT total_fees, fee_details FROM estimacion_fee_observation"
            " WHERE seller_sku = 'SS-MX-1'"
        ).fetchone()
        assert fee_ok[0] is not None
        fee_err = conn.execute(
            "SELECT total_fees, fee_details FROM estimacion_fee_observation"
            " WHERE seller_sku = 'SS-MX-2'"
        ).fetchone()
        assert fee_err[0] is None and fee_err[1] == []
        assert intentos["n"] == 2


@_skip_db
def test_ejecutar_ingesta_idempotente_segunda_corrida(tmp_path):
    snap = _snapshot_bridge(
        tmp_path / "bridge.db",
        [
            (
                "SS-MX-1",
                "B0EST01",
                "A1AM78C64UM0Y8",
                "amazon_mx",
                116.0,
                "AMAZON_NA",
                FETCH.isoformat(),
            ),
        ],
    )
    oferta = _oferta_resuelta()
    payload = _respuesta_exito(oferta)
    client = make_client(
        lambda request: (
            _token_response()
            if request.url.host == "api.amazon.com"
            else httpx.Response(200, json=payload)
        )
    )
    with db_fees() as conn:
        pid = conn.execute(
            "INSERT INTO product (odoo_sku, name) VALUES ('P', 'P') RETURNING id"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
            " VALUES (%s, 'amazon_mx', 'B0EST01', 'SS-MX-1')",
            (pid,),
        )
        conn.commit()
        r1 = ejecutar_ingesta(conn, ruta_sqlite=snap, now_utc=NOW, client=client)
        conn.commit()
        r2 = ejecutar_ingesta(conn, ruta_sqlite=snap, now_utc=NOW, client=client)
        conn.commit()
        assert r1.ofertas_nuevas == 1 and r1.fees_nuevos == 1
        assert r2.ofertas_reutilizadas == 1 and r2.fees_reutilizados == 1
        assert r2.rows_written == 0
        n = conn.execute("SELECT count(*) FROM estimacion_fee_observation").fetchone()[0]
        assert n == 1


@_skip_db
def test_ejecutar_ingesta_skip_reason_cerrado(tmp_path):
    snap = _snapshot_bridge(tmp_path / "empty.db", [])

    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("no debe salir red")

    with db_fees() as conn:
        with patch(
            "app.estimacion_ingest.resolver_oferta_para_listing",
            side_effect=RuntimeError("detalle externo SECRET"),
        ):
            pid = conn.execute(
                "INSERT INTO product (odoo_sku, name) VALUES ('P', 'P') RETURNING id"
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
                " VALUES (%s, 'amazon_mx', 'B0EST01', 'SS-MX-1')",
                (pid,),
            )
            conn.commit()
            resultado = ejecutar_ingesta(
                conn,
                ruta_sqlite=snap,
                now_utc=NOW,
                client=make_client(handler),
            )
        assert SKIP_OFERTA_EXCEPCION in (resultado.skip_reason or "")
        assert "SECRET" not in (resultado.skip_reason or "")
        assert "detalle externo" not in (resultado.skip_reason or "")


def test_cli_ingest_estimacion_despacha(monkeypatch):
    llamadas: list[list[str]] = []

    def fake_main(argv):
        llamadas.append(list(argv))
        return 0

    monkeypatch.setattr("app.estimacion_ingest.main", fake_main)
    codigo = cli.main(["ingest", "estimacion", "--sqlite", "/tmp/bridge.db"])
    assert codigo == 0
    assert llamadas == [["--sqlite", "/tmp/bridge.db"]]


def test_escenario_conserva_fecha_economica_si_fee_cruza_medianoche():
    fee_observed_at = datetime(2026, 9, 8, 23, 59, 59, 999999, tzinfo=UTC)
    conn = Mock()
    conn.transaction.return_value = nullcontext()
    persistido = Mock(reutilizada=False)

    with (
        patch("app.estimacion_ingest.sembrar_escenario_desde_refs") as sembrar,
        patch("app.estimacion_ingest.persistir_escenario", return_value=persistido),
    ):
        resultado = _persistir_escenario_item(
            conn,
            listing_id=1,
            oferta_id=2,
            fee_id=3,
            fee_observed_at=fee_observed_at,
        )

    assert resultado == (True, False)
    assert sembrar.call_args.kwargs["valoracion_date"] == date(2026, 9, 8)
    assert sembrar.call_args.kwargs["observed_at"] == datetime(2026, 9, 9, tzinfo=UTC)


def test_ingest_main_sin_dsn(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("ORBIT_DSN_INGEST", raising=False)
    snap = tmp_path / "x.db"
    snap.write_bytes(b"x")
    assert ingest_main(["--sqlite", str(snap)]) == 2


def test_ejecutar_ingesta_http_sin_txn(tmp_path):
    """Integracion ligera: verifica IDLE en handler durante pipeline."""
    psycopg = pytest.importorskip("psycopg")
    if _postgres_obligatorio_ausente():
        pytest.skip("sin Postgres")
    snap = _snapshot_bridge(
        tmp_path / "bridge.db",
        [
            (
                "SS-MX-1",
                "B0EST01",
                "A1AM78C64UM0Y8",
                "amazon_mx",
                116.0,
                "AMAZON_NA",
                FETCH.isoformat(),
            ),
        ],
    )
    estados: list[int] = []
    payload = _respuesta_exito(_oferta_resuelta())

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_response()
        return httpx.Response(200, json=payload)

    client = make_client(handler)
    original_cotizar = cotizar_oferta

    def cotizar_trackeado(fees_client, oferta, *, observed_at=None, now_utc=None):
        estados.append(conn_holder["conn"].info.transaction_status)
        return original_cotizar(fees_client, oferta, observed_at=observed_at, now_utc=now_utc)

    conn_holder: dict = {}

    with db_fees() as conn:
        conn_holder["conn"] = conn
        pid = conn.execute(
            "INSERT INTO product (odoo_sku, name) VALUES ('P', 'P') RETURNING id"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
            " VALUES (%s, 'amazon_mx', 'B0EST01', 'SS-MX-1')",
            (pid,),
        )
        conn.commit()
        with patch("app.estimacion_ingest.cotizar_oferta", side_effect=cotizar_trackeado):
            ejecutar_ingesta(conn, ruta_sqlite=snap, now_utc=NOW, client=client)
    assert estados == [psycopg.pq.TransactionStatus.IDLE]
