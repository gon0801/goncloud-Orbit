import datetime as dt
from decimal import Decimal
from test_optimizer_caso import fabrica_caso
from app.optimizer.caso import CasoHoja
from app.optimizer import goals as g

print("SONDA D: desde_json rechaza una clave nueva (contrato de freeze)")
caso = fabrica_caso()
js = caso.como_json()
js["proteccion"] = None
try:
    CasoHoja.desde_json(js)
    print("  acepto clave extra (inesperado)")
except ValueError as e:
    print("  ValueError:", str(e)[:90])
js2 = caso.como_json(); del js2["pedidos_inmaduros"]
try:
    CasoHoja.desde_json(js2); print("  acepto clave faltante (inesperado)")
except ValueError as e:
    print("  ValueError:", str(e)[:90])

print("SONDA F: peldano margen_plataforma -> banda [10,45] y rampa de bajada 0.5/ciclo")
med = g.MedicionMargen(margen_neto_pct=Decimal("8"), cobertura=Decimal("0.99"), dias_con_venta=90,
    venta_cubierta=Decimal("100000"), ledger_fresco_at=dt.datetime(2026,11,6,tzinfo=dt.UTC), moneda="MXN",
    ventana_desde=dt.date(2026,7,24), ventana_hasta=dt.date(2026,10,22))
hoy = g.hoy_de_ventana(med.ventana_hasta)
for ultimo in (None, Decimal("20.72")):
    r = g.resuelve_target_margen(med, Decimal("0.5"), hoy, ultimo, setting=Decimal("20"))
    print(f"  margen 8 x 0.5 = derivado {r.derivado}; ultimo={ultimo} -> aplicado {r.aplicado} (motivo {r.motivo})")
print("  constantes:", "BANDA_MIN", g.MARGEN_BANDA_MIN, "BANDA_MAX", g.MARGEN_BANDA_MAX, "PASO_BAJADA", g.MARGEN_PASO_MAX_BAJADA)
