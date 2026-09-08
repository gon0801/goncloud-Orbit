"""Reader batch de escenarios de estimacion (S5).

Separa lectura + overlay de frescura + JOIN de procedencia del camino de
persistencia en `estimacion_repository`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import psycopg

from app.estimacion_insumos import FRESHNESS_LIMIT


@dataclass(frozen=True)
class ProcedenciaRefs:
    """Timestamps/vigencias reales de oferta, fee, costo y politica.

    Se JOINean en el reader; la proyeccion S5 no inventa a partir del
    observed_at del escenario ni de pertenencia.
    """

    oferta_fetched_at: datetime | None = None
    oferta_observed_at: datetime | None = None
    fee_fees_estimated_at: datetime | None = None
    fee_observed_at: datetime | None = None
    costo_valid_from: date | None = None
    costo_valid_to: date | None = None
    politica_valid_from: date | None = None
    politica_valid_to: date | None = None


@dataclass(frozen=True)
class EscenarioLeido:
    id: int
    listing_id: int
    canal: str
    valoracion_date: date
    observed_at: datetime
    estado: str
    motivos: tuple[str, ...]
    contribucion: Decimal | None
    contribucion_pct: Decimal | None
    moneda: str | None
    componentes: list[dict[str, Any]]
    exclusiones: tuple[str, ...]
    canonical_input: dict[str, Any]
    context_fingerprint: str
    politica_version_id: int | None
    formula_version: str
    procedencia: ProcedenciaRefs | None = None


def leer_escenarios(
    conn: psycopg.Connection,
    listing_ids: list[int],
    *,
    as_of: datetime,
) -> list[EscenarioLeido]:
    """Reader batch: latest observed_at <= as_of por listing, sin N+1."""
    if not listing_ids:
        return []
    if as_of.tzinfo is None:
        raise ValueError("as_of debe incluir zona horaria")
    as_of_utc = as_of.astimezone(UTC)
    dia_as_of = as_of_utc.date()
    filas = conn.execute(
        "SELECT DISTINCT ON (e.listing_id)"
        " e.id, e.listing_id, e.canal, e.valoracion_date, e.observed_at, e.estado, e.motivos,"
        " e.contribucion, e.contribucion_pct, e.moneda, e.componentes, e.exclusiones,"
        " e.canonical_input, e.context_fingerprint, e.politica_version_id, e.formula_version,"
        " o.fetched_at, o.observed_at,"
        " f.fees_estimated_at, f.observed_at,"
        " c.valid_from, c.valid_to,"
        " p.valid_from, p.valid_to"
        " FROM estimacion_escenario e"
        " LEFT JOIN estimacion_oferta_observation o ON o.id = e.oferta_observation_id"
        " LEFT JOIN estimacion_fee_observation f ON f.id = e.fee_observation_id"
        " LEFT JOIN sku_cost c ON c.id = e.sku_cost_id"
        " LEFT JOIN estimacion_politica_version p ON p.id = e.politica_version_id"
        " WHERE e.listing_id = ANY(%s) AND e.observed_at <= %s"
        " ORDER BY e.listing_id, e.observed_at DESC",
        (listing_ids, as_of_utc),
    ).fetchall()
    resultado: list[EscenarioLeido] = []
    for f in filas:
        motivos_raw = f[6] or []
        exclusiones_raw = f[11] or []
        motivos = tuple(motivos_raw)
        estado = f[5]
        contribucion = f[7]
        contribucion_pct = f[8]
        moneda = f[9]
        componentes = list(f[10] or [])
        oferta_fetched_at = f[16]
        motivos_invalidacion: list[str] = []
        if f[3] != dia_as_of:
            motivos_invalidacion.append("valoracion_desactualizada")
        if oferta_fetched_at is not None:
            if oferta_fetched_at > as_of_utc:
                motivos_invalidacion.append("oferta_futura")
            elif as_of_utc - oferta_fetched_at > FRESHNESS_LIMIT:
                motivos_invalidacion.append("oferta_desactualizada")
        if motivos_invalidacion and estado == "disponible":
            estado = "desactualizada"
            motivos = tuple(dict.fromkeys((*motivos, *motivos_invalidacion)))
            contribucion = None
            contribucion_pct = None
            moneda = None
        procedencia = ProcedenciaRefs(
            oferta_fetched_at=f[16],
            oferta_observed_at=f[17],
            fee_fees_estimated_at=f[18],
            fee_observed_at=f[19],
            costo_valid_from=f[20],
            costo_valid_to=f[21],
            politica_valid_from=f[22],
            politica_valid_to=f[23],
        )
        resultado.append(
            EscenarioLeido(
                id=f[0],
                listing_id=f[1],
                canal=f[2],
                valoracion_date=f[3],
                observed_at=f[4],
                estado=estado,
                motivos=motivos,
                contribucion=contribucion,
                contribucion_pct=contribucion_pct,
                moneda=moneda,
                componentes=componentes,
                exclusiones=tuple(exclusiones_raw),
                canonical_input=dict(f[12] or {}),
                context_fingerprint=f[13],
                politica_version_id=f[14],
                formula_version=f[15],
                procedencia=procedencia,
            )
        )
    return resultado
