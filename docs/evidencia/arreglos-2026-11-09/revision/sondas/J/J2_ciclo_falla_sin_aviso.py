"""Sonda J2 (solo lectura, sin base ni red): cuando el ciclo de pujas truena,
el comando sale 1 al log y NADIE recibe Telegram; tampoco ads-salud mira el ciclo.

Uso: PYTHONPATH=<copia> python J2_ciclo_falla_sin_aviso.py <copia>
"""

from __future__ import annotations

import inspect
import io
import os
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import psycopg

WT = Path(sys.argv[1])
os.environ["ORBIT_DSN_DECIDE"] = "postgresql://x@127.0.0.1:1/x"

import app.cli as cli  # noqa: E402
import app.cycle as ciclo  # noqa: E402
import app.metricas_listas as ml  # noqa: E402
import app.notifica as notifica  # noqa: E402

envios = []
notifica._envia_texto = lambda texto, transport=None: envios.append(texto) or True
notifica.envia_aviso = lambda texto, transport=None: envios.append(texto) or True
notifica.canal_activo = lambda: True


class _Info:
    transaction_status = psycopg.pq.TransactionStatus.IDLE


class Conn:
    info = _Info()

    def close(self):
        pass


cli.connect = lambda dsn, **kw: Conn()
ml.guarda = lambda *a, **k: True  # metricas de ayer listas
ciclo.motivo_de_salto = lambda conn, p: None  # sin ciclo hoy


def truena(*a, **k):
    raise RuntimeError("server closed the connection unexpectedly (lectura de ventanas)")


ciclo.corre_ciclo = truena
out, err = io.StringIO(), io.StringIO()
with redirect_stdout(out), redirect_stderr(err):
    rc = cli.main(["cycle", "--platform", "amazon_mx"])
print(f"cycle amazon_mx con fallo: exit={rc} stderr={err.getvalue().strip()!r} telegram_enviados={len(envios)}")

src_sello = inspect.getsource(ciclo._sello_fallido)
src_corre = inspect.getsource(ciclo.corre_ciclo.__wrapped__) if hasattr(ciclo.corre_ciclo, "__wrapped__") else None
import importlib  # noqa: E402

real = importlib.reload(ciclo)
print("_sello_fallido llama a notifica:", "notifica" in inspect.getsource(real._sello_fallido))
print("corre_ciclo except llama a notifica:", "notifica" in inspect.getsource(real.corre_ciclo))

# ads-salud: el unico vigilante diario de Ads mira ingest_run, nunca optimizer_cycle.
salud_src = (WT / "app/ads/salud.py").read_text(encoding="utf-8")
print("app/ads/salud.py menciona optimizer_cycle:", "optimizer_cycle" in salud_src)
# Un exito de ingesta terminado hoy cierra el atraso aunque el ciclo de las 08:40 se haya saltado.
import datetime as dt  # noqa: E402

from app.ads.salud import unidades_atrasadas  # noqa: E402

hoy = dt.date(2026, 11, 9)
print("atraso con la ingesta terminada hoy a las 08:50:", unidades_atrasadas([(1, "amazon_us", hoy)], hoy))
# Lineas del ciclo documentadas en DEPLOY.md: sin cron-avisa (el log es el unico rastro)
deploy = (WT / "docs/DEPLOY.md").read_text(encoding="utf-8")
for linea in deploy.splitlines():
    if "app.cli cycle --platform" in linea and linea.startswith("4"):
        print("linea documentada:", linea[:160], "| cron-avisa:", "cron-avisa" in linea)
