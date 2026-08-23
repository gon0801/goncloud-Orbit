"""Hygiene PURO del optimizador (ORBIT 03, task 2.3).

Decide NEGATIVE_EXACT y HARVEST sobre los terminos YA COLAPSADOS de
`app.optimizer.windows` (`TerminosCortes`): cero IO en las decisiones (no
hay conn ni now() escondido; psycopg solo aparece como anotacion). El
orquestador (3.1) obtiene terminos_cortes, resuelve el goal (la config de
harvest llega como ConfigHarvest, task 2.4) y llama decide_hygiene por
entidad; la persistencia (`decision`) es de 3.1, no de aqui. La UNICA IO
del modulo es keywords_campana_destino (LECTURA) para el dedupe del harvest.

Reglas selladas (plans/orbit-03.md task 2.3 + diseno v2; el diseno manda):

- NEGATIVE_EXACT (kind 'negative'): orders==0 AND clicks>=20 AND cost>=
  umbral por plataforma {amazon_us: 8 USD, amazon_mx: 130 MXN}, UMBRALES
  INCLUSIVOS (>=), sobre la ventana de CORTES que trae TerminosCortes
  (madura: termina en decided_at - 10d, regla 6; el trigger
  decision_madurez_corte es la segunda capa). Sin dinero: kind negative
  exige value_currency/new_value NULL en el esquema (un negativo no mueve
  bid). El camino negative NO re-valida moneda (no mueve dinero y el umbral
  de cost se compara en la moneda del termino, sellada contra la plataforma
  por el trigger metric_moneda_de_plataforma en DB).
- HARVEST (kind 'harvest'): orders>=2 AND ACoS <= min(35%, target_acos_pct).
  La comparacion es <= INCLUSIVA por MULTIPLICACION exacta
  (cost * 100 <= tope_pct * ad_revenue): PROHIBIDO dividir (evita la
  division por cero y mantiene Decimal exacto). ad_revenue=0 solo pasa con
  cost=0 (cost*100 <= 0 exige cost==0; costo negativo no existe por schema):
  ventas gratis se harvestean, dinero quemado sin venta jamas. new_value =
  default_bid del goal con SU moneda (regla 4: el bid lleva moneda); el bid
  NO se redondea (la presentacion la decide el apply de PR2).
- ASIN-LIKE (`AgregadoTermino.is_asin_like`) se salta SIEMPRE, para AMBOS
  kinds (decision sellada del lead): la regla own-ASIN del diseno v2 (negativizar
  un ASIN propio es apagarte a ti mismo) y una keyword con texto ASIN no es
  harvesteable (matchearia cualquier producto, no intencion de busqueda).
- COMPLETITUD: la unidad es la ENTIDAD (sellado 2.1): terminos.completa False
  (<7 fechas de entidad) -> TODOS sus terminos skip 'entidad_incompleta'. El
  termino NO exige fechas propias: la evidencia por termino la dan sus
  umbrales de clicks/cost (exigirlas mataria los negativos long-tail).

Semantica de None (regla 3 del repo: dato faltante != cero; sellada en 1.5):

- orders=None es DESCONOCIDO, no 0: jamas NEGATIVE por dato faltante (un
  corte es accion con reversa cara; sin el dato no hay decision).
- clicks/cost None en el camino negative, cost/ad_revenue None en el camino
  harvest -> skip 'dato_faltante' (umbrales o ACoS no evaluables).

Config de harvest: incompleta (None, o cualquiera de campaign_id /
ad_group_id / default_bid / moneda ausente o vacio, o default_bid <= 0) ->
skip 'harvest_sin_config' CON MOTIVO, jamas placeholder (el CHECK
goal_harvest_completo lo impide en DB; aqui es la capa pura, que 3.1 puede
llamar con la config resuelta de un goal que la DB ya valido).

Dedupe del harvest: search_term ya presente como keyword EXACT en la campana
destino (keywords_existentes, que 3.1 llena via keywords_campana_destino) ->
skip 'harvest_duplicado'. match_type='EXACT' es decision sellada: el harvest
crea keyword EXACT, y una PHRASE con el mismo texto NO es duplicado (el
match semanticamente distinto no reemplaza al que se va a crear).

Coherencia de moneda del termino (defensa en profundidad, igual que 2.2: el
trigger metric_moneda_de_plataforma ya la sella en DB):
metric_currency debe ser 'USD' si platform=amazon_us, 'MXN' si amazon_mx;
si no coincide o es None -> skip 'moneda_incoherente' (fail-closed).

Orden de evaluacion POR TERMINO (precedencia sellada; el primer bloqueo es
el motivo que se reporta, auditable en 3.1):

(1) entidad incompleta -> TODOS los terminos skip 'entidad_incompleta'.
(2) is_asin_like -> skip 'asin_like'.
(3) orders None -> skip 'orders_desconocido'.
(4) orders==0 -> camino NEGATIVE: clicks o cost None -> skip 'dato_faltante';
    clicks>=20 AND cost>= umbral -> kind 'negative' (sin dinero); si no ->
    no-op 'sin_umbral_negative'.
(5) orders==1 -> no-op 'sin_banda' (ni negative ni harvest posibles: hay
    venta, y una sola venta no habilita harvest).
(6) orders>=2 -> camino HARVEST: cost o ad_revenue None -> skip
    'dato_faltante'; ACoS > tope -> no-op 'acos_sobre_tope'; config
    incompleta -> skip 'harvest_sin_config'; moneda de la CONFIG distinta a
    la de la plataforma -> skip 'harvest_moneda_incoherente' (la unica
    salida de hygiene que crea dinero en PR2; el esquema NO sella
    goal.bid_currency contra platform, un goal sembrado a mano con moneda
    cruzada fluye hasta un bid real si esto no se corta aqui); search_term
    en keywords_existentes -> skip 'harvest_duplicado'; moneda del termino
    incoherente con la plataforma -> skip 'moneda_incoherente'; si todo
    pasa -> kind 'harvest' con new_value=default_bid y SU moneda.

`motivo` es un vocabulario CERRADO (constantes MOTIVO_*; los tests lo fijan
literal): 3.1 lo persiste en decision/optimizer_cycle. Un resultado CON kind
lleva la ventana de cortes de TerminosCortes y el observed_at_max DEL
TERMINO (insumo de decision.data_observed_at); un no-op/skip no lleva
ventana (misma semantica que ResultadoBid: nada decidio, nada congela).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from app.optimizer.bid import PLATAFORMAS_MONEDA
from app.optimizer.windows import TerminosCortes

if TYPE_CHECKING:
    import psycopg

# ---------------------------------------------------------------------------
# Constantes selladas (fuente: plans/orbit-03.md task 2.3 + diseno v2)
# ---------------------------------------------------------------------------

NEGATIVE_CLICKS_MIN = 20  # inclusivo (>=)
NEGATIVE_COST_MIN: dict[str, Decimal] = {
    "amazon_us": Decimal("8"),
    "amazon_mx": Decimal("130"),
}

HARVEST_ORDERS_MIN = 2  # inclusivo (>=)
HARVEST_ACOS_TOPE_FIJO_PCT = Decimal("35")  # tope = min(35, target_acos_pct)

# Motivo de decision (vocabulario cerrado; los tests lo fijan literal)
MOTIVO_NEGATIVE = "negative_umbral"
MOTIVO_HARVEST = "harvest_umbral"

# Motivo de skip/no-op (vocabulario cerrado; los tests lo fijan literal)
MOTIVO_ENTIDAD_INCOMPLETA = "entidad_incompleta"
MOTIVO_ASIN_LIKE = "asin_like"
MOTIVO_ORDERS_DESCONOCIDO = "orders_desconocido"
MOTIVO_DATO_FALTANTE = "dato_faltante"
MOTIVO_SIN_UMBRAL_NEGATIVE = "sin_umbral_negative"
MOTIVO_SIN_BANDA = "sin_banda"
MOTIVO_ACOS_SOBRE_TOPE = "acos_sobre_tope"
MOTIVO_HARVEST_SIN_CONFIG = "harvest_sin_config"
MOTIVO_HARVEST_DUPLICADO = "harvest_duplicado"
MOTIVO_HARVEST_MONEDA_INCOHERENTE = "harvest_moneda_incoherente"
MOTIVO_MONEDA_INCOHERENTE = "moneda_incoherente"

_CIEN = Decimal("100")

# ---------------------------------------------------------------------------
# Estructuras (config que llega resuelta + resultado auditable)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConfigHarvest:
    """Config de harvest del goal, ya resuelta por el orquestador (2.4/3.1):
    harvest_campaign_id / harvest_ad_group_id son external_id TEXT y
    default_bid lleva SU moneda (bid_currency del goal; regla 4: la campana
    destino esta en la misma plataforma). Incompleta -> la capa pura salta
    CON MOTIVO, jamas placeholder."""

    campaign_id: str | None
    ad_group_id: str | None
    default_bid: Decimal | None
    moneda: str | None


@dataclass(frozen=True)
class ResultadoTermino:
    """Resultado de decidir UN termino. `kind` None = no-op/skip con `motivo`
    del vocabulario cerrado. kind 'negative' no lleva dinero (el esquema exige
    value_currency NULL); kind 'harvest' lleva new_value=default_bid con la
    moneda del goal. Con kind: window_* de TerminosCortes (ventana madura de
    cortes) y data_observed_at = observed_at_max DEL TERMINO; sin kind van en
    None (nada decidio, nada congela)."""

    search_term: str
    kind: str | None  # 'negative' | 'harvest' | None (no-op/skip)
    motivo: str | None
    new_value: Decimal | None
    value_currency: str | None
    window_start: dt.date | None
    window_end: dt.date | None
    data_observed_at: dt.datetime | None


# ---------------------------------------------------------------------------
# Motor (PURO: cero IO en las decisiones)
# ---------------------------------------------------------------------------


def _config_completa(config: ConfigHarvest | None) -> bool:
    """Los tres campos del goal van juntos o no van (CHECK
    goal_harvest_completo en DB; aqui es la capa pura): cualquier ausente,
    cadena vacia o bid no positivo deja la config incompleta."""
    if config is None:
        return False
    return (
        bool(config.campaign_id)
        and bool(config.ad_group_id)
        and config.default_bid is not None
        and config.default_bid > 0
        and bool(config.moneda)
    )


def _skip(search_term: str, motivo: str) -> ResultadoTermino:
    """Resultado sin kind: no lleva dinero ni ventana (misma semantica que
    ResultadoBid: nada decidio, nada congela)."""
    return ResultadoTermino(search_term, None, motivo, None, None, None, None, None)


def decide_hygiene(
    *,
    platform: str,
    terminos: TerminosCortes,
    target_acos_pct: Decimal,
    config_harvest: ConfigHarvest | None,
    keywords_existentes: frozenset[str],
) -> tuple[ResultadoTermino, ...]:
    """Decide negative/harvest para CADA termino de la entidad, puro y
    determinista. Un resultado por termino, en el orden de `terminos.terminos`
    (el del SQL: ORDER BY search_term). Ver el docstring del modulo para el
    orden de evaluacion sellado y la semantica de None."""
    if platform not in PLATAFORMAS_MONEDA:
        raise ValueError(
            f"plataforma fuera del vocabulario sellado {{amazon_us, amazon_mx}}: {platform!r}"
        )
    moneda = PLATAFORMAS_MONEDA[platform]
    umbral_cost = NEGATIVE_COST_MIN[platform]
    tope_pct = min(HARVEST_ACOS_TOPE_FIJO_PCT, target_acos_pct)

    if not terminos.completa:
        # Unidad de completitud sellada: la ENTIDAD. Cortar/pausar exige
        # madurez (regla 6): NINGUN termino de una entidad inmadura decide.
        return tuple(_skip(t.search_term, MOTIVO_ENTIDAD_INCOMPLETA) for t in terminos.terminos)

    resultados: list[ResultadoTermino] = []
    for t in terminos.terminos:
        # (2) ASIN-like: SIEMPRE skip, ambos kinds (decision sellada del lead)
        if t.is_asin_like:
            resultados.append(_skip(t.search_term, MOTIVO_ASIN_LIKE))
            continue
        # (3) orders None es DESCONOCIDO, no 0: jamas un corte por dato faltante
        if t.orders is None:
            resultados.append(_skip(t.search_term, MOTIVO_ORDERS_DESCONOCIDO))
            continue
        if t.orders == 0:
            # (4) camino NEGATIVE
            if t.clicks is None or t.cost is None:
                motivo = MOTIVO_DATO_FALTANTE
            elif t.clicks >= NEGATIVE_CLICKS_MIN and t.cost >= umbral_cost:
                resultados.append(
                    ResultadoTermino(
                        search_term=t.search_term,
                        kind="negative",
                        motivo=MOTIVO_NEGATIVE,
                        new_value=None,  # un negativo no mueve dinero (esquema: NULL)
                        value_currency=None,
                        window_start=terminos.window_start,
                        window_end=terminos.window_end,
                        data_observed_at=t.observed_at_max,
                    )
                )
                continue
            else:
                motivo = MOTIVO_SIN_UMBRAL_NEGATIVE
        elif t.orders == 1:
            # (5) ni negative (hay venta) ni harvest (una sola no alcanza)
            motivo = MOTIVO_SIN_BANDA
        else:
            # (6) camino HARVEST (orders >= 2)
            if t.cost is None or t.ad_revenue is None:
                motivo = MOTIVO_DATO_FALTANTE
            elif t.cost * _CIEN > tope_pct * t.ad_revenue:  # ACoS > tope (sin dividir)
                motivo = MOTIVO_ACOS_SOBRE_TOPE
            elif not _config_completa(config_harvest):
                motivo = MOTIVO_HARVEST_SIN_CONFIG
            elif config_harvest.moneda != moneda:
                motivo = MOTIVO_HARVEST_MONEDA_INCOHERENTE
            elif t.search_term in keywords_existentes:
                motivo = MOTIVO_HARVEST_DUPLICADO
            elif t.metric_currency != moneda:
                motivo = MOTIVO_MONEDA_INCOHERENTE
            else:
                resultados.append(
                    ResultadoTermino(
                        search_term=t.search_term,
                        kind="harvest",
                        motivo=MOTIVO_HARVEST,
                        new_value=config_harvest.default_bid,  # sin quantize (apply: PR2)
                        value_currency=config_harvest.moneda,
                        window_start=terminos.window_start,
                        window_end=terminos.window_end,
                        data_observed_at=t.observed_at_max,
                    )
                )
                continue
        resultados.append(_skip(t.search_term, motivo))
    return tuple(resultados)


# ---------------------------------------------------------------------------
# Parte con IO (SOLO lecturas; el conn llega por parametro)
# ---------------------------------------------------------------------------

# Keywords EXACT de la campana destino, via ad_entity (el comentario de la
# tabla declara que keyword_text/match_type existen PARA este duplicate-check,
# sin parsear payloads). match_type='EXACT' sellado: el harvest crea keyword
# EXACT y una PHRASE con el mismo texto NO es duplicado. La campana se
# direcciona por external_id (harvest_campaign_id del goal) recorriendo
# keyword -> ad_group -> campaign por parent_id.
_SQL_KEYWORDS_CAMPANA = """
SELECT k.keyword_text
  FROM ad_entity k
  JOIN ad_entity ag ON ag.id = k.parent_id AND ag.kind = 'ad_group'
  JOIN ad_entity c ON c.id = ag.parent_id AND c.kind = 'campaign'
 WHERE k.kind = 'keyword' AND k.match_type = 'EXACT'
   AND k.platform = %s::platform
   AND c.external_id = %s
"""


def keywords_campana_destino(
    conn: psycopg.Connection, platform: str, harvest_campaign_id: str
) -> frozenset[str]:
    """Textos de las keywords EXACT de la campana manual destino (LECTURA).
    Alimenta keywords_existentes de decide_hygiene. Campana sin keywords ->
    frozenset vacio (ausencia declarada, regla 3: el dedupe no bloquea nada)."""
    filas = conn.execute(_SQL_KEYWORDS_CAMPANA, (platform, harvest_campaign_id)).fetchall()
    return frozenset(fila[0] for fila in filas)
