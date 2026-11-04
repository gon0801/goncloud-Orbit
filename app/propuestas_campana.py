"""Propuestas economicas de campana, sin escrituras a Amazon Ads.

El reporte spCampaigns llega a ads_metric_observation con kind=campaign.
Cada evaluacion usa una sola ventana madura de esa entidad. La tabla propia
guarda episodios y nunca participa en apply_queue.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal

import httpx
import psycopg
from psycopg.types.json import Jsonb

from app import notifica
from app.optimizer import goals, windows
from app.optimizer.bid import PLATAFORMAS_MONEDA

UMBRAL_EXCESO = {"USD": Decimal("80"), "MXN": Decimal("1000")}
TIPO_RIESGO = "exceso_economico"
VERSION_POLITICA = "ads-proteccion-01-c1"
MULTIPLO_TARGET = Decimal("3")
MAX_EDAD_ESTADO = dt.timedelta(hours=48)

# C.5: motivos de cierre humano. El externo describe SOLO el readback
# (snapshot + fecha); jamas autor ni causalidad (DoD fila 103).
MOTIVO_DESCARTE_MANUAL = "descarte_manual"
MOTIVO_PAUSADO_EXTERNO = "estado_pausado_externo"


class PropuestaInexistente(Exception):
    """La propuesta a descartar no existe (endpoint -> 404)."""


class PropuestaYaCerrada(Exception):
    """La propuesta ya salio de open (endpoint -> 409, sin duplicar)."""


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
    # Obs4: el ACoS se congela a 2 decimales (el NUMERIC sin redondear
    # guardaba 134.6145789694176790950984499; la regla no lo usa).
    if dato.revenue > 0:
        acos = (dato.cost * Decimal("100") / dato.revenue).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    else:
        acos = None
    riesgo = dato.cost > MULTIPLO_TARGET * esperado and exceso >= UMBRAL_EXCESO[dato.currency]
    return Evaluacion(dato, riesgo, TIPO_RIESGO if riesgo else "bajo_limite", exceso, acos)


def transicion(ultimo_estado: str | None, *, riesgo: bool, status: str | None, reset: bool) -> str:
    """Un descarte o pausa externa no reabre sin una ventana observada segura.

    B2: una PAUSED con riesgo (sombra) SI deja fila visible
    (paused_observed, no accionable) en vez de nada. Obs5: una campana
    reactivada (ENABLED) con riesgo ABRE episodio nuevo aunque su fila
    previa sea paused_* (la pausa quedo atras; C.5 prohibe reabrir SIN
    episodio nuevo, no con uno). paused_external (cierre C.5 de una open
    cuya campana leyo PAUSED) jamas se toca aqui salvo para abrir el
    episodio siguiente a su reactivacion.
    """
    if status == "PAUSED":
        if ultimo_estado == "open":
            return "cerrar_estado_pausado"
        if ultimo_estado == "paused_observed":
            return "actualizar_pausado"
        if ultimo_estado is None and riesgo:
            return "registrar_pausado"
        return "mantener"
    if not riesgo:
        if ultimo_estado == "open":
            return "cerrar_riesgo"
        if ultimo_estado is not None and not reset:
            return "reiniciar"
        return "mantener"
    if ultimo_estado == "open":
        return "actualizar"
    if ultimo_estado in ("paused_observed", "paused_external"):
        # Candidato a episodio nuevo; _persiste exige evidencia NUEVA
        # (ventana/vintage avanzados) y si no la hay degrada a
        # actualizar_pausado/mantener (obs4r2).
        return "abrir"
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
SELECT id, status, reset_at, window_end, observed_at
  FROM ads_campaign_proposal
 WHERE campaign_id = %s AND risk_type = %s
 ORDER BY id DESC LIMIT 1 FOR UPDATE
"""

_SQL_INSERTAR = """
INSERT INTO ads_campaign_proposal
    (campaign_id, platform, campaign_external_id, profile_id, risk_type, status,
     first_seen_at, last_seen_at, window_start, window_end, observed_at,
     cost, revenue, currency, target_pct, target_source, excess, acos_pct,
     campaign_status, status_synced_at, evidence)
VALUES (%s, %s::platform, %s, %s, %s, 'open', %s, %s, %s, %s, %s,
        %s, %s, %s::currency, %s, %s, %s, %s, %s, %s, %s)
"""

