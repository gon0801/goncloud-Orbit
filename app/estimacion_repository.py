"""Repositorio MARGEN ESTIMADO 01 A.4 — persistencia de escenarios.

Carga politica/oferta/fee, delega la siembra unificada a `app.estimacion_siembra`
y persiste append-only en `estimacion_escenario`.
La lectura batch vive en `app.estimacion_reader` (reexportada aqui).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import psycopg
from psycopg.types.json import Json

from app.estimacion_insumos import FRESHNESS_LIMIT
from app.estimacion_reader import (
    EscenarioLeido,
    ProcedenciaRefs,
    leer_escenarios,
    ultimo_contexto,
)
from app.estimacion_siembra import (
    CostoFxEscenario,
    EscenarioPersistible,
    sembrar_desde_insumos,
    sembrar_negativo_desde_contexto,
    validar_fee_compatible,
)
from app.estimacion_universo import universo_de
from app.estimacion_venta import PoliticaCalculo

_ = (CostoFxEscenario, EscenarioLeido, ProcedenciaRefs, leer_escenarios)


@dataclass(frozen=True)
class ResultadoPolitica:
    motivo: str | None
    politica: PoliticaCalculo | None


@dataclass(frozen=True)
class ResultadoPersistenciaEscenario:
    id: int
    observed_at: datetime
    reutilizada: bool


def resolver_politica_aplicable(
    conn: psycopg.Connection,
    *,
    fecha: date,
    universo: str,
    observed_at: datetime,
) -> ResultadoPolitica:
    """Politica vigente en fecha/universo con created_at <= observed_at."""
    filas = conn.execute(
        "SELECT id, created_at, label, universo, formula_version, settings,"
        " valid_from, valid_to,"
        " (valid_from <= %s AND (valid_to IS NULL OR valid_to > %s)"
        "  AND created_at <= %s) AS vigente"
        " FROM estimacion_politica_version"
        " WHERE universo = %s"
        " ORDER BY vigente DESC, valid_from DESC, id DESC",
        (fecha, fecha, observed_at, universo),
    ).fetchall()
    if not filas:
        return ResultadoPolitica(motivo="politica_ausente", politica=None)
    vigentes = [f for f in filas if f[8]]
    if not vigentes:
        return ResultadoPolitica(motivo="politica_no_vigente", politica=None)
    if len(vigentes) > 1:
        return ResultadoPolitica(motivo="politica_ambigua", politica=None)
    f = vigentes[0]
    politica = PoliticaCalculo(
        politica_version_id=f[0],
        label=f[2],
        universo=f[3],
        formula_version=f[4],
        settings=dict(f[5]) if f[5] is not None else {},
        valid_from=f[6],
        valid_to=f[7],
        created_at=f[1],
    )
    return ResultadoPolitica(motivo=None, politica=politica)


def _cargar_oferta(conn: psycopg.Connection, oferta_id: int) -> dict[str, Any] | None:
    fila = conn.execute(
        "SELECT listing_id, platform, seller_sku, asin, canal, price_amount, price_currency,"
        " fetched_at, observed_at, context_fingerprint, canonical_input"
        " FROM estimacion_oferta_observation WHERE id = %s",
        (oferta_id,),
    ).fetchone()
    if fila is None:
        return None
    return {
        "listing_id": fila[0],
        "platform": fila[1],
        "seller_sku": fila[2],
        "asin": fila[3],
        "canal": fila[4],
        "price_amount": fila[5],
        "price_currency": fila[6],
        "fetched_at": fila[7],
        "observed_at": fila[8],
        "context_fingerprint": fila[9],
        "canonical_input": fila[10],
    }


def _cargar_fee(conn: psycopg.Connection, fee_id: int) -> dict[str, Any] | None:
    fila = conn.execute(
        "SELECT oferta_observation_id, listing_id, platform, seller_sku, asin, canal,"
        " quoted_price_amount, quoted_price_currency, total_fees, fee_details, estado,"
        " error_code, fees_estimated_at, fetched_at, observed_at, context_fingerprint"
        " FROM estimacion_fee_observation WHERE id = %s",
        (fee_id,),
    ).fetchone()
    if fila is None:
        return None
    return {
        "oferta_observation_id": fila[0],
        "listing_id": fila[1],
        "platform": fila[2],
        "seller_sku": fila[3],
        "asin": fila[4],
        "canal": fila[5],
        "quoted_price_amount": fila[6],
        "quoted_price_currency": fila[7],
        "total_fees": fila[8],
        "fee_details": fila[9],
        "estado": fila[10],
        "error_code": fila[11],
        "fees_estimated_at": fila[12],
        "fetched_at": fila[13],
        "observed_at": fila[14],
        "context_fingerprint": fila[15],
    }


def sembrar_escenario_desde_refs(
    conn: psycopg.Connection,
    *,
    listing_id: int,
    oferta_observation_id: int,
    fee_observation_id: int | None,
    valoracion_date: date,
    observed_at: datetime,
) -> EscenarioPersistible:
    """Construye escenario congelado desde refs de oferta/fee/costo/FX/politica."""
    oferta_raw = _cargar_oferta(conn, oferta_observation_id)
    if oferta_raw is None:
        raise ValueError(f"oferta {oferta_observation_id} inexistente")
    oferta_raw["oferta_observation_id"] = oferta_observation_id

    motivos: list[str] = []
    if oferta_raw["fetched_at"] > observed_at:
        motivos.append("oferta_futura")
    elif observed_at - oferta_raw["fetched_at"] > FRESHNESS_LIMIT:
        motivos.append("oferta_desactualizada")

    fila_producto = conn.execute(
        "SELECT product_id FROM listing WHERE id = %s",
        (listing_id,),
    ).fetchone()
    if fila_producto is None:
        raise ValueError(f"listing {listing_id} inexistente")
    product_id = fila_producto[0]

    universo = f"{oferta_raw['platform']}/{oferta_raw['canal']}"
    uni = universo_de(oferta_raw["platform"], oferta_raw["canal"])
    if uni is None:
        motivos.append("universo_no_soportado")
    pol_result = resolver_politica_aplicable(
        conn,
        fecha=valoracion_date,
        universo=universo,
        observed_at=observed_at,
    )
    if pol_result.motivo:
        motivos.append(pol_result.motivo)

    fee_raw: dict[str, Any] | None = None
    if fee_observation_id is not None:
        fee_raw = _cargar_fee(conn, fee_observation_id)
        if fee_raw is None:
            motivos.append("fee_ausente")
        else:
            motivo_fee = validar_fee_compatible(
                oferta_raw, fee_raw, escenario_observed_at=observed_at
            )
            if motivo_fee:
                motivos.append(motivo_fee)
    else:
        motivos.append("fee_ausente")

    return sembrar_desde_insumos(
        conn,
        listing_id=listing_id,
        product_id=product_id,
        oferta_raw=oferta_raw,
        fee_raw=fee_raw,
        fee_observation_id=fee_observation_id,
        universo=universo,
        uni=uni,
        politica=pol_result.politica,
        motivos=motivos,
        valoracion_date=valoracion_date,
        observed_at=observed_at,
    )


def persistir_escenario(
    conn: psycopg.Connection,
    escenario: EscenarioPersistible,
) -> ResultadoPersistenciaEscenario:
    """INSERT idempotente por source_event_id."""
    fila = conn.execute(
        "INSERT INTO estimacion_escenario"
        " (listing_id, platform, seller_sku, asin, canal, valoracion_date, observed_at,"
        " politica_version_id, formula_version, oferta_observation_id, fee_observation_id,"
        " sku_cost_id, costo_validation_run_id, costo_validated_at,"
        " fx_rate_date, fx_base, fx_quote, fx_rate, fx_source,"
        " moneda, contribucion, contribucion_pct, estado, motivos, componentes,"
        " exclusiones, canonical_input, context_fingerprint, source_event_id,"
        " envio_muestra_id, logistica_origen, miembro_product_id, fee_variable,"
        " fee_fijo, fee_cotizable)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
        " %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
        " ON CONFLICT (source_event_id) DO NOTHING"
        " RETURNING id, observed_at",
        (
            escenario.listing_id,
            escenario.platform,
            escenario.seller_sku,
            escenario.asin,
            escenario.canal,
            escenario.valoracion_date,
            escenario.observed_at,
            escenario.politica_version_id,
            escenario.formula_version,
            escenario.oferta_observation_id,
            escenario.fee_observation_id,
            escenario.sku_cost_id,
            escenario.costo_validation_run_id,
            escenario.costo_validated_at,
            escenario.fx_rate_date,
            escenario.fx_base,
            escenario.fx_quote,
            escenario.fx_rate,
            escenario.fx_source,
            escenario.moneda,
            escenario.contribucion,
            escenario.contribucion_pct,
            escenario.estado,
            Json(list(escenario.motivos)),
            Json(escenario.componentes),
            Json(list(escenario.exclusiones)),
            Json(escenario.canonical_input),
            escenario.context_fingerprint,
            escenario.source_event_id,
            escenario.envio_muestra_id,
            escenario.logistica_origen,
            escenario.miembro_product_id,
            escenario.fee_variable,
            escenario.fee_fijo,
            escenario.fee_cotizable,
        ),
    ).fetchone()
    if fila is not None:
        return ResultadoPersistenciaEscenario(id=fila[0], observed_at=fila[1], reutilizada=False)
    existente = conn.execute(
        "SELECT id, observed_at FROM estimacion_escenario WHERE source_event_id = %s",
        (escenario.source_event_id,),
    ).fetchone()
    if existente is None:
        raise ValueError(
            f"conflicto sin fila previa para source_event_id={escenario.source_event_id}"
        )
    return ResultadoPersistenciaEscenario(
        id=existente[0], observed_at=existente[1], reutilizada=True
    )


def sembrar_escenario_negativo_transicion(
    conn: psycopg.Connection,
    *,
    listing_id: int,
    motivo: str,
    snapshot_huella: str,
    valoracion_date: date,
    observed_at: datetime,
) -> EscenarioPersistible | None:
    """Escenario append-only sin oferta/fee/costo/politica cuando cae ausencia/ambiguedad.

    Solo si el listing ya tuvo contexto disponible. source_event_id estable
    por huella de snapshot.
    """
    contexto = ultimo_contexto(conn, listing_id, as_of=observed_at)
    if contexto is None:
        return None
    return sembrar_negativo_desde_contexto(
        listing_id=listing_id,
        contexto=contexto,
        motivo=motivo,
        snapshot_huella=snapshot_huella,
        valoracion_date=valoracion_date,
        observed_at=observed_at,
    )
