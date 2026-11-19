"""Prueba pura del bloque W-E para en_prod.sh (sin base ni red)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from app import pantalla_busquedas, pantalla_ruido
from app.optimizer.windows import DIAS_MADUREZ_CORTES


class _Cursor:
    def __init__(self, filas):
        self._filas = filas

    def fetchall(self):
        return list(self._filas)

    def fetchone(self):
        return self._filas[0] if self._filas else None


class _ConnFalsa:
    def __init__(self, respuestas):
        self._respuestas = respuestas

    def execute(self, sql, params=None):
        for fragmento, filas in self._respuestas:
            if fragmento in sql:
                return _Cursor(filas)
        return _Cursor([])


hoy = dt.date(2026, 11, 12)
conn_busquedas = _ConnFalsa([("FROM config_version", [({},)])])
busquedas = pantalla_busquedas.lee_busquedas(conn_busquedas, plataforma="amazon_mx", hoy=hoy)
assert busquedas.hasta == dt.date(2026, 11, 2), busquedas.hasta
assert busquedas.hasta == hoy - dt.timedelta(days=DIAS_MADUREZ_CORTES), busquedas.hasta
assert (busquedas.hasta - busquedas.desde).days + 1 == 90, (busquedas.desde, busquedas.hasta)

encendido = hoy - dt.timedelta(days=20)
filas = []
fecha = encendido - dt.timedelta(days=30)
while fecha <= hoy - dt.timedelta(days=1):
    inmaduro = fecha >= hoy - dt.timedelta(days=8)
    venta = Decimal("76") if inmaduro else Decimal("100")
    filas.append((fecha, Decimal("20"), 1, venta))
    fecha += dt.timedelta(days=1)

conn_ruido = _ConnFalsa(
    [
        ("FROM config_version c", [(dt.datetime.combine(encendido, dt.time(9), dt.UTC),)]),
        ("FROM v_metric_latest m", filas),
    ]
)
comparacion = pantalla_ruido._comparacion(conn_ruido, "amazon_mx", 30, hoy=hoy)
assert comparacion is not None
assert comparacion["despues_hasta"] == "2026-11-03", comparacion
assert comparacion["despues"]["acos_pct"] == "20.00", comparacion["despues"]

parcial_gasto = pantalla_ruido._bloque(
    [
        (dt.date(2026, 10, 10), Decimal("10"), 1, Decimal("100")),
        (dt.date(2026, 10, 10), None, 1, Decimal("100")),
    ]
)
assert parcial_gasto is not None
assert parcial_gasto["gasto"] is None, parcial_gasto
assert parcial_gasto["acos_pct"] is None, parcial_gasto
assert parcial_gasto["causa_acos"] == "cobertura_incompleta", parcial_gasto

print("W-E OK")
