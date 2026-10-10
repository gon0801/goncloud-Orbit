"""Lectura por plataforma para armar CasoHoja (BIDS 02 M.2). SOLO SELECT.

`lee_plataforma` lee UNA vez por plataforma y ciclo: tramos por hoja, hojas
activas, cambios de bid de 90 dias, y precio mas pedidos inmaduros (cuatro
consultas). `LecturasPlataforma.caso` arma el `CasoHoja` de una hoja con
cero consultas, o una (`_SQL_EFECTO`) si la hoja cambio de bid en 90 dias.

Frontera de lectura (lo decide este modulo, no la politica): dia sin fila
de la hoja pero con ingesta de la plataforma = cero medido; dia sin
ingesta = tramo None; metrica con alguna observacion NULL o negativa =
None solo para esa metrica. El roll-up a grupo y cuenta suma solo hojas
de `v_hoja_activa`, con veneno por metrica identico al `bool_and`.

Calendario de ingesta (guia M.2 cambio 3): dia con ingesta = fecha con al
menos una fila de alguna hoja de la plataforma en la misma lectura.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from app.optimizer.caso import (
    DIAS_LOOKBACK,
    DIAS_MADUREZ,
    DIAS_TRAMO_RECIENTE,
    BidVigente,
    CambioBid,
    CasoHoja,
    Economia,
    EconomiaPlataforma,
    EfectoCambio,
    EvidenciaNivel,
    InsumosPausa,
    Plataforma,
    PrecioVentana,
    Tramo,
    Trayectoria,
)
from app.optimizer.goals import COOLDOWN
from app.optimizer.windows import DIAS_FRESCURA_BIDS

DIAS_EFECTO = COOLDOWN.days  # pre7/post7 de EfectoCambio
DIAS_PRECIO_DESDE = 33  # PrecioVentana cubre D-33..D-3 (docstring de caso, exporta M.1)
DIAS_INMADUROS_DESDE = 9  # pedidos inmaduros cubren D-9..D-1 (docstring, exporta M.1)
DIAS_INMADUROS_HASTA = 1
DIAS_PRECIO_PREVIO = 30  # tramo de precio anterior al cambio


class Conexion(Protocol):
    def execute(self, sql: str, params: tuple = ()) -> Any: ...


# Tramos por hoja en UNA consulta con FILTER por tramo (sustituye a
# _SQL_CONVERSION_GRANO): sumas con veneno por metrica (NULL o negativo
# anula solo ESA metrica), conteo de filas por tramo, sello maximo y las
# fechas con fila (de ahi sale el calendario de ingesta). La ventana de
# fechas leida (D-180..D-1) cubre tramos, precio, inmaduros y todos los
# rangos del efecto (el mas hondo es vendia: x-90 con x >= D-90).
_SQL_TRAMOS = """
SELECT v.ad_entity_id,
       count(*) FILTER (WHERE v.metric_date BETWEEN %s AND %s),
       CASE WHEN bool_and(v.clicks IS NOT NULL AND v.clicks >= 0)
                 FILTER (WHERE v.metric_date BETWEEN %s AND %s)
            THEN sum(v.clicks) FILTER (WHERE v.metric_date BETWEEN %s AND %s)::bigint END,
       CASE WHEN bool_and(v.orders IS NOT NULL AND v.orders >= 0)
                 FILTER (WHERE v.metric_date BETWEEN %s AND %s)
            THEN sum(v.orders) FILTER (WHERE v.metric_date BETWEEN %s AND %s)::bigint END,
       CASE WHEN bool_and(v.ad_revenue IS NOT NULL AND v.ad_revenue >= 0)
                 FILTER (WHERE v.metric_date BETWEEN %s AND %s)
            THEN sum(v.ad_revenue) FILTER (WHERE v.metric_date BETWEEN %s AND %s) END,
       CASE WHEN bool_and(v.cost IS NOT NULL AND v.cost >= 0)
                 FILTER (WHERE v.metric_date BETWEEN %s AND %s)
            THEN sum(v.cost) FILTER (WHERE v.metric_date BETWEEN %s AND %s) END,
       CASE WHEN bool_and(v.impressions IS NOT NULL AND v.impressions >= 0)
                 FILTER (WHERE v.metric_date BETWEEN %s AND %s)
            THEN sum(v.impressions) FILTER (WHERE v.metric_date BETWEEN %s AND %s)::bigint END,
       count(*) FILTER (WHERE v.metric_date BETWEEN %s AND %s),
       CASE WHEN bool_and(v.clicks IS NOT NULL AND v.clicks >= 0)
                 FILTER (WHERE v.metric_date BETWEEN %s AND %s)
            THEN sum(v.clicks) FILTER (WHERE v.metric_date BETWEEN %s AND %s)::bigint END,
       CASE WHEN bool_and(v.orders IS NOT NULL AND v.orders >= 0)
                 FILTER (WHERE v.metric_date BETWEEN %s AND %s)
            THEN sum(v.orders) FILTER (WHERE v.metric_date BETWEEN %s AND %s)::bigint END,
       CASE WHEN bool_and(v.ad_revenue IS NOT NULL AND v.ad_revenue >= 0)
                 FILTER (WHERE v.metric_date BETWEEN %s AND %s)
            THEN sum(v.ad_revenue) FILTER (WHERE v.metric_date BETWEEN %s AND %s) END,
       CASE WHEN bool_and(v.cost IS NOT NULL AND v.cost >= 0)
                 FILTER (WHERE v.metric_date BETWEEN %s AND %s)
            THEN sum(v.cost) FILTER (WHERE v.metric_date BETWEEN %s AND %s) END,
       CASE WHEN bool_and(v.impressions IS NOT NULL AND v.impressions >= 0)
                 FILTER (WHERE v.metric_date BETWEEN %s AND %s)
            THEN sum(v.impressions) FILTER (WHERE v.metric_date BETWEEN %s AND %s)::bigint END,
       max(v.observed_at),
       array_agg(DISTINCT v.metric_date)
  FROM {fuente}
  JOIN ad_entity k ON k.id = v.ad_entity_id
 WHERE k.platform = %s::platform
   AND k.kind IN ('keyword', 'product_target')
   AND v.metric_currency = %s
   AND v.metric_date BETWEEN %s AND %s
 GROUP BY v.ad_entity_id
