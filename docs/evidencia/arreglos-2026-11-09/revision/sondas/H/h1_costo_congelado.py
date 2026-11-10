"""Sonda H1: un cambio de costo dos veces el mismo dia, con la ingesta de
Orbit (08:15 UTC) en medio, congela el SKU para siempre en el valor
intermedio y deja rows_skipped > 0 en todas las corridas siguientes.

Pura: usa colapsar() y _plan_sku() de app.costs, sin base.
"""

import datetime as dt

from app.costs import FilaCosto, _plan_sku, colapsar, INCLUDES_TAX

SKU = "NH-PRUEBA"
D = "2026-11-03"

# Corrida 1 (08:15 UTC del dia D): contabilidad ya cerro A a las 06:00 y abrio B.
origen_0815 = [
    FilaCosto(SKU, 100.0, "MXN", "2026-10-24 10:00:00", f"{D} 06:00:00", None),
    FilaCosto(SKU, 110.0, "MXN", f"{D} 06:00:00", None, None),
]
vig1, skips1, _ = colapsar(origen_0815)
cierres1, ins1, motivo1 = _plan_sku(vig1[SKU], {})
print("corrida 1 (D 08:15): inserciones=", ins1, "motivo=", motivo1, "skips=", dict(skips1))

# Lo que queda publicado en sku_cost tras la corrida 1.
publicadas = {v.desde: (v.costo, v.hasta, v.moneda, INCLUDES_TAX) for v in ins1}

# Corrida 2 (D+1 08:15): a las 12:00 del dia D el costo volvio a cambiar a 120.
origen_d1 = [
    FilaCosto(SKU, 100.0, "MXN", "2026-10-24 10:00:00", f"{D} 06:00:00", None),
    FilaCosto(SKU, 110.0, "MXN", f"{D} 06:00:00", f"{D} 12:00:00", None),
    FilaCosto(SKU, 120.0, "MXN", f"{D} 12:00:00", None, None),
]
vig2, skips2, stats2 = colapsar(origen_d1)
print("serie colapsada D+1:", vig2[SKU])
cierres2, ins2, motivo2 = _plan_sku(vig2[SKU], publicadas)
print("corrida 2 (D+1): cierres=", cierres2, "inserciones=", ins2, "motivo=", motivo2)

# Corrida 3 (D+10): el costo sube otra vez a 130; el SKU sigue rechazado.
origen_d10 = origen_d1[:-1] + [
    FilaCosto(SKU, 120.0, "MXN", f"{D} 12:00:00", "2026-11-12 09:00:00", None),
    FilaCosto(SKU, 130.0, "MXN", "2026-11-12 09:00:00", None, None),
]
vig3, _, _ = colapsar(origen_d10)
_, _, motivo3 = _plan_sku(vig3[SKU], publicadas)
print("corrida 3 (D+10): motivo=", motivo3)
abierta = [(d, c, h) for d, (c, h, _, _) in publicadas.items() if h is None]
print("vigencia ABIERTA que sigue usando Orbit:", abierta)

# Efecto en el motor de precios: la corrida de costos queda ok=True pero con
# rows_skipped >= 1 todos los dias; la estimacion exige rows_skipped = 0.
from datetime import UTC, date, datetime
from decimal import Decimal

from app.estimacion_insumos import _corrida_validacion_costos_aceptable, evaluar_costo_candidatos

hoy = date(2026, 11, 4)
ahora = datetime(2026, 11, 4, 12, 45, tzinfo=UTC)
fin_corrida = datetime(2026, 11, 4, 8, 15, 3, tzinfo=UTC)
print(
    "corrida de costos ok con 1 skip aceptable para validar:",
    _corrida_validacion_costos_aceptable(
        "accounting_sku_costs", True, fin_corrida, 1, fecha_escenario=hoy, now_utc=ahora
    ),
)
# Cualquier OTRO producto (costo sano y vigente) queda sin escenario:
fila_sana = (77, Decimal("95.0000"), "MXN", False, date(2026, 2, 20), None, 1)
print(
    "producto sano sin corrida validadora ->",
    evaluar_costo_candidatos(
        [fila_sana],
        producto_tiene_alguna=True,
        fecha_escenario=hoy,
        now_utc=ahora,
        corrida_validacion=None,
    ).motivo,
)
