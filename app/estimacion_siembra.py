"""Siembra de escenarios MARGEN ESTIMADO (Amazon 1 miembro y MeLi 1..N miembros).

Separa la construccion de `EntradaCalculo`, la resolucion de costo/FX/logistica
por miembro, el canonical_input y el armado de `EscenarioPersistible` del
acceso SQL puro de `app.estimacion_repository`.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import psycopg

from app.estimacion_amazon import partir_fees
from app.estimacion_insumos import resolver_costo, resolver_fx
from app.estimacion_universo import (
    LogisticaResuelta,
    Universo,
    envio_a_logistica,
    logistica_del_universo,
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
    detalle_fee_a_json,
    detalles_fee_desde_json,
    fee_variable_efectivo,
)
from app.meli.estimacion import partir_fees_meli
from app.precio.tipos import TasaFx

_SQL_MIEMBROS_UNIDAD = (
    "SELECT seller_sku, product_id FROM v_precio_unidad_miembro"
    " WHERE listing_id = %s ORDER BY seller_sku"
)

_MOTIVOS_TRANSICION_NEGATIVA = frozenset(
    {
        "oferta_ausente",
        "identidad_ambigua",
        "oferta_futura",
        "oferta_desactualizada",
        "logistica_fbm_pendiente",
    }
)


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
class _ContextoSiembra:
    listing_id: int
    oferta_raw: dict[str, Any]
    fee_observation_id: int | None
    fee_total: Decimal | None
    fee_estado: str | None
    fee_detalles: tuple[DetalleFee, ...]
    fee_variable: Decimal | None
    fee_fijo: Decimal | None
    universo: str
    uni: Universo | None
    politica: PoliticaCalculo | None
    valoracion_date: date
    observed_at: datetime

    @property
    def con_cotizador(self) -> bool | None:
        return (self.uni.cotizador is not None) if self.uni is not None else None

    @property
    def politica_version_id(self) -> int | None:
        return self.politica.politica_version_id if self.politica is not None else None

    @property
    def formula_version(self) -> str:
        return self.politica.formula_version if self.politica is not None else "S3"


@dataclass
class _InsumosMiembro:
    sku: str
    product_id: int | None
    costo_fx: CostoFxEscenario
    resuelta: LogisticaResuelta
    motivos_costo: list[str]
    motivos_logistica: list[str]
    resultado: ResultadoCalculo | None = None
    motivos_propios: list[str] | None = None

    @property
    def motivos_in(self) -> list[str]:
        return [*self.motivos_costo, *self.motivos_logistica]


def _estado_sin_cuenta(motivos: Sequence[str]) -> str:
    """Precedencia de `calcular_contribucion`: ambigua > desactualizada > incompleta."""
    if "identidad_ambigua" in motivos:
        return "identidad_ambigua"
    if any(m in ESTADOS_DESACTUALIZADA for m in motivos):
        return "desactualizada"
    return "incompleta"


def _resultado_vacio(motivos: Sequence[str]) -> ResultadoCalculo:
    unicos = tuple(dict.fromkeys(motivos))
    return ResultadoCalculo(
        estado=_estado_sin_cuenta(unicos),
        motivos=unicos,
        contribucion=None,
        contribucion_pct=None,
        moneda=None,
        componentes=(),
        exclusiones=EXCLUSIONES_FIJAS,
    )


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


def _construir_source_event_id(canonical: dict[str, Any]) -> str:
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode()).hexdigest()[:32]
    return f"escenario:{digest}"


def validar_fee_compatible(
    oferta: dict[str, Any],
    fee: dict[str, Any],
    *,
    escenario_observed_at: datetime,
) -> str | None:
    if fee["oferta_observation_id"] != oferta.get("oferta_observation_id"):
        return "fee_incompatible"
    for campo in ("listing_id", "platform", "seller_sku", "asin", "canal"):
        if oferta.get(campo) != fee.get(campo):
            return "fee_incompatible"
    if oferta["context_fingerprint"] != fee["context_fingerprint"]:
        return "fee_incompatible"
    if fee["quoted_price_amount"] != oferta["price_amount"]:
        return "fee_incompatible"
    if fee["quoted_price_currency"] != oferta["price_currency"]:
        return "fee_incompatible"
    if fee["estado"] != "success":
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
        conn, product_id=product_id, fecha_escenario=valoracion_date, now_utc=observed_at
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


def _resolver_tasa_fx(
    conn: psycopg.Connection,
    *,
    fecha: date,
    base: str,
    quote: str,
    max_dias: int | None,
) -> TasaFx | None:
    fx_res = resolver_fx(conn, fecha=fecha, base=base, quote=quote, max_dias=max_dias)
    if fx_res.motivo or fx_res.fx is None or fx_res.fx.rate is None:
        return None
    try:
        return TasaFx(
            base=fx_res.fx.base,
            quote=fx_res.fx.quote,
            tasa=fx_res.fx.rate,
            fecha=fx_res.fx.rate_date,
            fuente=fx_res.fx.source,
        )
    except ValueError:
        return None


def _tasa_fx_de_costo(fx: CostoFxEscenario) -> TasaFx | None:
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
    total = fee_raw["total_fees"] if fee_raw else None
    estado = fee_raw["estado"] if fee_raw else None
    detalles: tuple[DetalleFee, ...] = ()
    motivos_fee: list[str] = []
    if fee_raw is not None:
        try:
            detalles = detalles_fee_desde_json(fee_raw["fee_details"], estricto=True)
        except ValueError:
            motivos_fee.append("fee_incompatible")
    variable: Decimal | None = None
    fijo: Decimal | None = None
    if total is not None:
        partir = (
            partir_fees_meli if (fee_raw and fee_raw.get("platform") == "meli") else partir_fees
        )
        partido = partir(detalles, total)
        if isinstance(partido, str):
            motivos_fee.append(partido)
        else:
            variable, fijo = partido
    return total, estado, detalles, variable, fijo, tuple(motivos_fee)


def _resolver_miembro(
    conn: psycopg.Connection,
    ctx: _ContextoSiembra,
    *,
    sku: str,
    product_id: int | None,
) -> _InsumosMiembro:
    mot_costo: list[str] = []
    mot_logistica: list[str] = []
    costo_fx = CostoFxEscenario()
    resuelta = LogisticaResuelta(None, None, None, None, None, None, None)
    if product_id is None:
        mot_costo.append("costo_ausente")
    else:
        costo_fx = _resolver_costo_fx_escenario(
            conn,
            product_id=product_id,
            valoracion_date=ctx.valoracion_date,
            observed_at=ctx.observed_at,
            moneda_venta=ctx.oferta_raw["price_currency"],
            motivos=mot_costo,
            fx_max_dias=ctx.uni.fx_max_dias if ctx.uni is not None else None,
        )
        if ctx.uni is not None and ctx.politica is not None:
            res_envio = logistica_del_universo(
                conn,
                ctx.uni,
                product_id=product_id,
                politica_settings=ctx.politica.settings,
                fx=_tasa_fx_de_costo(costo_fx),
                resolver_fx_envio=lambda b, q, _u=ctx.uni: _resolver_tasa_fx(
                    conn, fecha=ctx.valoracion_date, base=b, quote=q, max_dias=_u.fx_max_dias
                ),
            )
            if isinstance(res_envio, str):
                mot_logistica.append(res_envio)
            else:
                resuelta = envio_a_logistica(res_envio)
    return _InsumosMiembro(
        sku=sku,
        product_id=product_id,
        costo_fx=costo_fx,
        resuelta=resuelta,
        motivos_costo=mot_costo,
        motivos_logistica=mot_logistica,
    )


def _construir_entrada(
    ctx: _ContextoSiembra,
    miembro: _InsumosMiembro,
    motivos_entrada: tuple[str, ...],
) -> EntradaCalculo:
    fx, res = miembro.costo_fx, miembro.resuelta
    return EntradaCalculo(
        precio_bruto=ctx.oferta_raw["price_amount"],
        precio_moneda=ctx.oferta_raw["price_currency"],
        costo_original=fx.amount,
        costo_moneda=fx.currency,
        fee_total=ctx.fee_total,
        costo_includes_tax=fx.includes_tax,
        fee_detalles=ctx.fee_detalles,
        fee_estado=ctx.fee_estado,
        fx_rate=fx.fx_rate,
        fx_rate_date=fx.fx_rate_date,
        fx_base=fx.fx_base,
        fx_quote=fx.fx_quote,
        fx_source=fx.fx_source,
        moneda_base=ctx.oferta_raw["price_currency"],
        motivos_entrada=motivos_entrada,
        logistica=res.logistica,
        logistica_origen=res.origen,
        logistica_original=res.original,
        logistica_moneda_original=res.moneda_original,
        logistica_tasa=res.tasa,
        logistica_tasa_fecha=res.tasa_fecha,
    )


def _construir_entrada_snapshot(
    ctx: _ContextoSiembra,
    miembro: _InsumosMiembro,
    motivos_entrada: Sequence[str],
) -> dict[str, Any]:
    fx, res = miembro.costo_fx, miembro.resuelta
    return {
        "precio_bruto": str(ctx.oferta_raw["price_amount"]),
        "precio_moneda": ctx.oferta_raw["price_currency"],
        "costo_original": str(fx.amount) if fx.amount is not None else None,
        "costo_moneda": fx.currency,
        "costo_includes_tax": fx.includes_tax,
        "costo_validation_run_id": fx.validation_run_id,
        "costo_validated_at": fx.validated_at.isoformat() if fx.validated_at is not None else None,
        "fee_total": str(ctx.fee_total) if ctx.fee_total is not None else None,
        "fee_estado": ctx.fee_estado,
        "fee_detalles": [detalle_fee_a_json(d) for d in ctx.fee_detalles],
        "fx_rate": str(fx.fx_rate) if fx.fx_rate is not None else None,
        "fx_rate_date": fx.fx_rate_date.isoformat() if fx.fx_rate_date is not None else None,
        "fx_base": fx.fx_base,
        "fx_quote": fx.fx_quote,
        "fx_source": fx.fx_source,
        "logistica": str(res.logistica) if res.logistica is not None else None,
        "logistica_origen": res.origen,
        "logistica_original": str(res.original) if res.original is not None else None,
        "logistica_moneda_original": res.moneda_original,
        "logistica_tasa": str(res.tasa) if res.tasa is not None else None,
        "logistica_tasa_fecha": (
            res.tasa_fecha.isoformat() if res.tasa_fecha is not None else None
        ),
        "motivos_entrada": list(motivos_entrada),
    }


def _sellar_escenario(
    ctx: _ContextoSiembra,
    *,
    costo_fx: CostoFxEscenario,
    resultado: ResultadoCalculo,
    motivos: tuple[str, ...],
    componentes: list[dict[str, Any]],
    entrada_snapshot: dict[str, Any],
    miembros: tuple[dict[str, Any], ...] | None = None,
    resuelta: LogisticaResuelta | None = None,
    miembro_product_id: int | None = None,
    escalar_fee: bool = True,
) -> EscenarioPersistible:
    oferta = ctx.oferta_raw
    id_canonica: dict[str, Any] = {
        "oferta_observation_id": oferta["oferta_observation_id"],
        "fee_observation_id": ctx.fee_observation_id,
        "sku_cost_id": costo_fx.sku_cost_id,
        "costo_validation_run_id": costo_fx.validation_run_id,
        "costo_validated_at": (
            costo_fx.validated_at.isoformat() if costo_fx.validated_at is not None else None
        ),
        "costo_includes_tax": costo_fx.includes_tax,
        "politica_version_id": ctx.politica_version_id,
        "valoracion_date": ctx.valoracion_date.isoformat(),
        "context_fingerprint": oferta["context_fingerprint"],
        "formula_version": ctx.formula_version,
        "fx_rate": str(costo_fx.fx_rate) if costo_fx.fx_rate is not None else None,
        "fx_rate_date": (
            costo_fx.fx_rate_date.isoformat() if costo_fx.fx_rate_date is not None else None
        ),
        "fx_base": costo_fx.fx_base,
        "fx_quote": costo_fx.fx_quote,
        "fx_source": costo_fx.fx_source,
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
        id_canonica["miembros"] = list(miembros)
    fee_var = (
        fee_variable_efectivo(ctx.fee_variable, ctx.politica) if escalar_fee else ctx.fee_variable
    )
    return EscenarioPersistible(
        listing_id=ctx.listing_id,
        platform=oferta["platform"],
        seller_sku=oferta["seller_sku"],
        asin=oferta["asin"],
        canal=oferta["canal"],
        valoracion_date=ctx.valoracion_date,
        observed_at=ctx.observed_at,
        politica_version_id=ctx.politica_version_id,
        formula_version=ctx.formula_version,
        oferta_observation_id=oferta["oferta_observation_id"],
        fee_observation_id=ctx.fee_observation_id,
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
        motivos=motivos,
        componentes=componentes,
        exclusiones=resultado.exclusiones,
        canonical_input={**id_canonica, "observed_at": ctx.observed_at.isoformat()},
        context_fingerprint=oferta["context_fingerprint"],
        source_event_id=_construir_source_event_id(id_canonica),
        envio_muestra_id=resuelta.muestra_id if resuelta is not None else None,
        logistica_origen=resuelta.origen if resuelta is not None else None,
        miembro_product_id=miembro_product_id,
        fee_variable=fee_var,
        fee_fijo=ctx.fee_fijo,
        fee_cotizable=ctx.con_cotizador,
    )


def _carga_de(resultado: ResultadoCalculo) -> Decimal:
    comp = {c.nombre: c for c in resultado.componentes}
    costo = comp["costo_normalizado"].importe_normalizado
    logistica = comp["logistica"].importe_normalizado
    assert costo is not None and logistica is not None
    return costo + logistica


def _canonica_miembros(infos: Sequence[_InsumosMiembro]) -> tuple[dict[str, Any], ...]:
    salida = []
    for info in infos:
        res = info.resultado
        salida.append(
            {
                "sku": info.sku,
                "product_id": info.product_id,
                "sku_cost_id": info.costo_fx.sku_cost_id,
                "envio_muestra_id": info.resuelta.muestra_id,
                "estado": res.estado if res is not None else None,
                "contribucion": (
                    str(res.contribucion)
                    if res is not None and res.contribucion is not None
                    else None
                ),
                "motivos": list(info.motivos_in)
                + ([m for m in res.motivos if m not in info.motivos_in] if res is not None else []),
            }
        )
    return tuple(salida)


def _sembrar_meli(
    conn: psycopg.Connection,
    ctx: _ContextoSiembra,
    motivos_fila: list[str],
) -> EscenarioPersistible:
    """Escenario `meli/meli`: una cuenta por miembro, la fila del que manda."""
    miembros = [
        (str(sku), pid) for sku, pid in conn.execute(_SQL_MIEMBROS_UNIDAD, (ctx.listing_id,))
    ]
    if not miembros:
        motivos_fila.append("costo_ausente")

    infos = [_resolver_miembro(conn, ctx, sku=sku, product_id=pid) for sku, pid in miembros]

    if ctx.politica is not None:
        for info in infos:
            entrada = _construir_entrada(
                ctx, info, tuple(dict.fromkeys([*motivos_fila, *info.motivos_in]))
            )
            info.resultado = calcular_contribucion(
                entrada,
                ctx.politica,
                valoracion_date=ctx.valoracion_date,
                universo_esperado=ctx.universo,
            )

        disponibles = [
            i for i in infos if i.resultado is not None and i.resultado.estado == "disponible"
        ]
        if infos and len(disponibles) == len(infos):
            manda = min(
                disponibles,
                key=lambda i: (-_carga_de(i.resultado), i.sku),  # type: ignore[arg-type]
            )
            resultado = manda.resultado
            assert resultado is not None
            componentes = _componentes_a_json(resultado)
            for info in infos:
                if info is manda or info.resultado is None:
                    continue
                componentes.append(
                    {
                        "nombre": f"miembro:{info.sku}",
                        "importe_normalizado": str(info.resultado.contribucion),
                        "moneda_normalizada": resultado.moneda,
                        "pertenece_a_total": False,
                    }
                )
            return _sellar_escenario(
                ctx,
                costo_fx=manda.costo_fx,
                resultado=resultado,
                motivos=(),
                componentes=componentes,
                entrada_snapshot=_construir_entrada_snapshot(ctx, manda, ()),
                miembros=_canonica_miembros(infos),
                resuelta=manda.resuelta,
                miembro_product_id=manda.product_id,
            )

        union = list(motivos_fila)
        for info in infos:
            assert info.resultado is not None
            for motivo in info.resultado.motivos:
                if motivo not in union:
                    union.append(motivo)
            info.motivos_propios = [m for m in info.resultado.motivos if m not in motivos_fila]
        motivos_sin_cuenta = union
    else:
        for info in infos:
            for motivo in info.motivos_in:
                if motivo not in motivos_fila:
                    motivos_fila.append(motivo)
        motivos_sin_cuenta = motivos_fila

    resultado_vacio = _resultado_vacio(motivos_sin_cuenta)
    componentes_sin: list[dict[str, Any]] = []
    for info in infos:
        propios = info.motivos_propios
        if propios is None:
            propios = [m for m in info.motivos_in if m not in motivos_sin_cuenta]
            if not propios and info.motivos_in:
                propios = list(info.motivos_in[:1])
        if propios:
            componentes_sin.append(
                {
                    "nombre": f"miembro:{info.sku}",
                    "fuente": propios[0],
                    "pertenece_a_total": False,
                }
            )
    return _sellar_escenario(
        ctx,
        costo_fx=CostoFxEscenario(),
        resultado=resultado_vacio,
        motivos=resultado_vacio.motivos,
        componentes=componentes_sin,
        entrada_snapshot={
            "precio_bruto": str(ctx.oferta_raw["price_amount"]),
            "precio_moneda": ctx.oferta_raw["price_currency"],
            "motivos_entrada": list(resultado_vacio.motivos),
        },
        miembros=_canonica_miembros(infos),
        escalar_fee=False,
    )


def sembrar_desde_insumos(
    conn: psycopg.Connection,
    *,
    listing_id: int,
    product_id: int,
    oferta_raw: dict[str, Any],
    fee_raw: dict[str, Any] | None,
    fee_observation_id: int | None,
    universo: str,
    uni: Universo | None,
    politica: PoliticaCalculo | None,
    motivos: list[str],
    valoracion_date: date,
    observed_at: datetime,
) -> EscenarioPersistible:
    """Construye `EscenarioPersistible` para Amazon (1 miembro) o MeLi (1..N)."""
    (fee_total, fee_estado, fee_detalles, fee_variable, fee_fijo, motivos_fee) = (
        _fees_de_observacion(fee_raw)
    )
    motivos_fila = [*motivos, *motivos_fee]
    ctx = _ContextoSiembra(
        listing_id=listing_id,
        oferta_raw=oferta_raw,
        fee_observation_id=fee_observation_id,
        fee_total=fee_total,
        fee_estado=fee_estado,
        fee_detalles=fee_detalles,
        fee_variable=fee_variable,
        fee_fijo=fee_fijo,
        universo=universo,
        uni=uni,
        politica=politica,
        valoracion_date=valoracion_date,
        observed_at=observed_at,
    )

    if oferta_raw["platform"] == "meli":
        return _sembrar_meli(conn, ctx, motivos_fila)

    miembro = _resolver_miembro(conn, ctx, sku=oferta_raw["seller_sku"], product_id=product_id)
    # Amazon preserva el orden historico: previos -> costo/FX -> fee -> logistica
    # para mantener estable `canonical_input`, `source_event_id` y `motivos[0]`.
    motivos_todos = [
        *motivos,
        *miembro.motivos_costo,
        *motivos_fee,
        *miembro.motivos_logistica,
    ]
    entrada = _construir_entrada(ctx, miembro, tuple(dict.fromkeys(motivos_todos)))

    if politica is None:
        resultado = _resultado_vacio(motivos_todos)
    else:
        resultado = calcular_contribucion(
            entrada, politica, valoracion_date=valoracion_date, universo_esperado=universo
        )
    motivos_finales = (
        resultado.motivos if resultado.motivos else tuple(dict.fromkeys(motivos_todos))
    )
    return _sellar_escenario(
        ctx,
        costo_fx=miembro.costo_fx,
        resultado=resultado,
        motivos=motivos_finales,
        componentes=_componentes_a_json(resultado),
        entrada_snapshot=_construir_entrada_snapshot(ctx, miembro, motivos_finales),
        resuelta=miembro.resuelta,
        miembro_product_id=product_id,
    )


def sembrar_negativo_desde_contexto(
    *,
    listing_id: int,
    contexto: dict[str, Any],
    motivo: str,
    snapshot_huella: str,
    valoracion_date: date,
    observed_at: datetime,
) -> EscenarioPersistible:
    """Arma `EscenarioPersistible` append-only de transicion negativa."""
    if motivo not in _MOTIVOS_TRANSICION_NEGATIVA:
        raise ValueError(f"motivo no soportado para transicion negativa: {motivo}")
    resultado = _resultado_vacio((motivo,))
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
        "motivos": list(resultado.motivos),
        "entrada": None,
        "resultado": {"estado": resultado.estado, "contribucion": None, "contribucion_pct": None},
    }
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
        estado=resultado.estado,
        motivos=resultado.motivos,
        componentes=_componentes_a_json(resultado),
        exclusiones=resultado.exclusiones,
        canonical_input=canonical,
        context_fingerprint=contexto["context_fingerprint"],
        source_event_id=_construir_source_event_id(id_canonica),
    )