"""

# Precio (D-33..D-3) y pedidos inmaduros (D-9..D-1) por hoja, misma forma.
_SQL_PRECIO = """
SELECT v.ad_entity_id,
       count(*) FILTER (WHERE v.metric_date BETWEEN %s AND %s),
       CASE WHEN bool_and(v.cost IS NOT NULL AND v.cost >= 0)
                 FILTER (WHERE v.metric_date BETWEEN %s AND %s)
            THEN sum(v.cost) FILTER (WHERE v.metric_date BETWEEN %s AND %s) END,
       CASE WHEN bool_and(v.clicks IS NOT NULL AND v.clicks >= 0)
                 FILTER (WHERE v.metric_date BETWEEN %s AND %s)
            THEN sum(v.clicks) FILTER (WHERE v.metric_date BETWEEN %s AND %s)::bigint END,
       count(*) FILTER (WHERE v.metric_date BETWEEN %s AND %s),
       CASE WHEN bool_and(v.orders IS NOT NULL AND v.orders >= 0)
                 FILTER (WHERE v.metric_date BETWEEN %s AND %s)
            THEN sum(v.orders) FILTER (WHERE v.metric_date BETWEEN %s AND %s)::bigint END,
       max(v.observed_at)
  FROM {fuente}
  JOIN ad_entity k ON k.id = v.ad_entity_id
 WHERE k.platform = %s::platform
   AND k.kind IN ('keyword', 'product_target')
   AND v.metric_currency = %s
   AND v.metric_date BETWEEN %s AND %s
 GROUP BY v.ad_entity_id
"""

# Hojas activas de la plataforma (el roll-up suma solo estas).
_SQL_HOJAS = """
SELECT hoja_id, ad_group_id FROM v_hoja_activa WHERE platform = %s
"""

# Cambios de bid de 90 dias de toda la plataforma, ascendentes por hoja
# (el orden lo fija el SQL: la derivacion del regreso lo exige).
_SQL_CAMBIOS = """
SELECT c.hoja_id, c.confirmado_el, c.bid_antes, c.bid_despues, c.origen
  FROM v_cambio_bid c
  JOIN ad_entity k ON k.id = c.hoja_id
 WHERE k.platform = %s::platform
   AND c.confirmado_el >= %s{tope}
 ORDER BY c.hoja_id, c.confirmado_el, c.decision_id
