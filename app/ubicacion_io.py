"""Lectura congelada y persistencia de la evaluacion por ubicacion (AUTO-04).

Todas las lecturas de `lee_casos` corren sobre la MISMA conexion de TX2: el
snapshot es consistente y el `CasoCampana` que se congela en
`ubicacion_evaluacion.entrada` es exactamente el que juzgo la politica pura.

Los numeros salen de una sola fuente por dato: el gasto de campana de
`v_metric_latest` (grano campana), las ubicaciones de `ads_placement_observation`
colapsadas con `DISTINCT ON (placement, metric_date)` por `observed_at DESC`
(regla 5) y los dias al tope de `temporada.cuenta_dias_topados`, la misma
definicion que usa la pantalla y los avisos.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

import psycopg
from psycopg.types.json import Json

from app.lecturas_historia import presupuestos_campanas_hoy
from app.optimizer import goals as g
from app.optimizer import temporada
from app.optimizer.temporada_config import CampanaTopada
from app.optimizer.ubicacion import (
    DIAS_REZAGO,
    DIAS_VENTANA_MAX,
    MOTIVO_PRODUCTO_SIN_EVIDENCIA,
    POLITICA,
    UBICACIONES,
    CasoCampana,
    EvidenciaUbicacion,
    MantenerUbicacion,
    MetaCampana,
    MoverUbicacion,
    VeredictoUbicacion,
    caso_a_json,
    caso_desde_json,
    modo_ubicacion_desde_settings,
    solo_bajadas_desde_settings,
)

_CIEN = Decimal(100)

_SQL_CONFIGS_ACTIVAS = """
SELECT c.id, v.id, v.estrategia_puja,
       v.ajuste_top_pct, v.ajuste_resto_pct, v.ajuste_producto_pct
  FROM ad_entity c
  JOIN ad_entity_state s ON s.ad_entity_id = c.id AND s.status = 'ENABLED'
  JOIN v_campana_config_vigente v ON v.ad_entity_id = c.id
 WHERE c.platform = %s AND c.kind = 'campaign'
 ORDER BY c.id
"""

_SQL_HISTORIA_CONFIG = """
SELECT observed_at, estrategia_puja,
       ajuste_top_pct, ajuste_resto_pct, ajuste_producto_pct
  FROM ads_campana_config_observation
 WHERE ad_entity_id = %s
 ORDER BY observed_at ASC, id ASC
"""

_SQL_GASTO_CAMPANA = """
SELECT CASE WHEN bool_and(cost IS NOT NULL) THEN sum(cost) END
  FROM v_metric_latest
 WHERE ad_entity_id = %s AND metric_date BETWEEN %s AND %s
"""

_SQL_PLACEMENTS = """
WITH ult AS (
    SELECT DISTINCT ON (placement, metric_date)
           placement, metric_date, clicks, cost, orders, ad_revenue
      FROM ads_placement_observation
     WHERE ad_entity_id = %s AND platform = %s AND metric_date BETWEEN %s AND %s
     ORDER BY placement, metric_date, observed_at DESC
)
SELECT placement,
       CASE WHEN bool_and(clicks IS NOT NULL) THEN sum(clicks) END,
       CASE WHEN bool_and(cost IS NOT NULL) THEN sum(cost) END,
       CASE WHEN bool_and(orders IS NOT NULL) THEN sum(orders) END,
       CASE WHEN bool_and(ad_revenue IS NOT NULL) THEN sum(ad_revenue) END,
       max(metric_date)
  FROM ult
 GROUP BY placement
"""

_SQL_INMADUROS = """
WITH ult AS (
    SELECT DISTINCT ON (placement, metric_date) placement, metric_date, orders
      FROM ads_placement_observation
     WHERE ad_entity_id = %s AND platform = %s AND metric_date BETWEEN %s AND %s
     ORDER BY placement, metric_date, observed_at DESC
)
SELECT placement,
       CASE WHEN bool_and(orders IS NOT NULL) THEN sum(orders) END
  FROM ult
 GROUP BY placement
