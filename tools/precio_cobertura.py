"""Recuadro de cobertura del catalogo (REPRICING 01, A.7).

Lector: `python tools/precio_cobertura.py --platform <p>` imprime el
recuadro S10 contra la base como lector (`ORBIT_DSN_READ`, solo SELECT
via `app.precio.fuentes`); cuadra con las activas de la fuente canonica
de Orbit. Al lado muestra la cuenta de identidad (`listing`: identidad,
no activas, sin aviso) y, solo con `--puente-activas N`, el contraste
contra las activas del puente con aviso si difieren mas del 5 %.
`--platform meli` no tiene fuente canonica en Orbit: imprime
`activas=unknown` y sale 0 sin recuadro. Cero Amazon, cero escritura.
"""

from __future__ import annotations

import argparse
import os
import sys

import psycopg

from app.db import OrbitDbError, connect
from app.precio.cobertura import (
    armar_recuadro,
    aviso_puente,
    cuadra_exact,
)
from app.precio.fuentes import (
    config_vigente_settings,
    contar_listing_identidad,
    hoy_base,
    leer_publicaciones,
    max_dias_desde_settings,
)


class Abortar(RuntimeError):
    """Fallo fail-closed del tool: mensaje al dueno, exit 2."""


def _dsn_read() -> str:
    dsn = os.environ.get("ORBIT_DSN_READ")
    if not dsn:
        raise Abortar("ORBIT_DSN_READ no esta en el entorno (unico DSN del tool)")
    return dsn


def _dinero(fila) -> str:
    if fila.precio is None:
        return "sin_precio"
    return f"{fila.precio:.2f} {fila.moneda}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--platform", required=True, choices=("amazon_mx", "amazon_us", "meli"))
    ap.add_argument("--puente-activas", required=False, default=None, type=int)
    args = ap.parse_args(argv)
    if args.platform == "meli":
        print("activas=unknown: sin fuente canonica de MeLi en Orbit (hecho 6; la trae M.3b)")
        return 0
    try:
        conn = connect(_dsn_read())
    except OrbitDbError as exc:
        print(f"precio_cobertura: {exc}", file=sys.stderr)
        return 2
    try:
        try:
            hoy = hoy_base(conn)
            max_dias = max_dias_desde_settings(config_vigente_settings(conn))
            filas = leer_publicaciones(conn, platform=args.platform, hoy=hoy)
            identidad = contar_listing_identidad(conn, platform=args.platform)
        except (psycopg.Error, ValueError) as exc:
            print(f"precio_cobertura: {exc}", file=sys.stderr)
            return 2
        rec = armar_recuadro(filas, platform=args.platform, max_dias=max_dias)
        marca = "[CUADRA]" if cuadra_exact(rec) else "[NO-CUADRA]"
        no_ev = " ".join(f"{m}={n}" for m, n in rec.no_evaluadas)
        fuera = " ".join(f"{f}={n}" for f, n in rec.fuera_de_alcance)
        print(f"recuadro {rec.platform} {hoy.isoformat()} (canonica Orbit): activas={rec.activas}")
        print(
            f"  evaluadas={rec.evaluadas} no_evaluadas={sum(n for _, n in rec.no_evaluadas)}"
            f" [{no_ev}] sin_goal={len(rec.sin_goal)}"
            f" fuera_de_alcance={sum(n for _, n in rec.fuera_de_alcance)}"
            f" [{fuera}] {marca}"
        )
        for detalle in rec.sin_goal:
            print(f"  sin_goal: {detalle.sku} ({_dinero(detalle)}, {detalle.canal})")
        print(
            f"  listing_identidad={identidad}"
            " (no son activas: Orbit no guarda el estado del bridge)"
        )
        for aviso in rec.avisos:
            print(f"  {aviso}")
        if args.puente_activas is None:
            print("  puente_activas=unknown (el estado del bridge no esta en Orbit)")
        else:
            print(f"  puente bridge={args.puente_activas} vs canonica={rec.activas}")
            puente_aviso = aviso_puente(activas=rec.activas, puente=args.puente_activas)
            if puente_aviso is not None:
                print(f"  {puente_aviso}")
        if not cuadra_exact(rec):
            return 1
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
