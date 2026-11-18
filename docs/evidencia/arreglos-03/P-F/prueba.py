from decimal import Decimal

from app.precio.siembra import LecturaHoy, _referencia

# Forma real de produccion (estimacion_fee_observation, meli, 2026-11-12):
# 989.00 MXN, SaleFee 192.86, fijo 0, publicacion 0; la comision llega ya
# partida del escenario.
lectura = LecturaHoy(
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
    fee_variable=Decimal("192.8600"),
    fee_fijo=Decimal("0.0000"),
    fee_estado="success",
)
estrella, p_actual, motivo = _referencia(lectura, Decimal("0.2000"))
assert motivo is None, f"MeLi completo sale sin P*: motivo={motivo!r}"
assert estrella is not None
print("P-F desplegado")
