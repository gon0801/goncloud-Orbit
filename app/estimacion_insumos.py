"""Insumos MARGEN ESTIMADO 01 A.2 — oferta fechada, costo y FX existentes.

Fuente oferta: snapshot SQLite READ-ONLY del bridge (`amazon_listing_prices`).
Destino oferta: `estimacion_oferta_observation` (append-only, dedupe por evento).
Costo/FX: lectura de `sku_cost` + `ingest_run` y funcion sellada `fx_resolve`.

Universo v1: FBA Amazon MX persistible; FBM/US excluidos con motivo explicito.
Sin OAuth, HTTP, scheduler, Product Fees ni calculo economico (A.3/A.4).
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
from psycopg.types.json import Json

from app.optimizer.bid import PLATAFORMAS_MONEDA

SOURCE_BRIDGE = "bridge_amazon_listing_prices"
SOURCE_COSTOS = "accounting_sku_costs"

MARKETPLACE_PLATAFORMA: dict[str, str] = {
    "A1AM78C64UM0Y8": "amazon_mx",
    "ATVPDKIKX0DER": "amazon_us",
}

CANAL_POR_FULFILLMENT: dict[str, str] = {
    "AMAZON_NA": "fba",
    "DEFAULT": "fbm",
}

FRESHNESS_LIMIT = timedelta(hours=6)

_MAX_DECIMALES = Decimal("0.0001")
_RUIDO_FLOAT = Decimal("0.00001")
_MAX_PRECIO = Decimal(10) ** 10


class InsumosError(Exception):
    """Error de lectura del snapshot bridge."""


@dataclass(frozen=True)
class FilaOfertaBridge:
    """Fila de amazon_listing_prices tal cual viene del bridge."""

    seller_sku: str
    asin: str | None
    marketplace_id: str
    marketplace_name: str | None
    price: float | None
    fulfillment_channel: str | None
    fetched_at: str | None


@dataclass(frozen=True)
class OfertaResuelta:
    """Oferta FBA MX fresca lista para persistir."""

    listing_id: int
    platform: str
    seller_sku: str
    asin: str
    canal: str
    price_amount: Decimal
    price_currency: str
    fetched_at: datetime
    canonical_input: dict[str, Any]
    context_fingerprint: str
    source_event_id: str


@dataclass(frozen=True)
class ResultadoResolucionOferta:
    """Outcome de resolver una oferta para un listing."""

    motivo: str | None
    oferta: OfertaResuelta | None


@dataclass(frozen=True)
class ResultadoPersistenciaOferta:
    """Fila insertada o reutilizada por source_event_id."""

    id: int
    observed_at: datetime
    reutilizada: bool


@dataclass(frozen=True)
class CostoResuelto:
    sku_cost_id: int
    cost_amount: Decimal
    cost_currency: str
    includes_tax: bool
    valid_from: date
    valid_to: date | None
    ingest_run_id: int | None
    validation_run_id: int
    validated_at: datetime


@dataclass(frozen=True)
class ResultadoCosto:
    motivo: str | None
    costo: CostoResuelto | None


@dataclass(frozen=True)
class FxResuelto:
    rate: Decimal | None
    rate_date: date | None
    source: str
    base: str
    quote: str


@dataclass(frozen=True)
class ResultadoFx:
    motivo: str | None
    fx: FxResuelto | None


def leer_ofertas_bridge(ruta: Path | str) -> tuple[FilaOfertaBridge, ...]:
    """Lee amazon_listing_prices del snapshot bridge (mode=ro)."""
    ruta = Path(ruta)
    if not ruta.is_file():
        raise InsumosError(f"snapshot inexistente: {ruta}")
    con = sqlite3.connect(f"file:{ruta.as_posix()}?mode=ro", uri=True)
    try:
        return tuple(
            FilaOfertaBridge(
                seller_sku=fila[0] or "",
                asin=fila[1],
                marketplace_id=fila[2] or "",
                marketplace_name=fila[3],
                price=fila[4],
                fulfillment_channel=fila[5],
                fetched_at=fila[6],
            )
            for fila in con.execute(
                "SELECT seller_sku, asin, marketplace_id, marketplace_name, price,"
                " fulfillment_channel, fetched_at"
                " FROM amazon_listing_prices"
                " ORDER BY marketplace_id, seller_sku"
            )
        )
    finally:
        con.close()


def mapear_plataforma(marketplace_id: str) -> str | None:
    return MARKETPLACE_PLATAFORMA.get((marketplace_id or "").strip())


def mapear_canal(fulfillment_channel: str | None) -> str | None:
    if fulfillment_channel is None:
        return None
    return CANAL_POR_FULFILLMENT.get(fulfillment_channel.strip())


def normalizar_precio(precio: float | None) -> tuple[Decimal | None, str | None]:
    """REAL bridge -> Decimal cuantizado o dato faltante."""
    if precio is None:
        return None, None
    if not math.isfinite(precio) or precio <= 0:
        return None, "precio no positivo (dato faltante)"
    valor = Decimal(str(precio))
    if abs(valor) >= _MAX_PRECIO:
        return None, "precio fuera de rango NUMERIC(14,4)"
    if abs(valor - valor.quantize(_MAX_DECIMALES)) >= _RUIDO_FLOAT:
        return None, "precio con mas de 4 decimales (dato faltante)"
    cuantizado = valor.quantize(_MAX_DECIMALES)
    if cuantizado <= 0:
        return None, "precio no positivo (dato faltante)"
    return cuantizado, None


def parse_fetched_at_utc(texto: str | None) -> datetime | None:
    if not texto or not str(texto).strip():
        return None
    raw = str(texto).strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def evaluar_frescura(fetched_at: datetime, now_utc: datetime) -> str:
    """fresco | desactualizada | futura."""
    if fetched_at > now_utc:
        return "futura"
    if now_utc - fetched_at > FRESHNESS_LIMIT:
        return "desactualizada"
    return "fresco"


def _motivo_universo(platform: str, canal: str) -> str | None:
    if platform == "amazon_us":
        return "us_sin_politica_prospectiva"
    if canal == "fbm":
        return "logistica_fbm_pendiente"
    if platform == "amazon_mx" and canal == "fba":
        return None
    return "universo_no_soportado"


def construir_canonical_input(
    fila: FilaOfertaBridge,
    platform: str,
    canal: str,
    precio: Decimal,
    moneda: str,
) -> dict[str, Any]:
    fetched = parse_fetched_at_utc(fila.fetched_at)
    return {
        "fuente": SOURCE_BRIDGE,
        "seller_sku": fila.seller_sku.strip(),
        "asin": (fila.asin or "").strip(),
        "marketplace_id": fila.marketplace_id.strip(),
        "marketplace_name": (fila.marketplace_name or "").strip(),
        "platform": platform,
        "canal": canal,
        "price": str(precio),
        "price_currency": moneda,
        "fulfillment_channel": (fila.fulfillment_channel or "").strip(),
        "fetched_at": fetched.isoformat() if fetched else None,
    }


def construir_context_fingerprint(
    canal: str,
    precio: Decimal,
    moneda: str,
    fetched_at: datetime,
) -> str:
    return f"{canal}:{precio}:{moneda}:{fetched_at.isoformat()}"


def construir_source_event_id(canonical_input: dict[str, Any]) -> str:
    payload = json.dumps(canonical_input, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode()).hexdigest()[:32]
    return f"bridge-oferta:{digest}"


def _fila_coincide_plataforma_asin(
    fila: FilaOfertaBridge,
    *,
    platform: str,
    asin: str,
) -> bool:
    """Coincidencia raw marketplace+ASIN sin exigir precio/canal/fetched_at validos."""
    if (fila.asin or "").strip() != asin.strip():
        return False
    return mapear_plataforma(fila.marketplace_id) == platform


def _normalizar_fila_asin(
    fila: FilaOfertaBridge,
    *,
    platform: str,
    asin: str,
) -> tuple[str, Decimal, str, datetime, str] | str:
    """Normaliza fila bridge por plataforma+ASIN; seller_sku queda aparte."""
    if (fila.asin or "").strip() != asin.strip():
        return "asin distinto"
    plataforma = mapear_plataforma(fila.marketplace_id)
    if plataforma != platform:
        return "plataforma distinta"
    canal = mapear_canal(fila.fulfillment_channel)
    if canal is None:
        return "canal desconocido"
    precio, motivo_precio = normalizar_precio(fila.price)
    if precio is None:
        return motivo_precio or "precio invalido"
    moneda = PLATAFORMAS_MONEDA.get(platform)
    if moneda is None:
        return "plataforma fuera de dominio"
    fetched = parse_fetched_at_utc(fila.fetched_at)
    if fetched is None:
        return "fetched_at invalido"
    return canal, precio, moneda, fetched, fila.seller_sku.strip()


def resolver_oferta_para_listing(
    filas_bridge: tuple[FilaOfertaBridge, ...] | list[FilaOfertaBridge],
    *,
    listing_id: int,
    platform: str,
    seller_sku: str,
    asin: str,
    now_utc: datetime,
) -> ResultadoResolucionOferta:
    """Resuelve oferta unica para listing+plataforma+ASIN; ambiguedad sobre filas raw."""
    raw_coincidentes = [
        fila
        for fila in filas_bridge
        if _fila_coincide_plataforma_asin(fila, platform=platform, asin=asin)
    ]
    if not raw_coincidentes:
        return ResultadoResolucionOferta(motivo="oferta_ausente", oferta=None)

    skus_raw = {(fila.seller_sku or "").strip() for fila in raw_coincidentes}
    if len(skus_raw) > 1:
        return ResultadoResolucionOferta(motivo="identidad_ambigua", oferta=None)

    candidatas: list[tuple[str, Decimal, str, datetime, str, FilaOfertaBridge]] = []
    for fila in raw_coincidentes:
        parsed = _normalizar_fila_asin(fila, platform=platform, asin=asin)
        if isinstance(parsed, str):
            continue
        canal, precio, moneda, fetched, sku_fila = parsed
        candidatas.append((canal, precio, moneda, fetched, sku_fila, fila))

    if not candidatas:
        return ResultadoResolucionOferta(motivo="oferta_ausente", oferta=None)

    claves = {
        (canal, precio, moneda, fetched.isoformat())
        for canal, precio, moneda, fetched, _, _ in candidatas
    }
    if len(claves) > 1:
        return ResultadoResolucionOferta(motivo="identidad_ambigua", oferta=None)

    canal, precio, moneda, fetched, sku_fila, fila = candidatas[0]
    if sku_fila != seller_sku.strip():
        return ResultadoResolucionOferta(motivo="identidad_ambigua", oferta=None)

    motivo_univ = _motivo_universo(platform, canal)
    if motivo_univ is not None:
        return ResultadoResolucionOferta(motivo=motivo_univ, oferta=None)

    frescura = evaluar_frescura(fetched, now_utc)
    if frescura == "futura":
        return ResultadoResolucionOferta(motivo="oferta_futura", oferta=None)
    if frescura == "desactualizada":
        return ResultadoResolucionOferta(motivo="oferta_desactualizada", oferta=None)

    canon = construir_canonical_input(fila, platform, canal, precio, moneda)
    oferta = OfertaResuelta(
        listing_id=listing_id,
        platform=platform,
        seller_sku=seller_sku.strip(),
        asin=asin.strip(),
        canal=canal,
        price_amount=precio,
        price_currency=moneda,
        fetched_at=fetched,
        canonical_input=canon,
        context_fingerprint=construir_context_fingerprint(canal, precio, moneda, fetched),
        source_event_id=construir_source_event_id(canon),
    )
    return ResultadoResolucionOferta(motivo=None, oferta=oferta)


def persistir_oferta_observation(
    conn: psycopg.Connection,
    oferta: OfertaResuelta,
    *,
    observed_at: datetime,
    ingest_run_id: int | None = None,
) -> ResultadoPersistenciaOferta:
    """INSERT idempotente por source_event_id; no commit oculto."""
    fila = conn.execute(
        "INSERT INTO estimacion_oferta_observation"
        " (listing_id, platform, seller_sku, asin, canal, price_amount, price_currency,"
        " fetched_at, observed_at, source_event_id, canonical_input, context_fingerprint,"
        " ingest_run_id)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
        " ON CONFLICT (source_event_id) DO NOTHING"
        " RETURNING id, observed_at",
        (
            oferta.listing_id,
            oferta.platform,
            oferta.seller_sku,
            oferta.asin,
            oferta.canal,
            oferta.price_amount,
            oferta.price_currency,
            oferta.fetched_at,
            observed_at,
            oferta.source_event_id,
            Json(oferta.canonical_input),
            oferta.context_fingerprint,
            ingest_run_id,
        ),
    ).fetchone()
    if fila is not None:
        return ResultadoPersistenciaOferta(id=fila[0], observed_at=fila[1], reutilizada=False)
    existente = conn.execute(
        "SELECT id, observed_at FROM estimacion_oferta_observation WHERE source_event_id = %s",
        (oferta.source_event_id,),
    ).fetchone()
    if existente is None:
        raise InsumosError(
            f"conflicto sin fila previa para source_event_id={oferta.source_event_id}"
        )
    return ResultadoPersistenciaOferta(id=existente[0], observed_at=existente[1], reutilizada=True)


def _costo_vigente_en(valid_from: date, valid_to: date | None, fecha: date) -> bool:
    return valid_from <= fecha and (valid_to is None or valid_to > fecha)


def _corrida_validacion_costos_aceptable(
    source: str | None,
    ok: bool | None,
    finished_at: datetime | None,
    rows_skipped: int | None,
    *,
    fecha_escenario: date,
    now_utc: datetime,
) -> bool:
    """Corrida GLOBAL accounting_sku_costs ok del mismo dia UTC, sin skips, terminada <= now_utc."""
    if source != SOURCE_COSTOS or ok is not True or finished_at is None:
        return False
    if rows_skipped is None or rows_skipped > 0:
        return False
    fin = finished_at if finished_at.tzinfo is not None else finished_at.replace(tzinfo=UTC)
    fin_utc = fin.astimezone(UTC)
    if fin_utc > now_utc.astimezone(UTC):
        return False
    return fin_utc.date() == fecha_escenario


def _buscar_corrida_validacion_costos(
    conn: psycopg.Connection,
    *,
    fecha_escenario: date,
    now_utc: datetime,
) -> tuple[int, datetime] | None:
    """Corrida diaria global mas reciente que valida costos en fecha_escenario."""
    fila = conn.execute(
        "SELECT ir.id, ir.source, ir.ok, ir.finished_at, ir.rows_skipped"
        " FROM ingest_run ir"
        " WHERE ir.source = %s AND ir.ok = true AND ir.finished_at IS NOT NULL"
        " AND ir.rows_skipped = 0"
        " AND (ir.finished_at AT TIME ZONE 'UTC')::date = %s"
        " AND ir.finished_at <= %s"
        " ORDER BY ir.finished_at DESC"
        " LIMIT 1",
        (SOURCE_COSTOS, fecha_escenario, now_utc),
    ).fetchone()
    if fila is None:
        return None
    run_id, source, ok, finished_at, rows_skipped = fila
    if not _corrida_validacion_costos_aceptable(
        source,
        ok,
        finished_at,
        rows_skipped,
        fecha_escenario=fecha_escenario,
        now_utc=now_utc,
    ):
        return None
    fin = finished_at if finished_at.tzinfo is not None else finished_at.replace(tzinfo=UTC)
    return run_id, fin.astimezone(UTC)


def evaluar_costo_candidatos(
    filas: list[tuple[Any, ...]],
    *,
    producto_tiene_alguna: bool,
    fecha_escenario: date,
    now_utc: datetime,
    corrida_validacion: tuple[int, datetime] | None = None,
) -> ResultadoCosto:
    """Clasifica vigencia del producto y exige corrida diaria global aparte del ingest_run_id."""
    if not producto_tiene_alguna:
        return ResultadoCosto(motivo="costo_ausente", costo=None)

    vigentes = [
        fila
        for fila in filas
        if _costo_vigente_en(fila[4], fila[5], fecha_escenario) and fila[1] > 0
    ]
    if not vigentes:
        return ResultadoCosto(motivo="costo_no_vigente", costo=None)
    if len(vigentes) > 1:
        return ResultadoCosto(motivo="costo_ambiguo", costo=None)

    if corrida_validacion is None:
        return ResultadoCosto(motivo="costo_desactualizado", costo=None)

    validation_run_id, validated_at = corrida_validacion
    fila = vigentes[0]
    costo = CostoResuelto(
        sku_cost_id=fila[0],
        cost_amount=fila[1],
        cost_currency=fila[2],
        includes_tax=fila[3],
        valid_from=fila[4],
        valid_to=fila[5],
        ingest_run_id=fila[6],
        validation_run_id=validation_run_id,
        validated_at=validated_at,
    )
    return ResultadoCosto(motivo=None, costo=costo)


def resolver_costo(
    conn: psycopg.Connection,
    *,
    product_id: int,
    fecha_escenario: date,
    now_utc: datetime,
) -> ResultadoCosto:
    """Costo vigente en fecha_escenario con corrida diaria global ok del mismo dia UTC."""
    filas = conn.execute(
        "SELECT c.id, c.cost_amount, c.cost_currency, c.includes_tax,"
        " c.valid_from, c.valid_to, c.ingest_run_id"
        " FROM sku_cost c"
        " WHERE c.product_id = %s"
        " ORDER BY c.valid_from DESC, c.id DESC",
        (product_id,),
    ).fetchall()
    alguna = conn.execute(
        "SELECT 1 FROM sku_cost WHERE product_id = %s LIMIT 1",
        (product_id,),
    ).fetchone()
    corrida = _buscar_corrida_validacion_costos(
        conn,
        fecha_escenario=fecha_escenario,
        now_utc=now_utc,
    )
    return evaluar_costo_candidatos(
        list(filas),
        producto_tiene_alguna=alguna is not None,
        fecha_escenario=fecha_escenario,
        now_utc=now_utc,
        corrida_validacion=corrida,
    )


def resolver_fx(
    conn: psycopg.Connection,
    *,
    fecha: date,
    base: str,
    quote: str,
) -> ResultadoFx:
    """FX via fx_resolve(fecha, base, quote, 7); misma moneda sin tasa externa."""
    if base == quote:
        return ResultadoFx(
            motivo=None,
            fx=FxResuelto(
                rate=None,
                rate_date=None,
                source="misma_moneda",
                base=base,
                quote=quote,
            ),
        )
    fila = conn.execute(
        "SELECT rate, rate_date, source FROM fx_resolve(%s, %s, %s, 7)",
        (fecha, base, quote),
    ).fetchone()
    if fila is None:
        return ResultadoFx(motivo="fx_ausente", fx=None)
    return ResultadoFx(
        motivo=None,
        fx=FxResuelto(
            rate=fila[0],
            rate_date=fila[1],
            source=fila[2],
            base=base,
            quote=quote,
        ),
    )
