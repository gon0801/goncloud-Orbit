"""Sonda F2: la guarda de frescura de estructura del ciclo (48 h) lee
MAX(ad_entity_state.synced_at) de TODA la plataforma, y el aplicador escribe
synced_at = now() en la fila de cada entidad que toca. Un solo bid aplicado
"refresca" la plataforma entera aunque el sync de estructura lleve dias muerto.

Pura: conexion falsa que contesta MAX(synced_at) como lo haria Postgres despues
del UPDATE del aplicador. Cero red, cero base.
"""
from __future__ import annotations

import datetime as dt

from app import apply, apply_cola, apply_producto, apply_harvest_reconciliacion
from app.optimizer import windows

AHORA = dt.datetime(2026, 11, 9, 8, 40, tzinfo=dt.UTC)
ULTIMO_SYNC_REAL = AHORA - dt.timedelta(days=10)        # ingest structure muerto hace 10 dias
ULTIMO_BID_APLICADO = AHORA - dt.timedelta(hours=24)    # ayer el ciclo aplico 1 bid


class Cur:
    def __init__(self, v):
        self.v = v

    def fetchone(self):
        return (self.v,)


class FakeConn:
    def __init__(self, synced_rows):
        self.synced_rows = synced_rows  # synced_at por entidad

    def execute(self, sql, params=None):
        if sql is windows._SQL_WATERMARK_PLATAFORMA:
            return Cur((AHORA - dt.timedelta(days=2)).date())
        if sql is windows._SQL_SYNC_PLATAFORMA:
            return Cur(max(self.synced_rows))
        raise AssertionError(sql)


print("escritores de synced_at=now() fuera del sync de estructura:")
for nombre, sql in (
    ("apply._SQL_ACTUALIZA_CACHE", apply._SQL_ACTUALIZA_CACHE),
    ("apply_cola._SQL_CACHE_ESTADO", apply_cola._SQL_CACHE_ESTADO),
    ("apply_producto._SQL_CACHE_ESTADO", apply_producto._SQL_CACHE_ESTADO),
    ("apply_harvest_reconciliacion._SQL_CACHE_ESTADO", apply_harvest_reconciliacion._SQL_CACHE_ESTADO),
):
    print(f"  {nombre}: {' '.join(sql.split())}")
print("guarda lee:", " ".join(windows._SQL_SYNC_PLATAFORMA.split()))

# 3500 entidades, todas con el sync real de hace 10 dias...
filas = [ULTIMO_SYNC_REAL] * 3500
print("sin applies recientes ->", windows.guarda_plataforma(FakeConn(filas), "amazon_mx", ahora=AHORA))
# ...y una sola tocada ayer por el aplicador.
filas[0] = ULTIMO_BID_APLICADO
print("con 1 bid aplicado ayer ->", windows.guarda_plataforma(FakeConn(filas), "amazon_mx", ahora=AHORA))
