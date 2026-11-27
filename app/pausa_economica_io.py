"""Lectura batch y persistencia de la pausa economica v2 (P03).

`lee_cache_reciente` hace UN SQL por barrido sobre `[D-40 .. D-1]` (mismo rango
y mismo criterio de cobertura que `app.proteccion_venta.lee_cache`) y deja en
memoria la serie diaria de `cost`/`ad_revenue`/`orders` por hoja, mas los dias
envenenados. `CacheRecienteEconomica.lectura` recorta en memoria y calcula
`completa` por cobertura de plataforma + veneno: un dia cubierto sin fila de la
hoja es CERO medido; una fila con NULL, negativa u otra moneda envenena la
ventana (regla 3: jamas un cero inventado).

`tx3_pausa_economica` inserta las evaluaciones del ciclo dentro de su propio
`SAVEPOINT`: un fallo de insercion se anota en `cuerpo['pausa_economica']
['error_tx3']` y TX3 sigue (nunca tumba las decisiones de puja del ciclo).

La fila es append-only (migracion 0105). El dueño de la regla sigue siendo
`app.optimizer.pausa_economica`; este modulo solo lleva los datos.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

import psycopg
from psycopg.types.json import Json

from app.lecturas_efecto import _completo
from app.optimizer import pausa_economica as regla
from app.optimizer.bid import PLATAFORMAS_MONEDA
from app.optimizer.caso import LecturaRecienteEconomica
from app.optimizer.windows import DIAS_MADUREZ_CORTES, DIAS_VENTANA
from app.redaction import scrub

if TYPE_CHECKING:
    from app.cycle_grupos import _Contadores
    from app.cycle_serializacion import _Pendiente

# Un SQL por barrido: la moneda viaja en la fila (NO en el WHERE) para que una
# fila de otra moneda envenene la ventana en vez de desaparecer en silencio.
_SQL_RECIENTES = """
SELECT v.ad_entity_id, v.metric_date, v.metric_currency,
       v.cost, v.ad_revenue, v.orders
  FROM v_metric_latest v
  JOIN ad_entity k ON k.id = v.ad_entity_id
 WHERE k.platform = %s::platform
   AND k.kind IN ('keyword', 'product_target')
   AND v.metric_date BETWEEN %s AND %s
 ORDER BY v.ad_entity_id, v.metric_date
"""

_SQL_INSERT = """
INSERT INTO pausa_economica_evaluacion (
    cycle_id, ad_entity_id, platform, modo, politica, config_version_id, decided_at,
    ventana_reciente_desde, ventana_reciente_hasta, moneda, target_acos_pct,
    cortes_completa, cost_maduro, revenue_maduro, cost_reciente, revenue_reciente,
    exceso_combinado, exceso_minimo, entrada, veredicto, motivo, decision_id)
