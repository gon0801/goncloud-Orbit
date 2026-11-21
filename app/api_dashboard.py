"""Router del dashboard del optimizador (ORBIT 16 - DASHBOARD 01).

Endpoints GET de LECTURA bajo `/api/dashboard` (rol `orbit_read` via
ORBIT_DSN_READ; sin DSN -> 503 fail-closed, misma dependencia que api.py).
Solo GET en toda la superficie (candado de introspeccion OpenAPI).

Task 1.4 - campanas + feed de decisiones (brief §3.3/§3.4):

- `/campanas`: resumen 30d por campana (grano campaign) + target EFECTIVO con
  PROCEDENCIA de 5 peldanos REUTILIZANDO `cascada_target_acos_con_procedencia`
  de goals.py (1.2) + estado VIVO del goal resuelto (campana > plataforma,
  decision 17). ANTI-MEZCLA (regla 4): cada fila lleva su moneda, NO existe
  total al pie.
- `/decisiones`: feed por CURSOR (`id <`, ORDER BY id DESC; PROHIBIDO
  limit/offset - decision 8) con JOIN a ad_entity para el nombre (nullable).
  El target mostrado se lee de `inputs.target_acos_pct_usado`, JAMAS de
  `inputs.goal.target_acos_pct` (NULL cuando gano el default - trampa sellada
  grok r2). Motivo en espanol via dict que IMPORTA los MOTIVO_* de
  bid/hygiene; motivo desconocido -> fallback al id crudo sin crash. Los
  pause traen old/new/value_currency NULL (CHECK del schema): se renderizan
  null, jamas 0.

REPRICING 01 A.6 - pantalla de precios: GET `/precios` (recuadro S10 en
vivo con app.precio.fuentes + app.precio.cobertura, decisiones del dia y
huerfanas) + `plataformas.<p>.precios` en /salud (liviano, sin recuadro,
sin cambiar las claves existentes).
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from psycopg.rows import dict_row, tuple_row

from app.ads.salud import bloque_salud as bloque_ads_ingest
from app.api import KINDS_DECISION, ConexionLectura, propuestas_campana_visibles
from app.api_common import (
    _SQL_ULTIMO_CICLO_POR_PLATAFORMA,
    _dec_str,
    _dos_dec,
    _fila_ciclo,
    _parse_notes,
    bloque_target_margen,
)
from app.api_common import target_margen_del_ciclo as _target_margen_del_ciclo
from app.api_precios import (  # noqa: F401 - reexport que usan /salud y pruebas
    _TOPE_RACHA_DIVERGENTE,
    _acciones_precio,
    _dinero_2,
    _precios_de,
    precios,
    validar_precio_aviso_dias,
)
from app.apply_cuota import KINDS_QUOTA, estado_quota
from app.apply_harvest import ROLES_DISCOVERY
from app.config_write import ADVERTENCIA_RESPALDO, clave_cap, metas_resumen_desde_settings
from app.dashboard_contribucion import contribucion_campanas as _contribucion_campanas
from app.dashboard_pagina import (
    _CAMPANA_ANCESTRO,
    _JOINS_ANCESTROS,
    _SQL_DECISIONES_CONTEOS,
    _SQL_DECISIONES_FEED,
    _SQL_DECISIONES_PAGINA,
    _SQL_DECISIONES_TOTAL_FILTRADO,
    TIPOS_DECISION,
    PageWindow,
)
from app.dashboard_resumen import CANALES_POR_DEFECTO as CANALES_RESUMEN_POR_DEFECTO
from app.dashboard_resumen import (
    DIAS_PERIODO_DEFAULT,
    acos_texto,
    hoy_resumen,
    ventana_resumen,
)
from app.dashboard_resumen import resumen as resumen_multicanal
from app.etiqueta_entidad import etiqueta_entidad, linea_entidad
from app.etiquetas import ETIQUETAS, TIPO_TARGET, etiqueta, motivo_decision_es
from app.optimizer import goals as g
from app.optimizer import hygiene, temporada
from app.optimizer.bid import PLATAFORMAS_MONEDA
from app.optimizer.windows import _SQL_SYNC_PLATAFORMA, _SQL_WATERMARK_PLATAFORMA
from app.redaction import install_scrub_filter, scrub
from app.spapi.salud import bloque_salud

logger = logging.getLogger(__name__)
install_scrub_filter(logger)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

# Ventana FIJA del resumen de campanas (brief §3.3): [D-30, D-1] con el hoy del Resumen.
DIAS_VENTANA_CAMPANAS = 30

# Paginacion del feed por CURSOR: tope duro de 200 filas por pagina (patron
# /audit; el feed jamas usa offset, decision 8 del header).
LIMITE_FEED_MAX = 200
LIMITE_FEED_DEFAULT = 50

# Resumen 30d de campanas: una fila por campana con sus sumas colapsadas
# (v_metric_latest) + el cache del acos_target publicado (ad_entity_state,
# 4to peldano de la cascada; LEFT JOIN: sin estado = None, regla 3). El
# LEFT JOIN NO rompe el grano: el WHERE e.kind = 'campaign' sigue siendo
# conjuncion obligatoria (candado AST).
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

# Goals completos (tabla chica; la precedencia la resuelve goals.py, jamas
# aqui). Mismo shape de columnas que _lee_goals de cycle.py.
_SQL_GOALS_DASHBOARD = """
SELECT scope, ad_entity_id, platform, target_acos_pct, bid_floor, bid_ceiling,
       bid_currency, harvest_campaign_id, harvest_ad_group_id,
       harvest_default_bid, enabled, mode
  FROM ads_optimizer_goal
"""

_SQL_CONFIG_VIGENTE = """
SELECT id, settings FROM config_version ORDER BY id DESC LIMIT 1
"""


# Ultimos 14 ciclos de la plataforma (acotado; el candado del test fija el
# LIMIT). El snapshot usa la MISMA fuente del /status de 3.2
# (_SQL_ULTIMO_CICLO_POR_PLATAFORMA, extraido a api_common).
_SQL_HISTORICO_14D = """
SELECT id, mode, platform, started_at, finished_at, decisions_count,
       applied_count, status, notes
  FROM optimizer_cycle
 WHERE platform = %s::platform
   AND motor = 'ads_optimizer'
 ORDER BY id DESC
 LIMIT 14
