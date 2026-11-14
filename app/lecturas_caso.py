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
from dataclasses import dataclass, replace
from decimal import Decimal, localcontext
from typing import Any, Protocol

from app.optimizer.caso import (
    DIAS_ECONOMIA_CAMBIO,
    DIAS_LOOKBACK,
    DIAS_MADUREZ,
    DIAS_MADUREZ_ECONOMIA,
    DIAS_TRAMO_RECIENTE,
    DIAS_VIGENCIA_ECONOMIA_CAMBIO,
    MOTIVO_ECONOMIA_CAMBIO_EN_VENTANA,
    MOTIVO_ECONOMIA_CAMBIO_POSTERIOR,
    MOTIVO_ECONOMIA_COBERTURA_INCOMPLETA,
    MOTIVO_ECONOMIA_EVIDENCIA_VENCIDA,
    MOTIVO_ECONOMIA_POST_INMADURO,
    PRECISION,
    REVISION_POLITICA,
    BidVigente,
    CambioBid,
    CasoHoja,
    Economia,
    EconomiaPlataforma,
    EfectoCambio,
    EpisodioEconomico,
    EvidenciaNivel,
    InsumosPausa,
    Plataforma,
    PrecioVentana,
    Tramo,
    Trayectoria,
    VentanaEconomica,
)
from app.optimizer.goals import COOLDOWN
from app.optimizer.windows import DIAS_FRESCURA_BIDS, AgregadoMetricas

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
# rangos del efecto (el mas hondo es el precio previo: x-30 con x >= D-90).
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

# Hojas activas de la plataforma (el roll-up suma solo estas). La campana se
# usa para relacionar los eventos de configuracion con su hoja (M5).
_SQL_HOJAS = """
SELECT hoja_id, ad_group_id, campana_id FROM v_hoja_activa WHERE platform = %s
"""

# Cambios de bid de 90 dias de toda la plataforma, ascendentes por hoja
# (el orden lo fija el SQL: la derivacion del regreso lo exige).
_SQL_CAMBIOS = """
SELECT c.hoja_id, c.confirmado_el, c.bid_antes, c.bid_despues, c.origen, d.new_value
  FROM v_cambio_bid c
  JOIN ad_entity k ON k.id = c.hoja_id
  LEFT JOIN decision d ON d.id = c.decision_id AND c.origen = 'regreso_del_dueno'
 WHERE k.platform = %s::platform
   AND c.confirmado_el >= %s{tope}
 ORDER BY c.hoja_id, c.confirmado_el, c.decision_id
"""

# Efecto del ULTIMO cambio de UNA hoja (camino historico, regla apagada): una
# fila con conteo y sumas por lado del cambio (pre7, trafico post, precio
# post, precio previo, pedidos de cortes). Se conserva byte a byte el camino
# de la base para que apagar M5/M6 no pague columnas que no usa.
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

