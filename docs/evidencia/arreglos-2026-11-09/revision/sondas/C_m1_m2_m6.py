"""Sonda C (pregunta 7): hoja con pausa por umbral protegida por venta reciente (M1),
equilibrio de producto (M2) y episodio maduro (M6) a la vez."""
import datetime as dt
import sys
from dataclasses import replace
sys.path.insert(0, "/private/tmp/claude-501/-Users-dn-dev-goncloud-Orbit/ae5b1bef-ffcc-4133-9d5d-a3f633c9263b/scratchpad/orbit07-wt")
import runpy
m = runpy.run_path("C_m6_politica.py", run_name="sonda")
from app.optimizer.caso import InsumosPausa, InsumosProteccion, LecturaReciente, VERSION_PROTECCION_VENTA
from app.optimizer.windows import AgregadoMetricas
from app.optimizer import proteccion as sp
from app.optimizer.politica import decide
D = m["D"]
hoy = dt.date(2026, 11, 9)
desde, hasta = hoy - dt.timedelta(days=40), hoy - dt.timedelta(days=10)
cortes = AgregadoMetricas(desde, hasta, tuple(desde + dt.timedelta(days=i) for i in range(31)), "USD",
                          D("60"), D("0"), D("0"), 3000, 150, 0, None)
prot = InsumosProteccion(version=VERSION_PROTECCION_VENTA, evaluado_el=hoy,
                         reciente=LecturaReciente(pedidos=2, ancla=hoy - dt.timedelta(days=3), cubierta=True, observado_al=None),
                         ventana_desde=desde, ventana_hasta=hasta, episodio=None)
print("\n== M1 + M2 + M6 ==")
print("  subpolitica M1:", sp.evalua(prot))
for nombre, episodio in (("sin episodio", None), ("episodio frena", m["ruido"]), ("episodio venta igual", m["igual"])):
    base = m["caso"](episodio, cambio=m["SUBIDA"], efecto=m["EFECTO_SUBIDA"])
    for eq in (None, D("30"), D("12")):
        c = replace(base,
                    pausa=InsumosPausa(cortes=cortes, umbral_clics=100, gasto_minimo=D("40"), expected_clicks=None,
                                       politica_economica=None, proteccion=prot),
                    economia=replace(base.economia, plataforma=replace(base.economia.plataforma, equilibrio_acos_pct=eq)))
        sin_m1 = replace(c, pausa=replace(c.pausa, proteccion=None))
        print(f"  {nombre:20s} equilibrio={eq!s:5s} con M1 -> {decide(c)} | sin M1 -> {decide(sin_m1)}")
