"""Sonda C (H2): subida tras una semana previa SIN ventas. La politica dice
frenar si 'no crecio la venta' o si 'con margen la contribucion no mejoro';
el codigo devuelve sin_evidencia por pre.venta <= 0 y la subida pasa."""
import sys
sys.path.insert(0, "/private/tmp/claude-501/-Users-dn-dev-goncloud-Orbit/ae5b1bef-ffcc-4133-9d5d-a3f633c9263b/scratchpad/orbit07-wt")
import runpy
m = runpy.run_path("C_m6_politica.py", run_name="sonda")
from app.optimizer.economia_cambio import juicio_repeticion
from app.optimizer.politica import decide
D, epi, ven, PRE, POST = m["D"], m["epi"], m["ven"], m["PRE"], m["POST"]
print("\n== H2 ==")
escenarios = {
    "pre 0 ventas/10 gasto, post 0 ventas/20 gasto (la venta no crecio)": (ven(PRE, "10", "0", 0, 8), ven(POST, "20", "0", 0, 15), None),
    "pre 0/10, post 10 venta/50 gasto, margen 8 (contribucion -10 -> -49.2)": (ven(PRE, "10", "0", 0, 8), ven(POST, "50", "10", 1, 40), "8"),
    "control: pre 1 centavo de venta, mismo post": (ven(PRE, "10", "0.01", 1, 8), ven(POST, "20", "0", 0, 15), None),
}
for nombre, (pre, post, margen) in escenarios.items():
    e = epi(pre, post, margen)
    j = juicio_repeticion(e, target_acos_pct=D("25"), factor=D("0.15"))
    v = decide(m["caso"](e, cambio=m["SUBIDA"], efecto=m["EFECTO_SUBIDA"]))
    print(f"  {nombre}\n     contribucion pre={e.contribucion_pre} post={e.contribucion_post} juicio={j.veredicto} decide={v}")