"""


def motivo_es(motivo: str | None) -> str | None:
    """Un motivo de /salud en palabras (FABRICA 02, A.6); None queda None
    (regla 3). Lo usan /cortes y el feed."""
    if motivo is None:
        return None
    return etiqueta(motivo, "motivo_salud")


def _ahora_utc() -> dt.datetime:
    """Instante actual del reloj de las pantallas (las pruebas lo inyectan)."""
    return dt.datetime.now(dt.UTC)


def _hoy_pantalla(*plataformas: str) -> dt.date:
    """Hoy de las pantallas: el del Resumen (hoy_resumen), fecha local minima
    de esos canales."""
    return hoy_resumen(plataformas, _ahora_utc())


def _ventana_campanas(plataformas: Sequence[str], instante: dt.datetime) -> tuple[dt.date, dt.date]:
    """[D-30, D-1] con el hoy del Resumen: la misma ventana que «Resumen · 30 dias»
    de esos canales."""
    hoy = hoy_resumen(plataformas, instante)
    return hoy - dt.timedelta(days=DIAS_VENTANA_CAMPANAS), hoy - dt.timedelta(days=1)


def _acoso(cost: Decimal | None, ad_revenue: Decimal | None) -> tuple[str | None, bool]:
    """(acos_str, sin_ventas) del dia; delega el ratio en dashboard_resumen.

    ad_revenue == 0 CONOCIDO -> sin_ventas true y acos null (infinito: jamas
    division ni 0 enganoso); cost o ad_revenue None -> dato faltante (acos
    null, sin_ventas false); si no, ratio Decimal exacto con 2 decimales como
    STRING (el cliente parsea con Number(); decision 7 del header). Una sola
    implementacion del ACoS para Campanas y el Resumen multicanal.
    """
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
    """Resumen multicanal (RESUMEN-ADS-02): por canal `totals`, `comparison`
    de igual longitud, `series` diaria con MA7, `coverage`, `max_metric_date`,
    `last_ingest_at` y `maduracion`. Moneda NATIVA por canal; los totales
    multi-canal van agrupados por moneda (jamas se mezclan MXN y USD).

    `periodo` acepta 7/30/90 dias (default 30). Con `desde` y `hasta` el
    rango es personalizado (ambos obligatorios; `periodo` se ignora) de hasta
    366 dias. La ventana visible termina en el D-1 del "hoy" del Resumen: la
    fecha local MINIMA entre las zonas de los canales pedidos (Amazon MX y
    MeLi cierran en America/Mexico_City, Amazon US en America/Los_Angeles).
    Un `hasta` >= hoy se RECORTA y la respuesta declara
    `hasta_solicitado`/`recortado`. Rango invertido, fuera del calendario o
    de mas de 366 dias -> 422.

    El calculo (catalogo, ventana, agregacion, MA7) vive en
    `app.dashboard_resumen`; este endpoint solo valida parametros y delega.
    """
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


def _goal_desde_fila(fila) -> g.Goal:
    """Reconstruye un Goal (dataclass puro de goals.py) desde una fila de
    ads_optimizer_goal (mismo shape que _lee_goals de cycle.py). La
    precedencia campana > plataforma la resuelve goals.py (resuelve_goal +
    cascada de 1.2), JAMAS se reimplementa en la capa web."""
    (
        scope,
        ad_entity_id,
        platform,
        target_acos_pct,
        bid_floor,
        bid_ceiling,
        bid_currency,
        harvest_campaign_id,
        harvest_ad_group_id,
        harvest_default_bid,
        enabled,
        mode,
    ) = fila
    return g.Goal(
        scope=scope,
        ad_entity_id=ad_entity_id,
        platform=platform,
        target_acos_pct=target_acos_pct,
        bid_floor=bid_floor,
        bid_ceiling=bid_ceiling,
        bid_currency=bid_currency,
        harvest_campaign_id=harvest_campaign_id,
        harvest_ad_group_id=harvest_ad_group_id,
        harvest_default_bid=harvest_default_bid,
        enabled=enabled,
        mode=mode,
    )


def _carga_goals(conn) -> tuple[dict[int, g.Goal], dict[str, g.Goal]]:
    """(goals de campana por ad_entity_id, goals de plataforma por platform)
    de la tabla completa (chica: 2 filas hoy). La precedencia la resuelve
    goals.py, no el SQL ni esta capa."""
    goals_campana: dict[int, g.Goal] = {}
    goals_plataforma: dict[str, g.Goal] = {}
    for fila in conn.execute(_SQL_GOALS_DASHBOARD).fetchall():
        goal = _goal_desde_fila(fila)
        if goal.scope == "campaign" and goal.ad_entity_id is not None:
            goals_campana[goal.ad_entity_id] = goal
        elif goal.scope == "platform" and goal.platform is not None:
            goals_plataforma[goal.platform] = goal
    return goals_campana, goals_plataforma


def _config_vigente_con_id(conn) -> tuple[int | None, dict]:
    """(id, settings) de la config_version VIGENTE (la de mayor id); sin
    config -> (None, {}) (el setting no gana y la cascada sigue a
    cache/default, regla 3). El id es la base de la edicion optimista de
    /settings (config_write.guarda_config)."""
    fila = conn.execute(_SQL_CONFIG_VIGENTE).fetchone()
    return (fila[0], fila[1]) if fila is not None else (None, {})


def _goal_estado(goal: g.Goal | None) -> dict | None:
    """Estado VIVO del goal resuelto (decision 17: el goal es config mutable,
    no historia): enabled + floor/ceiling EFECTIVOS (defaults de goals.py POR
    MONEDA, preflight 1.2 -- la moneda es goal.bid_currency) + mode + scope.
    Sin goal -> None (regla 3: faltante = null, no inventado)."""
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
    """Lo que el motor APLICO a las hojas de UNA campana (A7, dato puro):
    minimo/maximo Decimal (sin strings redondeados en campos), procedencias
    y ciclos que aportaron. display() formatea con _dos_dec (borde de
    presentacion): valor unico o rango "min–max", peldano unico o "mixto"."""

    minimo: Decimal
    maximo: Decimal
    procedencias: frozenset[str]
    ciclo_ids: frozenset[int]
    decideds: frozenset[dt.datetime]

    def display(self) -> dict:
        unico = _dos_dec(self.minimo) == _dos_dec(self.maximo)
        # decideds a UTC explicito: el isoformat crudo heredaria la TimeZone
        # de la sesion y el contrato seria no-determinista entre entornos.
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


# A7r2: hojas congeladas en el ULTIMO ciclo live+done de la plataforma
# (el plan: lo que ese ciclo aplico; una hoja vieja no mete su valor en
# el rango). Una query por plataforma; el colapso es Python puro.
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


def _congelados_por_campana(conn, plataforma: str) -> dict[int, CongeladoCampana]:
    """Congelado por campana (A7r2): hojas del ULTIMO ciclo live+done
    colapsadas en Python. Campana sin hojas en ese ciclo -> ausente (el
    endpoint publica null-honesto, regla 3)."""
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
    """Fila del resumen: metricas 30d (grano campaign, dinero string) +
    target EFECTIVO = lo que el motor aplico (congelado; A7) o null-honesto
    sin ciclo (campanas sin ciclo: null, cambio visible y pineado) + goal
    resuelto. Sin total al pie (regla 4)."""
    camp_id, nombre, _plataforma, _acos_cache, estado, cost, revenue, clicks = fila
    goal_campana = goals_campana.get(camp_id)
    acos, sin_ventas = _acoso(cost, revenue)
    return {
        "ad_entity_id": camp_id,
        "nombre": nombre,
        # estado publicado en Amazon (ad_entity_state; feedback del smoke
        # 1.7: no todas las campanas con datos 30d siguen activas). Sin
        # estado -> null (regla 3), jamas un estado inventado.
        "status": estado,
        "plataforma": plataforma,
        "moneda": PLATAFORMAS_MONEDA[plataforma],
        "metricas_30d": {
            "cost": _dec_str(cost),
            "ad_revenue": _dec_str(revenue),
            "clicks": clicks,
            "acos": acos,
            "sin_ventas": sin_ventas,
            # la ventana [D-30, D-1] siempre incluye D-8..D-1: el agregado
            # contiene dias que aun maduran y debe DECLARARLO (trampa de los
            # tres relojes), jamas mostrarlos como maduros.
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
    """Resumen de campanas de la(s) plataforma(s) (brief §3.3): metricas 30d
    colapsadas (v_metric_latest, kind='campaign') + target EFECTIVO = lo que
    el ultimo ciclo live+done congelo para sus hojas (A7; rango si difieren,
    null-honesto sin ciclo) + estado VIVO del goal resuelto. Cada fila
    lleva su moneda; NO existe total al pie (anti-mezcla, regla 4).

    Cambio de contrato A7 (documentado): target_efectivo ya no es la
    cascada viva {valor, peldano} sino {valor, minimo, maximo, peldano,
    ciclo{id_min, id_max, decided_min, decided_max}} o null. ui.py intacto:
    el sort por target degrada en rangos (no parsean a Decimal) y el filtro
    por "mixto" da 422 (vocabulario cerrado preexistente); ambos declarados.
    """
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


# FABRICA 02 (A.6): fase del harvest_job -> etiqueta. Espejo del CHECK de
# harvest_job.fase en 0001 + 0038 (pending, negative_created,
# exact_created, hermanas_negadas, done, failed): si el CHECK gana una
# fase, este dict la gana con su etiqueta.
FASES_ES_HARVEST: dict[str, str] = {
    "pending": "Pendiente de aplicar",
    "negative_created": "Negativo en la campana origen creado",
    "exact_created": "Keyword exacta creada en el destino",
    "hermanas_negadas": "Negando el termino en las campanas hermanas",
    "done": "Aplicado",
    "failed": "Fallido",
}


def _filtros_feed(platform, kind) -> tuple[list[str], list]:
    """Fragmentos SQL FIJOS + parametros de los filtros del feed (ningun texto
    del usuario se interpola: solo clausulas literales de este codigo).
    Devuelve la LISTA de clausulas (el caller agrega el cursor y une una sola
    vez): devolver el string ya unido rompia el feed — bug atrapado por
    test_feed_sql_compuesto_parsea_con_todos_los_filtros."""
    clausulas: list[str] = []
    params: list = []
    if platform is not None:
        clausulas.append("e.platform = %s::platform")
        params.append(platform)
    if kind is not None:
        clausulas.append("d.kind = %s::decision_kind")
        params.append(kind)
    return (clausulas, params)


_CAMPOS_BUSQUEDA_DECISIONES = (
    "coalesce(" + _CAMPANA_ANCESTRO + ", '')",
    "coalesce(e.name, '')",
    "coalesce(e.keyword_text, '')",
    "coalesce(d.search_term, '')",
)
_NOMBRE_SIN_SIGNOS = "regexp_replace(lower(e.name), '[^a-z0-9]+', ' ', 'g')"


def _normaliza_busqueda(texto: str) -> str:
    """Texto comparable contra la etiqueta visible: minusculas y sin acentos."""
    sin_acentos = "".join(
        caracter
        for caracter in unicodedata.normalize("NFKD", texto)
        if not unicodedata.combining(caracter)
    )
    return " ".join(re.sub(r"[^0-9a-z]+", " ", sin_acentos.lower()).split())


def _pares_busqueda_decisiones(texto: str) -> list[tuple[str, tuple[str, ...]]]:
    """Cada (expresion SQL, parametros) donde busca q en las decisiones.

    La entidad se pinta traducida por `etiqueta_entidad` (por ejemplo
    «busqueda amplia» para `QUERY_BROAD_REL_MATCHES`, o «mismo ASIN B0...»
    para un target con valor). El usuario no debe conocer el codigo interno:
    se agregan los codigos cuyo texto visible esta en q. Cuando q trae tambien
    el valor, la condicion exige codigo y valor en el mismo `e.name`; si no,
    agregaria todos los targets del mismo tipo.
    """
    pares: list[tuple[str, tuple[str, ...]]] = [
        (f"strpos(lower({campo}), lower(%s)) > 0", (texto,))
        for campo in _CAMPOS_BUSQUEDA_DECISIONES
    ]
    normalizado = _normaliza_busqueda(texto)
    if not normalizado:
        return pares
    for codigo, etiqueta_target in TIPO_TARGET.items():
        etiqueta = _normaliza_busqueda(etiqueta_target)
        if not etiqueta:
            continue
        if etiqueta in normalizado:
            resto = normalizado.replace(etiqueta, "", 1).strip()
            if resto:
                pares.append(
                    (
                        "(strpos(lower(e.name), lower(%s)) > 0"
                        " AND strpos(lower(e.name), lower(%s)) > 0)",
                        (codigo, resto),
                    )
                )
            else:
                pares.append(("strpos(lower(e.name), lower(%s)) > 0", (codigo,)))
        elif normalizado in etiqueta:
            pares.append(("strpos(lower(e.name), lower(%s)) > 0", (codigo,)))
    pares.append((f"strpos(lower({_NOMBRE_SIN_SIGNOS}), lower(%s)) > 0", (normalizado,)))
    return pares


def _filtros_decisiones(tipo: str | None, q: str | None) -> tuple[str, list]:
    """Clausulas del filtro de Decisiones (decision del dueno 1).

    `tipo` es vocabulario cerrado: se traduce a fragmentos SQL literales de
    este modulo y cualquier valor desconocido cae en `todas`. La busqueda va
    como parametro y usa `strpos`: el texto del usuario nunca se interpola ni
    se interpreta como patron LIKE.
    """
    clausulas: list[str] = []
    params: list = []
    texto = (q or "").strip()
    if texto:
        pares = _pares_busqueda_decisiones(texto)
        busqueda = " OR ".join(expresion for expresion, _valores in pares)
        clausulas.append(f"({busqueda})")
        params.extend(valor for _expresion, valores in pares for valor in valores)
    tipo = tipo if tipo in TIPOS_DECISION else "todas"
    if tipo == "pausa":
        clausulas.append("d.kind = 'pause'::decision_kind")
    elif tipo == "negativo":
        clausulas.append("d.kind = 'negative'::decision_kind")
    elif tipo == "captura":
        clausulas.append("d.kind = 'harvest'::decision_kind")
    elif tipo == "puja_sube":
        clausulas.append(
            "d.kind = 'bid'::decision_kind"
            " AND d.new_value IS NOT NULL AND d.old_value IS NOT NULL"
            " AND d.new_value > d.old_value"
        )
    elif tipo == "puja_baja":
        clausulas.append(
            "d.kind = 'bid'::decision_kind"
            " AND d.new_value IS NOT NULL AND d.old_value IS NOT NULL"
            " AND d.new_value < d.old_value"
        )
    return (" AND ".join(clausulas) or "true"), params


def _fila_decision_pagina(fila) -> dict:
    """Fila del HTML: el shape del feed + la entidad y la campana separadas
    (el diseno las pinta en la misma linea). El feed JSON NO las recibe:
    su contrato no cambia."""
    item = _fila_decision(fila)
    etiqueta_ent = etiqueta_entidad(
        kind=fila[12],
        name=fila[3],
        keyword_text=fila[13],
        campana=fila[14],
    )
    item["entidad"] = etiqueta_ent.hoja
    item["campana"] = etiqueta_ent.campana
    return item


def decisiones_pagina(
    conn: ConexionLectura,
    page: int,
    page_size: int,
    tipo: str | None = "todas",
    q: str | None = None,
) -> tuple[list[dict], PageWindow]:
    """Pagina HTML: count filtrado -> ventana clampada -> LIMIT/OFFSET.

    El total y la pagina usan las MISMAS clausulas: un filtro no puede pintar
    una ventana que no corresponde con su total. No es ruta.
    """
    filtros, params = _filtros_decisiones(tipo, q)
    consulta = conn.execute(_SQL_DECISIONES_TOTAL_FILTRADO.format(filtros=filtros), params)
    total = consulta.fetchone()[0]
    ventana = PageWindow.desde_total(total=total, page=page, page_size=page_size)
    filas = conn.execute(
        _SQL_DECISIONES_PAGINA.format(filtros=filtros),
        [*params, ventana.page_size, ventana.offset],
    ).fetchall()
    return [_fila_decision_pagina(fila) for fila in filas], ventana


def decisiones_conteos(conn: ConexionLectura, q: str | None = None) -> dict[str, int]:
    """Cuenta TODAS las decisiones que cumplen la busqueda, por tipo: es el
    numero que muestran las pestanas (no el de la pagina actual)."""
    filtros, params = _filtros_decisiones("todas", q)
    fila = conn.execute(_SQL_DECISIONES_CONTEOS.format(filtros=filtros), params).fetchone()
    return {
        "todas": fila[0],
        "pausa": fila[1],
        "negativo": fila[2],
        "captura": fila[3],
        "puja_sube": fila[4],
        "puja_baja": fila[5],
    }


def _fila_decision(fila) -> dict:
    """Una decision del feed. Trampas reales de la evidencia: el target se lee
    de inputs.target_acos_pct_usado (JAMAS de inputs.goal.target_acos_pct,
    NULL cuando gano el default — grok r2); los pause traen old/new/currency
    NULL (CHECK del schema): se renderizan null, jamas 0; motivo desconocido
    -> fallback al id crudo sin crash. FABRICA 02 (A.6): las columnas 15-17
    traen el harvest_job (id, fase, external_ids; NULLs sin job)."""
    inputs = fila[11] if isinstance(fila[11], dict) else {}
    motivo = inputs.get("motivo")
    # A4: linea de sombra del contrafactual (Python puro: inputs YA viaja
    # en la fila, cero SQL). Lookup de UNION (res C-W2: v2 emite bandas
    # de DECISIONES, abstenciones nuevas, y motivos SALUD-only como
    # rango_bloquea_ajuste/delta_bajo_umbral via distinto factor).
    frozen_v2 = inputs.get("evidencia_v2")
    evidencia_v2 = None
    if isinstance(frozen_v2, dict):
        veredicto = frozen_v2.get("veredicto") or {}
        motivo_v2 = veredicto.get("motivo")
        # A6-r1 F1 (DeepSeek): en via=decide con fallback, el veredicto ES
        # la decision viva v1 (no el resultado de v2): rotularlo "v2:"
        # mentiria. Se muestra la abstencion v2 real + quien decidio.
        abstencion = frozen_v2.get("abstencion_v2")
        fallback = frozen_v2.get("via") == "decide" and abstencion is not None
        motivo_mostrado = abstencion if fallback else motivo_v2
        evidencia_v2 = {
            "kind": veredicto.get("kind"),
            "motivo": motivo_mostrado,
            "motivo_es": motivo_decision_es(motivo_mostrado, fila[12]),
            "factor": veredicto.get("factor"),
            "new_value": veredicto.get("new_value"),
            "mantiene": (
                veredicto.get("kind") == fila[5] and veredicto.get("factor") == inputs.get("factor")
            ),
            "fallback_v1": fallback,
        }
    harvest_job = None
    if fila[15] is not None:
        externos = fila[17] if isinstance(fila[17], dict) else {}
        pendientes = externos.get("hermanas_pendientes")
        harvest_job = {
            "id": fila[15],
            "fase": fila[16],
            "fase_es": FASES_ES_HARVEST.get(fila[16], fila[16]),
            "hermanas_pendientes": pendientes if isinstance(pendientes, dict) else {},
        }
    return {
        "id": fila[0],
        "cycle_id": fila[1],
        "ad_entity_id": fila[2],
        "nombre": linea_entidad(
            kind=fila[12],
            name=fila[3],
            keyword_text=fila[13],
            campana=fila[14],
        ),
        "plataforma": fila[4],
        "kind": fila[5],
        "decided_at": fila[6],
        "search_term": fila[7],
        "old_value": _dec_str(fila[8]),
        "new_value": _dec_str(fila[9]),
        "value_currency": fila[10],
        "target_acos_pct_usado": inputs.get("target_acos_pct_usado"),
        "motivo_es": motivo_decision_es(motivo, fila[12]),
        "harvest_job": harvest_job,
        "evidencia_v2": evidencia_v2,
    }


@router.get("/decisiones")
def decisiones(
    conn: ConexionLectura,
    cursor: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=LIMITE_FEED_MAX)] = LIMITE_FEED_DEFAULT,
    platform: Literal["amazon_us", "amazon_mx"] | None = None,
    kind: KINDS_DECISION | None = None,
) -> dict:
    """Feed de decisiones por CURSOR (brief §3.4, decision 8): ORDER BY id
    DESC, `id < cursor` (la fila nueva con id mayor jamas se cuela en una
    pagina cuyo cursor ya quedo atras); PROHIBIDO limit/offset (offset sobre
    append-only produce huecos/duplicados). JOIN a ad_entity para el nombre
    (nullable). Sin filtros el `total` no se computa (patron /audit)."""
    clausulas, params = _filtros_feed(platform, kind)
    if cursor is not None:
        clausulas.append("d.id < %s")
        params.append(cursor)
    # limit+1: la fila extra solo dice si hay mas (jamas se sirve)
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


def _motivo_ciclo(status: str, notes) -> str | None:
    """Motivo visible de un ciclo degraded/failed/skipped (grok r2: skipped
    trae motivo_skip=escalera_off y quedaba fuera del filtro): de
    notes.motivo_skip traducido, o notes.degradacion_live, o notes.error (el
    sello de _sello_fallido persiste el error scrubbeado AHI — grok r2), o el
    texto plano del notes (formato mixto); None en ciclos done sin motivo.
    Jamas un ciclo roto por un notes raro (regla 3: faltante = null)."""
    if status not in ("degraded", "failed", "skipped"):
        return None
    if isinstance(notes, dict):
        motivo_skip = notes.get("motivo_skip")
        if motivo_skip is not None:
            return etiqueta(motivo_skip, "motivo_salud")
        degradacion = notes.get("degradacion_live")
        if degradacion:
            # Texto libre del ciclo: la frase conocida se traduce, otra pasa tal cual.
            return ETIQUETAS["degradacion_ciclo"].get(degradacion, degradacion)
        error = notes.get("error")
        if error:
            return error
        # _parse_notes envuelve notas de texto plano bajo la clave "texto".
        return notes.get("texto")
    return None


def _fila_historico(fila) -> dict:
    """Una fila del historico 14d: cycle_id, fecha (started_at), status,
    decisions_count y, cuando aplique, el motivo (degradado/failed visible
    con motivo — DoD de 1.5)."""
    (cycle_id, _mode, _platform, started_at, _fin, decisions_count, _applied, status, notes) = fila
    return {
        "cycle_id": cycle_id,
        "fecha": started_at,
        "status": status,
        "decisions_count": decisions_count,
        "motivo": _motivo_ciclo(status, _parse_notes(notes)),
    }


def _skips_traducidos(contadores: dict) -> list[dict]:
    """[{motivo, count, motivo_es}] del mas contado al menos contado (empate:
    por motivo). La lista conserva el orden hasta la grafica: un dict lo
    pierde en `tojson`, que ordena las claves."""
    return [
        {"motivo": motivo, "count": count, "motivo_es": etiqueta(motivo, "motivo_salud")}
        for motivo, count in sorted(contadores.items(), key=lambda par: (-par[1], par[0]))
    ]


def _skips_de(ultimo_ciclo: dict | None) -> dict:
    """Skips agregados por motivo del ULTIMO ciclo (vocabulario del
    orquestador + MOTIVO_* de bid/hygiene que importa a sus contadores),
    traducidos (decision 11). Sin ciclo o sin notes -> vacio, jamas null
    inventado."""
    if ultimo_ciclo is None or ultimo_ciclo.get("notes") is None:
        return {"entidad": [], "termino": []}
    skips = ultimo_ciclo["notes"].get("skips") or {}
    return {
        "entidad": _skips_traducidos(skips.get("entidad") or {}),
        "termino": _skips_traducidos(skips.get("termino") or {}),
    }


def _harvest_destino_de(ultimo_ciclo: dict | None) -> dict | None:
    """Bloque harvest_destino del ULTIMO ciclo (FABRICA 02, A.6): destinos
    resueltos por procedencia + campanas de grupo sin destino, traducidas.
    Sin ciclo o sin la clave (ciclos pre-A.6) -> None (regla 3: jamas se
    inventan resueltos). Con bloque, resueltos trae las tres claves
    (ausente en notes = 0: es un contador del mismo ciclo, no un dato
    faltante) y saltos_grupo ordenado por campana."""
    if ultimo_ciclo is None or ultimo_ciclo.get("notes") is None:
        return None
    bloque = ultimo_ciclo["notes"].get("harvest_destino")
    if not isinstance(bloque, dict):
        return None
    resueltos = bloque.get("resueltos") or {}
    saltos = bloque.get("saltos_grupo") or {}
    return {
        "resueltos": {k: resueltos.get(k, 0) for k in ("grupo", "excepcion", "terna")},
        "terna_es": ETIQUETAS["motivo_salud"][hygiene.MOTIVO_MIGRACION_PENDIENTE],
        "saltos_grupo": [
            {"campaign_id": int(camp), "motivo": motivo, "motivo_es": motivo_es(motivo)}
            for camp, motivo in sorted(saltos.items(), key=lambda par: int(par[0]))
        ],
    }


@router.get("/salud")
def salud(conn: ConexionLectura) -> dict:
    """Salud por plataforma (brief §3.5, plan 1.5): snapshot del ULTIMO ciclo
    + historico ACOTADO a 14d + watermarks (las MISMAS fuentes del motor:
    v_metric_latest y synced_at — regla 2) + skips del ultimo ciclo con su
    traduccion. REUTILIZA _parse_notes, _fila_ciclo y el SQL de ultimo ciclo
    extraidos a api_common (jamas dos copias, decision 6).

    Preflight 1.4 (sellado 4): cada plataforma gana "quota" —
    {kind: {used, cap, fuente}} para cada kind de KINDS_QUOTA. DECISION
    DECLARADA: la visibilidad vive AQUI (y no en /api/ads-optimizer/status)
    porque /salud es la fuente que la pantalla /salud ya consume
    (app/ui.py pagina_salud) y donde 0002/notifica ya declaran la
    visibilidad de la quota ("VISIBLE en Salud"); la tarea 1.5 renderiza
    desde aqui sin cablear un fetch nuevo, y NO se agregan rutas (la
    superficie de ambos routers queda sellada). UNA fuente: estado_quota de
    app.apply (el mismo motor_quota y la misma expresion de dia UTC que
    consume_quota); la app JAMAS re-implementa el mapeo de caps."""
    ciclos = {
        fila[2]: _fila_ciclo(fila)  # fila[2] = platform (ver SELECT)
        for fila in conn.execute(_SQL_ULTIMO_CICLO_POR_PLATAFORMA).fetchall()
    }
    plataformas: dict[str, dict] = {}
    for plataforma in PLATAFORMAS_MONEDA:
        watermark = conn.execute(_SQL_WATERMARK_PLATAFORMA, (plataforma,)).fetchone()[0]
        synced_at = conn.execute(_SQL_SYNC_PLATAFORMA, (plataforma,)).fetchone()[0]
        historico = conn.execute(_SQL_HISTORICO_14D, (plataforma,)).fetchall()
        ultimo = ciclos.get(plataforma)
        plataformas[plataforma] = {
            "watermark": watermark.isoformat() if watermark is not None else None,
            "synced_at": synced_at,
            "ultimo_ciclo": ultimo,
            "historico_14d": [_fila_historico(fila) for fila in historico],
            "skips": _skips_de(ultimo),
            "harvest_destino": _harvest_destino_de(ultimo),
            "quota": _quota_de(conn, plataforma),
            "avisos_propuesta": _avisos_propuesta_de(conn, plataforma, ultimo),
            "target_margen": bloque_target_margen(ultimo),
            "spapi": _spapi_de(conn, plataforma),
            "ads_ingest": _ads_ingest_de(conn, plataforma),
            "precios": _precios_de(conn, plataforma),
            "avisos_campana": _avisos_campana_de(conn, plataforma),
        }
    return {"plataformas": plataformas, "jev": _jev_de(conn)}


def _jev_de(conn: ConexionLectura) -> dict | None:
    """Bloque jev de /salud (JEV ADS 02 S.4), hermano de `plataformas`: si
    entrara ahi, shell.js lo pintaria como un mercado. Import tardio y None
    si algo falla (sin tablas S.3, sin config): la pantalla no muere."""
    try:
        from app import jev_salud

        return jev_salud.salud(conn, ahora=dt.datetime.now(dt.UTC)).como_dict()
    except Exception as exc:
        logger.warning("salud: jev ilegible: %s", scrub(str(exc)))
        return None


def _ads_ingest_de(conn: ConexionLectura, plataforma: str) -> dict | None:
    try:
        return bloque_ads_ingest(conn, plataforma)
    except Exception as exc:
        logger.warning("salud: ads_ingest %s ilegible: %s", plataforma, scrub(str(exc)))
        return None


def _avisos_campana_de(conn: ConexionLectura, plataforma: str) -> list[str] | None:
    """Bloque avisos_campana de UNA plataforma (BIDS 02, V.4): frases del
    dia (lectores P.2a + `avisos_del_dia`, ventana de 30 dias hasta ayer).
    None si algo falla (patron _jev_de: sin tablas V.2, sin config)."""
    try:
        from app import avisos_campana as ac
        from app import pantalla_dinero as pd

        hasta = _hoy_pantalla(plataforma) - dt.timedelta(days=1)
        desde = hasta - dt.timedelta(days=pd.DIAS_UBICACION - 1)
        por_ubi = pd.lee_ubicaciones(conn, plataforma=plataforma, desde=desde, hasta=hasta)
        por_camp = pd.lee_campanas(conn, plataforma=plataforma, desde=desde, hasta=hasta)
        umbral = pd.umbral_concluir(conn, plataforma)
        return [
            aviso.frase
            for aviso in ac.avisos_del_dia(
                por_ubi,
                por_camp,
                plataforma=plataforma,
                gasto_para_concluir=umbral,
                topada=pd.campana_topada_vigente(conn),
            )
        ]
    except Exception as exc:
        logger.warning("salud: avisos_campana %s ilegible: %s", plataforma, scrub(str(exc)))
        return None


_SQL_AVISOS_PROPUESTA_PENDIENTES = """
SELECT count(*)
  FROM ads_campaign_proposal
 WHERE platform = %s::platform AND status = 'open' AND aviso_estado = 'pending'
