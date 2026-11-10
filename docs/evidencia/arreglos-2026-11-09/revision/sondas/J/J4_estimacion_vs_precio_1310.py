"""Sonda J4 (solo lectura, sin base ni red): la espera de 20 min que CLARIDAD 01 F
agrego a refresh_estimacion.sh puede dejar la estimacion escrita DESPUES de la corrida
de precios de las 13:10. En esa banda el script sale 0 (no avisa) y el lector de
precios descarta la oferta de las 06:36 por vieja: el dia queda sin decidir.

Uso: PYTHONPATH=<copia> python J4_estimacion_vs_precio_1310.py <copia>
"""

from __future__ import annotations

import datetime as dt
import re
import sys
from decimal import Decimal
from pathlib import Path

WT = Path(sys.argv[1])
script = (WT / "tools/refresh_estimacion.sh").read_text(encoding="utf-8")
vueltas = int(re.search(r"seq (\d+)", script).group(1))
pausa = int(re.search(r"sleep (\d+)", script).group(1))
espera_max = dt.timedelta(seconds=vueltas * pausa)
print(f"espera maxima al bridge: {vueltas} x {pausa} s = {espera_max}")

# Duracion medida de la ingesta de estimacion (cron 12:45 -> escenario observado), consultas.txt q4
ingesta = dt.timedelta(minutes=7, seconds=52)
dia = dt.datetime(2026, 11, 9, tzinfo=dt.UTC)
cron = dia.replace(hour=12, minute=45)
precio = dia.replace(hour=13, minute=10)
print(f"ingesta medida 31-oct/1-nov: 12:45:00 -> 12:52:52 ({ingesta})")
for fin_bridge in ("12:51:45", "13:00:00", "13:02:30", "13:04:45"):
    h, m, s = map(int, fin_bridge.split(":"))
    fin = dia.replace(hour=h, minute=m, second=s)
    # el bucle consulta cada 15 s: copia en la primera consulta que ve al bridge terminado
    vueltas_hechas = -(-int((fin - cron).total_seconds()) // pausa)
    copia = cron + dt.timedelta(seconds=vueltas_hechas * pausa)
    escrito = copia + ingesta
    sale = 0 if copia - cron < espera_max else 1
    print(
        f"bridge US termina {fin_bridge} -> copia {copia:%H:%M:%S}, estimacion escrita ~{escrito:%H:%M:%S}"
        f" -> {'ANTES' if escrito <= precio else 'DESPUES'} de la corrida de 13:10; refresh_estimacion sale {sale}"
    )

# Lo que lee la corrida de 13:10 si la estimacion nueva aun no esta: el escenario anterior
# (oferta del bridge de las 06:36). Lector real con una fila sembrada.
from app import estimacion_reader as er  # noqa: E402


class _Res:
    def __init__(self, filas):
        self._filas = filas

    def fetchall(self):
        return self._filas


class Conn:
    def execute(self, sql, params=None):
        fila = [None] * 25
        fila[0], fila[1], fila[2] = 1, 10, "fbm"
        fila[3] = dia.date()
        fila[4] = dia.replace(hour=6, minute=52)
        fila[5], fila[6] = "disponible", []
        fila[7], fila[8], fila[9] = Decimal("120"), Decimal("0.2"), "USD"
        fila[10], fila[11], fila[12] = [], [], {}
        fila[16] = dia.replace(hour=6, minute=36)
        return _Res([tuple(fila)])


(esc,) = er.leer_escenarios(Conn(), [10], as_of=precio)
print(f"lector a las 13:10 con oferta de 06:36: estado={esc.estado} motivos={esc.motivos}")
corrida = (WT / "app/precio/corrida.py").read_text(encoding="utf-8")
print("la corrida salta lo ya decidido hoy:",
      "SELECT listing_id FROM precio_decision WHERE platform = %s AND decision_date = %s" in corrida)
