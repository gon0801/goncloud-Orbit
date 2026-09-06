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
 3. Mutacion (--acepto-mutacion-real --esperado 5 --huella --go): ledger
    pre-HTTP, POST propio (no app.ads.write), readback por LIST, sync y
    registro del grupo con 5 goals. --registrar reintenta solo el registro.
 4. Reversa (--desarmar <lote>) y --reconciliar: pausa verificada y
    promocion solo de lo verificado.

Shapes de campanas/adGroups/targets y el camino feliz de productAds son
HIPOTESIS hasta la sonda (tarea 11).

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
import contextlib
import datetime
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Any

import httpx
import psycopg
from psycopg.rows import tuple_row

from app import fabrica_plan as fp
from app import goals_write
from app.ads.client import DEFAULT_BASE_URL, AdsClient
from app.ads.config import AdsCredentials
from app.ads.structure import evaluar_perfiles, fetch_structure, sync_structure
from app.db import connect
from app.optimizer.goals import fraccion_desde_settings
from app.redaction import install_scrub_filter, register_secret, scrub

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


def _dsn_admin() -> str:
    return _dsn("ORBIT_DSN_ADMIN")


def _dsn_ingest() -> str:
    return _dsn("ORBIT_DSN_INGEST")


API = DEFAULT_BASE_URL
LWA_TOKEN_URL = "https://api.amazon.com/auth/o2/token"
VENDOR_CAMPANAS = fp.VENDOR_POR_PATH["/sp/campaigns"]
_ESPERADO_ROLES = 5
_PAUSADA = "PAUSED"

_SQL_INSERTA_LOTE = """
INSERT INTO fabrica_lote (lote, platform, tipo_producto, nombre_base, go_literal, huella,
                          plan, modo_goal, estado)
VALUES (%s, %s::platform, %s, %s, %s, %s, %s::jsonb, %s, 'planeado')
"""
_SQL_SELLA_LOTE = """
UPDATE fabrica_lote SET estado = %s, detalle = %s, finished_at = now() WHERE lote = %s
"""
_SQL_INSERTA_PASO = """
INSERT INTO fabrica_lote_paso (lote, orden, rol, recurso, request_payload, estado)
VALUES (%s, %s, %s::campana_rol, %s, %s::jsonb, 'planeado')
RETURNING id
"""
_SQL_SELLA_PASO_APPLIED = """
UPDATE fabrica_lote_paso
   SET estado = 'applied', external_id = %s, ack = %s::jsonb, readback_estado = %s
 WHERE id = %s
"""
_SQL_SELLA_PASO_FAILED = """
UPDATE fabrica_lote_paso
   SET estado = 'failed', external_id = %s, ack = %s::jsonb, readback_estado = %s
 WHERE id = %s
"""
# D-CURSOR-177-F2: ID+ACK durables ANTES del LIST; el paso sigue planeado
# hasta que el readback verifique (applied exige readback_estado en el CHECK).
_SQL_SELLA_PASO_ACK = """
UPDATE fabrica_lote_paso
   SET external_id = %s, ack = %s::jsonb
 WHERE id = %s AND estado = 'planeado'
"""
_SQL_ID_ENTIDAD = """
SELECT id FROM ad_entity WHERE platform = %s::platform AND kind = %s AND external_id = %s
"""
_SQL_INSERTA_GRUPO = """
INSERT INTO campana_grupo (platform, tipo_producto, nombre_base, lote, target_acos_pct,
                           target_derivado_pct, fraccion, target_procedencia, go_literal)
SELECT %s::platform, %s, %s, %s, %s, %s, %s, %s, go_literal
  FROM fabrica_lote WHERE lote = %s
ON CONFLICT (lote) DO UPDATE SET lote = EXCLUDED.lote
RETURNING id
"""
_SQL_INSERTA_ROL = """
INSERT INTO campana_grupo_rol (grupo_id, rol, ad_entity_id, ad_group_ad_entity_id)
VALUES (%s, %s::campana_rol, %s, %s)
ON CONFLICT DO NOTHING
"""
_SQL_INSERTA_PRODUCTO = """
INSERT INTO campana_grupo_producto (grupo_id, product_id, listing_id, seller_sku, margen_neto_pct)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT DO NOTHING
"""
_SQL_GOAL_EXISTENTE = """
SELECT id, enabled, mode, harvest_campaign_id, harvest_ad_group_id, harvest_default_bid
  FROM ads_optimizer_goal WHERE ad_entity_id = %s AND scope = 'campaign'
"""
_SQL_CAMPANAS_APPLIED = """
SELECT rol::text, recurso, external_id
  FROM fabrica_lote_paso
 WHERE lote = %s AND estado = 'applied' AND recurso IN ('campaign', 'ad_group')
 ORDER BY orden
"""
# D-CURSOR-177-F1: ledger completo (no solo campaign/ad_group) para --registrar.
_SQL_PASOS_LOTE = """
SELECT orden, rol::text, recurso, estado, external_id, request_payload, ack, readback_estado
  FROM fabrica_lote_paso
 WHERE lote = %s
 ORDER BY orden
"""
_SQL_CAMPANAS_DEL_LOTE = """
SELECT p.rol::text, p.external_id, g.id
  FROM fabrica_lote_paso p
  LEFT JOIN campana_grupo cg ON cg.lote = p.lote
  LEFT JOIN campana_grupo_rol r ON r.grupo_id = cg.id AND r.rol = p.rol
  LEFT JOIN ads_optimizer_goal g ON g.ad_entity_id = r.ad_entity_id AND g.scope = 'campaign'
 WHERE p.lote = %s AND p.recurso = 'campaign'
   AND p.estado IN ('applied', 'failed') AND p.external_id IS NOT NULL
 ORDER BY p.orden
"""
_SQL_PENDIENTES = """
SELECT p.id, p.lote, p.rol::text, l.platform::text, p.recurso, CASE p.recurso
         WHEN 'campaign' THEN '/sp/campaigns' WHEN 'ad_group' THEN '/sp/adGroups'
         WHEN 'product_ad' THEN '/sp/productAds' WHEN 'keyword' THEN '/sp/keywords'
         WHEN 'target' THEN '/sp/targets' ELSE '/sp/negativeKeywords' END,
       p.external_id, p.request_payload
  FROM fabrica_lote_paso p
  JOIN fabrica_lote l ON l.lote = p.lote
 WHERE p.estado IN ('planeado', 'failed')
   AND (%s::text IS NULL OR p.lote = %s)
   AND (%s::platform IS NULL OR l.platform = %s::platform)
 ORDER BY p.id
"""
_SQL_PROMUEVE_APPLIED = """
UPDATE fabrica_lote_paso
   SET estado = 'applied', ack = %s::jsonb, readback_estado = %s
 WHERE id = %s AND estado IN ('planeado', 'failed')
"""
# D-CURSOR-177-F4: Amazon pudo crear aunque el HTTP diga 5xx.
_HTTP_INCERTO = frozenset({500, 502, 503, 504})