"""


def _avisos_propuesta_de(
    conn: ConexionLectura, plataforma: str, ultimo_ciclo: dict | None
) -> dict | None:
    """Bloque avisos_propuesta de UNA plataforma (C.4 B1): propuestas open
    cuyo aviso Telegram sigue pending + el fallo del ultimo ciclo (de
    notes.telegram.aviso_propuesta). Sin tabla (pre-0042) -> None y la
    pantalla omite la seccion (patron _spapi_de)."""
    try:
        pendientes = conn.execute(_SQL_AVISOS_PROPUESTA_PENDIENTES, (plataforma,)).fetchone()[0]
    except Exception as exc:
        logger.warning(
            "salud: avisos de propuesta %s ilegibles: %s",
            plataforma,
            scrub(str(exc)),
        )
        return None
    fallo = None
    if ultimo_ciclo is not None and ultimo_ciclo.get("notes") is not None:
        fallo = (ultimo_ciclo["notes"].get("telegram") or {}).get("aviso_propuesta")
    return {"pendientes": pendientes, "fallo": fallo}


def _spapi_de(conn: ConexionLectura, plataforma: str) -> dict | None:
    """Bloque spapi de UNA plataforma (SP-API 01 A.5, ronda review). Si
    bloque_salud revienta (p. ej. columna platform ausente porque 0036 aun
    no se aplico: el codigo salio antes que la migracion), la pantalla NO
    muere entera — el bloque queda en None (la plantilla omite la seccion
    SP-API) y el resto (watermarks, ciclo, quota, historico) sigue
    visible. Espejo del try/except por pieza de _quota_de."""
    try:
        return bloque_salud(conn, plataforma)
    except Exception as exc:
        logger.warning(
            "salud: spapi %s ilegible: %s",
            plataforma,
            scrub(str(exc)),
        )
        return None


def _quota_de(conn: ConexionLectura, plataforma: str) -> dict:
    """El bloque quota de UNA plataforma (preflight 1.4). ADV-1 (adversary):
    un cap ROTO en la config (valor no numerico) revienta estado_quota A
    PROPOSITO en el camino de cobro (ruidoso, jamas disfraz de cap infinito)
    — pero la pantalla de LECTURA no muere entera por una forma rota: queda
    VISIBLE con fuente="config_rota" (cap null; no "sin_clave", que
    mentiria: la clave SI existe) y las formas sanas siguen legibles."""
    quota: dict = {}
    for kind in KINDS_QUOTA:
        try:
            quota[kind] = estado_quota(conn, plataforma, kind)
        except ValueError as exc:
            logger.warning(
                "salud: quota %s/%s ilegible (config rota): %s",
                plataforma,
                kind,
                scrub(str(exc)),
            )
            quota[kind] = {"used": 0, "cap": None, "fuente": "config_rota"}
    return quota


@router.get("/contribucion")
def contribucion_campanas(conn: ConexionLectura) -> dict:
    """Contribucion pre-cargos por campana (ORBIT 06 1.4, docs/MARGEN-ENTIDAD.md)."""
    return _contribucion_campanas(conn)


# Solo estados vetables: pending_veto y released (espera quota FIFO y sigue
# vetable); applying es punto de no retorno y los terminales no se vetan.
#
# CLARIDAD 01 C.1: modo separa las de practica. Una fila shadow nunca vence
# ni se aplica (trigger de migrations/0002_apply.sql:203), pero su veto si
# bloquea la clave (apply_cola._SQL_CLAVES_BLOQUEADAS no filtra por modo).
#
# CORTES UI 01: el indicador del harvest se lee de decision.inputs->'termino'
# (shape real en app/cycle.py: termino = {search_term, cost, ad_revenue,
# clicks, orders, fechas_distintas, moneda, ...}). Se trae el JSON entero y
# se extrae en Python (NULL = termino ausente -> indicador None, regla 3).
# FABRICA 02 (A.6): campana_id resuelve la campana del item por kind (los
# JOINs de ancestros ya estan): la query de hermanas excluye a la de origen.
_SQL_CORTES_PENDIENTES = (
    """
