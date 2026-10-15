"""Costo de envio por orden y escalera de origen (REPRICING 02 F.2, puro).

`costo_de_orden` cuenta la etiqueta UNA vez por cualquiera de sus tres
caminos (`max(labman_label, shipping_label, mfn_postage)`) mas `shipping_hb`.
`resolver_envio` es la unica escalera: propio -> familia -> marketplace.
Sin etiqueta no hay costo; sin envios en el universo no hay muestra.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.precio.envio import (
    REGLA_VERSION,
    CargoEnvio,
    Exclusiones,
    OrdenMedida,
    clasificar_cargo,
    costo_de_orden,
    resolver_envio,
)
from app.precio.tipos import Importe, MonedasMezcladas

DIA = date(2026, 10, 1)
VENTANA = (date(2026, 4, 4), date(2026, 9, 30))


def _cargo(fuente: str, valor: str, moneda: str = "MXN") -> CargoEnvio:
    return CargoEnvio(
        orden="702-f2", fuente=fuente, importe=Importe(Decimal(valor), moneda), dia=DIA
    )


def _orden(producto: int, valor: str, moneda: str = "MXN") -> OrdenMedida:
    return OrdenMedida(
        orden=f"702-f2-{producto}-{valor}",
        product_id=producto,
        costo=Importe(Decimal(valor), moneda),
        dia=DIA,
    )


def _finanzas(subtipo: str) -> str:
    return f"amazon|finance|fee|702-f2||{subtipo}"


def test_clasifica_las_seis_identidades_de_f0():
    assert (
        clasificar_cargo(_finanzas("LabmanLabelPurchase"), "shipping_fee", "amazon_mx")
        == "labman_label"
    )
    assert (
        clasificar_cargo("amazon|shipping_label|702-f2|2026-10-01", "shipping_fee", "amazon_mx")
        == "shipping_label"
    )
    assert (
        clasificar_cargo(_finanzas("MFNPostageFee"), "shipping_fee", "amazon_mx") == "mfn_postage"
    )
    assert clasificar_cargo(_finanzas("ShippingHB"), "shipping_fee", "amazon_mx") == "shipping_hb"
    assert clasificar_cargo(_finanzas("ShippingChargeback"), "shipping_fee", "amazon_mx") == "otros"
    assert (
        clasificar_cargo(_finanzas("MFNShippingChargeback"), "shipping_fee", "amazon_mx") == "otros"
    )


def test_clasifica_lo_desconocido_y_lo_malformado_como_otros():
    assert clasificar_cargo(_finanzas("SubtipoNuevo"), "shipping_fee", "amazon_mx") == "otros"
    assert clasificar_cargo(None, "shipping_fee", "amazon_mx") == "otros"
    assert clasificar_cargo("", "shipping_fee", "amazon_mx") == "otros"
    assert clasificar_cargo("sin-pipes", "shipping_fee", "amazon_mx") == "otros"
    assert clasificar_cargo("a|finance", "shipping_fee", "amazon_mx") == "otros"
    assert clasificar_cargo(_finanzas("MFNPostageFee"), "referral_fee", "amazon_mx") == "otros"


def test_costo_cuenta_la_etiqueta_una_vez_mas_hb():
    cargos = [
        _cargo("shipping_label", "450"),
        _cargo("labman_label", "449"),
        _cargo("shipping_hb", "80"),
    ]
    assert costo_de_orden(cargos) == Importe(Decimal("530"), "MXN")


def test_costo_con_solo_mfn_postage():
    assert costo_de_orden([_cargo("mfn_postage", "91")]) == Importe(Decimal("91"), "MXN")


def test_costo_ignora_otros_y_sin_etiqueta_no_hay_costo():
    assert costo_de_orden([_cargo("shipping_label", "450"), _cargo("otros", "999")]) == Importe(
        Decimal("450"), "MXN"
    )
    assert costo_de_orden([_cargo("otros", "999"), _cargo("shipping_hb", "80")]) is None
    assert costo_de_orden([_cargo("otros", "100")]) is None
    assert costo_de_orden([]) is None


def test_costo_solo_hb_no_es_etiqueta():
    assert costo_de_orden([_cargo("shipping_hb", "80")]) is None


def test_costo_etiquetas_moneda_distinta_revienta():
    with pytest.raises(MonedasMezcladas):
        costo_de_orden([_cargo("shipping_label", "450"), _cargo("labman_label", "449", "USD")])


def test_costo_hb_moneda_distinta_revienta():
    with pytest.raises(MonedasMezcladas):
        costo_de_orden([_cargo("shipping_label", "450"), _cargo("shipping_hb", "5", "USD")])


def test_escalera_con_envios_propios_da_propio():
    muestra = resolver_envio(
        7,
        propias=[_orden(7, "100"), _orden(7, "200")],
        hermanas=[_orden(8, "1")],
        familia_id=3,
        marketplace=[_orden(9, "1")],
        plataforma="amazon_mx",
        canal="fbm",
        ventana=VENTANA,
    )
    assert muestra is not None
    assert muestra.origen == "propio"
    assert muestra.valor == Importe(Decimal("150"), "MXN")
    assert muestra.mediana == Importe(Decimal("150"), "MXN")
    assert muestra.p90 == Importe(Decimal("200"), "MXN")
    assert muestra.maximo == Importe(Decimal("200"), "MXN")
    assert muestra.envios == 2
    assert muestra.donantes is None
    assert muestra.familia_id is None
    assert muestra.regla_version == REGLA_VERSION


def test_escalera_con_un_solo_envio_propio_basta():
    muestra = resolver_envio(
        7,
        propias=[_orden(7, "300")],
        hermanas=[],
        familia_id=None,
        marketplace=[],
        plataforma="amazon_us",
        canal="fbm",
        ventana=VENTANA,
    )
    assert muestra is not None
    assert (muestra.origen, muestra.valor) == ("propio", Importe(Decimal("300"), "MXN"))


def test_escalera_sin_propios_con_familia_da_mediana_de_medianas():
    muestra = resolver_envio(
        7,
        propias=[],
        hermanas=[_orden(8, "100"), _orden(8, "200"), _orden(9, "300")],
        familia_id=3,
        marketplace=[_orden(10, "1")],
        plataforma="amazon_mx",
        canal="fbm",
        ventana=VENTANA,
    )
    assert muestra is not None
    assert muestra.origen == "familia"
    assert muestra.valor == Importe(Decimal("225"), "MXN")
    assert muestra.donantes == 2
    assert muestra.familia_id == 3
    assert muestra.envios == 3


def test_escalera_sin_familia_da_marketplace():
    muestra = resolver_envio(
        7,
        propias=[],
        hermanas=[],
        familia_id=None,
        marketplace=[_orden(8, "100"), _orden(8, "200"), _orden(9, "400")],
        plataforma="amazon_mx",
        canal="fbm",
        ventana=VENTANA,
    )
    assert muestra is not None
    assert muestra.origen == "marketplace"
    assert muestra.valor == Importe(Decimal("275"), "MXN")
    assert muestra.donantes == 2
    assert muestra.familia_id is None


def test_escalera_sin_ningun_envio_no_da_muestra():
    assert (
        resolver_envio(
            7,
            propias=[],
            hermanas=[],
            familia_id=None,
            marketplace=[],
            plataforma="amazon_mx",
            canal="fbm",
            ventana=VENTANA,
        )
        is None
    )


def test_escalera_con_cero_propios_no_da_propio():
    muestra = resolver_envio(
        7,
        propias=[],
        hermanas=[_orden(8, "50")],
        familia_id=3,
        marketplace=[],
        plataforma="amazon_mx",
        canal="fbm",
        ventana=VENTANA,
    )
    assert muestra is not None
    assert muestra.origen == "familia"


def test_escalera_hermanas_sin_familia_cae_a_marketplace():
    muestra = resolver_envio(
        7,
        propias=[],
        hermanas=[_orden(8, "50")],
        familia_id=None,
        marketplace=[_orden(9, "70")],
        plataforma="amazon_mx",
        canal="fbm",
        ventana=VENTANA,
    )
    assert muestra is not None
    assert (muestra.origen, muestra.valor) == ("marketplace", Importe(Decimal("70"), "MXN"))


def test_exclusiones_fija_el_orden_posicional():
    assert Exclusiones(0, 0, 0) == Exclusiones(
        ordenes_multiproducto=0, ordenes_sin_producto=0, cargos_otros=0
    )
    conteo = Exclusiones(1, 2, 3)
    assert (conteo.ordenes_multiproducto, conteo.ordenes_sin_producto, conteo.cargos_otros) == (
        1,
        2,
        3,
    )
