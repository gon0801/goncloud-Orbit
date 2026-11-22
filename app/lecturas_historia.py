"""Lectura de historia de bids, manos externas, fines y presupuesto de campana.

Extraido de `app/lecturas_caso.py` (Ola D1) manteniendo el invariante SOLO SELECT
y sin dependencias de `app.apply` ni `app.ads`.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from app.lecturas_efecto import Conexion, _fuente
from app.optimizer import temporada
from app.optimizer.caso import (
    CambioBid,
    CambioFechado,
    HistoriaBid,
    ManoExterna,
    Plataforma,
    PresupuestoCampana,
)

# TEMPORADA 01 B: con temporada encendida la ventana crece a D-365 (o al inicio
# del episodio abierto mas viejo: un episodio abierto no caduca) y el SELECT
# trae decision_id, motivo_temporada y episodio_id de la vista de 0093.
_SQL_CAMBIOS = """
SELECT c.hoja_id, c.confirmado_el, c.bid_antes, c.bid_despues, c.origen,
       c.decision_id, c.motivo_temporada, c.episodio_id, d.new_value,
       d.inputs->>'motivo' AS motivo
  FROM v_cambio_bid c
  JOIN ad_entity k ON k.id = c.hoja_id
  LEFT JOIN decision d ON d.id = c.decision_id
 WHERE k.platform = %s::platform
   AND c.confirmado_el >= %s{tope}
 ORDER BY c.hoja_id, c.confirmado_el, c.decision_id
"""

_SQL_MANOS_EXTERNAS = """
SELECT x.ad_entity_id, x.detectada_el, x.bid_visto, 'exclusion'::text
  FROM temporada_exclusion x
  JOIN temporada_episodio e ON e.id = x.episodio_id
 WHERE e.platform = %s::platform AND x.bid_visto IS NOT NULL AND x.detectada_el >= %s
 UNION ALL
SELECT r.ad_entity_id, r.detectada_en, NULL::numeric, 'reactivacion_manual'::text
  FROM reactivacion_manual r
  JOIN ad_entity k ON k.id = r.ad_entity_id
 WHERE k.platform = %s::platform AND r.detectada_en >= %s
"""

_SQL_FINES = """
SELECT f.ad_entity_id, f.episodio_id
  FROM temporada_fin_reduccion f
  JOIN temporada_episodio e ON e.id = f.episodio_id
 WHERE e.platform = %s::platform
"""

_SQL_EPISODIO_ABIERTO_VIEJO = """
SELECT min(abierto_el) FROM temporada_episodio WHERE platform = %s::platform AND cerrado_el IS NULL
"""

# TEMPORADA 01 C: presupuesto vigente y gasto diario de campana (la MISMA
# fuente que pantalla_dinero y los avisos de Telegram: v_campana_config_vigente
# y v_metric_latest con kind='campaign'; Amazon reporta el costo de campana).
# Con `visto_el` el gasto sale de metrics_as_of y el presupuesto de la ultima
# observacion CONOCIDA entonces: una correccion append-only posterior o un
# cambio de presupuesto no altera el rejuego (revision del dueno, PR #701).
_PRESUPUESTO_VIGENTE = (
    "(SELECT v.presupuesto_diario FROM v_campana_config_vigente v WHERE v.ad_entity_id = c.id)"
)
_PRESUPUESTO_AS_OF = (
    "(SELECT o.presupuesto_diario FROM ads_campana_config_observation o"
    " WHERE o.ad_entity_id = c.id AND o.observed_at <= %s"
    " ORDER BY o.observed_at DESC LIMIT 1)"
)
_SQL_PRESUPUESTO_CAMPANA = """
SELECT c.id, {presupuesto}
  FROM ad_entity c
 WHERE c.platform = %s AND c.kind = 'campaign'
"""

_SQL_GASTO_CAMPANA = """
SELECT v.ad_entity_id, v.metric_date, v.cost
  FROM {fuente}
  JOIN ad_entity c ON c.id = v.ad_entity_id
 WHERE c.platform = %s AND c.kind = 'campaign' AND v.metric_date >= %s AND v.metric_date <= %s