def _cierra(*recursos: Any) -> None:
    """Cierra httpx/psycopg si tienen close. No inventa AdsClient.close."""
    for recurso in recursos:
        closer = getattr(recurso, "close", None)
        if closer is None:
            continue
        with contextlib.suppress(Exception):
            closer()


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


@dataclass
class _Ctx:
    http: Any
    token: str
    cred: AdsCredentials
    cliente_lectura: Any
    profile: int
    conn_admin: psycopg.Connection
    lote: str
    orden: int = 0
    conocidos: list = field(default_factory=list)


def _token_lwa(cred: AdsCredentials, client) -> str:
    resp = client.post(
        LWA_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": cred.refresh_token,
            "client_id": cred.client_id,
            "client_secret": cred.client_secret,
        },
    )
    if resp.status_code != 200:
        raise Abortar(f"LWA {resp.status_code}: {scrub(resp.text[:300])}")
    token = resp.json()["access_token"]
    register_secret(token)
    return token


def _perfiles(cliente_lectura) -> dict[str, int]:
    return {
        p.platform: p.profile_id
        for p in evaluar_perfiles(cliente_lectura)
        if p.aceptado and p.platform and p.profile_id is not None
    }


def _post(client, token: str, cred: AdsCredentials, profile: int, path: str, payload: dict) -> dict:
    vendor = fp.VENDOR_POR_PATH[path]
    resp = client.post(
        f"{API}{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Amazon-Advertising-API-ClientId": cred.client_id,
            "Amazon-Advertising-API-Scope": str(profile),
            "Content-Type": vendor,
            "Accept": vendor,
        },
        json={fp.ENVOLTURA_POR_PATH[path]: [payload]},
    )
    cuerpo: dict = {}
    with contextlib.suppress(ValueError):
        cuerpo = resp.json()
    return {"status": resp.status_code, "cuerpo": cuerpo, "texto": scrub(resp.text[:400])}


def _readback(cliente_lectura, profile: int, path_create: str, external: str) -> dict | None:
    path_list = fp.LIST_POR_PATH[path_create]
    clave = fp.CLAVE_ID_POR_PATH[path_create]
    try:
        resp = cliente_lectura.list_objects(
            path_list,
            {fp.FILTRO_ID_POR_LIST[path_list]: {"include": [str(external)]}},
            profile_id=profile,
        )
    except Exception:
        return None
    if resp.status_code != 200:
        return None
    try:
        cuerpo = resp.json()
    except Exception:
        return None
    if not isinstance(cuerpo, dict):
        return None
    contenedor = cuerpo.get(fp.CONTENEDOR_POR_LIST[path_list])
    if not isinstance(contenedor, list):
        return None
    for fila in contenedor:
        if isinstance(fila, dict) and str(fila.get(clave)) == str(external):
            return fila
    return None


