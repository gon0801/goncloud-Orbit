"""ENVIOS 01: cero PII y traductor Amazon (puro, sin red ni base)."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime

from app.envios.amazon import envio_desde_orden
from app.envios.modelo import Articulo, EnvioObservado, PaqueteObservado
from app.spapi.client import sanear
from app.spapi.orders import parsear_orden

_DIRECCION = {"name": "CENTINELA_NOMBRE", "addressLine1": "CENTINELA_CALLE", "postalCode": "53240"}


def _traducir(cruda: dict, platform: str = "amazon_mx") -> EnvioObservado:
    return envio_desde_orden(cruda, platform=platform, orden=parsear_orden(cruda))


def _orden(**mas) -> dict:
    return {
        "orderId": "701-1",
        "createdTime": "2026-11-20T10:00:00Z",
        "lastUpdatedTime": "2026-11-24T11:00:00Z",
        **mas,
    }


def test_envio_desde_orden_lista_cerrada_sin_pii_y_multipaquete():
    # Se traduce el crudo SIN sanear: la lista cerrada sola tiene que bastar.
    cruda = _orden(
        buyer={"buyerName": "CENTINELA_COMPRADOR", "buyerEmail": "CENTINELA_CORREO"},
        recipient={"deliveryAddress": _DIRECCION},
        shipFromAddress=_DIRECCION,
        destination="CENTINELA_DESTINO",
        comments="CENTINELA_COMENTARIO",
        proceeds={"grandTotal": {"amount": "749.00", "currencyCode": "MXN"}},
        fulfillment={
            "fulfilledBy": "MERCHANT",
            "fulfillmentStatus": "SHIPPED",
            "shipByWindow": {
                "earliestDateTime": "2026-11-21T06:00:00Z",
                "latestDateTime": "2026-11-22T05:59:59Z",
            },
            "deliverByWindow": {
                "earliestDateTime": "2026-11-24T06:00:00Z",
                "latestDateTime": "2026-11-27T05:59:59Z",
            },
        },
        orderItems=[
            {
                "orderItemId": "1",
                "quantityOrdered": 2,
                "product": {"asin": "B000000001", "sellerSku": "SKU-1", "title": "Pieza uno"},
                "buyer": {"giftMessage": "CENTINELA_REGALO"},
                "proceeds": {"total": {"amount": "500.00", "currencyCode": "MXN"}},
            },
            {
                "orderItemId": "2",
                "quantityOrdered": 1,
                "product": {"asin": "B000000002", "sellerSku": "SKU-2", "title": "Pieza dos"},
            },
        ],
        packages=[
            {
                "packageReferenceId": "1",
                "carrier": "AMZMX",
                "shippingService": "Estandar",
                "trackingNumber": "GUIA-1",
                "shipTime": "2026-11-21T15:00:00Z",
                "createdTime": "2026-11-21T14:00:00Z",
                "packageStatus": {"status": "DELIVERED", "detailedStatus": "DELIVERED"},
                "shipFromAddress": _DIRECCION,
                "packageItems": [{"orderItemId": "1", "quantity": 2}],
            },
            {
                "packageReferenceId": "2",
                "carrier": "AMZMX",
                "shippingService": "Express",
                "trackingNumber": "GUIA-2",
                "shipTime": "2026-11-21T16:00:00Z",
                "packageStatus": {
                    "status": "UNDELIVERABLE",
                    "detailedStatus": "RETURNING_TO_SELLER",
                },
                "recipient": _DIRECCION,
            },
            {"carrier": "AMZMX", "trackingNumber": "GUIA-SIN-REFERENCIA"},
        ],
    )

    envio = _traducir(cruda)

    assert envio == EnvioObservado(
        platform="amazon_mx",
        envio_ref="701-1",
        pedidos=("701-1",),
        pack_ref=None,
        logistica_fuente="MERCHANT",
        estado_fuente="SHIPPED",
        subestado_fuente=None,
        pedido_creado_el=datetime(2026, 11, 20, 10, 0, tzinfo=UTC),
        fuente_actualizado_el=datetime(2026, 11, 24, 11, 0, tzinfo=UTC),
        despachar_antes_de=datetime(2026, 11, 22, 5, 59, 59, tzinfo=UTC),
        entrega_prometida_el=datetime(2026, 11, 27, 5, 59, 59, tzinfo=UTC),
        entrega_limite_el=None,
        entregado_el=None,
        devuelto_el=None,
        articulos=(
            Articulo("701-1", "SKU-1", "Pieza uno", 2, "B000000001"),
            Articulo("701-1", "SKU-2", "Pieza dos", 1, "B000000002"),
        ),
        paquetes=(
            PaqueteObservado(
                paquete_ref="1",
                transportista="AMZMX",
                servicio="Estandar",
                guia="GUIA-1",
                despachado_el=datetime(2026, 11, 21, 15, 0, tzinfo=UTC),
                estado_fuente="DELIVERED",
                detalle_fuente="DELIVERED",
            ),
            PaqueteObservado(
                paquete_ref="2",
                transportista="AMZMX",
                servicio="Express",
                guia="GUIA-2",
                despachado_el=datetime(2026, 11, 21, 16, 0, tzinfo=UTC),
                estado_fuente="UNDELIVERABLE",
                detalle_fuente="RETURNING_TO_SELLER",
            ),
        ),
    )
    texto = json.dumps(asdict(envio), default=str)
    assert "CENTINELA" not in texto
    assert "53240" not in texto


def test_paquetes_deduplican_por_referencia_y_aceptan_entero():
    # La sonda no registra el tipo de packageReferenceId y store.guardar aborta
    # el lote entero con una referencia repetida: gana la primera aparicion.
    envio = _traducir(
        _orden(
            packages=[
                {"packageReferenceId": 7, "trackingNumber": "PRIMERA"},
                {"packageReferenceId": "7", "trackingNumber": "REPETIDA"},
                {"packageReferenceId": " 8 ", "trackingNumber": "OCHO"},
                {"packageReferenceId": 0, "trackingNumber": "CERO"},
            ]
        )
    )
    assert [(p.paquete_ref, p.guia) for p in envio.paquetes] == [
        ("7", "PRIMERA"),
        ("8", "OCHO"),
        ("0", "CERO"),
    ]


def test_paquete_sin_referencia_usable_se_omite():
    sin_referencia = [None, "", "   ", True, 1.5, {"id": "1"}]
    envio = _traducir(
        _orden(
            packages=[{"packageReferenceId": r} for r in sin_referencia]
            + ["no-es-dict", {"packageReferenceId": "9"}]
        )
    )
    assert envio.paquetes == (PaqueteObservado("9", None, None, None, None, None, None),)


def test_fecha_ilegible_queda_none_sin_tumbar_la_orden():
    envio = _traducir(
        _orden(
            fulfillment={
                "shipByWindow": {"latestDateTime": "no-es-fecha"},
                # Legible como texto pero fuera de rango al pasar a UTC.
                "deliverByWindow": {"latestDateTime": "0001-01-01T00:00:00+14:00"},
            },
            packages=[
                {"packageReferenceId": "1", "shipTime": 1763740800},
                # Sin zona se lee como UTC, igual que en parsear_orden.
                {"packageReferenceId": "2", "shipTime": "2026-11-21T15:00:00"},
                {"packageReferenceId": "3", "shipTime": "2026-11-21T09:00:00-06:00"},
            ],
        )
    )
    assert envio.despachar_antes_de is None
    assert envio.entrega_prometida_el is None
    assert [p.despachado_el for p in envio.paquetes] == [
        None,
        datetime(2026, 11, 21, 15, 0, tzinfo=UTC),
        datetime(2026, 11, 21, 15, 0, tzinfo=UTC),
    ]


def test_datos_ausentes_o_mal_formados_quedan_none():
    envio = _traducir(
        _orden(
            fulfillment="no-es-dict",
            orderItems=[
                {"quantityOrdered": -1, "product": "no-es-dict"},
                {"quantityOrdered": True, "product": {"asin": " ", "sellerSku": 5}},
                "no-es-dict",
            ],
            packages={"no": "lista"},
        ),
        platform="amazon_us",
    )
    assert envio == EnvioObservado(
        platform="amazon_us",
        envio_ref="701-1",
        pedidos=("701-1",),
        pack_ref=None,
        logistica_fuente=None,
        estado_fuente=None,
        subestado_fuente=None,
        pedido_creado_el=datetime(2026, 11, 20, 10, 0, tzinfo=UTC),
        fuente_actualizado_el=datetime(2026, 11, 24, 11, 0, tzinfo=UTC),
        despachar_antes_de=None,
        entrega_prometida_el=None,
        entrega_limite_el=None,
        entregado_el=None,
        devuelto_el=None,
        articulos=(
            Articulo("701-1", None, None, None, None),
            Articulo("701-1", None, None, None, None),
        ),
        paquetes=(),
    )


def test_sanear_descarta_shipfromaddress_completo():
    # Sonda 2026-11-25: en amazon_us el paquete trae el domicilio del remitente
    # y el `name` sobrevivia al filtro por campos de direccion.
    carga = {
        "orders": [
            {
                "orderId": "US-1",
                "packages": [
                    {
                        "packageReferenceId": "1",
                        "carrier": "DHL",
                        "shipFromAddress": {
                            "name": "CENTINELA_NOMBRE",
                            "addressLine1": "CENTINELA_CALLE",
                            "postalCode": "53240",
                        },
                    }
                ],
            }
        ]
    }
    assert sanear(carga, "orders") == {
        "orders": [{"orderId": "US-1", "packages": [{"packageReferenceId": "1", "carrier": "DHL"}]}]
    }