"""

# Efecto del ULTIMO cambio de UNA hoja: una fila con conteo y sumas por
# lado del cambio (pre7, trafico post, precio post, precio previo, vendia).
_SQL_EFECTO = """
SELECT count(*) FILTER (WHERE v.metric_date BETWEEN %s AND %s),
       CASE WHEN bool_and(v.impressions IS NOT NULL AND v.impressions >= 0)
                 FILTER (WHERE v.metric_date BETWEEN %s AND %s)
            THEN sum(v.impressions) FILTER (WHERE v.metric_date BETWEEN %s AND %s)::bigint END,
       CASE WHEN bool_and(v.clicks IS NOT NULL AND v.clicks >= 0)
                 FILTER (WHERE v.metric_date BETWEEN %s AND %s)
            THEN sum(v.clicks) FILTER (WHERE v.metric_date BETWEEN %s AND %s)::bigint END,
       count(*) FILTER (WHERE v.metric_date BETWEEN %s AND %s),
       CASE WHEN bool_and(v.impressions IS NOT NULL AND v.impressions >= 0)
                 FILTER (WHERE v.metric_date BETWEEN %s AND %s)
            THEN sum(v.impressions) FILTER (WHERE v.metric_date BETWEEN %s AND %s)::bigint END,
       count(*) FILTER (WHERE v.metric_date BETWEEN %s AND %s),
       CASE WHEN bool_and(v.clicks IS NOT NULL AND v.clicks >= 0)
                 FILTER (WHERE v.metric_date BETWEEN %s AND %s)
            THEN sum(v.clicks) FILTER (WHERE v.metric_date BETWEEN %s AND %s)::bigint END,
       CASE WHEN bool_and(v.cost IS NOT NULL AND v.cost >= 0)
                 FILTER (WHERE v.metric_date BETWEEN %s AND %s)
            THEN sum(v.cost) FILTER (WHERE v.metric_date BETWEEN %s AND %s) END,
       count(*) FILTER (WHERE v.metric_date BETWEEN %s AND %s),
       CASE WHEN bool_and(v.clicks IS NOT NULL AND v.clicks >= 0)
                 FILTER (WHERE v.metric_date BETWEEN %s AND %s)
            THEN sum(v.clicks) FILTER (WHERE v.metric_date BETWEEN %s AND %s)::bigint END,
       CASE WHEN bool_and(v.cost IS NOT NULL AND v.cost >= 0)
                 FILTER (WHERE v.metric_date BETWEEN %s AND %s)
            THEN sum(v.cost) FILTER (WHERE v.metric_date BETWEEN %s AND %s) END,
       count(*) FILTER (WHERE v.metric_date BETWEEN %s AND %s),
       CASE WHEN bool_and(v.orders IS NOT NULL AND v.orders >= 0)
                 FILTER (WHERE v.metric_date BETWEEN %s AND %s)
            THEN sum(v.orders) FILTER (WHERE v.metric_date BETWEEN %s AND %s)::bigint END
  FROM {fuente}
 WHERE v.ad_entity_id = %s
   AND v.metric_currency = %s
   AND v.metric_date BETWEEN %s AND %s