SELECT q.id, q.platform::text, q.familia, q.kind, q.ad_entity_id, e.external_id,
       q.search_term, q.estado, q.modo, q.vence_el, q.encolado_at, q.decision_id,
       e.kind::text AS entidad_kind, e.name, e.keyword_text,
       d.inputs AS decision_inputs,
       """
    + _CAMPANA_ANCESTRO
    + """ AS campana,
       CASE e.kind WHEN 'campaign' THEN e.id WHEN 'ad_group' THEN padre.id
            ELSE abuelo.id END AS campana_id
  FROM apply_queue q
  LEFT JOIN ad_entity e ON e.id = q.ad_entity_id
  JOIN decision d ON d.id = q.decision_id
"""
    + _JOINS_ANCESTROS
    + """
 WHERE q.estado IN ('pending_veto', 'released')
 ORDER BY q.vence_el, q.id
"""
)

# FABRICA 02 (A.6): hermanas del grupo (rol + externos de campana) menos la
# exacta (es el destino) y menos la de origen (%s segundo: su negativo ya
# existe por el propio harvest). Roles ausentes del grupo no aparecen.
_SQL_HERMANAS_GRUPO = """
SELECT r.rol::text, c.external_id, c.name
  FROM campana_grupo_rol r
  JOIN ad_entity c ON c.id = r.ad_entity_id
 WHERE r.grupo_id = %s AND r.rol <> 'category_exact' AND r.ad_entity_id <> %s
"""

# CORTES UI 01 (D2-D4): la pantalla se llama Propuestas y cada fila declara
# su tipo por KIND. La pausa se nombra por `kind:entidad_kind` (CLARIDAD 01
# C.4); los textos viven en app/etiquetas (dominios propuesta y efecto_rechazo).
DIRECCION_POR_KIND = {
    "pause": "recorta",
    "negative": "recorta",
    "harvest": "crece",
}


def _clave_texto(kind: str, entidad_kind: str | None) -> str:
    return f"{kind}:{entidad_kind}" if kind == "pause" and entidad_kind else kind


def _indicador_harvest(kind: str, decision_inputs: dict | None) -> dict | None:
    """Indicador que justifica el harvest (D2): ordenes e ingreso de la
    ventana desde decision.inputs->'termino'. Solo kind='harvest' y solo
    con termino COMPLETO (ordenes + ingreso + moneda); sin el o parcial:
    None (regla 3: un dict hueco pintaria " ordenes - de ingreso").
    Dinero = (valor string, moneda); NULL como null (regla 4)."""
    if kind != "harvest" or not isinstance(decision_inputs, dict):
        return None
    termino = decision_inputs.get("termino")
    if not isinstance(termino, dict):
        return None
    if (
        termino.get("orders") is None
        or termino.get("ad_revenue") is None
        or termino.get("moneda") is None
    ):
        return None
    return {
        "ordenes": termino.get("orders"),
        "ingreso": _dec_str(termino.get("ad_revenue")),
        "clics": termino.get("clicks"),
        "moneda": termino.get("moneda"),
    }


def _destino_harvest(kind: str, decision_inputs: dict | None) -> dict | None:
    """Destino congelado del harvest (FABRICA 02, A.6): inputs.goal.harvest
    con campaign_id, ad_group_id, resuelto_por, grupo_id, motivo y su
    traduccion. Solo kind='harvest' con goal.harvest dict; si no, None
    (regla 3: decisiones pre-F2 sin goal pasan intactas)."""
    if kind != "harvest" or not isinstance(decision_inputs, dict):
        return None
    goal = decision_inputs.get("goal")
    if not isinstance(goal, dict):
        return None
    harvest = goal.get("harvest")
    if not isinstance(harvest, dict):
        return None
    return {
        "campaign_id": harvest.get("campaign_id"),
        "ad_group_id": harvest.get("ad_group_id"),
        "resuelto_por": harvest.get("resuelto_por"),
        "grupo_id": harvest.get("grupo_id"),
        "motivo": harvest.get("motivo"),
        "motivo_es": motivo_es(harvest.get("motivo")),
    }


def _hermanas_de(conn, grupo_id: int, campana_id: int) -> list[dict] | None:
    """Hermanas donde se negara el termino (FABRICA 02, A.6): campanas del
    grupo menos la exacta y menos la de origen, en el orden canonico de
    ROLES_DISCOVERY (fuente unica, importada de apply_harvest: jamas una
    copia del orden). Una query por item harvest de grupo (la cola de veto
    es de decenas, no de miles). Si revienta (p. ej. esquema sin 0018) ->
    None + warning y la pantalla NO muere (patron _spapi_de)."""
    try:
        # tuple_row EXPLICITO: cortes() trabaja con dict_row y este helper
        # desempaqueta por posicion (con dicts iteraria las claves).
        with conn.cursor(row_factory=tuple_row) as cur:
            filas = cur.execute(_SQL_HERMANAS_GRUPO, (grupo_id, campana_id)).fetchall()
    except Exception as exc:
        logger.warning(
            "cortes: hermanas del grupo %s ilegibles: %s",
            grupo_id,
            scrub(str(exc)),
        )
        return None
    por_rol = {rol: (external, nombre) for rol, external, nombre in filas}
    return [
        {"rol": rol, "campaign_id": por_rol[rol][0], "nombre": por_rol[rol][1] or por_rol[rol][0]}
        for rol in ROLES_DISCOVERY
        if rol in por_rol
    ]


# Lee v_entidad_inerte (migracion 0013, UNICA fuente D2): nada se diagnostica
# aqui. NULLS LAST (gasto NULL = mezcla de monedas, fail-loud) e id de
# desempate estable.
# BIDS 01 2.6: JOIN a ad_entity por first_seen_at (migracion 0017) para
# mostrar la puerta de antiguedad del archivado. `en_espera` es ESPEJO EXACTO
# del predicado del tool (tools/archiva_inertes.py, _SQL_PLAN /
# _SQL_EXCLUIDOS_JOVENES) con el DEFAULT --min-antiguedad-dias=30: ese default
# es la fuente del numero (regla 2); si cambia, ESTE 30 se toca aqui.
# `archivable_desde` es informativo (primera vista + 30 dias, fecha UTC).
_SQL_INERTES = """
SELECT v.platform::text, v.kind::text, v.keyword_text, v.name, v.external_id,
       v.campaign_name, v.ad_group_name, v.clasificacion, v.dias_sin_impresiones,
       v.ultima_impresion, v.gasto_90d, v.moneda::text, v.ordenes_90d,
       (e.first_seen_at AT TIME ZONE 'UTC')::date AS first_seen_fecha,
       e.first_seen_at > (now() AT TIME ZONE 'UTC')::date - 30 AS en_espera,
       (e.first_seen_at AT TIME ZONE 'UTC')::date + 30 AS archivable_desde
  FROM v_entidad_inerte v
  JOIN ad_entity e ON e.id = v.id
 ORDER BY v.platform, v.clasificacion, v.gasto_90d DESC NULLS LAST, v.id
