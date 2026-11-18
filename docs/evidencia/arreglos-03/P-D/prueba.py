from app.precio.compuerta import (
    TOPE_CAMBIOS_DIA,
    Abierta,
    Anterior,
    Intencion,
    Retenida,
    cambios_en_vivo,
    es_nueva,
    evaluar,
)
from app.precio.config import leer_config_motor

motor = leer_config_motor(
    {
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
        "precio_senal_trafico_min": 200,
        "precio_cohorte_caida_pct": "0.25",
        "precio_envio_ventana_dias": 180,
    }
)

assert TOPE_CAMBIOS_DIA == 50, "el tope del dueno son 50 cambios por dia"

# 91 cambios en vivo en el dia (62 fba + 29 fbm, cada universo bajo el
# fusible: 62 > 0.50*125 es falso) se retienen en los dos universos.
fba = [Intencion(i, i, "fba", nueva=True, liberada=False, en_vivo=True) for i in range(62)]
fbm = [
    Intencion(1000 + i, 1000 + i, "fbm", nueva=True, liberada=False, en_vivo=True)
    for i in range(29)
]
assert cambios_en_vivo({"fba": fba, "fbm": fbm}) == 91
for canal, intenciones, medidas in (("fba", fba, 125), ("fbm", fbm, 59)):
    veredicto = evaluar(intenciones, medidas=medidas, derivas=(), config=motor, cambios_dia=91)
    assert isinstance(veredicto, Retenida) and veredicto.causa == "movimiento_masivo", (
        f"{canal}: {veredicto}"
    )
# Con 50 cambios en el dia el tope no retiene.
assert isinstance(evaluar(fba, medidas=125, derivas=(), config=motor, cambios_dia=50), Abierta)

# De practica a en vivo es intencion nueva; el segundo escalon en vivo no.
assert es_nueva(Anterior("subir", None, 7, "shadow"), goal_id=7, modo="live") is True
assert es_nueva(Anterior("subir", None, 7, "live"), goal_id=7, modo="live") is False

print("P-D desplegado")
