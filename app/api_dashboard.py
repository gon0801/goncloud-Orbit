"""Router del dashboard del optimizador (ORBIT 16 - DASHBOARD 01).

Endpoints GET de LECTURA bajo `/api/dashboard` (rol `orbit_read` via
ORBIT_DSN_READ; sin DSN -> 503 fail-closed, misma dependencia que api.py).
Solo GET en toda la superficie (candado de introspeccion OpenAPI).

La logica de lectura pesada se distribuye en modulos hoja cohesivos:
- `app/dashboard_resumen.py` (+ `app/dashboard_resumen_calc.py`): Resumen multicanal.
- `app/dashboard_salud.py`: bloque `/salud` y sus helpers.
- `app/dashboard_cortes.py`: `/cortes`, `/gasto-sin-venta` e `/inertes`.
- `app/dashboard_temporada.py`: equilibrio, metas vigentes y estado de baja.
- `app/api_precios.py`: `/precios` y bloque de precios en `/salud`.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query

from app.api import KINDS_DECISION, ConexionLectura
from app.api_common import _dec_str, _dos_dec
from app.api_common import target_margen_del_ciclo as _target_margen_del_ciclo
from app.api_precios import (  # noqa: F401 - reexport que usan /salud y pruebas
    _TOPE_RACHA_DIVERGENTE,
    _acciones_precio,
    _dinero_2,
    _precios_de,
    precios,
    validar_precio_aviso_dias,
)
from app.apply_cuota import KINDS_QUOTA
from app.config_write import ADVERTENCIA_RESPALDO, clave_cap, metas_resumen_desde_settings
from app.dashboard_contribucion import contribucion_campanas as _contribucion_campanas
from app.dashboard_cortes import (  # noqa: F401 - reexport contrato pruebas
    DIRECCION_POR_KIND,
    _banda_honesta,
    _bloqueos_jev,
    _calculo_viejo,
    _clave_texto,
    _destino_harvest,
    _indicador_harvest,
    _motivo_sin_lectura,
    _proporcion_de,
    _senal_presentada,
    _senal_presentada_vista,
    leer_cortes,
    leer_gasto_sin_venta,
    leer_inertes,
)
from app.dashboard_pagina import (  # noqa: F401 - reexport para UI y pruebas
    _CAMPANA_ANCESTRO,
    _CAMPOS_BUSQUEDA_DECISIONES,
    _JOINS_ANCESTROS,
    _NOMBRE_SIN_SIGNOS,
    _SQL_DECISIONES_CONTEOS,
    _SQL_DECISIONES_FEED,
    _SQL_DECISIONES_PAGINA,
    _SQL_DECISIONES_TOTAL_FILTRADO,
    FASES_ES_HARVEST,
    TIPOS_DECISION,
    PageWindow,
    _fila_decision,
    _fila_decision_pagina,
    _filtros_decisiones,
    _filtros_feed,
    _normaliza_busqueda,
    _pares_busqueda_decisiones,
    decisiones_conteos,
    decisiones_pagina,
)
from app.dashboard_resumen import CANALES_POR_DEFECTO as CANALES_RESUMEN_POR_DEFECTO
from app.dashboard_resumen import (
    DIAS_PERIODO_DEFAULT,
    acos_texto,
    hoy_resumen,
    ventana_resumen,
)
from app.dashboard_resumen import resumen as resumen_multicanal
from app.dashboard_salud import (  # noqa: F401 - reexport contrato pruebas
    _SQL_HISTORICO_14D,
    _ads_ingest_de,
    _avisos_campana_de,
    _avisos_propuesta_de,
    _fila_historico,
    _harvest_destino_de,
    _jev_de,
    _motivo_ciclo,
    _quota_de,
    _skips_de,
    _skips_traducidos,
    _spapi_de,
    leer_salud,
    motivo_es,
)
from app.dashboard_temporada import (  # noqa: F401 - reexport contrato pruebas
    _SQL_CONFIG_VIGENTE,
    _SQL_EQUILIBRIO_TEMPORADA,
    _SQL_ESTADO_BAJA_DECISIONES,
    _SQL_ESTADO_BAJA_KEYWORDS,
    _SQL_GOALS_SETTINGS,
    _SQL_PAUSADAS_DE_BAJA,
    COLUMNAS_EQUILIBRIO_TEMPORADA,
    RAZON_SIN_EQUILIBRIO,
    _config_vigente_con_id,
    _equilibrio_de_fila,
    _goal_desde_fila,
    _goal_plataforma_de,
    _numero_corto,
    _pct_un_decimal,
    equilibrio_temporada,
    estado_de_baja,
    metas_vigentes_temporada,
    pausadas_de_baja,
    resumen_temporada_para_guardado,
    temporada_formulario,
)
from app.etiquetas import ETIQUETAS
from app.optimizer import goals as g
from app.optimizer import temporada
from app.optimizer.bid import PLATAFORMAS_MONEDA
from app.redaction import install_scrub_filter
from app.spapi.salud import bloque_salud

logger = logging.getLogger(__name__)
install_scrub_filter(logger)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

# Ventana FIJA del resumen de campanas (brief §3.3): [D-30, D-1] con el hoy del Resumen.
DIAS_VENTANA_CAMPANAS = 30

# Paginacion del feed por CURSOR: tope duro de 200 filas por pagina.
LIMITE_FEED_MAX = 200
LIMITE_FEED_DEFAULT = 50

_SQL_CAMPANAS_30D = """
SELECT e.id, e.name, e.platform, s.acos_target, s.status,
       CASE WHEN bool_and(v.cost IS NOT NULL) THEN sum(v.cost) END,
       CASE WHEN bool_and(v.ad_revenue IS NOT NULL) THEN sum(v.ad_revenue) END,
       CASE WHEN bool_and(v.clicks IS NOT NULL) THEN sum(v.clicks)::bigint END
  FROM v_metric_latest v
  JOIN ad_entity e ON e.id = v.ad_entity_id
  LEFT JOIN ad_entity_state s ON s.ad_entity_id = e.id
 WHERE e.kind = 'campaign'
   AND e.platform = %s::platform
   AND v.metric_date BETWEEN %s AND %s
 GROUP BY e.id, e.name, e.platform, s.acos_target, s.status
 ORDER BY e.id
