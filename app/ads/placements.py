"""Reporte por placement (BIDS 02, V.2). spCampaigns agrupado por
campaignPlacement, DAILY, en corrida PROPIA (otro `source`, otro cron)
para que un fallo aqui no tire los cuatro reportes principales.

Fase de API COMPLETA antes de la fase de DB; la run se abre antes de
consultar perfiles y todo fallo se sella ok=false antes de re-lanzar
(misma disciplina que `sync_metrics`). `observed_at` lo fija Python por
reporte (no `now()` de la transaccion): dos reportes distintos del mismo
dia dejan dos observaciones y con un solo sello chocarian en la PK.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

import psycopg

from app.ads.reports import (
    _SQL_ENTIDADES,
    AdsClient,
    AdsReportsError,
    ResultadoIngesta,
    _decimal_reporte,
    _entero_reporte,
    _registrar_resultado,
    _validar_rango,
)
from app.ads.structure import (
    PerfilAds,
    _formato_skip_reason,
    _sellar_run,
)
from app.redaction import scrub

logger = logging.getLogger(__name__)

Ubicacion = Literal[
    "arriba_de_busqueda", "resto_de_busqueda", "paginas_de_producto", "fuera_de_amazon"
]

PLACEMENTS_CFG = {
    "nombre": "placements",
    "reportTypeId": "spCampaigns",
    "groupBy": ["campaign", "campaignPlacement"],
    "columns": [
        "date",
        "campaignId",
        "placementClassification",
        "impressions",
        "clicks",
        "cost",
        "purchases30d",
        "sales30d",
    ],
}
SOURCE_PLACEMENTS = "amazon_ads_placements_v3"

_UBICACION = {
    "Top of Search on-Amazon": "arriba_de_busqueda",
    "Other on-Amazon": "resto_de_busqueda",
    "Detail Page on-Amazon": "paginas_de_producto",
    "Off Amazon": "fuera_de_amazon",
}

_SQL_INSERT_PLACEMENT = """
INSERT INTO ads_placement_observation
    (platform, ad_entity_id, placement, metric_date, observed_at, metric_currency,
     impressions, clicks, cost, orders, ad_revenue, source_report_id)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (platform, ad_entity_id, placement, metric_date, source_report_id)
    WHERE source_report_id IS NOT NULL
