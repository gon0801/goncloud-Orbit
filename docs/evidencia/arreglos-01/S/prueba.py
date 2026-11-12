"""S (H-H2): el codigo desplegado espera las metricas hasta las 16:00 UTC y
despues avisa. Funcion pura: sin base ni red.
Se corre con `bash docs/evidencia/arreglos-01/en_prod.sh .../S/prueba.py`.
"""

import contextlib
import datetime as dt
import io

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
assert "16:00 UTC" in err.getvalue(), err.getvalue()
print(f"16:00 UTC: rc={rc} (avisa)")
print("S OK")