def _expresion_normalizada(exp: Any) -> tuple:
    if not isinstance(exp, list):
        return ()
    return tuple(
        sorted((str(e.get("type")), str(e.get("value"))) for e in exp if isinstance(e, dict))
    )


def _monto_wire_cuadra(crudo_leido: Any, crudo_pedido: Any) -> bool:
    if crudo_leido is None:
        return False
    try:
        pedido = Decimal(str(crudo_pedido)).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
        leido = Decimal(str(crudo_leido)).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    except (InvalidOperation, ValueError):
        return False
    return leido == pedido


def _readback_cuadra(leido: dict | None, payload: dict) -> bool:
    if not isinstance(leido, dict) or leido.get("state") != fp.ESTADO_NUEVO:
        return False
    for clave in (
        "keywordText",
        "matchType",
        "sku",
        "name",
        "targetingType",
        "campaignId",
        "adGroupId",
    ):
        if clave in payload and str(leido.get(clave)) != str(payload[clave]):
            return False
    if "expression" in payload and (
        _expresion_normalizada(leido.get("expression"))
        != _expresion_normalizada(payload["expression"])
    ):
        return False
    for clave in ("bid", "defaultBid"):
        if clave in payload and not _monto_wire_cuadra(leido.get(clave), payload[clave]):
            return False
    pedido_budget = payload.get("budget")
    if isinstance(pedido_budget, dict) and "budget" in pedido_budget:
        leido_budget = leido.get("budget")
        if not isinstance(leido_budget, dict) or not _monto_wire_cuadra(
            leido_budget.get("budget"), pedido_budget["budget"]
        ):
            return False
        if "budgetType" in pedido_budget and str(leido_budget.get("budgetType")) != str(
            pedido_budget["budgetType"]
        ):
            return False
    return True


def _inserta_lote(conn, lote: str, plan: fp.PlanGrupo, huella: str, go: str) -> None:
    conn.execute(
        _SQL_INSERTA_LOTE,
        (
            lote,
            plan.platform,
            plan.tipo_producto,
            plan.nombre_base,
            go,
            huella,
            json.dumps(fp.plan_como_json(plan)),
            plan.modo,
        ),
    )
    conn.commit()


def _sella_lote(conn, lote: str, estado: str, detalle: str | None) -> None:
    conn.execute(_SQL_SELLA_LOTE, (estado, detalle, lote))
    conn.commit()


def _inserta_paso(ctx: _Ctx, paso: fp.Paso, payload: dict) -> int:
    ctx.orden += 1
    fila = ctx.conn_admin.execute(
        _SQL_INSERTA_PASO, (ctx.lote, ctx.orden, paso.rol, paso.recurso, json.dumps(payload))
    ).fetchone()
    ctx.conn_admin.commit()
    return fila[0]


def _sella_paso(ctx: _Ctx, paso_id: int, ok: bool, external, ack: dict, readback) -> None:
    sql = _SQL_SELLA_PASO_APPLIED if ok else _SQL_SELLA_PASO_FAILED
    ctx.conn_admin.execute(sql, (external, json.dumps(ack, default=str), readback, paso_id))
    ctx.conn_admin.commit()


def _guarda_ack(ctx: _Ctx, paso_id: int, external: str, ack: dict) -> None:
    """D-CURSOR-177-F2: persiste ID+ACK con COMMIT antes del sleep/LIST."""
    ctx.conn_admin.execute(_SQL_SELLA_PASO_ACK, (external, json.dumps(ack, default=str), paso_id))
    ctx.conn_admin.commit()


def _falla_paso(ctx: _Ctx, paso: fp.Paso, paso_id: int, ack: dict, external, motivo: str) -> None:
    _sella_paso(ctx, paso_id, False, external, ack, None)
    raise Abortar(motivo)


def _motivo_post_fallido(paso: fp.Paso, ack: dict) -> str:
    status = ack.get("status")
    if status == "excepcion" or status in _HTTP_INCERTO:
        return (
            f"{paso.descripcion} ({paso.rol}) INCERTO (status {status}):"
            " Amazon pudo crear un ENABLED; no reintentar este POST;"
            " --desarmar solo pausa filas con external_id"
        )
    return f"{paso.descripcion} ({paso.rol}) rechazado (status {status})"


