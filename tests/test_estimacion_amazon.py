"""Cotizador del universo Amazon y particion variable/fijo (REPRICING 02 F.3).

`CotizadorAmazon` es el `cotizador` de los tres universos Amazon del
registro: cotiza con el marketplace y fulfillment del universo de la oferta.
`partir_fees` parte el arbol con el MISMO predicado que
`derivar_ref_fijo` (ReferralFee unico con monto positivo): variable es el
ReferralFee, fijo es el resto. Sin red real (MockTransport).
"""

from __future__ import annotations

import json as json_stdlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx

from app.estimacion_amazon import CotizadorAmazon, partir_fees
from app.estimacion_fees import ProductFeesClient
from app.estimacion_insumos import (
    FilaOfertaBridge,
    OfertaResuelta,
    construir_canonical_input,
    construir_context_fingerprint,
    construir_source_event_id,
)
from app.estimacion_venta import DetalleFee

NOW = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)
FETCH = NOW - timedelta(hours=1)
FEE_TIME = NOW - timedelta(minutes=30)


def _oferta(
    *,
    platform: str = "amazon_mx",
    canal: str = "fbm",
    precio: str = "116.0000",
    moneda: str = "MXN",
) -> OfertaResuelta:
    fila = FilaOfertaBridge(
        seller_sku="SS-F3-1",
        asin="B0F300001",
        marketplace_id=("ATVPDKIKX0DER" if platform == "amazon_us" else "A1AM78C64UM0Y8"),
        marketplace_name=platform,
        price=float(precio),
        fulfillment_channel="DEFAULT" if canal == "fbm" else "AMAZON_NA",
        fetched_at=FETCH.isoformat(),
    )
    monto = Decimal(precio)
    canon = construir_canonical_input(fila, platform, canal, monto, moneda)
    return OfertaResuelta(
        listing_id=1,
        platform=platform,
        seller_sku="SS-F3-1",
        asin="B0F300001",
        canal=canal,
        price_amount=monto,
        price_currency=moneda,
        fetched_at=FETCH,
        canonical_input=canon,
        context_fingerprint=construir_context_fingerprint(canal, monto, moneda, FETCH),
        source_event_id=construir_source_event_id(canon),
    )


def _respuesta(detalles: list[tuple[str, str]], oferta: OfertaResuelta) -> dict:
    moneda = oferta.price_currency
    total = sum(Decimal(monto) for _, monto in detalles)
    return {
        "payload": {
            "FeesEstimateResult": {
                "Status": "Success",
                "FeesEstimateIdentifier": {
                    "MarketplaceId": (
                        "ATVPDKIKX0DER" if oferta.platform == "amazon_us" else "A1AM78C64UM0Y8"
                    ),
                    "IdType": "SellerSKU",
                    "IdValue": oferta.seller_sku,
                    "SellerInputIdentifier": oferta.source_event_id,
                    "IsAmazonFulfilled": oferta.canal == "fba",
                    "PriceToEstimateFees": {
                        "ListingPrice": {
                            "CurrencyCode": moneda,
                            "Amount": float(oferta.price_amount),
                        }
                    },
                },
                "FeesEstimate": {
                    "TimeOfFeesEstimation": FEE_TIME.isoformat(),
                    "TotalFeesEstimate": {
                        "CurrencyCode": moneda,
                        "Amount": float(total),
                    },
                    "FeeDetailList": [
                        {
                            "FeeType": tipo,
                            "FeeAmount": {"CurrencyCode": moneda, "Amount": float(monto)},
                            "FinalFee": {"CurrencyCode": moneda, "Amount": float(monto)},
                        }
                        for tipo, monto in detalles
                    ],
                },
            }
        },
    }


def _cliente(respuesta: dict, pedidos: list) -> ProductFeesClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return httpx.Response(200, json={"access_token": "fake", "expires_in": 3600})
        pedidos.append(json_stdlib.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json=respuesta)

    return ProductFeesClient(
        secrets_dir=None,
        credentials={
            "lwa_app_id": "fake",
            "lwa_client_secret": "fake",
            "refresh_token": "fake",
        },
        transport=httpx.MockTransport(handler),
        sleep=lambda _s: None,
        clock=lambda: 0.0,
    )


