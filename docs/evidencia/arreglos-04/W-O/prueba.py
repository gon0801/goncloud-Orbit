"""Prueba real del bloque W-O, para en_prod.sh.

Funciones puras: sin base, sin red, sin escritura.
"""

from app import cycle as ciclo
from app.etiquetas import etiqueta
from app.optimizer import politica, proteccion

ESPERADOS = (
    (
        politica.MOTIVO_SUBIDA_EN_ESPERA,
        "Su pausa está en espera por una venta reciente: Orbit no sube la puja mientras tanto",
    ),
    (
        proteccion.MOTIVO_PROTEGIDA,
        "Iba a pausarse, pero tiene un pedido reciente: la pausa espera",
    ),
    (
        proteccion.MOTIVO_INDETERMINADA,
        "Iba a pausarse, pero falta el dato de sus pedidos recientes: la pausa espera",
    ),
    (
        ciclo.MOTIVO_PAUSA_PRODUCTO_PENDIENTE,
        "La pausa de su producto está en camino: Orbit no sube la puja de lo que va a pausar",
    ),
)

for motivo, esperado in ESPERADOS:
    obtenido = etiqueta(motivo, "motivo_salud")
    assert obtenido == esperado, f"{motivo}: {obtenido!r} != {esperado!r}"

print("W-O OK")