# ORBIT 07 M5 (regla encendida): el mismo efecto mas las dos ventanas
# economicas de siete fechas SIN el dia del cambio: [x-7, x-1] y [x+1, x+7].
# Las ventanas de trafico y precio conservan sus rangos historicos; las
# economicas son campos aparte para no cambiar R1/R15/R16 ni _precio_de_hoy.
_SQL_EFECTO_ECONOMIA = """
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
            THEN sum(v.orders) FILTER (WHERE v.metric_date BETWEEN %s AND %s)::bigint END,
       count(*) FILTER (WHERE v.metric_date BETWEEN %s AND %s),
       CASE WHEN bool_and(v.cost IS NOT NULL AND v.cost >= 0)
                 FILTER (WHERE v.metric_date BETWEEN %s AND %s)
            THEN sum(v.cost) FILTER (WHERE v.metric_date BETWEEN %s AND %s) END,
       CASE WHEN bool_and(v.ad_revenue IS NOT NULL AND v.ad_revenue >= 0)
                 FILTER (WHERE v.metric_date BETWEEN %s AND %s)
            THEN sum(v.ad_revenue) FILTER (WHERE v.metric_date BETWEEN %s AND %s) END,
       CASE WHEN bool_and(v.revenue_same_sku IS NOT NULL AND v.revenue_same_sku >= 0)
                 FILTER (WHERE v.metric_date BETWEEN %s AND %s)
            THEN sum(v.revenue_same_sku) FILTER (WHERE v.metric_date BETWEEN %s AND %s) END,
       CASE WHEN bool_and(v.orders IS NOT NULL AND v.orders >= 0)
                 FILTER (WHERE v.metric_date BETWEEN %s AND %s)
            THEN sum(v.orders) FILTER (WHERE v.metric_date BETWEEN %s AND %s)::bigint END,
       CASE WHEN bool_and(v.clicks IS NOT NULL AND v.clicks >= 0)
                 FILTER (WHERE v.metric_date BETWEEN %s AND %s)
            THEN sum(v.clicks) FILTER (WHERE v.metric_date BETWEEN %s AND %s)::bigint END,
       count(*) FILTER (WHERE v.metric_date BETWEEN %s AND %s),
       CASE WHEN bool_and(v.cost IS NOT NULL AND v.cost >= 0)
                 FILTER (WHERE v.metric_date BETWEEN %s AND %s)
            THEN sum(v.cost) FILTER (WHERE v.metric_date BETWEEN %s AND %s) END,
       CASE WHEN bool_and(v.ad_revenue IS NOT NULL AND v.ad_revenue >= 0)
                 FILTER (WHERE v.metric_date BETWEEN %s AND %s)
            THEN sum(v.ad_revenue) FILTER (WHERE v.metric_date BETWEEN %s AND %s) END,
       CASE WHEN bool_and(v.revenue_same_sku IS NOT NULL AND v.revenue_same_sku >= 0)
                 FILTER (WHERE v.metric_date BETWEEN %s AND %s)
            THEN sum(v.revenue_same_sku) FILTER (WHERE v.metric_date BETWEEN %s AND %s) END,
       CASE WHEN bool_and(v.orders IS NOT NULL AND v.orders >= 0)
                 FILTER (WHERE v.metric_date BETWEEN %s AND %s)
            THEN sum(v.orders) FILTER (WHERE v.metric_date BETWEEN %s AND %s)::bigint END,
       CASE WHEN bool_and(v.clicks IS NOT NULL AND v.clicks >= 0)
                 FILTER (WHERE v.metric_date BETWEEN %s AND %s)
            THEN sum(v.clicks) FILTER (WHERE v.metric_date BETWEEN %s AND %s)::bigint END
  FROM {fuente}
 WHERE v.ad_entity_id = %s
   AND v.metric_currency = %s
   AND v.metric_date BETWEEN %s AND %s
"""

# ORBIT 07 M5: eventos que invalidan la comparacion de una ventana. Solo se
# leen cuando la regla economica esta encendida, en lote por plataforma y con
# el corte bitemporal (visto_el) de la lectura. ads_campana_config_observation
# solo inserta filas cuando la config cambio (guarda_config); campana_ajuste
# solo trae ajustes confirmados; precio_cambio se ata al ad group por sus
# product ads (listing); un product_ad con first_seen_at nuevo cambia el
# alcance del grupo.
_SQL_EVENTOS_CAMPANA = """
SELECT o.ad_entity_id, o.observed_at
  FROM ads_campana_config_observation o
  JOIN ad_entity c ON c.id = o.ad_entity_id AND c.kind = 'campaign'
 WHERE c.platform = %s::platform
   AND o.observed_at >= %s AND o.observed_at <= %s
"""

_SQL_AJUSTES_CAMPANA = """
SELECT a.campana_id, a.confirmado_el
  FROM campana_ajuste a
 WHERE a.platform = %s::platform
   AND a.confirmado_el IS NOT NULL
   AND a.confirmado_el >= %s AND a.confirmado_el <= %s
"""

_SQL_PRECIOS_GRUPO = """
SELECT pa.parent_id, pc.enviado_at
  FROM precio_cambio pc
  JOIN ad_entity pa ON pa.platform = pc.platform
                   AND pa.kind = 'product_ad'
                   AND pa.listing_id = pc.listing_id
 WHERE pc.platform = %s::platform
   AND pc.aplicado IS TRUE
   AND pc.estado IN ('enviado', 'confirmado')
   AND pc.enviado_at IS NOT NULL
   AND pc.enviado_at >= %s AND pc.enviado_at <= %s
"""

