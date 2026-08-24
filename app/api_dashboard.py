"""Router de series temporales del dashboard (ORBIT 16 - DASHBOARD 01, task 1.3).

Endpoints GET de LECTURA bajo `/api/dashboard` (rol `orbit_read` via
ORBIT_DSN_READ; sin DSN -> 503 fail-closed, misma dependencia que api.py).
Solo GET en toda la superficie (candado de introspeccion OpenAPI).

Sellos de las series (brief docs/DASHBOARD.md §3.1/§3.2/§3.6; el header del
plan manda):

- COLAPSO BITEMPORAL (regla 5): las series leen SIEMPRE `v_metric_latest`
  (ultima observacion por (entidad, fecha)), jamas la tabla cruda.
- GRANO EXPLICITO (anti-doble-conteo): JOIN a ad_entity con
  `e.kind = 'campaign'` en AMBAS queries; el kind vive en ad_entity, no en la
  observacion; las hojas (keyword/product_target) duplicarian el dinero
  (evidencia: campaign 63.96 = keyword 24.94 + product_target 39.02).
- VENTANA [D-30, D-1] UTC: default relativo a hoy; el dia en curso EXCLUIDO
  (vintage parcial, regla 6): un `hasta` que lo pida se RECORTA a D-1 y la
  respuesta declara el rango efectivo.
- INMADUREZ: D-8..D-1 marcados `inmaduro: true` (la atribucion madura 5-8d y
  el costo hasta D+15: mostrarlos sin marca mentiria).
- NULL != 0 (regla 3): spine completo del rango; fecha sin fila -> valores
  null (hueco visible); metrica NULL en alguna campana del dia -> agregado
  envenenado (bool_and, mismo criterio que windows.py) -> null, jamas 0.
- SIN_VENTAS: ad_revenue == 0 (conocido) -> acos null + sin_ventas true (caso
  real: amazon_us 2026-08-22, cost 66.6300, revenue 0.0000).
- DINERO COMO STRING (regla 4): cost/ad_revenue salen str() del NUMERIC(14,4)
  tal cual ("363.1400"); clicks entero; acos string de 2 decimales o null.
  El cliente parsea con Number() (decision 7 del header). MONEDA por serie
  desde PLATAFORMAS_MONEDA (la misma fuente del motor), jamas un total que
  mezcle monedas.

DETERMINISMO: `hoy` sale de `_hoy_utc()` (UTC), inyectable en tests; las
ventanas se calculan en Python, no en SQL (nada depende de la TimeZone de
sesion).
"""

from __future__ import annotations

import datetime as dt
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query

from app.api import ConexionLectura
from app.api_common import _dec_str
from app.optimizer.bid import PLATAFORMAS_MONEDA

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

# Tope duro de la ventana pedible: un anio. El default es [D-30, D-1]; sin
# tope, un `desde` arbitrariamente antiguo es un SELECT que escala con la
# historia.
MAX_DIAS_VENTANA = 366

# Dias inmaduros marcados: [D-8, D-1] (la atribucion madura 5-8d y el costo
# hasta D+15; el dia en curso esta EXCLUIDO de la serie, regla 6).
DIAS_INMADUROS = 8

DIAS_VENTANA_DEFAULT = 30

_SQL_ENTIDAD = """
SELECT platform, name, kind FROM ad_entity WHERE id = %s
"""

_SQL_SERIE_PLATAFORMA = """
SELECT v.metric_date,
       CASE WHEN bool_and(v.cost IS NOT NULL) THEN sum(v.cost) END,
       CASE WHEN bool_and(v.ad_revenue IS NOT NULL) THEN sum(v.ad_revenue) END,
       CASE WHEN bool_and(v.clicks IS NOT NULL) THEN sum(v.clicks)::bigint END
  FROM v_metric_latest v
  JOIN ad_entity e ON e.id = v.ad_entity_id
 WHERE e.kind = 'campaign'
   AND e.platform = %s::platform
   AND v.metric_date BETWEEN %s AND %s
 GROUP BY v.metric_date
 ORDER BY v.metric_date
"""

