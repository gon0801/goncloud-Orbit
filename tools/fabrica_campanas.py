#!/usr/bin/env python3
"""CLI de la fabrica de campanas Amazon SP por grupo (FABRICA 01).

Spec: docs/superpowers/specs/2026-09-05-fabrica-campanas-grupos-design.md.
Wrapper fino sobre `app.fabrica_datos` (planificacion sin HTTP) y
`app.fabrica_motor` (mutacion, registro, reversa y reconciliacion).

Conserva la corrida por stdin dentro del contenedor app:

    docker exec -i orbit-app-1 python - --plataforma amazon_mx \
      --tipo-producto collar_perro --nombre-base "Collar reflectante" \
      --listing-ids 12,15 --modo shadow \
      --budget-auto 150 --budget-phrase 120 --budget-product 120 \
      --budget-broad 120 --budget-exact 150 \
      --bid-auto 4.50 --bid-phrase 5.00 --bid-product 5.00 \
      --bid-broad 4.00 --bid-exact 6.00 < tools/fabrica_campanas.py
"""

from __future__ import annotations

import argparse
import logging
import sys

from app import fabrica_datos as fd
from app import fabrica_lotes as fl
from app import fabrica_motor as fm
from app import fabrica_plan as fp
from app.ads.campana_config import presupuesto_p90
from app.db import connect
from app.fabrica_datos import Abortar
from app.fabrica_registro import _dsn_read, _log
from app.redaction import install_scrub_filter

install_scrub_filter(logging.getLogger())


def _imprime_dry_run(plan: fp.PlanGrupoV2, huella: str) -> None:
    if fp.target_para_goals(plan) is None:
        print(fp.TEXTO_TARGET_MARGEN_PLATAFORMA, flush=True)
    # A2: lineas solo cuando hay familia (sin etiqueta la salida es
    # byte-identica a trunk: lane 1 de regresion).
    if plan.familia is not None:
        print(f"familia: {plan.familia} (tipo_producto tomado de la familia)", flush=True)
    if plan.advertencia_mezcla is not None:
        print(f"advertencia: {plan.advertencia_mezcla}", flush=True)
    for rol in fd._roles_del_plan(plan):
        parametro = plan.parametros[rol]
        semillas = sum(
            paso.recurso in ("keyword", "target", "negative_keyword")
            for paso in fp.pasos_del_rol(plan, rol)
        )
        print(
            f"{rol}: budget={parametro.budget} bid={parametro.bid} "
            f"target={plan.objetivo.acos_pct} origen={plan.objetivo.origen} "
            f"fuente_bid={parametro.fuente_bid} "
            f"procedencia={plan.objetivo.procedencia} semillas={semillas}",
            flush=True,
        )
    for publicacion in plan.publicaciones:
        margen = publicacion.margen_neto_pct
        print(
            f"publicacion={publicacion.listing_id} asin={publicacion.asin} "
            f"seller_sku={publicacion.seller_sku} margen={margen}",
            flush=True,
        )
    print(f"huella del conjunto: {huella}", flush=True)


def _crear(args) -> int:
    conn_read = connect(_dsn_read())
    try:  # D-GLM-6-3: la conexion de lectura se cierra en exito y en error
        plan = fd._arma_plan(args, conn_read)
        huella = fp.huella_plan_v2(plan)
        avisos = fd.avisos_presupuesto_alto(plan, presupuesto_p90(conn_read, plan.moneda))
        for aviso in avisos:
            print(aviso, flush=True)
        _imprime_dry_run(plan, huella)
    finally:
        conn_read.close()
    if not args.acepto_mutacion_real:
        _log(
            "dry_run",
            platform=plan.platform,
            tipo_producto=plan.tipo_producto,
            target=str(plan.objetivo.acos_pct),
            publicaciones=[p.listing_id for p in plan.publicaciones],
            existentes=len(plan.existentes),
            huella=huella,
            nota="sin --acepto-mutacion-real no se toca Amazon",
        )
        return 0
    if avisos and not args.acepto_presupuesto_alto:
        raise Abortar(
            "\n".join([*avisos, "Repite con --acepto-presupuesto-alto para crearlas así"])
        )
    return fm._mutar(args, plan, huella)


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plataforma", choices=tuple(fp.MONEDA_POR_PLATAFORMA), default=None)
    ap.add_argument("--tipo-producto", default=None)
    ap.add_argument("--nombre-base", default=None)
    ap.add_argument("--listing-ids", default=None, help="ids de listing v2 separados por coma")
    ap.add_argument(
        "--target-acos",
        default=None,
        help=(
            "objetivo manual v2 explicito (vive dentro de la banda del margen);"
            " sin el, el objetivo se deriva del margen medido y los goals nacen"
            " NULL (margen de la plataforma)"
        ),
    )
    ap.add_argument(
        "--modo",
        choices=fp.MODOS_GOAL,
        default=None,
        help="modo de los 5 goals (decision 13: explicito, sin default)",
    )
    for s in ("auto", "phrase", "product", "broad", "exact"):
        ap.add_argument(f"--budget-{s}", default=None)
        ap.add_argument(f"--bid-{s}", default=None)
    ap.add_argument("--acepto-mutacion-real", action="store_true")
    ap.add_argument(
        "--acepto-presupuesto-alto",
        action="store_true",
        help="confirma un budget de mas del doble de la referencia (W-N)",
    )
    ap.add_argument("--esperado", type=int, default=None, help="campanas autorizadas (5)")
    ap.add_argument("--huella", default=None, help="huella del dry-run")
    ap.add_argument("--go", default=None, help="literal del dueno (va al ledger)")
    ap.add_argument(
        "--registrar",
        default=None,
        help="lote failed cuyas campanas ya existen: reintenta solo sync + registro",
    )
    ap.add_argument("--desarmar", default=None, help="lote a pausar (reversa)")
    ap.add_argument("--reconciliar", action="store_true")
    ap.add_argument("--lote", default=None)
    return ap


def _valida_args_creacion(args) -> None:
    # A2: --tipo-producto es opcional (con familia unica sale del slug);
    # _tipo_desde_familia lo exige cuando no hay familia unica.
    faltan = [
        nombre
        for nombre in ("plataforma", "nombre_base", "listing_ids", "modo")
        if getattr(args, nombre) is None
    ]
    faltan += [
        f"{k}_{s}"
        for k in ("budget", "bid")
        for s in ("auto", "phrase", "product", "broad", "exact")
        if getattr(args, f"{k}_{s}") is None
    ]
    if faltan:
        raise SystemExit(f"faltan argumentos obligatorios: {', '.join(faltan)}")


def main() -> int:
    args = _parser().parse_args()
    if args.reconciliar:
        return fl._reconciliar_cmd(args)
    if args.registrar is not None:
        return fl._registrar_cmd(args)
    if args.desarmar is not None:
        return fl._desarmar(args)
    _valida_args_creacion(args)
    return _crear(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Abortar as exc:
        _log("ABORTAR_FAIL_CLOSED", motivo=str(exc))
        sys.exit(2)