"""

# BIDS 01 2.6: resumen del ledger de lotes (keyword_archivo_manual, migracion
# 0014): por lote, la fecha del primer intento y conteos por estado; los 5
# mas recientes. Tabla vacia -> cero filas (sin fila inventada, regla 3).
_SQL_LOTES_INERTES = """
SELECT lote, (min(intentado_at) AT TIME ZONE 'UTC')::date AS primer_intento,
       count(*) FILTER (WHERE estado = 'planeado') AS planeado,
       count(*) FILTER (WHERE estado = 'applied') AS applied,
       count(*) FILTER (WHERE estado = 'failed') AS failed,
       count(*) FILTER (WHERE estado = 'repuesto') AS repuesto
  FROM keyword_archivo_manual
 GROUP BY lote
 ORDER BY min(intentado_at) DESC
 LIMIT 5
"""


def _senal_presentada(propuesta) -> dict | None:
    """SenalPropuesta a contrato de pantalla (S.5 J5b): cada cara con su
    titulo y su frase, y None cuando el job todavia no sello nada para esa
    decision. El destino ilegible si informa (la plantilla lo pinta como
    "sin lectura")."""
    origen = _senal_presentada_vista(propuesta.origen)
    destino = _senal_presentada_vista(propuesta.destino)
    if origen is None and destino is None and not propuesta.destino_ilegible:
        return None
    return {
        "origen": origen,
        "destino": destino,
        "destino_ilegible": propuesta.destino_ilegible,
    }


@router.get("/cortes")
def cortes(conn: ConexionLectura) -> dict:
    """Cortes pendientes de veto con su vencimiento (regla 22: la UI CONSUME
    este endpoint, no reimplementa queries). El POST del veto vive en
    app/api_write.py (auth de escritura, sellado 18); esta lectura no
    necesita token (es la misma conexion de lectura del dashboard)."""
    conn.row_factory = dict_row
    filas = conn.execute(_SQL_CORTES_PENDIENTES).fetchall()
    # S.5 J5b: senal guardada por decision (SOLO lectura). `_propuestas`
    # desempaqueta por posicion: pide tuplas y devuelve la fabrica a dict_row
    # antes de seguir.
    senal_disponible = True
    try:
        from app import jev_salud as pantallas_jev

        conn.row_factory = tuple_row
        try:
            senales = pantallas_jev.de_propuestas(conn, [fila["decision_id"] for fila in filas])
        finally:
            conn.row_factory = dict_row
        senal_por_decision = {
            decision_id: _senal_presentada(vista) for decision_id, vista in senales.items()
        }
    except Exception as exc:
        logger.warning("cortes: senal Jev ilegible: %s", scrub(str(exc)))
        senal_por_decision = {}
        senal_disponible = False
    items = []
    for fila in filas:
        # FABRICA 02 (A.6): destino congelado + hermanas del grupo. La query
        # de hermanas corre SOLO con resuelto_por grupo y grupo_id entero
        # (sin eso — la cola vieja — nunca corre); sin campana conocida no
        # se puede excluir al origen y la lista seria mentira: None.
        destino = _destino_harvest(fila["kind"], fila["decision_inputs"])
        hermanas = None
        if (
            destino is not None
            and destino["resuelto_por"] == "grupo"
            and isinstance(destino["grupo_id"], int)
            and fila["campana_id"] is not None
        ):
            hermanas = _hermanas_de(conn, destino["grupo_id"], fila["campana_id"])
        clave_texto = _clave_texto(fila["kind"], fila["entidad_kind"])
        items.append(
            {
                "id": fila["id"],
                "plataforma": fila["platform"],
                "familia": fila["familia"],
                "kind": fila["kind"],
                "ad_entity_id": fila["ad_entity_id"],
                "external_id": fila["external_id"],
                "nombre": linea_entidad(
                    kind=fila["entidad_kind"],
                    name=fila["name"],
                    keyword_text=fila["keyword_text"],
                    campana=fila["campana"],
                ),
                "search_term": fila["search_term"],
                "estado": fila["estado"],
                "modo": fila["modo"],
                "vence_el": fila["vence_el"].isoformat(),
                "encolado_at": fila["encolado_at"].isoformat(),
                "decision_id": fila["decision_id"],
                "etiqueta": etiqueta(clave_texto, "propuesta"),
                "direccion": DIRECCION_POR_KIND.get(fila["kind"]),
                "efecto_rechazo": etiqueta(clave_texto, "efecto_rechazo"),
                "indicador": _indicador_harvest(fila["kind"], fila["decision_inputs"]),
                "destino": destino,
                "hermanas": hermanas,
                "senal": senal_por_decision.get(fila["decision_id"]),
            }
        )
    # C.4 B2: propuestas de campana en la MISMA pantalla (open accionables
    # + paused_observed informativas). Si la tabla aun no existe (pre-0042),
    # la seccion queda vacia y la pantalla NO muere (patron _spapi_de).
    try:
        propuestas = propuestas_campana_visibles(conn)
    except Exception as exc:
        logger.warning("cortes: propuestas de campana ilegibles: %s", scrub(str(exc)))
        propuestas = []
    try:
        bloqueos_jev, bloqueos_disponibles = _bloqueos_jev(conn)
    except Exception as exc:
        logger.warning("cortes: bloqueos Jev ilegibles: %s", scrub(str(exc)))
        bloqueos_jev = {"interruptor": False, "plataformas": [], "bloqueos": []}
        bloqueos_disponibles = False
    return {
        "items": items,
        "propuestas_campana": propuestas,
        "senal_disponible": senal_disponible,
        "bloqueos_jev": bloqueos_jev,
        "bloqueos_disponibles": bloqueos_disponibles,
    }


def _motivo_sin_lectura(motivo: str) -> str:
    return etiqueta(motivo, "motivo_sin_lectura")


def _bloqueos_jev(conn) -> tuple[dict, bool]:
    from app.bloqueo_dueno import lee_pantalla
    from app.jev_vista import frase_de_lectura

    pantalla = lee_pantalla(conn)
    for plataforma in pantalla["plataformas"]:
        for propuesta in plataforma["propuestas"]:
            propuesta["frase"] = frase_de_lectura(
                "ajena",
                propuesta.pop("motivos") or (),
                int(propuesta.pop("miembros") or 0),
                propuesta.pop("listado_de"),
                motivo_texto=_motivo_sin_lectura,
            )
    return pantalla, True


_ORDEN_LECTURAS = (
    "vendio_aqui",
    "vende_en_otro",
    "relevante_sin_venta",
    "ajena",
    "sin_lectura",
)

_ORDEN_BANDAS = ("ninguno", "pocos", "una_parte", "todos", "sin_dato")


def _banda_honesta(fila: dict) -> str:
    """Banda de proporcion de una fila de gasto sin venta (S.5 J5b): sin
    roster probado no se afirma proporcion (diseno: decir "ninguno" exige
    conocer todos los productos) y la fila cae en `sin_dato`."""
    from app.jev_vista import banda_de_proporcion

    if not fila.get("roster_probado"):
        return "sin_dato"
    return banda_de_proporcion(fila.get("satisfacen", 0), fila.get("evaluados", 0))


@router.get("/gasto-sin-venta")
def gasto_sin_venta(conn: ConexionLectura, plataforma: str | None = None) -> dict:
    """Busquedas que gastan y no venden, por lectura (JEV ADS 02, S.5 J5b;
    regla 22: la UI CONSUME este endpoint). Funcion delgada: delega en
    `jev_salud.gasto_sin_venta` (SOLO SELECT) y solo presenta — frase,
    banda honesta, secciones por lectura con sus totales y aviso de
    calculo viejo (> 36 h). Sin mercado se mira amazon_mx; mercado ajeno
    es 422."""
    from app import jev_salud as pantallas_jev
    from app.jev_ads import es_plataforma_amazon
    from app.jev_vista import titulo_de_banda, titulo_de_lectura

    mercado = plataforma or "amazon_mx"
    if not es_plataforma_amazon(mercado):
        raise HTTPException(status_code=422, detail="plataforma fuera de vocabulario")
    pantalla = pantallas_jev.gasto_sin_venta(conn, plataforma=mercado)
    base = pantalla.como_dict()
    filas = []
    for cruda in base["filas"]:
        fila = _senal_presentada_vista(cruda)
        economia = dict(fila.get("economia") or {})
        for parte in ("aqui",):
            if economia.get(parte) and economia[parte].get("gasto") is not None:
                economia[parte] = {
                    **economia[parte],
                    "gasto": _dinero_2(economia[parte]["gasto"]),
                }
        fila["economia"] = economia
        fila["banda"] = _banda_honesta(fila)
        filas.append(fila)
    secciones = []
    for lectura in _ORDEN_LECTURAS:
        propias = [fila for fila in filas if fila["lectura"] == lectura]
        if not propias:
            continue
        total = base["totales"].get(lectura, {"busquedas": 0, "gasto": "0"})
        monedas = {fila["economia"]["moneda"] for fila in propias}
        seccion: dict = {
            "lectura": lectura,
            "titulo": titulo_de_lectura(lectura),
            "busquedas": total["busquedas"],
            "gasto": _dinero_2(total["gasto"]),
            "moneda": next(iter(monedas)) if len(monedas) == 1 else None,
            "filas": propias,
        }
        if lectura == "relevante_sin_venta":
            propias.sort(key=_proporcion_de)
            seccion["bandas"] = [
                {
                    "banda": banda,
                    "titulo": titulo_de_banda(banda),
                    "filas": [fila for fila in propias if fila["banda"] == banda],
                }
                for banda in _ORDEN_BANDAS
                if any(fila["banda"] == banda for fila in propias)
            ]
        secciones.append(seccion)
    calculado = base["calculado_el"]
    return {
        "plataforma": mercado,
        "calculado_el": calculado,
        "aviso_desactualizado": _calculo_viejo(calculado),
        "secciones": secciones,
        "total_filas": len(filas),
    }


def _senal_presentada_vista(vista) -> dict | None:
    """Una cara de senal (`SenalVista` o su dict de `como_dict`) con su
    titulo y su frase para la pantalla (presentacion pura de
    `app.jev_vista`); None si el job aun no la sello."""
    from app.jev_vista import frase_de_lectura, titulo_de_lectura

    if vista is None:
        return None
    fila = vista if isinstance(vista, dict) else vista.como_dict()
    fila = dict(fila)
    fila["titulo"] = titulo_de_lectura(fila["lectura"])
    fila["frase"] = frase_de_lectura(
        fila["lectura"],
        fila.get("motivos") or [],
        fila.get("miembros", 0),
        fila.get("listado_de"),
        motivo_texto=_motivo_sin_lectura,
    )
    return fila


def _proporcion_de(fila: dict) -> float:
    """Proporcion satisfacen/evaluados para ordenar (S.5: ascendente dentro
    de "corresponden y no venden"). Sin evaluados va primera (banda
    `sin_dato`)."""
    evaluados = fila.get("evaluados", 0)
    return (fila.get("satisfacen", 0) / evaluados) if evaluados else 0.0


def _calculo_viejo(calculado_el: str | None) -> bool:
    """Aviso de la pantalla (diseno S.5): True sin calculo o con la ultima
    corrida hace mas de 36 horas."""
    if not calculado_el:
        return True
    try:
        calculado = dt.datetime.fromisoformat(calculado_el)
    except ValueError:
        return True
    ahora = dt.datetime.now(dt.UTC)
    if calculado.tzinfo is None:
        calculado = calculado.replace(tzinfo=dt.UTC)
    return (ahora - calculado) > dt.timedelta(hours=36)


@router.get("/inertes")
def inertes(conn: ConexionLectura) -> dict:
    """Hojas sin trafico con su clasificacion (regla 22: la UI CONSUME este
    endpoint). Dinero con moneda y NULL como null (reglas 3-4).

    BIDS 01 2.6: ademas first_seen_at / en_espera / archivable_desde por item
    (puerta de antiguedad del archivado, espejo del default del tool) y
    `lotes`: resumen de keyword_archivo_manual (tabla vacia -> [])."""
    conn.row_factory = dict_row
    items = [
        {
            "plataforma": fila["platform"],
            "kind": fila["kind"],
            "texto": etiqueta_entidad(
                kind=fila["kind"],
                name=fila["name"],
                keyword_text=fila["keyword_text"],
                campana=fila["campaign_name"],
            ).hoja,
            "external_id": fila["external_id"],
            "campana": fila["campaign_name"],
            "ad_group": fila["ad_group_name"],
            "clasificacion": fila["clasificacion"],
            "dias_sin_impresiones": fila["dias_sin_impresiones"],
            "ultima_impresion": (
                fila["ultima_impresion"].isoformat() if fila["ultima_impresion"] else None
            ),
            "gasto_90d": _dec_str(fila["gasto_90d"]),
            "moneda": fila["moneda"],
            "ordenes_90d": int(fila["ordenes_90d"]),
            "first_seen_at": (
                fila["first_seen_fecha"].isoformat() if fila["first_seen_fecha"] else None
            ),
            "en_espera": bool(fila["en_espera"]) if fila["en_espera"] is not None else None,
            "archivable_desde": (
                fila["archivable_desde"].isoformat() if fila["archivable_desde"] else None
            ),
        }
        for fila in conn.execute(_SQL_INERTES).fetchall()
    ]
    totales: dict[str, dict[str, int]] = {}
    for item in items:
        por_clase = totales.setdefault(item["plataforma"], {})
        por_clase[item["clasificacion"]] = por_clase.get(item["clasificacion"], 0) + 1
    lotes = [
        {
            "lote": fila["lote"],
            "fecha": fila["primer_intento"].isoformat(),
            "planeado": fila["planeado"],
            "applied": fila["applied"],
            "failed": fila["failed"],
            "repuesto": fila["repuesto"],
        }
        for fila in conn.execute(_SQL_LOTES_INERTES).fetchall()
    ]
    return {"totales": totales, "items": items, "lotes": lotes}


# TEMPORADA 01 A: tarjeta «Temporadas de venta». La lectura del equilibrio es
# la MISMA vista que lee el ciclo (un numero, una fuente) y el motivo de
# abstencion sale del MISMO resolver (goals.motivo_dato_invalido). La columna
# fees_sin_tipo de la vista distingue «cargos de Amazon sin clasificar» del
# resto de sin_margen (diseno, «Punto de equilibrio», «En Ajustes»).
_SQL_EQUILIBRIO_TEMPORADA = """
SELECT platform, ventana_desde, ventana_hasta, venta_total, venta_cubierta,
       cargos_con_orden, cargos_sin_orden, cogs, cobertura, dias_con_venta,
       fees_sin_tipo, margen_neto_pct, ledger_fresco_at, moneda,
       cogs_vigente, cobertura_vigente, margen_vigente_pct
  FROM v_target_margen_plataforma
 WHERE platform = %s::platform
