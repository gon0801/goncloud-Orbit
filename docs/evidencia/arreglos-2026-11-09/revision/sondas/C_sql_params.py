"""Sonda C: placeholders de _SQL_EFECTO(_ECONOMIA) contra los parametros que arma _lee_efecto,
y que las 13 primeras columnas del SQL economico sean las del historico."""
import datetime as dt
import sys
from decimal import Decimal as D
sys.path.insert(0, "/private/tmp/claude-501/-Users-dn-dev-goncloud-Orbit/ae5b1bef-ffcc-4133-9d5d-a3f633c9263b/scratchpad/orbit07-wt")
import runpy
m = runpy.run_path("C_m5_episodio.py", run_name="sonda")
from app import lecturas_caso as lc

def cols(sql):
    cuerpo = sql.split("FROM {fuente}")[0]
    return [c.strip() for c in cuerpo.replace("SELECT", "", 1).split(",\n")]

h, e = cols(lc._SQL_EFECTO), cols(lc._SQL_EFECTO_ECONOMIA)
print("columnas historico:", len(h), "economico:", len(e), "13 primeras iguales:", h == e[:13])
for econ in (False, True):
    lect = m["lecturas"](m["X"] + dt.timedelta(days=40), (m["SUBIDA"],))
    object.__setattr__(lect, "_con_economia", econ)
    conn = m["ConexionFalsa"]()
    if not econ:
        orig = conn.execute
        conn.execute = lambda sql, params=(): (setattr(conn, "params", params), m["_Fila"](orig(sql, params)._fila[:13]))[1]
    sql = lc._SQL_EFECTO_ECONOMIA if econ else lc._SQL_EFECTO
    leido = lc._lee_efecto(conn, lect, 1, (m["SUBIDA"],), None)
    print(f"con_economia={econ}: placeholders={sql.count('%s')} parametros={len(conn.params)} efecto={leido.efecto}")
