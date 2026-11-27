"""Replay y verificacion READ-ONLY de la pausa economica v2 (P03).

Uso (solo lectura, nunca escribe):

    python tools/pausa_economica_verifica.py --platform amazon_us
    python tools/pausa_economica_verifica.py --platform amazon_mx --madurez

Re-ejecuta la MISMA funcion pura del motor
(`app.optimizer.pausa_economica.evalua_pausa_economica`) sobre la `entrada`
JSONB congelada de cada fila de `pausa_economica_evaluacion` y compara el
veredicto y el motivo. Es la compuerta 3 de los 5 ciclos en `shadow`: el replay
tiene que ser 100% determinista y cubrir TODAS las filas del mercado (recorrido
paginado por `id`, sin tope implicito).

`--limit N` existe solo para inspeccion: la salida se marca `[PARCIAL]` y el
comando sale 2 (no apta para la compuerta de promocion).

Con `--madurez` agrega el desenlace real `+10d` (regla 6) sobre
`v_metric_latest`: un candidato sigue PENDIENTE hasta que la ultima fecha del
seguimiento tiene `DIAS_MADUREZ_CORTES` dias de madurez; solo entonces se
calcula y juzga el ACoS. No escribe nada y no toca Amazon.

Codigos de salida: 0 replay completo sin divergencias; 1 divergencias o error
de base; 2 replay parcial por `--limit`.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from decimal import Decimal

import psycopg

from app.optimizer import pausa_economica as regla
from app.optimizer.windows import DIAS_MADUREZ_CORTES
from app.redaction import scrub

# Tamano de pagina del keyset: el replay completo nunca se corta por lote.
TAM_LOTE = 500

_SQL_EVALUACIONES = """
SELECT id, cycle_id, ad_entity_id, modo, veredicto, motivo, moneda,
       ventana_reciente_desde, ventana_reciente_hasta, target_acos_pct, entrada
  FROM pausa_economica_evaluacion
 WHERE platform = %s::platform AND id > %s
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


_SQL_PAUSAR = """
SELECT id, cycle_id, ad_entity_id, ventana_reciente_hasta, target_acos_pct
  FROM pausa_economica_evaluacion
 WHERE platform = %s::platform AND id > %s AND veredicto = 'pausar'
 ORDER BY id
 LIMIT %s
"""

_SQL_HAY_MAS = """
SELECT EXISTS (
    SELECT 1 FROM pausa_economica_evaluacion
     WHERE platform = %s::platform AND id > %s
)
"""


def _dsn() -> str:
    for clave in ("ORBIT_DSN_READ", "ORBIT_TEST_DSN"):
        valor = os.environ.get(clave)
        if valor:
            return valor
    raise SystemExit("define ORBIT_DSN_READ (solo lectura) o ORBIT_TEST_DSN")


def _todas(
    conn: psycopg.Connection, sql: str, platform: str, limite: int | None
) -> tuple[list, bool]:
    """TODAS las filas del alcance, por keyset. `limite` solo recorta para
    inspeccion y devuelve `parcial=True` si quedaban filas."""
    filas: list = []
    ultimo = 0
    while True:
        tam = TAM_LOTE if limite is None else min(TAM_LOTE, limite - len(filas))
        if tam <= 0:
            break
        lote = conn.execute(sql, (platform, ultimo, tam)).fetchall()
        if not lote:
            break
        filas.extend(lote)
        ultimo = lote[-1][0]
        if limite is not None and len(filas) >= limite:
            break
    parcial = False
    if limite is not None:
        parcial = bool(conn.execute(_SQL_HAY_MAS, (platform, ultimo)).fetchone()[0])
    return filas, parcial


def _replay(conn: psycopg.Connection, platform: str, limite: int | None) -> tuple[int, bool]:
    filas, parcial = _todas(conn, _SQL_EVALUACIONES, platform, limite)
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
    marca = " [PARCIAL]" if parcial else ""
    print(f"replay{marca}: {len(filas)} filas, {malas} divergencias")
    return malas, parcial


def package(exc: Exception) -> str:
    return scrub(str(exc)) or type(exc).__name__


def _madurez_disponible(hasta: dt.date, hoy: dt.date) -> bool:
    """La ultima fecha del seguimiento (+10d) ya cumplio la madurez de la
    regla 6 (`fin <= hoy - DIAS_MADUREZ_CORTES`). Contar filas no basta."""
    fin = hasta + dt.timedelta(days=10)
    return fin <= hoy - dt.timedelta(days=DIAS_MADUREZ_CORTES)


def _madurez(conn: psycopg.Connection, platform: str, limite: int | None, *, hoy: dt.date) -> int:
    """Desenlace +10d de cada candidato `pausar`, solo si la ventana maduro."""
    filas, _parcial = _todas(conn, _SQL_PAUSAR, platform, limite)
    pendientes = 0
    for _id, cycle_id, entidad, hasta, target in filas:
        if not _madurez_disponible(hasta, hoy):
            pendientes += 1
            fin = hasta + dt.timedelta(days=10)
            madura_el = fin + dt.timedelta(days=DIAS_MADUREZ_CORTES)
            print(f"madurez {cycle_id}/{entidad}: pendiente (madura el {madura_el})")
            continue
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
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="inspeccion parcial (marca [PARCIAL] y sale 2); por defecto TODAS",
    )
    parser.add_argument("--madurez", action="store_true", help="desenlace real +10d")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit <= 0:
        print("--limit debe ser > 0", file=sys.stderr)
        return 2
    hoy = dt.datetime.now(dt.UTC).date()
    try:
        conn = psycopg.connect(_dsn(), connect_timeout=10)
        try:
            malas, parcial = _replay(conn, args.platform, args.limit)
            if args.madurez:
                _madurez(conn, args.platform, args.limit, hoy=hoy)
        finally:
            conn.close()
    except psycopg.Error as exc:
        print(f"error de base: {package(exc)}", file=sys.stderr)
        return 1
    if malas:
        return 1
    if parcial:
        print(
            "replay PARCIAL por --limit: NO sirve para la compuerta de promocion",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