"""


def _cambios_por_hoja(filas: list) -> dict[int, tuple[CambioBid, ...]]:
    """Trayectorias ascendentes desde `v_cambio_bid` (el SQL ya ordena). El
    `bid_antes` de un regreso del dueno es el `bid_despues` del cambio
    anterior de esa hoja; sin cambio anterior en la ventana sale del
    `new_value` de la decision revertida (B3: exacto con racha de un
    corte; con racha larga sobreestima el piso, lado fail-closed). Solo
    se omite si tampoco hay decision revertida con `new_value`."""
    por_hoja: dict[int, list[CambioBid]] = {}
    for (
        hoja_id,
        confirmado_el,
        bid_antes,
        bid_despues,
        origen,
        _did,
        _mt,
        _ep,
        revierte_new,
        _motivo,
    ) in filas:
        previos = por_hoja.setdefault(hoja_id, [])
        if bid_antes is None:
            anterior = next((p for p in reversed(previos) if p.origen != "ajuste_de_campana"), None)
            if anterior is not None:
                # ajuste_de_campana trae el bid actual, no un historico.
                bid_antes = anterior.bid_despues
            elif revierte_new is not None:
                bid_antes = revierte_new
            else:
                continue
        previos.append(
            CambioBid(
                fecha=confirmado_el.astimezone(dt.UTC).date(),
                bid_antes=bid_antes,
                bid_despues=bid_despues,
                origen=origen,
            )
        )
    return {hoja_id: tuple(cambios) for hoja_id, cambios in por_hoja.items()}


def _historia_por_hoja(
    filas: list,
    manos: dict[int, list[ManoExterna]],
    fines: dict[int, set[int]],
    desde: dt.date,
) -> dict[int, HistoriaBid]:
    """HistoriaBid congelada por hoja (TEMPORADA 01 B): cambios con instante,
    decision_id, motivo_temporada y episodio_id; manos externas fechadas
    (exclusiones con bid_visto) y episodios con fin guardado. La trayectoria
    de 90 dias se deriva aparte, sin tocar este congelado."""
    por_hoja: dict[int, list[CambioFechado]] = {}
    for (
        hoja_id,
        confirmado_el,
        bid_antes,
        bid_despues,
        origen,
        did,
        motivo_t,
        episodio_id,
        _rev,
        motivo,
    ) in filas:
        por_hoja.setdefault(hoja_id, []).append(
            CambioFechado(
                confirmado_el=confirmado_el.astimezone(dt.UTC),
                decision_id=did,
                bid_antes=bid_antes,
                bid_despues=bid_despues,
                origen=origen,
                motivo_temporada=motivo_t,
                episodio_id=episodio_id,
                motivo=motivo,
            )
        )
    hojas = set(por_hoja) | set(manos) | set(fines)
    return {
        hoja: HistoriaBid(
            desde=desde,
            cambios=tuple(por_hoja.get(hoja, ())),
            fines=frozenset(fines.get(hoja, ())),
            manos_externas=tuple(manos.get(hoja, ())),
        )
        for hoja in hojas
    }


def _historia_temporada(
    conn: Conexion,
    plataforma: Plataforma,
    d: dt.date,
    desde: dt.date,
    visto_el: dt.datetime | None,
    activa: bool,
    memoria_dias: int = 365,
) -> tuple[
    dt.date,
    dict[int, list[ManoExterna]],
    dict[int, set[int]],
    dict[int, tuple[CambioBid, ...]],
    dict[int, HistoriaBid] | None,
]:
    """Los cambios de bid de la plataforma, la ventana extendida de la historia
    (D-memoria_dias o el inicio del episodio abierto mas viejo: un episodio
    abierto no caduca), las manos externas fechadas, los episodios con fin
    guardado y la trayectoria de 90 dias derivada como hoy (TEMPORADA 01 B)."""
    desde_historia = desde
    if activa:
        desde_historia = d - dt.timedelta(days=memoria_dias)
        viejo = conn.execute(_SQL_EPISODIO_ABIERTO_VIEJO, (plataforma,)).fetchone()[0]
        if viejo is not None:
            desde_historia = min(desde_historia, viejo.astimezone(dt.UTC).date())
    tope = "" if visto_el is None else " AND c.confirmado_el <= %s"
    p_cambios: tuple = (
        plataforma,
        dt.datetime(desde_historia.year, desde_historia.month, desde_historia.day, tzinfo=dt.UTC),
    )
    if visto_el is not None:
        p_cambios = (*p_cambios, visto_el)
    filas_cambios = conn.execute(_SQL_CAMBIOS.format(tope=tope), p_cambios).fetchall()
    if not activa:
        return desde_historia, {}, {}, _cambios_por_hoja(filas_cambios), None
    desde_90 = dt.datetime(desde.year, desde.month, desde.day, tzinfo=dt.UTC)
    filas_90 = [f for f in filas_cambios if f[1] >= desde_90]
    desde_h = dt.datetime(
        desde_historia.year, desde_historia.month, desde_historia.day, tzinfo=dt.UTC
    )
    manos: dict[int, list[ManoExterna]] = {}
    for hoja, instante, bid_visto, origen in conn.execute(
        _SQL_MANOS_EXTERNAS, (plataforma, desde_h, plataforma, desde_h)
    ).fetchall():
        manos.setdefault(hoja, []).append(
            ManoExterna(instante=instante.astimezone(dt.UTC), bid_visto=bid_visto, origen=origen)
        )
    fines: dict[int, set[int]] = {}
    for hoja, episodio_id in conn.execute(_SQL_FINES, (plataforma,)).fetchall():
        fines.setdefault(hoja, set()).add(episodio_id)
    limite = desde
    cambios_90 = {
        hoja: tuple(c for c in cs if c.fecha >= limite)
        for hoja, cs in _cambios_por_hoja(filas_90).items()
    }
    historia = _historia_por_hoja(filas_cambios, manos, fines, desde_historia)
    return desde_historia, manos, fines, {h: cs for h, cs in cambios_90.items() if cs}, historia


_SQL_DIAS_INGESTA = """
SELECT DISTINCT v.metric_date
  FROM v_metric_latest v
  JOIN ad_entity e ON e.id = v.ad_entity_id
 WHERE e.platform = %s::platform AND v.metric_date >= %s AND v.metric_date <= %s
