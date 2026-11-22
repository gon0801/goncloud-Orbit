"""Pipeline de escritura DB para reportes Amazon Ads v3.

La planificacion pura vive en `app.ads.reports_plan`, las especificaciones y el
transporte HTTP viven en `app.ads.reports_specs` (re-exportados aqui para
mantener una unica superficie publica estable). Este modulo concentra la
escritura append-only (`ingest_metrics`, `ingest_search_terms`,
`ingest_productos`) y la envoltura transaccional compartida por grupo
(`escribir_grupo_transaccional` / `escribir_grupo`).
"""

from __future__ import annotations

import datetime as dt
import logging
from collections import Counter
from collections.abc import Callable, Sequence
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from app.ads.reports_plan import (  # noqa: F401 - re-export estable de planificacion pura
    _RE_ASIN,
    AdsReportsError,
    _decimal_reporte,
    _entero_reporte,
    _envenenar_clave,
    _FilaPlano,
    _FilaProducto,
    _FilaTermino,
    _fusionar_fila_producto,
    _planea_filas,
    _planea_filas_productos,
    _planea_filas_terminos,
    _sumar_decimal,
    _sumar_entero,
    es_asin_like,
)
from app.ads.reports_specs import (  # noqa: F401 - re-export estable de specs/transporte
    CAMPANAS_CFG,
    KEYWORDS_CFG,
    MAX_BYTES_REPORTE,
    MAX_RANGO_DIAS,
    PRODUCTOS_CFG,
    REPORTES_CFG,
    SEARCH_TERMS_CFG,
    SOURCE_PRODUCTOS,
    TARGETS_CFG,
    ResultadoIngesta,
    ResumenReporte,
    solicitar_reporte,
)
from app.ads.reports_specs import descargar_filas as _descargar_filas_spec
from app.ads.reports_specs import json_de as _json_de  # noqa: F401 - usado por descarga.py
from app.ads.reports_specs import registrar_resultado as _registrar_resultado
from app.ads.reports_specs import (
    validar_rango as _validar_rango,  # noqa: F401 - usado por diario.py
)
from app.ads.salud import SOURCE  # noqa: F401 - reexport que usa app.ads.diario
from app.ads.structure import PerfilAds, _formato_skip_reason, _sellar_run
from app.dia_cerrado import ultimo_dia_cerrado
from app.redaction import install_scrub_filter, scrub

logger = logging.getLogger(__name__)
install_scrub_filter(logger)


def descargar_filas(client, url: str) -> list[dict]:
    """Delegado que honra `MAX_BYTES_REPORTE` de este modulo (tests monkeypatch)."""
    return _descargar_filas_spec(client, url, max_bytes=MAX_BYTES_REPORTE)


_SQL_ENTIDADES = """
    SELECT external_id, id FROM ad_entity
    WHERE platform = %s AND kind = %s AND external_id = ANY(%s)
"""

_SQL_FECHA_HOY = "SELECT now()"

_SQL_INSERT_METRICA = """
    INSERT INTO ads_metric_observation
        (ad_entity_id, metric_date, observed_at, metric_currency, cost,
         ad_revenue, revenue_same_sku, impressions, clicks, orders,
         source_report_id, ingest_run_id)
    VALUES (%s, %s, now(), %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (ad_entity_id, metric_date, source_report_id)
        WHERE source_report_id IS NOT NULL
    DO NOTHING
    RETURNING ad_entity_id
"""

_SQL_INSERT_TERMINO = """
    INSERT INTO search_term_observation
        (platform, ad_entity_id, search_term, metric_date, observed_at, metric_currency,
         cost, clicks, orders, ad_revenue, is_asin_like, source_report_id, ingest_run_id)
    VALUES (%s, %s, %s, %s, now(), %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (platform, ad_entity_id, search_term, metric_date, source_report_id)
        WHERE source_report_id IS NOT NULL
    DO NOTHING
    RETURNING search_term
"""

_SQL_INSERT_PRODUCTO = """
    INSERT INTO ads_product_metric_observation
        (platform, advertised_asin, advertised_sku, metric_date, observed_at,
         metric_currency, impressions, clicks, cost, purchases30d, sales30d,
         purchases_same_sku30d, attributed_sales_same_sku_30d,
         campaign_ids, ad_ids, source_report_id, ingest_run_id)
    VALUES (%s, %s, %s, %s, now(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (platform, advertised_asin, advertised_sku, metric_date, source_report_id)
        WHERE source_report_id IS NOT NULL
    DO NOTHING
    RETURNING advertised_asin
"""

_SKIPS_SIN_ACTIVIDAD_DEL_RANGO = frozenset(
    {
        "fila agregada por clave duplicada en el reporte",
        "fila con metric_date fuera del rango solicitado",
        "fila de un día que aún no cierra",
    }
)


def _cobertura_integra(skips: Counter[str], report_id: str) -> bool:
    duplicado = f"fila duplicada del reporte {report_id}"
    return all(
        motivo in _SKIPS_SIN_ACTIVIDAD_DEL_RANGO or motivo == duplicado
        for motivo, n in skips.items()
        if n
    )