"""

_SQL_GOALS_DASHBOARD = """
SELECT scope, ad_entity_id, platform, target_acos_pct, bid_floor, bid_ceiling,
       bid_currency, harvest_campaign_id, harvest_ad_group_id,
       harvest_default_bid, enabled, mode
  FROM ads_optimizer_goal
"""

_SQL_CONGELADO_CAMPANAS = """
SELECT camp.id AS campana_id, t.target_acos_pct, t.procedencia, t.cycle_id, t.decided_at
  FROM target_acos_ciclo t
  JOIN optimizer_cycle c ON c.id = t.cycle_id
  JOIN ad_entity k ON k.id = t.ad_entity_id
  JOIN ad_entity ag ON ag.id = k.parent_id AND ag.kind = 'ad_group'
  JOIN ad_entity camp ON camp.id = ag.parent_id AND camp.kind = 'campaign'
 WHERE c.platform = %s::platform AND c.mode = 'live' AND c.status = 'done'
   AND c.id = (SELECT max(c2.id) FROM optimizer_cycle c2
                WHERE c2.platform = c.platform AND c2.motor = 'ads_optimizer'
                  AND c2.mode = 'live' AND c2.status = 'done')
"""

_SQL_CHROME_COLA = """
SELECT modo::text, count(*)
  FROM apply_queue
 WHERE estado IN ('pending_veto', 'released')
 GROUP BY modo
"""

_SQL_CHROME_WATERMARK = """
SELECT e.platform::text, MAX(m.metric_date)
  FROM ads_metric_observation m
  JOIN ad_entity e ON e.id = m.ad_entity_id
 GROUP BY e.platform
"""

_SQL_CHROME_ULTIMO_CICLO = """
SELECT DISTINCT ON (platform)
       platform::text, mode::text, status::text, started_at, decisions_count
  FROM optimizer_cycle
 WHERE platform IS NOT NULL AND motor = 'ads_optimizer'
 ORDER BY platform, id DESC
