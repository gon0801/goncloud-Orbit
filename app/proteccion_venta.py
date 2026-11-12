"""Lectura y escritura de la proteccion por venta reciente (ORBIT 07 M1).

`lee_cache` hace DOS consultas de lectura por plataforma y barrido: la historia
de pedidos de cada hoja sobre el rango que cubre su ventana de cortes y los
episodios persistidos. La subpolitica pura (`app.optimizer.proteccion`) decide;
este modulo solo lleva los datos. La escritura es append-only y ocurre en TX3
del ciclo o en la transaccion de la cola: una fila por episodio (ancla +
vencimiento fijos).

El rango leido es D-(DIAS_VENTANA + DIAS_MADUREZ_CORTES)..D-1: alcanza para la
ventana reciente de cualquier hoja decidible, que empieza el dia siguiente al
fin de SU ventana de cortes. Un pedido conocido no se borra porque falte
cobertura o porque un dia venga envenenado; solo `pedidos` pasa a desconocido.

La cobertura del rango reciente es de PLATAFORMA, igual que la lectura de
inmaduros existente: un dia con ingesta de cualquier hoja keyword/product_target
cuenta. Entidad sin filas en una plataforma cubierta = cero MEDIDO; metrica
venenosa = None (desconocido, jamas cero).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import psycopg

from app.lecturas_caso import _completo
from app.optimizer.bid import PLATAFORMAS_MONEDA
from app.optimizer.caso import (
    VERSION_PROTECCION_VENTA,
    EpisodioProteccion,
    InsumosProteccion,
    LecturaReciente,
    ResultadoProteccion,
)
from app.optimizer.windows import DIAS_MADUREZ_CORTES, DIAS_VENTANA

DIAS_RECIENTES = 9  # D-9..D-1: la ventana minima de la regla de pujas

_SQL_RECIENTES = """
SELECT v.ad_entity_id,
       array_agg(v.metric_date ORDER BY v.metric_date) FILTER (WHERE v.orders > 0),
       array_agg(v.orders ORDER BY v.metric_date) FILTER (WHERE v.orders > 0),
       array_agg(v.metric_date) FILTER (WHERE v.orders IS NULL OR v.orders < 0),
       max(v.observed_at),
       array_agg(DISTINCT v.metric_date)
  FROM v_metric_latest v
  JOIN ad_entity k ON k.id = v.ad_entity_id
 WHERE k.platform = %s::platform
   AND k.kind IN ('keyword', 'product_target')
   AND v.metric_currency = %s
   AND v.metric_date BETWEEN %s AND %s
 GROUP BY v.ad_entity_id
"""

_SQL_EPISODIOS = """
SELECT ad_entity_id, ancla, vence_el
  FROM proteccion_venta_reciente
 WHERE platform = %s::platform AND version = %s
 ORDER BY ad_entity_id, ancla DESC
"""

_SQL_INSERT = """
INSERT INTO proteccion_venta_reciente
    (platform, ad_entity_id, version, ancla, vence_el, abierta_el, observado_el)
