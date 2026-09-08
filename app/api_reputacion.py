"""API GET de reputacion v1 (REPUTACION 01 A.6; acta 0.5 §6).

`GET /api/reputacion/resumen`: por listing (rating, count,
tendencia, estado de fuente incl. Sin-verificar Amazon-texto),
cuenta MeLi, preguntas pendientes, alertas abiertas y reviews
recientes. Solo lectura (ConexionLectura); `ui.py` reusa
`carga_resumen` con la misma conexion, jamas reimplementa queries.

Seguridad display: la API devuelve JSON crudo (strings intactos);
el escape es del template (autoescape Jinja, pantalla sin JS).
URLs: allowlist Amazon dp https o None (MeLi sin permalink
guardado: no se inventa). Imagenes: fuera de v1 (cero <img>).
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter
from psycopg import Connection

from app.api import ConexionLectura

router = APIRouter(prefix="/api/reputacion", tags=["reputacion"])

_DOMINIOS_AMAZON = {"amazon_mx": "amazon.com.mx", "amazon_us": "amazon.com"}
_TOPE_PENDIENTES = 50
_TOPE_ALERTAS = 50
_TOPE_REVIEWS = 20


def _url_allowlist(platform: str, external_id: str | None) -> str | None:
    """URL del listing o None. Solo https Amazon dp (mismo patron que
    la ingesta A.2); MeLi no guarda permalink: None, nunca inventada."""
    dominio = _DOMINIOS_AMAZON.get(platform)
    if dominio is None or not external_id:
        return None
    return f"https://www.{dominio}/dp/{external_id}"


def _tendencia(actual: float | None, previo: float | None) -> str:
    if actual is None or previo is None:
        return "sin-dato"
    if round(actual, 1) > round(previo, 1):
        return "sube"
    if round(actual, 1) < round(previo, 1):
        return "baja"
    return "estable"


def _series(conn: Connection) -> dict:
    """Snapshots por entidad, recientes primero (escala trivial:
    ~500 filas/semana; misma decision que A.5)."""
    filas = conn.execute(
        "SELECT platform, external_id, metric_date, rating, review_count"
        " FROM reputation_snapshot ORDER BY metric_date DESC, observed_at DESC"
    ).fetchall()
    por_entidad: dict = {}
    for plat, ext, fecha, rating, count in filas:
        por_entidad.setdefault((plat, ext), []).append(
            (
                fecha,
                None if rating is None else float(rating),
                None if count is None else int(count),
            )
        )
    return por_entidad


def _previo(serie: list, hoy: dt.date, es_meli: bool):
    """Previo para tendencia (mismo criterio que caida_rating A.5:
    MeLi <= hoy-7d; Amazon N-1 por metric_date distinta)."""
    if es_meli:
        corte = hoy - dt.timedelta(days=7)
        for fecha, rating, _count in serie[1:]:
            if fecha <= corte:
                return rating
        return None
    primera = serie[0][0]
    for fecha, rating, _count in serie[1:]:
        if fecha != primera:
            return rating
    return None


def carga_resumen(conn: Connection, hoy: dt.date | None = None) -> dict:
    """Todo lo que muestra /reputacion (reusable por ui.py)."""
    hoy = hoy or dt.datetime.now(dt.UTC).date()
    con_reviews = {
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT external_id FROM review_event WHERE platform = 'meli'"
        ).fetchall()
    }
    listings = []
    for (plat, ext), serie in sorted(_series(conn).items()):
        fecha, rating, count = serie[0]
        previo = _previo(serie, hoy, plat == "meli")
        if plat == "meli":
            texto = "verificado" if ext in con_reviews else "sin-reviews"
        else:
            texto = "sin-verificar"  # A.3 bloqueada: Amazon sin texto en v1
        listings.append(
            {
                "platform": plat,
                "external_id": ext,
                "rating": rating,
                "review_count": count,
                "tendencia": _tendencia(rating, previo),
                "estado_fuente": "ok" if rating is not None else "sin-datos",
                "texto": texto,
                "url": _url_allowlist(plat, ext),
                "metric_date": fecha.isoformat(),
            }
        )
    seller = conn.execute(
        "SELECT metric_date, level_id, power_seller, disputas_total, disputas_abiertas"
        " FROM seller_reputation_snapshot ORDER BY metric_date DESC, observed_at DESC"
        " LIMIT 1"
    ).fetchone()
    cuenta = (
        None
        if seller is None
        else {
            "metric_date": seller[0].isoformat(),
            "level_id": seller[1],
            "power_seller": seller[2],
            "disputas_total": seller[3],
            "disputas_abiertas": seller[4],
        }
    )
    pendientes = conn.execute(
        "SELECT DISTINCT ON (external_id, question_external_id)"
        " external_id, question_external_id, texto, observed_at"
        " FROM meli_question WHERE estado = 'UNANSWERED'"
        " ORDER BY external_id, question_external_id, observed_at DESC"
        " LIMIT %s",
        (_TOPE_PENDIENTES,),
    ).fetchall()
    total_pend = conn.execute(
        "SELECT count(*) FROM (SELECT DISTINCT external_id, question_external_id"
        " FROM meli_question WHERE estado = 'UNANSWERED') s"
    ).fetchone()[0]
    alertas = conn.execute(
        "SELECT tipo, severidad, platform, external_id, mensaje, created_at"
        " FROM reputation_alert WHERE NOT resolved"
        " ORDER BY CASE severidad WHEN 'critica' THEN 0 WHEN 'aviso' THEN 1 ELSE 2 END,"
        " created_at LIMIT %s",
        (_TOPE_ALERTAS,),
    ).fetchall()
    total_alertas = conn.execute(
        "SELECT count(*) FROM reputation_alert WHERE NOT resolved"
    ).fetchone()[0]
    reviews = conn.execute(
        "SELECT DISTINCT ON (platform, review_external_id)"
        " external_id, review_external_id, rating, titulo, texto, published_at"
        " FROM review_event WHERE platform = 'meli'"
        " ORDER BY platform, review_external_id, observed_at DESC"
        " LIMIT %s",
        (_TOPE_REVIEWS,),
    ).fetchall()
    return {
        "listings": listings,
        "cuenta_meli": cuenta,
        "preguntas_pendientes": [
            {"external_id": e, "question_external_id": q, "texto": t} for e, q, t, _o in pendientes
        ],
        "total_pendientes": total_pend,
        "alertas_abiertas": [
            {
                "tipo": t,
                "severidad": s,
                "platform": p,
                "external_id": e,
                "mensaje": m,
                "created_at": c.isoformat() if c else None,
            }
            for t, s, p, e, m, c in alertas
        ],
        "total_alertas": total_alertas,
        "reviews_recientes": [
            {
                "external_id": e,
                "review_external_id": r,
                "rating": rating,
                "titulo": tit,
                "texto": tex,
                "published_at": pub.isoformat() if pub else None,
            }
            for e, r, rating, tit, tex, pub in reviews
        ],
        "amazon_texto": "sin-verificar",
    }


@router.get("/resumen")
def resumen(conn: ConexionLectura) -> dict:
    """Resumen de reputacion para /reputacion (acta 0.5 §6)."""
    return carga_resumen(conn)
