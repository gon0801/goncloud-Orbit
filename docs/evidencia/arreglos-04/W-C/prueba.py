"""Prueba pura del bloque W-C para en_prod.sh (sin base ni red)."""

import datetime as dt

from app import api_dashboard
from app.dashboard_resumen import hoy_resumen, ventana_resumen

assert not hasattr(api_dashboard, "_hoy_utc"), "api_dashboard aun define _hoy_utc"

instante = dt.datetime(2026, 11, 13, 3, 0, tzinfo=dt.UTC)
plataformas = ("amazon_mx",)
esperado = (dt.date(2026, 10, 13), dt.date(2026, 11, 11))
obtenido = api_dashboard._ventana_campanas(plataformas, instante)
assert obtenido == esperado, f"ventana inesperada: {obtenido} != {esperado}"

ventana = ventana_resumen(
    periodo=30,
    desde=None,
    hasta=None,
    hoy=hoy_resumen(plataformas, instante),
)
assert obtenido == (ventana.desde, ventana.hasta), (
    f"desfase contra Resumen: {obtenido} != {(ventana.desde, ventana.hasta)}"
)

print("W-C OK")
