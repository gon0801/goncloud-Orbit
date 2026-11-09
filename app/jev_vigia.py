"""`python -m app.cli jev-vigia` (CLARIDAD 01 B.5): avisa si Jev dejo de correr.

Corre fuera del proceso de jev-senales, una vez al dia a las 11:00 UTC, con la
misma conexion de lectura (`ORBIT_DSN_READ`; `app_ingest` no puede leer
`jev_corrida`, 0052). Ve lo que el aviso de `jev_senales.main` no ve: la linea
de cron borrada o un candado colgado. No guarda estado: avisa cada dia mientras
siga detenido.

Salida 0 si Jev esta al dia, apagado o el aviso salio; 1 si esta detenido y el
aviso no salio (asi cron-avisa lo dice por su lado); 2 sin DSN.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import timedelta

from app.db import connect
from app.jev_lectura import Apagado
from app.jev_senales import _ajustes
from app.redaction import scrub

HORAS_SIN_CORRER = 13


def revisa(conn) -> str | None:
    """El texto del aviso, o None si Jev esta apagado o su ultimo `inicio`
    tiene 13 h o menos (corre a las 09:30 y 21:30 UTC)."""
    if isinstance(_ajustes(conn), Apagado):
        return None
    inicio, ahora = conn.execute(
        "SELECT max(at), now() FROM jev_corrida WHERE evento = 'inicio'"
    ).fetchone()
    if inicio is not None and ahora - inicio <= timedelta(hours=HORAS_SIN_CORRER):
        return None
    if inicio is None:
        desde = "Nunca ha corrido."
    else:
        desde = f"La última vez empezó hace {int((ahora - inicio).total_seconds() // 3600)} horas."
    return (
        "[Orbit] Gasto sin venta no se está calculando: Jev está encendido pero no corre."
        f" {desde} Debía correr dos veces al día. La pantalla Gasto sin venta muestra datos viejos."
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli jev-vigia",
        description=(
            f"Avisa por Telegram si Jev esta encendido y no empieza una corrida desde hace"
            f" mas de {HORAS_SIN_CORRER} h. Solo lee (ORBIT_DSN_READ)."
        ),
    )
    parser.add_argument("--seco", action="store_true", help="imprime el aviso sin mandarlo")
    args = parser.parse_args(argv)
    dsn = os.environ.get("ORBIT_DSN_READ")
    if not dsn:
        print("jev-vigia: falta ORBIT_DSN_READ", file=sys.stderr)
        return 2
    from app import notifica

    try:
        with connect(dsn) as conn:
            aviso = revisa(conn)
    except Exception as exc:
        print(f"jev-vigia fallo: {type(exc).__name__}: {scrub(str(exc))}", file=sys.stderr)
        return 1
    if aviso is None:
        print("jev-vigia: nada que avisar (Jev apagado o al dia)")
        return 0
    print(f"jev-vigia: {aviso}")
    if args.seco:
        return 0
    if notifica.envia_aviso(aviso):
        return 0
    print("jev-vigia: el aviso no salio", file=sys.stderr)
    return 1