def _ejecuta_paso(ctx: _Ctx, paso: fp.Paso, padres: dict) -> str:
    payload = {**padres, **paso.payload}
    paso_id = _inserta_paso(ctx, paso, payload)
    try:
        ack = _post(ctx.http, ctx.token, ctx.cred, ctx.profile, paso.path, payload)
    except Exception as exc:
        ack = {"status": "excepcion", "cuerpo": {}, "texto": scrub(str(exc))[:300]}
    if not fp.ack_ok(ack):
        _falla_paso(ctx, paso, paso_id, ack, None, _motivo_post_fallido(paso, ack))
    external = fp.id_creado(ack, fp.CLAVE_ID_POR_PATH[paso.path])
    if external is None:
        _falla_paso(
            ctx,
            paso,
            paso_id,
            ack,
            None,
            f"{paso.descripcion} ({paso.rol}) INCERTO: ack sin"
            f" {fp.CLAVE_ID_POR_PATH[paso.path]}; Amazon pudo crear;"
            " no reintentar este POST; --desarmar solo pausa filas con external_id",
        )
    # ID confirmado: durable ANTES del LIST (sigue planeado hasta verificar).
    _guarda_ack(ctx, paso_id, str(external), ack)
    ctx.conocidos.append({"rol": paso.rol, "recurso": paso.recurso, "external": str(external)})
    time.sleep(0.3)
    try:
        leido = _readback(ctx.cliente_lectura, ctx.profile, paso.path, external)
    except Exception as exc:
        leido = None
        _log(
            "readback_excepcion",
            lote=ctx.lote,
            rol=paso.rol,
            recurso=paso.recurso,
            external=external,
            error=scrub(str(exc))[:200],
        )
    readback = leido.get("state") if isinstance(leido, dict) else None
    cuadra = _readback_cuadra(leido, payload)
    _log(
        "paso",
        lote=ctx.lote,
        rol=paso.rol,
        recurso=paso.recurso,
        external=external,
        ack=ack["cuerpo"],
        readback=readback,
        ok=cuadra,
    )
    _sella_paso(ctx, paso_id, cuadra, external, ack, readback)
    if not cuadra:
        raise Abortar(
            f"readback de {paso.descripcion} ({paso.rol}) no cuadra ({readback});"
            f" external_id={external} queda en el ledger (failed) y en --desarmar;"
            " no reintentar este POST"
        )
    return external


def _ejecuta_rol(ctx: _Ctx, plan: fp.PlanGrupo, rol: str) -> dict:
    pasos = fp.pasos_del_rol(plan, rol)
    externos: dict = {"rol": rol, "product_ads": [], "semillas": []}
    externos["campaign"] = _ejecuta_paso(ctx, pasos[0], {})
    padres = {"campaignId": externos["campaign"]}
    externos["ad_group"] = _ejecuta_paso(ctx, pasos[1], padres)
    padres["adGroupId"] = externos["ad_group"]
    for paso in pasos[2:]:
        ext = _ejecuta_paso(ctx, paso, padres)
        destino = externos["product_ads"] if paso.recurso == "product_ad" else externos["semillas"]
        destino.append({"recurso": paso.recurso, "external": ext})
    return externos


def _valida_go(args, huella: str) -> None:
    if args.esperado is None:
        raise Abortar("mutacion real exige --esperado 5 (las 5 campanas del grupo)")
    if args.esperado != _ESPERADO_ROLES:
        raise Abortar(f"--esperado {args.esperado} != {_ESPERADO_ROLES} campanas del grupo")
    if not args.huella:
        raise Abortar("mutacion real exige --huella del dry-run (autorizacion por conjunto)")
    if args.huella != huella:
        raise Abortar(
            f"--huella {args.huella} != huella del plan {huella}: el plan cambio, se re-autoriza"
        )
    if not args.go or not args.go.strip():
        raise Abortar("mutacion real exige --go con el literal del dueno (no vacio)")


def _sync(cliente_lectura) -> None:
    conn_ingest = connect(_dsn_ingest())
    try:
        sync_structure(conn_ingest, fetch_structure(cliente_lectura))
    finally:
        _cierra(conn_ingest)


def _id_entidad(conn, platform: str, kind: str, external: str) -> int:
    fila = conn.execute(_SQL_ID_ENTIDAD, (platform, kind, external)).fetchone()
    if fila is None:
        raise Abortar(f"{kind} {external} no esta en ad_entity tras el sync: registro detenido")
    return fila[0] if not isinstance(fila, dict) else fila["id"]


