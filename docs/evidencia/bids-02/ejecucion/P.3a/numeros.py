#!/usr/bin/env python
"""P.3a: imprime `lee_danadas(...).como_dict()` sobre la copia de produccion.

Uso (regla 11, cuando claw lo encarga):
    cd ~/dev/goncloud-Orbit   # o el arbol de la seccion 2
    PYTHONPATH=. python docs/evidencia/bids-02/ejecucion/P.3a/numeros.py \
        --plataforma amazon_mx [--dsn DSN_COPIA]

El DSN sale de --dsn, de ORBIT_P3A_DSN o de ORBIT_TEST_DSN con la base
`orbit_copia_bids02_0b` (la que arma `ejecucion/0.b/copia.sh`). Solo LEE la
copia; si le faltan las vistas de la 0060 las aplica (base desechable
local, jamas produccion). No imprime el DSN.

Salida: JSON con una fila por hoja (hoja_id, recortes, pedidos_antes_90d,
clics_antes_14d, clics_ahora_14d, bid_antes, bid_hoy, ya_regresada), para
comparar contra `control.sql` sobre la misma copia.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import psycopg
from psycopg import sql as pgsql

RAIZ = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(RAIZ))

from app.pantalla_danadas import lee_danadas  # noqa: E402

BASE_COPIA = "orbit_copia_bids02_0b"
ROLES_0060 = ("app_decide", "app_read", "app_admin")


def _dsn(args) -> str:
    if args.dsn:
        return args.dsn
    if os.environ.get("ORBIT_P3A_DSN"):
        return os.environ["ORBIT_P3A_DSN"]
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plataforma", required=True, choices=("amazon_mx", "amazon_us"))
    parser.add_argument("--dsn", default=None)
    args = parser.parse_args()
    with psycopg.connect(_dsn(args), autocommit=True) as conn:
        conn.execute("SET TIME ZONE 'UTC'")
        _asegura_vistas(conn)
        pantalla = lee_danadas(conn, plataforma=args.plataforma)
    print(json.dumps(pantalla.como_dict(), indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