"""

# Columnas de _SQL_EQUILIBRIO_TEMPORADA, para leer la fila por nombre
# (sin el prefijo _SQL_: el test de parseo recorre las constantes _SQL_*).
COLUMNAS_EQUILIBRIO_TEMPORADA = (
    "platform",
    "ventana_desde",
    "ventana_hasta",
    "venta_total",
    "venta_cubierta",
    "cargos_con_orden",
    "cargos_sin_orden",
    "cogs",
    "cobertura",
    "dias_con_venta",
    "fees_sin_tipo",
    "margen_neto_pct",
    "ledger_fresco_at",
    "moneda",
    "cogs_vigente",
    "cobertura_vigente",
    "margen_vigente_pct",
)

# Las razones en llano de «sin equilibrio» (diseno, «En Ajustes»); la de
# sin_margen se especializa abajo con fees_sin_tipo.
RAZON_SIN_EQUILIBRIO = {
    "cobertura_baja": "faltan costos de algunos productos",
    "ledger_rancio": "los datos de ventas tienen más de 3 días",
    "ventana_corta": "hay pocas ventas en los últimos 90 días",
    "sin_margen": "sin margen medido en la ventana",
}


def _pct_un_decimal(valor: Decimal) -> str:
    """El equilibrio siempre con un decimal (45.5, 29.0), como el diseno."""
    return f"{valor.quantize(Decimal('0.1')):.1f}"


def _numero_corto(valor: Decimal) -> str:
    """Meta en puntos sin ceros colgando: 25 -> «25», 22.75 -> «22.75»."""
    return format(valor.normalize(), "f")


def _equilibrio_de_fila(fila: Mapping, hoy: dt.date) -> dict:
    """Fila de v_target_margen_plataforma -> equilibrio y razon en llano.
    Sin equilibrio la razon dice por que (regla 3: None se explica, jamas se
    rellena); fees_sin_tipo > 0 nombra los cargos sin clasificar."""
    medicion = g.medicion_desde_fila(fila)
    motivo = g.motivo_dato_invalido(medicion, hoy)
    if motivo is None:
        return {"equilibrio": medicion.margen_neto_pct, "razon": None}
    if motivo == "sin_margen" and (fila.get("fees_sin_tipo") or 0) > 0:
        razon = "hay cargos de Amazon sin clasificar"
    else:
        razon = RAZON_SIN_EQUILIBRIO.get(motivo, RAZON_SIN_EQUILIBRIO["sin_margen"])
    return {"equilibrio": None, "razon": razon}


def equilibrio_temporada(
    conn: ConexionLectura,
    plataforma: str,
    *,
    meta_hoy: Decimal | None,
    peldano: str | None,
    meta_alta: Decimal | None,
    hoy: dt.date,
) -> dict:
    """Lo que la tarjeta de temporada muestra de UNA plataforma: la frase de
    la meta de hoy, la linea del punto de equilibrio con el costo vigente
    (bloque I), el aviso ROJO cuando la meta vigente o la de alta iguala o
    pasa el equilibrio, y la razon en llano cuando no se puede calcular."""
    sale_de_margen = (
        " (sale de tu margen)"
        if peldano in ("margen_plataforma", "margen_familia", "margen_producto")
        else ""
    )
    frase_meta_hoy = (
        None if meta_hoy is None else f"Hoy tu meta es {_numero_corto(meta_hoy)} %{sale_de_margen}."
    )
    fila = conn.execute(_SQL_EQUILIBRIO_TEMPORADA, (plataforma,)).fetchone()
    resuelto = (
        _equilibrio_de_fila(dict(zip(COLUMNAS_EQUILIBRIO_TEMPORADA, fila, strict=True)), hoy)
        if fila is not None
        else {"equilibrio": None, "razon": RAZON_SIN_EQUILIBRIO["sin_margen"]}
    )
    equilibrio = resuelto["equilibrio"]
    razon = resuelto["razon"]
    linea = (
        None
        if equilibrio is None
        else (
            "Punto de equilibrio con el costo de hoy:"
            f" {_pct_un_decimal(equilibrio)} %. Arriba de ese ACoS pierdes dinero en cada venta."
        )
    )
    nombre = etiqueta(plataforma, "plataforma")
    avisos: list[str] = []
    if equilibrio is not None:
        eq = _pct_un_decimal(equilibrio)
        if meta_alta is not None and meta_alta >= equilibrio:
            meta_txt = _numero_corto(meta_alta)
            avisos.append(
                f"Con una meta de {meta_txt} % en temporada alta, Orbit acepta ventas que"
                f" pierden dinero en {nombre}: las que salen con un ACoS entre tu punto de"
                f" equilibrio, {eq} %, y {meta_txt} %."
            )
        if meta_hoy is not None and meta_hoy >= equilibrio:
            meta_txt = _numero_corto(meta_hoy)
            avisos.append(
                f"Con tu meta de hoy de {meta_txt} %, Orbit ya acepta ventas que pierden"
                f" dinero en {nombre}: las que salen con un ACoS entre tu punto de"
                f" equilibrio, {eq} %, y {meta_txt} %."
            )
    return {
        "equilibrio": None if equilibrio is None else _pct_un_decimal(equilibrio),
        "linea": linea,
        "razon": razon,
        "aviso": " ".join(avisos) or None,
        "frase_meta_hoy": frase_meta_hoy,
        "meta_alta_frase": (
            None if meta_alta is None else f"En temporada alta: {_numero_corto(meta_alta)} %."
        ),
    }


def metas_vigentes_temporada(
    conn: ConexionLectura,
) -> tuple[dict[str, Decimal], dict[str, str | None]]:
    """Meta que gobierna hoy cada plataforma (la MISMA cascada que pinta la
    tarjeta de plataforma) y su peldano. Sin peldano -> la plataforma no
    entra en las metas (regla 3); el peldano si viaja para la frase."""
    metas: dict[str, Decimal] = {}
    peldanos: dict[str, str | None] = {}
    settings = _config_vigente_con_id(conn)[1]
    for plataforma in PLATAFORMAS_MONEDA:
        goal_id, goal = _goal_plataforma_de(conn, plataforma)
        valor, peldano = g.cascada_target_acos_con_procedencia(
            None,
            goal,
            settings,
            None,
            plataforma,
            _target_margen_del_ciclo(conn, plataforma),
        )
        del goal_id
        peldanos[plataforma] = peldano
        if valor is not None:
            metas[plataforma] = valor
    return metas, peldanos


def _goal_plataforma_de(conn: ConexionLectura, plataforma: str) -> tuple[int | None, g.Goal | None]:
    """El goal de plataforma y su id, para la cascada (misma consulta de
    _SQL_GOALS_SETTINGS)."""
    for fila in conn.execute(_SQL_GOALS_SETTINGS).fetchall():
        if fila[1] == "platform":
            goal = _goal_desde_fila(fila[1:13])
            if goal.platform == plataforma:
                return fila[0], goal
    return None, None


# TEMPORADA 01 A, bloque de estado de la baja (diseno, «Uso» 1): los conteos
# salen de decisiones REALES de ciclos LIVE (las de practica jamas se envian
# a Amazon y no pueden confirmarse; hallazgo de la revision), contadas por
# keyword distinta. Sin episodios todavia (bloque B), el bloque de baja
# vigente se acota por su fecha de inicio en el calendario guardado.
_SQL_ESTADO_BAJA_DECISIONES = """
SELECT ultima.ad_entity_id,
       EXISTS (SELECT 1 FROM decision_application da
                WHERE da.decision_id = ultima.decision_id
                  AND da.verify_ok IS TRUE) AS confirmada
  FROM (
    -- La ULTIMA decision de temporada de cada keyword manda: una entrada
    -- confirmada con un ajuste todavia pendiente cuenta como pendiente
    -- (hallazgo de la revision: bool_or miraba cualquier confirmacion
    -- historica y escondia el ajuste).
    SELECT DISTINCT ON (d.ad_entity_id)
           d.ad_entity_id, d.id AS decision_id
      FROM decision d
      JOIN ad_entity e ON e.id = d.ad_entity_id
      JOIN optimizer_cycle c ON c.id = d.cycle_id
     WHERE e.platform = %s::platform
       AND c.mode = 'live'
       AND d.kind = 'bid'
       AND d.inputs->>'motivo' IN ('entra_temporada_baja', 'ajusta_temporada_baja')
       AND (d.decided_at AT TIME ZONE 'UTC')::date >= %s
     ORDER BY d.ad_entity_id, d.decided_at DESC, d.id DESC
  ) ultima