_SQL_SERIE_CAMPANA = """
SELECT v.metric_date,
       CASE WHEN bool_and(v.cost IS NOT NULL) THEN sum(v.cost) END,
       CASE WHEN bool_and(v.ad_revenue IS NOT NULL) THEN sum(v.ad_revenue) END,
       CASE WHEN bool_and(v.clicks IS NOT NULL) THEN sum(v.clicks)::bigint END
  FROM v_metric_latest v
  JOIN ad_entity e ON e.id = v.ad_entity_id
 WHERE e.kind = 'campaign'
   AND e.id = %s
   AND v.metric_date BETWEEN %s AND %s
 GROUP BY v.metric_date
 ORDER BY v.metric_date
"""


def _hoy_utc() -> dt.date:
    """Fecha UTC de hoy (unico reloj del modulo; los tests la inyectan)."""
    return dt.datetime.now(dt.UTC).date()


def _ventana_efectiva(
    desde: dt.date | None, hasta: dt.date | None, hoy: dt.date
) -> tuple[dt.date, dt.date]:
    """Ventana efectiva de la serie: default [D-30, D-1]; el dia en curso
    EXCLUIDO (un `hasta` que lo pida se recorta a D-1, sellado); `desde`
    posterior a `hasta` tras el recorte -> 422; ventana de mas de
    MAX_DIAS_VENTANA -> 422. La respuesta SIEMPRE declara el rango efectivo."""
    ultimo = hoy - dt.timedelta(days=1)  # D-1
    if desde is None:
        desde = ultimo - dt.timedelta(days=DIAS_VENTANA_DEFAULT - 1)  # D-30
    if hasta is None or hasta > ultimo:
        hasta = ultimo
    if desde > hasta:
        raise HTTPException(
            status_code=422,
            detail="desde no puede ser posterior a hasta (el dia en curso esta excluido)",
        )
    if (hasta - desde).days + 1 > MAX_DIAS_VENTANA:
        raise HTTPException(
            status_code=422,
            detail=f"ventana de mas de {MAX_DIAS_VENTANA} dias: acota el rango",
        )
    return desde, hasta


def _ventana_inmaduros(hoy: dt.date) -> dict[str, str]:
    """Ventana de inmadurez [D-8, D-1] como contrato de la respuesta."""
    return {
        "desde": (hoy - dt.timedelta(days=DIAS_INMADUROS)).isoformat(),
        "hasta": (hoy - dt.timedelta(days=1)).isoformat(),
    }


