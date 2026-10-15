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
from app.estimacion_venta import DetalleFee
from app.precio.puerto import RefsAuditoria
from app.precio.tipos import Componentes, Importe, MuestraEnvio, Tasas

__all__ = [
    "ProcedenciaRefs",
    "EscenarioLeido",
    "CuentaLeida",
    "leer_escenarios",
    "leer_cuenta",
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
        " p.valid_from, p.valid_to, p.label"
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


# ---------------------------------------------------------------------------
# La cuenta del escenario, ya tipada (el unico lugar que conoce la forma
# del JSON `componentes` del escenario)
# ---------------------------------------------------------------------------


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
    # REPRICING 02 F.3: columnas del escenario (crudas; F.4 las prefiere al
    # arbol). None en filas viejas: el llamador cae a `derivar_ref_fijo`.
    logistica_origen: str | None = None
    envio_muestra: MuestraEnvio | None = None
    fee_variable: Decimal | None = None
    fee_fijo: Decimal | None = None
    fee_cotizable: bool | None = None


def _detalle_fee(nodo: dict) -> DetalleFee:
    """`fee_details` normalizado (forma de `parsear_respuesta_fees`) a arbol."""
    if not isinstance(nodo, dict):
        raise ValueError(f"detalle de fee no es objeto: {nodo!r}")
    try:
        tasa_raw = nodo.get("tax_amount")
        hijos_raw = nodo.get("included_fee_details") or ()
        return DetalleFee(
            fee_type=nodo["fee_type"],
            final_fee=Decimal(str(nodo["final_fee"])),
            tax_amount=None if tasa_raw is None else Decimal(str(tasa_raw)),
            included_fee_details=tuple(_detalle_fee(hijo) for hijo in hijos_raw),
        )
    except (KeyError, InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"detalle de fee invalido: {nodo!r}") from exc


def _importe(valor, moneda: str, *, campo: str) -> Importe:
    if valor is None or moneda is None:
        raise ValueError(f"{campo} sin valor o sin moneda")
    return Importe(Decimal(valor), moneda)


def _componentes_por_nombre(componentes: list) -> dict:
    try:
        return {c["nombre"]: c for c in componentes}
    except (KeyError, TypeError) as exc:
        raise ValueError(f"componentes no son lista de dicts: {componentes!r}") from exc


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

    fila_ids = conn.execute(
        "SELECT oferta_observation_id, fee_observation_id, envio_muestra_id,"
        " logistica_origen, miembro_product_id, fee_variable, fee_fijo, fee_cotizable"
        " FROM estimacion_escenario WHERE id = %s",
        (esc.id,),
    ).fetchone()
    if fila_ids is None:
        raise ValueError(f"escenario {esc.id} desaparecio")
    oferta_id, fee_id, muestra_id, l_origen, miembro, fee_var, fee_fijo, fee_cot = fila_ids
    muestra = None
    if muestra_id is not None:
        fila_muestra = conn.execute(
            "SELECT id, product_id, platform, canal, origen, valor, valor_currency,"
            " envios, donantes, ventana_desde, ventana_hasta, regla_version"
            " FROM precio_envio_muestra WHERE id = %s",
            (muestra_id,),
        ).fetchone()
        if fila_muestra is None:
            raise ValueError(f"muestra {muestra_id} del escenario {esc.id} ausente")
        (
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
        ) = fila_muestra
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
    oferta = conn.execute(
        "SELECT price_amount, price_currency, observed_at"
        " FROM estimacion_oferta_observation WHERE id = %s",
        (oferta_id,),
    ).fetchone()
    if oferta is None:
        raise ValueError(f"oferta {oferta_id} del escenario {esc.id} ausente")
    p_valor, p_moneda, oferta_obs_en = oferta
    fee = conn.execute(
        "SELECT total_fees, quoted_price_currency, fee_details FROM estimacion_fee_observation"
        " WHERE id = %s",
        (fee_id,),
    ).fetchone()
    if fee is None:
        raise ValueError(f"fee {fee_id} del escenario {esc.id} ausente")
    fee_total, fee_moneda, fee_detalles_raw = fee

    partes = _componentes_por_nombre(esc.componentes)
    try:
        bruto = partes["precio_bruto"]
        ingreso_c = partes["ingreso_normalizado"]
        costo_c = partes["costo_normalizado"]
        envio_c = partes["logistica"]
        isr_c = partes["isr"]
        monedas = {
            p_moneda,
            ingreso_c.get("moneda_normalizada"),
            costo_c.get("moneda_normalizada"),
            fee_moneda,
            envio_c.get("moneda_normalizada"),
            isr_c.get("moneda_normalizada"),
        }
        if len(monedas) != 1:
            raise ValueError(
                f"escenario {esc.id} con monedas divergentes: {sorted(map(str, monedas))}"
            )
        comp = Componentes(
            p_actual=_importe(p_valor, p_moneda, campo="p_actual"),
            ingreso=_importe(ingreso_c.get("importe_normalizado"), p_moneda, campo="ingreso"),
            costo=_importe(costo_c.get("importe_normalizado"), p_moneda, campo="costo"),
            fees=_importe(fee_total, p_moneda, campo="fees"),
            envio=_importe(envio_c.get("importe_normalizado"), p_moneda, campo="envio"),
            isr=_importe(isr_c.get("importe_normalizado"), p_moneda, campo="isr"),
        )
        if comp.ingreso.valor <= 0:
            raise ValueError(f"escenario {esc.id} con ingreso no positivo")
        divisor_iva = Decimal(bruto.get("importe_normalizado")) / comp.ingreso.valor
        isr_tasa = comp.isr.valor / comp.ingreso.valor
        try:
            detalles = tuple(_detalle_fee(nodo) for nodo in (fee_detalles_raw or []))
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
    )
