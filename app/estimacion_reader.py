"""Reader batch de escenarios de estimacion (S5).

Separa lectura + overlay de frescura + JOIN de procedencia del camino de
persistencia en `estimacion_repository`. Desde REPRICING 02 (0.b) tambien
es el UNICO lugar que conoce la forma del JSON `componentes` del escenario:
`leer_cuenta` desarma ese JSON y devuelve la cuenta tipada (o su motivo).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import psycopg

from app.estimacion_insumos import FRESHNESS_LIMIT
from app.estimacion_venta import DetalleFee, detalles_fee_desde_json
from app.precio.puerto import RefsAuditoria
from app.precio.tipos import Componentes, Importe, MuestraEnvio, Tasas

__all__ = [
    "ProcedenciaRefs",
    "EscenarioLeido",
    "CuentaLeida",
    "leer_escenarios",
    "leer_cuenta",
    "ultimo_contexto",
]


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
    politica_label: str | None = None
    politica_settings: dict[str, Any] | None = None


# Fuentes S5 verificables (tablas/refs persistidas, no inventadas).
FUENTE_OFERTA = "oferta"
FUENTE_PRODUCT_FEES = "product_fees"
FUENTE_SKU_COST = "sku_cost"
FUENTE_POLITICA = "politica"


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
        " p.valid_from, p.valid_to, p.label, p.settings"
        " FROM estimacion_escenario e"
        " LEFT JOIN estimacion_oferta_observation o ON o.id = e.oferta_observation_id"
        " LEFT JOIN estimacion_fee_observation f ON f.id = e.fee_observation_id"
        " LEFT JOIN sku_cost c ON c.id = e.sku_cost_id"
        " LEFT JOIN estimacion_politica_version p ON p.id = e.politica_version_id"
        " WHERE e.listing_id = ANY(%s) AND e.observed_at <= %s"
        " ORDER BY e.listing_id, e.observed_at DESC, e.id DESC",
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
            politica_label=f[24],
            politica_settings=dict(f[25]) if f[25] is not None else None,
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


@dataclass(frozen=True)
class CuentaLeida:
    """El escenario vigente de un listing desarmado y validado.

    No es `Cuenta` completa: esta cuenta conserva el arbol de fees SIN
    partir (un escenario sin ReferralFee unico debe seguir siendo evaluable
    en tolerancia); quien necesita `(ref, fijo)` lo parte con
    `objetivo.derivar_ref_fijo`.
    """

    componentes: Componentes
    fee_detalles: tuple[DetalleFee, ...]
    precio_cotizado: Importe
    iva_divisor: Decimal
    isr_tasa: Decimal
    precio_incluye_iva: bool
    oferta_observada_en: datetime
    tasas: Tasas
    refs: RefsAuditoria
    canal: str
    # REPRICING 02 F.3: columnas del escenario (crudas). None en filas
    # viejas: cada llamador cae a derivar del arbol con `derivar_ref_fijo`.
    logistica_origen: str | None = None
    envio_muestra: MuestraEnvio | None = None
    fee_variable: Decimal | None = None
    fee_fijo: Decimal | None = None
    fee_cotizable: bool | None = None
    referral_fee_iva_multiplicador: Decimal = Decimal("1")


def _importe(valor, moneda: str, *, campo: str) -> Importe:
    if valor is None or moneda is None:
        raise ValueError(f"{campo} sin valor o sin moneda")
    return Importe(Decimal(valor), moneda)


def _componentes_por_nombre(componentes: list) -> dict:
    try:
        return {c["nombre"]: c for c in componentes}
    except (KeyError, TypeError) as exc:
        raise ValueError(f"componentes no son lista de dicts: {componentes!r}") from exc


_SQL_CUENTA_DETALLE = (
    "SELECT e.oferta_observation_id, e.fee_observation_id, e.envio_muestra_id,"
    " e.logistica_origen, e.miembro_product_id, e.fee_variable, e.fee_fijo, e.fee_cotizable,"
    " o.id, o.price_amount, o.price_currency, o.observed_at,"
    " f.id, f.total_fees, f.quoted_price_currency, f.fee_details,"
    " m.id, m.product_id, m.platform, m.canal, m.origen, m.valor, m.valor_currency,"
    " m.envios, m.donantes, m.ventana_desde, m.ventana_hasta, m.regla_version"
    " FROM estimacion_escenario e"
    " LEFT JOIN estimacion_oferta_observation o ON o.id = e.oferta_observation_id"
    " LEFT JOIN estimacion_fee_observation f ON f.id = e.fee_observation_id"
    " LEFT JOIN precio_envio_muestra m ON m.id = e.envio_muestra_id"
    " WHERE e.id = %s"
)


def leer_cuenta(
    conn: psycopg.Connection, listing_id: int, *, as_of: datetime
) -> CuentaLeida | str | None:
    """Escenario vigente ya tipado, su primer motivo o `None` sin escenario.

    `str` = el escenario existe pero no esta `disponible` (B4: sin mas
    lecturas, el motivo real de S2 viaja tal cual). `ValueError` = el
    escenario esta `disponible` pero sus piezas no cuadran (el llamador lo
    vuelve `escenario_incoherente` con este texto de diagnostico).
    """
    escenarios = leer_escenarios(conn, [listing_id], as_of=as_of)
    if not escenarios:
        return None
    esc = escenarios[0]
    if esc.estado != "disponible":
        return esc.motivos[0] if esc.motivos else "estimacion_motivo_desconocido"

    fila = conn.execute(_SQL_CUENTA_DETALLE, (esc.id,)).fetchone()
    if fila is None:
        raise ValueError(f"escenario {esc.id} desaparecio")
    (
        oferta_id,
        fee_id,
        muestra_id,
        l_origen,
        miembro,
        fee_var,
        fee_fijo,
        fee_cot,
        oferta_existe,
        p_valor,
        p_moneda,
        oferta_obs_en,
        fee_existe,
        fee_total,
        fee_moneda,
        fee_detalles_raw,
        m_id,
        m_pid,
        m_plat,
        m_canal,
        m_origen,
        m_valor,
        m_moneda,
        m_envios,
        m_donantes,
        m_desde,
        m_hasta,
        m_regla,
    ) = fila
    muestra = None
    if muestra_id is not None:
        if m_id is None:
            raise ValueError(f"muestra {muestra_id} del escenario {esc.id} ausente")
        muestra = MuestraEnvio(
            id=m_id,
            product_id=m_pid,
            plataforma=str(m_plat),
            canal=str(m_canal),
            origen=m_origen,
            valor=Importe(m_valor, str(m_moneda)),
            envios=m_envios,
            donantes=m_donantes,
            ventana=(m_desde, m_hasta),
            regla_version=m_regla,
        )
    if oferta_existe is None:
        raise ValueError(f"oferta {oferta_id} del escenario {esc.id} ausente")
    if fee_existe is None:
        raise ValueError(f"fee {fee_id} del escenario {esc.id} ausente")

    partes = _componentes_por_nombre(esc.componentes)
    es_s4 = esc.formula_version == "S4"
    try:
        bruto = partes["precio_bruto"]
        ingreso_c = partes["ingreso_normalizado"]
        costo_c = partes["costo_normalizado"]
        envio_c = partes["logistica"]
        isr_c = partes["isr"]
        fee_c = partes.get("fee_total")
        monedas = {
            p_moneda,
            ingreso_c.get("moneda_normalizada"),
            costo_c.get("moneda_normalizada"),
            fee_moneda,
            envio_c.get("moneda_normalizada"),
            isr_c.get("moneda_normalizada"),
        }
        if fee_c is not None and fee_c.get("moneda_normalizada") is not None:
            monedas.add(fee_c.get("moneda_normalizada"))
        if es_s4:
            ret_iva_c = partes["retencion_iva"]
            monedas.add(ret_iva_c.get("moneda_normalizada"))
            if esc.procedencia is None or esc.procedencia.politica_settings is None:
                raise ValueError(f"escenario S4 {esc.id} sin settings de politica")
            mult_raw = esc.procedencia.politica_settings.get("referral_fee_iva_multiplicador")
            if mult_raw is None:
                raise ValueError(f"escenario S4 {esc.id} sin referral_fee_iva_multiplicador")
            mult_ref = Decimal(str(mult_raw))
            if not mult_ref.is_finite() or mult_ref < Decimal("1"):
                raise ValueError(
                    f"escenario S4 {esc.id} con referral_fee_iva_multiplicador invalido: {mult_ref}"
                )
            fee_efectivo = (
                fee_c.get("importe_normalizado")
                if fee_c is not None and fee_c.get("importe_normalizado") is not None
                else fee_total
            )
            retenciones_total = Decimal(str(isr_c.get("importe_normalizado"))) + Decimal(
                str(ret_iva_c.get("importe_normalizado"))
            )
        else:
            mult_ref = Decimal("1")
            fee_efectivo = fee_total
            retenciones_total = isr_c.get("importe_normalizado")
        if len(monedas) != 1:
            raise ValueError(
                f"escenario {esc.id} con monedas divergentes: {sorted(map(str, monedas))}"
            )
        comp = Componentes(
            p_actual=_importe(p_valor, p_moneda, campo="p_actual"),
            ingreso=_importe(ingreso_c.get("importe_normalizado"), p_moneda, campo="ingreso"),
            costo=_importe(costo_c.get("importe_normalizado"), p_moneda, campo="costo"),
            fees=_importe(fee_efectivo, p_moneda, campo="fees"),
            envio=_importe(envio_c.get("importe_normalizado"), p_moneda, campo="envio"),
            isr=_importe(retenciones_total, p_moneda, campo="isr"),
        )
        if comp.ingreso.valor <= 0:
            raise ValueError(f"escenario {esc.id} con ingreso no positivo")
        divisor_iva = Decimal(bruto.get("importe_normalizado")) / comp.ingreso.valor
        isr_tasa = comp.isr.valor / comp.ingreso.valor
        try:
            detalles = detalles_fee_desde_json(fee_detalles_raw, estricto=False)
        except ValueError as exc:
            raise ValueError(f"fee_details del escenario {esc.id}: {exc}") from exc
    except KeyError as exc:
        raise ValueError(f"escenario {esc.id} sin componente {exc}") from exc
    except (InvalidOperation, TypeError, AttributeError) as exc:
        raise ValueError(f"escenario {esc.id} con componentes podridos: {exc}") from exc

    return CuentaLeida(
        componentes=comp,
        fee_detalles=detalles,
        precio_cotizado=_importe(p_valor, p_moneda, campo="precio_cotizado"),
        iva_divisor=divisor_iva,
        isr_tasa=isr_tasa,
        precio_incluye_iva=divisor_iva != 1,
        oferta_observada_en=oferta_obs_en,
        tasas=Tasas(
            iva_divisor=divisor_iva,
            precio_incluye_iva=divisor_iva != 1,
            retencion_tasa=isr_tasa,
        ),
        refs=RefsAuditoria(
            escenario_id=esc.id,
            fee_observation_id=fee_id,
            oferta_observation_id=oferta_id,
            envio_muestra_id=muestra_id,
            product_id=miembro,
        ),
        canal=esc.canal,
        logistica_origen=l_origen,
        envio_muestra=muestra,
        fee_variable=fee_var,
        fee_fijo=fee_fijo,
        fee_cotizable=fee_cot,
        referral_fee_iva_multiplicador=mult_ref,
    )


def ultimo_contexto(
    conn: psycopg.Connection,
    listing_id: int,
    *,
    as_of: datetime,
) -> dict[str, Any] | None:
    """Ultimo escenario disponible del listing hasta as_of; None sin contexto."""
    fila = conn.execute(
        "SELECT canal, context_fingerprint, platform, seller_sku, asin, source_event_id"
        " FROM estimacion_escenario"
        " WHERE listing_id = %s"
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