VALUES (%s, %s, %s::platform, %s, %s, %s, %s, %s, %s, %s::currency, %s,
        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (cycle_id, ad_entity_id) DO NOTHING
"""


@dataclass(frozen=True)
class _SerieHoja:
    """Serie diaria valida y dias venenosos de una hoja en el rango leido."""

    valores: dict[dt.date, tuple[Decimal, Decimal, int]]
    venenosos: frozenset[dt.date]


@dataclass(frozen=True)
class CacheRecienteEconomica:
    """Lectura por lote del ciclo o del barrido. Sin consultas por hoja."""

    plataforma: str
    evaluado_el: dt.date
    moneda: str
    _lee_desde: dt.date
    _dias_plataforma: frozenset[dt.date]
    _series: dict[int, _SerieHoja]

    def lectura(self, hoja_id: int, *, desde: dt.date, hasta: dt.date) -> LecturaRecienteEconomica:
        """Ventana reciente de la hoja; `completa` solo si el rango esta
        cubierto por la plataforma y ninguna fila de la hoja esta envenenada."""
        serie = self._series.get(hoja_id)
        veneno = serie is not None and any(desde <= fecha <= hasta for fecha in serie.venenosos)
        cubierta = (
            desde <= hasta
            and desde >= self._lee_desde
            and _completo(desde, hasta, self._dias_plataforma)
        )
        if not cubierta or veneno:
            return LecturaRecienteEconomica(
                desde=desde,
                hasta=hasta,
                evaluado_el=self.evaluado_el,
                moneda=self.moneda,
                completa=False,
                cost=None,
                ad_revenue=None,
                orders=None,
            )
        cost = Decimal(0)
        ad_revenue = Decimal(0)
        orders = 0
        fecha = desde
        while fecha <= hasta:
            if serie is not None and fecha in serie.valores:
                costo_dia, venta_dia, pedidos_dia = serie.valores[fecha]
                cost += costo_dia
                ad_revenue += venta_dia
                orders += pedidos_dia
            fecha += dt.timedelta(days=1)
        return LecturaRecienteEconomica(
            desde=desde,
            hasta=hasta,
            evaluado_el=self.evaluado_el,
            moneda=self.moneda,
            completa=True,
            cost=cost,
            ad_revenue=ad_revenue,
            orders=orders,
        )


@dataclass(frozen=True)
class EvaluacionPausa:
    """Una hoja juzgada por la regla en el ciclo: fila de auditoria en TX3."""

    ad_entity_id: int
    platform: str
    modo: str
    decided_at: dt.datetime
    ventana_desde: dt.date
    ventana_hasta: dt.date
    moneda: str
    target_acos_pct: Decimal
    entrada: regla.EntradaPausaEconomica
    veredicto: regla.VeredictoPausaEconomica


def _fila_serie(filas: list[tuple], moneda: str) -> tuple[_SerieHoja, set[dt.date]]:
    valores: dict[dt.date, tuple[Decimal, Decimal, int]] = {}
    venenosos: set[dt.date] = set()
    dias: set[dt.date] = set()
    for _hoja, fecha, moneda_fila, cost, ad_revenue, orders in filas:
        dias.add(fecha)
        if (
            moneda_fila != moneda
            or cost is None
            or ad_revenue is None
            or orders is None
            or cost < 0
            or ad_revenue < 0
            or orders < 0
        ):
            venenosos.add(fecha)
            valores.pop(fecha, None)
            continue
        valores[fecha] = (cost, ad_revenue, orders)
    return _SerieHoja(valores=valores, venenosos=frozenset(venenosos)), dias


def lee_cache_reciente(
    conn: psycopg.Connection, platform: str, *, ahora: dt.datetime
) -> CacheRecienteEconomica:
    """UN SELECT por plataforma. `ahora` es el reloj del intento (tz-aware)."""
    if ahora.tzinfo is None:
        raise ValueError("ahora debe ser tz-aware: un naive evaluaria segun la TZ local")
    evaluado_el = ahora.astimezone(dt.UTC).date()
    lee_desde = evaluado_el - dt.timedelta(days=DIAS_VENTANA + DIAS_MADUREZ_CORTES)
    hasta = evaluado_el - dt.timedelta(days=1)
    moneda = PLATAFORMAS_MONEDA[platform]
    filas = conn.execute(_SQL_RECIENTES, (platform, lee_desde, hasta)).fetchall()
    por_hoja: dict[int, list[tuple]] = {}
    for fila in filas:
        por_hoja.setdefault(fila[0], []).append(fila)
    series: dict[int, _SerieHoja] = {}
    dias: set[dt.date] = set()
    for hoja, filas_hoja in por_hoja.items():
        serie, dias_hoja = _fila_serie(filas_hoja, moneda)
        series[hoja] = serie
        dias.update(dias_hoja)
    return CacheRecienteEconomica(
        plataforma=platform,
        evaluado_el=evaluado_el,
        moneda=moneda,
        _lee_desde=lee_desde,
        _dias_plataforma=frozenset(dias),
        _series=series,
    )


def _decisiones_economicas(
    pendientes: Sequence[_Pendiente], ids_decisiones: Sequence[int]
) -> dict[int, int]:
    """`ad_entity_id -> decision_id` solo de las pausas economicas emitidas.

    `_inserta_decisiones` devuelve los ids en el MISMO orden de `pendientes`;
    los pendientes de otras hojas nunca comparten clave."""
    mapa: dict[int, int] = {}
    for indice, pendiente in enumerate(pendientes):
        if indice >= len(ids_decisiones):
            break
        if (
            pendiente.kind == "pause"
            and pendiente.inputs.get("motivo") == regla.MOTIVO_PAUSE_ECONOMICA
        ):
            mapa[pendiente.ad_entity_id] = ids_decisiones[indice]
    return mapa


def tx3_pausa_economica(
    conn: psycopg.Connection,
    cycle_id: int,
    config_id: int,
    contadores: _Contadores,
    pendientes: Sequence[_Pendiente],
    ids_decisiones: Sequence[int],
    cuerpo: dict,
) -> None:
    """Inserta evaluaciones en pausa_economica_evaluacion dentro de su propio SAVEPOINT.
    Si falla, anota error_tx3 en cuerpo['pausa_economica'] sin abortar TX3."""
    evaluaciones = contadores.pausa_economica_evals
    if not evaluaciones:
        return
    ids_por_hoja = _decisiones_economicas(pendientes, ids_decisiones)
    try:
        with conn.transaction():
            for evaluacion in evaluaciones:
                veredicto = evaluacion.veredicto
                decision_id = (
                    ids_por_hoja.get(evaluacion.ad_entity_id) if evaluacion.modo == "live" else None
                )
                conn.execute(
                    _SQL_INSERT,
                    (
                        cycle_id,
                        evaluacion.ad_entity_id,
                        evaluacion.platform,
                        evaluacion.modo,
                        regla.POLITICA_PAUSE_ECONOMICA_V2,
                        config_id,
                        evaluacion.decided_at,
                        evaluacion.ventana_desde,
                        evaluacion.ventana_hasta,
                        evaluacion.moneda,
                        evaluacion.target_acos_pct,
                        veredicto.cortes_completa,
                        veredicto.cost_maduro,
                        veredicto.revenue_maduro,
                        veredicto.cost_reciente,
                        veredicto.revenue_reciente,
                        veredicto.exceso_combinado,
                        evaluacion.entrada.exceso_minimo,
                        Json(regla.entrada_a_json(evaluacion.entrada)),
                        veredicto.veredicto,
                        veredicto.motivo,
                        decision_id,
                    ),
                )
    except Exception as exc:
        bloque = cuerpo.setdefault("pausa_economica", {})
        bloque["error_tx3"] = scrub(str(exc)) or type(exc).__name__


def resumen_evals(
    evaluaciones: Sequence[EvaluacionPausa],
    *,
    sin_cortes: int = 0,
    anomalia_tope: bool = False,
) -> dict:
    """El bloque `notes.pausa_economica`, construido UNA vez desde la lista."""
    veredictos = Counter(evaluacion.veredicto.veredicto for evaluacion in evaluaciones)
    motivos = Counter(evaluacion.veredicto.motivo for evaluacion in evaluaciones)
    resumen = {
        "evaluadas": len(evaluaciones),
        "pausar": veredictos.get("pausar", 0),
        "mantener": veredictos.get("mantener", 0),
        "sin_cortes": sin_cortes,
        "motivos": dict(motivos),
    }
    if anomalia_tope:
        resumen["anomalia_tope"] = True
    return resumen


def esquema_pausa_economica_disponible(conn: psycopg.Connection) -> bool:
    """False en una base anterior a la migracion 0105."""
    return bool(
        conn.execute(
            "SELECT to_regclass('public.pausa_economica_evaluacion') IS NOT NULL"
        ).fetchone()[0]
    )


def resumen_ultimo_ciclo(conn: psycopg.Connection, platform: str) -> dict | None:
    """Conteos del ultimo ciclo que evaluo la pausa economica en el mercado."""
    if not esquema_pausa_economica_disponible(conn):
        return None
    ciclo = conn.execute(
        "SELECT max(cycle_id) FROM pausa_economica_evaluacion WHERE platform = %s",
        (platform,),
    ).fetchone()[0]
    if ciclo is None:
        return None
    fila = conn.execute(
        """
        SELECT count(*),
               count(*) FILTER (WHERE veredicto = 'pausar'),
               count(*) FILTER (WHERE veredicto = 'pausar' AND modo = 'live'),
               count(*) FILTER (WHERE veredicto = 'pausar' AND modo = 'live'
                                  AND decision_id IS NULL)
          FROM pausa_economica_evaluacion
         WHERE cycle_id = %s
        """,
        (ciclo,),
    ).fetchone()
    return {
        "cycle_id": ciclo,
        "evaluadas": fila[0],
        "pausar": fila[1],
        "pausar_live": fila[2],
        "pausar_sin_decision": fila[3],
    }
