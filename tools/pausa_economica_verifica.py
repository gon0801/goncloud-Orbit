"""Replay y verificacion READ-ONLY de la pausa economica v2 (P03).

Uso (solo lectura, nunca escribe):

    python tools/pausa_economica_verifica.py --platform amazon_us
    python tools/pausa_economica_verifica.py --platform amazon_mx --madurez

Re-ejecuta la MISMA funcion pura del motor
(`app.optimizer.pausa_economica.evalua_pausa_economica`) sobre la `entrada`
JSONB congelada de cada fila de `pausa_economica_evaluacion` y compara el
veredicto y el motivo. Es la compuerta 3 de los 5 ciclos en `shadow`: el replay
tiene que ser 100% determinista.

Con `--madurez` agrega el desenlace real `+10d` (regla 6) sobre
`v_metric_latest`: cuantos dias maduros hay desde la ventana juzgada y el ACoS
resultante, para decidir la promocion a `live` mercado por mercado. No escribe
nada y no toca Amazon.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from decimal import Decimal

import psycopg

from app.optimizer import pausa_economica as regla
from app.redaction import scrub

_SQL_EVALUACIONES = """
SELECT id, cycle_id, ad_entity_id, modo, veredicto, motivo, moneda,
       ventana_reciente_desde, ventana_reciente_hasta, target_acos_pct, entrada
  FROM pausa_economica_evaluacion
 WHERE platform = %s::platform
 ORDER BY id
 LIMIT %s
"""

_SQL_MADUREZ = """
SELECT CASE WHEN bool_and(cost IS NOT NULL) THEN sum(cost) END,
       CASE WHEN bool_and(ad_revenue IS NOT NULL) THEN sum(ad_revenue) END,
       CASE WHEN bool_and(orders IS NOT NULL) THEN sum(orders) END,
       count(*)
  FROM v_metric_latest
 WHERE ad_entity_id = %s AND metric_date BETWEEN %s AND %s
"""


def _dsn() -> str:
    for clave in ("ORBIT_DSN_READ", "ORBIT_TEST_DSN"):
        valor = os.environ.get(clave)
        if valor:
            return valor
    raise SystemExit("define ORBIT_DSN_READ (solo lectura) o ORBIT_TEST_DSN")


def _replay(conn: psycopg.Connection, platform: str, limite: int) -> int:
    filas = conn.execute(_SQL_EVALUACIONES, (platform, limite)).fetchall()
    malas = 0
    for fila in filas:
        (
            _id,
            cycle_id,
            entidad,
            _modo,
            veredicto,
            motivo,
            _moneda,
            _desde,
            _hasta,
            _target,
            entrada,
        ) = fila
        try:
            calculado = regla.evalua_pausa_economica(regla.entrada_desde_json(entrada))
        except ValueError as exc:
            malas += 1
            print(f"FALLA fila {cycle_id}/{entidad}: entrada ilegible: {package(exc)}")
            continue
        if (calculado.veredicto, calculado.motivo) != (veredicto, motivo):
            malas += 1
            print(
                f"FALLA fila {cycle_id}/{entidad}: guardado {veredicto}/{motivo},"
                f" replay {calculado.veredicto}/{calculado.motivo}"
            )
    print(f"replay: {len(filas)} filas, {malas} divergencias")
    return malas


def package(exc: Exception) -> str:
    return scrub(str(exc)) or type(exc).__name__


def _madurez(conn: psycopg.Connection, platform: str, limite: int) -> int:
    """Desenlace +10d de cada candidato `pausar`: dias maduros y ACoS real."""
    filas = conn.execute(
        """
        SELECT cycle_id, ad_entity_id, ventana_reciente_hasta, target_acos_pct
          FROM pausa_economica_evaluacion
         WHERE platform = %s::platform AND veredicto = 'pausar'
         ORDER BY id
         LIMIT %s
        """,
        (platform, limite),
    ).fetchall()
    pendientes = 0
    for cycle_id, entidad, hasta, target in filas:
        desde = hasta + dt.timedelta(days=1)
        fin = hasta + dt.timedelta(days=10)
        cost, revenue, orders, dias = conn.execute(_SQL_MADUREZ, (entidad, desde, fin)).fetchone()
        if cost is None or revenue is None or dias < 10:
            pendientes += 1
            print(f"madurez {cycle_id}/{entidad}: pendiente ({dias}/10 dias)")
            continue
        acos = None if revenue == 0 else Decimal(cost) / Decimal(revenue) * Decimal(100)
        texto = "n/d" if acos is None else f"{acos:.2f}%"
        juicio = "sobre la meta" if acos is None or acos > Decimal(target) else "dentro"
        print(
            f"madurez {cycle_id}/{entidad}: ACoS +10d {texto} vs target {target}%"
            f" ({juicio}; {orders if orders is not None else 'n/d'} pedidos)"
        )
    print(f"madurez: {len(filas)} candidatos, {pendientes} pendientes")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay READ-ONLY de la pausa economica v2")
    parser.add_argument("--platform", required=True, choices=("amazon_mx", "amazon_us"))
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--madurez", action="store_true", help="desenlace real +10d")
    args = parser.parse_args(argv)
    try:
        conn = psycopg.connect(_dsn(), connect_timeout=10)
        try:
            malas = _replay(conn, args.platform, args.limit)
            if args.madurez:
                _madurez(conn, args.platform, args.limit)
        finally:
            conn.close()
    except psycopg.Error as exc:
        print(f"error de base: {package(exc)}", file=sys.stderr)
        return 1
    return 1 if malas else 0


if __name__ == "__main__":
    raise SystemExit(main())