"""


def _exige_tz(momento: dt.datetime, nombre: str) -> None:
    if momento.tzinfo is None:
        raise ValueError(f"{nombre} debe ser tz-aware: un naive evaluaria segun la TZ local")


def _fuente(visto_el: dt.datetime | None) -> tuple[str, tuple]:
    """Fragmento FROM y sus parametros: lo ultimo, o lo visible entonces."""
    if visto_el is None:
        return "v_metric_latest v", ()
    return "metrics_as_of(%s) v", (visto_el,)


def _completo(desde: dt.date, hasta: dt.date, dias: frozenset[dt.date]) -> bool:
    """Todo dia del rango tuvo ingesta (un rango vacio vale completo)."""
    dia = desde
    while dia <= hasta:
        if dia not in dias:
            return False
        dia += dt.timedelta(days=1)
    return True


def _tramo(
    filas: int,
    valores: tuple,
    desde: dt.date,
    hasta: dt.date,
    dias: frozenset[dt.date],
) -> Tramo:
    """Tramo desde una fila cruda: hueco de ingesta = None; sin filas con
    ingesta completa = ceros medidos; con filas = sumas (veneno intacto)."""
    if not _completo(desde, hasta, dias):
        return Tramo(None, None, None, None, None)
    if filas == 0:
        return Tramo(0, 0, Decimal(0), Decimal(0), 0)
    return Tramo(*valores)


def _junta(valores: list) -> Any:
    """Suma con veneno por metrica (semantica del bool_and global)."""
    if any(v is None for v in valores):
        return None
    return sum(valores)


def _enrolla(niveles: list[EvidenciaNivel]) -> EvidenciaNivel | None:
    if not niveles:
        return None
    return EvidenciaNivel(
        reciente=Tramo(
            clics=_junta([e.reciente.clics for e in niveles]),
            pedidos=_junta([e.reciente.pedidos for e in niveles]),
            venta=_junta([e.reciente.venta for e in niveles]),
            gasto=_junta([e.reciente.gasto for e in niveles]),
            impresiones=_junta([e.reciente.impresiones for e in niveles]),
        ),
        antiguo=Tramo(
            clics=_junta([e.antiguo.clics for e in niveles]),
            pedidos=_junta([e.antiguo.pedidos for e in niveles]),
            venta=_junta([e.antiguo.venta for e in niveles]),
            gasto=_junta([e.antiguo.gasto for e in niveles]),
            impresiones=_junta([e.antiguo.impresiones for e in niveles]),
        ),
    )


def _cambios_por_hoja(filas: list) -> dict[int, tuple[CambioBid, ...]]:
    """Trayectorias ascendentes desde `v_cambio_bid` (el SQL ya ordena). El
    `bid_antes` de un regreso del dueno es el `bid_despues` del cambio
    anterior de esa hoja; sin cambio anterior se omite (regla 3: inventar
    el previo corromperia el piso aprendido)."""
    por_hoja: dict[int, list[CambioBid]] = {}
    for hoja_id, confirmado_el, bid_antes, bid_despues, origen in filas:
        previos = por_hoja.setdefault(hoja_id, [])
        if bid_antes is None:
            if not previos:
                continue
            bid_antes = previos[-1].bid_despues
        previos.append(
            CambioBid(
                fecha=confirmado_el.astimezone(dt.UTC).date(),
                bid_antes=bid_antes,
                bid_despues=bid_despues,
                origen=origen,
            )
        )
    return {hoja_id: tuple(cambios) for hoja_id, cambios in por_hoja.items()}


def _mide(
    filas: int,
    valor: Any,
    desde: dt.date,
    hasta: dt.date,
    dias: frozenset[dt.date],
    cero: Any,
) -> Any:
    """Una metrica del efecto: hueco = None; rango vacio = cero tipado;
    con filas = suma (veneno intacto)."""
    if not _completo(desde, hasta, dias):
        return None
    if filas == 0:
        return cero
    return valor


@dataclass(frozen=True)
class LecturasPlataforma:
    """Todo lo que se lee UNA vez por plataforma y ciclo: tramos por hoja,
    roll-up a ad group y cuenta solo con hojas activas, cambios de bid de
    90 dias, precio, inmaduros y calendario de dias con ingesta. Los dicts
    son privados: el unico acceso es `caso`."""

    plataforma: Plataforma
    decidido_el: dt.datetime
    economia: EconomiaPlataforma
    _por_hoja: dict[int, EvidenciaNivel]
    _por_grupo: dict[int, EvidenciaNivel]
    _cuenta: EvidenciaNivel | None
    _cambios: dict[int, tuple[CambioBid, ...]]
    _precio: dict[int, PrecioVentana]
    _inmaduros: dict[int, int | None]
    _dias_con_ingesta: frozenset[dt.date]
    _d: dt.date
    _visto_el: dt.datetime | None
    _observado_al: dt.datetime | None

    def caso(
        self,
        conn: Conexion,
        *,
        hoja_id: int,
        ad_group_id: int | None,
        bid: BidVigente,
        target_acos_pct: Decimal,
        pausa: InsumosPausa,
    ) -> CasoHoja:
        """Arma el caso de una hoja. Cero consultas si la hoja no cambio de
        bid en 90 dias; UNA consulta (`_SQL_EFECTO`) si cambio. La ventana
        de cortes de la pausa la sigue leyendo cycle con windows (llega
        dentro de `pausa`)."""
        cambios = self._cambios.get(hoja_id, ())
        if cambios:
            trayectoria = Trayectoria(
                cambios=cambios, efecto=_lee_efecto(conn, self, hoja_id, cambios)
            )
        else:
            trayectoria = Trayectoria(cambios=(), efecto=None)
        return CasoHoja(
            plataforma=self.plataforma,
            hoja_id=hoja_id,
            ad_group_id=ad_group_id,
            bid=bid,
            economia=Economia(plataforma=self.economia, target_acos_pct=target_acos_pct),
            propia=self._por_hoja.get(hoja_id),
            pedidos_inmaduros=self._inmaduros.get(hoja_id),
            grupo=self._por_grupo.get(ad_group_id),
            cuenta=self._cuenta,
            precio=self._precio.get(hoja_id, PrecioVentana(gasto=None, clics=None)),
            trayectoria=trayectoria,
            pausa=pausa,
            ventana_desde=self._d - dt.timedelta(days=DIAS_LOOKBACK),
            ventana_hasta=self._d - dt.timedelta(days=DIAS_MADUREZ),
            observado_al=self._observado_al,
        )


def _lee_efecto(
    conn: Conexion,
    lect: LecturasPlataforma,
    hoja_id: int,
    cambios: tuple[CambioBid, ...],
) -> EfectoCambio:
    """Efecto del ultimo cambio de la hoja (UNA consulta). Los rangos son
    los del prototipo (exporta M.1); `dias_post` cuenta dias con ingesta."""
    uno = dt.timedelta(days=1)
    x = cambios[-1].fecha
    d = lect._d
    dias = lect._dias_con_ingesta
    ayer = d - dt.timedelta(days=DIAS_INMADUROS_HASTA)
    dias_post = sum(1 for n in range((ayer - x).days) if x + uno * (n + 1) in dias)
    k = min(DIAS_EFECTO, dias_post)
    fresco = d - dt.timedelta(days=DIAS_FRESCURA_BIDS)
    pre7 = (x - dt.timedelta(days=DIAS_EFECTO), x - uno)
    post = (x + uno, x + uno * k)
    postp = (x + uno, fresco)
    previo = (
        cambios[-2].fecha + uno if len(cambios) > 1 else x - dt.timedelta(days=DIAS_PRECIO_PREVIO)
    )
    prep = (max(previo, x - dt.timedelta(days=DIAS_PRECIO_PREVIO)), x - uno)
    ven = (x - dt.timedelta(days=DIAS_LOOKBACK), x - uno)
    fuente, pv = _fuente(lect._visto_el)
    fechas = [*pre7] * 5 + [*post] * 3 + [*postp] * 5 + [*prep] * 5 + [*ven] * 3
    fila = conn.execute(
        _SQL_EFECTO.format(fuente=fuente),
        (*fechas, *pv, hoja_id, lect.economia.moneda, ven[0], max(fresco, post[1])),
    ).fetchone()
    (
        n_pre7,
        i_pre7,
        c_pre7,
        n_post,
        i_post,
        n_postp,
        c_postp,
        g_postp,
        n_prep,
        c_prep,
        g_prep,
        n_ven,
        p_ven,
    ) = fila
    vendidos = _mide(n_ven, p_ven, ven[0], ven[1], dias, 0)
    return EfectoCambio(
        dias_post=dias_post,
        impresiones_pre7=_mide(n_pre7, i_pre7, pre7[0], pre7[1], dias, 0),
        clics_pre7=_mide(n_pre7, c_pre7, pre7[0], pre7[1], dias, 0),
        impresiones_post=_mide(n_post, i_post, post[0], post[1], dias, 0),
        dias_post_trafico=k,
        clics_post=_mide(n_postp, c_postp, postp[0], postp[1], dias, 0),
        gasto_post=_mide(n_postp, g_postp, postp[0], postp[1], dias, Decimal(0)),
        clics_pre=_mide(n_prep, c_prep, prep[0], prep[1], dias, 0),
        gasto_pre=_mide(n_prep, g_prep, prep[0], prep[1], dias, Decimal(0)),
        vendia=None if vendidos is None else vendidos >= 1,
    )


def lee_plataforma(
    conn: Conexion,
    plataforma: Plataforma,
    decidido_el: dt.datetime,
    *,
    economia: EconomiaPlataforma,
    visto_el: dt.datetime | None = None,
) -> LecturasPlataforma:
    """Cuatro consultas por plataforma. `visto_el` None = ultima observacion
    (v_metric_latest, el ciclo). `visto_el` con fecha = lectura bitemporal
    "como se veia" (metrics_as_of): es lo que usa el rejuego de ciclos
    pasados, y por eso la regla 5 (append-only) alcanza para validar el
    mismo dia."""
    _exige_tz(decidido_el, "decidido_el")
    if visto_el is not None:
        _exige_tz(visto_el, "visto_el")
    d = decidido_el.astimezone(dt.UTC).date()
    hasta = d - dt.timedelta(days=DIAS_MADUREZ)
    desde = d - dt.timedelta(days=DIAS_LOOKBACK)
    rec_desde = hasta - dt.timedelta(days=DIAS_TRAMO_RECIENTE - 1)
    ant_hasta = rec_desde - dt.timedelta(days=1)
    cal_desde = d - dt.timedelta(days=2 * DIAS_LOOKBACK)
    ayer = d - dt.timedelta(days=DIAS_INMADUROS_HASTA)
    pre_desde = d - dt.timedelta(days=DIAS_PRECIO_DESDE)
    fresco = d - dt.timedelta(days=DIAS_FRESCURA_BIDS)
    inm_desde = d - dt.timedelta(days=DIAS_INMADUROS_DESDE)
    moneda = economia.moneda
    fuente, pv = _fuente(visto_el)
    # 11 por tramo = 1 conteo + 5 metricas x 2 filtros (veneno y suma).
    p_rec = [rec_desde, hasta] * 11
    p_ant = [desde, ant_hasta] * 11
    filas_tramos = conn.execute(
        _SQL_TRAMOS.format(fuente=fuente),
        (*p_rec, *p_ant, *pv, plataforma, moneda, cal_desde, ayer),
    ).fetchall()
    filas_hojas = conn.execute(_SQL_HOJAS, (plataforma,)).fetchall()
    tope = "" if visto_el is None else " AND c.confirmado_el <= %s"
    p_cambios: tuple = (plataforma, dt.datetime(desde.year, desde.month, desde.day, tzinfo=dt.UTC))
    if visto_el is not None:
        p_cambios = (*p_cambios, visto_el)
    filas_cambios = conn.execute(_SQL_CAMBIOS.format(tope=tope), p_cambios).fetchall()
    p_pre = [pre_desde, fresco] * 5
    p_inm = [inm_desde, ayer] * 3
    filas_precio = conn.execute(
        _SQL_PRECIO.format(fuente=fuente),
        (*p_pre, *p_inm, *pv, plataforma, moneda, pre_desde, ayer),
    ).fetchall()
    dias = frozenset(f for fila in filas_tramos for f in (fila[14] or ()))
    por_hoja = {}
    for fila in filas_tramos:
        (hoja, fr, c_r, p_r, v_r, g_r, i_r, fa, c_a, p_a, v_a, g_a, i_a, _obs, _f) = fila
        if fr + fa == 0:
            continue  # sin filas en la ventana madura: propia None
        por_hoja[hoja] = EvidenciaNivel(
            reciente=_tramo(fr, (c_r, p_r, v_r, g_r, i_r), rec_desde, hasta, dias),
            antiguo=_tramo(fa, (c_a, p_a, v_a, g_a, i_a), desde, ant_hasta, dias),
        )
    activas: dict[int, int] = {}
    miembros: dict[int, list[int]] = {}
    for hoja, grupo in filas_hojas:
        activas[hoja] = grupo
        miembros.setdefault(grupo, []).append(hoja)
    por_grupo = {}
    for grupo, hojas in miembros.items():
        nivel = _enrolla([por_hoja[h] for h in hojas if h in por_hoja])
        if nivel is not None:
            por_grupo[grupo] = nivel
    cuenta = _enrolla([ev for h, ev in por_hoja.items() if h in activas])
    precio_ok = _completo(pre_desde, fresco, dias)
    inm_ok = _completo(inm_desde, ayer, dias)
    precio = {}
    inmaduros = {}
    for hoja, f_pre, gasto, clics, f_inm, pedidos, _obs in filas_precio:
        if not precio_ok:
            precio[hoja] = PrecioVentana(gasto=None, clics=None)
        elif f_pre == 0:
            precio[hoja] = PrecioVentana(gasto=Decimal(0), clics=0)
        else:
            precio[hoja] = PrecioVentana(gasto=gasto, clics=clics)
        if not inm_ok:
            inmaduros[hoja] = None
        elif f_inm == 0:
            inmaduros[hoja] = 0
        else:
            inmaduros[hoja] = pedidos
    sellos = [fila[13] for fila in filas_tramos] + [fila[6] for fila in filas_precio]
    observado = max((s for s in sellos if s is not None), default=None)
    return LecturasPlataforma(
        plataforma=plataforma,
        decidido_el=decidido_el,
        economia=economia,
        _por_hoja=por_hoja,
        _por_grupo=por_grupo,
        _cuenta=cuenta,
        _cambios=_cambios_por_hoja(filas_cambios),
        _precio=precio,
        _inmaduros=inmaduros,
        _dias_con_ingesta=dias,
        _d=d,
        _visto_el=visto_el,
        _observado_al=observado,
    )
