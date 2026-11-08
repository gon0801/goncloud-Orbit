import datetime as dt
from app.lecturas_caso import _completo, DIAS_INMADUROS_DESDE, DIAS_INMADUROS_HASTA
d = dt.date(2026, 11, 6)
inm_desde = d - dt.timedelta(days=DIAS_INMADUROS_DESDE)
ayer = d - dt.timedelta(days=DIAS_INMADUROS_HASTA)
todos = frozenset(inm_desde + dt.timedelta(days=i) for i in range(9))
sin_ayer = todos - {ayer}
print("rango inmaduros:", inm_desde, "..", ayer)
print("ingesta completa  -> inm_ok =", _completo(inm_desde, ayer, todos))
print("falta solo D-1    -> inm_ok =", _completo(inm_desde, ayer, sin_ayer), "(lecturas_caso.py:501-502 => pedidos_inmaduros=None para TODAS las hojas)")
