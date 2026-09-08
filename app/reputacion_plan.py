"""Planes puros de reputacion: de respuestas crudas a filas (A.2/A.4).

Partido de app/reputacion.py por el guardrail de 900 lineas (D-LEAD-A4-4):
sin red ni DB, testeable con dicts.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from dataclasses import dataclass, field

# Planes puros (sin DB ni red)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlanSnapshot:
    plataforma: str
    external_id: str
    alcance: str = "listing"
    rating: float | None = None
    review_count: int | None = None
    parent_asin: str | None = None
    fetched_at: dt.datetime | None = None
    extra: dict = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class PlanReview:
    plataforma: str
    external_id: str
    review_external_id: str
    rating: int | None = None
    titulo: str | None = None
    texto: str | None = None
    publicada: bool = True
    published_at: dt.datetime | None = None
    fetched_at: dt.datetime | None = None


def _rating_1_5(valor) -> float | None:
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return None
    return numero if 1 <= numero <= 5 else None


def _entero_no_negativo(valor) -> int | None:
    if isinstance(valor, bool):
        return None
    try:
        numero = int(valor)
    except (TypeError, ValueError):
        return None
    return numero if numero >= 0 else None


def _instante(valor) -> dt.datetime | None:
    if not isinstance(valor, str) or not valor:
        return None
    try:
        momento = dt.datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        return None
    return momento if momento.tzinfo is not None else momento.replace(tzinfo=dt.UTC)


def plan_snapshot_meli(
    item_id: str,
    item: dict,
    reviews: dict,
    fetched_at: dt.datetime,
) -> tuple[PlanSnapshot | None, list[PlanReview], Counter]:
    """Snapshot + reviews de un item MeLi (shapes E/0.3). Puro."""
    skips: Counter = Counter()
    total = (reviews.get("paging") or {}).get("total")
    review_count = _entero_no_negativo(total)
    if review_count is None and total is not None:
        skips["meli: paging.total invalido (se descarta)"] += 1
        return None, [], skips
    # E/0.3: total=0 trae avg=0; ese 0 es sin-dato, no rating.
    rating = None
    if (review_count or 0) > 0:
        rating = _rating_1_5(reviews.get("rating_average"))
        if rating is None and reviews.get("rating_average") is not None:
            skips["meli: rating_average fuera de [1,5] (NULL, no inventado)"] += 1
    extra: dict = {}
    health = item.get("health")
    if isinstance(health, (int, float)) and not isinstance(health, bool):
        extra["health"] = health
    levels = reviews.get("rating_levels")
    if isinstance(levels, dict):
        extra["levels"] = {k: v for k, v in levels.items() if _entero_no_negativo(v) is not None}
    snapshot = PlanSnapshot(
        plataforma="meli",
        external_id=item_id,
        rating=rating,
        review_count=review_count if review_count is not None else 0,
        fetched_at=fetched_at,
        extra=extra,
    )
    eventos: list[PlanReview] = []
    crudas = reviews.get("reviews")
    if not isinstance(crudas, list):
        if crudas is not None:
            skips["meli: reviews no-lista (se descarta)"] += 1
        return snapshot, [], skips
    for rv in crudas:
        if not isinstance(rv, dict) or rv.get("id") is None:
            skips["meli: review sin id (se descarta)"] += 1
            continue
        rate = _entero_no_negativo(rv.get("rate"))
        if rate is not None and not 1 <= rate <= 5:
            skips["meli: rate fuera de [1,5] (NULL, no inventado)"] += 1
            rate = None
        eventos.append(
            PlanReview(
                plataforma="meli",
                external_id=item_id,
                review_external_id=str(rv["id"]),
                rating=rate,
                titulo=rv.get("title") if isinstance(rv.get("title"), str) else None,
                texto=rv.get("content") if isinstance(rv.get("content"), str) else None,
                publicada=rv.get("status", "published") == "published",
                published_at=_instante(rv.get("date_created")),
                fetched_at=fetched_at,
            )
        )
    return snapshot, eventos, skips


def plan_snapshot_junglee(
    pedido: str,
    item: dict,
    fetched_at: dt.datetime,
    plataforma: str,
) -> tuple[PlanSnapshot | None, Counter]:
    """Snapshot Amazon desde un item junglee (E/0.2). Puro."""
    skips: Counter = Counter()
    rating = _rating_1_5(item.get("stars"))
    if rating is None and item.get("stars") is not None:
        skips["amazon: stars fuera de [1,5] o invalido (NULL)"] += 1
    review_count = _entero_no_negativo(item.get("reviewsCount"))
    if review_count is None and item.get("reviewsCount") is not None:
        skips["amazon: reviewsCount invalido (NULL)"] += 1
    padre = item.get("asin")
    return (
        PlanSnapshot(
            plataforma=plataforma,
            external_id=pedido,
            rating=rating,
            review_count=review_count,
            parent_asin=str(padre) if padre else None,
            fetched_at=fetched_at,
        ),
        skips,
    )


# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PlanSeller:
    level_id: str | None = None
    power_seller: str | None = None
    tx_total: int | None = None
    tx_completed: int | None = None
    tx_canceled: int | None = None
    ratings_positive: int | None = None
    ratings_neutral: int | None = None
    ratings_negative: int | None = None
    disputas_total: int | None = None
    disputas_abiertas: int | None = None
    fetched_at: dt.datetime | None = None


@dataclass(frozen=True)
class PlanQuestion:
    external_id: str
    question_external_id: str
    estado: str
    texto: str | None = None
    respuesta: str | None = None
    asked_at: dt.datetime | None = None
    answered_at: dt.datetime | None = None
    fetched_at: dt.datetime | None = None


def plan_seller(rep: dict, disputas: list[dict], fetched_at: dt.datetime) -> PlanSeller:
    """Reputacion de cuenta MeLi (shape E/0.3). Puro."""
    tx = rep.get("transactions") or {}
    ratings = tx.get("ratings") or {}
    abiertas = sum(1 for d in disputas if isinstance(d, dict) and d.get("status") != "closed")
    return PlanSeller(
        level_id=rep.get("level_id") if isinstance(rep.get("level_id"), str) else None,
        power_seller=rep.get("power_seller_status")
        if isinstance(rep.get("power_seller_status"), str)
        else None,
        tx_total=_entero_no_negativo(tx.get("total")),
        tx_completed=_entero_no_negativo(tx.get("completed")),
        tx_canceled=_entero_no_negativo(tx.get("canceled")),
        ratings_positive=_entero_no_negativo(ratings.get("positive")),
        ratings_neutral=_entero_no_negativo(ratings.get("neutral")),
        ratings_negative=_entero_no_negativo(ratings.get("negative")),
        disputas_total=len(disputas),
        disputas_abiertas=abiertas,
        fetched_at=fetched_at,
    )


_ESTADOS_PREGUNTA = ("ANSWERED", "UNANSWERED")


def plan_question(item_id: str, pregunta: dict, fetched_at: dt.datetime) -> PlanQuestion | None:
    """Una pregunta MeLi (shape E/0.3). Puro. Estado desconocido = None."""
    if pregunta.get("id") is None:
        return None
    estado = pregunta.get("status")
    if estado not in _ESTADOS_PREGUNTA:
        return None  # revienta visible en el sync via skip, no se cuela
    respuesta = pregunta.get("answer") or {}
    return PlanQuestion(
        external_id=item_id,
        question_external_id=str(pregunta["id"]),
        estado=estado,
        texto=pregunta.get("text") if isinstance(pregunta.get("text"), str) else None,
        respuesta=respuesta.get("text") if isinstance(respuesta.get("text"), str) else None,
        asked_at=_instante(pregunta.get("date_created")),
        answered_at=_instante(respuesta.get("date_created")),
        fetched_at=fetched_at,
    )
