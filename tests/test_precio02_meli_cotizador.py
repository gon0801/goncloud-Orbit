"""Cotizador MeLi (REPRICING 02 M.3).

`CotizadorMeli` cotiza por GET a `/sites/MLM/listing_prices` (conclusion 2
de M.0: separa porcentaje y fijo) y parte en variable/fijo. Sin red para
lo que el registro no trae; errores de plataforma son resultado, no
excepcion. Precio invalido falla fuerte, igual que `cotizar_a_precio`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from app.estimacion_insumos import OfertaResuelta
from app.reputacion_clientes import ClienteMeli, MeliCredentials

AHORA = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)


def _creds() -> MeliCredentials:
    return MeliCredentials(access_token="tok")


def _oferta(**kwargs) -> OfertaResuelta:
    base = {
        "listing_id": 7,
        "platform": "meli",
        "seller_sku": "SKU-M3-A",
        "asin": "MLM123",
        "canal": "meli",
        "price_amount": Decimal("979.11"),
        "price_currency": "MXN",
        "fetched_at": AHORA,
        "canonical_input": {"categoria_id": "MLM1440", "listing_type_id": "gold_pro"},
        "context_fingerprint": "meli:979.11:MXN",
        "source_event_id": "meli-oferta:x",
    }
    base.update(kwargs)
    return OfertaResuelta(**base)


def _cliente(handler, **kwargs) -> ClienteMeli:
    kwargs.setdefault("backoff_base", 0.0)
    return ClienteMeli(_creds(), transport=httpx.MockTransport(handler), **kwargs)


RESPUESTA_M0 = {
    "currency_id": "MXN",
    "sale_fee_amount": 190.93,
    "sale_fee_details": {"percentage_fee": 19.5, "fixed_fee": 0, "gross_amount": 190.93},
    "listing_fee_amount": 0,
    "listing_fee_details": {"fixed_fee": 0, "gross_amount": 0},
}


def test_cotiza_por_listing_prices_y_parte_desde_montos():
    from app.meli.estimacion import CotizadorMeli

    vistas = []

    def handler(request: httpx.Request) -> httpx.Response:
        vistas.append(request)
        return httpx.Response(200, json=RESPUESTA_M0)

    cot = CotizadorMeli(_cliente(handler))
    res = cot.cotizar(_oferta(), Decimal("979.11"), ahora=lambda: AHORA)
    assert res.estado == "success"
    assert res.total_fees == Decimal("190.93")
    assert [(d["fee_type"], d["final_fee"]) for d in res.fee_details] == [
        ("SaleFee", "190.9300"),
        ("SaleFeeFixed", "0.0000"),
        ("ListingFee", "0.0000"),
    ]
    assert res.fees_estimated_at == res.fetched_at == AHORA
    assert len(vistas) == 1
    assert vistas[0].url.path == "/sites/MLM/listing_prices"
    params = dict(vistas[0].url.params)
    assert params == {
        "price": "979.11",
        "category_id": "MLM1440",
        "listing_type_id": "gold_pro",
    }


def test_cotiza_con_fijo_y_publicacion():
    from app.meli.estimacion import CotizadorMeli

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "currency_id": "MXN",
                "sale_fee_amount": 190.93,
                "sale_fee_details": {"percentage_fee": 19.0, "fixed_fee": 5},
                "listing_fee_amount": 2,
            },
        )

    cot = CotizadorMeli(_cliente(handler))
    res = cot.cotizar(_oferta(), Decimal("979.11"), ahora=lambda: AHORA)
    assert res.estado == "success"
    assert res.total_fees == Decimal("192.93")
    assert [(d["fee_type"], d["final_fee"]) for d in res.fee_details] == [
        ("SaleFee", "185.9300"),
        ("SaleFeeFixed", "5.0000"),
        ("ListingFee", "2.0000"),
    ]


def test_monto_con_mas_de_4_decimales_es_incompatible():
    from app.meli.estimacion import CotizadorMeli

    cuerpo = dict(RESPUESTA_M0)
    cuerpo["sale_fee_amount"] = 190.93666

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=cuerpo)

    cot = CotizadorMeli(_cliente(handler))
    res = cot.cotizar(_oferta(), Decimal("979.11"), ahora=lambda: AHORA)
    assert res.estado == "error"
    assert res.error_code == "fee_contrato_incompatible"


def test_universo_sin_cotizador_no_toca_red():
    from app.meli.estimacion import CotizadorMeli

    llamadas = []

    def handler(request: httpx.Request) -> httpx.Response:
        llamadas.append(request)
        return httpx.Response(200, json=RESPUESTA_M0)

    cot = CotizadorMeli(_cliente(handler))
    res = cot.cotizar(
        _oferta(platform="amazon_mx", canal="fba"), Decimal("979.11"), ahora=lambda: AHORA
    )
    assert res.estado == "error"
    assert res.error_code == "fee_universo_no_soportado"
    assert llamadas == []


def test_error_500_da_fee_error_sin_levantar():
    from app.meli.estimacion import CotizadorMeli

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"message": "x"})

    cot = CotizadorMeli(_cliente(handler, max_intentos=2))
    res = cot.cotizar(_oferta(), Decimal("979.11"), ahora=lambda: AHORA)
    assert res.estado == "error"
    assert res.error_code == "fee_error"
    assert res.total_fees is None


def test_respuesta_ilegible_da_contrato_incompatible():
    from app.meli.estimacion import CotizadorMeli

    for cuerpo in ({}, {"sale_fee_amount": "mucho"}, {"sale_fee_amount": 10}):

        def handler(request: httpx.Request, c=cuerpo) -> httpx.Response:
            return httpx.Response(200, json=c)

        cot = CotizadorMeli(_cliente(handler))
        res = cot.cotizar(_oferta(), Decimal("979.11"), ahora=lambda: AHORA)
        assert res.estado == "error"
        assert res.error_code == "fee_contrato_incompatible"


def test_listing_fee_ausente_o_moneda_distinta_es_incompatible():
    from app.meli.estimacion import CotizadorMeli

    sin_listing = dict(RESPUESTA_M0)
    del sin_listing["listing_fee_amount"]
    moneda_mala = dict(RESPUESTA_M0)
    moneda_mala["currency_id"] = "USD"
    for cuerpo in (sin_listing, moneda_mala, {"currency_id": "MXN"}):

        def handler(request: httpx.Request, c=cuerpo) -> httpx.Response:
            return httpx.Response(200, json=c)

        cot = CotizadorMeli(_cliente(handler))
        res = cot.cotizar(_oferta(), Decimal("979.11"), ahora=lambda: AHORA)
        assert res.estado == "error"
        assert res.error_code == "fee_contrato_incompatible"


def test_categoria_ausente_da_fee_incompatible_sin_red():
    from app.meli.estimacion import CotizadorMeli

    llamadas = []

    def handler(request: httpx.Request) -> httpx.Response:
        llamadas.append(request)
        return httpx.Response(200, json=RESPUESTA_M0)

    cot = CotizadorMeli(_cliente(handler))
    res = cot.cotizar(_oferta(canonical_input={}), Decimal("979.11"), ahora=lambda: AHORA)
    assert res.estado == "error"
    assert res.error_code == "fee_incompatible"
    assert llamadas == []


def test_precio_invalido_falla_fuerte():
    from app.meli.estimacion import CotizadorMeli

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no debe salir a red")

    cot = CotizadorMeli(_cliente(handler))
    for malo in (Decimal("0"), Decimal("-1"), Decimal("NaN"), "979.11", Decimal("979.111")):
        with pytest.raises(ValueError):
            cot.cotizar(_oferta(), malo, ahora=lambda: AHORA)


def test_partir_fees_meli():
    from app.estimacion_venta import DetalleFee
    from app.meli.estimacion import partir_fees_meli

    def det(tipo, monto):
        return DetalleFee(fee_type=tipo, final_fee=Decimal(monto))

    assert partir_fees_meli(
        (det("SaleFee", "190.93"), det("SaleFeeFixed", "0"), det("ListingFee", "0")),
        Decimal("190.93"),
    ) == (Decimal("190.93"), Decimal("0"))
    assert partir_fees_meli(
        (det("SaleFee", "100"), det("SaleFeeFixed", "5"), det("ListingFee", "2")),
        Decimal("107"),
    ) == (Decimal("100"), Decimal("7"))
    assert partir_fees_meli((), Decimal("10")) == "fee_incompatible"
    assert partir_fees_meli((det("ListingFee", "10"),), Decimal("10")) == "fee_incompatible"
    assert (
        partir_fees_meli((det("SaleFee", "5"), det("SaleFee", "5")), Decimal("10"))
        == "fee_incompatible"
    )
    assert partir_fees_meli((det("SaleFee", "0"),), Decimal("10")) == "fee_incompatible"


def test_canonica_fee_meli_sin_marketplace_y_estable():
    from app.meli.estimacion import (
        construir_fee_canonical_input_meli,
        construir_fee_source_event_id_meli,
    )

    canon = construir_fee_canonical_input_meli(_oferta())
    assert "marketplace_id" not in canon
    assert canon["categoria_id"] == "MLM1440"
    assert canon["listing_type_id"] == "gold_pro"
    assert construir_fee_source_event_id_meli(canon).startswith("meli-fee:")
    assert construir_fee_source_event_id_meli(canon) == construir_fee_source_event_id_meli(
        dict(canon)
    )
    otro = construir_fee_canonical_input_meli(_oferta(price_amount=Decimal("100.00")))
    assert construir_fee_source_event_id_meli(otro) != construir_fee_source_event_id_meli(canon)
