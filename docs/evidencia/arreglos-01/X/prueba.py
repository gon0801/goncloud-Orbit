"""Prueba real del bloque X (AE-U5), para en_prod.sh.

Funciones puras: sin base, sin red, sin escritura. Comprueba los topes
humanos en el codigo desplegado y sus bordes exactos.
"""

from decimal import Decimal

from app.optimizer import goals as g

# El tope de presupuesto replica el doble del maximo medido el 2026-11-13:
# 5353.78 MXN y 26.22 USD -> 10708 y 53.
RECHAZOS = (
    ("presupuesto_diario", Decimal("20000"), "MXN"),
    ("presupuesto_diario", Decimal("10708.01"), "MXN"),
    ("presupuesto_diario", Decimal("100"), "USD"),
    ("presupuesto_diario", Decimal("53.01"), "USD"),
    ("bid", Decimal("11"), "USD"),
    ("bid", Decimal("180.01"), "MXN"),
)
ACEPTADOS = (
    ("presupuesto_diario", Decimal("5353.78"), "MXN"),
    ("presupuesto_diario", Decimal("10708"), "MXN"),
    ("presupuesto_diario", Decimal("26.22"), "USD"),
    ("presupuesto_diario", Decimal("53"), "USD"),
    ("bid", Decimal("10"), "USD"),
    ("bid", Decimal("180"), "MXN"),
)

for clase, monto, moneda in RECHAZOS:
    try:
        g.exige_bajo_techo(clase, monto, moneda)
    except ValueError:
        pass
    else:
        raise AssertionError(f"{clase} {monto} {moneda} deberia rechazarse")

for clase, monto, moneda in ACEPTADOS:
    g.exige_bajo_techo(clase, monto, moneda)

for dentro in (Decimal("10"), Decimal("45")):
    g.exige_banda_meta_manual(dentro)
for fuera in (Decimal("5"), Decimal("60")):
    try:
        g.exige_banda_meta_manual(fuera)
    except ValueError:
        pass
    else:
        raise AssertionError(f"la meta manual {fuera} % deberia rechazarse")

print("X OK")