"""

_SQL_CAMPANAS_DE_HOJAS = """
SELECT DISTINCT campana_id
  FROM v_hoja_activa
 WHERE platform = %s AND hoja_id = ANY(%s)
"""

_SQL_DIAS_PLACEMENTS = """
SELECT DISTINCT metric_date
  FROM ads_placement_observation
 WHERE platform = %s AND metric_date BETWEEN %s AND %s
"""

_SQL_HOJAS = """
SELECT hoja_id, campana_id
  FROM v_hoja_activa
 WHERE platform = %s
"""

_SQL_IMPULSOS_ABIERTOS = """
SELECT DISTINCT ag.parent_id
  FROM v_ad_group_impulso_abierto v
  JOIN ad_entity ag ON ag.id = v.ad_group_ad_entity_id
 WHERE v.platform = %s AND ag.parent_id IS NOT NULL
"""


def sql_orden_viva(alias: str) -> str:
    """Predicado unico de orden viva sobre `campana_ajuste`.

    Una intencion automatica con desenlace en `ubicacion_sin_aplicar` no llego
    a Amazon (o Amazon mostro otro valor al reconciliar): ninguna guarda la
    cuenta como orden en curso ni la reconciliacion la sella. Las filas
    manuales (`evaluacion_id` NULL) siempre cuentan."""
    return (
        "NOT EXISTS (SELECT 1 FROM ubicacion_sin_aplicar s"
        f" WHERE s.evaluacion_id = {alias}.evaluacion_id)"
    )


_SQL_AJUSTES_EN_VUELO = f"""
SELECT DISTINCT a.campana_id
  FROM campana_ajuste a
 WHERE a.platform = %s AND a.confirmado_el IS NULL AND {sql_orden_viva("a")}