def _registrar(ctx: _Ctx, plan: fp.PlanGrupo, creadas: list[dict]) -> int:
    _sync(ctx.cliente_lectura)
    conn = ctx.conn_admin
    exact = next(c for c in creadas if c["rol"] == "category_exact")
    ids = {
        c["rol"]: (
            _id_entidad(conn, plan.platform, "campaign", c["campaign"]),
            _id_entidad(conn, plan.platform, "ad_group", c["ad_group"]),
        )
        for c in creadas
    }
    grupo = conn.execute(
        _SQL_INSERTA_GRUPO,
        (
            plan.platform,
            plan.tipo_producto,
            plan.nombre_base,
            ctx.lote,
            plan.target.aplicado,
            plan.target.derivado,
            plan.target.fraccion,
            plan.target.procedencia,
            ctx.lote,
        ),
    ).fetchone()[0]
    if isinstance(grupo, dict):
        grupo = grupo["id"]
    for rol, (camp_id, ag_id) in ids.items():
        conn.execute(_SQL_INSERTA_ROL, (grupo, rol, camp_id, ag_id))
    for p in plan.productos:
        conn.execute(
            _SQL_INSERTA_PRODUCTO,
            (grupo, p.product_id, p.listing_id, p.seller_sku, p.margen_neto_pct),
        )
    conn.commit()
    ahora = datetime.datetime.now(datetime.UTC)
    bid_exact = plan.parametros["category_exact"].bid
    for rol, (camp_id, _ag_id) in ids.items():
        previo = conn.execute(_SQL_GOAL_EXISTENTE, (camp_id,)).fetchone()
        if previo is not None:
            if isinstance(previo, dict):
                goal_previo = previo["id"]
                enabled = previo["enabled"]
                mode = previo["mode"]
                h_camp = previo["harvest_campaign_id"]
                h_ag = previo["harvest_ad_group_id"]
                h_bid = previo["harvest_default_bid"]
            else:
                goal_previo, enabled, mode, h_camp, h_ag, h_bid = previo
            if (
                not enabled
                or mode != plan.modo
                or str(h_camp) != str(exact["campaign"])
                or str(h_ag) != str(exact["ad_group"])
                or h_bid is None
            ):
                raise Abortar(
                    f"goal existente de {rol} (id={goal_previo}) no esta listo"
                    f" (enabled={enabled}, mode={mode}): no se sella applied;"
                    " no se reactiva por --registrar"
                )
            _log("goal_ya_existe", lote=ctx.lote, rol=rol, goal_id=goal_previo)
            continue
        goal = goals_write.crea_goal(
            conn,
            ad_entity_id=camp_id,
            target_acos_pct=plan.target.aplicado,
            bid_currency=plan.moneda,
            mode=plan.modo,
            harvest_campaign_id=exact["campaign"],
            harvest_ad_group_id=exact["ad_group"],
            harvest_default_bid=bid_exact,
            created_at=ahora,
        )
        _log("goal_creado", lote=ctx.lote, rol=rol, goal_id=goal["id"], mode=plan.modo)
    _log("grupo_registrado", lote=ctx.lote, grupo_id=grupo, target=str(plan.target.aplicado))
    return grupo