# B2: primera vista de una PAUSED con riesgo (sombra): fila VISIBLE pero no
# accionable (cerrada al nacer; el CHECK exige closed_at fuera de open).
_SQL_INSERTAR_PAUSADO = """
INSERT INTO ads_campaign_proposal
    (campaign_id, platform, campaign_external_id, profile_id, risk_type, status,
     first_seen_at, last_seen_at, closed_at, close_reason, close_evidence,
     window_start, window_end, observed_at,
     cost, revenue, currency, target_pct, target_source, excess, acos_pct,
     campaign_status, status_synced_at, evidence)
VALUES (%s, %s::platform, %s, %s, %s, 'paused_observed', %s, %s, %s, %s, %s,
        %s, %s, %s, %s, %s, %s::currency, %s, %s, %s, %s, %s, %s, %s)
"""

_SQL_ACTUALIZAR_PAUSADO = """
UPDATE ads_campaign_proposal
   SET last_seen_at = %s, window_start = %s, window_end = %s,
       observed_at = %s, cost = %s, revenue = %s, currency = %s::currency,
       target_pct = %s, target_source = %s, excess = %s, acos_pct = %s,
       campaign_status = %s, status_synced_at = %s, evidence = %s
 WHERE id = %s AND status = 'paused_observed'
"""

# Obs5: la fila abierta cuya campana cae a ARCHIVED / goal-off / estado
# viejo refresca su snapshot sin cerrarse (el riesgo no se refuto, el dato
# falto; cerrar mentiria y reabrir duplicaria).
_SQL_ACTUALIZAR_ESTADO = """
UPDATE ads_campaign_proposal
   SET last_seen_at = %s,
       campaign_status = COALESCE(%s, campaign_status),
       status_synced_at = COALESCE(%s, status_synced_at),
       evidence = %s
 WHERE id = %s AND status IN ('open', 'paused_observed')
"""

# B1: propuestas abiertas cuyo aviso Telegram sigue pendiente, en orden de
# apertura (el nombre sale del JOIN: el aviso lo muestra).
_SQL_AVISOS_PENDIENTES = """
SELECT p.id, p.platform, p.campaign_external_id, e.name,
       p.first_seen_at, p.window_start, p.window_end,
       p.cost, p.revenue, p.currency, p.target_pct, p.target_source,
       p.excess, p.acos_pct, p.aviso_intentos
  FROM ads_campaign_proposal p
  JOIN ad_entity e ON e.id = p.campaign_id
 WHERE p.status = 'open' AND p.aviso_estado = 'pending'
 ORDER BY p.id
"""

_SQL_MARCA_AVISO = """
UPDATE ads_campaign_proposal
   SET aviso_estado = %s,
       aviso_intentos = aviso_intentos + 1,
       aviso_enviado_at = CASE WHEN %s = 'sent' THEN now() ELSE aviso_enviado_at END
 WHERE id = %s
"""


_SQL_ACTUALIZAR = """
UPDATE ads_campaign_proposal
   SET last_seen_at = %s, window_start = %s, window_end = %s,
       observed_at = %s, cost = %s, revenue = %s, currency = %s::currency,
       target_pct = %s, target_source = %s, excess = %s, acos_pct = %s,
       campaign_status = %s, status_synced_at = %s, evidence = %s
 WHERE id = %s AND status = 'open'
"""

# C.5: descarte humano atomico (patron veto: el WHERE de estado ES la
# carrera; rowcount 0 = otro movio la fila y se relee para 404/409).
_SQL_DESCARTAR = """
UPDATE ads_campaign_proposal
   SET status = 'dismissed', closed_at = now(), last_seen_at = now(),
       close_reason = %s, close_evidence = %s
 WHERE id = %s AND status = 'open'
RETURNING id, status, closed_at, close_reason
"""

_SQL_ESTADO_PROPUESTA = "SELECT status FROM ads_campaign_proposal WHERE id = %s"

# C.5: profile_id por plataforma para el readback campaignId/profile: el
# ultimo written de ads_report_result (0040). Sin fuente -> NULL (regla 3:
# el profile_id no se inventa; comentario de 0042).
_SQL_PROFILE_POR_PLATAFORMA = """
SELECT profile_id
  FROM ads_report_result
 WHERE platform = %s::platform AND report_name = 'campanas' AND status = 'written'
   AND profile_id IS NOT NULL
 ORDER BY observed_at DESC LIMIT 1
"""