def test_cotizador_fbm_mx_pide_mfn():
    oferta = _oferta()
    pedidos: list = []
    cotizador = CotizadorAmazon(_cliente(_respuesta([("ReferralFee", "5.00")], oferta), pedidos))
    resultado = cotizador.cotizar(oferta, Decimal("116.0000"), ahora=lambda: NOW)
    assert resultado.estado == "success"
    (pedido,) = pedidos
    fees = pedido["FeesEstimateRequest"]
    assert fees["MarketplaceId"] == "A1AM78C64UM0Y8"
    assert fees["IsAmazonFulfilled"] is False


def test_cotizador_us_pide_marketplace_us():
    oferta = _oferta(platform="amazon_us", precio="29.99", moneda="USD")
    pedidos: list = []
    cotizador = CotizadorAmazon(_cliente(_respuesta([("ReferralFee", "4.50")], oferta), pedidos))
    resultado = cotizador.cotizar(oferta, Decimal("29.99"), ahora=lambda: NOW)
    assert resultado.estado == "success"
    (pedido,) = pedidos
    assert pedido["FeesEstimateRequest"]["MarketplaceId"] == "ATVPDKIKX0DER"


def test_cotizador_fuera_del_registro_no_sale_a_red():
    oferta = _oferta(platform="amazon_us", canal="fba", precio="29.99", moneda="USD")
    pedidos: list = []
    cotizador = CotizadorAmazon(_cliente(_respuesta([("ReferralFee", "4.50")], oferta), pedidos))
    resultado = cotizador.cotizar(oferta, Decimal("29.99"), ahora=lambda: NOW)
    assert (resultado.estado, resultado.error_code) == ("error", "fee_universo_no_soportado")
    assert pedidos == []


def _detalles(*pares: tuple[str, str]) -> tuple[DetalleFee, ...]:
    return tuple(DetalleFee(fee_type=tipo, final_fee=Decimal(monto)) for tipo, monto in pares)


def test_partir_referral_unico():
    variable, fijo = partir_fees(
        _detalles(("FBAFees", "10.0000"), ("ReferralFee", "5.0000")), Decimal("15.0000")
    )
    assert (variable, fijo) == (Decimal("5.0000"), Decimal("10.0000"))


def test_partir_solo_referral_fijo_cero():
    variable, fijo = partir_fees(_detalles(("ReferralFee", "5.0000")), Decimal("5.0000"))
    assert (variable, fijo) == (Decimal("5.0000"), Decimal("0.0000"))


def test_partir_arbol_us():
    variable, fijo = partir_fees(
        _detalles(
            ("ReferralFee", "4.5000"),
            ("VariableClosingFee", "0.3000"),
            ("PerItemFee", "1.8000"),
        ),
        Decimal("6.6000"),
    )
    assert (variable, fijo) == (Decimal("4.5000"), Decimal("2.1000"))


def test_partir_sin_referral_da_motivo():
    assert partir_fees(_detalles(("FBAFees", "10.0000")), Decimal("10.0000")) == "fee_incompatible"


def test_partir_dos_referrals_da_motivo():
    assert (
        partir_fees(
            _detalles(("ReferralFee", "5.0000"), ("ReferralFee", "1.0000")),
            Decimal("6.0000"),
        )
        == "fee_incompatible"
    )


def test_partir_referral_cero_no_cuenta():
    assert (
        partir_fees(
            _detalles(("ReferralFee", "0.0000"), ("FBAFees", "10.0000")),
            Decimal("10.0000"),
        )
        == "fee_incompatible"
    )


def test_partir_y_derivar_coinciden_en_mismo_arbol():
    from app.precio.objetivo import derivar_ref_fijo

    detalles = _detalles(("FBAFees", "10.0000"), ("ReferralFee", "5.0000"))
    variable, fijo = partir_fees(detalles, Decimal("15.0000"))
    _ref, fijo_derivado = derivar_ref_fijo(detalles, Decimal("15.0000"), Decimal("116.0000"))
    assert fijo == fijo_derivado
