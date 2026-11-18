import dataclasses
import datetime as dt
import inspect

from app import dashboard_resumen
from app.dia_cerrado import dia_local
from app.meli.ads_source import OrigenMeliAds, leer_origen

noche = dt.datetime(2026, 11, 13, 3, 17, tzinfo=dt.UTC)
assert dia_local("meli", noche) == dt.date(2026, 11, 12), "21:17 del 12-nov en Mexico"
assert "ahora" in inspect.signature(leer_origen).parameters, "leer_origen sin reloj"
assert "sin_cerrar" in {f.name for f in dataclasses.fields(OrigenMeliAds)}, "sin contador del dia en curso"
assert not hasattr(dashboard_resumen, "_fin_del_dia"), "el Resumen conserva su copia de zonas"
print("D-F desplegado")