_SQL_ANUNCIOS_NUEVOS = """
SELECT a.parent_id, a.first_seen_at
  FROM ad_entity a
 WHERE a.platform = %s::platform
   AND a.kind = 'product_ad'
   AND a.first_seen_at >= %s AND a.first_seen_at <= %s
"""

# ARREGLOS 01 T (unidad AD-U6a, C-H1): quinta fuente de invalidacion. Un
# negative o un harvest aplicado en el MISMO ad group cambia el alcance del
# grupo y contamina [x-7, x+7]; el harvest crea ademas un negative EXACT en el
# origen. Se lee en el mismo lote de eventos por ad group. 'ok' y
# 'ok:reconciliado' son los dos sellos de un intento aplicado (la misma regla
# que tools/dossier_adversarial.py:125): el reconciliador confirma un PUT
# ambiguo y el corte si ocurrio. La fecha del corte es `started_at`, el intento
# real que nace antes del HTTP; `finished_at` sella la reconciliacion y puede
# caer en otro ciclo, asi que no sirve para ubicar el corte en la ventana.
_SQL_CORTES_GRUPO = """
SELECT d.ad_entity_id, a.started_at
  FROM apply_attempt a
  JOIN decision d  ON d.id = a.decision_id AND d.kind IN ('negative','harvest')
  JOIN ad_entity g ON g.id = d.ad_entity_id AND g.kind = 'ad_group'
 WHERE g.platform = %s::platform
   AND a.resultado IN ('ok','ok:reconciliado')
   AND a.tipo IN ('normal','reversa')
   AND a.started_at >= %s AND a.started_at <= %s
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
    anterior de esa hoja; sin cambio anterior en la ventana sale del
    `new_value` de la decision revertida (B3: exacto con racha de un
    corte; con racha larga sobreestima el piso, lado fail-closed). Solo
    se omite si tampoco hay decision revertida con `new_value`."""
    por_hoja: dict[int, list[CambioBid]] = {}
    for hoja_id, confirmado_el, bid_antes, bid_despues, origen, revierte_new in filas:
        previos = por_hoja.setdefault(hoja_id, [])
        if bid_antes is None:
            anterior = next((p for p in reversed(previos) if p.origen != "ajuste_de_campana"), None)
            if anterior is not None:
                # B6: la fila del ajuste trae el bid de HOY, no un
                # historico; derivar de ella falsea el piso aprendido.
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
    _campanas: dict[int, int]
    _eventos_campana: dict[int, tuple[dt.date, ...]]
    _eventos_grupo: dict[int, tuple[dt.date, ...]]
    _version_economia: str | None
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
        equilibrio_acos_pct: Decimal | None = None,
    ) -> CasoHoja:
        """Arma el caso de una hoja. Cero consultas si la hoja no cambio de
        bid en 90 dias; UNA consulta (`_SQL_EFECTO`) si cambio. La ventana
        de cortes de la pausa la sigue leyendo cycle con windows (llega
        dentro de `pausa`).

        ORBIT 07 M2: `equilibrio_acos_pct` sustituye el equilibrio de la
        plataforma por el del producto cuando la meta individual gana. El
        target y su punto de equilibrio viajan juntos; None conserva la
        semantica anterior (mismo equilibrio de plataforma).

        ORBIT 07 M5: con la regla economica encendida, la consulta del efecto
        trae ademas las ventanas economicas del ultimo cambio y el caso
        congela su episodio. Apagada, no se leen ni congelan episodios y el
        camino de consultas vuelve al de la base."""
        cambios = self._cambios.get(hoja_id, ())
        plataforma = (
            replace(self.economia, equilibrio_acos_pct=equilibrio_acos_pct)
            if equilibrio_acos_pct is not None
            else self.economia
        )
        if cambios:
            leido = _lee_efecto(conn, self, hoja_id, cambios, pausa.cortes)
            if self._version_economia is not None:
                assert leido.pre_economico is not None and leido.post_economico is not None
            episodio = (
                _episodio_economico(
                    self,
                    cambios=cambios,
                    pre=leido.pre_economico,
                    post=leido.post_economico,
                    margen_pct=plataforma.equilibrio_acos_pct,
                    campana_id=self._campanas.get(hoja_id),
                    ad_group_id=ad_group_id,
                )
                if self._version_economia is not None
                else None
            )
            trayectoria = Trayectoria(cambios=cambios, efecto=leido.efecto, episodio=episodio)
        else:
            trayectoria = Trayectoria(cambios=(), efecto=None)
        return CasoHoja(
            plataforma=self.plataforma,
            hoja_id=hoja_id,
            ad_group_id=ad_group_id,
            bid=bid,
            economia=Economia(plataforma=plataforma, target_acos_pct=target_acos_pct),
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
            revision=REVISION_POLITICA,
        )

    def efecto(
        self, conn: Conexion, *, hoja_id: int, cortes: AgregadoMetricas | None
    ) -> tuple[CambioBid, EfectoCambio] | None:
        """Ultimo cambio y su efecto como en `caso`; None sin cambio en 90 dias."""
        cambios = self._cambios.get(hoja_id, ())
        if not cambios:
            return None
        return cambios[-1], _lee_efecto(conn, self, hoja_id, cambios, cortes).efecto