"""

PISA_POR_PELDANO: dict[str, str] = {p: ETIQUETAS["pisa"][p] for p in g.PELDANOS_CASCADA}


def _ahora_utc() -> dt.datetime:
    """Instante actual del reloj de las pantallas (las pruebas lo inyectan)."""
    return dt.datetime.now(dt.UTC)


def _hoy_pantalla(*plataformas: str) -> dt.date:
    """Hoy de las pantallas: el del Resumen (hoy_resumen), fecha local minima."""
    return hoy_resumen(plataformas, _ahora_utc())


def _ventana_campanas(plataformas: Sequence[str], instante: dt.datetime) -> tuple[dt.date, dt.date]:
    """[D-30, D-1] con el hoy del Resumen."""
    hoy = hoy_resumen(plataformas, instante)
    return hoy - dt.timedelta(days=DIAS_VENTANA_CAMPANAS), hoy - dt.timedelta(days=1)


def _mercado_o_422(plataforma: str | None) -> str:
    """Mercado Amazon valido o 422 (default amazon_mx)."""
    mercado = plataforma or "amazon_mx"
    if mercado not in PLATAFORMAS_MONEDA:
        raise HTTPException(status_code=422, detail="plataforma fuera de vocabulario")
    return mercado


def _acoso(cost: Decimal | None, ad_revenue: Decimal | None) -> tuple[str | None, bool]:
    """(acos_str, sin_ventas) del dia; delega el ratio en dashboard_resumen."""
    sin_ventas = ad_revenue is not None and ad_revenue == 0
    return acos_texto(cost, ad_revenue), sin_ventas


@router.get("/resumen")
def resumen_ads(
    conn: ConexionLectura,
    periodo: int = DIAS_PERIODO_DEFAULT,
    desde: dt.date | None = None,
    hasta: dt.date | None = None,
    canal: Literal["amazon_mx", "amazon_us", "meli"] | None = None,
) -> dict:
    """Resumen multicanal (RESUMEN-ADS-02): valida parametros y delega."""
    canales = (canal,) if canal is not None else CANALES_RESUMEN_POR_DEFECTO
    instante = _ahora_utc()
    hoy = hoy_resumen(canales, instante)
    try:
        ventana = ventana_resumen(periodo=periodo, desde=desde, hasta=hasta, hoy=hoy)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    salida = resumen_multicanal(conn, ventana=ventana, canales=canales, hoy=hoy, instante=instante)
    salida["metas_acos"] = metas_resumen_desde_settings(_config_vigente_con_id(conn)[1])
    return salida


def _carga_goals(conn) -> tuple[dict[int, g.Goal], dict[str, g.Goal]]:
    """(goals de campana por ad_entity_id, goals de plataforma por platform)."""
    goals_campana: dict[int, g.Goal] = {}
    goals_plataforma: dict[str, g.Goal] = {}
    for fila in conn.execute(_SQL_GOALS_DASHBOARD).fetchall():
        goal = _goal_desde_fila(fila)
        if goal.scope == "campaign" and goal.ad_entity_id is not None:
            goals_campana[goal.ad_entity_id] = goal
        elif goal.scope == "platform" and goal.platform is not None:
            goals_plataforma[goal.platform] = goal
    return goals_campana, goals_plataforma


def _goal_estado(goal: g.Goal | None) -> dict | None:
    """Estado VIVO del goal resuelto."""
    if goal is None:
        return None
    floor, ceiling = g.resuelve_floor_ceiling(goal, goal.bid_currency)
    return {
        "enabled": goal.enabled,
        "floor": _dec_str(floor),
        "ceiling": _dec_str(ceiling),
        "mode": goal.mode,
        "scope": goal.scope,
    }


@dataclass(frozen=True)
class CongeladoCampana:
    """Lo que el motor APLICO a las hojas de UNA campana (A7, dato puro)."""

    minimo: Decimal
    maximo: Decimal
    procedencias: frozenset[str]
    ciclo_ids: frozenset[int]
    decideds: frozenset[dt.datetime]

    def display(self) -> dict:
        unico = _dos_dec(self.minimo) == _dos_dec(self.maximo)
        decideds_utc = sorted(d.astimezone(dt.UTC).isoformat() for d in self.decideds)
        return {
            "valor": (
                _dos_dec(self.minimo)
                if unico
                else f"{_dos_dec(self.minimo)}–{_dos_dec(self.maximo)}"
            ),
            "minimo": _dos_dec(self.minimo),
            "maximo": _dos_dec(self.maximo),
            "peldano": next(iter(self.procedencias)) if len(self.procedencias) == 1 else "mixto",
            "ciclo": {
                "id_min": min(self.ciclo_ids),
                "id_max": max(self.ciclo_ids),
                "decided_min": decideds_utc[0],
                "decided_max": decideds_utc[-1],
            },
        }


def _congelados_por_campana(conn, plataforma: str) -> dict[int, CongeladoCampana]:
    """Congelado por campana (A7r2): hojas del ULTIMO ciclo live+done."""
    por_campana: dict[int, list] = {}
    for fila in conn.execute(_SQL_CONGELADO_CAMPANAS, (plataforma,)).fetchall():
        por_campana.setdefault(fila[0], []).append(fila[1:])
    congelados = {}
    for campana_id, filas in por_campana.items():
        targets = [fila[0] for fila in filas]
        congelados[campana_id] = CongeladoCampana(
            minimo=min(targets),
            maximo=max(targets),
            procedencias=frozenset(fila[1] for fila in filas),
            ciclo_ids=frozenset(fila[2] for fila in filas),
            decideds=frozenset(fila[3] for fila in filas),
        )
    return congelados


def _fila_campana(
    fila,
    goals_campana: dict[int, g.Goal],
    goal_plataforma: g.Goal | None,
    plataforma: str,
    congelado: CongeladoCampana | None,
) -> dict:
    camp_id, nombre, _plataforma, _acos_cache, estado, cost, revenue, clicks = fila
    goal_campana = goals_campana.get(camp_id)
    acos, sin_ventas = _acoso(cost, revenue)
    return {
        "ad_entity_id": camp_id,
        "nombre": nombre,
        "status": estado,
        "plataforma": plataforma,
        "moneda": PLATAFORMAS_MONEDA[plataforma],
        "metricas_30d": {
            "cost": _dec_str(cost),
            "ad_revenue": _dec_str(revenue),
            "clicks": clicks,
            "acos": acos,
            "sin_ventas": sin_ventas,
            "inmaduro": True,
        },
        "target_efectivo": congelado.display() if congelado is not None else None,
        "goal": _goal_estado(g.resuelve_goal(goal_campana, goal_plataforma)),
    }


@router.get("/campanas")
def campanas(
    conn: ConexionLectura,
    platform: Literal["amazon_us", "amazon_mx"] | None = None,
) -> dict:
    """Resumen de campanas 30d + target efectivo congelado + estado del goal."""
    plataformas = (platform,) if platform is not None else tuple(PLATAFORMAS_MONEDA)
    desde, hasta = _ventana_campanas(plataformas, _ahora_utc())
    goals_campana, goals_plataforma = _carga_goals(conn)
    items: list[dict] = []
    for plataforma in plataformas:
        congelados = _congelados_por_campana(conn, plataforma)
        for fila in conn.execute(_SQL_CAMPANAS_30D, (plataforma, desde, hasta)).fetchall():
            items.append(
                _fila_campana(
                    fila,
                    goals_campana,
                    goals_plataforma.get(plataforma),
                    plataforma,
                    congelados.get(fila[0]),
                )
            )
    return {"items": items}


@router.get("/decisiones")
def decisiones(
    conn: ConexionLectura,
    cursor: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=LIMITE_FEED_MAX)] = LIMITE_FEED_DEFAULT,
    platform: Literal["amazon_us", "amazon_mx"] | None = None,
    kind: KINDS_DECISION | None = None,
) -> dict:
    """Feed de decisiones por CURSOR (`id < cursor`, ORDER BY id DESC)."""
    clausulas, params = _filtros_feed(platform, kind)
    if cursor is not None:
        clausulas.append("d.id < %s")
        params.append(cursor)
    params.append(limit + 1)
    filas = conn.execute(
        _SQL_DECISIONES_FEED.format(filtros=" AND ".join(clausulas) or "true"),
        params,
    ).fetchall()
    hay_mas = len(filas) > limit
    items = [_fila_decision(fila) for fila in filas[:limit]]
    return {
        "items": items,
        "next_cursor": items[-1]["id"] if hay_mas and items else None,
        "has_more": hay_mas,
    }


@router.get("/salud")
def salud(conn: ConexionLectura) -> dict:
    """Salud por plataforma + historico 14d + skips + JEV."""
    return leer_salud(conn, hoy_pantalla_fn=_hoy_pantalla, spapi_salud_fn=bloque_salud)


@router.get("/contribucion")
def contribucion_campanas(conn: ConexionLectura) -> dict:
    """Contribucion pre-cargos por campana (ORBIT 06 1.4, docs/MARGEN-ENTIDAD.md)."""
    return _contribucion_campanas(conn)


@router.get("/cortes")
def cortes(conn: ConexionLectura) -> dict:
    """Cortes pendientes de veto + propuestas de campana + bloqueos JEV."""
    return leer_cortes(conn)


@router.get("/gasto-sin-venta")
def gasto_sin_venta(conn: ConexionLectura, plataforma: str | None = None) -> dict:
    """Busquedas que gastan y no venden, por lectura (JEV ADS 02, S.5 J5b)."""
    return leer_gasto_sin_venta(conn, _mercado_o_422(plataforma))


@router.get("/inertes")
def inertes(conn: ConexionLectura) -> dict:
    """Hojas sin trafico con su clasificacion y lotes de archivado."""
    return leer_inertes(conn)


def _goal_editable(goal_id: int, goal: g.Goal, plataforma, nombre, pisa_a) -> dict:
    """Goal con id y estado VIVO (_goal_estado) para el form de /settings."""
    return {
        "id": goal_id,
        "plataforma": plataforma,
        "ad_entity_id": goal.ad_entity_id,
        "nombre": nombre,
        "target": _dec_str(goal.target_acos_pct),
        "moneda": goal.bid_currency,
        "pisa_a": pisa_a,
        **_goal_estado(goal),
    }


@router.get("/settings")
def settings(conn: ConexionLectura) -> dict:
    """Lectura completa de la pantalla /settings."""
    config_id, settings_actual = _config_vigente_con_id(conn)
    goals = [
        (fila[0], _goal_desde_fila(fila[1:13]), fila[13], fila[14])
        for fila in conn.execute(_SQL_GOALS_SETTINGS).fetchall()
    ]
    goal_plataforma = {
        goal.platform: (goal_id, goal) for goal_id, goal, _, _ in goals if goal.scope == "platform"
    }
    plataformas: list[dict] = []
    peldanos: dict[str, str | None] = {}
    hoy_temporada = dt.datetime.now(dt.UTC).date()
    config_temporada = temporada.desde_settings(settings_actual)
    meta_alta_form = {
        plataforma: temporada.meta_alta_desde_settings(settings_actual, plataforma)
        for plataforma in temporada.PLATAFORMAS_TEMPORADA
    }
    for plataforma in PLATAFORMAS_MONEDA:
        goal_id, goal = goal_plataforma.get(plataforma, (None, None))
        valor, peldano = g.cascada_target_acos_con_procedencia(
            None,
            goal,
            settings_actual,
            None,
            plataforma,
            _target_margen_del_ciclo(conn, plataforma),
        )
        peldanos[plataforma] = peldano
        fraccion = g.fraccion_desde_settings(settings_actual, plataforma)
        caps = {kind: settings_actual.get(clave_cap(plataforma, kind)) for kind in KINDS_QUOTA}
        plataformas.append(
            {
                "plataforma": plataforma,
                "moneda": PLATAFORMAS_MONEDA[plataforma],
                "target_vigente": {"valor": _dec_str(valor), "peldano": peldano},
                "target_manual_pct": _dec_str(
                    settings_actual.get(g.clave_target_plataforma(plataforma))
                ),
                "margen_habilitado": fraccion is not None,
                "fraccion_margen": _dec_str(fraccion),
                "confianza_recorte": _dec_str(
                    g.confianza_recorte_desde_settings(settings_actual, plataforma)
                ),
                "confianza_subida": _dec_str(
                    g.confianza_subida_desde_settings(settings_actual, plataforma)
                ),
                "motor_bid": settings_actual.get(g.clave_bid_politica(plataforma)),
                "motor_bid_vive": (
                    g.politica_bid_desde_settings(settings_actual, plataforma)
                    == g.POLITICA_BID_VIGENTE
                ),
                "caps": {kind: _dec_str(cap) for kind, cap in caps.items()},
                "goal": (_goal_editable(goal_id, goal, plataforma, None, None) if goal else None),
                "meta_alta": _numero_corto(meta_alta_form[plataforma])
                if plataforma in meta_alta_form
                else None,
                "modo_temporada": settings_actual.get(f"ads_temporada_{plataforma}"),
                "recuperacion_modo": settings_actual.get(f"ads_bid_recuperacion_{plataforma}"),
                "recuperacion_memoria_dias": temporada.memoria_recuperacion_desde_settings(
                    settings_actual, plataforma
                ),
                "campanas_con_meta_propia": sum(
                    1
                    for _gid, goal_c, plataforma_c, _nombre in goals
                    if goal_c.scope == "campaign" and plataforma_c == plataforma
                ),
                "temporada": equilibrio_temporada(
                    conn,
                    plataforma,
                    meta_hoy=valor,
                    peldano=peldano,
                    meta_alta=meta_alta_form.get(plataforma),
                    hoy=hoy_temporada,
                ),
                "temporada_estado": (
                    estado_de_baja(conn, plataforma, config_temporada, hoy_temporada)
                    if config_temporada is not None
                    and settings_actual.get(f"ads_temporada_{plataforma}") in temporada.MODOS_MOTOR
                    else None
                ),
            }
        )
    estado_temporada = None
    if config_temporada is not None and any(
        settings_actual.get(f"ads_temporada_{p}") in temporada.MODOS_MOTOR
        for p in temporada.PLATAFORMAS_TEMPORADA
    ):
        estacion_hoy = temporada.resuelve(config_temporada, hoy_temporada)
        estado_temporada = {
            "fase": estacion_hoy.fase,
            "desde": (
                estacion_hoy.inicio_bloque.isoformat() if estacion_hoy.fase == "baja" else None
            ),
        }
    topada_vigente = temporada.campana_topada_desde_settings(settings_actual)
    return {
        "config_version_id": config_id,
        "modo_global": settings_actual.get(g.CLAVE_SETTING_MODO),
        "advertencia_respaldo": ADVERTENCIA_RESPALDO,
        "temporada_form": temporada_formulario(settings_actual),
        "campana_topada": {
            "pct": _numero_corto(topada_vigente.pct * 100),
            "dias": topada_vigente.dias,
        },
        "temporada_estado": estado_temporada,
        "precio_modo_global": settings_actual.get("precio_modo_global"),
        "precio_modo_universo": settings_actual.get("precio_modo_universo") or {},
        "precio_universos": [
            "amazon_mx/fba",
            "amazon_mx/fbm",
            "amazon_us/fbm",
            "meli/meli",
        ],
        "resumen_acos_objetivo": metas_resumen_desde_settings(settings_actual),
        "resumen_canales": list(CANALES_RESUMEN_POR_DEFECTO),
        "plataformas": plataformas,
        "goals": [
            _goal_editable(
                goal_id,
                goal,
                goal.platform or plataforma_campana,
                nombre,
                PISA_POR_PELDANO[peldanos[plataforma_campana]]
                if (
                    goal.scope == "campaign"
                    and plataforma_campana in peldanos
                    and peldanos[plataforma_campana] is not None
                )
                else None,
            )
            for goal_id, goal, plataforma_campana, nombre in goals
        ],
    }


@router.get("/keywords-danadas")
def keywords_danadas(conn: ConexionLectura, plataforma: str | None = None) -> dict:
    """Keywords danadas del mercado (BIDS 02, P.3b)."""
    from app import pantalla_danadas

    return pantalla_danadas.lee_danadas(conn, plataforma=_mercado_o_422(plataforma)).como_dict()


@router.get("/ruido")
def ruido(conn: ConexionLectura, plataforma: str | None = None) -> dict:
    """Tablero de ruido del mercado (BIDS 02, P.5)."""
    from app import pantalla_ruido

    mercado = _mercado_o_422(plataforma)
    return pantalla_ruido.lee_ruido(
        conn, plataforma=mercado, hoy=_hoy_pantalla(mercado)
    ).como_dict()


@router.get("/donde-poner-el-dinero")
def donde_poner_el_dinero(conn: ConexionLectura, plataforma: str | None = None) -> dict:
    """Donde poner el dinero del mercado (BIDS 02, P.1)."""
    from app import pantalla_dinero

    mercado = _mercado_o_422(plataforma)
    return pantalla_dinero.lee_dinero(
        conn, plataforma=mercado, hasta=_hoy_pantalla(mercado) - dt.timedelta(days=1)
    ).como_dict()


@router.get("/productos-sin-clics")
def productos_sin_clics(conn: ConexionLectura, plataforma: str | None = None) -> dict:
    """Productos que venden y casi no reciben clics (BIDS 02, P.4)."""
    from app import fabrica_web, pantalla_decisiones_producto, pantalla_productos

    mercado = _mercado_o_422(plataforma)
    base = pantalla_productos.lee_productos(
        conn, plataforma=mercado, hoy=_hoy_pantalla(mercado)
    ).como_dict()
    for impulso in base["impulsos"]:
        impulso["frase_fuera_de_amazon"] = pantalla_productos.frase_fuera_de_amazon(
            impulso["clics_fuera_de_amazon"], impulso["clics"]
        )
    decisiones_prod = pantalla_decisiones_producto.lee_decisiones_producto(
        conn, mercado
    ).como_dict()
    subfamilias = fabrica_web.tipos_producto(conn, mercado)
    return {
        "plataforma": mercado,
        **base,
        "decisiones_producto": decisiones_prod,
        "subfamilias": subfamilias,
    }


@router.get("/productos/simula-pausa")
def productos_simula_pausa(
    conn: ConexionLectura,
    platform: str,
    clics: str | None = None,
    gasto: str | None = None,
) -> dict:
    """AUTO-03 H.8: simulacion de pausa por producto (solo lectura)."""
    from app import pantalla_decisiones_producto
    from app.config_write import SettingsInvalido
    from app.optimizer import productos
    from app.productos_io import SinMetricasDeAnuncios

    if platform not in productos.PLATAFORMAS:
        raise HTTPException(status_code=422, detail="plataforma sin decisiones por producto")
    try:
        simulacion = pantalla_decisiones_producto.simula_pausa(
            conn, platform, ahora=_ahora_utc(), clics=clics, gasto=gasto
        )
    except SettingsInvalido as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SinMetricasDeAnuncios as exc:
        raise HTTPException(
            status_code=422, detail="el mercado no tiene metricas de anuncios que simular"
        ) from exc
    return simulacion.como_json()


@router.get("/productos/pausa")
def productos_pausa(conn: ConexionLectura, platform: str) -> dict:
    """AUTO-03 I.8: estado vigente de la pausa por producto (solo lectura)."""
    from app import pantalla_decisiones_producto
    from app.optimizer import productos

    if platform not in productos.PLATAFORMAS:
        raise HTTPException(status_code=422, detail="plataforma sin decisiones por producto")
    return pantalla_decisiones_producto.estado_pausa(conn, platform)


@router.get("/chrome")
def chrome(conn: ConexionLectura) -> dict:
    """Resumen liviano para `shell.js` (3 queries en vez de /cortes + /salud)."""
    modos = dict(conn.execute(_SQL_CHROME_COLA).fetchall())
    watermarks = {
        plat: (dia.isoformat() if dia is not None else None)
        for plat, dia in conn.execute(_SQL_CHROME_WATERMARK).fetchall()
    }
    ciclos = {
        plat: {
            "mode": mode,
            "status": status,
            "started_at": started_at.isoformat() if started_at is not None else None,
            "decisions_count": int(decisions_count or 0),
        }
        for plat, mode, status, started_at, decisions_count in conn.execute(
            _SQL_CHROME_ULTIMO_CICLO
        ).fetchall()
    }
    return {
        "propuestas": {
            "live": int(modos.get("live", 0)),
            "shadow": int(modos.get("shadow", 0)),
        },
        "plataformas": {
            plat: {
                "watermark": watermarks.get(plat),
                "ultimo_ciclo": ciclos.get(plat),
            }
            for plat in PLATAFORMAS_MONEDA
        },
    }


@router.get("/busquedas-sin-venta")
def busquedas_sin_venta(conn: ConexionLectura, plataforma: str | None = None) -> dict:
    """Busquedas que gastan sin vender (BIDS 02, P.6)."""
    from app import pantalla_busquedas

    mercado = _mercado_o_422(plataforma)
    datos = pantalla_busquedas.lee_busquedas(
        conn, plataforma=mercado, hoy=_hoy_pantalla(mercado)
    ).como_dict()
    for clave in ("asins", "palabras"):
        for fila in datos[clave]:
            fila["gasto"] = _dinero_2(fila["gasto"])
    return datos
