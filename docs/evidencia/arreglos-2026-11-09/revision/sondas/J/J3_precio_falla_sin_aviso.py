"""Sonda J3 (solo lectura, sin base ni red): una corrida de precios que truena a media
decision (como la de MeLi del 22 al 25-oct) sale 1 al log y no manda ningun Telegram.

Uso: PYTHONPATH=<copia> python J3_precio_falla_sin_aviso.py <copia>
"""

from __future__ import annotations

import argparse
import contextlib
import io
import os
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

WT = Path(sys.argv[1])
os.environ["ORBIT_DSN_DECIDE"] = "postgresql://x@127.0.0.1:1/x"

import app.cli as cli  # noqa: E402
import app.cli_precio as cp  # noqa: E402
import app.notifica as notifica  # noqa: E402
import app.precio.corrida as corrida  # noqa: E402

envios = []
notifica._envia_texto = lambda texto, transport=None: envios.append(texto) or True
avisos = []
notifica.avisar_precio = lambda *a, **k: avisos.append(a)

cli._conexion_decide = lambda: (object(), lambda: None)


@contextlib.contextmanager
def mercado_falso():
    yield type("M", (), {"clave": "meli"})()


cp._abrir_plataforma = lambda cli_mod, platform: mercado_falso()


def truena(conn, mercado, *, owner, avisar=None):
    raise RuntimeError("precio_decision: product_id 375 no es miembro del listing 1556")


corrida.correr = truena
args = argparse.Namespace(reporte=False, desde=None, hasta=None, platform=["meli"])
out, err = io.StringIO(), io.StringIO()
with redirect_stdout(out), redirect_stderr(err):
    rc = cp.main_precio(args)
print(f"precio meli con fallo a media corrida: exit={rc} stderr={err.getvalue().strip()!r}"
      f" avisar_precio_llamado={len(avisos)} telegram_enviados={len(envios)}")

deploy = (WT / "docs/DEPLOY.md").read_text(encoding="utf-8")
for linea in deploy.splitlines():
    if "app.cli precio --platform meli" in linea and linea[:1].isdigit():
        print("linea documentada MeLi:", linea[:120], "| cron-avisa:", "cron-avisa" in linea)
rev = (WT / "docs/evidencia/alertas-2026-10-25/revision.md").read_text(encoding="utf-8")
for l in rev.splitlines():
    if l.startswith("Corridas MeLi observadas"):
        print("precedente 25-oct:", l[:260])