@dataclass(frozen=True)
class _EfectoLeido:
    """La consulta unica del efecto devuelve tambien las dos ventanas
    economicas (M5) para congelarlas en el episodio sin otra consulta.
    Apagada la regla, las dos ventanas son None y se ejecuta el SQL historico."""

    efecto: EfectoCambio
    pre_economico: VentanaEconomica | None
    post_economico: VentanaEconomica | None


def _lee_efecto(
    conn: Conexion,
    lect: LecturasPlataforma,
    hoja_id: int,
    cambios: tuple[CambioBid, ...],
    cortes: AgregadoMetricas | None,
) -> _EfectoLeido:
    """Efecto del ultimo cambio de la hoja (UNA consulta). Los rangos son
    los del prototipo (exporta M.1); `dias_post` cuenta dias con ingesta.
    CLARIDAD 01 K: `vendia` se mide en la ventana de CORTES de la pausa
    (windows.ventana_cortes), no en los 90 dias previos al cambio: una venta
    vieja no regresa la puja de una keyword sin pedidos. Sin cortes, None.
    Con la regla encendida, la MISMA consulta trae las ventanas economicas de
    SIETE fechas completas sin el dia del cambio: [x-7, x-1] y [x+1, x+7].
    Apagada, se ejecuta el SQL historico de 13 columnas."""
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
    # Sin cortes, un rango vacio: el FILTER de pedidos no cuenta filas.
    ven = (cortes.window_start, cortes.window_end) if cortes is not None else (x, x - uno)
    econ_pre = (x - dt.timedelta(days=DIAS_ECONOMIA_CAMBIO), x - uno)
    econ_post = (x + uno, x + dt.timedelta(days=DIAS_ECONOMIA_CAMBIO))
    fuente, pv = _fuente(lect._visto_el)
    base = [*pre7] * 5 + [*post] * 3 + [*postp] * 5 + [*prep] * 5 + [*ven] * 3
    if lect._version_economia is not None:
        sql = _SQL_EFECTO_ECONOMIA
        fechas = base + [*econ_pre] * 11 + [*econ_post] * 11
        limite = max(fresco, econ_post[1])
    else:
        sql = _SQL_EFECTO
        fechas = base
        limite = max(fresco, post[1])
    desde = min(pre7[0], prep[0], econ_pre[0], ven[0])
    fila = conn.execute(
        sql.format(fuente=fuente),
        (*fechas, *pv, hoja_id, lect.economia.moneda, desde, limite),
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
    ) = fila[:13]
    vendidos = None if cortes is None else _mide(n_ven, p_ven, ven[0], ven[1], dias, 0)
    efecto = EfectoCambio(
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
    if lect._version_economia is None:
        return _EfectoLeido(efecto=efecto, pre_economico=None, post_economico=None)
    (
        n_econ_pre,
        g_econ_pre,
        r_econ_pre,
        rs_econ_pre,
        o_econ_pre,
        c_econ_pre,
        n_econ_post,
        g_econ_post,
        r_econ_post,
        rs_econ_post,
        o_econ_post,
        c_econ_post,
    ) = fila[13:]
    return _EfectoLeido(
        efecto=efecto,
        pre_economico=_ventana_economica(
            n_econ_pre,
            econ_pre,
            dias,
            gasto=g_econ_pre,
            venta=r_econ_pre,
            venta_mismo_sku=rs_econ_pre,
            pedidos=o_econ_pre,
            clics=c_econ_pre,
        ),
        post_economico=_ventana_economica(
            n_econ_post,
            econ_post,
            dias,
            gasto=g_econ_post,
            venta=r_econ_post,
            venta_mismo_sku=rs_econ_post,
            pedidos=o_econ_post,
            clics=c_econ_post,
        ),
    )


def _ventana_economica(
    filas: int,
    rango: tuple[dt.date, dt.date],
    dias: frozenset[dt.date],
    *,
    gasto: Any,
    venta: Any,
    venta_mismo_sku: Any,
    pedidos: Any,
    clics: Any,
) -> VentanaEconomica:
    desde, hasta = rango
    return VentanaEconomica(
        desde=desde,
        hasta=hasta,
        gasto=_mide(filas, gasto, desde, hasta, dias, Decimal(0)),
        venta=_mide(filas, venta, desde, hasta, dias, Decimal(0)),
        venta_mismo_sku=_mide(filas, venta_mismo_sku, desde, hasta, dias, Decimal(0)),
        pedidos=_mide(filas, pedidos, desde, hasta, dias, 0),
        clics=_mide(filas, clics, desde, hasta, dias, 0),
    )


def _episodio_economico(
    lect: LecturasPlataforma,
    *,
    cambios: tuple[CambioBid, ...],
    pre: VentanaEconomica,
    post: VentanaEconomica,
    margen_pct: Decimal | None,
    campana_id: int | None,
    ad_group_id: int | None,
) -> EpisodioEconomico | None:
    """Episodio del ULTIMO cambio de bid real (no un ajuste de campana).

    `estado` separa madurez/comparabilidad de `aplicable`: un cambio de puja,
    presupuesto, placement, precio o alcance posterior deja el episodio como
    historia. Un evento DENTRO de [x-7, x+7] contaminaria la comparacion y la
    marca incomparable, aunque el episodio siga sin cambios posteriores. El
    episodio tambien caduca a los 31 dias del cambio (14 despues de madurar):
    mas viejo no gobierna la repeticion y R16 decide como antes.
    """
    reales = [
        i
        for i, cambio in enumerate(cambios)
        if cambio.bid_antes != cambio.bid_despues and cambio.origen != "ajuste_de_campana"
    ]
    if not reales:
        return None
    indice = reales[-1]
    cambio = cambios[indice]
    x = cambio.fecha
    d = lect._d
    version = lect._version_economia
    assert version is not None
    eventos = [otro.fecha for i, otro in enumerate(cambios) if i != indice]
    if campana_id is not None:
        eventos.extend(lect._eventos_campana.get(campana_id, ()))
    if ad_group_id is not None:
        eventos.extend(lect._eventos_grupo.get(ad_group_id, ()))
    desde = x - dt.timedelta(days=DIAS_ECONOMIA_CAMBIO)
    hasta = x + dt.timedelta(days=DIAS_ECONOMIA_CAMBIO)
    en_ventana = any(desde <= evento <= hasta for evento in eventos)
    posteriores = any(evento > x for evento in eventos)
    vencida = d >= x + dt.timedelta(days=DIAS_VIGENCIA_ECONOMIA_CAMBIO)
    aplicable = not posteriores and not vencida
    if aplicable:
        motivo_aplicabilidad = None
    elif vencida:
        motivo_aplicabilidad = MOTIVO_ECONOMIA_EVIDENCIA_VENCIDA
    else:
        motivo_aplicabilidad = MOTIVO_ECONOMIA_CAMBIO_POSTERIOR
    madura = hasta <= d - dt.timedelta(days=DIAS_MADUREZ_ECONOMIA)
    if not madura:
        estado = "inmaduro"
        motivo = MOTIVO_ECONOMIA_POST_INMADURO
    elif en_ventana:
        estado = "incomparable"
        motivo = MOTIVO_ECONOMIA_CAMBIO_EN_VENTANA
    elif not _ventanas_medidas(pre, post):
        estado = "incomparable"
        motivo = MOTIVO_ECONOMIA_COBERTURA_INCOMPLETA
    else:
        estado = "maduro"
        motivo = None
    return EpisodioEconomico(
        version=version,
        fecha_cambio=x,
        moneda=lect.economia.moneda,
        pre=pre,
        post=post,
        estado=estado,
        motivo=motivo,
        aplicable=aplicable,
        motivo_aplicabilidad=motivo_aplicabilidad,
        margen_pct=margen_pct,
        contribucion_pre=_contribucion(pre, margen_pct),
        contribucion_post=_contribucion(post, margen_pct),
    )


def _ventanas_medidas(pre: VentanaEconomica, post: VentanaEconomica) -> bool:
    """Las cuatro metricas que la regla necesita, conocidas en ambas ventanas.

    `venta_mismo_sku` se conserva como evidencia pero no condiciona: el
    margen del producto ya resolvio su compatibilidad y la regla usa la venta
    total del reporte de ads.
    """
    campos = ("gasto", "venta", "pedidos", "clics")
    return all(getattr(pre, campo) is not None for campo in campos) and all(
        getattr(post, campo) is not None for campo in campos
    )


def _contribucion(ventana: VentanaEconomica, margen_pct: Decimal | None) -> Decimal | None:
    if margen_pct is None or ventana.venta is None or ventana.gasto is None:
        return None
    with localcontext(prec=PRECISION):
        return ventana.venta * margen_pct / Decimal(100) - ventana.gasto


def lee_plataforma(
    conn: Conexion,
    plataforma: Plataforma,
    decidido_el: dt.datetime,
    *,
    economia: EconomiaPlataforma,
    visto_el: dt.datetime | None = None,
    version_economia: str | None = None,
) -> LecturasPlataforma:
    """Cuatro consultas por plataforma. `visto_el` None = ultima observacion
    (v_metric_latest, el ciclo). `visto_el` con fecha = lectura bitemporal
    "como se veia" (metrics_as_of): es lo que usa el rejuego de ciclos
    pasados, y por eso la regla 5 (append-only) alcanza para validar el
    mismo dia.

    `version_economia` (ORBIT 07 M5) agrega cinco consultas por plataforma
    para los eventos que invalidan un episodio (configuracion de campana,
    ajustes, precios, product ads nuevos y negatives o harvests aplicados en
    el mismo ad group) y estampa esa version en el episodio nuevo. None no las
    ejecuta y deja el camino de consultas historico."""
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
    eventos_campana: dict[int, list[dt.date]] = {}
    eventos_grupo: dict[int, list[dt.date]] = {}
    if version_economia is not None:
        corte = visto_el if visto_el is not None else decidido_el
        bajo = dt.datetime.combine(cal_desde, dt.time.min, tzinfo=dt.UTC)
        for consulta, destino in (
            (_SQL_EVENTOS_CAMPANA, eventos_campana),
            (_SQL_AJUSTES_CAMPANA, eventos_campana),
        ):
            for entidad, sello in conn.execute(consulta, (plataforma, bajo, corte)).fetchall():
                destino.setdefault(entidad, []).append(sello.astimezone(dt.UTC).date())
        for consulta in (_SQL_PRECIOS_GRUPO, _SQL_ANUNCIOS_NUEVOS, _SQL_CORTES_GRUPO):
            for entidad, sello in conn.execute(consulta, (plataforma, bajo, corte)).fetchall():
                eventos_grupo.setdefault(entidad, []).append(sello.astimezone(dt.UTC).date())
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
    campanas: dict[int, int] = {}
    for hoja, grupo, campana in filas_hojas:
        activas[hoja] = grupo
        campanas[hoja] = campana
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
        _campanas=campanas,
        _eventos_campana={k: tuple(v) for k, v in eventos_campana.items()},
        _eventos_grupo={k: tuple(v) for k, v in eventos_grupo.items()},
        _version_economia=version_economia,
        _dias_con_ingesta=dias,
        _d=d,
        _visto_el=visto_el,
        _observado_al=observado,
    )
