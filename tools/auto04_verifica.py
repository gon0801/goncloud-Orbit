"""Verificador de replay determinista de ubicaciones (AUTO-04, spec seccion 9).

Vuelve a correr `decide_ubicacion` sobre cada `CasoCampana` congelado en
`ubicacion_evaluacion` y lo compara con el veredicto guardado. Las filas que
TX3 sustituyo por `producto_sin_evidencia` no se reproducen desde el caso y se
cuentan aparte. Solo lectura con `ORBIT_DSN_READ`.

    python -m tools.auto04_verifica replay [--platform amazon_mx] [--desde 2026-11-27]
    python -m tools.auto04_verifica resumen --platform amazon_mx [--dias 14]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from dataclasses import dataclass

from app.db import connect
from app.optimizer.ubicacion import (
    MOTIVO_PRODUCTO_SIN_EVIDENCIA,
    POLITICA,
    MoverUbicacion,
    caso_desde_json,
    decide_ubicacion,
)

PLATAFORMAS = ("amazon_mx", "amazon_us")

_SQL_EVALUACIONES = """
SELECT id, entrada, veredicto, motivo, ubicacion, de_pct, a_pct
  FROM ubicacion_evaluacion
 WHERE politica = %(politica)s
   AND (%(platform)s::platform IS NULL OR platform = %(platform)s::platform)
   AND (%(desde)s::date IS NULL OR (decided_at AT TIME ZONE 'UTC')::date >= %(desde)s::date)
 ORDER BY id
"""

_SQL_POR_MODO = """
SELECT modo, count(*),
       count(DISTINCT (decided_at AT TIME ZONE 'UTC')::date) FILTER (WHERE modo = 'shadow')
  FROM ubicacion_evaluacion
 WHERE platform = %s::platform
   AND (decided_at AT TIME ZONE 'UTC')::date BETWEEN %s::date AND %s::date
 GROUP BY modo
"""

_SQL_MOVER_POR_MOTIVO = """
SELECT motivo, count(*)
  FROM ubicacion_evaluacion
 WHERE platform = %s::platform AND veredicto = 'mover'
   AND (decided_at AT TIME ZONE 'UTC')::date BETWEEN %s::date AND %s::date
 GROUP BY motivo
"""


@dataclass(frozen=True)
class Replay:
    revisadas: int
    sustituidas: int
    discrepancias: tuple[int, ...]

    @property
    def comparadas(self) -> int:
        return self.revisadas - self.sustituidas

    @property
    def ok(self) -> bool:
        return self.comparadas > 0 and not self.discrepancias


def _repetido(entrada: dict) -> tuple:
    veredicto = decide_ubicacion(caso_desde_json(entrada))
    if isinstance(veredicto, MoverUbicacion):
        return ("mover", veredicto.motivo, veredicto.ubicacion, veredicto.de_pct, veredicto.a_pct)
    return ("mantener", veredicto.motivo, None, None, None)


def verifica_replay(conn, *, platform: str | None = None, desde: dt.date | None = None) -> Replay:
    revisadas = sustituidas = 0
    discrepancias: list[int] = []
    filas = conn.execute(
        _SQL_EVALUACIONES, {"politica": POLITICA, "platform": platform, "desde": desde}
    ).fetchall()
    for evaluacion_id, entrada, *guardado in filas:
        revisadas += 1
        if guardado[0] == "mantener" and guardado[1] == MOTIVO_PRODUCTO_SIN_EVIDENCIA:
            sustituidas += 1
        elif _repetido(entrada) != tuple(guardado):
            discrepancias.append(evaluacion_id)
    return Replay(revisadas, sustituidas, tuple(discrepancias))


def resumen_practica(conn, platform: str, *, hoy: dt.date, dias: int = 14) -> dict:
    """Evaluaciones por modo, dias con practica y movimientos por motivo en la
    ventana; la compuerta a `live` pide 14 dias de practica (spec seccion 9)."""
    desde = hoy - dt.timedelta(days=dias - 1)
    por_modo = conn.execute(_SQL_POR_MODO, (platform, desde, hoy)).fetchall()
    return {
        "platform": platform,
        "desde": desde.isoformat(),
        "evaluaciones": {modo: total for modo, total, _dias in por_modo},
        "dias_en_practica": sum(dias_shadow or 0 for _modo, _total, dias_shadow in por_modo),
        "mover_por_motivo": dict(
            conn.execute(_SQL_MOVER_POR_MOTIVO, (platform, desde, hoy)).fetchall()
        ),
    }


def _conexion():
    dsn = os.environ.get("ORBIT_DSN_READ")
    if not dsn:
        raise SystemExit("ORBIT_DSN_READ no esta definido")
    conn = connect(dsn, autocommit=True)
    conn.execute("SET TIME ZONE 'UTC'")
    return conn


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.auto04_verifica", description=__doc__)
    sub = parser.add_subparsers(dest="comando", required=True)
    p_replay = sub.add_parser("replay", help="repite la politica sobre los casos congelados")
    p_replay.add_argument("--platform", choices=PLATAFORMAS)
    p_replay.add_argument("--desde", type=dt.date.fromisoformat)
    p_resumen = sub.add_parser("resumen", help="resume la practica de la ventana")
    p_resumen.add_argument("--platform", required=True, choices=PLATAFORMAS)
    p_resumen.add_argument("--dias", type=int, default=14)
    args = parser.parse_args(argv)
    with _conexion() as conn:
        if args.comando == "resumen":
            hoy = dt.datetime.now(dt.UTC).date()
            datos = resumen_practica(conn, args.platform, hoy=hoy, dias=args.dias)
            print(json.dumps(datos, ensure_ascii=False, sort_keys=True))
            return 0
        replay = verifica_replay(conn, platform=args.platform, desde=args.desde)
    print(
        json.dumps(
            {
                "comparadas": replay.comparadas,
                "revisadas": replay.revisadas,
                "sustituidas": replay.sustituidas,
                "discrepancias": list(replay.discrepancias),
            },
            sort_keys=True,
        )
    )
    return 0 if replay.ok else 1


if __name__ == "__main__":
    sys.exit(main())
