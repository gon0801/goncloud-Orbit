"""S (H-H2): el codigo desplegado espera las metricas hasta que la guarda da
el dia por perdido (16:00 UTC) y despues avisa. Funcion pura: sin base ni red.
Se corre con `bash docs/evidencia/arreglos-01/en_prod.sh .../S/prueba.py`.
"""

import contextlib
import datetime as dt
import io
import sys
from pathlib import Path

# Al correr el archivo, sys.path[0] es esta carpeta y no la raiz del repo;
# en_prod.sh lo pasa por stdin (__file__ = "<stdin>") y el contenedor ya
# tiene /app en el path.
if globals().get("__file__", "") not in ("", "<stdin>"):
    raiz = Path(__file__).resolve().parents[4]
    if str(raiz) not in sys.path:
        sys.path.insert(0, str(raiz))

from app.metricas_listas import SALIDA_ESPERA, fin_de_intentos, salida_sin_metricas

HOY = dt.date(2026, 11, 9)

fin = fin_de_intentos(HOY)
assert fin == dt.datetime(2026, 11, 9, 16, 0, tzinfo=dt.UTC), fin
assert SALIDA_ESPERA == 75, SALIDA_ESPERA
print(f"fin_de_intentos({HOY}) = {fin:%H:%M} UTC")

antes = dt.datetime(2026, 11, 9, 15, 59, 59, tzinfo=dt.UTC)
out, err = io.StringIO(), io.StringIO()
with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
    rc = salida_sin_metricas("cycle amazon_us", ahora=antes)
assert rc == SALIDA_ESPERA == 75, rc
assert "sin métricas de ayer: espera al siguiente horario" in out.getvalue(), out.getvalue()
assert err.getvalue() == "", err.getvalue()
print(f"15:59:59 UTC: rc={rc} (espera)")

despues = dt.datetime(2026, 11, 9, 16, 0, tzinfo=dt.UTC)
out, err = io.StringIO(), io.StringIO()
with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
    rc = salida_sin_metricas("cycle amazon_us", ahora=despues)
assert rc == 1, rc
assert out.getvalue() == "", out.getvalue()
assert "cycle amazon_us: hoy no corrió" in err.getvalue(), err.getvalue()
assert "El último intento empieza a las 13:15 UTC" in err.getvalue(), err.getvalue()
assert "se corta solo a las 15:45 UTC" in err.getvalue(), err.getvalue()
assert "desde las 16:00 UTC la guarda da el día por perdido" in err.getvalue(), err.getvalue()
assert "termina a las 16:00" not in err.getvalue(), err.getvalue()
print(f"16:00 UTC: rc={rc} (avisa)")

# avisos-campana exige tambien la corrida de placements: su aviso no atribuye
# a las metricas una falta que puede ser de los placements.
out, err = io.StringIO(), io.StringIO()
with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
    rc = salida_sin_metricas("avisos-campana", ahora=despues, incluye_placements=True)
assert rc == 1, rc
assert "las métricas o los placements de ayer no llegaron" in err.getvalue(), err.getvalue()
assert "El último intento empieza a las 13:15 UTC" in err.getvalue(), err.getvalue()
print("avisos-campana: el aviso nombra metricas o placements")
print("S OK")
