"""O (AD-U3): el codigo desplegado espera la pausa cuando hay un pedido
reciente conocido aunque falte el dato completo del rango (A-H1). Funcion
pura: sin base ni red.
Se corre con `bash docs/evidencia/arreglos-01/en_prod.sh .../O/prueba.py`.
"""

import datetime as dt

from app.optimizer import proteccion as p
from app.optimizer.caso import (
    VERSION_PROTECCION_VENTA,
    InsumosProteccion,
    LecturaReciente,
)

HOY = dt.date(2026, 11, 9)
ANCLA = HOY - dt.timedelta(days=3)

for cubierta in (True, False):
    insumos = InsumosProteccion(
        version=VERSION_PROTECCION_VENTA,
        evaluado_el=HOY,
        reciente=LecturaReciente(
            pedidos=None,
            ancla=ANCLA,
            cubierta=cubierta,
            observado_al=dt.datetime(2026, 11, 9, 8, 0, tzinfo=dt.UTC),
        ),
        ventana_desde=HOY - dt.timedelta(days=20),
        ventana_hasta=HOY - dt.timedelta(days=10),
        episodio=None,
    )
    resultado = p.evalua(insumos)
    assert resultado.accion == "esperar", (cubierta, resultado)
    assert resultado.motivo == p.MOTIVO_PROTEGIDA, (cubierta, resultado)
    assert resultado.ancla == ANCLA, (cubierta, resultado)
    assert resultado.abre_episodio is True, (cubierta, resultado)
    print(f"cubierta={cubierta}: accion={resultado.accion} ancla={resultado.ancla}")
print("O OK")
