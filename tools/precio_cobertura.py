"""Recuadro de cobertura del catalogo (REPRICING 01, A.7).

Lector: `python tools/precio_cobertura.py --platform <p>` imprime el
recuadro S10 contra la base como lector (`ORBIT_DSN_READ`, solo SELECT
via `app.precio.fuentes`); cuadra con las activas de la fuente canonica
de Orbit. Al lado muestra la cuenta de identidad (`listing`: identidad,
no activas, sin aviso) y, solo con `--puente-activas N`, el contraste
contra las activas del puente con aviso si difieren mas del 5 %.
`--platform meli` (R05-r3 B2) compara las activas del catalogo (lo
mismo que `MercadoMeli.catalogo` fluye a decisiones) contra las
activas vistas por la ultima corrida `meli_catalogo` terminada de
hoy: 0 si cuadran, 1 si difieren, 2 sin corrida (no medido). Las
activas sin miembros mapeados se cuentan aparte, no fallan.
Cero escritura.
"""

from __future__ import annotations

import argparse
import os
import sys

import psycopg

from app import precio_mercados as mercados
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
    universos_encendidos,
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


_SQL_CORRIDA_MELI_HOY = (
    "SELECT id, ok FROM ingest_run WHERE source = 'meli_catalogo'"
    " AND finished_at IS NOT NULL AND started_at::date = CURRENT_DATE"
    " ORDER BY id DESC LIMIT 1"
)
_SQL_ACTIVAS_CORRIDA = (
    "SELECT DISTINCT item_id FROM meli_publicacion_observation"
    " WHERE ingest_run_id = %s AND estado = 'active'"
)
_SQL_MAPEADAS = (
    "SELECT l.external_id FROM listing l WHERE l.platform = 'meli'"
    " AND EXISTS (SELECT 1 FROM listing_miembro m WHERE m.listing_id = l.id"
    " AND m.platform = 'meli' AND m.activo AND m.product_id IS NOT NULL)"
)


def _cobertura_meli(conn) -> int:
    """Activas del catalogo vs activas vistas por la corrida de hoy.

    0 cuadran, 1 difieren, 2 sin corrida terminada de hoy (no medido).
    """
    hoy = conn.execute("SELECT CURRENT_DATE").fetchone()[0]
    corrida = conn.execute(_SQL_CORRIDA_MELI_HOY).fetchone()
    if corrida is None:
        print("cobertura meli: sin corrida meli_catalogo terminada de hoy (no medido)")
        return 2
    run, ok = corrida
    api = {r[0] for r in conn.execute(_SQL_ACTIVAS_CORRIDA, (run,)).fetchall()}
    with mercados.abrir("meli") as vitrina:
        catalogo = {u.unidad.externo for u in vitrina.catalogo(conn, hoy)}
    mapeadas = {r[0] for r in conn.execute(_SQL_MAPEADAS).fetchall()}
    sin_mapear = sorted(api - mapeadas)
    faltan = sorted(api - catalogo - set(sin_mapear))
    sobran = sorted(catalogo - api)
    marca = "[CUADRA]" if not faltan and not sobran else "[NO-CUADRA]"
    print(
        f"cobertura meli {hoy.isoformat()} (corrida {run}, ok={ok}):"
        f" api_activas={len(api)} catalogo={len(catalogo)}"
        f" sin_mapear={len(sin_mapear)} {marca}"
    )
    if sin_mapear:
        print(
            f"  sin_mapear: {', '.join(sin_mapear)}"
            " (activas en la API sin miembros mapeados: no fluyen)"
        )
    for item in faltan:
        print(f"  faltan_en_catalogo: {item} (activa mapeada sin obs activa vigente)")
    for item in sobran:
        print(f"  sobran_en_catalogo: {item} (en catalogo, no activa en la corrida de hoy)")
    return 0 if not faltan and not sobran else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--platform", required=True, choices=("amazon_mx", "amazon_us", "meli"))
    ap.add_argument("--puente-activas", required=False, default=None, type=int)
    args = ap.parse_args(argv)
    if args.puente_activas is not None and args.puente_activas < 0:
        ap.error("--puente-activas debe ser mayor o igual que cero")
    try:
        conn = connect(_dsn_read())
    except (OrbitDbError, Abortar) as exc:
        print(f"precio_cobertura: {exc}", file=sys.stderr)
        return 2
    try:
        try:
            if args.platform == "meli":
                return _cobertura_meli(conn)
            hoy = hoy_base(conn)
            max_dias = max_dias_desde_settings(config_vigente_settings(conn))
            with mercados.abrir(args.platform) as vitrina:
                filas = leer_publicaciones(conn, vitrina, hoy=hoy)
            universos = universos_encendidos(conn)
            identidad = contar_listing_identidad(conn, platform=args.platform)
        except (psycopg.Error, ValueError) as exc:
            print(f"precio_cobertura: {exc}", file=sys.stderr)
            return 2
        rec = armar_recuadro(filas, platform=args.platform, max_dias=max_dias, universos=universos)
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