def _payload_como_dict(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        return json.loads(raw)
    if raw is None:
        return {}
    return dict(raw)


def _fila_paso(fila) -> dict:
    if isinstance(fila, dict):
        return fila
    claves = (
        "orden",
        "rol",
        "recurso",
        "estado",
        "external_id",
        "request_payload",
        "ack",
        "readback_estado",
    )
    return dict(zip(claves, fila, strict=True))


def _pedido_con_padres(paso: fp.Paso, camp: str | None, ag: str | None) -> dict:
    if paso.recurso == "campaign":
        return dict(paso.payload)
    if paso.recurso == "ad_group":
        return {**paso.payload, "campaignId": camp}
    return {**paso.payload, "campaignId": camp, "adGroupId": ag}


def _identidad_coincide(recurso: str, pedido: dict, guardado: Any) -> bool:
    """Compara identidad del paso (campos), no el JSON serializado completo."""
    g = _payload_como_dict(guardado)
    claves = {
        "campaign": ("name", "targetingType"),
        "ad_group": ("name", "campaignId"),
        "product_ad": ("sku", "campaignId", "adGroupId"),
        "keyword": ("keywordText", "matchType", "campaignId", "adGroupId"),
        "target": ("campaignId", "adGroupId"),
        "negative_keyword": ("keywordText", "matchType", "campaignId", "adGroupId"),
    }
    for clave in claves.get(recurso, ()):
        if clave in pedido and str(g.get(clave)) != str(pedido[clave]):
            return False
    if recurso == "target" and "expression" in pedido:
        return _expresion_normalizada(g.get("expression")) == _expresion_normalizada(
            pedido["expression"]
        )
    return True


def _exige_ledger_completo(conn, plan: fp.PlanGrupo, lote: str) -> None:
    """D-CURSOR-177-F1: todos los pasos de pasos_del_rol deben estar applied."""
    filas = [_fila_paso(f) for f in conn.execute(_SQL_PASOS_LOTE, (lote,)).fetchall()]
    camp_ag: dict[str, dict[str, str]] = {}
    for f in filas:
        if (
            f["estado"] == "applied"
            and f["external_id"]
            and f["recurso"] in ("campaign", "ad_group")
        ):
            camp_ag.setdefault(f["rol"], {})[f["recurso"]] = str(f["external_id"])
    usados: set[int] = set()
    faltan: list[str] = []
    for rol in fp.ROLES_ORDEN_CREACION:
        ids = camp_ag.get(rol, {})
        if "campaign" not in ids or "ad_group" not in ids:
            faltan.append(f"{rol}: falta campaign/ad_group applied")
            continue
        camp, ag = ids["campaign"], ids["ad_group"]
        for paso in fp.pasos_del_rol(plan, rol):
            pedido = _pedido_con_padres(paso, camp, ag)
            match_i = None
            for i, f in enumerate(filas):
                if i in usados or f["rol"] != rol or f["recurso"] != paso.recurso:
                    continue
                if not _identidad_coincide(paso.recurso, pedido, f["request_payload"]):
                    continue
                match_i = i
                break
            if match_i is None:
                faltan.append(f"{rol}/{paso.recurso}: ausente en ledger")
                continue
            usados.add(match_i)
            f = filas[match_i]
            if (
                f["estado"] != "applied"
                or not f["external_id"]
                or f["ack"] is None
                or not f["readback_estado"]
            ):
                faltan.append(
                    f"{rol}/{paso.recurso}: estado={f['estado']} (exige applied con evidencia)"
                )
    if faltan:
        raise Abortar(f"lote {lote} incompleto para registrar: {'; '.join(faltan)}")


def _registrar_cmd(args) -> int:
    conn_admin = connect(_dsn_admin())
    try:
        conn_admin.row_factory = tuple_row
        fila = conn_admin.execute(
            "SELECT plan, estado FROM fabrica_lote WHERE lote = %s", (args.registrar,)
        ).fetchone()
        if fila is None:
            raise Abortar(f"lote {args.registrar} no existe")
        plan_json, estado = fila
        if estado == "applied":
            raise Abortar(f"lote {args.registrar} ya esta applied: nada que registrar")
        if estado == "desarmado":
            raise Abortar(
                f"lote {args.registrar} esta desarmado (campanas PAUSED): registrarlo "
                "lo resucitaria a medias; si se quiere vivo, es decision nueva del dueno"
            )
        plan = fp.plan_desde_json(plan_json)
        _exige_ledger_completo(conn_admin, plan, args.registrar)
        pasos = conn_admin.execute(_SQL_CAMPANAS_APPLIED, (args.registrar,)).fetchall()
        conn_admin.commit()
        creadas: dict[str, dict] = {}
        for rol, recurso, external in pasos:
            creadas.setdefault(rol, {"rol": rol, "product_ads": [], "semillas": []})[recurso] = (
                external
            )
        faltan = [r for r in fp.ROLES_ORDEN_CREACION if "ad_group" not in creadas.get(r, {})]
        if faltan:
            raise Abortar(
                f"lote {args.registrar} sin campana+ad group applied para"
                f" {faltan}: --reconciliar primero"
            )
        cred = AdsCredentials.from_secrets_dir()
        cliente_lectura = AdsClient(cred)
        perfiles = _perfiles(cliente_lectura)
        if plan.platform not in perfiles:
            raise Abortar(f"sin perfil aceptado para {plan.platform}")
        ctx = _Ctx(
            None, "", cred, cliente_lectura, perfiles[plan.platform], conn_admin, args.registrar
        )
        _registrar(ctx, plan, [creadas[r] for r in fp.ROLES_ORDEN_CREACION])
        _sella_lote(conn_admin, args.registrar, "applied", "registro reintentado")
        return 0
    finally:
        _cierra(conn_admin)


def _mutar(args, plan: fp.PlanGrupo, huella: str) -> int:
    _valida_go(args, huella)
    # D-CURSOR-177-F3: falla cerrado antes de LWA/lote/POST (spec §5.1).
    _dsn_ingest()
    cred = AdsCredentials.from_secrets_dir()
    cliente_lectura = AdsClient(cred)
    perfiles = _perfiles(cliente_lectura)
    _log("perfiles", perfiles=perfiles)
    if plan.platform not in perfiles:
        raise Abortar(f"sin perfil aceptado para {plan.platform}: no se muta")
    conn_admin = None
    http = None
    try:
        conn_admin = connect(_dsn_admin())
        http = httpx.Client(timeout=httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0))
        token = _token_lwa(cred, http)
        lote = _lote_nuevo(plan)
        _inserta_lote(conn_admin, lote, plan, huella, args.go)
        ctx = _Ctx(http, token, cred, cliente_lectura, perfiles[plan.platform], conn_admin, lote)
        creadas: list[dict] = []
        try:
            for rol in fp.ROLES_ORDEN_CREACION:
                creadas.append(_ejecuta_rol(ctx, plan, rol))
                _log("campana_creada", lote=lote, rol=rol, external=creadas[-1]["campaign"])
        except Abortar as exc:
            detalle = f"{exc} | creadas: {[c['rol'] for c in creadas]} | conocidos: {ctx.conocidos}"
            _sella_lote(conn_admin, lote, "failed", detalle)
            nota = (
                "las creadas quedan ENABLED en Amazon; --desarmar <lote> las pausa"
                if "INCERTO" not in str(exc)
                else (
                    "resultado INCERTO: no reintentar POST; --desarmar solo pausa"
                    " filas con external_id; verificar consola Amazon por nombre"
                )
            )
            _log(
                "lote_detenido",
                lote=lote,
                motivo=str(exc),
                creadas=[c["rol"] for c in creadas],
                conocidos=ctx.conocidos,
                nota=nota,
            )
            raise
        try:
            _registrar(ctx, plan, creadas)
        except Exception as exc:
            conn_admin.rollback()
            _sella_lote(
                conn_admin,
                lote,
                "failed",
                f"campanas creadas en Amazon, registro interno incompleto: {exc}",
            )
            _log(
                "lote_detenido",
                lote=lote,
                motivo=f"registro: {exc}",
                creadas=[c["rol"] for c in creadas],
                nota="--registrar <lote> reintenta SOLO el registro (no toca Amazon)",
            )
            raise Abortar(f"registro interno incompleto: {exc}") from exc
        _sella_lote(conn_admin, lote, "applied", None)
        _log("reconciliacion_final", lote=lote, campanas=[c["campaign"] for c in creadas], ok=True)
        return 0
    finally:
        _cierra(http, conn_admin)