"""


def campanas_con_ajuste_en_vuelo(conn: psycopg.Connection, platform: str) -> frozenset[int]:
    return frozenset(f[0] for f in conn.execute(_SQL_AJUSTES_EN_VUELO, (platform,)))


@dataclass(frozen=True)
class SugerenciaUbicacion:
    """Sugerencia vigente de una campana, tal como se muestra en la pantalla."""

    campana_id: int
    evaluacion_id: int
    modo: str
    ubicacion: str
    de_pct: int
    a_pct: int
    motivo: str
    decided_at: dt.datetime


def _fecha_utc(momento: dt.datetime) -> dt.date:
    return momento.astimezone(dt.UTC).date()


def _normaliza(estrategia, top, resto, producto) -> tuple:
    return (estrategia, top or 0, resto or 0, producto or 0)


def _ultimo_cambio(conn, campana: int) -> dt.date | None:
    """Fecha UTC del ultimo cambio de estrategia o de cualquier multiplicador.

    La primera observacion no cuenta como cambio y `NULL` se normaliza a 0, de
    modo que una observacion que solo rellena un `NULL` no reinicia el
    enfriamiento.
    """
    previo = None
    ultimo = None
    for observed_at, estrategia, top, resto, producto in conn.execute(
        _SQL_HISTORIA_CONFIG, (campana,)
    ).fetchall():
        actual = _normaliza(estrategia, top, resto, producto)
        if previo is not None and actual != previo:
            ultimo = _fecha_utc(observed_at)
        previo = actual
    return ultimo


def _ventana(ultimo: dt.date | None, decided_at: dt.datetime) -> tuple[dt.date, dt.date]:
    """Ventana madura. Un cambio dentro de los ultimos 3 dias deja la ventana
    VACIA (`desde == hasta`), nunca invertida: el CHECK de la tabla prohibe
    `desde > hasta` y la compuerta de enfriamiento ya abstiene ese caso."""
    hoy = _fecha_utc(decided_at)
    hasta = hoy - dt.timedelta(days=DIAS_REZAGO)
    max_desde = hasta - dt.timedelta(days=DIAS_VENTANA_MAX - 1)
    if ultimo is None:
        return max_desde, hasta
    desde = max(max_desde, ultimo + dt.timedelta(days=1))
    return min(desde, hasta), hasta


def _placements(conn, campana: int, platform: str, desde: dt.date, hasta: dt.date) -> dict:
    filas = conn.execute(_SQL_PLACEMENTS, (campana, platform, desde, hasta)).fetchall()
    return {
        fila[0]: {
            "clics": fila[1],
            "gasto": fila[2],
            "pedidos": fila[3],
            "venta": fila[4],
            "max_fecha": fila[5],
        }
        for fila in filas
    }


def campanas_de_hojas(conn, platform: str, hojas) -> frozenset:
    if not hojas:
        return frozenset()
    return frozenset(
        fila[0] for fila in conn.execute(_SQL_CAMPANAS_DE_HOJAS, (platform, list(hojas)))
    )


def _dias_con_placements(conn, platform: str, desde: dt.date, hasta: dt.date) -> frozenset:
    return frozenset(
        fila[0] for fila in conn.execute(_SQL_DIAS_PLACEMENTS, (platform, desde, hasta))
    )


def _inmaduros(
    conn,
    campana: int,
    platform: str,
    decided_at: dt.datetime,
    *,
    cubiertos: bool,
) -> dict[str, int | None]:
    """Pedidos de D-2..D-1 por ubicacion. Sin cobertura de ingesta de
    placements en ambas fechas devuelve None en todas (no se inventa cero);
    con cobertura completa, una ubicacion sin filas es cero medido."""
    if not cubiertos:
        return {}
    hoy = _fecha_utc(decided_at)
    hasta = hoy - dt.timedelta(days=1)
    desde = hoy - dt.timedelta(days=2)
    filas = conn.execute(_SQL_INMADUROS, (campana, platform, desde, hasta)).fetchall()
    return {fila[0]: _entero(fila[1]) for fila in filas}


def _entero(valor):
    """`sum(bigint)` llega como `Decimal`; los conteos del dominio son `int`."""
    return None if valor is None else int(valor)


def _medida(info: dict | None, hay_ingesta: bool) -> dict:
    """Metricas de UNA ubicacion. Sin fila e ingesta presente = ceros medidos;
    sin fila y sin ingesta = None (regla 3)."""
    if info is None:
        if not hay_ingesta:
            return {"clics": None, "gasto": None, "pedidos": None, "venta": None}
        return {"clics": 0, "gasto": Decimal(0), "pedidos": 0, "venta": Decimal(0)}
    return {
        "clics": _entero(info["clics"]),
        "gasto": info["gasto"],
        "pedidos": _entero(info["pedidos"]),
        "venta": info["venta"],
    }


def _cubre_ingesta(dias: frozenset, desde: dt.date, hasta: dt.date) -> bool:
    return all(desde + dt.timedelta(days=i) in dias for i in range((hasta - desde).days + 1))


def _gasto_campana(conn, campana: int, desde: dt.date, hasta: dt.date) -> Decimal | None:
    return conn.execute(_SQL_GASTO_CAMPANA, (campana, desde, hasta)).fetchone()[0]


def _banda_de_metas(
    metas_de_campana: Iterable[tuple[Decimal, str]],
) -> MetaCampana | None:
    pares = [(meta, proc) for meta, proc in metas_de_campana]
    if not pares:
        return None
    valores = [meta for meta, _ in pares]
    procedencias = tuple(sorted({proc for _, proc in pares}))
    return MetaCampana(
        exigente_pct=min(valores),
        laxa_pct=max(valores),
        procedencias=procedencias,
    )


def _dias_topados(
    conn, plataforma: str, decided_at: dt.datetime, settings: Mapping
) -> tuple[CampanaTopada, dict[int, int | None]]:
    """Dias al tope de los ultimos 7 dias con ingesta hasta ayer UTC, con el
    lector unico `presupuestos_campanas_hoy` (mismo criterio que la pantalla y
    los avisos). Sin dias medidos o sin presupuesto conocido devuelve None."""
    topada = temporada.campana_topada_desde_settings(settings)
    presupuestos = presupuestos_campanas_hoy(conn, plataforma, _fecha_utc(decided_at), topada)
    # Mismo criterio que recuperacion.py: sin presupuesto, sin conteo o con
    # menos dias medidos que el corte, el resultado es "sin medir" (None).
    return topada, {
        cid: (
            None
            if info.presupuesto_diario is None
            or info.dias_topados is None
            or info.dias_medidos < info.topada.dias
            else info.dias_topados
        )
        for cid, info in presupuestos.items()
    }


def lee_casos(
    conn: psycopg.Connection,
    platform: str,
    decided_at: dt.datetime,
    *,
    settings: Mapping,
    goals: Mapping[int, g.Goal],
    metas_hoja: Mapping[int, tuple[Decimal, str]],
    goal_plataforma: g.Goal | None = None,
    economia,
    fase_temporada: str | None,
) -> tuple[CasoCampana, ...]:
    """Construye el `CasoCampana` congelado de cada campana activa del mercado.

    Devuelve tupla vacia si `ads_ubicacion_<platform>` esta apagado (fail-closed).
    Solo entran campanas `ENABLED` con config vigente y goal resuelto.
    """
    if modo_ubicacion_desde_settings(settings, platform) == "off":
        return ()
    abiertos = {f[0] for f in conn.execute(_SQL_IMPULSOS_ABIERTOS, (platform,))}
    en_vuelo = campanas_con_ajuste_en_vuelo(conn, platform)
    hoy = _fecha_utc(decided_at)
    topada, dias_topados_por_campana = _dias_topados(conn, platform, decided_at, settings)
    cubre_inmaduros = _cubre_ingesta(
        _dias_con_placements(
            conn, platform, hoy - dt.timedelta(days=2), hoy - dt.timedelta(days=1)
        ),
        hoy - dt.timedelta(days=2),
        hoy - dt.timedelta(days=1),
    )
    solo_bajadas = solo_bajadas_desde_settings(settings, platform)
    metas_por_campana: dict[int, list[tuple[Decimal, str]]] = {}
    for hoja_id, campana_id in conn.execute(_SQL_HOJAS, (platform,)).fetchall():
        if hoja_id in metas_hoja:
            metas_por_campana.setdefault(campana_id, []).append(metas_hoja[hoja_id])

    casos: list[CasoCampana] = []
    for campana, config_id, estrategia, top, resto, producto in conn.execute(
        _SQL_CONFIGS_ACTIVAS, (platform,)
    ).fetchall():
        elegible = g.elegible(g.resuelve_goal(goals.get(campana), goal_plataforma))
        ultimo = _ultimo_cambio(conn, campana)
        desde, hasta = _ventana(ultimo, decided_at)
        placements = _placements(conn, campana, platform, desde, hasta)
        inmaduros = _inmaduros(conn, campana, platform, decided_at, cubiertos=cubre_inmaduros)
        vigentes = {
            "arriba_de_busqueda": top or 0,
            "resto_de_busqueda": resto or 0,
            "paginas_de_producto": producto or 0,
        }
        hay_ingesta = bool(placements)
        ubicaciones = tuple(
            EvidenciaUbicacion(
                ubicacion=ubicacion,
                ajuste_vigente_pct=vigentes[ubicacion],
                **_medida(placements.get(ubicacion), hay_ingesta),
                pedidos_inmaduros=(None if not cubre_inmaduros else inmaduros.get(ubicacion, 0)),
            )
            for ubicacion in UBICACIONES
        )
        # El reporte por placement solo trae filas con actividad: sumar las
        # presentes y dejar que la conciliacion contra el gasto de campana
        # delate un hueco real (regla 10).
        total = None
        if placements and all(info["gasto"] is not None for info in placements.values()):
            total = sum((info["gasto"] for info in placements.values()), Decimal(0))
        fresco = (
            bool(placements)
            and max(
                info["max_fecha"] for info in placements.values() if info["max_fecha"] is not None
            )
            >= hasta
        )
        casos.append(
            CasoCampana(
                campana_id=campana,
                plataforma=platform,
                config_id=config_id,
                estrategia=estrategia,
                ventana_desde=desde,
                ventana_hasta=hasta,
                ubicaciones=ubicaciones,
                meta=_banda_de_metas(metas_por_campana.get(campana, ())),
                economia=economia,
                gasto_campana=_gasto_campana(conn, campana, desde, hasta),
                gasto_placements_total=total,
                placements_frescos=fresco,
                dias_desde_ultimo_cambio=None if ultimo is None else (hoy - ultimo).days,
                ajuste_en_vuelo=campana in en_vuelo,
                elegible=elegible,
                en_impulso=campana in abiertos,
                solo_bajadas=solo_bajadas,
                dias_topados_7d=dias_topados_por_campana.get(campana),
                topada=topada,
                fase_temporada=fase_temporada,
            )
        )
    return tuple(casos)


def guarda_evaluaciones(
    conn: psycopg.Connection,
    *,
    cycle_id: int,
    config_version_id: int,
    platform: str,
    decided_at: dt.datetime,
    evaluaciones: Sequence[tuple[CasoCampana, str, VeredictoUbicacion]],
) -> dict[int, int]:
    """Inserta una fila por campana y devuelve `campana_id -> evaluacion_id`."""
    ids: dict[int, int] = {}
    for caso, modo, veredicto in evaluaciones:
        if isinstance(veredicto, MoverUbicacion):
            ubicacion, de_pct, a_pct = veredicto.ubicacion, veredicto.de_pct, veredicto.a_pct
        else:
            ubicacion = de_pct = a_pct = None
        fila = conn.execute(
            """
            INSERT INTO ubicacion_evaluacion (
                cycle_id, campana_id, platform, modo, politica, config_version_id,
                antes_config_id, decided_at, ventana_desde, ventana_hasta, moneda,
                entrada, veredicto, motivo, ubicacion, de_pct, a_pct)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                cycle_id,
                caso.campana_id,
                platform,
                modo,
                POLITICA,
                config_version_id,
                caso.config_id,
                decided_at,
                caso.ventana_desde,
                caso.ventana_hasta,
                caso.economia.moneda,
                Json(caso_a_json(caso)),
                "mover" if isinstance(veredicto, MoverUbicacion) else "mantener",
                veredicto.motivo,
                ubicacion,
                de_pct,
                a_pct,
            ),
        ).fetchone()
        ids[caso.campana_id] = fila[0]
    return ids


