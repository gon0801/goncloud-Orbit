"""Sonda G-4: un movimiento legitimo de precio dispara `insumo_sistemico` al dia siguiente.

`corrida._medir_compuerta` vigila `retencion` (r_valor = ISR en dinero = tasa x
ingreso) y `fee_variable` (fee que escala con el precio). Los dos son funcion
del PRECIO: si ayer se aplico +10 % a 60 de 158 FBA (dentro del fusible: 60 <
79), hoy esas 60 muestran un "salto" de 10 % > 5 % en los dos insumos.
Config de prod: insumo_salto_pct 0.05, insumo_salto_frac 0.30, minimo 10.
"""

from decimal import Decimal as D

from app.precio.compuerta import armar_deriva, evaluar
from app.precio.config import leer_config_motor

motor = leer_config_motor({
    "precio_modo_global": "live", "precio_modo_universo": {"amazon_mx/fba": "live"},
    "precio_goal_min_pct": "0.10", "precio_goal_max_pct": "0.60",
    "precio_freno_dias_error": 3, "precio_catalogo_max_dias_sin_reportar": 3,
    "precio_aviso_dias_sin_evaluar": 3, "precio_fusible_frac": "0.50",
    "precio_fusible_min_movimientos": 10, "precio_insumo_salto_pct": "0.05",
    "precio_insumo_salto_frac": "0.30", "precio_corte_errores_consecutivos": 5,
    "precio_senal_trafico_min": 100, "precio_cohorte_caida_pct": "0.30",
    "precio_envio_ventana_dias": 90,
})
N, MOVIDAS = 158, 60
P0, P1 = D("500.00"), D("550.00")  # +10 % aplicado ayer (escalon de prod)
IVA, ISR, REF = D("1.16"), D("0.0125"), D("0.15")


def r_valor(p):  # ISR en dinero, como lo guarda precio_decision.r_valor
    return (ISR * p / IVA).quantize(D("0.0001"))


def fee_var(p):  # referral, como estimacion_escenario.fee_variable
    return (REF * p).quantize(D("0.0001"))


pares_ret, pares_fv, pares_costo = [], [], []
for i in range(N):
    antes, ahora = (P0, P1) if i < MOVIDAS else (P0, P0)
    pares_ret.append((r_valor(antes), r_valor(ahora), False))
    pares_fv.append((fee_var(antes), fee_var(ahora), False))
    pares_costo.append((D("150"), D("150"), False))  # el costo NO cambio

derivas = [
    armar_deriva("fba", "costo", pares_costo, pct=motor.insumo_salto_pct),
    armar_deriva("fba", "retencion", pares_ret, pct=motor.insumo_salto_pct),
    armar_deriva("fba", "fee_variable", pares_fv, pct=motor.insumo_salto_pct),
]
for d in derivas:
    print(d)
# Hoy: 15 intenciones nuevas de OTROS productos (muy por debajo del fusible)
from app.precio.compuerta import Intencion
intenciones = [Intencion(1000 + i, 1000 + i, "fba", nueva=True, liberada=False) for i in range(15)]
print("veredicto del dia siguiente:", evaluar(intenciones, medidas=N, derivas=derivas, config=motor))
