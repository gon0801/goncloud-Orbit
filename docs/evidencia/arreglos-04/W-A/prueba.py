"""Prueba pura del bloque W-A para en_prod.sh (sin base ni red)."""

from app import api_dashboard, ui_metricas

rutas_series = sorted(
    getattr(ruta, "path", "")
    for ruta in api_dashboard.router.routes
    if "series" in getattr(ruta, "path", "")
)
assert not rutas_series, f"rutas /series aun registradas: {rutas_series}"
assert not hasattr(ui_metricas, "kpis_serie"), "ui_metricas aun define kpis_serie"
assert not hasattr(ui_metricas, "clase_cambio"), "ui_metricas aun define clase_cambio"

print("W-A OK")
