"""CLI de lote acotado del asesor Jev (JEV ADS 01, 1.4 y R2).

Solicita o retoma una revision con presupuesto de llamadas y solicitud
idempotente. Fuera del ciclo de Ads: NINGUNA operacion automatica
(cycle/apply) llama a este comando ni al asesor.

Tres sujetos posibles:

- `evaluar --decision-id N`: la decision guardada (negative o harvest). El
  termino, el grupo de origen y, en harvest, el destino congelado salen de
  la propia decision; la asesoria aparece en /cortes.
- `evaluar --plataforma P --grupo-id G --termino T [--termino T2 ...]`: lote
  de semillas de un grupo (plan canonico = el lote, hash por contenido).
- `evaluar-plan --plan solicitud.json`: la MISMA solicitud del preview de
  fabrica (el cuerpo de POST /api/fabrica/export-semillas). El CLI calcula el
  export sobre la base, igual que el endpoint: huella, listings y terminos
  salen del plan, no de un archivo que se pueda editar. El universo son los
  productos del NUEVO plan y la asesoria aparece en
  /api/fabrica/asesoria/<huella>.

Ceremonia comun:

- Dry-run por omision: imprime lo que `--aplicar` haria con la MISMA regla
  (censo y fichas, miembros fuera, exitos reutilizables de la solicitud y
  pares que pagaria con el presupuesto) SIN escribir ni llamar a TypeSafe.
- `--aplicar`: revision + intenciones confirmadas antes del HTTP, resultados
  despues; retomar con la MISMA `--solicitud` termina el lote sin pagar dos
  veces un par.
- La clave de TypeSafe sale de `<ORBIT_SECRETS_DIR>/typesafe.json`; sin clave
  el lote corre apagado: estados visibles, cero HTTP.
- DSN: `ORBIT_DSN_ADMIN` (operacion humana). Ese login necesita ademas el
  grupo `app_jev` para escribir revisiones (docs/DEPLOY.md:
  `GRANT app_jev TO orbit_admin`).

Salidas: 0 completo o seco; 1 error (mensaje redactado); 2 configuracion;
3 presupuesto agotado (retomar con la misma --solicitud).

Ejemplos:

  tools/jev_ads.py evaluar --decision-id 2367 --solicitud <uuid>
  tools/jev_ads.py evaluar --decision-id 2367 --solicitud <uuid> --aplicar
  tools/jev_ads.py evaluar --plataforma amazon_mx --grupo-id 5 \\
      --termino 'soporte mesa' --solicitud <uuid> --presupuesto 8 --aplicar
  tools/jev_ads.py evaluar-plan --plan solicitud.json --solicitud <uuid> --aplicar
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from uuid import UUID

import psycopg

from app.db import OrbitDbError, connect
from app.jev_ads import SemillasARevisar
from app.jev_asesor import AsesorAds
from app.jev_catalogo import censo_de_plan, censo_grupo, decision_a_revisar
from app.jev_juicios import leer_api_key
from app.redaction import scrub

SALIDA_ERROR = 1
SALIDA_CONFIGURACION = 2
SALIDA_PRESUPUESTO_AGOTADO = 3


class _Configuracion(Exception):
    """Argumentos o solicitud de plan invalidos: exit 2 con el motivo."""


def _presupuesto(valor: str) -> int:
    numero = int(valor)
    if numero < 1:
        raise argparse.ArgumentTypeError("el presupuesto debe ser al menos 1")
    return numero


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jev_ads", description=__doc__)
    sub = parser.add_subparsers(dest="comando", required=True)
    evaluar = sub.add_parser("evaluar", help="decision guardada o lote de un grupo")
    evaluar.add_argument("--decision-id", type=int, default=None)
    evaluar.add_argument("--plataforma", choices=("amazon_mx", "amazon_us"))
    evaluar.add_argument("--grupo-id", type=int)
    evaluar.add_argument("--termino", action="append", help="termino literal; repetible")
    plan = sub.add_parser("evaluar-plan", help="solicitud de un preview de fabrica")
    plan.add_argument("--plan", required=True)
    for comando in (evaluar, plan):
        comando.add_argument("--solicitud", required=True, type=UUID)
        comando.add_argument("--presupuesto", type=_presupuesto, default=10)
        comando.add_argument("--aplicar", action="store_true", help="sin esta bandera es dry-run")
    return parser


def _terminos(crudos) -> tuple[str, ...]:
    terminos = tuple(str(t).strip() for t in crudos or ())
    if not terminos:
        raise _Configuracion("no hay terminos que cotejar")
    if any(not t for t in terminos):
        raise _Configuracion("cada termino debe traer texto")
    if len(set(terminos)) != len(terminos):
        raise _Configuracion("termino repetido")
    return terminos


def _canonico(objeto: object) -> str:
    return json.dumps(objeto, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sujeto_de_plan(conn, ruta: str):
    """El export se CALCULA aqui con la solicitud del preview, por el mismo
    camino que POST /api/fabrica/export-semillas (validacion de SolicitudPlan
    y exportar_semillas): huella, listings y terminos son del plan."""
    from fastapi import HTTPException
    from pydantic import ValidationError

    from app import fabrica_web
    from app.api_fabrica import SolicitudPlan

    try:
        with open(ruta, encoding="utf-8") as archivo:
            solicitud = SolicitudPlan.model_validate(json.load(archivo)).model_dump()
    except (OSError, ValueError, ValidationError) as error:
        raise _Configuracion(f"solicitud de plan ilegible: {error}") from error
    try:
        export = fabrica_web.exportar_semillas(conn, solicitud)
    except HTTPException as error:
        motivo = f"plan rechazado ({error.status_code}): {error.detail}"
        if error.status_code < 500:  # la solicitud no arma un plan: configuracion
            raise _Configuracion(motivo) from error
        raise ValueError(motivo) from error
    sujeto = SemillasARevisar(
        plan_sha256=export["plan_sha256"],
        plan_canonico=export["plan_canonico"],
        fuentes_semillas=export["fuentes_semillas"],
        terminos=_terminos(export["terminos_a_cotejar"]),
        censo=censo_de_plan(conn, plataforma=export["plataforma"], listing_ids=export["listings"]),
        plataforma=export["plataforma"],
    )
    return sujeto, export["roles_terminos"]


def _sujeto(conn, args):
    """El sujeto que se revisa y el rol de cada termino de fabrica (se
    imprime junto a su resultado)."""
    if args.comando == "evaluar-plan":
        return _sujeto_de_plan(conn, args.plan)
    if args.decision_id is not None:
        if args.plataforma or args.grupo_id is not None or args.termino:
            raise _Configuracion(
                "--decision-id no se combina con --plataforma/--grupo-id/--termino"
            )
        return decision_a_revisar(conn, args.decision_id), {}
    if not args.plataforma or args.grupo_id is None:
        raise _Configuracion("sin --decision-id hacen falta --plataforma, --grupo-id y --termino")
    terminos = _terminos(args.termino)
    canon = _canonico(
        {"grupo_id": args.grupo_id, "plataforma": args.plataforma, "terminos": list(terminos)}
    )
    sujeto = SemillasARevisar(
        plan_sha256=hashlib.sha256(canon.encode("utf-8")).hexdigest(),
        plan_canonico=json.loads(canon),
        fuentes_semillas={"grupo_id": args.grupo_id, "plataforma": args.plataforma},
        terminos=terminos,
        censo=censo_grupo(conn, plataforma=args.plataforma, ad_group_id=args.grupo_id),
        plataforma=args.plataforma,
    )
    return sujeto, {}


def _imprimir_resultado(imprimir, ambito: str, termino: str, rol: str, resultado) -> None:
    etiqueta = f"{ambito} {termino}" + (f" ({rol})" if rol else "")
    if hasattr(resultado, "producto_ids"):
        imprimir(
            f"resultado {etiqueta} HayCompatible productos={list(resultado.producto_ids)}"
            f" cobertura={resultado.miembros_con_juicio}/{resultado.miembros_totales}"
        )
    elif hasattr(resultado, "miembros_totales"):
        imprimir(f"resultado {etiqueta} NingunoCompatible universo={resultado.miembros_totales}")
    else:
        imprimir(f"resultado {etiqueta} Indeterminado motivos={sorted(resultado.motivos)}")


def _correr(conn, args, *, pedir, transporte, imprimir) -> int:
    sujeto, roles = _sujeto(conn, args)
    asesor = AsesorAds(
        conn,
        pedir=pedir,
        transporte=transporte,
        api_key=leer_api_key(),
        presupuesto=args.presupuesto,
    )
    if not args.aplicar:
        plan = asesor.plan_seco(sujeto, solicitud_id=args.solicitud)
        agotaria = "; se agotaria (retomar con la misma --solicitud)" if plan.agotaria else ""
        imprimir(
            f"seco: {plan.miembros} miembro(s); fichas vigentes {plan.fichas}; pares nuevos"
            f" {plan.pares_nuevos}, reutilizables {plan.pares_reutilizables}; pagaria"
            f" {plan.pagaria} de presupuesto {args.presupuesto}{agotaria}"
        )
        imprimir("seco: nada escrito ni llamado; agrega --aplicar")
        return 0
    revision = asesor.evaluar(sujeto, solicitud_id=args.solicitud)
    ambito = "origen" if revision.destinos else "grupo"
    for termino, resultado in revision.resultados:
        _imprimir_resultado(imprimir, ambito, termino, ",".join(roles.get(termino, [])), resultado)
    for termino, resultado in revision.destinos:
        _imprimir_resultado(imprimir, "destino", termino, "", resultado)
    if revision.presupuesto_agotado:
        imprimir("presupuesto_agotado: retoma con la misma --solicitud")
        return SALIDA_PRESUPUESTO_AGOTADO
    imprimir("lote completo")
    return 0


def main(
    argv: list[str] | None = None,
    *,
    pedir=None,
    transporte=None,
    dsn: str | None = None,
    imprimir=print,
) -> int:
    args = _parser().parse_args(argv)
    dsn = dsn or os.environ.get("ORBIT_DSN_ADMIN")
    if not dsn:
        print("falta ORBIT_DSN_ADMIN", file=sys.stderr)
        return SALIDA_CONFIGURACION
    try:
        with connect(dsn) as conn:
            return _correr(conn, args, pedir=pedir, transporte=transporte, imprimir=imprimir)
    except _Configuracion as error:
        print(f"configuracion rechazada: {scrub(str(error))}", file=sys.stderr)
        return SALIDA_CONFIGURACION
    except (ValueError, psycopg.Error, OrbitDbError) as error:
        print(scrub(str(error)), file=sys.stderr)
        return SALIDA_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
