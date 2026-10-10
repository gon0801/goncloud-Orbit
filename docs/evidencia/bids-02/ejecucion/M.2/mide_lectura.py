#!/usr/bin/env python
"""M.2: mide `lee_plataforma` por plataforma sobre la copia de produccion.

Uso (cuando claw lo encarga):
    cd ~/dev/wt-bids-02-s2
    PYTHONPATH=. python docs/evidencia/bids-02/ejecucion/M.2/mide_lectura.py [--dsn DSN_COPIA]

El DSN sale de --dsn, de ORBIT_M2_DSN o de ORBIT_TEST_DSN con la base
`orbit_copia_bids02_0b` (la que arma `ejecucion/0.b/copia.sh`). Solo LEE la
copia; si le faltan las vistas de la 0060 las aplica (base desechable
local, jamas produccion). No imprime el DSN.

Salida: una linea por plataforma con los segundos de `lee_plataforma` mas
conteos de contexto (fuera de la seccion medida). Esperado: menos de 1 s
por plataforma. La economia es andamio del medidor (moneda real por
plataforma, resto de los docstrings de caso.py): la economia real la
resuelve el ciclo en M.3.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import time
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg import sql as pgsql

RAIZ = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(RAIZ))

from app.lecturas_caso import lee_plataforma  # noqa: E402
from app.optimizer.caso import EconomiaPlataforma  # noqa: E402

BASE_COPIA = "orbit_copia_bids02_0b"
ROLES_0060 = ("app_decide", "app_read", "app_admin")
PLATAFORMAS = (("amazon_mx", "MXN", "350"), ("amazon_us", "USD", "36"))


def _dsn(args) -> str:
    if args.dsn:
        return args.dsn
    if os.environ.get("ORBIT_M2_DSN"):
        return os.environ["ORBIT_M2_DSN"]
    base = os.environ.get("ORBIT_TEST_DSN", "postgresql://orbit:orbit@localhost:5432/postgres")
    return base.rsplit("/", 1)[0] + "/" + BASE_COPIA


def _asegura_vistas(conn) -> None:
    """Crea los roles de privilegios si faltan y aplica la 0060 si la copia
    no trae las vistas (esquema de produccion anterior a BIDS 02)."""
    hay = {r[0] for r in conn.execute("SELECT rolname FROM pg_roles").fetchall()}
    for rol in ROLES_0060:
        if rol not in hay:
            conn.execute(pgsql.SQL("CREATE ROLE {} NOLOGIN").format(pgsql.Identifier(rol)))
    existe = conn.execute("SELECT to_regclass('public.v_hoja_activa') IS NOT NULL").fetchone()[0]
    if not existe:
        conn.execute((RAIZ / "migrations" / "0060_bids02_base_lectura.sql").read_text())


def _economia(moneda: str, tope: str) -> EconomiaPlataforma:
    return EconomiaPlataforma(
        moneda=moneda,
        equilibrio_acos_pct=None,
        gasto_para_concluir=Decimal(tope),
        confianza_recorte=Decimal("0.80"),
        confianza_subida=Decimal("0.70"),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", default=None)
    args = parser.parse_args()
    with psycopg.connect(_dsn(args), autocommit=True) as conn:
        conn.execute("SET TIME ZONE 'UTC'")
        _asegura_vistas(conn)
        ahora = dt.datetime.now(dt.UTC)
        for plataforma, moneda, tope in PLATAFORMAS:
            inicio = time.perf_counter()
            lee_plataforma(conn, plataforma, ahora, economia=_economia(moneda, tope))
            segundos = time.perf_counter() - inicio
            hojas = conn.execute(
                "SELECT count(*) FROM v_hoja_activa WHERE platform = %s", (plataforma,)
            ).fetchone()[0]
            print(f"{plataforma}: lee_plataforma {segundos:.2f} s ({hojas} hojas activas)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