def marca_producto_sin_evidencia(
    evaluaciones: Sequence[tuple[CasoCampana, str, VeredictoUbicacion]],
    campanas: Iterable[int],
) -> tuple[tuple[CasoCampana, str, VeredictoUbicacion], ...]:
    """Reemplaza por `mantener(producto_sin_evidencia)` las campanas que
    perdieron su meta al guardar `evaluacion_producto_ciclo` en TX3."""
    sin_meta = frozenset(campanas)
    if not sin_meta:
        return tuple(evaluaciones)
    return tuple(
        (caso, modo, MantenerUbicacion(MOTIVO_PRODUCTO_SIN_EVIDENCIA))
        if caso.campana_id in sin_meta
        else (caso, modo, veredicto)
        for caso, modo, veredicto in evaluaciones
    )


def _margen_para_orden(caso: CasoCampana, veredicto: MoverUbicacion) -> Decimal:
    """Magnitud con la que la politica ordeno el movimiento (exceso u holgura)."""
    ev = next((e for e in caso.ubicaciones if e.ubicacion == veredicto.ubicacion), None)
    if ev is None or ev.gasto is None or ev.venta is None or caso.meta is None:
        return Decimal(0)
    if veredicto.a_pct < veredicto.de_pct:
        return ev.gasto - (caso.meta.laxa_pct / _CIEN) * ev.venta
    return (caso.meta.exigente_pct / _CIEN) * ev.venta - ev.gasto


