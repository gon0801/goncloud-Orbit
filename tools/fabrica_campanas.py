#!/usr/bin/env python3
"""Fabrica de campanas Amazon SP por grupo con estructura fija (FABRICA 01).

Spec: docs/superpowers/specs/2026-09-05-fabrica-campanas-grupos-design.md.
Operacion de NEGOCIO con go del dueno (no pasa por apply_queue ni por la
escalera): crea 5 campanas (exact, phrase, broad, product targeting, auto)
para un tipo_producto, con target = fraccion x margen MINIMO de los productos
(v_margen_producto), product ads por producto, semillas de la biblioteca y
del historial propio, ledger fabrica_lote/fabrica_lote_paso ANTES de cada
HTTP, readback por LIST, sync de estructura y goals por campana.

QUE HACE, EN ORDEN:

 1. Plan desde la BASE (ORBIT_DSN_READ): fraccion del setting vigente,
    productos con margen y seller_sku, semillas, campanas existentes de los
    mismos productos (SOLO se reportan, decision 9). Validacion SIN HTTP.
 2. Dry-run por defecto: lineas del plan + huella del conjunto; cero HTTP.
    ESTA ENTREGA (tarea 6) TERMINA AQUI: unicamente lectura de DB.
 3. Mutacion (--acepto-mutacion-real ...): pendiente de la tarea 7.
 4. Reversa (--desarmar) y --reconciliar: pendientes de la tarea 9.

CORRIDA (dentro del contenedor app, por stdin; la imagen solo trae app/):

    docker exec -i orbit-app-1 python - --plataforma amazon_mx \
      --tipo-producto collar_perro --nombre-base "Collar reflectante" \
      --productos 12,15 --modo shadow \
      --budget-auto 150 --budget-phrase 120 --budget-product 120 \
      --budget-broad 120 --budget-exact 150 \
      --bid-auto 4.50 --bid-phrase 5.00 --bid-product 5.00 \
      --bid-broad 4.00 --bid-exact 6.00 < tools/fabrica_campanas.py

La mutacion real tendra su propio sello v3 (patron archiva_inertes/
reactiva_campanas): NO importa app.ads.write (candado en
tests/test_architecture.py). Shapes de campanas/adGroups/targets y el camino
feliz de productAds son HIPOTESIS hasta la sonda (plans/fabrica-01.md
tarea 11).
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import os
import sys
from decimal import Decimal, InvalidOperation
from typing import Any

import psycopg

from app import fabrica_plan as fp
from app.db import connect
from app.optimizer.goals import fraccion_desde_settings
from app.redaction import install_scrub_filter, scrub

install_scrub_filter(logging.getLogger())

# --- SQL de plan (ORBIT_DSN_READ) ------------------------------------------
# Primer parametro CASTEADO (%s::platform): sin el cast Postgres no infiere el
# tipo (IndeterminateDatatype, leccion de archiva_inertes).
_SQL_SETTINGS = "SELECT id, settings FROM config_version ORDER BY id DESC LIMIT 1"

# Un producto con N listings en la plataforma produce N filas: _productos
# ABORTA nombrandolo (fail-loud, regla 3 — la fabrica no elige listing; sin
# el guard serian N product ads por campana y UniqueViolation en
# campana_grupo_producto DESPUES de los POST). Residual F1: --listing.
_SQL_PRODUCTOS = """
SELECT p.id, p.odoo_sku, l.id, l.external_id, l.seller_sku, m.margen_neto_pct
  FROM product p
  LEFT JOIN listing l ON l.product_id = p.id AND l.platform = %s::platform
  LEFT JOIN v_margen_producto m ON m.product_id = p.id AND m.platform = %s::platform
 WHERE p.id = ANY(%s)
 ORDER BY p.id