def _exigir_perfil_aceptado(perfil: PerfilAds, nombre: str) -> None:
    if not perfil.aceptado or perfil.platform is None or perfil.moneda is None:
        raise AdsReportsError(
            f"{nombre} exige un perfil aceptado (profile_id={perfil.profile_id!r})"
        )


def _ejecutar_inserts(
    conn: psycopg.Connection,
    perfil: PerfilAds,
    plan: Sequence[Any],
    *,
    total_filas: int,
    skips: Counter[str],
    sql_insert: str,
    construir_params: Callable[[Any, int | None], tuple],
    report_id: str,
    kind: str | None = None,
    cobertura_integra: Callable[[Counter[str], str], bool] | None = None,
) -> ResultadoIngesta:
    """Ejecuta el bloque comun de resolucion de `ad_entity` + INSERTs append-only."""
    ids: dict[str, int] = {}
    if kind is not None:
        externos = sorted({fila.external_id for fila in plan})
        if externos:
            ids = {
                str(external): entidad_id
                for external, entidad_id in conn.execute(
                    _SQL_ENTIDADES, (perfil.platform, kind, externos)
                ).fetchall()
            }

    written = 0
    for fila in plan:
        entidad_id: int | None = None
        if kind is not None:
            entidad_id = ids.get(fila.external_id)
            if entidad_id is None:
                skips[f"entidad ausente en ad_entity ({kind})"] += 1
                logger.debug(
                    "entidad ausente en ad_entity: %s %s (correr sync de estructura)",
                    kind,
                    fila.external_id,
                )
                continue
        if conn.execute(sql_insert, construir_params(fila, entidad_id)).fetchone() is None:
            skips[f"fila duplicada del reporte {report_id}"] += 1
            continue
        written += 1

    return ResultadoIngesta(
        report_id=report_id,
        filas=total_filas,
        rows_written=written,
        rows_skipped=sum(skips.values()),
        skip_reason=_formato_skip_reason(skips),
        skips=skips,
        cobertura_integra=cobertura_integra(skips, report_id) if cobertura_integra else None,
    )


def ingest_metrics(
    conn: psycopg.Connection,
    perfil: PerfilAds,
    reporte_cfg: dict,
    report_id: str,
    filas: list[dict],
    *,
    run_id: int,
    ultimo_cerrado: dt.date,
    fecha_ini: dt.date,
    fecha_fin: dt.date,
) -> ResultadoIngesta:
    """Inserta las filas de UN reporte en `ads_metric_observation` (append-only)."""
    _exigir_perfil_aceptado(perfil, "ingest_metrics")
    plan, skips = _planea_filas(
        reporte_cfg, filas, ultimo_cerrado=ultimo_cerrado, fecha_ini=fecha_ini, fecha_fin=fecha_fin
    )
    return _ejecutar_inserts(
        conn,
        perfil,
        plan,
        total_filas=len(filas),
        skips=skips,
        sql_insert=_SQL_INSERT_METRICA,
        kind=reporte_cfg["kind"],
        report_id=report_id,
        construir_params=lambda fila, entidad_id: (
            entidad_id,
            fila.metric_date,
            perfil.moneda,
            fila.cost,
            fila.ad_revenue,
            fila.revenue_same_sku,
            fila.impressions,
            fila.clicks,
            fila.orders,
            report_id,
            run_id,
        ),
    )


def ingest_search_terms(
    conn: psycopg.Connection,
    perfil: PerfilAds,
    reporte_cfg: dict,
    report_id: str,
    filas: list[dict],
    *,
    run_id: int,
    ultimo_cerrado: dt.date,
    fecha_ini: dt.date,
    fecha_fin: dt.date,
) -> ResultadoIngesta:
    """Inserta las filas de UN reporte en `search_term_observation` (append-only)."""
    _exigir_perfil_aceptado(perfil, "ingest_search_terms")
    plan, skips = _planea_filas_terminos(
        reporte_cfg, filas, ultimo_cerrado=ultimo_cerrado, fecha_ini=fecha_ini, fecha_fin=fecha_fin
    )
    return _ejecutar_inserts(
        conn,
        perfil,
        plan,
        total_filas=len(filas),
        skips=skips,
        sql_insert=_SQL_INSERT_TERMINO,
        kind=reporte_cfg["kind"],
        report_id=report_id,
        construir_params=lambda fila, entidad_id: (
            perfil.platform,
            entidad_id,
            fila.search_term,
            fila.metric_date,
            perfil.moneda,
            fila.cost,
            fila.clicks,
            fila.orders,
            fila.ad_revenue,
            fila.is_asin_like,
            report_id,
            run_id,
        ),
    )


