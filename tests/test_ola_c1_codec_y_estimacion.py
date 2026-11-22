"""Pruebas unitarias de Ola C1: codec por tipos de CasoHoja, ReglasPolitica y DetalleFee."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from test_optimizer_caso import fabrica_caso

from app.estimacion_venta import (
    DetalleFee,
    PoliticaCalculo,
    detalle_fee_a_json,
    detalle_fee_desde_json,
    detalles_fee_desde_json,
    parsear_politica,
)
from app.optimizer.caso import CasoHoja


def test_caso_codec_roundtrip_y_errores_estrictos():
    caso = fabrica_caso()
    crudo = caso.como_json()
    reconstruido = CasoHoja.desde_json(crudo)
    assert reconstruido == caso

    # Clave faltante falla cerrado
    incompleto = dict(crudo)
    del incompleto["bid"]
    with pytest.raises(ValueError, match="claves distintas"):
        CasoHoja.desde_json(incompleto)

    # Clave extra desconocida falla cerrado
    con_extra = {**crudo, "campo_fantasma": 123}
    with pytest.raises(ValueError, match="claves distintas"):
        CasoHoja.desde_json(con_extra)

    # Booleano en campo int falla cerrado (bool es subclase de int en Python)
    bad_bool = {**crudo, "hoja_id": True}
    with pytest.raises(ValueError, match="int|entero"):
        CasoHoja.desde_json(bad_bool)

    # Float en campo Decimal falla cerrado (Regla 4)
    bad_float = {**crudo, "bid": {**crudo["bid"], "valor": 10.5}}
    with pytest.raises(ValueError, match="string decimal"):
        CasoHoja.desde_json(bad_float)

    # Marcadores opcionales de eras nuevas: se eliminan del dict ya poblado y el
    # codec historico los reconstruye como None.
    crudo_con_claves = {
        **crudo,
        "temporada": None,
        "historia": None,
        "recuperacion": None,
        "presupuesto": None,
        "trayectoria": {**crudo["trayectoria"], "episodio": None},
    }
    sin_opcionales = dict(crudo_con_claves)
    for opcional in ("temporada", "historia", "recuperacion", "presupuesto"):
        assert opcional in sin_opcionales
        del sin_opcionales[opcional]
    sin_opcionales["trayectoria"] = dict(sin_opcionales["trayectoria"])
    assert "episodio" in sin_opcionales["trayectoria"]
    del sin_opcionales["trayectoria"]["episodio"]
    assert CasoHoja.desde_json(sin_opcionales) == caso


def test_sembrar_amazon_preserva_orden_motivos_costo_antes_de_fee(monkeypatch):
    """Amazon mantiene previos -> costo/FX -> fee -> logistica (huella estable)."""
    from datetime import UTC
    from types import SimpleNamespace

    from app import estimacion_siembra as siembra
    from app.estimacion_universo import universo_de

    monkeypatch.setattr(
        siembra,
        "resolver_costo",
        lambda *_a, **_kw: SimpleNamespace(motivo="costo_ausente", costo=None),
    )
    monkeypatch.setattr(
        siembra,
        "logistica_del_universo",
        lambda *_a, **_kw: "envio_sin_historia",
    )
    pol = PoliticaCalculo(
        politica_version_id=1,
        label="mx-s4",
        universo="amazon_mx/fbm",
        formula_version="S4",
        settings={
            "precio_incluye_iva": True,
            "iva_divisor": "1.16",
            "isr_tasa": "0.025",
            "retencion_iva_tasa": "0.08",
            "referral_fee_iva_multiplicador": "1.16",
            "logistica": "0",
            "logistica_semantica": "muestra",
            "fee_tax_amount_requiere_politica": True,
        },
        valid_from=date(2026, 1, 1),
        valid_to=None,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    ahora = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    esc = siembra.sembrar_desde_insumos(
        None,  # type: ignore[arg-type]
        listing_id=7,
        product_id=11,
        oferta_raw={
            "oferta_observation_id": 100,
            "platform": "amazon_mx",
            "seller_sku": "SKU-1",
            "asin": "B000000001",
            "canal": "fbm",
            "price_amount": Decimal("116.0000"),
            "price_currency": "MXN",
            "context_fingerprint": "fp-1",
        },
        fee_raw={
            "platform": "amazon_mx",
            "total_fees": Decimal("12.0000"),
            "estado": "success",
            # Sin ReferralFee -> partir_fees devuelve fee_incompatible.
            "fee_details": [{"fee_type": "FBAFees", "final_fee": "12.0000"}],
        },
        fee_observation_id=200,
        universo="amazon_mx/fbm",
        uni=universo_de("amazon_mx", "fbm"),
        politica=pol,
        motivos=[],
        valoracion_date=date(2026, 6, 1),
        observed_at=ahora,
    )
    assert esc.motivos[:3] == ("costo_ausente", "fee_incompatible", "envio_sin_historia")
    assert esc.canonical_input["motivos"][:3] == [
        "costo_ausente",
        "fee_incompatible",
        "envio_sin_historia",
    ]


def test_parsear_politica_s3_y_s4():
    pol_s4 = PoliticaCalculo(
        politica_version_id=10,
        label="mx-s4",
        universo="amazon_mx/fba",
        formula_version="S4",
        settings={
            "precio_incluye_iva": True,
            "iva_divisor": "1.16",
            "isr_tasa": "0.025",
            "retencion_iva_tasa": "0.08",
            "referral_fee_iva_multiplicador": "1.16",
            "logistica": "0",
            "logistica_semantica": "incluida en FBA",
            "fee_tax_amount_requiere_politica": True,
        },
        valid_from=date(2026, 1, 1),
        valid_to=None,
        created_at=datetime(2026, 1, 1),
    )
    reglas, motivo = parsear_politica(pol_s4, date(2026, 6, 1), universo_esperado="amazon_mx/fba")
    assert motivo is None
    assert reglas is not None
    assert reglas.es_s4 is True
    assert reglas.iva_divisor == Decimal("1.16")
    assert reglas.isr_tasa == Decimal("0.025")
    assert reglas.ret_iva_tasa == Decimal("0.08")
    assert reglas.mult_ref == Decimal("1.16")
    assert reglas.logistica_base == Decimal("0")

    # Multiplicador < 1 es invalido
    pol_mala = PoliticaCalculo(
        **{
            **pol_s4.__dict__,
            "settings": {**pol_s4.settings, "referral_fee_iva_multiplicador": "0.90"},
        }
    )
    reglas_mala, motivo_mala = parsear_politica(pol_mala, date(2026, 6, 1))
    assert reglas_mala is None
    assert motivo_mala == "politica_invalida"


def test_detalles_fee_canonico_roundtrip_y_estricto():
    arbol = DetalleFee(
        fee_type="ReferralFee",
        final_fee=Decimal("15.0000"),
        tax_amount=None,
        included_fee_details=(DetalleFee(fee_type="SubFee", final_fee=Decimal("2.5000")),),
    )
    serializado = [detalle_fee_a_json(arbol)]
    assert detalles_fee_desde_json(serializado, estricto=True) == (arbol,)
    assert detalles_fee_desde_json(serializado, estricto=False) == (arbol,)
    assert detalles_fee_desde_json(None) == ()

    # Modo estricto rechaza negativos o >4 decimales
    with pytest.raises(ValueError, match="negativo"):
        detalle_fee_desde_json({"fee_type": "ReferralFee", "final_fee": "-1"}, estricto=True)
    with pytest.raises(ValueError, match="4 decimales"):
        detalle_fee_desde_json({"fee_type": "ReferralFee", "final_fee": "1.12345"}, estricto=True)
    with pytest.raises(ValueError, match="no es lista"):
        detalles_fee_desde_json({"fee_type": "ReferralFee"})