def _put_estado_campana(ctx: _Ctx, external: str, estado: str) -> dict:
    resp = ctx.http.put(
        f"{API}/sp/campaigns",
        headers={
            "Authorization": f"Bearer {ctx.token}",
            "Amazon-Advertising-API-ClientId": ctx.cred.client_id,
            "Amazon-Advertising-API-Scope": str(ctx.profile),
            "Content-Type": VENDOR_CAMPANAS,
            "Accept": VENDOR_CAMPANAS,
        },
        json={"campaigns": [{"campaignId": str(external), "state": estado}]},
    )
    cuerpo: dict = {}
    with contextlib.suppress(ValueError):
        cuerpo = resp.json()
    return {"status": resp.status_code, "cuerpo": cuerpo, "texto": scrub(resp.text[:400])}


def _pausa_una(ctx: _Ctx, rol: str, external: str, goal_id) -> None:
    ack = _put_estado_campana(ctx, external, _PAUSADA)
    if not fp.ack_ok(ack):
        _log("lote_detenido", lote=ctx.lote, motivo=f"PUT PAUSED de {rol} rechazado")
        raise Abortar(f"PUT PAUSED de {external} ({rol}) rechazado (status {ack.get('status')})")
    time.sleep(0.3)
    leido = _readback(ctx.cliente_lectura, ctx.profile, "/sp/campaigns", external)
    estado = leido.get("state") if isinstance(leido, dict) else None
    _log(
        "desarmar",
        lote=ctx.lote,
        rol=rol,
        external=external,
        ack=ack["cuerpo"],
        readback=estado,
        ok=estado == _PAUSADA,
    )
    if estado != _PAUSADA:
        raise Abortar(f"readback de {external} ({rol}) != PAUSED ({estado}): se detiene")
    if goal_id is not None:
        goals_write.edita_goal(
            ctx.conn_admin,
            goal_id,
            enabled=False,
            updated_at=datetime.datetime.now(datetime.UTC),
        )


def _desarmar(args) -> int:
    conn_admin = None
    http = None
    try:
        conn_admin = connect(_dsn_admin())
        filas = conn_admin.execute(_SQL_CAMPANAS_DEL_LOTE, (args.desarmar,)).fetchall()
        conn_admin.commit()
        if not filas:
            raise Abortar(
                f"lote {args.desarmar} sin campanas que pausar en fabrica_lote_paso"
                " (applied, o failed con external): nada que desarmar"
            )
        for rol, external, goal_id in filas:
            print(f"{rol} | {external} | goal={goal_id} -> PAUSED + enabled=false", flush=True)
        _log("plan_desarmar", lote=args.desarmar, campanas=len(filas))
        if not args.acepto_mutacion_real:
            _log(
                "dry_run",
                modo="desarmar",
                lote=args.desarmar,
                nota="sin --acepto-mutacion-real no se pausa nada",
            )
            return 0
        if not args.go or not args.go.strip():
            raise Abortar("desarmar real exige --go con el literal del dueno")
        cred = AdsCredentials.from_secrets_dir()
        cliente_lectura = AdsClient(cred)
        perfiles = _perfiles(cliente_lectura)
        platform = conn_admin.execute(
            "SELECT platform::text FROM fabrica_lote WHERE lote = %s", (args.desarmar,)
        ).fetchone()
        if platform is None or platform[0] not in perfiles:
            raise Abortar(f"sin perfil aceptado para el lote {args.desarmar}")
        http = httpx.Client(timeout=httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0))
        ctx = _Ctx(
            http,
            _token_lwa(cred, http),
            cred,
            cliente_lectura,
            perfiles[platform[0]],
            conn_admin,
            args.desarmar,
        )
        pausadas = 0
        for rol, external, goal_id in filas:
            _pausa_una(ctx, rol, external, goal_id)
            pausadas += 1
        _sella_lote(conn_admin, args.desarmar, "desarmado", f"go: {args.go}")
        _log("reconciliacion_final", lote=args.desarmar, pausadas=pausadas, ok=True)
        return 0
    finally:
        _cierra(http, conn_admin)


