"""Sonda D (cola): fila released que vende el dia A y luego deja de tener
impresiones. La ventana de cortes se ancla en la ultima fecha propia de la
hoja (max - 3), asi que al vencer el episodio el ancla queda fuera de la
ventana madura y la subpolitica responde `indeterminada` (espera) mientras la
hoja no vuelva a tener filas. Pura, sin base.
  /Users/dn/dev/goncloud-Orbit/.venv/bin/python -I <este archivo> <orbit07-wt>
"""

import datetime as dt
import sys

sys.path.insert(0, sys.argv[1])

from app.optimizer import proteccion as subp  # noqa: E402
from app.optimizer.caso import (  # noqa: E402
    VERSION_PROTECCION_VENTA,
    EpisodioProteccion,
    InsumosProteccion,
    LecturaReciente,
)
from app.optimizer.windows import fin_ventana_cortes, inicio_ventana  # noqa: E402

A = dt.date(2026, 11, 3)  # dia de la venta = ultima fila de la hoja
episodio = EpisodioProteccion(ancla=A, vence_el=A + dt.timedelta(days=10))
for dias_despues in (10, 15, 30, 60):
    hoy = A + dt.timedelta(days=dias_despues)
    decidido = dt.datetime.combine(hoy, dt.time(12), tzinfo=dt.UTC)
    fin = fin_ventana_cortes(A, decidido)  # max_fecha propia = A
    r = subp.evalua(
        InsumosProteccion(
            version=VERSION_PROTECCION_VENTA,
            evaluado_el=hoy,
            reciente=LecturaReciente(pedidos=0, ancla=None, cubierta=True, observado_al=None),
            ventana_desde=inicio_ventana(fin),
            ventana_hasta=fin,
            episodio=episodio,
        )
    )
    print(f"hoy=A+{dias_despues}: ventana_cortes_fin={fin} (< ancla {A}) -> accion={r.accion} motivo={r.motivo}")