def _evidencia(evaluacion: Evaluacion, *, riesgo_sombra: bool | None = None) -> dict:
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
        "profile_id": None,
        "riesgo_ignorando_estado": riesgo_sombra,
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


def _sombra(evaluacion: Evaluacion, decidido: dt.datetime) -> Evaluacion:
    """Re-evaluacion de una campana PAUSED ignorando el gate de estado:
    decide si merece fila visible (paused_observed) y aporta exceso/acos
    (la evaluacion real trae None y excess es NOT NULL). La evaluacion
    real (campana_no_enabled, sin riesgo) sigue mandando para todo lo
    demas: jamas se abre propuesta accionable sobre una PAUSED (DoD C.4)."""
    return evalua(replace(evaluacion.dato, status="ENABLED"), decidido=decidido)


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


def _resuelve_profile_id(conn: psycopg.Connection, platform: str) -> int | None:
    """Profile de Amazon Ads para el readback campaignId/profile (C.5).

    Fuente UNICA: ultimo written de ads_report_result (0040) de la
    plataforma. Sin fuente -> None (regla 3); la fase de decision no hace
    HTTP y el profile_id no se inventa."""
    fila = conn.execute(_SQL_PROFILE_POR_PLATAFORMA, (platform,)).fetchone()
    return fila[0] if fila is not None else None


def descartar_propuesta(conn: psycopg.Connection, propuesta_id: int, actor: str) -> dict:
    """Cierra open->dismissed una propuesta (C.5, descarte humano).

    Solo DB: cero HTTP a Amazon (con test que bloquea httpx). Atomico
    (patron veto): inexistente -> PropuestaInexistente; ya cerrada ->
    PropuestaYaCerrada (repetir no duplica ni reabre). Hace commit
    explicito (la conexion del endpoint no es autocommit)."""
    if not actor or not actor.strip():
        raise ValueError("actor vacio: el descarte exige quien lo hace")
    fila = conn.execute(
        _SQL_DESCARTAR,
        (MOTIVO_DESCARTE_MANUAL, Jsonb({"actor": actor.strip()}), propuesta_id),
    ).fetchone()
    if fila is None:
        estado = conn.execute(_SQL_ESTADO_PROPUESTA, (propuesta_id,)).fetchone()
        if estado is None:
            raise PropuestaInexistente(f"propuesta {propuesta_id} no existe")
        raise PropuestaYaCerrada(f"propuesta {propuesta_id} ya cerrada ({estado[0]})")
    conn.commit()
    return {
        "id": fila[0],
        "status": fila[1],
        "closed_at": fila[2].isoformat(),
        "close_reason": fila[3],
    }


