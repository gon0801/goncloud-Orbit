"""Repositorio MARGEN ESTIMADO 01 A.4 — persistencia de escenarios.

Carga politica/costo/FX via `app.estimacion_insumos`, construye entrada congelada,
invoca calculador puro y persiste append-only en `estimacion_escenario`.
La lectura batch vive en `app.estimacion_reader` (reexportada aqui).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import psycopg
from psycopg.types.json import Json

from app.estimacion_amazon import partir_fees
from app.estimacion_insumos import FRESHNESS_LIMIT, resolver_costo, resolver_fx
from app.estimacion_reader import (
    EscenarioLeido,
    ProcedenciaRefs,
    leer_escenarios,
    ultimo_contexto,
)
from app.estimacion_universo import (
    LogisticaResuelta,
    envio_a_logistica,
    logistica_del_universo,
    universo_de,
)
from app.estimacion_venta import (
    ESTADOS_DESACTUALIZADA,
    EXCLUSIONES_FIJAS,
    MOTIVOS_ESTIMACION,
    DetalleFee,
    EntradaCalculo,
    PoliticaCalculo,
    ResultadoCalculo,
    calcular_contribucion,
    validar_dinero,
)
from app.meli.estimacion import partir_fees_meli
from app.precio.tipos import TasaFx

# Callers historicos importan EscenarioLeido / leer_escenarios desde aqui.
_ = (EscenarioLeido, ProcedenciaRefs, leer_escenarios)


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
    # REPRICING 02 F.3: envio con origen y fees partidos (0054; la 0055 los
    # exige a todo disponible). None = incompleta/desactualizada.
    envio_muestra_id: int | None = None
    logistica_origen: str | None = None
    miembro_product_id: int | None = None
    fee_variable: Decimal | None = None
    fee_fijo: Decimal | None = None
    fee_cotizable: bool | None = None


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
    miembros: tuple[dict[str, Any], ...] | None = None,
) -> dict[str, Any]:
    canonica = {
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
    if miembros is not None:
        canonica["miembros"] = list(miembros)
    return canonica


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
        # Los observados guardan el codigo crudo (auditoria); la fila solo
        # admite vocabulario cerrado: lo desconocido (`fee_error`,
        # `fee_contrato_incompatible`, de Amazon y MeLi) colapsa a
        # `fee_incompatible` en vez de fugar a `estimacion_motivo_desconocido`.
        codigo = fee.get("error_code") or "fee_ausente"
        return codigo if codigo in MOTIVOS_ESTIMACION else "fee_incompatible"
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
    fx_max_dias: int | None = None,
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
            max_dias=fx_max_dias,
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


def _tasa_fx_de_costo(costo_fx: CostoFxEscenario) -> TasaFx | None:
    """Tasa usable para logistica, o None (el llamador ya valido)."""
    fx = costo_fx
    if fx.fx_rate and fx.fx_rate_date and fx.fx_base and fx.fx_quote and fx.fx_source:
        try:
            return TasaFx(
                base=fx.fx_base,
                quote=fx.fx_quote,
                tasa=fx.fx_rate,
                fecha=fx.fx_rate_date,
                fuente=fx.fx_source,
            )
        except ValueError:
            return None
    return None


def _fees_de_observacion(
    fee_raw: dict[str, Any] | None,
) -> tuple[
    Decimal | None,
    str | None,
    tuple[DetalleFee, ...],
    Decimal | None,
    Decimal | None,
    tuple[str, ...],
]:
    """Total, estado, detalles y partido de la observacion de fees.

    Devuelve los motivos en orden: primero el de detalles ilegibles, luego
    el del partido. El partido corre aunque los detalles fallen.
    """
    total = fee_raw["total_fees"] if fee_raw else None
    estado = fee_raw["estado"] if fee_raw else None
    detalles: tuple[DetalleFee, ...] = ()
    motivos_fee: list[str] = []
    if fee_raw is not None:
        try:
            detalles = _detalles_desde_json(fee_raw["fee_details"])
        except ValueError:
            motivos_fee.append("fee_incompatible")
    variable: Decimal | None = None
    fijo: Decimal | None = None
    if total is not None:
        if fee_raw is not None and fee_raw.get("platform") == "meli":
            partido = partir_fees_meli(detalles, total)
        else:
            partido = partir_fees(detalles, total)
        if isinstance(partido, str):
            motivos_fee.append(partido)
        else:
            variable, fijo = partido
    return total, estado, detalles, variable, fijo, tuple(motivos_fee)


_SQL_MIEMBROS_UNIDAD = (
    "SELECT seller_sku, product_id FROM v_precio_unidad_miembro"
    " WHERE listing_id = %s ORDER BY seller_sku"
)


def _carga_de(resultado: ResultadoCalculo) -> Decimal:
    """C+L que uso la cuenta, en moneda de venta (orden exacto del que manda)."""
    comp = {c.nombre: c for c in resultado.componentes}
    costo = comp["costo_normalizado"].importe_normalizado
    logistica = comp["logistica"].importe_normalizado
    assert costo is not None and logistica is not None
    return costo + logistica


def _estado_meli_union(motivos: list[str]) -> str:
    """Precedencia de `calcular_contribucion`: ambigua > desactualizada > resto."""
    if "identidad_ambigua" in motivos:
        return "identidad_ambigua"
    if any(m in ESTADOS_DESACTUALIZADA for m in motivos):
        return "desactualizada"
    return "incompleta"


def _sembrar_meli(
    conn: psycopg.Connection,
    *,
    listing_id: int,
    oferta_raw: dict[str, Any],
    fee_raw: dict[str, Any] | None,
    fee_observation_id: int | None,
    universo: str,
    uni: Any,
    politica: PoliticaCalculo | None,
    motivos: list[str],
    valoracion_date: date,
    observed_at: datetime,
) -> EscenarioPersistible:
    """Escenario `meli/meli`: una cuenta por miembro, la fila del que manda.

    Manda el margen mas bajo (con goal unico equivale al P* mas alto);
    empate -> `seller_sku` ascendente. Los demas miembros van en
    `componentes` como `miembro:<sku>` con `pertenece_a_total = false`.
    Sin politica o con un miembro sin insumos, la fila es no-disponible
    con la union de motivos (los fallos de insumo traen su SKU a la vista).
    """
    (fee_total, fee_estado, fee_detalles, fee_variable, fee_fijo, motivos_fee) = (
        _fees_de_observacion(fee_raw)
    )
    motivos_fila = list(motivos)
    motivos_fila.extend(motivos_fee)
    moneda_venta = oferta_raw["price_currency"]
    miembros = [(str(sku), pid) for sku, pid in conn.execute(_SQL_MIEMBROS_UNIDAD, (listing_id,))]
    if not miembros:
        # Cero filas = M.1 sin miembros activos (catalogo pendiente o baja
        # total): `costo_ausente` honesto, sin SKU porque no hay unidad
        # que nombrar. Difiere de Amazon a proposito (alla la vista trae
        # la fila del listing; aca la unidad ES sus miembros).
        motivos_fila.append("costo_ausente")

    infos: list[dict[str, Any]] = []
    for sku, pid in miembros:
        mot_in: list[str] = []
        costo_fx = CostoFxEscenario()
        resuelta = LogisticaResuelta(None, None, None, None, None, None, None)
        if pid is None:
            mot_in.append("costo_ausente")
        else:
            costo_fx = _resolver_costo_fx_escenario(
                conn,
                product_id=pid,
                valoracion_date=valoracion_date,
                observed_at=observed_at,
                moneda_venta=moneda_venta,
                motivos=mot_in,
                fx_max_dias=uni.fx_max_dias if uni is not None else None,
            )
            if uni is not None and politica is not None:
                res_envio = logistica_del_universo(
                    conn,
                    uni,
                    product_id=pid,
                    politica_settings=politica.settings,
                    fx=_tasa_fx_de_costo(costo_fx),
                )
                if isinstance(res_envio, str):
                    mot_in.append(res_envio)
                else:
                    resuelta = envio_a_logistica(res_envio)
        infos.append(
            {
                "sku": sku,
                "product_id": pid,
                "costo_fx": costo_fx,
                "resuelta": resuelta,
                "motivos_in": mot_in,
                "resultado": None,
            }
        )

    if politica is None:
        for info in infos:
            for motivo in info["motivos_in"]:
                if motivo not in motivos_fila:
                    motivos_fila.append(motivo)
        return _fila_meli_sin_cuenta(
            listing_id=listing_id,
            oferta_raw=oferta_raw,
            fee_observation_id=fee_observation_id,
            infos=infos,
            motivos_fila=motivos_fila,
            fee_variable=fee_variable,
            fee_fijo=fee_fijo,
            con_cotizador=(uni.cotizador is not None) if uni is not None else None,
            politica_version_id=None,
            formula_version="S3",
            valoracion_date=valoracion_date,
            observed_at=observed_at,
        )

    for info in infos:
        costo_fx = info["costo_fx"]
        resuelta = info["resuelta"]
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
            motivos_entrada=tuple(dict.fromkeys([*motivos_fila, *info["motivos_in"]])),
            logistica=resuelta.logistica,
            logistica_origen=resuelta.origen,
            logistica_original=resuelta.original,
            logistica_moneda_original=resuelta.moneda_original,
            logistica_tasa=resuelta.tasa,
            logistica_tasa_fecha=resuelta.tasa_fecha,
        )
        info["resultado"] = calcular_contribucion(
            entrada,
            politica,
            valoracion_date=valoracion_date,
            universo_esperado=universo,
        )

    disponibles = [i for i in infos if i["resultado"].estado == "disponible"]
    if infos and len(disponibles) == len(infos):
        # Manda max (C+L) exactos, no min pct (4dp empataria distinto):
        # mismo P, fees y retencion => min margen <=> max carga <=> max P*.
        manda = min(disponibles, key=lambda i: (-_carga_de(i["resultado"]), i["sku"]))
        return _fila_meli_disponible(
            listing_id=listing_id,
            oferta_raw=oferta_raw,
            fee_observation_id=fee_observation_id,
            fee_total=fee_total,
            fee_estado=fee_estado,
            fee_detalles=fee_detalles,
            infos=infos,
            manda=manda,
            fee_variable=fee_variable,
            fee_fijo=fee_fijo,
            con_cotizador=(uni.cotizador is not None) if uni is not None else None,
            politica=politica,
            valoracion_date=valoracion_date,
            observed_at=observed_at,
        )

    union = list(motivos_fila)
    for info in infos:
        for motivo in info["resultado"].motivos:
            if motivo not in union:
                union.append(motivo)
    for info in infos:
        info["motivos_propios"] = [m for m in info["resultado"].motivos if m not in motivos_fila]
    return _fila_meli_sin_cuenta(
        listing_id=listing_id,
        oferta_raw=oferta_raw,
        fee_observation_id=fee_observation_id,
        infos=infos,
        motivos_fila=union,
        fee_variable=fee_variable,
        fee_fijo=fee_fijo,
        con_cotizador=(uni.cotizador is not None) if uni is not None else None,
        politica_version_id=politica.politica_version_id,
        formula_version=politica.formula_version,
        valoracion_date=valoracion_date,
        observed_at=observed_at,
    )


def _canonica_miembros(infos: list[dict[str, Any]]) -> tuple[dict[str, Any], ...]:
    """Entradas por miembro para la identidad (un cambio re-siembra).

    Cubre sku_cost_id, muestra, producto, estado, contribucion y motivos:
    costos y muestras son append-only (cambio = id nuevo). La tasa FX no
    viaja por miembro: si mueve la cuenta cambia `contribucion` (el mismo
    Decimal que va a la fila) y la identidad cambia; si no la mueve, la
    fila visible es identica y reusar es correcto, no ruido.
    """
    salida = []
    for info in infos:
        costo_fx = info["costo_fx"]
        resuelta = info["resuelta"]
        resultado = info["resultado"]
        salida.append(
            {
                "sku": info["sku"],
                "product_id": info["product_id"],
                "sku_cost_id": costo_fx.sku_cost_id,
                "envio_muestra_id": resuelta.muestra_id,
                "estado": resultado.estado if resultado is not None else None,
                "contribucion": (
                    str(resultado.contribucion)
                    if resultado is not None and resultado.contribucion is not None
                    else None
                ),
                "motivos": list(info["motivos_in"])
                + (
                    [m for m in resultado.motivos if m not in info["motivos_in"]]
                    if resultado is not None
                    else []
                ),
            }
        )
    return tuple(salida)


def _fila_meli_disponible(
    *,
    listing_id: int,
    oferta_raw: dict[str, Any],
    fee_observation_id: int | None,
    fee_total: Decimal | None,
    fee_estado: str | None,
    fee_detalles: tuple[DetalleFee, ...],
    infos: list[dict[str, Any]],
    manda: dict[str, Any],
    fee_variable: Decimal | None,
    fee_fijo: Decimal | None,
    con_cotizador: bool | None,
    politica: PoliticaCalculo,
    valoracion_date: date,
    observed_at: datetime,
) -> EscenarioPersistible:
    resultado = manda["resultado"]
    costo_fx = manda["costo_fx"]
    resuelta = manda["resuelta"]
    componentes = _componentes_a_json(resultado)
    for info in infos:
        if info is manda:
            continue
        componentes.append(
            {
                "nombre": f"miembro:{info['sku']}",
                "importe_normalizado": str(info["resultado"].contribucion),
                "moneda_normalizada": resultado.moneda,
                "pertenece_a_total": False,
            }
        )
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
        "fee_detalles": [_detalle_fee_a_json(d) for d in fee_detalles],
        "fx_rate": str(costo_fx.fx_rate) if costo_fx.fx_rate is not None else None,
        "fx_rate_date": (
            costo_fx.fx_rate_date.isoformat() if costo_fx.fx_rate_date is not None else None
        ),
        "fx_base": costo_fx.fx_base,
        "fx_quote": costo_fx.fx_quote,
        "fx_source": costo_fx.fx_source,
        "logistica": str(resuelta.logistica) if resuelta.logistica is not None else None,
        "logistica_origen": resuelta.origen,
        "logistica_original": (str(resuelta.original) if resuelta.original is not None else None),
        "logistica_moneda_original": resuelta.moneda_original,
        "logistica_tasa": str(resuelta.tasa) if resuelta.tasa is not None else None,
        "logistica_tasa_fecha": (
            resuelta.tasa_fecha.isoformat() if resuelta.tasa_fecha is not None else None
        ),
        "motivos_entrada": [],
    }
    id_canonica = _canonical_frozen(
        oferta_observation_id=oferta_raw["oferta_observation_id"],
        fee_observation_id=fee_observation_id,
        sku_cost_id=costo_fx.sku_cost_id,
        costo_validation_run_id=costo_fx.validation_run_id,
        costo_validated_at=costo_fx.validated_at,
        costo_includes_tax=costo_fx.includes_tax,
        politica_version_id=politica.politica_version_id,
        valoracion_date=valoracion_date,
        context_fingerprint=oferta_raw["context_fingerprint"],
        formula_version=politica.formula_version,
        fx_rate=costo_fx.fx_rate,
        fx_rate_date=costo_fx.fx_rate_date,
        fx_base=costo_fx.fx_base,
        fx_quote=costo_fx.fx_quote,
        fx_source=costo_fx.fx_source,
        motivos=(),
        resultado=resultado,
        entrada_snapshot=entrada_snapshot,
        miembros=_canonica_miembros(infos),
    )
    # `seller_sku` es el ancla, no el que manda: el trigger 0028
    # `estimacion_escenario_referencias_coherentes` exige que case con
    # `listing.seller_sku` (igual que oferta y fee). El que manda va en
    # `miembro_product_id` (esa columna si es suya por diseno) y su SKU se
    # recupera por `product.odoo_sku`. Desvia de la frase de `datos.sql`
    # ("seller_sku el SKU del miembro que manda"), que pediria migracion;
    # ver `ejecucion/M.3/diseno-vs-trigger.md`.
    return EscenarioPersistible(
        listing_id=listing_id,
        platform=oferta_raw["platform"],
        seller_sku=oferta_raw["seller_sku"],
        asin=oferta_raw["asin"],
        canal=oferta_raw["canal"],
        valoracion_date=valoracion_date,
        observed_at=observed_at,
        politica_version_id=politica.politica_version_id,
        formula_version=politica.formula_version,
        oferta_observation_id=oferta_raw["oferta_observation_id"],
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
        motivos=(),
        componentes=componentes,
        exclusiones=resultado.exclusiones,
        canonical_input={**id_canonica, "observed_at": observed_at.isoformat()},
        context_fingerprint=oferta_raw["context_fingerprint"],
        source_event_id=_construir_source_event_id(id_canonica),
        envio_muestra_id=resuelta.muestra_id,
        logistica_origen=resuelta.origen,
        miembro_product_id=manda["product_id"],
        fee_variable=fee_variable,
        fee_fijo=fee_fijo,
        fee_cotizable=con_cotizador,
    )


def _fila_meli_sin_cuenta(
    *,
    listing_id: int,
    oferta_raw: dict[str, Any],
    fee_observation_id: int | None,
    infos: list[dict[str, Any]],
    motivos_fila: list[str],
    fee_variable: Decimal | None,
    fee_fijo: Decimal | None,
    con_cotizador: bool | None,
    politica_version_id: int | None,
    formula_version: str,
    valoracion_date: date,
    observed_at: datetime,
) -> EscenarioPersistible:
    estado = _estado_meli_union(motivos_fila)
    motivos = tuple(dict.fromkeys(motivos_fila))
    componentes = []
    for info in infos:
        propios = info.get("motivos_propios")
        if propios is None:
            propios = [m for m in info["motivos_in"] if m not in motivos_fila]
            if not propios and info["motivos_in"]:
                propios = list(info["motivos_in"][:1])
        if propios:
            componentes.append(
                {
                    "nombre": f"miembro:{info['sku']}",
                    "fuente": propios[0],
                    "pertenece_a_total": False,
                }
            )
    resultado = ResultadoCalculo(
        estado=estado,
        motivos=motivos,
        contribucion=None,
        contribucion_pct=None,
        moneda=None,
        componentes=(),
        exclusiones=EXCLUSIONES_FIJAS,
    )
    id_canonica = _canonical_frozen(
        oferta_observation_id=oferta_raw["oferta_observation_id"],
        fee_observation_id=fee_observation_id,
        sku_cost_id=None,
        costo_validation_run_id=None,
        costo_validated_at=None,
        costo_includes_tax=None,
        politica_version_id=politica_version_id,
        valoracion_date=valoracion_date,
        context_fingerprint=oferta_raw["context_fingerprint"],
        formula_version=formula_version,
        fx_rate=None,
        fx_rate_date=None,
        fx_base=None,
        fx_quote=None,
        fx_source=None,
        motivos=motivos,
        resultado=resultado,
        entrada_snapshot={
            "precio_bruto": str(oferta_raw["price_amount"]),
            "precio_moneda": oferta_raw["price_currency"],
            "motivos_entrada": list(motivos),
        },
        miembros=_canonica_miembros(infos),
    )
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
        oferta_observation_id=oferta_raw["oferta_observation_id"],
        fee_observation_id=fee_observation_id,
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
        componentes=componentes,
        exclusiones=EXCLUSIONES_FIJAS,
        canonical_input={**id_canonica, "observed_at": observed_at.isoformat()},
        context_fingerprint=oferta_raw["context_fingerprint"],
        source_event_id=_construir_source_event_id(id_canonica),
        envio_muestra_id=None,
        logistica_origen=None,
        miembro_product_id=None,
        fee_variable=fee_variable,
        fee_fijo=fee_fijo,
        fee_cotizable=con_cotizador,
    )


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
    uni = universo_de(oferta_raw["platform"], oferta_raw["canal"])
    if uni is None:
        motivos.append("universo_no_soportado")
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

    if oferta_raw["platform"] == "meli":
        return _sembrar_meli(
            conn,
            listing_id=listing_id,
            oferta_raw=oferta_raw,
            fee_raw=fee_raw,
            fee_observation_id=fee_observation_id,
            universo=universo,
            uni=uni,
            politica=politica,
            motivos=motivos,
            valoracion_date=valoracion_date,
            observed_at=observed_at,
        )

    costo_fx = _resolver_costo_fx_escenario(
        conn,
        product_id=product_id,
        valoracion_date=valoracion_date,
        observed_at=observed_at,
        moneda_venta=oferta_raw["price_currency"],
        motivos=motivos,
        fx_max_dias=uni.fx_max_dias if uni is not None else None,
    )
    moneda_venta = oferta_raw["price_currency"]

    (fee_total, fee_estado, fee_detalles, fee_variable, fee_fijo, motivos_fee) = (
        _fees_de_observacion(fee_raw)
    )
    motivos.extend(motivos_fee)

    tasa = _tasa_fx_de_costo(costo_fx)

    resuelta = LogisticaResuelta(None, None, None, None, None, None, None)
    if uni is not None and politica is not None:
        res_envio = logistica_del_universo(
            conn,
            uni,
            product_id=product_id,
            politica_settings=politica.settings,
            fx=tasa,
        )
        if isinstance(res_envio, str):
            motivos.append(res_envio)
        else:
            resuelta = envio_a_logistica(res_envio)
    logistica = resuelta.logistica
    logistica_origen = resuelta.origen
    envio_muestra_id = resuelta.muestra_id
    logistica_original = resuelta.original
    logistica_moneda_original = resuelta.moneda_original
    logistica_tasa = resuelta.tasa
    logistica_tasa_fecha = resuelta.tasa_fecha

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
        logistica=logistica,
        logistica_origen=logistica_origen,
        logistica_original=logistica_original,
        logistica_moneda_original=logistica_moneda_original,
        logistica_tasa=logistica_tasa,
        logistica_tasa_fecha=logistica_tasa_fecha,
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
        "logistica": str(logistica) if logistica is not None else None,
        "logistica_origen": logistica_origen,
        "logistica_original": str(logistica_original) if logistica_original is not None else None,
        "logistica_moneda_original": logistica_moneda_original,
        "logistica_tasa": str(logistica_tasa) if logistica_tasa is not None else None,
        "logistica_tasa_fecha": (
            logistica_tasa_fecha.isoformat() if logistica_tasa_fecha is not None else None
        ),
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
        envio_muestra_id=envio_muestra_id,
        logistica_origen=logistica_origen,
        miembro_product_id=product_id,
        fee_variable=fee_variable,
        fee_fijo=fee_fijo,
        fee_cotizable=(uni.cotizador is not None) if uni is not None else None,
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
            Json(motivos_json),
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
    motivos_admitidos = {
        "oferta_ausente",
        "identidad_ambigua",
        "oferta_futura",
        "oferta_desactualizada",
        "logistica_fbm_pendiente",
    }
    if motivo not in motivos_admitidos:
        raise ValueError(f"motivo no soportado para transicion negativa: {motivo}")

    contexto = ultimo_contexto(conn, listing_id, as_of=observed_at)
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
