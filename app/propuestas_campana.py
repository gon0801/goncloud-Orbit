"""Propuestas economicas de campana, sin escrituras a Amazon Ads.

El reporte spCampaigns llega a ads_metric_observation con kind=campaign.
Cada evaluacion usa una sola ventana madura de esa entidad. La tabla propia
guarda episodios y nunca participa en apply_queue.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal

import psycopg
from psycopg.types.json import Jsonb

from app.optimizer import goals, windows
from app.optimizer.bid import PLATAFORMAS_MONEDA

UMBRAL_EXCESO = {"USD": Decimal("80"), "MXN": Decimal("1000")}
TIPO_RIESGO = "exceso_economico"
VERSION_POLITICA = "ads-proteccion-01-c1"
MULTIPLO_TARGET = Decimal("3")
MAX_EDAD_ESTADO = dt.timedelta(hours=48)


@dataclass(frozen=True)
class DatoCampana:
    campaign_id: int
    platform: str
    external_id: str
    nombre: str | None
    status: str | None
    status_synced_at: dt.datetime | None
    window_start: dt.date | None
    window_end: dt.date | None
    fechas_distintas: int
    cost: Decimal | None
    revenue: Decimal | None
    currency: str | None
    observed_at: dt.datetime | None
    target_pct: Decimal | None
    target_source: str | None
    goal_enabled: bool


@dataclass(frozen=True)
class Evaluacion:
    dato: DatoCampana
    riesgo: bool
    motivo: str
    exceso: Decimal | None
    acos_pct: Decimal | None


def evalua(dato: DatoCampana, *, decidido: dt.datetime | None = None) -> Evaluacion:
    """Evalua el limite sellado; None abstiene y PAUSED nunca abre propuesta."""
    motivo = None
    if decidido is not None and (
        dato.status_synced_at is None
        or decidido - dato.status_synced_at > MAX_EDAD_ESTADO
        or dato.status_synced_at > decidido
    ):
        motivo = "estado_vencido"
    elif dato.status != "ENABLED":
        motivo = "campana_no_enabled"
    elif not dato.goal_enabled:
        motivo = "goal_disabled"
    elif dato.window_end is None or dato.fechas_distintas < windows.MIN_FECHAS_COMPLETITUD:
        motivo = "ventana_incompleta"
    elif decidido is not None and dato.window_end > decidido.astimezone(
        dt.UTC
    ).date() - dt.timedelta(days=windows.DIAS_MADUREZ_CORTES):
        motivo = "ventana_inmadura"
    elif dato.currency != PLATAFORMAS_MONEDA.get(dato.platform):
        motivo = "moneda_invalida"
    elif dato.cost is None:
        motivo = "cost_ausente"
    elif dato.revenue is None:
        motivo = "revenue_ausente"
    elif dato.target_pct is None or dato.target_source is None:
        motivo = "target_ausente"
    elif (
        not dato.cost.is_finite()
        or dato.cost < 0
        or not dato.revenue.is_finite()
        or dato.revenue < 0
        or not dato.target_pct.is_finite()
        or dato.target_pct <= 0
    ):
        motivo = "dato_invalido"
    if motivo is not None:
        return Evaluacion(dato, False, motivo, None, None)
    assert dato.cost is not None and dato.revenue is not None and dato.target_pct is not None
    esperado = dato.target_pct * dato.revenue / Decimal("100")
    exceso = dato.cost - esperado
    acos = dato.cost * Decimal("100") / dato.revenue if dato.revenue > 0 else None
    riesgo = dato.cost > MULTIPLO_TARGET * esperado and exceso >= UMBRAL_EXCESO[dato.currency]
    return Evaluacion(dato, riesgo, TIPO_RIESGO if riesgo else "bajo_limite", exceso, acos)


def transicion(ultimo_estado: str | None, *, riesgo: bool, status: str | None, reset: bool) -> str:
    """Un descarte o pausa externa no reabre sin una ventana observada segura."""
    if status == "PAUSED":
        return "cerrar_estado_pausado" if ultimo_estado == "open" else "mantener"
    if not riesgo:
        if ultimo_estado == "open":
            return "cerrar_riesgo"
        if ultimo_estado is not None and not reset:
            return "reiniciar"
        return "mantener"
    if ultimo_estado == "open":
        return "actualizar"
    return "abrir" if ultimo_estado is None or reset else "mantener"


_SQL_CAMPANAS = """
SELECT c.id, c.external_id, c.name, s.status, s.synced_at, s.acos_target
  FROM ad_entity c
  LEFT JOIN ad_entity_state s ON s.ad_entity_id = c.id
 WHERE c.platform = %s::platform AND c.kind = 'campaign'
 ORDER BY c.id
"""

_SQL_MAX_FECHA_ASOF = """
SELECT max(metric_date)
  FROM ads_metric_observation
 WHERE ad_entity_id = %s AND observed_at <= %s
