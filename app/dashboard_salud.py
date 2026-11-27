"""Lectura del bloque `/api/dashboard/salud` (ORBIT 16 / DASHBOARD 01).

Extraido de `app/api_dashboard.py` (Ola F1) para acotar el tamano del router.
Solo lectura (SELECT).
"""

from __future__ import annotations

import datetime as dt
import logging

from app.ads.salud import bloque_salud as bloque_ads_ingest
from app.api_common import (
    _SQL_ULTIMO_CICLO_POR_PLATAFORMA,
    _fila_ciclo,
    _parse_notes,
    bloque_target_margen,
)
from app.api_precios import _precios_de
from app.apply_cuota import KINDS_QUOTA, estado_quota
from app.etiquetas import ETIQUETAS, etiqueta
from app.optimizer import hygiene
from app.optimizer.bid import PLATAFORMAS_MONEDA
from app.optimizer.windows import _SQL_SYNC_PLATAFORMA, _SQL_WATERMARK_PLATAFORMA
from app.redaction import install_scrub_filter, scrub
from app.spapi.salud import bloque_salud
from app.ubicacion_io import resumen_ultimo_ciclo

logger = logging.getLogger(__name__)
install_scrub_filter(logger)

_SQL_HISTORICO_14D = """
SELECT id, mode, platform, started_at, finished_at, decisions_count,
       applied_count, status, notes
  FROM optimizer_cycle
 WHERE platform = %s::platform
   AND motor = 'ads_optimizer'
 ORDER BY id DESC
 LIMIT 14
"""

_SQL_AVISOS_PROPUESTA_PENDIENTES = """
SELECT count(*)
  FROM ads_campaign_proposal
 WHERE platform = %s::platform AND status = 'open' AND aviso_estado = 'pending'
"""


def motivo_es(motivo: str | None) -> str | None:
    """Un motivo de /salud en palabras; None queda None (regla 3)."""
    if motivo is None:
        return None
    return etiqueta(motivo, "motivo_salud")


def _motivo_ciclo(status: str, notes) -> str | None:
    if status not in ("degraded", "failed", "skipped"):
        return None
    if isinstance(notes, dict):
        motivo_skip = notes.get("motivo_skip")
        if motivo_skip is not None:
            return etiqueta(motivo_skip, "motivo_salud")
        degradacion = notes.get("degradacion_live")
        if degradacion:
            return ETIQUETAS["degradacion_ciclo"].get(degradacion, degradacion)
        error = notes.get("error")
        if error:
            return error
        return notes.get("texto")
    return None


def _fila_historico(fila) -> dict:
    (cycle_id, _mode, _platform, started_at, _fin, decisions_count, _applied, status, notes) = fila
    return {
        "cycle_id": cycle_id,
        "fecha": started_at,
        "status": status,
        "decisions_count": decisions_count,
        "motivo": _motivo_ciclo(status, _parse_notes(notes)),
    }


def _skips_traducidos(contadores: dict) -> list[dict]:
    return [
        {"motivo": motivo, "count": count, "motivo_es": etiqueta(motivo, "motivo_salud")}
        for motivo, count in sorted(contadores.items(), key=lambda par: (-par[1], par[0]))
    ]


def _skips_de(ultimo_ciclo: dict | None) -> dict:
    if ultimo_ciclo is None or ultimo_ciclo.get("notes") is None:
        return {"entidad": [], "termino": []}
    skips = ultimo_ciclo["notes"].get("skips") or {}
    return {
        "entidad": _skips_traducidos(skips.get("entidad") or {}),
        "termino": _skips_traducidos(skips.get("termino") or {}),
    }


def _harvest_destino_de(ultimo_ciclo: dict | None) -> dict | None:
    if ultimo_ciclo is None or ultimo_ciclo.get("notes") is None:
        return None
    bloque = ultimo_ciclo["notes"].get("harvest_destino")
    if not isinstance(bloque, dict):
        return None
    resueltos = bloque.get("resueltos") or {}
    saltos = bloque.get("saltos_grupo") or {}
    return {
        "resueltos": {k: resueltos.get(k, 0) for k in ("grupo", "excepcion", "terna")},
        "terna_es": ETIQUETAS["motivo_salud"][hygiene.MOTIVO_MIGRACION_PENDIENTE],
        "saltos_grupo": [
            {"campaign_id": int(camp), "motivo": motivo, "motivo_es": motivo_es(motivo)}
            for camp, motivo in sorted(saltos.items(), key=lambda par: int(par[0]))
        ],
    }


def _jev_de(conn) -> dict | None:
    try:
        from app import jev_salud

        return jev_salud.salud(conn, ahora=dt.datetime.now(dt.UTC)).como_dict()
    except Exception as exc:
        logger.warning("salud: jev ilegible: %s", scrub(str(exc)))
        return None


