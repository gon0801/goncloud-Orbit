"""Revision ESTIMACION: el plan de goals de precio nunca calcula el precio de
referencia (P*) de una publicacion de Mercado Libre.

Falla: la estimacion de MeLi guarda sus comisiones como `SaleFee` /
`SaleFeeFixed` / `ListingFee` (app/meli/estimacion.py:421-438), pero
`siembra._referencia` las parte con `derivar_ref_fijo`, que solo reconoce el
`ReferralFee` de Amazon (app/precio/objetivo.py:83). Resultado: toda fila MeLi
sale `referencia_sin_derivar`, sin P*, y el candado "el precio salta mas de
25 %" (`salto_mayor_25`) nunca puede dispararse en MeLi. La misma regla de
partir fees vive en tres lugares (partir_fees, partir_fees_meli,
derivar_ref_fijo) y este llamador eligio la de Amazon.
"""

from __future__ import annotations

from decimal import Decimal

from app.precio.siembra import LecturaHoy, _referencia


def _lectura_meli() -> LecturaHoy:
    # Forma real de produccion (estimacion_fee_observation, meli, 2026-11-12):
    # 989.00 MXN, SaleFee 192.86, fijo 0, publicacion 0.
    return LecturaHoy(
        m_pct=Decimal("30.0000"),
        p_actual=Decimal("989.0000"),
        moneda="MXN",
        componentes=[
            {"nombre": "precio_bruto", "importe_normalizado": "989.0000"},
            {"nombre": "ingreso_normalizado", "importe_normalizado": "852.5862"},
            {"nombre": "costo_normalizado", "importe_normalizado": "200.0000"},
            {"nombre": "logistica", "importe_normalizado": "120.0000"},
            {"nombre": "isr", "importe_normalizado": "21.3147"},
        ],
        fee_total=Decimal("192.8600"),
        fee_quoted=Decimal("989.0000"),
        fee_detalles=[
            {"fee_type": "SaleFee", "final_fee": "192.8600", "currency": "MXN"},
            {"fee_type": "SaleFeeFixed", "final_fee": "0.0000", "currency": "MXN"},
            {"fee_type": "ListingFee", "final_fee": "0.0000", "currency": "MXN"},
        ],
        fee_estado="success",
    )


def test_plan_de_goals_meli_sin_precio_de_referencia():
    estrella, p_actual, motivo = _referencia(_lectura_meli(), Decimal("0.2000"))
    assert p_actual is not None
    # Lo que deberia pasar: con escenario MeLi completo, P* se calcula igual
    # que en Amazon (variable = SaleFee).
    assert motivo is None, f"MeLi completo sale sin P*: motivo={motivo!r}"
    assert estrella is not None