def evaluaciones_a_aplicar(
    conn: psycopg.Connection, cycle_id: int, platform: str
) -> tuple[tuple[int, CasoCampana, MoverUbicacion], ...]:
    """Movimientos `live` del ciclo pendientes, en el orden determinista de la
    seccion 7: recortes antes que subidas y, dentro de cada grupo, por magnitud
    descendente y `campana_id`."""
    filas = conn.execute(
        """
        SELECT ue.id, ue.entrada, ue.ubicacion, ue.de_pct, ue.a_pct, ue.motivo,
               ue.veredicto
          FROM ubicacion_evaluacion ue
         WHERE ue.cycle_id = %s AND ue.platform = %s
           AND ue.modo = 'live' AND ue.veredicto = 'mover'
           AND NOT EXISTS (SELECT 1 FROM campana_ajuste a
                            WHERE a.evaluacion_id = ue.id)
           AND NOT EXISTS (SELECT 1 FROM ubicacion_sin_aplicar s
                            WHERE s.evaluacion_id = ue.id)
        """,
        (cycle_id, platform),
    ).fetchall()
    pendientes = []
    for eval_id, entrada, ubicacion, de_pct, a_pct, motivo, _veredicto in filas:
        caso = caso_desde_json(entrada)
        veredicto = MoverUbicacion(ubicacion=ubicacion, de_pct=de_pct, a_pct=a_pct, motivo=motivo)
        pendientes.append((eval_id, caso, veredicto))
    pendientes.sort(
        key=lambda p: (
            0 if p[2].a_pct < p[2].de_pct else 1,
            -_margen_para_orden(p[1], p[2]),
            p[1].campana_id,
        )
    )
    return tuple(pendientes)