"""

_SQL_AGREGADO_ASOF = """
WITH ultima AS (
    SELECT DISTINCT ON (metric_date)
           metric_date, metric_currency, cost, ad_revenue, observed_at
      FROM ads_metric_observation
     WHERE ad_entity_id = %s AND metric_date BETWEEN %s AND %s
       AND observed_at <= %s
     ORDER BY metric_date, observed_at DESC, source_report_id DESC NULLS LAST
)
SELECT count(*)::integer, min(metric_currency),
       CASE WHEN bool_and(cost IS NOT NULL) THEN sum(cost) END,
       CASE WHEN bool_and(ad_revenue IS NOT NULL) THEN sum(ad_revenue) END,
       max(observed_at)
  FROM ultima
"""

_SQL_ULTIMO = """
SELECT id, status, reset_at
  FROM ads_campaign_proposal
 WHERE campaign_id = %s AND risk_type = %s
 ORDER BY id DESC LIMIT 1 FOR UPDATE
"""

_SQL_INSERTAR = """
INSERT INTO ads_campaign_proposal
    (campaign_id, platform, campaign_external_id, risk_type, status,
     first_seen_at, last_seen_at, window_start, window_end, observed_at,
     cost, revenue, currency, target_pct, target_source, excess, acos_pct,
     campaign_status, status_synced_at, evidence)
VALUES (%s, %s::platform, %s, %s, 'open', %s, %s, %s, %s, %s,
        %s, %s, %s::currency, %s, %s, %s, %s, %s, %s, %s)
"""

_SQL_ACTUALIZAR = """
UPDATE ads_campaign_proposal
   SET last_seen_at = %s, window_start = %s, window_end = %s,
       observed_at = %s, cost = %s, revenue = %s, currency = %s::currency,
       target_pct = %s, target_source = %s, excess = %s, acos_pct = %s,
       campaign_status = %s, status_synced_at = %s, evidence = %s
 WHERE id = %s AND status = 'open'
