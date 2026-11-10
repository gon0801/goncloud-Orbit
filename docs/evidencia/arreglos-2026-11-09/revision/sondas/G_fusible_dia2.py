"""Sonda G-1: el fusible y el insumo sistemico se abren solos al dia siguiente.

Reproduce, con las funciones puras que usa `corrida._medir_compuerta`, lo que
pasa el dia 1 (91 de 158 quieren moverse por un costo cargado mal: retiene) y
el dia 2 (las mismas 91 vuelven a querer moverse; su decision anterior es la
`subir` RETENIDA de ayer, que nunca se aplico).

Config = la de produccion (migracion 0054 + D.0): fusible 0.50 / min 10,
insumo salto 0.05 / frac 0.30.
"""

from decimal import Decimal as D

from app.precio.compuerta import Anterior, Intencion, armar_deriva, es_nueva, evaluar
from app.precio.config import leer_config_motor

settings = {
    "precio_modo_global": "live",
    "precio_modo_universo": {"amazon_mx/fba": "live"},
    "precio_goal_min_pct": "0.10",
    "precio_goal_max_pct": "0.60",
    "precio_freno_dias_error": 3,
    "precio_catalogo_max_dias_sin_reportar": 3,
    "precio_aviso_dias_sin_evaluar": 3,
    "precio_fusible_frac": "0.50",
    "precio_fusible_min_movimientos": 10,
    "precio_insumo_salto_pct": "0.05",
    "precio_insumo_salto_frac": "0.30",
    "precio_corte_errores_consecutivos": 5,
    "precio_senal_trafico_min": 100,
    "precio_cohorte_caida_pct": "0.30",
    "precio_envio_ventana_dias": 90,
}
motor = leer_config_motor(settings)
GOAL_ID = 7  # mismo goal los dos dias (nadie lo toco)
N, MUEVEN = 158, 91

# --- Dia 1: costo cargado 20 % arriba en 91 productos; decision previa = mantener
intenciones_d1 = []
pares_costo_d1 = []
for i in range(N):
    anterior = Anterior(resultado="mantener", motivo="en_tolerancia", goal_id=GOAL_ID)
    if i < MUEVEN:
        intenciones_d1.append(
            Intencion(i, i, "fba", nueva=es_nueva(anterior, goal_id=GOAL_ID), liberada=False)
        )
        pares_costo_d1.append((D("100.00"), D("120.00"), False))  # ayer 100, hoy 120
    else:
        pares_costo_d1.append((D("100.00"), D("100.00"), False))
d1 = evaluar(
    intenciones_d1,
    medidas=N,
    derivas=[armar_deriva("fba", "costo", pares_costo_d1, pct=motor.insumo_salto_pct)],
    config=motor,
)
print("DIA 1:", d1)

# --- Dia 2: nadie solto la corrida. La decision anterior de las 91 es la
# `subir` de ayer (retenida, sin precio_cambio -> sin cooldown -> hoy vuelve
# a salir `subir`). El costo sigue mal: ayer 120, hoy 120.
intenciones_d2 = []
pares_costo_d2 = []
for i in range(N):
    if i < MUEVEN:
        anterior = Anterior(resultado="subir", motivo=None, goal_id=GOAL_ID)  # la retenida
        intenciones_d2.append(
            Intencion(i, i, "fba", nueva=es_nueva(anterior, goal_id=GOAL_ID), liberada=False)
        )
        pares_costo_d2.append((D("120.00"), D("120.00"), False))
    else:
        pares_costo_d2.append((D("100.00"), D("100.00"), False))
d2 = evaluar(
    intenciones_d2,
    medidas=N,
    derivas=[armar_deriva("fba", "costo", pares_costo_d2, pct=motor.insumo_salto_pct)],
    config=motor,
)
print("DIA 2:", d2)
print("es_nueva(Anterior('subir', None, 7), goal_id=7) =",
      es_nueva(Anterior("subir", None, GOAL_ID), goal_id=GOAL_ID))