DO NOTHING
RETURNING ad_entity_id
"""


@dataclass
class _FilaPlacement:
    """Una fila planificada: lo que se inserta si ningun gate la salta."""

    ad_entity_id: int
    metric_date: dt.date
    placement: Ubicacion
    impressions: int | None
    clicks: int | None
    cost: Decimal | None
    orders: int | None
    revenue: Decimal | None


def _clave_externa(crudo: object) -> str | None:
    """campaignId del reporte (numero) a external_id (texto). None cuando
    falta o trae mala forma (jamas se inventa id)."""
    if crudo is None or isinstance(crudo, bool):
        return None
    if isinstance(crudo, str):
        return crudo.strip() or None
    if isinstance(crudo, int):
        return str(crudo)
    if isinstance(crudo, float) and crudo.is_integer():
        return str(int(crudo))
    return None


def _planea_filas_placements(
    filas: list[dict],
    *,
    entidades: dict[str, int],
    desde: dt.date | None = None,
    hasta: dt.date | None = None,
    hoy: dt.date | None = None,
) -> tuple[list[_FilaPlacement], Counter[str]]:
    """Valida cada fila ANTES de tocar la base (vocabulario CERRADO de
    skips, como _planea_filas_productos). Desconocido, negativo o no
    numerico salta la fila y se cuenta; ausente queda NULL (regla 3).
    Fila con las CINCO metricas ausentes salta: se insertaria con NULLs
    y el DISTINCT ON por observed_at DESC taparia datos completos
    anteriores de esa (campana, ubicacion, fecha). Con rango, fecha
    futura o fuera de [desde, hasta] salta como en el pipeline
    principal (reports.py: la fuente no manda lo pedido)."""
    plan: list[_FilaPlacement] = []
    skips: Counter[str] = Counter()
    for fila in filas:
        if not isinstance(fila, dict):
            skips["fila de placements sin campaignId"] += 1
            continue
        externa = _clave_externa(fila.get("campaignId"))
        if externa is None:
            skips["fila de placements sin campaignId"] += 1
            continue
        entidad = entidades.get(externa)
        if entidad is None:
            skips["fila de placements de campana desconocida"] += 1
            continue
        cruda_ubi = fila.get("placementClassification")
        ubicacion = _UBICACION.get(cruda_ubi) if isinstance(cruda_ubi, str) else None
        if ubicacion is None:
            skips["fila de placements con placement desconocido"] += 1
            continue
        cruda = fila.get("date")
        try:
            if not isinstance(cruda, str):
                raise ValueError
            metric_date = dt.date.fromisoformat(cruda)
        except ValueError:
            skips["fila de placements con date invalida"] += 1
            continue
        if hoy is not None and metric_date > hoy:
            skips["fila de placements con metric_date futura"] += 1
            continue
        if desde is not None and hasta is not None and (metric_date < desde or metric_date > hasta):
            skips["fila de placements con metric_date fuera del rango"] += 1
            continue
        try:
            impressions = _entero_reporte(fila.get("impressions"), "impressions")
            clicks = _entero_reporte(fila.get("clicks"), "clicks")
            cost = _decimal_reporte(fila.get("cost"), "cost")
            orders = _entero_reporte(fila.get("purchases30d"), "purchases30d")
            revenue = _decimal_reporte(fila.get("sales30d"), "sales30d")
        except ValueError:
            skips["fila de placements con metrica no numerica"] += 1
            continue
        if any(m is not None and m < 0 for m in (impressions, clicks, cost, orders, revenue)):
            skips["fila de placements con metrica negativa"] += 1
            continue
        if all(m is None for m in (impressions, clicks, cost, orders, revenue)):
            skips["fila de placements sin metricas"] += 1
            continue
        plan.append(
            _FilaPlacement(
                ad_entity_id=entidad,
                metric_date=metric_date,
                placement=ubicacion,
                impressions=impressions,
                clicks=clicks,
                cost=cost,
                orders=orders,
                revenue=revenue,
            )
        )
    return plan, skips


def ingest_placements(
    conn: psycopg.Connection,
    perfil: PerfilAds,
    report_id: str,
    filas: list[dict],
    *,
    observed_at: dt.datetime,
    desde: dt.date | None = None,
    hasta: dt.date | None = None,
    hoy: dt.date | None = None,
) -> ResultadoIngesta:
    """Inserta las filas de UN reporte de placements (append-only, 0062).

    Resuelve las campanas en un solo SELECT; la moneda es la del perfil.
    Re-ingestar el MISMO reporte no duplica (ON CONFLICT DO NOTHING
    contra el indice de dedupe) y las absorbidas cuentan como saltadas.
    Con rango, lo futuro o fuera de [desde, hasta] salta (espejo del
    pipeline); `hoy` omiso es la fecha de observed_at.
    """
    if not perfil.aceptado or perfil.platform is None or perfil.moneda is None:
        raise AdsReportsError(
            f"ingest_placements exige un perfil aceptado (profile_id={perfil.profile_id!r})"
        )
    externas = sorted(
        {
            clave
            for fila in filas
            if isinstance(fila, dict) and (clave := _clave_externa(fila.get("campaignId")))
        }
    )
    entidades = (
        {
            fila[0]: fila[1]
            for fila in conn.execute(
                _SQL_ENTIDADES, (perfil.platform, "campaign", externas)
            ).fetchall()
        }
        if externas
        else {}
    )
    plan, skips = _planea_filas_placements(
        filas,
        entidades=entidades,
        desde=desde,
        hasta=hasta,
        hoy=hoy if hoy is not None else observed_at.date(),
    )
    written = 0
    for fila in plan:
        insertado = conn.execute(
            _SQL_INSERT_PLACEMENT,
            (
                perfil.platform,
                fila.ad_entity_id,
                fila.placement,
                fila.metric_date,
                observed_at,
                perfil.moneda,
                fila.impressions,
                fila.clicks,
                fila.cost,
                fila.orders,
                fila.revenue,
                report_id,
            ),
        ).fetchone()
        if insertado is None:
            skips[f"fila duplicada del reporte {report_id}"] += 1
            continue
        written += 1
    return ResultadoIngesta(
        report_id=report_id,
        filas=len(filas),
        rows_written=written,
        rows_skipped=sum(skips.values()),
        skip_reason=_formato_skip_reason(skips),
        skips=skips,
    )


def escribir_grupo(
    conn: psycopg.Connection,
    run_id: int,
    grupo,
    items: list,
    *,
    fecha_ini: dt.date,
    fecha_fin: dt.date,
):
    """Fase DB del grupo placements: la escribe `al_completar` de
    app.ads.descarga cuando el grupo cierra su fase de API. Una transaccion:
    ingest_placements por reporte (observed_at lo fija Python por reporte: dos
    reportes del mismo dia dejan dos observaciones), `written` por reporte y
    sello ok=true en la misma. En fallo: rollback + sello best-effort ok=false
    + re-lanzamiento (descarga marca el grupo y sigue con los demas)."""
    from app.ads.descarga import ResultadoEscritura

    del grupo
    escritos = 0
    skips: Counter[str] = Counter()
    perfil_actual: PerfilAds | None = None
    try:
        with conn.transaction():
            for item in items:
                perfil = item.perfil
                perfil_actual = perfil
                observado = dt.datetime.now(dt.UTC)
                resultado = ingest_placements(
                    conn,
                    perfil,
                    item.report_id,
                    item.filas,
                    observed_at=observado,
                    desde=fecha_ini,
                    hasta=fecha_fin,
                )
                escritos += resultado.rows_written
                skips.update(resultado.skips)
                _registrar_resultado(
                    conn, run_id, perfil, PLACEMENTS_CFG, item.report_id, "written"
                )
            _sellar_run(
                conn,
                run_id,
                ok=True,
                rows_written=escritos,
                rows_skipped=sum(skips.values()),
                skip_reason=_formato_skip_reason(skips),
            )
    except BaseException as exc:
        try:
            with conn.transaction():
                _registrar_resultado(
                    conn,
                    run_id,
                    perfil_actual,
                    PLACEMENTS_CFG,
                    None,
                    "failed" if perfil_actual else "global_failed",
                    str(exc) or type(exc).__name__,
                )
                _sellar_run(
                    conn,
                    run_id,
                    ok=False,
                    rows_skipped=0,
                    skip_reason=scrub(str(exc)) or type(exc).__name__,
                )
        except Exception:
            logger.warning(
                "ingest_run %s quedo ABIERTA: fallo tambien su sello de fallo; "
                "el error original de la corrida era: %s",
                run_id,
                scrub(str(exc)),
            )
        raise

    return ResultadoEscritura(
        rows_written=escritos,
        rows_skipped=sum(skips.values()),
        skip_reason=_formato_skip_reason(skips),
    )


def sync_placements(
    conn: psycopg.Connection,
    client: AdsClient,
    *,
    desde: dt.date,
    hasta: dt.date,
    sleep=time.sleep,
    reloj=time.monotonic,
) -> int:
    """Orquestador: su fase de API vive en app.ads.descarga (misma vuelta de
    sondeo que metricas cuando el caller los agrupa); aqui solo se arma el
    grupo y la fase de DB (`escribir_grupo`). Devuelve filas escritas; todo
    fallo se sella ok=false antes de subir (incluido cero perfiles aceptados,
    que con contrato int solo puede avisar fallando). `source` propio: un
    fallo aqui no tira a los demas grupos de la vuelta."""
    from app.ads.descarga import GrupoDescarga, descarga

    _validar_rango(desde, hasta)
    grupo = GrupoDescarga(clave="placements", source=SOURCE_PLACEMENTS, cfgs=(PLACEMENTS_CFG,))
    resultados = descarga(
        conn,
        client,
        [grupo],
        (desde, hasta),
        reloj=reloj,
        sleep=sleep,
        al_completar=escribir_grupo,
    )
    res = resultados["placements"]
    if not res.ok:
        if res.exc is not None:
            raise res.exc
        raise AdsReportsError(res.skip_reason or "la corrida de placements fallo")
    return res.escritura.rows_written