def ultimas_evaluaciones_por_campana(
    conn: psycopg.Connection, platform: str
) -> dict[int, SugerenciaUbicacion]:
    """La evaluacion mas reciente por campana, solo si sigue vigente.

    Una sugerencia deja de mostrarse cuando el `antes_config_id` juzgado ya no
    es la configuracion vigente (el dueño cambio algo) o cuando la evaluacion ya
    tiene un ajuste asociado (aplicado o pendiente).
    """
    filas = conn.execute(
        """
        WITH ultima AS (
            SELECT DISTINCT ON (campana_id) *
              FROM ubicacion_evaluacion
             WHERE platform = %s
             ORDER BY campana_id, id DESC
        )
        SELECT u.campana_id, u.id, u.modo, u.ubicacion, u.de_pct, u.a_pct,
               u.motivo, u.decided_at
          FROM ultima u
          JOIN v_campana_config_vigente v
            ON v.ad_entity_id = u.campana_id AND v.id = u.antes_config_id
         WHERE u.veredicto = 'mover'
           AND NOT EXISTS (SELECT 1 FROM campana_ajuste a
                            WHERE a.evaluacion_id = u.id)
        """,
        (platform,),
    ).fetchall()
    return {
        fila[0]: SugerenciaUbicacion(
            campana_id=fila[0],
            evaluacion_id=fila[1],
            modo=fila[2],
            ubicacion=fila[3],
            de_pct=fila[4],
            a_pct=fila[5],
            motivo=fila[6],
            decided_at=fila[7],
        )
        for fila in filas
    }


def resumen_ubicaciones_ciclo(conn: psycopg.Connection, cycle_id: int) -> dict:
    """Conteos del ciclo para **Salud**: evaluadas, mover, en practica y aplicadas."""
    fila = conn.execute(
        """
        SELECT count(*),
               count(*) FILTER (WHERE veredicto = 'mover'),
               count(*) FILTER (WHERE veredicto = 'mover' AND modo = 'shadow'),
               count(*) FILTER (
                   WHERE veredicto = 'mover' AND modo = 'live'
                     AND EXISTS (SELECT 1 FROM campana_ajuste a
                                  WHERE a.evaluacion_id = ubicacion_evaluacion.id
                                    AND a.confirmado_el IS NOT NULL))
          FROM ubicacion_evaluacion
         WHERE cycle_id = %s
        """,
        (cycle_id,),
    ).fetchone()
    return {
        "evaluadas": fila[0],
        "mover": fila[1],
        "en_practica": fila[2],
        "aplicadas": fila[3],
    }
