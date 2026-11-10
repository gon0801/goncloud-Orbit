"""Sonda C (pregunta 7, datos coherentes): cortes D-40..D-10 con 0 pedidos, 150 clics y 60 USD;
los pedidos del tramo reciente caen todos en D-50..D-41. Venta reciente D-9..D-1 protege (M1)."""
import datetime as dt
import sys
from dataclasses import replace
sys.path.insert(0, "/private/tmp/claude-501/-Users-dn-dev-goncloud-Orbit/ae5b1bef-ffcc-4133-9d5d-a3f633c9263b/scratchpad/orbit07-wt")
import runpy
m = runpy.run_path("C_m1_m2_m6.py", run_name="sonda")
from app.optimizer.caso import EvidenciaNivel, Tramo, Trayectoria
from app.optimizer.politica import decide
D = m["D"]
propia = EvidenciaNivel(
    reciente=Tramo(clics=180, pedidos=4, venta=D("600"), gasto=D("72"), impresiones=6000),   # D-50..D-10 (incluye los 150 clics/60 USD/0 pedidos de cortes)
    antiguo=Tramo(clics=60, pedidos=3, venta=D("450"), gasto=D("24"), impresiones=2000),     # D-90..D-51
)
mm = m["m"]
base = mm["caso"](None, cambio=mm["SUBIDA"], efecto=mm["EFECTO_SUBIDA"], propia=propia)
c = replace(base, trayectoria=Trayectoria(cambios=(), efecto=None),
            pausa=replace(base.pausa, cortes=m["cortes"], proteccion=m["prot"]),
            precio=base.precio.__class__(gasto=D("72"), clics=150))
print("\n== M1 con datos coherentes, hoja sin cambios en 90 dias ==")
print("  con M1 (venta reciente protege):", decide(c))
print("  sin M1 (semantica previa)      :", decide(replace(c, pausa=replace(c.pausa, proteccion=None))))
grupo = EvidenciaNivel(
    reciente=Tramo(clics=400, pedidos=10, venta=D("1500"), gasto=D("150"), impresiones=12000),
    antiguo=Tramo(clics=150, pedidos=6, venta=D("900"), gasto=D("60"), impresiones=5000),
)
c2 = replace(c, grupo=grupo, cuenta=grupo)
print("  con grupo/cuenta coherentes:")
print("    con M1 (venta reciente protege):", decide(c2))
print("    sin M1 (semantica previa)      :", decide(replace(c2, pausa=replace(c2.pausa, proteccion=None))))
