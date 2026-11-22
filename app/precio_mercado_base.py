"""Base compartida de los adaptadores de mercado del motor de precios.

Vive en `app/` (no en `app/spapi/` ni en `app/precio/`) para respetar ambos
candados de arquitectura:
- `test_meli_no_importa_spapi`: `app/meli/precio_mercado.py` nunca importa `app.spapi.*`.
- pureza de `app/precio/`: `app/precio/` no importa `psycopg` ni lectores IO.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from app.estimacion_insumos import oferta_para_cotizar
from app.estimacion_venta import DetalleFee, detalle_fee_desde_json
from app.precio import puerto
from app.precio.config import ConfigPrecio
from app.precio.config import leer_entero as _entero
from app.precio.fuentes import (
    cambios_previos,
    config_vigente_settings,
    historial_margenes,
    ultima_subida,
)
from app.precio.puerto import Lectura, MetaGoal
from app.precio.tipos import EntradaDecision, Escenario, Fees, ObservacionPricing
from app.precio.ventas import evaluar_senal


def detalle_fee_desde_dict(nodo: dict, *, con_hijos: bool = True) -> DetalleFee:
    """Normaliza un dict de fee a `DetalleFee`; lo corrupto levanta `ValueError`."""
    return detalle_fee_desde_json(nodo, estricto=False, con_hijos=con_hijos)


def tiene_tax(detalles: tuple[DetalleFee, ...]) -> bool:
    return any(
        (det.tax_amount is not None and det.tax_amount != Decimal(0))
        or tiene_tax(det.included_fee_details)
        for det in detalles
    )


def suma_top(detalles: tuple[DetalleFee, ...]) -> Decimal:
    return sum((det.final_fee for det in detalles), Decimal(0))


def dia_utc(valor: date | datetime) -> date:
    """`observed_at` a dia UTC (la fila ya puede traer `date`)."""
    return valor.astimezone(UTC).date() if isinstance(valor, datetime) else valor


def disponibilidad_estricta(
    activos: Sequence[bool],
    stocks: Sequence[int | None],
    *,
    solo_estado: bool = False,
) -> puerto.Disponibilidad:
    """Disponibilidad diaria conservadora (F.4 / M.4).

    `solo_estado=True` (FBM): solo exige estados positivos.
    `solo_estado=False` (FBA / MeLi): exige todos activos Y todos con stock > 0;
    cualquier inactivo o stock <= 0 es `no_disponible`.
    """
    if solo_estado:
        if not activos:
            return "sin_observacion"
        return "disponible" if all(activos) else "no_disponible"
    if any(s is not None and s <= 0 for s in stocks) or any(not b for b in activos):
        return "no_disponible"
    if activos and all(activos) and stocks and all(s is not None and s > 0 for s in stocks):
        return "disponible"
    return "sin_observacion"


def lectura_error(motivo: str, diagnostico: str) -> Lectura:
    """Lectura fallida uniforme sin inventar campos (regla 3)."""
    return Lectura(
        entrada=None,
        motivo=motivo,
        diagnostico=diagnostico,
        ids=None,
        cotizable=None,
        fees_escenario=motivo,
    )


def leer_trafico_min_y_rachas(
    conn: Any,
    platform: str,
    listing_ids: Sequence[int],
    hoy: date,
) -> tuple[int, dict[int, int]]:
    """Lee el umbral `precio_senal_trafico_min` y la racha previa por listing."""
    trafico_min = _entero(
        config_vigente_settings(conn), "precio_senal_trafico_min", minimo=20, maximo=5000
    )
    racha_por_lid = {
        fila[0]: fila[1]
        for fila in conn.execute(
            "SELECT DISTINCT ON (listing_id) listing_id, racha_senal"
            " FROM precio_decision"
            " WHERE listing_id = ANY(%s) AND platform = %s AND decision_date < %s"
            " ORDER BY listing_id, decision_date DESC",
            (list(listing_ids), platform, hoy),
        ).fetchall()
    }
    return trafico_min, racha_por_lid


def construir_lectura_unidad(
    conn: Any,
    *,
    platform: str,
    unidad: puerto.UnidadPrecio,
    meta: MetaGoal,
    cuenta: Any,
    fees_escenario: Fees | str,
    observacion: ObservacionPricing | None,
    buy_box: bool | None,
    historia: puerto.HistoriaUnidad,
    hoy: date,
    config: ConfigPrecio,
    racha_por_lid: Mapping[int, int | None],
    trafico_min: int,
    ids: dict[str, Any],
) -> Lectura:
    """Armado compartido de `Escenario`, `EntradaDecision` y `Lectura`."""
    cambios = cambios_previos(conn, unidad.listing_id, platform)
    senal = evaluar_senal(
        historia,
        hoy=hoy,
        config=config,
        racha_previa=int(racha_por_lid.get(unidad.listing_id) or 0),
        ultima_subida=ultima_subida(cambios),
        trafico_min=trafico_min,
    )
    escenario = Escenario(
        componentes=cuenta.componentes,
        fee_detalles=cuenta.fee_detalles,
        precio_cotizado=cuenta.precio_cotizado,
        iva_divisor=cuenta.iva_divisor,
        isr_tasa=cuenta.isr_tasa,
        precio_incluye_iva=cuenta.precio_incluye_iva,
        oferta_observada_en=cuenta.oferta_observada_en,
        referral_fee_iva_multiplicador=getattr(
            cuenta, "referral_fee_iva_multiplicador", Decimal("1")
        ),
    )
    cotizable = oferta_para_cotizar(conn, cuenta.refs.oferta_observation_id)
    entrada = EntradaDecision(
        listing_id=unidad.listing_id,
        platform=platform,
        product_id=unidad.producto_ancla,
        canal=cuenta.canal,
        mode=meta.mode,
        goal=meta.goal,
        escenario=escenario,
        pricing=observacion,
        senal=senal,
        cambios=cambios,
        historial=historial_margenes(conn, unidad.listing_id, platform),
        goal_vigente_desde=meta.goal_vigente_desde,
        motivo_estimacion=None,
        buy_box_is_own=buy_box,
    )
    return Lectura(
        entrada=entrada,
        motivo=None,
        diagnostico="",
        ids=ids,
        cotizable=cotizable,
        fees_escenario=fees_escenario,
        historia=historia,
    )