"""

# Bid propio vs heredado y sin tocar: sobre el MISMO universo de keywords
# habilitadas (sin_tocar = keywords elegibles SIN decision de temporada de
# ciclos live en el bloque; hallazgo de la revision: restar decisiones a un
# total de keywords mezclaba unidades y conjuntos).
_SQL_ESTADO_BAJA_KEYWORDS = """
SELECT count(*) AS total,
       count(*) FILTER (WHERE s.current_bid IS NULL AND NOT EXISTS (
           SELECT 1 FROM decision d
             JOIN optimizer_cycle c ON c.id = d.cycle_id
            WHERE d.ad_entity_id = e.id AND c.mode = 'live' AND d.kind = 'bid'
              AND d.inputs->>'motivo' IN ('entra_temporada_baja', 'ajusta_temporada_baja')
              AND (d.decided_at AT TIME ZONE 'UTC')::date >= %s)) AS grupo,
       count(*) FILTER (WHERE NOT EXISTS (
           SELECT 1 FROM decision d
             JOIN optimizer_cycle c ON c.id = d.cycle_id
            WHERE d.ad_entity_id = e.id AND c.mode = 'live' AND d.kind = 'bid'
              AND d.inputs->>'motivo' IN ('entra_temporada_baja', 'ajusta_temporada_baja')
              AND (d.decided_at AT TIME ZONE 'UTC')::date >= %s)) AS sin_tocar
  FROM ad_entity e
  JOIN ad_entity_state s ON s.ad_entity_id = e.id AND s.status = 'ENABLED'
 WHERE e.platform = %s::platform AND e.kind = 'keyword'
"""

# Pausas de Orbit CONFIRMADAS por Amazon y aun vigentes, ancladas a la fila
# live/applied mas reciente de apply_queue (la misma propiedad vigente que
# _SQL_PAUSAS_DE_BAJA del diseno): si el dueno la reactivo (reversa ok o
# reactivacion_manual posterior a la aplicacion) y la volvio a pausar a
# mano en Amazon, esa pausa ya es del dueno y NO se atribuye a Orbit.
# Una fila por keyword aunque acumule pausas.
_SQL_PAUSADAS_DE_BAJA = """
SELECT DISTINCT ON (q.ad_entity_id) q.ad_entity_id,
       COALESCE(e.keyword_text, e.name) AS texto
  FROM apply_queue q
  JOIN decision d ON d.id = q.decision_id
  JOIN optimizer_cycle c ON c.id = d.cycle_id
  JOIN decision_application da ON da.decision_id = d.id AND da.verify_ok IS TRUE
  JOIN ad_entity e ON e.id = q.ad_entity_id
  JOIN ad_entity_state s ON s.ad_entity_id = q.ad_entity_id AND s.status = 'PAUSED'
 WHERE q.platform = %s::platform
   AND q.kind = 'pause' AND q.modo = 'live' AND q.estado = 'applied'
   AND c.mode = 'live'
   AND d.inputs->'caso'->'temporada'->'frontera'->>'fase_hoja' = 'baja'
   AND (d.decided_at AT TIME ZONE 'UTC')::date >= %s
   AND NOT EXISTS (SELECT 1 FROM apply_attempt a
                    WHERE a.decision_id = d.id AND a.tipo = 'reversa'
                    GROUP BY a.decision_id
                    HAVING bool_or(a.resultado = 'ok') OR count(*) >= 3)
   AND NOT EXISTS (SELECT 1 FROM reactivacion_manual r
                    WHERE r.ad_entity_id = q.ad_entity_id
                      AND r.detectada_en > q.applied_at)
   AND NOT (q.ad_entity_id = ANY(%s::bigint[]))
   -- El ancla es la ULTIMA pausa aplicada de la keyword, no la ultima que
   -- pasa los filtros de temporada (hallazgo de la revision: una pausa
   -- aplicada despues sin marca de temporada deja sin efecto a la antigua).
   AND q.id = (SELECT q2.id FROM apply_queue q2
                WHERE q2.ad_entity_id = q.ad_entity_id
                  AND q2.kind = 'pause' AND q2.estado = 'applied'
                ORDER BY q2.applied_at DESC LIMIT 1)
 ORDER BY q.ad_entity_id
"""


def pausadas_de_baja(
    conn: ConexionLectura, plataforma: str, inicio: dt.date, mantener: frozenset[int] | set[int]
) -> list[dict]:
    """Las keywords que Orbit pauso en esta baja y va a reactivar al
    terminar, con su texto para la lista de la tarjeta (el texto visible de
    una keyword es keyword_text). La usa el bloque de estado y el endpoint
    «Mantener pausada» para validar su boton."""
    filas = conn.execute(_SQL_PAUSADAS_DE_BAJA, (plataforma, inicio, sorted(mantener))).fetchall()
    return [{"ad_entity_id": fila[0], "nombre": fila[1] or f"keyword #{fila[0]}"} for fila in filas]


def estado_de_baja(
    conn: ConexionLectura, plataforma: str, config: temporada.ConfigTemporada, hoy: dt.date
) -> dict:
    """Lo que la tarjeta muestra durante una baja ENCENDIDA (el llamador
    comprueba el modo de la plataforma): confirmaciones de Amazon, pendientes
    y sin tocar con su razon en llano, mas la lista de keywords pausadas a
    reactivar. Todo SELECT; sin episodios todavia (bloque B), el bloque de
    baja se acota por la fecha de inicio del calendario."""
    estacion = temporada.resuelve(config, hoy)
    if estacion.fase != "baja":
        return {}
    inicio = estacion.inicio_bloque
    confirmados = pendientes = 0
    for fila in conn.execute(_SQL_ESTADO_BAJA_DECISIONES, (plataforma, inicio)).fetchall():
        if fila[1]:
            confirmados += 1
        else:
            pendientes += 1
    total, grupo, sin_tocar = conn.execute(
        _SQL_ESTADO_BAJA_KEYWORDS, (inicio, inicio, plataforma)
    ).fetchone()
    frase_sin_tocar = f"Sin tocar: {sin_tocar}."
    if grupo:
        # La razon «porque cambiaste tú el bid» llega con las exclusiones
        # del bloque B (temporada_exclusion); la unica fuente real hoy es el
        # bid heredado, y SOLO entre las keywords sin decision: atribuirle
        # el caso sin tocar a una keyword que ya tiene decision es mentir
        # (hallazgo de la revision: grupo lleva el mismo predicado).
        frase_sin_tocar += (
            f" {grupo} usan el bid de su grupo (Orbit no los toca hasta que tengan bid propio)."
        )
    return {
        "fase": "baja",
        "desde": inicio.isoformat(),
        "confirmados": confirmados,
        "pendientes": pendientes,
        "sin_tocar": sin_tocar,
        "frase_sin_tocar": frase_sin_tocar,
        "pausadas": pausadas_de_baja(conn, plataforma, inicio, config.mantener_pausadas),
    }


def resumen_temporada_para_guardado(
    conn: ConexionLectura, settings_antes: Mapping, settings_nuevo: Mapping, hoy: dt.date
) -> temporada.ResumenTemporada | None:
    """«Así queda tu año» para la respuesta del guardado (el mismo resumen
    que la vista previa). None si la config nueva ya no trae temporada."""
    config_antes = temporada.desde_settings(settings_antes)
    config_nueva = temporada.desde_settings(settings_nuevo)
    if config_nueva is None:
        return None
    meta_alta = {
        p: temporada.meta_alta_desde_settings(settings_nuevo, p)
        for p in temporada.PLATAFORMAS_TEMPORADA
    }
    return temporada.resumen(
        config_nueva,
        hoy,
        antes=config_antes,
        metas_hoy=metas_vigentes_temporada(conn)[0],
        meta_alta=meta_alta,
    )


def temporada_formulario(settings: Mapping) -> dict:
    """Lo que la tarjeta prellena: la config GUARDADA, o los valores por
    defecto del formulario (que nunca deciden solos)."""
    guardada = temporada.desde_settings(settings)
    salida = {
        "guardada": guardada is not None,
        "meses": list(temporada.MESES_POR_DEFECTO),
        "fin_anticipado": None,
        "mantener_pausadas": [],
        **temporada.FORMULARIO_POR_DEFECTO,
    }
    if guardada is not None:
        blob = temporada.como_guardada(guardada)
        salida["meses"] = list(blob["meses"])
        salida["fin_anticipado"] = blob["fin_anticipado"]
        salida["mantener_pausadas"] = list(blob["mantener_pausadas"])
        for clave in (
            "reduccion_baja_pct",
            "dias_preparacion",
            "mult_cortes_baja",
            "aumento_presupuesto_alta_pct",
        ):
            salida[clave] = blob[clave]
    return salida


# Goals CON id (la UI los edita via POST /api/ads-optimizer/goals/{id}) y la
# plataforma/nombre de la campana para los de scope campaign (su fila trae
# platform NULL por el CHECK del schema).
_SQL_GOALS_SETTINGS = """
SELECT g.id, g.scope, g.ad_entity_id, g.platform, g.target_acos_pct, g.bid_floor,
       g.bid_ceiling, g.bid_currency, g.harvest_campaign_id, g.harvest_ad_group_id,
       g.harvest_default_bid, g.enabled, g.mode, e.platform, e.name
  FROM ads_optimizer_goal g
  LEFT JOIN ad_entity e ON e.id = g.ad_entity_id
 ORDER BY g.id
