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
    INTENTOS_POLL,
    AdsClient,
    AdsReportsError,
    ResultadoIngesta,
    _decimal_reporte,
    _entero_reporte,
    _registrar_resultado,
    _run_de_fallo_de_api,
    _validar_rango,
    descargar_filas,
    esperar_reporte,
    solicitar_reporte,
)
from app.ads.salud import intentar_procesar_run
from app.ads.structure import (
    _SQL_ABRIR_RUN,
    PerfilAds,
    _formato_skip_reason,
    _sellar_run,
    evaluar_perfiles,
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
    filas: list[dict], *, entidades: dict[str, int]
) -> tuple[list[_FilaPlacement], Counter[str]]:
    """Valida cada fila ANTES de tocar la base (vocabulario CERRADO de
    skips, como _planea_filas_productos). Desconocido, negativo o no
    numerico salta la fila y se cuenta; ausente queda NULL (regla 3)."""
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
        ubicacion = _UBICACION.get(fila.get("placementClassification"))
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
) -> ResultadoIngesta:
    """Inserta las filas de UN reporte de placements (append-only, 0062).

    Resuelve las campanas en un solo SELECT; la moneda es la del perfil.
    Re-ingestar el MISMO reporte no duplica (ON CONFLICT DO NOTHING
    contra el indice de dedupe) y las absorbidas cuentan como saltadas.
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
    plan, skips = _planea_filas_placements(filas, entidades=entidades)
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


def sync_placements(
    conn: psycopg.Connection,
    client: AdsClient,
    *,
    desde: dt.date,
    hasta: dt.date,
    sleep=time.sleep,
) -> int:
    """Orquestador propio: perfiles aceptados -> reporte por perfil ->
    ingesta, con `source` propio. Devuelve filas escritas; todo fallo
    sella su run ok=false antes de re-lanzar (incluido cero perfiles
    aceptados, que con contrato int solo puede avisar fallando)."""
    _validar_rango(desde, hasta)
    with conn.transaction():
        run_id = conn.execute(_SQL_ABRIR_RUN, (SOURCE_PLACEMENTS,)).fetchone()[0]

    descargados: list[tuple[PerfilAds, str, list[dict]]] = []
    perfil_actual: PerfilAds | None = None
    report_id_actual: str | None = None
    try:
        todos = evaluar_perfiles(client)
        perfiles = [perfil for perfil in todos if perfil.aceptado]
        rechazados = [perfil for perfil in todos if not perfil.aceptado]
        with conn.transaction():
            for rechazado in rechazados:
                _registrar_resultado(
                    conn,
                    run_id,
                    rechazado,
                    None,
                    None,
                    "rejected",
                    rechazado.motivo or "perfil rechazado sin motivo",
                )
            for perfil in perfiles:
                _registrar_resultado(conn, run_id, perfil, PLACEMENTS_CFG, None, "pending")
    except BaseException as exc:
        _run_de_fallo_de_api(conn, run_id, exc, None, None, None)
        raise
    if not perfiles:
        # Fuera del try: sync_metrics RETORNA aqui (no raise) para no sellar
        # dos veces; con contrato int solo se puede avisar fallando, asi que
        # el raise va donde el except de fase API ya no lo alcanza.
        motivo = (
            "ningun perfil aceptado: /v2/profiles devolvio 0 perfiles"
            if not rechazados
            else "ningun perfil aceptado: "
            + "; ".join(p.motivo or "sin motivo" for p in rechazados)
        )
        with conn.transaction():
            if not rechazados:
                _registrar_resultado(conn, run_id, None, None, None, "global_failed", motivo)
            _sellar_run(conn, run_id, ok=False, rows_skipped=0, skip_reason=motivo)
        intentar_procesar_run(conn, run_id)
        raise AdsReportsError(motivo)
    try:
        for perfil in perfiles:
            perfil_actual, report_id_actual = perfil, None
            report_id = solicitar_reporte(client, perfil, PLACEMENTS_CFG, desde, hasta)
            report_id_actual = report_id
            url = esperar_reporte(client, perfil, report_id, sleep=sleep, intentos=INTENTOS_POLL)
            filas = descargar_filas(client, url)
            descargados.append((perfil, report_id, filas))
            with conn.transaction():
                _registrar_resultado(conn, run_id, perfil, PLACEMENTS_CFG, report_id, "downloaded")
    except BaseException as exc:
        _run_de_fallo_de_api(conn, run_id, exc, perfil_actual, PLACEMENTS_CFG, report_id_actual)
        raise

    escritos = 0
    skips: Counter[str] = Counter()
    perfil_actual, report_id_actual = None, None
    try:
        with conn.transaction():
            for perfil, report_id, filas in descargados:
                perfil_actual, report_id_actual = perfil, report_id
                observado = dt.datetime.now(dt.UTC)
                resultado = ingest_placements(conn, perfil, report_id, filas, observed_at=observado)
                escritos += resultado.rows_written
                skips.update(resultado.skips)
                _registrar_resultado(conn, run_id, perfil, PLACEMENTS_CFG, report_id, "written")
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
                    report_id_actual,
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
        intentar_procesar_run(conn, run_id)
        raise

    intentar_procesar_run(conn, run_id)
    return escritos
