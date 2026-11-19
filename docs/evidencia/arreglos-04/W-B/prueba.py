"""Prueba real del bloque W-B (ARREGLOS 04), para en_prod.sh.

Funciones puras e inspeccion de modulos: sin base, sin red, sin escritura.
Comprueba que el plan v1 fue retirado de app.fabrica_plan y tools.fabrica_campanas.
"""

from app import fabrica_plan
from tools import fabrica_campanas

for ausente in ("PlanGrupo", "plan_desde_json", "version_creacion_desde_settings"):
    assert not hasattr(fabrica_plan, ausente), f"app.fabrica_plan conserva {ausente}"

assert not hasattr(fabrica_campanas, "_SQL_PRODUCTOS"), (
    "tools.fabrica_campanas conserva _SQL_PRODUCTOS"
)

print("W-B OK")