"""

# E4: el goal de campana pisa TODO lo de abajo (incluido el margen); la
# pantalla dice a QUIEN pisa segun el peldano que gobierna la plataforma sin
# ese goal. Se deriva de PELDANOS_CASCADA: un peldano sin etiqueta revienta
# al importar (KeyError), no inventa un texto.
PISA_POR_PELDANO: dict[str, str] = {p: ETIQUETAS["pisa"][p] for p in g.PELDANOS_CASCADA}


def _goal_editable(goal_id: int, goal: g.Goal, plataforma, nombre, pisa_a) -> dict:
    """Goal con id y estado VIVO (_goal_estado) para el form de la pantalla.
    Sin campos harvest: no se editan en v1 (E6)."""
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
    """Lo que la pantalla /settings edita, con la verdad al lado (E1): por
    plataforma el target VIGENTE y su peldano (la MISMA cascada del motor,
    cascada_target_acos_con_procedencia, con el aplicado del margen del
    ultimo ciclo), el target manual, el interruptor del margen (E3: clave
    presente = encendido) con su fraccion, el motor de bids (A6: crudo +
    resuelto), los caps y el goal de plataforma; `modo_global` de SOLO
    lectura (E6; ausente -> null, jamas inventado); y los goals con id y,
    en los de campana, a quien pisan (E4)."""
    config_id, settings = _config_vigente_con_id(conn)
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
    config_temporada = temporada.desde_settings(settings)
    meta_alta_form = {
        plataforma: temporada.meta_alta_desde_settings(settings, plataforma)
        for plataforma in temporada.PLATAFORMAS_TEMPORADA
    }
    for plataforma in PLATAFORMAS_MONEDA:
        goal_id, goal = goal_plataforma.get(plataforma, (None, None))
        valor, peldano = g.cascada_target_acos_con_procedencia(
            None, goal, settings, None, plataforma, _target_margen_del_ciclo(conn, plataforma)
        )
        peldanos[plataforma] = peldano
        # A3 lane 5: la fraccion pasa por el lector del motor (ausente =
        # None como antes; corrupta = ValueError y la pagina NO se muestra,
        # igual que target y confianzas).
        fraccion = g.fraccion_desde_settings(settings, plataforma)
        caps = {kind: settings.get(clave_cap(plataforma, kind)) for kind in KINDS_QUOTA}
        plataformas.append(
            {
                "plataforma": plataforma,
                "moneda": PLATAFORMAS_MONEDA[plataforma],
                "target_vigente": {"valor": _dec_str(valor), "peldano": peldano},
                "target_manual_pct": _dec_str(settings.get(g.clave_target_plataforma(plataforma))),
                "margen_habilitado": fraccion is not None,
                "fraccion_margen": _dec_str(fraccion),
                # A3: confianzas resueltas (ausentes = defaults 0.80/0.70).
                "confianza_recorte": _dec_str(
                    g.confianza_recorte_desde_settings(settings, plataforma)
                ),
                "confianza_subida": _dec_str(
                    g.confianza_subida_desde_settings(settings, plataforma)
                ),
                # M.3: interruptor del motor de bids: valor CRUDO (None =
                # ausente = apagado) + resuelto (vive: True = niveles_v3).
                # Clave corrupta = ValueError y la pagina NO se muestra,
                # igual que target/fraccion/confianzas.
                "motor_bid": settings.get(g.clave_bid_politica(plataforma)),
                "motor_bid_vive": (
                    g.politica_bid_desde_settings(settings, plataforma) == g.POLITICA_BID_VIGENTE
                ),
                "caps": {kind: _dec_str(cap) for kind, cap in caps.items()},
                "goal": (_goal_editable(goal_id, goal, plataforma, None, None) if goal else None),
                # TEMPORADA 01 A: meta de alta, modos y equilibrio de la
                # tarjeta «Temporadas de venta» + subida rapida (Motor de bids).
                "meta_alta": _numero_corto(meta_alta_form[plataforma])
                if plataforma in meta_alta_form
                else None,
                "modo_temporada": settings.get(f"ads_temporada_{plataforma}"),
                "recuperacion_modo": settings.get(f"ads_bid_recuperacion_{plataforma}"),
                "recuperacion_memoria_dias": temporada.memoria_recuperacion_desde_settings(
                    settings, plataforma
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
                # TEMPORADA 01 A, bloque de estado de la baja (vacio fuera de
                # baja y CON LA TEMPORADA APAGADA: sin modo no hay estado
                # operativo ni acciones, hallazgo de la revision): conteos de
                # decisiones de temporada y keywords pausadas a reactivar.
                "temporada_estado": (
                    estado_de_baja(conn, plataforma, config_temporada, hoy_temporada)
                    if config_temporada is not None
                    and settings.get(f"ads_temporada_{plataforma}") in temporada.MODOS_MOTOR
                    else None
                ),
            }
        )
    estado_temporada = None
    if config_temporada is not None and any(
        settings.get(f"ads_temporada_{p}") in temporada.MODOS_MOTOR
        for p in temporada.PLATAFORMAS_TEMPORADA
    ):
        estacion_hoy = temporada.resuelve(config_temporada, hoy_temporada)
        estado_temporada = {
            "fase": estacion_hoy.fase,
            "desde": (
                estacion_hoy.inicio_bloque.isoformat() if estacion_hoy.fase == "baja" else None
            ),
        }
    topada_vigente = temporada.campana_topada_desde_settings(settings)
    return {
        "config_version_id": config_id,
        "modo_global": settings.get(g.CLAVE_SETTING_MODO),
        "advertencia_respaldo": ADVERTENCIA_RESPALDO,
        # TEMPORADA 01 A: tarjeta «Temporadas de venta» (prefill de la
        # tarjeta + criterio de campana topada resuelto + estado de la baja).
        "temporada_form": temporada_formulario(settings),
        "campana_topada": {
            "pct": _numero_corto(topada_vigente.pct * 100),
            "dias": topada_vigente.dias,
        },
        "temporada_estado": estado_temporada,
        # S.2: apagador de precios (tolerante: config vieja sin claves =
        # null/{} y la pagina se muestra; la corrida es la estricta).
        "precio_modo_global": settings.get("precio_modo_global"),
        "precio_modo_universo": settings.get("precio_modo_universo") or {},
        # Los cuatro universos del registro (app/estimacion_universo.py):
        # la plantilla los lista aunque no tengan entrada (sin entrada = off).
        "precio_universos": [
            "amazon_mx/fba",
            "amazon_mx/fbm",
            "amazon_us/fbm",
            "meli/meli",
        ],
        "resumen_acos_objetivo": metas_resumen_desde_settings(settings),
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
                    # T.1: sin peldano no hay a quien pisar (sin KeyError).
                    and peldanos[plataforma_campana] is not None
                )
                else None,
            )
            for goal_id, goal, plataforma_campana, nombre in goals
        ],
    }


@router.get("/keywords-danadas")
def keywords_danadas(conn: ConexionLectura, plataforma: str | None = None) -> dict:
    """Keywords danadas del mercado (BIDS 02, P.3b). Funcion delgada: delega
    en `pantalla_danadas.lee_danadas` (SOLO SELECT) y pasa su dict tal cual.
    Sin mercado se mira amazon_mx; mercado ajeno es 422."""
    from app import pantalla_danadas

    mercado = plataforma or "amazon_mx"
    if mercado not in PLATAFORMAS_MONEDA:
        raise HTTPException(status_code=422, detail="plataforma fuera de vocabulario")
    return pantalla_danadas.lee_danadas(conn, plataforma=mercado).como_dict()


@router.get("/ruido")
def ruido(conn: ConexionLectura, plataforma: str | None = None) -> dict:
    """Tablero de ruido del mercado (BIDS 02, P.5). Funcion delgada: delega
    en `pantalla_ruido.lee_ruido` (SOLO SELECT) y pasa su dict tal cual.
    Sin mercado se mira amazon_mx; mercado ajeno es 422."""
    from app import pantalla_ruido

    mercado = plataforma or "amazon_mx"
    if mercado not in PLATAFORMAS_MONEDA:
        raise HTTPException(status_code=422, detail="plataforma fuera de vocabulario")
    return pantalla_ruido.lee_ruido(
        conn, plataforma=mercado, hoy=_hoy_pantalla(mercado)
    ).como_dict()


@router.get("/donde-poner-el-dinero")
def donde_poner_el_dinero(conn: ConexionLectura, plataforma: str | None = None) -> dict:
    """Donde poner el dinero del mercado (BIDS 02, P.1). Funcion delgada:
    delega en `pantalla_dinero.lee_dinero` (SOLO SELECT) y pasa su dict tal
    cual. Sin mercado se mira amazon_mx; mercado ajeno es 422."""
    from app import pantalla_dinero

    mercado = plataforma or "amazon_mx"
    if mercado not in PLATAFORMAS_MONEDA:
        raise HTTPException(status_code=422, detail="plataforma fuera de vocabulario")
    return pantalla_dinero.lee_dinero(
        conn, plataforma=mercado, hasta=_hoy_pantalla(mercado) - dt.timedelta(days=1)
    ).como_dict()


@router.get("/productos-sin-clics")
def productos_sin_clics(conn: ConexionLectura, plataforma: str | None = None) -> dict:
    """Productos que venden y casi no reciben clics (BIDS 02, P.4).
    Funcion delgada: delega en `pantalla_productos.lee_productos` (SOLO
    SELECT) y agrega la frase de clics fuera de Amazon por impulso y las
    decisiones por producto del ultimo ciclo (AUTO-03 E.1,
    `pantalla_decisiones_producto`) y las subfamilias conocidas que el
    campo Subfamilia sugiere (CLARIDAD 01 D.1). Sin mercado se mira
    amazon_mx; mercado ajeno es 422."""
    from app import fabrica_web, pantalla_decisiones_producto, pantalla_productos

    mercado = plataforma or "amazon_mx"
    if mercado not in PLATAFORMAS_MONEDA:
        raise HTTPException(status_code=422, detail="plataforma fuera de vocabulario")
    base = pantalla_productos.lee_productos(
        conn, plataforma=mercado, hoy=_hoy_pantalla(mercado)
    ).como_dict()
    for impulso in base["impulsos"]:
        impulso["frase_fuera_de_amazon"] = pantalla_productos.frase_fuera_de_amazon(
            impulso["clics_fuera_de_amazon"], impulso["clics"]
        )
    decisiones = pantalla_decisiones_producto.lee_decisiones_producto(conn, mercado).como_dict()
    subfamilias = fabrica_web.tipos_producto(conn, mercado)
    conn.commit()
    return {
        "plataforma": mercado,
        **base,
        "decisiones_producto": decisiones,
        "subfamilias": subfamilias,
    }


@router.get("/productos/simula-pausa")
def productos_simula_pausa(
    conn: ConexionLectura,
    platform: str,
    clics: str | None = None,
    gasto: str | None = None,
) -> dict:
    """AUTO-03 H.8: que pausaria la pausa por producto con los limites
    vigentes o con `clics`/`gasto` (sin guardarlos), y el costo de
    equivocarse sobre la ventana anterior. Rol de lectura, no escribe en la
    cola. Un limite bajo el piso es 422 con el minimo."""
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
    conn.commit()
    return simulacion.como_json()


@router.get("/productos/pausa")
def productos_pausa(conn: ConexionLectura, platform: str) -> dict:
    """AUTO-03 I.8: modo vigente de la pausa por producto (off, shadow o
    live, de la ultima config_version), los limites vigentes, si las dos
    claves de limites estan guardadas (sin ellas no se enciende) y la
    config_version_id sobre la que la pantalla enciende o regresa a
    practica. Rol de lectura, solo SELECT."""
    from app import pantalla_decisiones_producto
    from app.optimizer import productos

    if platform not in productos.PLATAFORMAS:
        raise HTTPException(status_code=422, detail="plataforma sin decisiones por producto")
    estado = pantalla_decisiones_producto.estado_pausa(conn, platform)
    conn.commit()
    return estado


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
    """Busquedas que gastan sin vender (BIDS 02, P.6). Funcion
    delgada: delega en `pantalla_busquedas.lee_busquedas` (SOLO
    SELECT). Sin mercado se mira amazon_mx; mercado ajeno es 422."""
    from app import pantalla_busquedas

    mercado = plataforma or "amazon_mx"
    if mercado not in PLATAFORMAS_MONEDA:
        raise HTTPException(status_code=422, detail="plataforma fuera de vocabulario")
    datos = pantalla_busquedas.lee_busquedas(
        conn, plataforma=mercado, hoy=_hoy_pantalla(mercado)
    ).como_dict()
    # R13-K: la plantilla de la pagina imprime el gasto tal cual; sale
    # de aqui con dos decimales, como el de gasto_sin_venta.
    for clave in ("asins", "palabras"):
        for fila in datos[clave]:
            fila["gasto"] = _dinero_2(fila["gasto"])
    return datos
