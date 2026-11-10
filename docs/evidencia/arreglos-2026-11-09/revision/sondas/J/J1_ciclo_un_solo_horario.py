"""Sonda J1 (solo lectura, sin base): el ciclo de pujas y el vigia del impulso
salen 0 y no corren cuando la ingesta de las 07:10 aun no deja D-1 a su minuto,
y el crontab documentado no tiene otro horario para ellos.

Uso: PYTHONPATH=<copia> python J1_ciclo_un_solo_horario.py <copia>
"""

from __future__ import annotations

import io
import os
import re
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import psycopg

WT = Path(sys.argv[1])


class _Res:
    def __init__(self, fila=None, filas=None):
        self._fila = fila
        self._filas = filas or []

    def fetchone(self):
        return self._fila

    def fetchall(self):
        return self._filas


class _Info:
    transaction_status = psycopg.pq.TransactionStatus.IDLE


class ConnSinMetricas:
    """Postgres falso: la ingesta de hoy aun no escribio D-1 (toda guarda da None)."""

    info = _Info()

    def __init__(self, vivos=None):
        self.sql = []
        self._vivos = vivos or []

    def execute(self, sql, params=None):
        self.sql.append(" ".join(sql.split())[:70])
        if "FROM impulso i" in sql:
            return _Res(filas=self._vivos)
        return _Res(fila=None)

    def rollback(self):
        pass

    def commit(self):
        pass

    def close(self):
        pass


def corre(main, argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = main(argv)
    return rc, out.getvalue().strip(), err.getvalue().strip()


# 1) cycle: la guarda de metricas de ayer salta y sale 0; corre_ciclo nunca se llama.
os.environ["ORBIT_DSN_DECIDE"] = "postgresql://x@127.0.0.1:1/x"
os.environ["ORBIT_DSN_ADMIN"] = "postgresql://x@127.0.0.1:1/x"
import app.cli as cli  # noqa: E402
import app.cycle as ciclo  # noqa: E402

llamado = []
ciclo.corre_ciclo = lambda *a, **k: llamado.append(1)
conn = ConnSinMetricas()
cli.connect = lambda dsn, **kw: conn
rc, out, err = corre(cli.main, ["cycle", "--platform", "amazon_us"])
print(f"cycle amazon_us sin D-1: exit={rc} stdout={out!r} stderr={err!r} corre_ciclo_llamado={bool(llamado)}")

# 2) impulso-vigia con un impulso vivo: tambien sale 0 (su docstring promete 1 'sin ingesta').
import app.db as db  # noqa: E402

conn2 = ConnSinMetricas(vivos=[(7, "amazon_mx", None, None, None, None, None, None, None, None, None)])
db.connect = lambda dsn, **kw: conn2
from app import cli_bids  # noqa: E402

rc2, out2, err2 = corre(cli_bids.main_impulso_vigia, [])
print(f"impulso-vigia con 1 impulso vivo y sin D-1: exit={rc2} stdout={out2!r} stderr={err2!r}")
doc = cli_bids.main_impulso_vigia.__doc__.split("R09-F:")[1].split("\n")[0:3]
print("docstring R09-F:", " ".join(x.strip() for x in doc))

# 3) Horarios del ciclo/vigia/avisos en el bloque instalador de DEPLOY.md vs el plan.
deploy = (WT / "docs/DEPLOY.md").read_text(encoding="utf-8")
bloque = deploy.split("ORBIT_BLOCK=$(cat <<'CRON'")[1].split("\nCRON\n")[0]
for patron in ("app.cli cycle", "app.cli impulso-vigia", "app.cli avisos-campana", "app.cli ingest diario"):
    lineas = [l.split(" docker")[0].split(" /usr/bin/flock")[0] for l in bloque.splitlines() if patron in l]
    print(f"DEPLOY.md bloque instalador, {patron}: {lineas}")
plan = (WT / "plans/ingesta-01.md").read_text(encoding="utf-8")
for l in plan.splitlines():
    if re.match(r"\| 1[036]:0[58] ", l):
        print("plan ingesta-01:", l)
salida = (WT / "docs/evidencia/ingesta-01/ejecucion/D.1/instalador-salida-20261021-183311.txt").read_text()
print("crontab instalado 2026-10-21, lineas de cycle:",
      [l.split(" docker")[0] for l in salida.splitlines() if "app.cli cycle" in l])
from app.ads import diario  # noqa: E402

print("HORARIOS_INGESTA:", diario.HORARIOS_INGESTA, "TOPE_PARED_SEGUNDOS:", diario.TOPE_PARED_SEGUNDOS / 3600, "h")
print("texto de aviso de productos/ubicaciones:", diario._texto_plazo("las métricas por producto", "US", "10:40"))
