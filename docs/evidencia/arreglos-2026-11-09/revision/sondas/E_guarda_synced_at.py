"""Sonda E-H1: la guarda de frescura de estructura (synced_at > 48h) no dispara
si el propio motor aplico algo en las ultimas 48 h.

windows._SQL_SYNC_PLATAFORMA = SELECT max(s.synced_at) FROM ad_entity_state ... (TODAS las filas)
apply._SQL_ACTUALIZA_CACHE  = UPDATE ad_entity_state SET current_bid = %s, synced_at = now() ...
(tambien apply_cola.py:310, apply_harvest_reconciliacion.py:121, apply_producto.py:66).

Se simula la tabla: 3590 filas que el sync de estructura (06:45) escribio hace 5 dias
(el sync murio) y 1 fila que el apply del motor refresco ayer a las 08:40.
Se pasa a la guarda real con una conexion falsa que devuelve lo que daria el SQL.
"""

from __future__ import annotations

import datetime as dt

from app.optimizer import windows

ahora = dt.datetime(2026, 11, 9, 8, 40, tzinfo=dt.UTC)
sync_estructura = ahora - dt.timedelta(days=5)  # ultimo sync de estructura exitoso
apply_motor = ahora - dt.timedelta(hours=24)  # readback del bid aplicado ayer

filas_synced_at = [sync_estructura] * 3590 + [apply_motor]
max_synced = max(filas_synced_at)  # lo que devuelve _SQL_SYNC_PLATAFORMA


class _Cur:
    def __init__(self, valor):
        self._valor = valor

    def fetchone(self):
        return (self._valor,)


class _Conn:
    def execute(self, sql, params=()):
        if sql is windows._SQL_WATERMARK_PLATAFORMA:
            return _Cur(ahora.date() - dt.timedelta(days=1))
        if sql is windows._SQL_SYNC_PLATAFORMA:
            return _Cur(max_synced)
        raise AssertionError(sql)


print("ultimo sync de estructura:", sync_estructura.isoformat(), f"({(ahora - sync_estructura).days} dias)")
print("max(synced_at) que ve la guarda:", max_synced.isoformat())
print("guarda_plataforma ->", windows.guarda_plataforma(_Conn(), "amazon_us", ahora=ahora))
print("sin el apply del motor ->", end=" ")
max_synced = sync_estructura
print(windows.guarda_plataforma(_Conn(), "amazon_us", ahora=ahora))
