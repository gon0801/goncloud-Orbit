"""Banco de pruebas de las reglas puras (REPRICING 01 A.2).

Parte de los numeros del acta 0.3: P=116, I=100, C=40, F=15, L=0, R=2.50
-> contribucion 42.50, con POLITICA_FBA_MX_SELLADA (iva_divisor 1.16,
isr_tasa 0.025, precio_incluye_iva True). Todo en Decimal.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.estimacion_venta import DetalleFee
from app.precio.tipos import (
    MOTIVOS_FRENADO,
    MOTIVOS_GOAL_INALCANZABLE,
    MOTIVOS_MANTENER,
    MOTIVOS_NO_EVALUADO,
    RESULTADOS,
    SUBMOTIVOS_SIN_DATO,
    Importe,
    SenalVentas,
    exigir_decimal,
    motivo_permitido,
)

MXN = "MXN"


def imp(valor: str, moneda: str = MXN) -> Importe:
    return Importe(Decimal(valor), moneda)


# ---------------------------------------------------------------- tipos


def test_tipos_seis_resultados_exactos():
    assert RESULTADOS == (
        "subir",
        "bajar",
        "mantener",
        "no_evaluado",
        "goal_inalcanzable",
        "frenado",
    )


def test_tipos_importe_rechaza_float():
    with pytest.raises(TypeError):
        Importe(116.01, MXN)  # type: ignore[arg-type]


def test_tipos_importe_rechaza_int_y_moneda_vacia():
    with pytest.raises(TypeError):
        Importe(116, MXN)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        Importe(Decimal("116"), "  ")


def test_tipos_exigir_decimal_rechaza_float_y_no_finito():
    with pytest.raises(TypeError):
        exigir_decimal(0.30, campo="goal")
    with pytest.raises(ValueError):
        exigir_decimal(Decimal("NaN"), campo="goal")


def test_tipos_vocabulario_mantener_cerrado():
    assert motivo_permitido("mantener", "en_tolerancia")
    assert motivo_permitido("mantener", "ventas_sin_dato:racha_incompleta")
    assert not motivo_permitido("mantener", "ventas_sin_dato:inventado")
    assert not motivo_permitido("mantener", "en_tolerancia:extra")
    assert not motivo_permitido("mantener", None)


def test_tipos_vocabulario_no_evaluado_cerrado():
    assert motivo_permitido("no_evaluado", "fee_error:fee_http_429")
    assert not motivo_permitido("no_evaluado", "fee_error")
    assert not motivo_permitido("no_evaluado", "inventado")
    for motivo in (
        "oferta_desactualizada",
        "fx_ausente",
        "fee_ausente",
        "impuesto_fee_pendiente",
        "precio_divergente",
        "moneda_divergente",
    ):
        assert motivo_permitido("no_evaluado", motivo), motivo
    assert MOTIVOS_NO_EVALUADO and MOTIVOS_MANTENER


def test_tipos_subir_bajar_sin_motivo():
    assert motivo_permitido("subir", None)
    assert not motivo_permitido("subir", "en_tolerancia")
    assert motivo_permitido("bajar", None)
    for motivo in MOTIVOS_GOAL_INALCANZABLE:
        assert motivo_permitido("goal_inalcanzable", motivo)
    for motivo in MOTIVOS_FRENADO:
        assert motivo_permitido("frenado", motivo)
    assert len(SUBMOTIVOS_SIN_DATO) == 8


def test_tipos_senal_sin_dato_exige_submotivo_conocido():
    with pytest.raises(ValueError):
        SenalVentas("sin_dato", "inventado", 0, 0, 0, 0, 0)
    with pytest.raises(ValueError):
        SenalVentas("perdiendo", "racha_incompleta", 1, 1, 1, 1, 3)


def test_tipos_detalle_fee_reexportado_sin_io():
    det = DetalleFee("ReferralFee", Decimal("15"), None, ())
    assert det.fee_type == "ReferralFee"


HOY = date(2026, 9, 17)
AHORA = datetime(2026, 9, 17, 13, 10, tzinfo=UTC)