VALUES (%s::platform, %s, %s, %s, %s, %s, %s)
ON CONFLICT (platform, ad_entity_id, version, ancla) DO NOTHING
"""


@dataclass(frozen=True)
class TransicionProteccion:
    """Fila append-only que el escritor debe insertar al abrir un episodio."""

    platform: str
    ad_entity_id: int
    ancla: dt.date
    vence_el: dt.date
    abierta_el: dt.datetime
    observado_el: dt.datetime


@dataclass(frozen=True)
class _HistoriaReciente:
    """Pedidos y dias venenosos de una hoja en el rango leido."""

    con_pedido: tuple[tuple[dt.date, int], ...]
    venenosos: tuple[dt.date, ...]
    observado_al: dt.datetime | None


@dataclass(frozen=True)
class CacheProteccion:
    """Lectura por lote del barrido o del ciclo. Sin consultas por hoja."""

    plataforma: str
    evaluado_el: dt.date
    _lee_desde: dt.date
    _dias_plataforma: frozenset[dt.date]
    _historia: dict[int, _HistoriaReciente]
    _episodios: dict[int, EpisodioProteccion]

    def tiene_proteccion(self, hoja_id: int) -> bool:
        """True si la hoja tiene un episodio persistido o un pedido conocido en
        la ventana de pujas D-9..D-1. Es la puerta para no congelar sus pujas
        por una fila `released`.

        Un episodio vencido no se trata como veto: mientras la fila released
        siga esperando, el ciclo evalua la hoja y solo suprime la pausa por
        cero ventas (el ancla y el vencimiento originales no se tocan). La
        espera indeterminada (ancla aun fuera de la ventana madura) tampoco
        bloquea las pujas. El apagado de la regla es el setting de politica,
        no la simple ausencia de la fila released.
        """
        if hoja_id in self._episodios:
            return True
        hasta = self.evaluado_el - dt.timedelta(days=1)
        desde = self.evaluado_el - dt.timedelta(days=DIAS_RECIENTES)
        return self._lectura(hoja_id, desde, hasta).ancla is not None

    def insumos(
        self,
        hoja_id: int,
        *,
        ventana_desde: dt.date | None,
        ventana_hasta: dt.date | None,
    ) -> InsumosProteccion:
        """La lectura reciente de la hoja empieza el dia siguiente al fin de SU
        ventana de cortes (nunca antes que D-9) y termina en D-1."""
        desde = self.evaluado_el - dt.timedelta(days=DIAS_RECIENTES)
        if ventana_hasta is not None:
            desde = min(desde, ventana_hasta + dt.timedelta(days=1))
        hasta = self.evaluado_el - dt.timedelta(days=1)
        return InsumosProteccion(
            version=VERSION_PROTECCION_VENTA,
            evaluado_el=self.evaluado_el,
            reciente=self._lectura(hoja_id, desde, hasta),
            ventana_desde=ventana_desde,
            ventana_hasta=ventana_hasta,
            episodio=self._episodios.get(hoja_id),
        )

    def _lectura(self, hoja_id: int, desde: dt.date, hasta: dt.date) -> LecturaReciente:
        historia = self._historia.get(hoja_id)
        con_pedido = historia.con_pedido if historia is not None else ()
        venenosos = historia.venenosos if historia is not None else ()
        observado_al = historia.observado_al if historia is not None else None
        if desde < self._lee_desde:
            ancla = next((fecha for fecha, _ in con_pedido if fecha <= hasta), None)
            return LecturaReciente(
                pedidos=None, ancla=ancla, cubierta=False, observado_al=observado_al
            )
        cubierta = _completo(desde, hasta, self._dias_plataforma)
        ancla = next((fecha for fecha, _ in con_pedido if desde <= fecha <= hasta), None)
        veneno = any(desde <= fecha <= hasta for fecha in venenosos)
        if not cubierta or veneno:
            return LecturaReciente(
                pedidos=None, ancla=ancla, cubierta=cubierta, observado_al=observado_al
            )
        pedidos = sum(cantidad for fecha, cantidad in con_pedido if desde <= fecha <= hasta)
        return LecturaReciente(
            pedidos=pedidos, ancla=ancla, cubierta=True, observado_al=observado_al
        )

    def transicion(
        self, hoja_id: int, resultado: ResultadoProteccion, abierta_el: dt.datetime
    ) -> TransicionProteccion | None:
        if not resultado.abre_episodio or resultado.ancla is None or resultado.vence_el is None:
            return None
        historia = self._historia.get(hoja_id)
        observado = historia.observado_al if historia is not None else None
        observado_el = abierta_el if observado is None else min(abierta_el, observado)
        return TransicionProteccion(
            platform=self.plataforma,
            ad_entity_id=hoja_id,
            ancla=resultado.ancla,
            vence_el=resultado.vence_el,
            abierta_el=abierta_el,
            observado_el=observado_el,
        )


def lee_cache(conn: psycopg.Connection, platform: str, ahora: dt.datetime) -> CacheProteccion:
    """DOS SELECT por plataforma. `ahora` es el reloj del intento (tz-aware)."""
    if ahora.tzinfo is None:
        raise ValueError("ahora debe ser tz-aware: un naive evaluaria segun la TZ local")
    evaluado_el = ahora.astimezone(dt.UTC).date()
    lee_desde = evaluado_el - dt.timedelta(days=DIAS_VENTANA + DIAS_MADUREZ_CORTES)
    hasta = evaluado_el - dt.timedelta(days=1)
    moneda = PLATAFORMAS_MONEDA[platform]
    filas = conn.execute(
        _SQL_RECIENTES,
        (platform, moneda, lee_desde, hasta),
    ).fetchall()
    historia: dict[int, _HistoriaReciente] = {}
    dias: set[dt.date] = set()
    for hoja, fechas, pedidos, venenosos, observado, fechas_plataforma in filas:
        historia[hoja] = _HistoriaReciente(
            con_pedido=tuple(zip(fechas or (), pedidos or (), strict=True)),
            venenosos=tuple(venenosos or ()),
            observado_al=observado,
        )
        dias.update(fechas_plataforma or ())
    episodios: dict[int, EpisodioProteccion] = {}
    for hoja, ancla, vence_el in conn.execute(
        _SQL_EPISODIOS, (platform, VERSION_PROTECCION_VENTA)
    ).fetchall():
        episodios.setdefault(hoja, EpisodioProteccion(ancla=ancla, vence_el=vence_el))
    return CacheProteccion(
        plataforma=platform,
        evaluado_el=evaluado_el,
        _lee_desde=lee_desde,
        _dias_plataforma=frozenset(dias),
        _historia=historia,
        _episodios=episodios,
    )


def inserta_transiciones(
    conn: psycopg.Connection, transiciones: list[TransicionProteccion]
) -> None:
    """Append-only e idempotente por (platform, entidad, version, ancla)."""
    if not transiciones:
        return
    conn.cursor().executemany(
        _SQL_INSERT,
        [
            (
                t.platform,
                t.ad_entity_id,
                VERSION_PROTECCION_VENTA,
                t.ancla,
                t.vence_el,
                t.abierta_el,
                t.observado_el,
            )
            for t in transiciones
        ],
    )