def ingest_productos(
    conn: psycopg.Connection,
    perfil: PerfilAds,
    reporte_cfg: dict,
    report_id: str,
    filas: list[dict],
    *,
    run_id: int,
    ultimo_cerrado: dt.date,
    fecha_ini: dt.date,
    fecha_fin: dt.date,
) -> ResultadoIngesta:
    """Inserta `spAdvertisedProduct` en `ads_product_metric_observation` (append-only)."""
    del reporte_cfg
    _exigir_perfil_aceptado(perfil, "ingest_productos")
    plan, skips = _planea_filas_productos(
        filas, ultimo_cerrado=ultimo_cerrado, fecha_ini=fecha_ini, fecha_fin=fecha_fin
    )
    return _ejecutar_inserts(
        conn,
        perfil,
        plan,
        total_filas=len(filas),
        skips=skips,
        sql_insert=_SQL_INSERT_PRODUCTO,
        report_id=report_id,
        cobertura_integra=_cobertura_integra,
        construir_params=lambda fila, _ent: (
            perfil.platform,
            fila.asin,
            fila.sku,
            fila.metric_date,
            perfil.moneda,
            fila.impressions,
            fila.clicks,
            fila.cost,
            fila.purchases30d,
            fila.sales30d,
            fila.purchases_same_sku30d,
            fila.attributed_sales_same_sku_30d,
            Jsonb(fila.campaign_ids),
            Jsonb(fila.ad_ids),
            report_id,
            run_id,
        ),
    )


_INGESTORES_POR_TABLA = {
    None: ingest_metrics,
    "search_term_observation": ingest_search_terms,
    "ads_product_metric_observation": ingest_productos,
}


def escribir_grupo_transaccional(
    conn: psycopg.Connection,
    run_id: int,
    items: list,
    *,
    fecha_ini: dt.date,
    fecha_fin: dt.date,
    ingestar_item: Callable[[Any, dt.datetime | None], tuple[ResultadoIngesta, dict]],
    leer_ahora_db: bool = True,
    incluir_resumenes: bool = True,
    cfg_en_fallo: dict | None = None,
    report_id_en_fallo: bool = True,
):
    """Envoltura transaccional compartida por `reports.escribir_grupo` y `placements`."""
    from app.ads.descarga import ResultadoEscritura

    escritos = 0
    skips: Counter[str] = Counter()
    resumenes: list[ResumenReporte] = []
    perfil_actual: PerfilAds | None = None
    cfg_actual: dict | None = cfg_en_fallo
    report_id_actual: str | None = None
    try:
        with conn.transaction():
            ahora = conn.execute(_SQL_FECHA_HOY).fetchone()[0] if leer_ahora_db else None
            for item in items:
                perfil_actual = item.perfil
                cfg_actual = cfg_en_fallo or item.cfg
                report_id_actual = item.report_id if report_id_en_fallo else None
                resultado, cfg_usada = ingestar_item(item, ahora)
                escritos += resultado.rows_written
                skips.update(resultado.skips)
                if incluir_resumenes:
                    resumenes.append(
                        ResumenReporte(
                            profile_id=item.perfil.profile_id,
                            platform=item.perfil.platform,
                            nombre=cfg_usada["nombre"],
                            report_id=item.report_id,
                            status="COMPLETED",
                            filas=resultado.filas,
                            rows_written=resultado.rows_written,
                            rows_skipped=resultado.rows_skipped,
                            skip_reason=resultado.skip_reason,
                        )
                    )
                _registrar_resultado(
                    conn,
                    run_id,
                    item.perfil,
                    cfg_usada,
                    item.report_id,
                    "written",
                    rango=(fecha_ini, fecha_fin),
                    cobertura_integra=resultado.cobertura_integra,
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
                tiene_unidad = perfil_actual is not None and cfg_actual is not None
                _registrar_resultado(
                    conn,
                    run_id,
                    perfil_actual,
                    cfg_actual,
                    report_id_actual,
                    "failed" if tiene_unidad else "global_failed",
                    str(exc) or type(exc).__name__,
                    rango=(fecha_ini, fecha_fin) if tiene_unidad else None,
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

    if incluir_resumenes:
        return ResultadoEscritura(
            rows_written=escritos,
            rows_skipped=sum(skips.values()),
            skip_reason=_formato_skip_reason(skips),
            reportes=resumenes,
        )
    return ResultadoEscritura(
        rows_written=escritos,
        rows_skipped=sum(skips.values()),
        skip_reason=_formato_skip_reason(skips),
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
    """Fase DB de UN grupo de metricas/productos."""
    del grupo

    def _ingestar(item, ahora):
        tabla = item.cfg.get("tabla")
        ingestor = _INGESTORES_POR_TABLA.get(tabla)
        if ingestor is None:
            raise AdsReportsError(
                f"cfg de reporte {item.cfg.get('nombre')!r} con tabla desconocida: {tabla!r}"
            )
        resultado = ingestor(
            conn,
            item.perfil,
            item.cfg,
            item.report_id,
            item.filas,
            run_id=run_id,
            ultimo_cerrado=ultimo_dia_cerrado(item.perfil.platform, ahora),
            fecha_ini=fecha_ini,
            fecha_fin=fecha_fin,
        )
        return resultado, item.cfg

    return escribir_grupo_transaccional(
        conn,
        run_id,
        items,
        fecha_ini=fecha_ini,
        fecha_fin=fecha_fin,
        ingestar_item=_ingestar,
        leer_ahora_db=True,
        incluir_resumenes=True,
    )
