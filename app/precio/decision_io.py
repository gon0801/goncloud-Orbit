"""Cotizacion y persistencia de decisiones del motor de precios (REPRICING 01 A.5).

Extraido de `app/precio/corrida.py` (Ola E): agrupa la maquina de cotizaciones
(`cotizar_y_decidir`), la resolucion de moneda por observaciones
(`moneda_contexto`) y la escritura de filas (`persistir_decision`,
`_persistir_uno`). Escribe UNICAMENTE en `precio_cotizacion` y
`precio_decision`.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal

import psycopg
import psycopg.errors
from psycopg.types.json import Json

from app.estimacion_venta import DetalleFee
from app.precio.config import ConfigPrecio
from app.precio.reglas import decidir
from app.precio.tipos import Decision, EntradaDecision, PideCotizacion
from app.redaction import scrub

logger = logging.getLogger(__name__)

__all__ = [
    "ERRORES_DE_FILA",
    "_rechazo_de_base",
    "_no_evaluado",
    "_frenado",
    "_linea",
    "cotizar_y_decidir",
    "moneda_contexto",
    "persistir_decision",
    "_persistir_uno",
]

# ARREGLOS 03 P-B: rechazos de la base por el DATO de una fila (CHECK,
# llave, unico, numero fuera de rango): son de esa publicacion y la aislan
# (la conexion va en autocommit: un INSERT rechazado no deja transaccion
# rota). Cualquier otro `psycopg.Error` es de la infra y aborta la corrida.
ERRORES_DE_FILA = (psycopg.IntegrityError, psycopg.DataError)


def _rechazo_de_base(errores: list, lid, platform: str, exc: Exception) -> None:
    """P-B: deja el error (con scrub) de la fila que la base rechazo."""
    msg = scrub(f"listing={lid} {platform} rechazo_de_base: {exc.__class__.__name__}: {exc}")
    logger.error("corrida %s", msg)
    errores.append(msg)


def _decision_sin_cuenta(
    resultado: str, goal: Decimal, mode: str, motivo: str, diagnostico: str = ""
) -> Decision:
    if diagnostico:
        logger.info("corrida %s %s: %s", resultado, motivo, scrub(diagnostico))
    return Decision(
        resultado=resultado,
        motivo=motivo,
        m_actual=None,
        goal=goal,
        p_actual=None,
        p_objetivo=None,
        p_aplicado=None,
        componentes=None,
        u15=None,
        u60=None,
        n15=None,
        n60=None,
        racha=None,
        perdiendo=None,
        aplicado=False,
        mode=mode,
    )


def _no_evaluado(goal: Decimal, mode: str, motivo: str, diagnostico: str = "") -> Decision:
    return _decision_sin_cuenta("no_evaluado", goal, mode, motivo, diagnostico)


def _frenado(goal: Decimal, mode: str, motivo: str, diagnostico: str) -> Decision:
    return _decision_sin_cuenta("frenado", goal, mode, motivo, diagnostico)


def _linea(lid: int, platform: str, mode: str, decision: Decision) -> str:
    return f"listing={lid} {platform} {mode} {decision.resultado} {decision.motivo or '-'}"


def _evento_cotizacion(listing_id: int, platform: str, hoy: date, intento: int) -> str:
    """Identidad de la cotizacion: par + dia + intento (UNIQUE en 0039)."""
    return f"precio-cotiz-{listing_id}-{platform}-{hoy.isoformat()}-{intento}"


def _detalle_a_dict(detalle: DetalleFee) -> dict:
    """Arbol `DetalleFee` -> dicts de `precio_cotizacion.fee_details`."""
    return {
        "fee_type": detalle.fee_type,
        "final_fee": str(detalle.final_fee),
        "tax_amount": None if detalle.tax_amount is None else str(detalle.tax_amount),
        "included_fee_details": [
            _detalle_a_dict(incluido) for incluido in detalle.included_fee_details
        ],
    }


def _persistir_cotizacion(
    conn: psycopg.Connection,
    *,
    listing_id: int,
    platform: str,
    hoy: date,
    oferta_id: int,
    pedido: PideCotizacion,
    cot,
) -> int:
    """La cotizacion nace ANTES que la decision (S5 r3-1): es su insumo."""
    evento = _evento_cotizacion(listing_id, platform, hoy, pedido.intento)
    if cot.fees is not None:
        total, total_moneda = cot.fees.total.valor, pedido.precio.moneda
        detalles = [_detalle_a_dict(det) for det in cot.fees.detalle]
        estimada_en, codigo, estado = cot.estimada_en, None, "success"
    else:
        total, total_moneda, detalles = None, None, []
        estimada_en, codigo, estado = None, cot.error_code or "desconocido", "error"
    return conn.execute(
        "INSERT INTO precio_cotizacion (listing_id, platform, intento, oferta_observation_id,"
        " quoted_price, quoted_price_currency, total_fees, total_fees_currency, fee_details,"
        " fees_estimated_at, estado, error_code, source_event_id)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (
            listing_id,
            platform,
            pedido.intento,
            oferta_id,
            pedido.precio.valor,
            pedido.precio.moneda,
            total,
            total_moneda,
            Json(detalles),
            estimada_en,
            estado,
            codigo,
            evento,
        ),
    ).fetchone()[0]


def cotizar_y_decidir(
    conn: psycopg.Connection,
    entrada: EntradaDecision,
    *,
    mercado,
    oferta,
    oferta_id: int,
    hoy: date,
    config: ConfigPrecio,
    fees_escenario,
) -> tuple[Decision, int | None]:
    """Maquina S4 #3: como maximo dos cotizaciones reales, persistidas.

    Devuelve `(decision_final, cotizacion_id_usada)`. Cotiza con
    `mercado.cotizar` (ya `puerto.Cotizacion`, fees partidos) y decide con
    `decidir(..., cotizaciones, fees_escenario)`.
    """
    hechas: list = []
    ultimo_id: int | None = None
    for _ in range(3):
        salida = decidir(
            entrada,
            hoy=hoy,
            config=config,
            cotizaciones=tuple(hechas),
            fees_escenario=fees_escenario,
        )
        if not isinstance(salida, PideCotizacion):
            return salida, ultimo_id
        evento = _evento_cotizacion(entrada.listing_id, entrada.platform, hoy, salida.intento)
        reuso = mercado.cotizacion_guardada(
            conn,
            evento=evento,
            listing_id=entrada.listing_id,
            hoy=hoy,
            oferta_id=oferta_id,
            pedido=salida,
        )
        if reuso is not None:
            ultimo_id, cot = reuso
        else:
            cot = mercado.cotizar(conn, oferta, salida.precio)
            try:
                ultimo_id = _persistir_cotizacion(
                    conn,
                    listing_id=entrada.listing_id,
                    platform=entrada.platform,
                    hoy=hoy,
                    oferta_id=oferta_id,
                    pedido=salida,
                    cot=cot,
                )
            except psycopg.errors.UniqueViolation:
                reuso = mercado.cotizacion_guardada(
                    conn,
                    evento=evento,
                    listing_id=entrada.listing_id,
                    hoy=hoy,
                    oferta_id=oferta_id,
                    pedido=salida,
                )
                if reuso is None:
                    raise
                ultimo_id, cot = reuso
        hechas.append(cot)
    raise AssertionError("la maquina pidio una tercera cotizacion (S4 #3: maximo dos)")


def moneda_contexto(conn: psycopg.Connection, listing_id: int, platform: str) -> str:
    """Moneda para `p_actual_currency` cuando la decision no trae P."""
    fila = conn.execute(
        "SELECT price_currency FROM estimacion_oferta_observation"
        " WHERE listing_id = %s AND platform = %s"
        " ORDER BY observed_at DESC LIMIT 1",
        (listing_id, platform),
    ).fetchone()
    if fila is not None:
        return fila[0]
    fila = conn.execute(
        "SELECT price_currency FROM estimacion_oferta_observation"
        " WHERE platform = %s"
        " ORDER BY observed_at DESC LIMIT 1",
        (platform,),
    ).fetchone()
    if fila is not None:
        return fila[0]
    raise ValueError(f"sin moneda observable para {platform}: sin ofertas")


def persistir_decision(
    conn: psycopg.Connection,
    decision: Decision,
    *,
    listing_id: int,
    platform: str,
    product_id: int | None,
    canal: str | None,
    escenario_id: int | None,
    fee_observation_id: int | None,
    cotizacion_id: int | None,
    config_version_id: int | None,
    corrida_id: int,
    goal_id: int | None = None,
    l_origen: str | None = None,
    envio_muestra_id: int | None = None,
    senal_evidencia: str | None = None,
    verificacion: str | None = None,
) -> int:
    """Una fila por (listing, platform, dia); `corrida_id` obligatorio."""
    comp = decision.componentes
    moneda = comp.p_actual.moneda if comp else moneda_contexto(conn, listing_id, platform)
    return conn.execute(
        "INSERT INTO precio_decision (listing_id, platform, resultado, motivo, product_id,"
        " canal, m_actual, goal, p_actual, p_actual_currency, p_objetivo, p_objetivo_currency,"
        " p_aplicado, p_aplicado_currency, i_valor, i_currency, c_valor, c_currency,"
        " f_valor, f_currency, l_valor, l_currency, r_valor, r_currency, escenario_id,"
        " fee_observation_id, cotizacion_id, u15, u60, n15, n60, racha_senal, perdiendo,"
        " buy_box_is_own, mode, config_version_id, corrida_id, goal_id, l_origen,"
        " envio_muestra_id, senal_evidencia, verificacion)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
        " %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
        " %s, %s, %s, %s)"
        " RETURNING id",
        (
            listing_id,
            platform,
            decision.resultado,
            decision.motivo,
            product_id,
            canal,
            decision.m_actual,
            decision.goal,
            comp.p_actual.valor if comp else None,
            moneda,
            decision.p_objetivo.valor if decision.p_objetivo else None,
            decision.p_objetivo.moneda if decision.p_objetivo else moneda,
            decision.p_aplicado.valor if decision.p_aplicado else None,
            decision.p_aplicado.moneda if decision.p_aplicado else moneda,
            comp.ingreso.valor if comp else None,
            comp.ingreso.moneda if comp else moneda,
            comp.costo.valor if comp else None,
            comp.costo.moneda if comp else moneda,
            comp.fees.valor if comp else None,
            comp.fees.moneda if comp else moneda,
            comp.envio.valor if comp else None,
            comp.envio.moneda if comp else moneda,
            comp.isr.valor if comp else None,
            comp.isr.moneda if comp else moneda,
            escenario_id,
            fee_observation_id,
            cotizacion_id,
            decision.u15,
            decision.u60,
            decision.n15,
            decision.n60,
            decision.racha,
            decision.perdiendo,
            decision.buy_box_is_own,
            decision.mode,
            config_version_id,
            corrida_id,
            goal_id,
            l_origen,
            envio_muestra_id,
            senal_evidencia,
            verificacion,
        ),
    ).fetchone()[0]


def _persistir_uno(
    conn: psycopg.Connection,
    decision: Decision,
    *,
    lid: int,
    mode: str,
    prod_id: int,
    canal,
    meta,
    platform: str,
    goal_id: int,
    cfg_id,
    corrida_id: int,
    lineas: list,
    errores: list,
    senal_evidencia: str | None = None,
    persistir_fn=persistir_decision,
) -> tuple[int, int]:
    """Persiste UNA decision ya decidida; devuelve `(persistida, saltada)` (0/1)."""
    verificacion = (
        (decision.verificacion or "cotizada") if decision.resultado in ("subir", "bajar") else None
    )
    try:
        producto = meta.get("product_id") if meta else None
        if producto is None:
            miembro = conn.execute(
                "SELECT product_id FROM v_precio_unidad_miembro"
                " WHERE listing_id = %s AND platform = %s AND product_id = %s LIMIT 1",
                (lid, platform, prod_id),
            ).fetchone()
            producto = miembro[0] if miembro else None
        persistir_fn(
            conn,
            decision,
            listing_id=lid,
            platform=platform,
            product_id=producto,
            canal=canal,
            escenario_id=meta["escenario_id"] if meta else None,
            fee_observation_id=meta["fee_observation_id"] if meta else None,
            cotizacion_id=meta["cotizacion_id"] if meta else None,
            config_version_id=cfg_id,
            corrida_id=corrida_id,
            goal_id=goal_id,
            l_origen=meta["logistica_origen"] if meta else None,
            envio_muestra_id=meta["envio_muestra_id"] if meta else None,
            senal_evidencia=senal_evidencia,
            verificacion=verificacion,
        )
    except ERRORES_DE_FILA as exc:
        if isinstance(exc, psycopg.errors.CheckViolation) and "goal_reemplazado" in str(exc):
            msg = scrub(f"listing={lid} {platform} goal_cambiado: {exc}")
            logger.error("corrida %s", msg)
            errores.append(msg)
            return 0, 1
        _rechazo_de_base(errores, lid, platform, exc)
        return 0, 0
    except psycopg.Error:
        raise
    except Exception as exc:
        msg = scrub(f"listing={lid} {platform} {exc.__class__.__name__}: {exc}")
        logger.error("corrida %s", msg)
        errores.append(msg)
        return 0, 0
    lineas.append(_linea(lid, platform, mode, decision))
    return 1, 0