"""


def presupuesto_campana_hoy(
    conn: Conexion,
    plataforma: Plataforma,
    campana_id: int,
    hoy: dt.date,
    topada: temporada.CampanaTopada,
) -> PresupuestoCampana | None:
    """PresupuestoCampana de UNA campana con los ultimos 7 dias con ingesta
    (el mismo criterio que `_presupuesto_campanas` usa en el ciclo). None si
    la campana no tiene presupuesto conocido. Solo lectura."""
    desde = hoy - dt.timedelta(days=13)
    dias = frozenset(
        f[0] for f in conn.execute(_SQL_DIAS_INGESTA, (plataforma, desde, hoy)).fetchall()
    )
    return _presupuesto_campanas(conn, plataforma, dias, topada).get(campana_id)


def _presupuesto_campanas(
    conn: Conexion,
    plataforma: Plataforma,
    dias: frozenset[dt.date],
    topada: temporada.CampanaTopada,
    visto_el: dt.datetime | None = None,
) -> dict[int, PresupuestoCampana]:
    """PresupuestoCampana por campana (TEMPORADA 01 C): el presupuesto vigente
    (o el conocido a `visto_el`, para el rejuego bitemporal) y cuantos de los
    ULTIMOS 7 dias con ingesta de la plataforma el gasto de la campana alcanzo
    `topada.pct` de el (del mismo corte temporal: metrics_as_of)."""
    fuente, pv = _fuente(visto_el)
    if visto_el is None:
        sql = _SQL_PRESUPUESTO_CAMPANA.format(presupuesto=_PRESUPUESTO_VIGENTE)
        params: tuple = (plataforma,)
    else:
        sql = _SQL_PRESUPUESTO_CAMPANA.format(presupuesto=_PRESUPUESTO_AS_OF)
        params = (visto_el, plataforma)
    presupuestos = {campana: monto for campana, monto in conn.execute(sql, params).fetchall()}
    ultimos_7 = sorted(dias)[-7:]
    gasto: dict[int, dict[dt.date, Decimal]] = {}
    if ultimos_7:
        for campana, metric_date, cost in conn.execute(
            _SQL_GASTO_CAMPANA.format(fuente=fuente),
            (*pv, plataforma, ultimos_7[0], ultimos_7[-1]),
        ).fetchall():
            gasto.setdefault(campana, {})[metric_date] = cost
    resultado: dict[int, PresupuestoCampana] = {}
    for campana, monto in presupuestos.items():
        dias_topados = None
        if monto is not None:
            filas_gasto = gasto.get(campana, {})
            if any(dia in filas_gasto and filas_gasto[dia] is None for dia in ultimos_7):
                dias_topados = None
            else:
                dias_topados = sum(
                    1 for dia in ultimos_7 if filas_gasto.get(dia, Decimal(0)) >= topada.pct * monto
                )
        resultado[campana] = PresupuestoCampana(
            presupuesto_diario=monto,
            dias_medidos=len(ultimos_7),
            dias_topados=dias_topados,
            topada=topada,
        )
    return resultado