def _persiste(conn: psycopg.Connection, evaluacion: Evaluacion, decidido: dt.datetime) -> None:
    dato = evaluacion.dato
    sombra: bool | None = None
    exceso = evaluacion.exceso
    acos = evaluacion.acos_pct
    if dato.status == "PAUSED" and evaluacion.motivo == "campana_no_enabled":
        # B2: la PAUSED se mide en sombra para decidir visibilidad (la
        # evaluacion real sigue sin riesgo: DoD C.4 intacto).
        sombra_eval = _sombra(evaluacion, decidido)
        sombra = sombra_eval.riesgo
        riesgo = sombra
        exceso = sombra_eval.exceso
        acos = sombra_eval.acos_pct
    elif evaluacion.motivo in (TIPO_RIESGO, "bajo_limite"):
        riesgo = evaluacion.riesgo
    else:
        # Obs5: motivo de estado/dato con fila abierta -> refresca el
        # snapshot sin cerrar; sin fila abierta no hay nada que mostrar.
        previo = conn.execute(_SQL_ULTIMO, (dato.campaign_id, TIPO_RIESGO)).fetchone()
        if previo is not None and previo[1] == "open":
            conn.execute(
                _SQL_ACTUALIZAR_ESTADO,
                (
                    decidido,
                    dato.status,
                    dato.status_synced_at,
                    Jsonb(_evidencia(evaluacion)),
                    previo[0],
                ),
            )
        return
    previo = conn.execute(_SQL_ULTIMO, (dato.campaign_id, TIPO_RIESGO)).fetchone()
    if previo is None:
        prev_id, prev_estado, reset, prev_fin, prev_visto = (None,) * 5
    else:
        prev_id, prev_estado, reset, prev_fin, prev_visto = previo
    accion = transicion(prev_estado, riesgo=riesgo, status=dato.status, reset=reset is not None)
    # Obs4r2: reactivada SIN evidencia nueva (misma ventana madura y mismo
    # vintage que la pausa) = MISMO episodio: no reabre ni avisa de nuevo
    # (C.5: "no reabre hasta un nuevo episodio"). Solo refresca el snapshot
    # pausado (propio; el externo de C.5 ni se toca). Con ventana/vintage
    # avanzados SI abre (gasto nuevo, episodio nuevo).
    if (
        accion == "abrir"
        and prev_estado in ("paused_observed", "paused_external")
        and (dato.window_end, dato.observed_at) == (prev_fin, prev_visto)
    ):
        accion = "actualizar_pausado" if prev_estado == "paused_observed" else "mantener"
    if accion == "abrir":
        conn.execute(
            _SQL_INSERTAR,
            (
                dato.campaign_id,
                dato.platform,
                dato.external_id,
                _resuelve_profile_id(conn, dato.platform),
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
    elif accion == "registrar_pausado":
        evidencia = _evidencia(evaluacion, riesgo_sombra=sombra)
        conn.execute(
            _SQL_INSERTAR_PAUSADO,
            (
                dato.campaign_id,
                dato.platform,
                dato.external_id,
                _resuelve_profile_id(conn, dato.platform),
                TIPO_RIESGO,
                decidido,
                decidido,
                decidido,
                "estado_pausado_observado",
                Jsonb(evidencia),
                dato.window_start,
                dato.window_end,
                dato.observed_at,
                dato.cost,
                dato.revenue,
                dato.currency,
                dato.target_pct,
                dato.target_source,
                exceso,
                acos,
                dato.status,
                dato.status_synced_at,
                Jsonb(evidencia),
            ),
        )
    elif accion == "actualizar_pausado":
        if exceso is None:
            # La sombra perdio el dato (ventana/ausente): solo refresca la
            # evidencia, sin pisar numeros sanos con NULL (excess NOT NULL).
            conn.execute(
                _SQL_ACTUALIZAR_ESTADO,
                (
                    decidido,
                    dato.status,
                    dato.status_synced_at,
                    Jsonb(_evidencia(evaluacion, riesgo_sombra=sombra)),
                    prev_id,
                ),
            )
            return
        conn.execute(
            _SQL_ACTUALIZAR_PAUSADO,
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
                exceso,
                acos,
                dato.status,
                dato.status_synced_at,
                Jsonb(_evidencia(evaluacion, riesgo_sombra=sombra)),
                prev_id,
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
        # C.5: la open cuya campana lee PAUSED se cierra como pausa EXTERNA
        # (paused_external): snapshot + fecha, sin inferir autor ni
        # causalidad (pudo ser David sobre esta propuesta u otro cambio; la
        # evidencia no trae actor). La PAUSED vista sin open previa sigue
        # siendo paused_observed informativa (B2, registrar_pausado).
        if accion == "cerrar_estado_pausado":
            status = "paused_external"
            motivo_cierre = "estado_pausado_externo"
        else:
            status = "resolved"
            motivo_cierre = evaluacion.motivo
        if accion == "cerrar_estado_pausado":
            # C.5: el cierre por readback refresca el snapshot de estado de
            # la fila (la pantalla muestra la PAUSED leida, no la ENABLED de
            # apertura) y anida la evidencia bajo "cierre" con el perfil del
            # readback; sin autor ni causalidad inferidos.
            evidencia_cierre = _evidencia(evaluacion)
            evidencia_cierre["profile_id"] = _resuelve_profile_id(conn, dato.platform)
            evidencia_cierre = {"cierre": evidencia_cierre}
        else:
            evidencia_cierre = _evidencia(evaluacion)
        conn.execute(
            """UPDATE ads_campaign_proposal
                  SET status = %s, closed_at = %s, last_seen_at = %s,
                      close_reason = %s, close_evidence = %s, reset_at = %s,
                      campaign_status = %s, status_synced_at = %s
                WHERE id = %s AND status = 'open'""",
            (
                status,
                decidido,
                decidido,
                motivo_cierre,
                Jsonb(evidencia_cierre),
                decidido if accion == "cerrar_riesgo" else None,
                dato.status,
                dato.status_synced_at,
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
        """SELECT scope, ad_entity_id, platform, target_acos_pct, bid_floor,
                  bid_ceiling, bid_currency, harvest_campaign_id,
                  harvest_ad_group_id, harvest_default_bid, enabled, mode
             FROM ads_optimizer_goal
            WHERE platform = %s::platform OR ad_entity_id = ANY(%s::bigint[])""",
        (platform, ids),
    ).fetchall()

    def _goal(fila) -> goals.Goal:
        return goals.Goal(
            scope=fila[0],
            ad_entity_id=fila[1],
            platform=fila[2],
            target_acos_pct=fila[3],
            bid_floor=fila[4],
            bid_ceiling=fila[5],
            bid_currency=fila[6],
            harvest_campaign_id=fila[7],
            harvest_ad_group_id=fila[8],
            harvest_default_bid=fila[9],
            enabled=fila[10],
            mode=fila[11],
        )

    por_campana = {f[1]: _goal(f) for f in goals_rows if f[0] == "campaign"}
    goal_plataforma = next((_goal(f) for f in goals_rows if f[0] == "platform"), None)
    evaluaciones: list[Evaluacion] = []
    for camp_id, external_id, nombre, status, synced_at, cache_target in filas:
        campana = por_campana.get(camp_id)
        elegido = campana if campana is not None else goal_plataforma
        enabled = elegido is not None and elegido.enabled and elegido.mode != "off"
        try:
            # Obs3: la variante con procedencia (UNICA fuente del orden de
            # peldanos, regla 2): valor y peldano salen de UNA llamada, no
            # de dos calculos que podrian discrepar (M6/M7).
            target, source = goals.cascada_target_acos_con_procedencia(
                campana, goal_plataforma, settings, cache_target, platform, target_margen
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


def envia_avisos_pendientes(
    conn: psycopg.Connection, *, transport: httpx.BaseTransport | None = None
) -> dict[str, list]:
    """B1: aviso Telegram de cada propuesta open con aviso pendiente.

    Contrato pending/sent: se marca sent SOLO tras HTTP 2xx; un fallo deja
    pending (+1 intento) y el ciclo siguiente reintenta SIN duplicar (las
    sent jamas se re-envian). Un commit por aviso: un corte a media lista
    no pierde los ya marcados ni re-envia los ya enviados (la ventana
    HTTP-ok/commit-muerto puede duplicar UN aviso: residuo declarado, sin
    llave de idempotencia del lado de Telegram no hay exactly-once).
    Solo filas open: la PAUSED visible (paused_observed) no es accionable
    y no avisa (DoD C.4). Devuelve {"enviados": [...], "fallos": [...]}.

    El SELECT va en su PROPIA transaccion (BN1r2: la conexion de prod no
    es autocommit — un SELECT suelto abriria la TX implicita y tanto los
    `with` de marcado como el sello posterior serian savepoints jamas
    commiteados: avisos duplicados, notes perdidas y degraded que llega
    como done). Al salir, la conexion queda IDLE.
    """
    with conn.transaction():
        pendientes = conn.execute(_SQL_AVISOS_PENDIENTES).fetchall()
    enviados: list[int] = []
    fallos: list[int] = []
    for fila in pendientes:
        (
            prop_id,
            platform,
            external_id,
            nombre,
            first_seen_at,
            window_start,
            window_end,
            cost,
            revenue,
            currency,
            target_pct,
            target_source,
            excess,
            acos_pct,
            _intentos,
        ) = fila
        aviso = notifica.PropuestaCampanaNueva(
            proposal_id=prop_id,
            platform=platform,
            campaign_external_id=external_id,
            nombre=nombre,
            first_seen_at=first_seen_at,
            window_start=window_start,
            window_end=window_end,
            cost=cost,
            revenue=revenue,
            currency=currency,
            target_pct=target_pct,
            target_source=target_source,
            excess=excess,
            acos_pct=acos_pct,
        )
        ok = notifica.notifica_propuesta_campana(aviso, transport=transport)
        estado = "sent" if ok else "pending"
        with conn.transaction():
            conn.execute(_SQL_MARCA_AVISO, (estado, estado, prop_id))
        (enviados if ok else fallos).append(prop_id)
    return {"enviados": enviados, "fallos": fallos}
