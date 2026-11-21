"""Lectura de datos y armado del plan de la fabrica de campanas (FABRICA 01).

Solo lectura sobre Postgres (`ORBIT_DSN_READ`), cero HTTP y cero mutaciones.
Extraido de `tools/fabrica_campanas.py` en Fase 3.2 para invertir la fuga
`app/ -> tools/` y darle dueno canonico al armado de `PlanGrupoV2`.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import psycopg

from app import fabrica_plan as fp
from app import familias
from app.optimizer.goals import fraccion_desde_settings, presupuesto_alto

# Primer parametro CASTEADO (%s::platform): sin el cast Postgres no infiere el
# tipo (IndeterminateDatatype, leccion de archiva_inertes).
_SQL_SETTINGS = "SELECT id, settings FROM config_version ORDER BY id DESC LIMIT 1"

_SQL_PUBLICACIONES = """
SELECT l.id, p.id, l.external_id, l.seller_sku, m.margen_neto_pct
  FROM listing l
  JOIN product p ON p.id = l.product_id
  LEFT JOIN v_margen_producto m ON m.product_id = p.id AND m.platform = l.platform
 WHERE l.platform = %s::platform AND l.id = ANY(%s)
 ORDER BY l.id
"""

# A2: productos de los listings para resolver la familia ANTES de armar el
# plan (el tipo_producto sale del slug cuando hay familia unica).
_SQL_PRODUCTOS_DE_LISTINGS = """
SELECT DISTINCT l.product_id
  FROM listing l
 WHERE l.platform = %s::platform AND l.id = ANY(%s)
