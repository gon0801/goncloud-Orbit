"""CLI de lote acotado del asesor Jev (JEV ADS 01, 1.4).

Solicita o retoma un lote de revisiones contra el censo de un grupo Amazon,
con presupuesto de llamadas y solicitud idempotente. Fuera del ciclo de
Ads: NINGUNA operacion automatica (cycle/apply) llama a este comando ni al
asesor.

- Dry-run por omision: arma el sujeto, resuelve el censo y las fichas
  vigentes e imprime el plan SIN escribir ni llamar a TypeSafe.
- `--aplicar` ejecuta: revision + intenciones confirmadas antes del HTTP,
  resultados despues, reutilizacion de exitos de la misma revision y
  presupuesto acotado; retomar con la MISMA `--solicitud` termina el lote
  sin pagar dos veces un par.
- `--decision-id` se RECHAZA por ahora (exit 2): atar la revision a una
  decision exige leer termino y censo de la decision guardada, que llega
  con la fila 2.1. Sin esa bandera el sujeto es semillas de un lote de
  grupo (plan canonico = el lote mismo, hash por contenido).
- La clave de TypeSafe sale de `<ORBIT_SECRETS_DIR>/typesafe.json`; sin
  clave el lote corre apagado: estados visibles, cero HTTP.
- DSN: `ORBIT_DSN_ADMIN` (operacion humana; app_jev escribe revisiones y
  app_admin gestiona fichas, el CLI no toca cola/ledger/bibliotecas).

Ejemplos:

  tools/jev_ads.py evaluar --plataforma amazon_mx --grupo-id 5 \\
      --termino 'soporte mesa' --solicitud 0b5e...-uuid --presupuesto 8
  tools/jev_ads.py evaluar ... --aplicar
  tools/jev_ads.py evaluar ... --aplicar   # retoma: reutiliza exitos
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from uuid import UUID

from app.db import connect
from app.jev_ads import AsesorAds, SemillasARevisar
from app.jev_catalogo import censo_grupo, ficha_vigente
from app.jev_juicios import leer_api_key
from app.redaction import scrub


def _plan_canonico(args) -> dict:
    return {
        "grupo_id": args.grupo_id,
        "plataforma": args.plataforma,
        "terminos": list(args.termino),
    }


def _imprimir_resultado(imprimir, termino: str, resultado) -> None:
    if hasattr(resultado, "producto_ids"):
        imprimir(
            f"resultado {termino} HayCompatible productos={list(resultado.producto_ids)}"
            f" cobertura={resultado.miembros_con_juicio}/{resultado.miembros_totales}"
        )
    elif hasattr(resultado, "miembros_totales"):
        imprimir(f"resultado {termino} NingunoCompatible universo={resultado.miembros_totales}")
    else:
        imprimir(f"resultado {termino} Indeterminado motivos={sorted(resultado.motivos)}")


def main(
    argv: list[str] | None = None,
    *,
    pedir=None,
    transporte=None,
    dsn: str | None = None,
    imprimir=print,
) -> int:
    parser = argparse.ArgumentParser(prog="jev_ads", description=__doc__)
    sub = parser.add_subparsers(dest="comando", required=True)
    evaluar = sub.add_parser(
        "evaluar", help="solicita o retoma un lote de revision (dry-run sin --aplicar)"
    )
    evaluar.add_argument("--plataforma", required=True, choices=("amazon_mx", "amazon_us"))
    evaluar.add_argument("--grupo-id", type=int, required=True)
    evaluar.add_argument(
        "--termino",
        action="append",
        required=True,
        help="termino literal a cotejar; repetible",
    )
    evaluar.add_argument("--solicitud", required=True, type=UUID)
    evaluar.add_argument("--presupuesto", type=int, default=10)
    evaluar.add_argument(
        "--decision-id",
        type=int,
        default=None,
        help="RECHAZADO por ahora (exit 2): atar la revision a una decision"
        " llega con la fila 2.1; sin esta bandera, semillas",
    )
    evaluar.add_argument("--aplicar", action="store_true", help="sin esta bandera es dry-run")
    args = parser.parse_args(argv)
    if args.decision_id is not None:
        imprimir(
            "configuracion rechazada: --decision-id todavia no esta soportado"
            " (la revision atada a decision llega con la fila 2.1); quita la"
            " bandera para un lote de semillas de grupo"
        )
        return 2

    dsn = dsn or os.environ.get("ORBIT_DSN_ADMIN")
    if not dsn:
        imprimir("falta ORBIT_DSN_ADMIN", file=sys.stderr)
        return 2
    api_key = leer_api_key()
    with connect(dsn) as conn:
        censo = censo_grupo(conn, plataforma=args.plataforma, ad_group_id=args.grupo_id)
        if not args.aplicar:
            ahora = datetime.now(UTC)
            con_ficha = 0
            for miembro in censo.miembros:
                if miembro.producto_id is None or len(miembro.listing_ids) != 1:
                    continue
                if (
                    ficha_vigente(
                        conn,
                        producto_id=miembro.producto_id,
                        plataforma=args.plataforma,
                        listing_id=next(iter(miembro.listing_ids)),
                        ahora=ahora,
                    )
                    is not None
                ):
                    con_ficha += 1
            imprimir(
                f"lote: {len(args.termino)} termino(s) contra {len(censo.miembros)}"
                f" miembro(s); fichas vigentes {con_ficha}; presupuesto"
                f" {args.presupuesto}; exhaustivo={censo.exhaustivo}"
            )
            imprimir("seco: nada escrito ni llamado; agrega --aplicar")
            return 0
        canon = _canonico(_plan_canonico(args))
        sujeto = SemillasARevisar(
            plan_sha256=hashlib.sha256(canon.encode("utf-8")).hexdigest(),
            plan_canonico=json.loads(canon),
            fuentes_semillas={"grupo_id": args.grupo_id, "plataforma": args.plataforma},
            terminos=tuple(args.termino),
            censo=censo,
            plataforma=args.plataforma,
        )
        asesor = AsesorAds(
            conn,
            pedir=pedir,
            transporte=transporte,
            api_key=api_key,
            presupuesto=args.presupuesto,
        )
        revision = asesor.evaluar(sujeto, solicitud_id=args.solicitud)
        for termino, resultado in revision.resultados:
            _imprimir_resultado(imprimir, termino, resultado)
        if revision.presupuesto_agotado:
            imprimir("presupuesto_agotado: retoma con la misma --solicitud")
            return 1
        imprimir("lote completo")
        return 0


def _canonico(objeto: object) -> str:
    return json.dumps(objeto, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


__all__ = ["main", "scrub"]