def _reconciliar(
    conn_admin, cliente_lectura, perfiles: dict, lote: str | None, plataforma: str | None
) -> dict:
    filas = conn_admin.execute(_SQL_PENDIENTES, (lote, lote, plataforma, plataforma)).fetchall()
    conn_admin.commit()
    resumen = {"pendientes": len(filas), "recuperadas": 0, "sin_verificar": 0, "ausentes": 0}
    por_plataforma: dict[str, list] = {}
    for fila in filas:
        por_plataforma.setdefault(fila[3], []).append(fila)
    for plataforma_fila, pasos in por_plataforma.items():
        profile = perfiles.get(plataforma_fila)
        if profile is None:
            _log(
                "reconciliar_sin_perfil",
                plataforma=plataforma_fila,
                pasos=len(pasos),
                nota="sin perfil aceptado: quedan sin verificar",
            )
            resumen["sin_verificar"] += len(pasos)
            continue
        for fid, lote_fila, rol, _plat, recurso, path, external, payload in pasos:
            if external is None:
                _log(
                    "reconciliar_sin_external",
                    paso=fid,
                    lote=lote_fila,
                    rol=rol,
                    recurso=recurso,
                    nota="el POST no dejo id: verificar a mano por nombre en la consola",
                )
                resumen["sin_verificar"] += 1
                continue
            leido = _readback(cliente_lectura, profile, path, external)
            if leido is None:
                resumen["sin_verificar"] += 1
                _log(
                    "reconciliar_sin_verificar",
                    paso=fid,
                    external=external,
                    nota="el LIST no respondio o no lo trae",
                )
                continue
            if not _readback_cuadra(leido, payload if isinstance(payload, dict) else {}):
                resumen["ausentes"] += 1
                _log(
                    "reconciliar_no_cuadra", paso=fid, external=external, estado=leido.get("state")
                )
                continue
            ack = {"fuente": "reconciliar", "external": external, "lote": lote_fila}
            conn_admin.execute(_SQL_PROMUEVE_APPLIED, (json.dumps(ack), leido.get("state"), fid))
            conn_admin.commit()
            resumen["recuperadas"] += 1
    _log("reconciliacion", lote=lote, **resumen)
    if resumen["ausentes"]:
        raise Abortar(
            f"reconciliacion con {resumen['ausentes']} paso(s) que viven pero no cuadran"
            " con el payload del ledger: verificacion manual (no se promueve a ciegas)"
        )
    if resumen["sin_verificar"]:
        raise Abortar(
            f"reconciliacion con {resumen['sin_verificar']} paso(s) sin verificar:"
            " nada se promovio a ciegas"
        )
    return resumen


def _reconciliar_cmd(args) -> int:
    conn_admin = None
    try:
        if args.plataforma is None and args.lote is None:
            raise Abortar("--reconciliar exige --lote X o --plataforma (para elegir el perfil)")
        conn_admin = connect(_dsn_admin())
        cred = AdsCredentials.from_secrets_dir()
        cliente_lectura = AdsClient(cred)
        perfiles = _perfiles(cliente_lectura)
        platform = args.plataforma
        if args.lote is not None:
            fila = conn_admin.execute(
                "SELECT platform::text FROM fabrica_lote WHERE lote = %s", (args.lote,)
            ).fetchone()
            if fila is None:
                raise Abortar(f"lote {args.lote} no existe")
            lote_platform = fila[0]
            if platform is None:
                platform = lote_platform
            elif platform != lote_platform:
                raise Abortar(
                    f"--lote {args.lote} es {lote_platform} pero --plataforma"
                    f" {platform}: filtros contradictorios"
                )
        if platform not in perfiles:
            raise Abortar(f"sin perfil aceptado para {platform}")
        _reconciliar(conn_admin, cliente_lectura, perfiles, args.lote, platform)
        return 0
    finally:
        _cierra(conn_admin)


def main() -> int:
    args = _parser().parse_args()
    if args.reconciliar:
        return _reconciliar_cmd(args)
    if args.registrar is not None:
        return _registrar_cmd(args)
    if args.desarmar is not None:
        return _desarmar(args)
    _valida_args_creacion(args)
    return _crear(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Abortar as exc:
        _log("ABORTAR_FAIL_CLOSED", motivo=str(exc))
        sys.exit(2)