"""

# Terminos de las campanas del producto: campanas con un product_ad ligado
# al listing. GRANO DEL ORIGEN sellado por la evidencia de la tarea 1 (f):
# search_term_observation tiene UN solo grano en produccion, ad_group.
# D-GLM-6-1: el dia de referencia es un PARAMETRO UTC calculado en Python
# (`%s::date`), NO CURRENT_DATE.
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

SUFIJO_POR_ROL: dict[str, str] = {
    "auto_discovery": "auto",
    "category_phrase": "phrase",
    "product_targeting": "product",
    "category_broad": "broad",
    "category_exact": "exact",
}


class Abortar(RuntimeError):
    """Fail-closed: la realidad difiere del plan o la API rechaza."""


@dataclass(frozen=True)
class SolicitudDatosPlan:
    """Entradas tipadas para leer datos observados de un plan v2 (sin SimpleNamespace)."""

    plataforma: str
    listing_ids: str
    origen_objetivo: str | None = None
    target_acos: Any = None


@dataclass(frozen=True)
class SolicitudPlan:
    """Solicitud tipada para armar un PlanGrupoV2 desde la web o el CLI."""

    plataforma: str
    nombre_base: str
    listing_ids: str | None
    modo: str
    parametros: dict[str, dict[str, Any]]
    tipo_producto: str | None = None
    origen_objetivo: str | None = None
    target_acos: Any = None

    @classmethod
    def desde_web(cls, solicitud: dict[str, Any]) -> SolicitudPlan:
        listings = solicitud.get("listing_ids")
        listing_ids = ",".join(str(lid) for lid in sorted(listings)) if listings else None
        objetivo = solicitud.get("objetivo") or {}
        return cls(
            plataforma=solicitud["plataforma"],
            nombre_base=solicitud.get("nombre_base", ""),
            listing_ids=listing_ids,
            modo=solicitud["modo"],
            parametros=solicitud["parametros"],
            tipo_producto=solicitud.get("tipo_producto"),
            origen_objetivo=objetivo.get("origen") if objetivo else None,
            target_acos=objetivo.get("acos_pct") if objetivo else None,
        )


def _roles_del_plan(plan: fp.PlanGrupoV2) -> tuple[str, ...]:
    return plan.roles


def _fraccion(conn: psycopg.Connection, platform: str) -> Decimal:
    fila = conn.execute(_SQL_SETTINGS).fetchone()
    settings = fila[1] if fila else {}
    fraccion = fraccion_desde_settings(settings or {}, platform)  # invalida -> ValueError
    if fraccion is None:
        raise Abortar(
            f"sin fraccion: ads_target_fraccion_margen_{platform} ausente en la config vigente"
        )
    return fraccion


def _publicaciones_v2(
    conn: psycopg.Connection, platform: str, ids: list[int]
) -> list[fp.PublicacionGrupoV2]:
    filas = conn.execute(_SQL_PUBLICACIONES, (platform, ids)).fetchall()
    vistos = {fila[0] for fila in filas}
    faltan = sorted(set(ids) - vistos)
    if faltan:
        raise Abortar(f"listing(s) {faltan} no existe(n) en {platform}")
    asins = [str(fila[2]).strip() if fila[2] else "" for fila in filas]
    if not all(fp.PATRON_ASIN.fullmatch(asin) for asin in asins):
        raise Abortar("ASIN ausente o invalido en publicaciones v2")
    skus = [str(fila[3]).strip() if fila[3] else "" for fila in filas]
    if not all(skus) or len(skus) != len(set(skus)):
        raise Abortar("seller_sku ausente o duplicado en publicaciones v2")
    return [
        fp.PublicacionGrupoV2(
            listing_id=fila[0],
            product_id=fila[1],
            asin=asins[indice],
            seller_sku=str(fila[3]).strip(),
            platform=platform,
            margen_neto_pct=Decimal(fila[4]) if fila[4] is not None else None,
        )
        for indice, fila in enumerate(filas)
    ]


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


def _recomendaciones_rol(filas: Any) -> tuple[fp.Recomendacion, ...]:
    return tuple(
        fp.Recomendacion(
            fp.Expresion(fila["tipo"], fila.get("valor")),
            _decimal(fila["minimo"], "minimo Amazon"),
            _decimal(fila["sugerido"], "sugerido Amazon"),
            _decimal(fila["maximo"], "maximo Amazon"),
            fila.get("fuente", "amazon_v4"),
        )
        for fila in filas
    )


def _parametros(args) -> dict[str, fp.ParametrosRol]:
    if isinstance(args, SolicitudPlan):
        return {
            rol: fp.ParametrosRol(
                rol=rol,
                budget=_decimal(args.parametros[rol]["budget"], f"--budget-{s}"),
                bid=_decimal(args.parametros[rol]["bid"], f"--bid-{s}"),
                fuente_bid=args.parametros[rol].get("fuente_bid", "manual"),
                recomendaciones=_recomendaciones_rol(
                    args.parametros[rol].get("recomendaciones", ())
                ),
            )
            for rol, s in SUFIJO_POR_ROL.items()
        }
    return {
        rol: fp.ParametrosRol(
            rol=rol,
            budget=_decimal(getattr(args, f"budget_{s}"), f"--budget-{s}"),
            bid=_decimal(getattr(args, f"bid_{s}"), f"--bid-{s}"),
            fuente_bid=getattr(args, f"fuente_bid_{s}", "manual"),
            recomendaciones=_recomendaciones_rol(getattr(args, f"recomendaciones_{s}", ())),
        )
        for rol, s in SUFIJO_POR_ROL.items()
    }


def _ids_listings(texto: str) -> list[int]:
    try:
        ids = [int(p) for p in (texto or "").split(",") if p.strip()]
    except ValueError:
        raise Abortar(f"--listing-ids debe ser ids enteros separados por coma: {texto!r}") from None
    if not ids or len(ids) != len(set(ids)):
        raise Abortar("--listing-ids no puede ser vacio ni contener duplicados")
    return sorted(ids)


def _tipo_desde_familia(
    conn: psycopg.Connection, platform: str, product_ids: list[int], tipo_dado: str | None
) -> tuple[str, str | None, str | None]:
    """A2: tipo_producto, slug de familia y advertencia de mezcla.

    Una familia unica manda: el tipo sale de su slug (el dado se corrige).
    Sin etiqueta o con mezcla, el tipo dado es obligatorio (camino viejo).
    """
    por_producto = familias.familia_de_productos(conn, platform, product_ids)
    distintas = {info["slug"]: info["nombre"] for info in por_producto.values()}
    sin_etiqueta = sorted(set(product_ids) - set(por_producto))
    if len(distintas) == 1 and not sin_etiqueta:
        slug = next(iter(distintas))
        return slug, slug, None
    # Mezcla y/o parcial: camino viejo con el tipo dado + aviso de cada mal.
    # Nadie etiquetado = camino viejo silencioso (salida byte-identica).
    avisos = []
    mezcla = fp.aviso_mezcla_familias(list(distintas.values()))
    if mezcla is not None:
        avisos.append(mezcla)
    if distintas and sin_etiqueta:
        avisos.append(f"productos sin familia en el grupo: {sin_etiqueta}")
    if tipo_dado is None:
        raise Abortar("--tipo-producto es obligatorio sin familia unica en los productos")
    return tipo_dado, None, " · ".join(avisos) or None


def avisos_presupuesto_alto(plan: fp.PlanGrupoV2, p90: Decimal | None) -> list[str]:
    """W-N: una linea por campana nueva cuyo budget pasa del doble del P90
    de las activas en su moneda (o del numero fijo si no hay activas). Puro:
    recibe el plan y el P90; lo usan la terminal y la web."""
    avisos = []
    for rol in _roles_del_plan(plan):
        aviso = presupuesto_alto(plan.parametros[rol].budget, plan.moneda, p90, "tus_campanas")
        if aviso is not None:
            nombre = fp.nombre_campana(plan.tipo_producto, plan.nombre_base, rol, plan.fecha)
            avisos.append(f"{nombre}: {aviso.texto()}")
    return avisos


def _datos_plan_v2(args, conn_read, tipo: str):
    """Datos observados del plan v2; independientes de budgets y bids."""
    ids = _ids_listings(args.listing_ids)
    publicaciones = _publicaciones_v2(conn_read, args.plataforma, ids)
    origen = getattr(args, "origen_objetivo", None)
    if origen is None:
        # A1: --target-acos es opcional; sin el, el CLI deriva del margen
        # medido (la web siempre manda origen_objetivo explicito).
        if getattr(args, "target_acos", None) is not None:
            origen = "manual_lanzamiento"
        else:
            origen = "margen_medido"
    if origen == "manual_lanzamiento":
        try:
            target = _decimal(args.target_acos, "--target-acos")
        except (InvalidOperation, TypeError):
            raise Abortar("--target-acos invalido") from None
        # Banda y forma se validan aqui porque es el unico punto de alta manual
        # compartido por CLI, preview, crear y sugerir_bids.
        fp.valida_banda_manual(target)
        fp.valida_forma_objetivo(target)
        objetivo = fp.ObjetivoPlanV2(origen, target, "manual_lanzamiento confirmado")
    elif origen == "margen_medido":
        fraccion = _fraccion(conn_read, args.plataforma)
        margenes = [p.margen_neto_pct for p in publicaciones]
        fp.valida_objetivo_margen_v2(margenes)
        resultado = fp.target_del_grupo(margenes, fraccion)
        objetivo = fp.ObjetivoPlanV2(
            origen,
            resultado.aplicado,
            resultado.procedencia,
            resultado.fraccion,
            resultado.derivado,
        )
    else:
        raise Abortar("v2 exige objetivo manual_lanzamiento o margen_medido")
    listings = [p.listing_id for p in publicaciones]
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
        terminos, kws, negs, objetivo.acos_pct, terminos_exact=terminos_exact
    )
    existentes = tuple(_existentes(conn_read, args.plataforma, listings))
    return publicaciones, objetivo, semillas, existentes


def _arma_plan_v2(
    args, conn_read, tipo_dado: str | None, moneda: str, parametros: dict
) -> fp.PlanGrupoV2:
    # A2: la familia sale de los productos de los listings; con familia
    # unica el tipo es su slug (el dado se corrige).
    ids = _ids_listings(args.listing_ids)
    filas = conn_read.execute(_SQL_PRODUCTOS_DE_LISTINGS, (args.plataforma, ids)).fetchall()
    tipo, familia, advertencia = _tipo_desde_familia(
        conn_read, args.plataforma, [fila[0] for fila in filas], tipo_dado
    )
    publicaciones, objetivo, semillas, existentes = _datos_plan_v2(args, conn_read, tipo)
    plan = fp.PlanGrupoV2(
        args.plataforma,
        tipo,
        args.nombre_base.strip(),
        datetime.datetime.now(datetime.UTC).date(),
        moneda,
        args.modo,
        tuple(publicaciones),
        parametros,
        objetivo,
        semillas,
        existentes,
        familia,
        advertencia,
    )
    fp._valida_plan_v2(plan)
    return plan


def _arma_plan(args, conn_read: psycopg.Connection) -> fp.PlanGrupoV2:
    """Plan completo desde la base + validacion SIN HTTP (spec §5.1)."""
    try:
        tipo_dado = (
            fp.valida_tipo_producto(args.tipo_producto)
            if getattr(args, "tipo_producto", None) is not None
            else None
        )
        moneda = fp.MONEDA_POR_PLATAFORMA[args.plataforma]
        parametros = _parametros(args)
        fp.valida_parametros(parametros, moneda)
        if not args.nombre_base or not args.nombre_base.strip():
            raise Abortar("--nombre-base no puede ser vacio")
        return _arma_plan_v2(args, conn_read, tipo_dado, moneda, parametros)
    except fp.PlanInvalido as exc:
        raise Abortar(str(exc)) from exc
    finally:
        conn_read.commit()  # la lectura cierra su txn ANTES de cualquier salida