"""

# Terminos de las campanas del producto: campanas con un product_ad ligado
# al listing. GRANO DEL ORIGEN sellado por la evidencia de la tarea 1 (f)
# ("Decisiones y evidencia"): search_term_observation tiene UN solo grano en
# produccion, ad_group (34,045 filas en 105 dias; cero con kind = 'campaign').
# La CTE `origenes` es SOLO los ad groups de las campanas del producto — SIN
# UNION con la campana: un UNION duplicaria orders/cost si algun dia
# apareciera el grano campana. El test siembra una fila en ese grano y exige
# que NO cuente (si alguien regresa el UNION, truena).
#
# D-GLM-6-1: el dia de referencia es un PARAMETRO UTC calculado en Python
# (`%s::date`), NO CURRENT_DATE: la zona horaria de la sesion no puede mover
# las ventanas [D-105, D-15) / [D-39, D-9) en silencio.
# Revision #176: despues de colapsar, un NULL conserva desconocida SOLO
# su metrica (mismo patron que optimizer/windows.py). SUM solo ignoraria
# el dia incompleto e inventaria un total para decidir las semillas exact.
_SQL_TERMINOS = """
WITH ventana AS (
    SELECT %s::date - %s AS desde, %s::date - %s AS hasta
),
campanas AS (
    SELECT DISTINCT ag.parent_id AS campaign_id
      FROM ad_entity pa
      JOIN ad_entity ag ON ag.id = pa.parent_id
     WHERE pa.kind = 'product_ad' AND pa.platform = %s::platform
       AND pa.listing_id = ANY(%s)
),
origenes AS (
    SELECT ag.id FROM ad_entity ag JOIN campanas c ON c.campaign_id = ag.parent_id
     WHERE ag.kind = 'ad_group'
),
ultimas AS (
    SELECT DISTINCT ON (s.ad_entity_id, s.search_term, s.metric_date)
           s.search_term, s.is_asin_like, s.orders, s.cost, s.ad_revenue
      FROM search_term_observation s
      JOIN origenes o ON o.id = s.ad_entity_id
      CROSS JOIN ventana v
     WHERE s.platform = %s::platform
       AND s.metric_date >= v.desde AND s.metric_date < v.hasta
     ORDER BY s.ad_entity_id, s.search_term, s.metric_date, s.observed_at DESC,
              -- desempate bitemporal sellado (r3 codex 3): mismo observed_at
              -- en dos reportes = gana el reporte mas reciente (patron de
              -- app/optimizer/windows.py, colapso de observaciones)
              s.source_report_id DESC NULLS LAST
)
SELECT search_term, bool_or(is_asin_like),
       CASE WHEN bool_and(orders IS NOT NULL) THEN SUM(orders) END,
       CASE WHEN bool_and(cost IS NOT NULL) THEN SUM(cost) END,
       CASE WHEN bool_and(ad_revenue IS NOT NULL) THEN SUM(ad_revenue) END
  FROM ultimas
 GROUP BY search_term
HAVING COALESCE(SUM(orders), 0) >= 1
 ORDER BY SUM(orders) DESC, search_term
"""

# MISMO agregado pero sobre la ventana de CORTES del motor (VENTANA_CORTES_
# DIAS = [D-39, D-9); regla 6: madurez >= 10d, spec §6): alimenta SOLO los
# candidatos a semilla exact. El CTE ventana_cortes es el marcador que
# distingue esta consulta en los tests.
_SQL_TERMINOS_EXACT = _SQL_TERMINOS.replace("ventana", "ventana_cortes")

_SQL_BIBLIOTECA_KW = """
SELECT texto FROM keyword_biblioteca
 WHERE tipo_producto = %s AND platform = %s::platform AND orders >= 1
 ORDER BY orders DESC, texto
"""
_SQL_BIBLIOTECA_NEG = """
SELECT texto FROM negative_biblioteca
 WHERE tipo_producto = %s AND platform = %s::platform
 ORDER BY texto
"""

_SQL_EXISTENTES = """
SELECT DISTINCT c.external_id, c.name, s.status
  FROM ad_entity pa
  JOIN ad_entity ag ON ag.id = pa.parent_id
  JOIN ad_entity c ON c.id = ag.parent_id
  LEFT JOIN ad_entity_state s ON s.ad_entity_id = c.id
 WHERE pa.kind = 'product_ad' AND pa.platform = %s::platform
   AND pa.listing_id = ANY(%s)
 ORDER BY c.external_id
