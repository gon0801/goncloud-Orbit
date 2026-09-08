"""Repositorio MARGEN ESTIMADO 01 A.4 — persistencia y lectura batch de escenarios.

Carga politica/costo/FX via `app.estimacion_insumos`, construye entrada congelada,
invoca calculador puro y persiste append-only en `estimacion_escenario`.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import psycopg
from psycopg.types.json import Json

from app.estimacion_insumos import FRESHNESS_LIMIT, resolver_costo, resolver_fx
from app.estimacion_venta import (
    ESTADOS_DESACTUALIZADA,
    EXCLUSIONES_FIJAS,
    DetalleFee,
    EntradaCalculo,
    PoliticaCalculo,
    ResultadoCalculo,
    calcular_contribucion,
    validar_dinero,
)


@dataclass(frozen=True)
class ResultadoPolitica:
    motivo: str | None
    politica: PoliticaCalculo | None


@dataclass(frozen=True)
class CostoFxEscenario:
    sku_cost_id: int | None = None
    amount: Decimal | None = None
    currency: str | None = None
    includes_tax: bool | None = None
    validation_run_id: int | None = None
    validated_at: datetime | None = None
    fx_rate: Decimal | None = None
    fx_rate_date: date | None = None
    fx_base: str | None = None
    fx_quote: str | None = None
    fx_source: str | None = None


@dataclass(frozen=True)
class EscenarioPersistible:
    listing_id: int
    platform: str
    seller_sku: str
    asin: str
    canal: str
    valoracion_date: date
    observed_at: datetime
    politica_version_id: int | None
    formula_version: str
    oferta_observation_id: int | None
    fee_observation_id: int | None
    sku_cost_id: int | None
    costo_validation_run_id: int | None
    costo_validated_at: datetime | None
    fx_rate_date: date | None
    fx_base: str | None
    fx_quote: str | None
    fx_rate: Decimal | None
    fx_source: str | None
    moneda: str | None
    contribucion: Decimal | None
    contribucion_pct: Decimal | None
    estado: str
    motivos: tuple[str, ...]
    componentes: list[dict[str, Any]]
    exclusiones: tuple[str, ...]
    canonical_input: dict[str, Any]
    context_fingerprint: str
    source_event_id: str


@dataclass(frozen=True)
class ResultadoPersistenciaEscenario:
    id: int
    observed_at: datetime
    reutilizada: bool


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


def resolver_politica_aplicable(
    conn: psycopg.Connection,
    *,
    fecha: date,
    universo: str,
    observed_at: datetime,
) -> ResultadoPolitica:
    """Politica vigente en fecha/universo con created_at <= observed_at."""
    existe = conn.execute(
        "SELECT 1 FROM estimacion_politica_version WHERE universo = %s LIMIT 1",
        (universo,),
    ).fetchone()
    if not existe:
        return ResultadoPolitica(motivo="politica_ausente", politica=None)

    filas = conn.execute(
        "SELECT id, created_at, label, universo, formula_version, settings,"
        " valid_from, valid_to"
        " FROM estimacion_politica_version"
        " WHERE universo = %s"
        " AND valid_from <= %s"
        " AND (valid_to IS NULL OR valid_to > %s)"
        " AND created_at <= %s"
        " ORDER BY valid_from DESC, id DESC",
        (universo, fecha, fecha, observed_at),
    ).fetchall()
    if not filas:
        return ResultadoPolitica(motivo="politica_no_vigente", politica=None)
    if len(filas) > 1:
        return ResultadoPolitica(motivo="politica_ambigua", politica=None)
    f = filas[0]
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


def _parse_decimal_estricto(raw: Any, *, campo: str) -> Decimal:
    if raw is None:
        raise ValueError(f"{campo} ausente")
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{campo} invalido") from exc


def _parse_detalle_fee(raw: Any) -> DetalleFee:
    if not isinstance(raw, dict):
        raise ValueError("fee_detalle no es objeto")
    fee_type = raw.get("fee_type")
    if not isinstance(fee_type, str) or not fee_type.strip():
        raise ValueError("fee_type invalido")
    final_fee = _parse_decimal_estricto(raw.get("final_fee"), campo="final_fee")
    validar_dinero(final_fee, campo="final_fee", positivo=False, permitir_cero=True)
    tax_raw = raw.get("tax_amount")
    tax_amount: Decimal | None = None
    if tax_raw is not None:
        tax_amount = _parse_decimal_estricto(tax_raw, campo="tax_amount")
        validar_dinero(tax_amount, campo="tax_amount", positivo=False, permitir_cero=True)
    incluidos_raw = raw.get("included_fee_details")
    if incluidos_raw is None:
        incluidos_raw = []
    if not isinstance(incluidos_raw, list):
        raise ValueError("included_fee_details invalido")
    incluidos = tuple(_parse_detalle_fee(inc) for inc in incluidos_raw)
    return DetalleFee(
        fee_type=fee_type,
        final_fee=final_fee,
        tax_amount=tax_amount,
        included_fee_details=incluidos,
    )


def _detalles_desde_json(fee_details: Any) -> tuple[DetalleFee, ...]:
    if fee_details is None:
        return ()
    if not isinstance(fee_details, list):
        raise ValueError("fee_details no es lista")
    return tuple(_parse_detalle_fee(raw) for raw in fee_details)


def _componentes_a_json(resultado: ResultadoCalculo) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for c in resultado.componentes:
        item: dict[str, Any] = {"nombre": c.nombre, "pertenece_a_total": c.pertenece_a_total}
        if c.importe_original is not None:
            item["importe_original"] = str(c.importe_original)
        if c.moneda_original is not None:
            item["moneda_original"] = c.moneda_original
        if c.importe_normalizado is not None:
            item["importe_normalizado"] = str(c.importe_normalizado)
        if c.moneda_normalizada is not None:
            item["moneda_normalizada"] = c.moneda_normalizada
        if c.tasa is not None:
            item["tasa"] = str(c.tasa)
        if c.fecha is not None:
            item["fecha"] = c.fecha.isoformat()
        if c.fuente is not None:
            item["fuente"] = c.fuente
        out.append(item)
    return out


def _detalle_fee_a_json(detalle: DetalleFee) -> dict[str, Any]:
    return {
        "fee_type": detalle.fee_type,
        "final_fee": str(detalle.final_fee),
        "tax_amount": str(detalle.tax_amount) if detalle.tax_amount is not None else None,
        "included_fee_details": [
            _detalle_fee_a_json(incluido) for incluido in detalle.included_fee_details
        ],
    }


def _construir_source_event_id(canonical: dict[str, Any]) -> str:
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode()).hexdigest()[:32]
    return f"escenario:{digest}"


def _canonical_frozen(
    *,
    oferta_observation_id: int | None,
    fee_observation_id: int | None,
    sku_cost_id: int | None,
    costo_validation_run_id: int | None,
    costo_validated_at: datetime | None,
    costo_includes_tax: bool | None,
    politica_version_id: int | None,
    valoracion_date: date,
    context_fingerprint: str,
    formula_version: str,
    fx_rate: Decimal | None,
    fx_rate_date: date | None,
    fx_base: str | None,
    fx_quote: str | None,
    fx_source: str | None,
    motivos: tuple[str, ...],
    resultado: ResultadoCalculo,
    entrada_snapshot: dict[str, Any],
) -> dict[str, Any]:
    return {
        "oferta_observation_id": oferta_observation_id,
        "fee_observation_id": fee_observation_id,
        "sku_cost_id": sku_cost_id,
        "costo_validation_run_id": costo_validation_run_id,
        "costo_validated_at": (
            costo_validated_at.isoformat() if costo_validated_at is not None else None
        ),
        "costo_includes_tax": costo_includes_tax,
        "politica_version_id": politica_version_id,
        "valoracion_date": valoracion_date.isoformat(),
        "context_fingerprint": context_fingerprint,
        "formula_version": formula_version,
        "fx_rate": str(fx_rate) if fx_rate is not None else None,
        "fx_rate_date": fx_rate_date.isoformat() if fx_rate_date is not None else None,
        "fx_base": fx_base,
        "fx_quote": fx_quote,
        "fx_source": fx_source,
        "motivos": list(motivos),
        "entrada": entrada_snapshot,
        "resultado": {
            "estado": resultado.estado,
            "contribucion": (
                str(resultado.contribucion) if resultado.contribucion is not None else None
            ),
            "contribucion_pct": (
                str(resultado.contribucion_pct) if resultado.contribucion_pct is not None else None
            ),
        },
    }


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


def _validar_fee_compatible(
    oferta: dict[str, Any],
    fee: dict[str, Any],
    *,
    escenario_observed_at: datetime,
) -> str | None:
    if fee["oferta_observation_id"] != oferta.get("oferta_observation_id"):
        return "fee_incompatible"
    campos = (
        ("listing_id", "listing_id"),
        ("platform", "platform"),
        ("seller_sku", "seller_sku"),
        ("asin", "asin"),
        ("canal", "canal"),
    )
    for k1, k2 in campos:
        if oferta.get(k1) != fee.get(k2):
            return "fee_incompatible"
    if oferta["context_fingerprint"] != fee["context_fingerprint"]:
        return "fee_incompatible"
    if fee["quoted_price_amount"] != oferta["price_amount"]:
        return "fee_incompatible"
    if fee["quoted_price_currency"] != oferta["price_currency"]:
        return "fee_incompatible"
    if fee["estado"] != "success":
        return fee.get("error_code") or "fee_ausente"
    if fee["fees_estimated_at"] is not None and fee["fees_estimated_at"] < oferta["fetched_at"]:
        return "fee_incompatible"
    if oferta["observed_at"] > escenario_observed_at:
        return "fee_incompatible"
    if fee["observed_at"] > escenario_observed_at:
        return "fee_incompatible"
    return None


def _resolver_costo_fx_escenario(
    conn: psycopg.Connection,
    *,
    product_id: int,
    valoracion_date: date,
    observed_at: datetime,
    moneda_venta: str | None,
    motivos: list[str],
) -> CostoFxEscenario:
    costo_res = resolver_costo(
        conn,
        product_id=product_id,
        fecha_escenario=valoracion_date,
        now_utc=observed_at,
    )
    if costo_res.motivo:
        motivos.append(costo_res.motivo)
        return CostoFxEscenario()
    if costo_res.costo is None:
        return CostoFxEscenario()

    costo = costo_res.costo
    valores: dict[str, Any] = {
        "sku_cost_id": costo.sku_cost_id,
        "amount": costo.cost_amount,
        "currency": costo.cost_currency,
        "includes_tax": costo.includes_tax,
        "validation_run_id": costo.validation_run_id,
        "validated_at": costo.validated_at,
    }
    if moneda_venta and costo.cost_currency != moneda_venta:
        fx_res = resolver_fx(
            conn,
            fecha=valoracion_date,
            base=moneda_venta,
            quote=costo.cost_currency,
        )
        if fx_res.motivo:
            motivos.append(fx_res.motivo)
        elif fx_res.fx is not None and fx_res.fx.rate is not None:
            valores.update(
                fx_rate=fx_res.fx.rate,
                fx_rate_date=fx_res.fx.rate_date,
                fx_base=fx_res.fx.base,
                fx_quote=fx_res.fx.quote,
                fx_source=fx_res.fx.source,
            )
    return CostoFxEscenario(**valores)


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
    fee_raw: dict[str, Any] | None = None

    product_id = conn.execute(
        "SELECT product_id FROM listing WHERE id = %s",
        (listing_id,),
    ).fetchone()
    if product_id is None:
        raise ValueError(f"listing {listing_id} inexistente")
    product_id = product_id[0]

    universo = f"{oferta_raw['platform']}/{oferta_raw['canal']}"
    pol_result = resolver_politica_aplicable(
        conn,
        fecha=valoracion_date,
        universo=universo,
        observed_at=observed_at,
    )
    politica: PoliticaCalculo | None = pol_result.politica
    if pol_result.motivo:
        motivos.append(pol_result.motivo)

    if fee_observation_id is not None:
        fee_raw = _cargar_fee(conn, fee_observation_id)
        if fee_raw is None:
            motivos.append("fee_ausente")
        else:
            motivo_fee = _validar_fee_compatible(
                oferta_raw, fee_raw, escenario_observed_at=observed_at
            )
            if motivo_fee:
                motivos.append(motivo_fee)
    else:
        motivos.append("fee_ausente")

    costo_fx = _resolver_costo_fx_escenario(
        conn,
        product_id=product_id,
        valoracion_date=valoracion_date,
        observed_at=observed_at,
        moneda_venta=oferta_raw["price_currency"],
        motivos=motivos,
    )
    moneda_venta = oferta_raw["price_currency"]

    fee_total = fee_raw["total_fees"] if fee_raw else None
    fee_estado = fee_raw["estado"] if fee_raw else None
    fee_detalles: tuple[DetalleFee, ...] = ()
    if fee_raw is not None:
        try:
            fee_detalles = _detalles_desde_json(fee_raw["fee_details"])
        except ValueError:
            motivos.append("fee_incompatible")

    entrada = EntradaCalculo(
        precio_bruto=oferta_raw["price_amount"],
        precio_moneda=oferta_raw["price_currency"],
        costo_original=costo_fx.amount,
        costo_moneda=costo_fx.currency,
        fee_total=fee_total,
        costo_includes_tax=costo_fx.includes_tax,
        fee_detalles=fee_detalles,
        fee_estado=fee_estado,
        fx_rate=costo_fx.fx_rate,
        fx_rate_date=costo_fx.fx_rate_date,
        fx_base=costo_fx.fx_base,
        fx_quote=costo_fx.fx_quote,
        fx_source=costo_fx.fx_source,
        moneda_base=moneda_venta,
        motivos_entrada=tuple(dict.fromkeys(motivos)),
    )

    if politica is None:
        if "identidad_ambigua" in motivos:
            estado_sin_pol = "identidad_ambigua"
        elif any(m in ESTADOS_DESACTUALIZADA for m in motivos):
            estado_sin_pol = "desactualizada"
        else:
            estado_sin_pol = "incompleta"
        resultado = ResultadoCalculo(
            estado=estado_sin_pol,
            motivos=tuple(dict.fromkeys(motivos)),
            contribucion=None,
            contribucion_pct=None,
            moneda=None,
            componentes=(),
            exclusiones=EXCLUSIONES_FIJAS,
        )
        formula_version = "S3"
        politica_version_id: int | None = None
    else:
        resultado = calcular_contribucion(
            entrada,
            politica,
            valoracion_date=valoracion_date,
            universo_esperado=universo,
        )
        formula_version = politica.formula_version
        politica_version_id = politica.politica_version_id

    motivos_finales = resultado.motivos if resultado.motivos else tuple(dict.fromkeys(motivos))
    entrada_snapshot = {
        "precio_bruto": str(oferta_raw["price_amount"]),
        "precio_moneda": oferta_raw["price_currency"],
        "costo_original": str(costo_fx.amount) if costo_fx.amount is not None else None,
        "costo_moneda": costo_fx.currency,
        "costo_includes_tax": costo_fx.includes_tax,
        "costo_validation_run_id": costo_fx.validation_run_id,
        "costo_validated_at": (
            costo_fx.validated_at.isoformat() if costo_fx.validated_at is not None else None
        ),
        "fee_total": str(fee_total) if fee_total is not None else None,
        "fee_estado": fee_estado,
        "fee_detalles": [_detalle_fee_a_json(detalle) for detalle in fee_detalles],
        "fx_rate": str(costo_fx.fx_rate) if costo_fx.fx_rate is not None else None,
        "fx_rate_date": (
            costo_fx.fx_rate_date.isoformat() if costo_fx.fx_rate_date is not None else None
        ),
        "fx_base": costo_fx.fx_base,
        "fx_quote": costo_fx.fx_quote,
        "fx_source": costo_fx.fx_source,
        "motivos_entrada": list(motivos_finales),
    }
    id_canonica = _canonical_frozen(
        oferta_observation_id=oferta_observation_id,
        fee_observation_id=fee_observation_id,
        sku_cost_id=costo_fx.sku_cost_id,
        costo_validation_run_id=costo_fx.validation_run_id,
        costo_validated_at=costo_fx.validated_at,
        costo_includes_tax=costo_fx.includes_tax,
        politica_version_id=politica_version_id,
        valoracion_date=valoracion_date,
        context_fingerprint=oferta_raw["context_fingerprint"],
        formula_version=formula_version,
        fx_rate=costo_fx.fx_rate,
        fx_rate_date=costo_fx.fx_rate_date,
        fx_base=costo_fx.fx_base,
        fx_quote=costo_fx.fx_quote,
        fx_source=costo_fx.fx_source,
        motivos=motivos_finales,
        resultado=resultado,
        entrada_snapshot=entrada_snapshot,
    )
    canonical = {
        **id_canonica,
        "observed_at": observed_at.isoformat(),
    }

    return EscenarioPersistible(
        listing_id=listing_id,
        platform=oferta_raw["platform"],
        seller_sku=oferta_raw["seller_sku"],
        asin=oferta_raw["asin"],
        canal=oferta_raw["canal"],
        valoracion_date=valoracion_date,
        observed_at=observed_at,
        politica_version_id=politica_version_id,
        formula_version=formula_version,
        oferta_observation_id=oferta_observation_id,
        fee_observation_id=fee_observation_id,
        sku_cost_id=costo_fx.sku_cost_id,
        costo_validation_run_id=costo_fx.validation_run_id,
        costo_validated_at=costo_fx.validated_at,
        fx_rate_date=costo_fx.fx_rate_date,
        fx_base=costo_fx.fx_base,
        fx_quote=costo_fx.fx_quote,
        fx_rate=costo_fx.fx_rate,
        fx_source=costo_fx.fx_source,
        moneda=resultado.moneda,
        contribucion=resultado.contribucion,
        contribucion_pct=resultado.contribucion_pct,
        estado=resultado.estado,
        motivos=motivos_finales,
        componentes=_componentes_a_json(resultado),
        exclusiones=resultado.exclusiones,
        canonical_input=canonical,
        context_fingerprint=oferta_raw["context_fingerprint"],
        source_event_id=_construir_source_event_id(id_canonica),
    )


def persistir_escenario(
    conn: psycopg.Connection,
    escenario: EscenarioPersistible,
) -> ResultadoPersistenciaEscenario:
    """INSERT idempotente por source_event_id."""
    motivos_json = list(escenario.motivos)
    fila = conn.execute(
        "INSERT INTO estimacion_escenario"
        " (listing_id, platform, seller_sku, asin, canal, valoracion_date, observed_at,"
        " politica_version_id, formula_version, oferta_observation_id, fee_observation_id,"
        " sku_cost_id, costo_validation_run_id, costo_validated_at,"
        " fx_rate_date, fx_base, fx_quote, fx_rate, fx_source,"
        " moneda, contribucion, contribucion_pct, estado, motivos, componentes,"
        " exclusiones, canonical_input, context_fingerprint, source_event_id)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
        " %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
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
            Json(motivos_json),
            Json(escenario.componentes),
            Json(list(escenario.exclusiones)),
            Json(escenario.canonical_input),
            escenario.context_fingerprint,
            escenario.source_event_id,
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


def _ultimo_contexto_fba_mx(
    conn: psycopg.Connection,
    listing_id: int,
    *,
    as_of: datetime,
) -> dict[str, Any] | None:
    """Ultimo escenario amazon_mx/fba observado hasta as_of; None si no hubo contexto."""
    fila = conn.execute(
        "SELECT canal, context_fingerprint, platform, seller_sku, asin, source_event_id"
        " FROM estimacion_escenario"
        " WHERE listing_id = %s AND platform = 'amazon_mx' AND canal = 'fba'"
        " AND estado = 'disponible'"
        " AND observed_at <= %s"
        " ORDER BY observed_at DESC LIMIT 1",
        (listing_id, as_of),
    ).fetchone()
    if fila is None:
        return None
    return {
        "canal": fila[0],
        "context_fingerprint": fila[1],
        "platform": fila[2],
        "seller_sku": fila[3],
        "asin": fila[4],
        "escenario_base_source_event_id": fila[5],
    }


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

    Solo si el listing ya tuvo contexto FBA MX. source_event_id estable por huella de snapshot.
    """
    motivos_admitidos = {
        "oferta_ausente",
        "identidad_ambigua",
        "oferta_futura",
        "oferta_desactualizada",
        "logistica_fbm_pendiente",
    }
    if motivo not in motivos_admitidos:
        raise ValueError(f"motivo no soportado para transicion negativa: {motivo}")

    contexto = _ultimo_contexto_fba_mx(conn, listing_id, as_of=observed_at)
    if contexto is None:
        return None

    if motivo == "identidad_ambigua":
        estado = "identidad_ambigua"
    elif motivo in ("oferta_futura", "oferta_desactualizada"):
        estado = "desactualizada"
    else:
        estado = "incompleta"
    motivos = (motivo,)
    formula_version = "S3"
    id_canonica = {
        "tipo": "ausencia_bridge",
        "listing_id": listing_id,
        "motivo": motivo,
        "snapshot_huella": snapshot_huella,
        "valoracion_date": valoracion_date.isoformat(),
        "context_fingerprint": contexto["context_fingerprint"],
        "escenario_base_source_event_id": contexto["escenario_base_source_event_id"],
        "formula_version": formula_version,
    }
    canonical = {
        **id_canonica,
        "observed_at": observed_at.isoformat(),
        "motivos": list(motivos),
        "entrada": None,
        "resultado": {"estado": estado, "contribucion": None, "contribucion_pct": None},
    }
    resultado_stub = ResultadoCalculo(
        estado=estado,
        motivos=motivos,
        contribucion=None,
        contribucion_pct=None,
        moneda=None,
        componentes=(),
        exclusiones=EXCLUSIONES_FIJAS,
    )
    return EscenarioPersistible(
        listing_id=listing_id,
        platform=contexto["platform"],
        seller_sku=contexto["seller_sku"],
        asin=contexto["asin"],
        canal=contexto["canal"],
        valoracion_date=valoracion_date,
        observed_at=observed_at,
        politica_version_id=None,
        formula_version=formula_version,
        oferta_observation_id=None,
        fee_observation_id=None,
        sku_cost_id=None,
        costo_validation_run_id=None,
        costo_validated_at=None,
        fx_rate_date=None,
        fx_base=None,
        fx_quote=None,
        fx_rate=None,
        fx_source=None,
        moneda=None,
        contribucion=None,
        contribucion_pct=None,
        estado=estado,
        motivos=motivos,
        componentes=_componentes_a_json(resultado_stub),
        exclusiones=resultado_stub.exclusiones,
        canonical_input=canonical,
        context_fingerprint=contexto["context_fingerprint"],
        source_event_id=_construir_source_event_id(id_canonica),
    )


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
