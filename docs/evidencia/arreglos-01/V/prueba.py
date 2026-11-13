"""V (AD-U8): el codigo desplegado no salta a la meta de plataforma cuando una
keyword pierde la meta de su producto (B-H1). Funcion pura: sin base ni red.
Se corre con `bash docs/evidencia/arreglos-01/en_prod.sh .../V/prueba.py`.
"""

import datetime as dt
from decimal import Decimal

from app.optimizer import goals as g
from app.optimizer import productos as p

HOY = dt.date(2026, 11, 13)
AHORA = dt.datetime(2026, 11, 13, 8, 0, tzinfo=dt.UTC)


def familia_deriva_20() -> g.MedicionMargen:
    return g.MedicionMargen(
        margen_neto_pct=Decimal("20"),
        cobertura=Decimal("1"),
        dias_con_venta=40,
        venta_cubierta=Decimal("4000"),
        ledger_fresco_at=AHORA,
        moneda="USD",
        ventana_desde=HOY - dt.timedelta(days=40),
        ventana_hasta=HOY,
    )


# Caso A: el previo de la hoja es margen_producto (4 %). La version nueva no
# interpola: la familia aplica su derivado (20 x 0.5 = 10) en ese ciclo.
ancla = p.ancla_familia(
    (Decimal("4"), "margen_producto"),
    Decimal("25.5"),
    version=p.POLITICA_ECONOMIA,
)
assert ancla == (None, False), ancla
resolucion, ganadora = g.resuelve_target_margen_familia(
    familia_deriva_20(),
    None,
    Decimal("0.5"),
    HOY,
    ancla[0],
    Decimal("25.5"),
    tiene_previo=ancla[1],
)
assert ganadora == "etiqueta", ganadora
assert resolucion.aplicado == Decimal("10"), resolucion
print(f"familia tras margen_producto: ancla={ancla} aplicado={resolucion.aplicado}")

# Caso B: ledger rancio con previo de la hoja. El motivo transitorio converge
# 0.5 al destino en vez de soltar el peldano de producto.
converge = p.meta_de_hoja(
    p.SinDato(("ledger_rancio",)),
    ultimo=Decimal("4"),
    destino=Decimal("25.5"),
    tiene_previo=True,
    version=p.POLITICA_ECONOMIA,
)
assert converge == g.ResolucionMargen(Decimal("4.5"), None, "ledger_rancio", True), converge
print(f"ledger_rancio con previo: {converge}")

sin_previo = p.meta_de_hoja(
    p.SinDato(("ledger_rancio",)),
    ultimo=Decimal("4"),
    destino=Decimal("25.5"),
    tiene_previo=False,
    version=p.POLITICA_ECONOMIA,
)
assert sin_previo == g.ResolucionMargen(None, None, "ledger_rancio", False), sin_previo
print(f"ledger_rancio sin previo: {sin_previo}")

# AUTO-03 conserva su ancla historica con cualquier previo de la hoja.
legacy = p.ancla_familia(
    (Decimal("4"), "margen_producto"),
    Decimal("25.5"),
    version=p.ECONOMIA_AUTO03,
)
assert legacy == (Decimal("4"), False), legacy
print(f"auto03 conserva el ancla: {legacy}")
print("V OK")