def _acoso(cost: Decimal | None, ad_revenue: Decimal | None) -> tuple[str | None, bool]:
    """(acos_str, sin_ventas) del dia. ad_revenue == 0 CONOCIDO -> sin_ventas
    true y acos null (infinito: jamas division ni 0 enganoso); cost o
    ad_revenue None -> dato faltante (acos null, sin_ventas false); si no,
    ratio Decimal exacto con 2 decimales como STRING (el cliente parsea con
    Number(); decision 7 del header). Redondeo HALF_UP EXPLICITO (convencion
    comercial; hallazgo kimi: el default del contexto es half-even y 11.125
    saldria "11.12"). Solo presentacion: el motor jamas materializa ACoS
    (compara por multiplicacion)."""
    if ad_revenue is None:
        return None, False
    if ad_revenue == 0:
        return None, True
    if cost is None:
        return None, False
    acos = (cost / ad_revenue * Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return str(acos), False


def _fila_serie(
    fecha: dt.date,
    fila: tuple | None,
    inmaduro_desde: dt.date,
    inmaduro_hasta: dt.date,
) -> dict:
    """Una fila de la serie: fecha del spine con sus valores. `fila` None
    (fecha sin datos) -> TODO null (hueco visible, jamas 0)."""
    inmaduro = inmaduro_desde <= fecha <= inmaduro_hasta
    if fila is None:
        return {
            "fecha": fecha.isoformat(),
            "cost": None,
            "ad_revenue": None,
            "clicks": None,
            "acos": None,
            "sin_ventas": False,
            "inmaduro": inmaduro,
        }
    cost, ad_revenue, clicks = fila
    acos, sin_ventas = _acoso(cost, ad_revenue)
    return {
        "fecha": fecha.isoformat(),
        "cost": _dec_str(cost),
        "ad_revenue": _dec_str(ad_revenue),
        "clicks": clicks,
        "acos": acos,
        "sin_ventas": sin_ventas,
        "inmaduro": inmaduro,
    }


def _arma_serie(
    desde: dt.date, hasta: dt.date, por_fecha: dict[dt.date, tuple], hoy: dt.date
) -> list[dict]:
    """Spine completo [desde, hasta]: TODAS las fechas presentes; fecha sin
    fila -> hueco null (regla 3); inmaduro relativo a hoy (D-8..D-1)."""
    inmaduro_desde = hoy - dt.timedelta(days=DIAS_INMADUROS)
    inmaduro_hasta = hoy - dt.timedelta(days=1)
    serie = []
    fecha = desde
    while fecha <= hasta:
        serie.append(_fila_serie(fecha, por_fecha.get(fecha), inmaduro_desde, inmaduro_hasta))
        fecha += dt.timedelta(days=1)
    return serie


@router.get("/series/plataforma")
def serie_plataforma(
    conn: ConexionLectura,
    platform: Literal["amazon_us", "amazon_mx"],
    desde: dt.date | None = None,
    hasta: dt.date | None = None,
) -> dict:
    """Serie diaria de la plataforma (grano kind='campaign'): cost/revenue
    STRING, clicks entero, acos string o null, sin_ventas, inmaduro. Spine
    completo del rango efectivo (declarado en la respuesta)."""
    hoy = _hoy_utc()
    desde_ef, hasta_ef = _ventana_efectiva(desde, hasta, hoy)
    filas = conn.execute(_SQL_SERIE_PLATAFORMA, (platform, desde_ef, hasta_ef)).fetchall()
    por_fecha = {fila[0]: (fila[1], fila[2], fila[3]) for fila in filas}
    return {
        "plataforma": platform,
        "moneda": PLATAFORMAS_MONEDA[platform],
        "desde": desde_ef.isoformat(),
        "hasta": hasta_ef.isoformat(),
        "ventana_inmaduros": _ventana_inmaduros(hoy),
        "series": _arma_serie(desde_ef, hasta_ef, por_fecha, hoy),
    }


@router.get("/series/campana")
def serie_campana(
    conn: ConexionLectura,
    ad_entity_id: Annotated[int, Query(ge=1)],
    desde: dt.date | None = None,
    hasta: dt.date | None = None,
) -> dict:
    """Serie diaria de UNA campana (kind='campaign' EXPLICITO): mismo contrato
    de filas que la serie de plataforma. 404 si el id no existe; 422 si la
    entidad no es kind='campaign' o su plataforma no tiene moneda sellada."""
    fila_entidad = conn.execute(_SQL_ENTIDAD, (ad_entity_id,)).fetchone()
    if fila_entidad is None:
        raise HTTPException(status_code=404, detail=f"ad_entity_id {ad_entity_id} no existe")
    platform, nombre, kind = fila_entidad
    if kind != "campaign":
        raise HTTPException(
            status_code=422,
            detail=(
                f"ad_entity_id {ad_entity_id} es kind={kind}: el endpoint es "
                "solo para kind='campaign'"
            ),
        )
    if platform not in PLATAFORMAS_MONEDA:
        raise HTTPException(
            status_code=422,
            detail=f"plataforma {platform} sin moneda sellada para el dashboard",
        )
    hoy = _hoy_utc()
    desde_ef, hasta_ef = _ventana_efectiva(desde, hasta, hoy)
    filas = conn.execute(_SQL_SERIE_CAMPANA, (ad_entity_id, desde_ef, hasta_ef)).fetchall()
    por_fecha = {fila[0]: (fila[1], fila[2], fila[3]) for fila in filas}
    return {
        "ad_entity_id": ad_entity_id,
        "nombre": nombre,
        "plataforma": platform,
        "moneda": PLATAFORMAS_MONEDA[platform],
        "desde": desde_ef.isoformat(),
        "hasta": hasta_ef.isoformat(),
        "ventana_inmaduros": _ventana_inmaduros(hoy),
        "series": _arma_serie(desde_ef, hasta_ef, por_fecha, hoy),
    }