"""


def _evidencia(evaluacion: Evaluacion) -> dict:
    dato = evaluacion.dato
    return {
        "motivo": evaluacion.motivo,
        "fuente_dinero": "spCampaigns",
        "policy_version": VERSION_POLITICA,
        "limite_relativo": str(MULTIPLO_TARGET),
        "limite_exceso": (
            str(UMBRAL_EXCESO[dato.currency]) if dato.currency in UMBRAL_EXCESO else None
        ),
        "campaign_id": dato.external_id,
        "nombre": dato.nombre,
        "fechas_distintas": dato.fechas_distintas,
        "cost": str(dato.cost) if dato.cost is not None else None,
        "revenue": str(dato.revenue) if dato.revenue is not None else None,
        "currency": dato.currency,
        "target_pct": str(dato.target_pct) if dato.target_pct is not None else None,
        "target_source": dato.target_source,
        "excess": str(evaluacion.exceso) if evaluacion.exceso is not None else None,
        "acos_pct": str(evaluacion.acos_pct) if evaluacion.acos_pct is not None else None,
        "window_start": dato.window_start.isoformat() if dato.window_start else None,
        "window_end": dato.window_end.isoformat() if dato.window_end else None,
        "campaign_status": dato.status,
        "status_synced_at": (dato.status_synced_at.isoformat() if dato.status_synced_at else None),
        "observed_at": dato.observed_at.isoformat() if dato.observed_at else None,
    }


def _ventana_asof(
    conn: psycopg.Connection, campaign_id: int, decidido: dt.datetime
) -> tuple[
    dt.date | None,
    dt.date | None,
    int,
    str | None,
    Decimal | None,
    Decimal | None,
    dt.datetime | None,
]:
    """Mismo corte maduro del motor, con vintage conocido al decidir."""
    max_fecha = conn.execute(_SQL_MAX_FECHA_ASOF, (campaign_id, decidido)).fetchone()[0]
    if max_fecha is None:
        return (None, None, 0, None, None, None, None)
    fin = windows.fin_ventana_cortes(max_fecha, decidido)
    inicio = windows.inicio_ventana(fin)
    cuenta, moneda, cost, revenue, observado = conn.execute(
        _SQL_AGREGADO_ASOF, (campaign_id, inicio, fin, decidido)
    ).fetchone()
    return (inicio, fin, cuenta, moneda, cost, revenue, observado)


def _persiste(conn: psycopg.Connection, evaluacion: Evaluacion, decidido: dt.datetime) -> None:
    dato = evaluacion.dato
    if evaluacion.motivo not in (TIPO_RIESGO, "bajo_limite") and not (
        dato.status == "PAUSED" and evaluacion.motivo == "campana_no_enabled"
    ):
        return
    previo = conn.execute(_SQL_ULTIMO, (dato.campaign_id, TIPO_RIESGO)).fetchone()
    prev_id, prev_estado, reset = previo if previo is not None else (None, None, None)
    accion = transicion(
        prev_estado, riesgo=evaluacion.riesgo, status=dato.status, reset=reset is not None
    )
    if accion == "abrir":
        conn.execute(
            _SQL_INSERTAR,
            (
                dato.campaign_id,
                dato.platform,
                dato.external_id,
                TIPO_RIESGO,
                decidido,
                decidido,
                dato.window_start,
                dato.window_end,
                dato.observed_at,
                dato.cost,
                dato.revenue,
                dato.currency,
                dato.target_pct,
                dato.target_source,
                evaluacion.exceso,
                evaluacion.acos_pct,
                dato.status,
                dato.status_synced_at,
                Jsonb(_evidencia(evaluacion)),
            ),
        )
    elif accion == "actualizar":
        conn.execute(
            _SQL_ACTUALIZAR,
            (
                decidido,
                dato.window_start,
                dato.window_end,
                dato.observed_at,
                dato.cost,
                dato.revenue,
                dato.currency,
                dato.target_pct,
                dato.target_source,
                evaluacion.exceso,
                evaluacion.acos_pct,
                dato.status,
                dato.status_synced_at,
                Jsonb(_evidencia(evaluacion)),
                prev_id,
            ),
        )
    elif accion in ("cerrar_estado_pausado", "cerrar_riesgo"):
        status = "paused_observed" if accion == "cerrar_estado_pausado" else "resolved"
        motivo_cierre = (
            "estado_pausado_observado" if accion == "cerrar_estado_pausado" else evaluacion.motivo
        )
        conn.execute(
            """UPDATE ads_campaign_proposal
                  SET status = %s, closed_at = %s, last_seen_at = %s,
                      close_reason = %s, close_evidence = %s, reset_at = %s
                WHERE id = %s AND status = 'open'""",
            (
                status,
                decidido,
                decidido,
                motivo_cierre,
                Jsonb(_evidencia(evaluacion)),
                decidido if accion == "cerrar_riesgo" else None,
                prev_id,
            ),
        )
    elif accion == "reiniciar":
        conn.execute(
            "UPDATE ads_campaign_proposal SET reset_at = %s WHERE id = %s AND reset_at IS NULL",
            (decidido, prev_id),
        )


def lee_evaluaciones(
    conn: psycopg.Connection,
    *,
    platform: str,
    decidido: dt.datetime,
    settings: dict,
    target_margen: Decimal | None,
) -> tuple[Evaluacion, ...]:
    """Lee todas las campanas bajo el snapshot del ciclo; no escribe."""
    if decidido.tzinfo is None:
        raise ValueError("decidido debe incluir zona horaria")
    filas = conn.execute(_SQL_CAMPANAS, (platform,)).fetchall()
    ids = [fila[0] for fila in filas]
    goals_rows = conn.execute(
        """SELECT scope, ad_entity_id, target_acos_pct, enabled, mode
             FROM ads_optimizer_goal
            WHERE platform = %s::platform OR ad_entity_id = ANY(%s::bigint[])""",
        (platform, ids),
    ).fetchall()
    por_campana = {f[1]: f for f in goals_rows if f[0] == "campaign"}
    goal_plataforma = next((f for f in goals_rows if f[0] == "platform"), None)
    evaluaciones: list[Evaluacion] = []
    for camp_id, external_id, nombre, status, synced_at, cache_target in filas:
        elegido = por_campana.get(camp_id, goal_plataforma)
        enabled = elegido is not None and elegido[3] and elegido[4] != "off"
        try:
            objetivo = elegido[2] if elegido is not None else None
            scope = elegido[0] if elegido is not None else None
            setting = goals.target_desde_settings(settings, platform)
            target = goals.cascada_target_acos(
                objetivo, setting, cache_target, target_margen, scope
            )
            source = goals.peldano_target_acos(
                objetivo, scope, target_margen, setting, cache_target
            )
        except (ValueError, ArithmeticError):
            target, source = None, None
        inicio, fin, fechas, moneda, cost, revenue, observado = _ventana_asof(
            conn, camp_id, decidido
        )
        dato = DatoCampana(
            camp_id,
            platform,
            external_id,
            nombre,
            status,
            synced_at,
            inicio,
            fin,
            fechas,
            cost,
            revenue,
            moneda,
            observado,
            target,
            source,
            bool(enabled),
        )
        evaluaciones.append(evalua(dato, decidido=decidido))
    return tuple(evaluaciones)


def guarda_evaluaciones(
    conn: psycopg.Connection, evaluaciones: tuple[Evaluacion, ...], decidido: dt.datetime
) -> dict[str, int]:
    """Persiste el resultado congelado de la fase de lectura."""
    motivos: Counter[str] = Counter()
    for resultado in evaluaciones:
        motivos[resultado.motivo] += 1
        _persiste(conn, resultado, decidido)
    return dict(motivos)