def _ads_ingest_de(conn, plataforma: str) -> dict | None:
    try:
        return bloque_ads_ingest(conn, plataforma)
    except Exception as exc:
        logger.warning("salud: ads_ingest %s ilegible: %s", plataforma, scrub(str(exc)))
        return None


def _avisos_campana_de(conn, plataforma: str, *, hoy_pantalla_fn) -> list[str] | None:
    try:
        from app import avisos_campana as ac
        from app import pantalla_dinero as pd

        hasta = hoy_pantalla_fn(plataforma) - dt.timedelta(days=1)
        desde = hasta - dt.timedelta(days=pd.DIAS_UBICACION - 1)
        por_ubi = pd.lee_ubicaciones(conn, plataforma=plataforma, desde=desde, hasta=hasta)
        por_camp = pd.lee_campanas(conn, plataforma=plataforma, desde=desde, hasta=hasta)
        umbral = pd.umbral_concluir(conn, plataforma)
        return [
            aviso.frase
            for aviso in ac.avisos_del_dia(
                por_ubi,
                por_camp,
                plataforma=plataforma,
                gasto_para_concluir=umbral,
                topada=pd.campana_topada_vigente(conn),
            )
        ]
    except Exception as exc:
        logger.warning("salud: avisos_campana %s ilegible: %s", plataforma, scrub(str(exc)))
        return None


def _avisos_propuesta_de(conn, plataforma: str, ultimo_ciclo: dict | None) -> dict | None:
    try:
        pendientes = conn.execute(_SQL_AVISOS_PROPUESTA_PENDIENTES, (plataforma,)).fetchone()[0]
    except Exception as exc:
        logger.warning(
            "salud: avisos de propuesta %s ilegibles: %s",
            plataforma,
            scrub(str(exc)),
        )
        return None
    fallo = None
    if ultimo_ciclo is not None and ultimo_ciclo.get("notes") is not None:
        fallo = (ultimo_ciclo["notes"].get("telegram") or {}).get("aviso_propuesta")
    return {"pendientes": pendientes, "fallo": fallo}


def _spapi_de(conn, plataforma: str, *, spapi_salud_fn=bloque_salud) -> dict | None:
    try:
        return spapi_salud_fn(conn, plataforma)
    except Exception as exc:
        logger.warning(
            "salud: spapi %s ilegible: %s",
            plataforma,
            scrub(str(exc)),
        )
        return None


def _quota_de(conn, plataforma: str) -> dict:
    quota: dict = {}
    for kind in KINDS_QUOTA:
        try:
            quota[kind] = estado_quota(conn, plataforma, kind)
        except ValueError as exc:
            logger.warning(
                "salud: quota %s/%s ilegible (config rota): %s",
                plataforma,
                kind,
                scrub(str(exc)),
            )
            quota[kind] = {"used": 0, "cap": None, "fuente": "config_rota"}
    return quota


def leer_salud(conn, *, hoy_pantalla_fn, spapi_salud_fn=bloque_salud) -> dict:
    """Snapshot de salud por plataforma + bloque JEV."""
    ciclos = {
        fila[2]: _fila_ciclo(fila)
        for fila in conn.execute(_SQL_ULTIMO_CICLO_POR_PLATAFORMA).fetchall()
    }
    plataformas: dict[str, dict] = {}
    for plataforma in PLATAFORMAS_MONEDA:
        watermark = conn.execute(_SQL_WATERMARK_PLATAFORMA, (plataforma,)).fetchone()[0]
        synced_at = conn.execute(_SQL_SYNC_PLATAFORMA, (plataforma,)).fetchone()[0]
        historico = conn.execute(_SQL_HISTORICO_14D, (plataforma,)).fetchall()
        ultimo = ciclos.get(plataforma)
        plataformas[plataforma] = {
            "watermark": watermark.isoformat() if watermark is not None else None,
            "synced_at": synced_at,
            "ultimo_ciclo": ultimo,
            "historico_14d": [_fila_historico(fila) for fila in historico],
            "skips": _skips_de(ultimo),
            "harvest_destino": _harvest_destino_de(ultimo),
            "quota": _quota_de(conn, plataforma),
            "ubicaciones": resumen_ultimo_ciclo(conn, plataforma),
            "avisos_propuesta": _avisos_propuesta_de(conn, plataforma, ultimo),
            "target_margen": bloque_target_margen(ultimo),
            "spapi": _spapi_de(conn, plataforma, spapi_salud_fn=spapi_salud_fn),
            "ads_ingest": _ads_ingest_de(conn, plataforma),
            "precios": _precios_de(conn, plataforma),
            "avisos_campana": _avisos_campana_de(conn, plataforma, hoy_pantalla_fn=hoy_pantalla_fn),
        }
    return {"plataformas": plataformas, "jev": _jev_de(conn)}
