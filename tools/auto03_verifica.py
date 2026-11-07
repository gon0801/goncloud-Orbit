"""Verificacion de las decisiones por producto (AUTO-03) contra los datos reales.

Corre el MISMO codigo del ciclo en solo lectura (ORBIT_DSN_READ, transaccion
READ ONLY) para que la evidencia previa a cada `live` no espere un ciclo.

- evalua --platform P [--json ruta]: app/productos_io.py y el resolutor sobre
  los datos de hoy; imprime resumen_cobertura y, con --json, guarda
  {resumen, entradas, evaluaciones}.
- replay [--json ruta | --cycle-id N|ultimo]: vuelve a correr
  resolver_producto sobre cada entrada congelada e imprime
  `replay <plataforma>: diferencias: N`. Sin argumentos toma el ultimo ciclo
  done de cada plataforma. Sale 1 con alguna diferencia, con un ciclo cuya
  meta no estaba en off y trae notes.productos.error, con menos o mas filas
  que notes.productos.productos_evaluados, o sin evaluaciones que revisar.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from collections.abc import Mapping, Sequence

from app import productos_io
from app.cycle import lee_goals
from app.db import connect
from app.optimizer import productos as p

_SQL_CONFIG = "SELECT settings FROM config_version ORDER BY id DESC LIMIT 1"

_SQL_CICLO = """
SELECT id, platform::text, notes FROM optimizer_cycle
 WHERE motor = 'ads_optimizer' AND id = %s
"""

_SQL_ULTIMO_CICLO = """
SELECT id, platform::text, notes FROM optimizer_cycle
 WHERE motor = 'ads_optimizer' AND platform = %s::platform AND status = 'done'
 ORDER BY id DESC LIMIT 1
"""

_SQL_EVALUACIONES = """
SELECT entrada, resultado FROM evaluacion_producto_ciclo
 WHERE cycle_id = %s ORDER BY product_id
"""


def _conexion():
    dsn = os.environ.get("ORBIT_DSN_READ")
    if not dsn:
        raise SystemExit("ORBIT_DSN_READ no esta definido")
    conn = connect(dsn, autocommit=True)
    conn.execute("SET TIME ZONE 'UTC'")
    return conn


def evalua(plataforma: str) -> dict:
    with _conexion() as conn, conn.transaction():
        conn.execute("SET TRANSACTION READ ONLY")
        fila = conn.execute(_SQL_CONFIG).fetchone()
        settings = fila[0] if fila else {}
        entradas = productos_io.lee_entradas_productos(
            conn, plataforma, decidido_el=dt.datetime.now(dt.UTC), settings=settings
        )
        campanas = sorted(
            {grupo.campana_id for e in entradas for grupo in e.grupos_aptos_para_meta}
        )
        goals = lee_goals(conn, plataforma, campanas)
    evaluaciones = [p.resolver_producto(e) for e in entradas]
    return {
        "resumen": productos_io.resumen_cobertura(plataforma, evaluaciones, goals),
        "entradas": [e.como_json() for e in entradas],
        "evaluaciones": [productos_io.resultado_json(ev, ()) for ev in evaluaciones],
    }


def diferencias(filas: Sequence[tuple[Mapping, Mapping]]) -> int:
    """Cuantas evaluaciones congeladas no reproduce el resolutor vigente."""
    n = 0
    for entrada, resultado in filas:
        congelada = productos_io.evaluacion_de_fila(entrada, resultado)
        n += p.resolver_producto(congelada.entrada) != congelada
    return n


def _replay_ciclo(conn, ciclo: tuple | None, plataforma: str) -> bool:
    if ciclo is None:
        print(f"replay {plataforma}: sin evaluaciones (sin ciclo done)")
        return False
    cycle_id, plataforma, notes = ciclo
    notas = (json.loads(notes) if isinstance(notes, str) else notes or {}).get("productos")
    filas = conn.execute(_SQL_EVALUACIONES, (cycle_id,)).fetchall()
    ok = True
    if notas is not None and notas.get("error"):
        print(f"replay {plataforma}: ciclo {cycle_id} con error: {notas['error']}")
        ok = False
    if notas is not None and len(filas) != notas.get("productos_evaluados"):
        print(
            f"replay {plataforma}: ciclo {cycle_id} guardo {len(filas)} evaluaciones y "
            f"evaluo {notas.get('productos_evaluados')}"
        )
        ok = False
    if not filas:
        print(f"replay {plataforma}: sin evaluaciones (ciclo {cycle_id})")
        return False
    n = diferencias(filas)
    print(f"replay {plataforma}: diferencias: {n} (ciclo {cycle_id}, {len(filas)} evaluaciones)")
    return ok and n == 0


def replay(ruta: str | None, cycle_id: str | None) -> bool:
    if ruta is not None:
        with open(ruta, encoding="utf-8") as f:
            datos = json.load(f)
        plataforma = datos["resumen"]["plataforma"]
        filas = list(zip(datos["entradas"], datos["evaluaciones"], strict=True))
        if not filas:
            print(f"replay {plataforma}: sin evaluaciones")
            return False
        n = diferencias(filas)
        print(f"replay {plataforma}: diferencias: {n}")
        return n == 0
    with _conexion() as conn, conn.transaction():
        conn.execute("SET TRANSACTION READ ONLY")
        if cycle_id in (None, "ultimo"):
            resultados = [
                _replay_ciclo(conn, conn.execute(_SQL_ULTIMO_CICLO, (plat,)).fetchone(), plat)
                for plat in p.PLATAFORMAS
            ]
            return all(resultados)
        ciclo = conn.execute(_SQL_CICLO, (int(cycle_id),)).fetchone()
        return _replay_ciclo(conn, ciclo, f"ciclo {cycle_id}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.auto03_verifica", description=__doc__)
    sub = parser.add_subparsers(dest="comando", required=True)
    p_evalua = sub.add_parser("evalua", help="evalua los productos de hoy en solo lectura")
    p_evalua.add_argument("--platform", required=True, choices=p.PLATAFORMAS)
    p_evalua.add_argument("--json", help="guarda {resumen, entradas, evaluaciones} en esta ruta")
    p_replay = sub.add_parser("replay", help="vuelve a correr el resolutor sobre lo congelado")
    origen = p_replay.add_mutually_exclusive_group()
    origen.add_argument("--json", help="salida de evalua --json")
    origen.add_argument("--cycle-id", help="id de optimizer_cycle o 'ultimo' (por omision)")
    args = parser.parse_args(argv)
    if args.comando == "evalua":
        datos = evalua(args.platform)
        print(json.dumps(datos["resumen"], ensure_ascii=False, sort_keys=True))
        if args.json:
            with open(args.json, "w", encoding="utf-8") as f:
                json.dump(datos, f, ensure_ascii=False)
        return 0
    return 0 if replay(args.json, args.cycle_id) else 1


if __name__ == "__main__":
    sys.exit(main())