"""


class Abortar(RuntimeError):
    """Fail-closed: la realidad difiere del plan o la API rechaza."""


def _log(evento: str, **campos: Any) -> None:
    print(scrub(json.dumps({"evento": evento, **campos}, default=str)), flush=True)


def _dsn(nombre: str) -> str:
    dsn = os.environ.get(nombre)
    if not dsn:
        raise Abortar(f"{nombre} no esta en el entorno (corre dentro del contenedor app)")
    return dsn


def _dsn_read() -> str:
    return _dsn("ORBIT_DSN_READ")


# --- lecturas ----------------------------------------------------------------


def _fraccion(conn: psycopg.Connection, platform: str) -> Decimal:
    fila = conn.execute(_SQL_SETTINGS).fetchone()
    settings = fila[1] if fila else {}
    fraccion = fraccion_desde_settings(settings or {}, platform)  # invalida -> ValueError
    if fraccion is None:
        raise Abortar(
            f"sin fraccion: ads_target_fraccion_margen_{platform} ausente en la config vigente"
        )
    return fraccion


def _productos(conn: psycopg.Connection, platform: str, ids: list[int]) -> list[fp.ProductoGrupo]:
    filas = conn.execute(_SQL_PRODUCTOS, (platform, platform, ids)).fetchall()
    vistos = {f[0] for f in filas}
    faltan = sorted(set(ids) - vistos)
    if faltan:
        raise Abortar(f"producto(s) {faltan} no existe(n) en product")
    n_listings: dict[int, int] = {}
    for f in filas:
        n_listings[f[0]] = n_listings.get(f[0], 0) + 1
    out = []
    for pid, odoo, lid, asin, sku, margen in filas:
        if n_listings[pid] > 1:
            raise Abortar(
                f"producto {pid} ({odoo}) tiene {n_listings[pid]} listings en {platform}:"
                " la fabrica no elige (regla 3); multi-listing queda fuera de F1"
                " hasta --listing explicito (residual)"
            )
        if lid is None:
            raise Abortar(f"producto {pid} ({odoo}) sin listing en {platform}")
        if margen is None:
            raise Abortar(
                f"producto {pid} ({odoo}) sin margen medible en v_margen_producto (regla 3)"
            )
        if not sku or not str(sku).strip():
            raise Abortar(
                f"producto {pid} ({odoo}) sin seller_sku en {platform}: no hay product ad"
            )
        out.append(fp.ProductoGrupo(pid, odoo, lid, asin, str(sku).strip(), Decimal(margen)))
    return out


def _terminos_producto(
    conn: psycopg.Connection,
    platform: str,
    listing_ids: list[int],
    *,
    sql: str = _SQL_TERMINOS,
    ventana: tuple[int, int] = fp.VENTANA_DIAS,
) -> list[fp.TerminoProducto]:
    """Agregado bitemporal de search_term_observation en la ventana pedida.
    Default: ventana del margen [D-105, D-15); con sql=_SQL_TERMINOS_EXACT y
    ventana=fp.VENTANA_CORTES_DIAS: candidatos a exact (regla 6). El dia de
    referencia viaja como parametro UTC (D-GLM-6-1)."""
    hoy = datetime.datetime.now(datetime.UTC).date()
    desde, hasta = ventana
    filas = conn.execute(sql, (hoy, desde, hoy, hasta, platform, listing_ids, platform)).fetchall()
    return [
        fp.TerminoProducto(
            texto=f[0],
            is_asin_like=bool(f[1]),
            orders=int(f[2]) if f[2] is not None else None,
            cost=Decimal(f[3]) if f[3] is not None else None,
            revenue=Decimal(f[4]) if f[4] is not None else None,
        )
        for f in filas
    ]


def _biblioteca(conn: psycopg.Connection, tipo: str, platform: str) -> tuple[list[str], list[str]]:
    kws = [f[0] for f in conn.execute(_SQL_BIBLIOTECA_KW, (tipo, platform)).fetchall()]
    negs = [f[0] for f in conn.execute(_SQL_BIBLIOTECA_NEG, (tipo, platform)).fetchall()]
    return kws, negs


def _existentes(conn: psycopg.Connection, platform: str, listing_ids: list[int]) -> list[dict]:
    return [
        {"external_id": f[0], "name": f[1], "status": f[2]}
        for f in conn.execute(_SQL_EXISTENTES, (platform, listing_ids)).fetchall()
    ]


def _decimal(texto: str, nombre: str) -> Decimal:
    try:
        valor = Decimal(str(texto).strip())
    except InvalidOperation:
        raise Abortar(f"{nombre} no es un numero: {texto!r}") from None
    if not valor.is_finite():
        raise Abortar(f"{nombre} debe ser finito: {texto!r}")
    return valor


def _parametros(args) -> dict[str, fp.ParametrosRol]:
    sufijo = {
        "auto_discovery": "auto",
        "category_phrase": "phrase",
        "product_targeting": "product",
        "category_broad": "broad",
        "category_exact": "exact",
    }
    return {
        rol: fp.ParametrosRol(
            rol=rol,
            budget=_decimal(getattr(args, f"budget_{s}"), f"--budget-{s}"),
            bid=_decimal(getattr(args, f"bid_{s}"), f"--bid-{s}"),
        )
        for rol, s in sufijo.items()
    }


def _ids_productos(texto: str) -> list[int]:
    try:
        ids = sorted({int(p) for p in texto.split(",") if p.strip()})
    except ValueError:
        raise Abortar(f"--productos debe ser ids enteros separados por coma: {texto!r}") from None
    if not ids:
        raise Abortar("--productos no puede ser vacio")
    return ids


def _arma_plan(args, conn_read: psycopg.Connection) -> fp.PlanGrupo:
    """Plan completo desde la base + validacion SIN HTTP (spec §5.1)."""
    try:
        tipo = fp.valida_tipo_producto(args.tipo_producto)
        moneda = fp.MONEDA_POR_PLATAFORMA[args.plataforma]
        parametros = _parametros(args)
        fp.valida_parametros(parametros, moneda)
        ids = _ids_productos(args.productos)
        fraccion = _fraccion(conn_read, args.plataforma)
        productos = _productos(conn_read, args.plataforma, ids)
        target = fp.target_del_grupo([p.margen_neto_pct for p in productos], fraccion)
        listings = [p.listing_id for p in productos]
        terminos = _terminos_producto(conn_read, args.plataforma, listings)
        terminos_exact = _terminos_producto(
            conn_read,
            args.plataforma,
            listings,
            sql=_SQL_TERMINOS_EXACT,
            ventana=fp.VENTANA_CORTES_DIAS,
        )
        kws, negs = _biblioteca(conn_read, tipo, args.plataforma)
        semillas = fp.semillas_desde_terminos(
            terminos, kws, negs, target.aplicado, terminos_exact=terminos_exact
        )
        existentes = tuple(_existentes(conn_read, args.plataforma, listings))
    except fp.PlanInvalido as exc:
        raise Abortar(str(exc)) from exc
    finally:
        conn_read.commit()  # la lectura cierra su txn ANTES de cualquier salida
    if not args.nombre_base or not args.nombre_base.strip():
        raise Abortar("--nombre-base no puede ser vacio")
    return fp.PlanGrupo(
        platform=args.plataforma,
        tipo_producto=tipo,
        nombre_base=args.nombre_base.strip(),
        fecha=datetime.datetime.now(datetime.UTC).date(),
        moneda=moneda,
        modo=args.modo,
        productos=tuple(productos),
        parametros=parametros,
        target=target,
        semillas=semillas,
        existentes=existentes,
    )


def _lote_nuevo(plan: fp.PlanGrupo) -> str:
    marca = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d-%H%M%S")
    return f"fabrica-{plan.platform}-{plan.tipo_producto}-{marca}"


def _imprime_dry_run(plan: fp.PlanGrupo, huella: str) -> None:
    for linea in fp.lineas_dry_run(plan):
        print(linea, flush=True)
    print(f"huella del conjunto: {huella}", flush=True)


def _crear(args) -> int:
    conn_read = connect(_dsn_read())
    try:  # D-GLM-6-3: la conexion de lectura se cierra en exito y en error
        plan = _arma_plan(args, conn_read)
        huella = fp.huella_plan(plan)
        _imprime_dry_run(plan, huella)
    finally:
        conn_read.close()
    if not args.acepto_mutacion_real:
        _log(
            "dry_run",
            platform=plan.platform,
            tipo_producto=plan.tipo_producto,
            target=str(plan.target.aplicado),
            productos=[p.product_id for p in plan.productos],
            existentes=len(plan.existentes),
            huella=huella,
            nota="sin --acepto-mutacion-real no se toca Amazon",
        )
        return 0
    return _mutar(args, plan, huella)  # tarea 7


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plataforma", choices=tuple(fp.MONEDA_POR_PLATAFORMA), default=None)
    ap.add_argument("--tipo-producto", default=None)
    ap.add_argument("--nombre-base", default=None)
    ap.add_argument("--productos", default=None, help="ids de product separados por coma")
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
    ap.add_argument("--esperado", type=int, default=None, help="campanas autorizadas (5)")
    ap.add_argument("--huella", default=None, help="huella del dry-run")
    ap.add_argument("--go", default=None, help="literal del dueno (va al ledger)")
    ap.add_argument("--desarmar", default=None, help="lote a pausar (reversa, tarea 9)")
    ap.add_argument("--reconciliar", action="store_true")
    ap.add_argument("--lote", default=None)
    return ap


def _valida_args_creacion(args) -> None:
    faltan = [
        nombre
        for nombre in ("plataforma", "tipo_producto", "nombre_base", "productos", "modo")
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


# --- pendientes de tareas 7-9: rechazo explicito, esta entrega es solo lectura


def _mutar(args, plan, huella) -> int:  # tarea 7
    raise Abortar("--acepto-mutacion-real: pendiente de la tarea 7; esta entrega es solo lectura")


def _desarmar(args) -> int:  # tarea 9
    raise Abortar("--desarmar: pendiente de la tarea 9; esta entrega es solo lectura")


def _reconciliar_cmd(args) -> int:  # tarea 9
    raise Abortar("--reconciliar: pendiente de la tarea 9; esta entrega es solo lectura")


def main() -> int:
    args = _parser().parse_args()
    if args.reconciliar:
        return _reconciliar_cmd(args)  # tarea 9
    if args.desarmar is not None:
        return _desarmar(args)  # tarea 9
    _valida_args_creacion(args)
    return _crear(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Abortar as exc:
        _log("ABORTAR_FAIL_CLOSED", motivo=str(exc))
        sys.exit(2)
