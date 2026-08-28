"""Bid engine PURO del optimizador (ORBIT 03, task 2.2).

Decide SOBRE los agregados ya colapsados de `app.optimizer.windows`
(`AgregadoMetricas`): cero IO (no importa psycopg, no conn, no now()
escondido). El orquestador (3.1) obtiene las ventanas, resuelve el goal
(target/floor/ceiling, task 2.4) y llama `decide_bid` por entidad; la
persistencia (`decision`, clamps del esquema) es de 3.1, no de aqui.

Reglas selladas (docs/traspaso/ADS_OPTIMIZER_V2_DESIGN.md reglas 1-5; el
diseno manda):

- PAUSE (kind 'pause') sobre el agregado de la ventana de CORTES: un pause
  ES un corte (regla 6; el trigger `decision_madurez_corte` exige
  window_end <= decided_at - 10d; decidirlo con la ventana de bids seria
  rechazado por la base -- 2.1 lo probo). Umbrales INCLUSIVOS (>=):
  orders=0 AND clicks>= umbral_pause (CORTES 01 1.3: llega RESUELTO por
  parametro -- adaptativo por producto con piso legacy 100, resuelto por
  cortes.umbral_corte en cycle.py; el replay lee el congelado) AND cost>=
  {amazon_us: 40 USD, amazon_mx: 500 MXN} (CORTES 03, dueno 2026-08-28;
  antes 25 / 12 USD / 200 MXN).
- Bandas (kind 'bid') sobre el agregado de la ventana de BIDS:
  * -25% si ACoS > 1.35x target AND orders>=1 (estricto >)
  * -12% si ACoS > 1.15x target (sin condicion de orders)
  * +15% si ACoS < 0.85x target AND orders>=3 (estricto <)
- ACoS = cost / ad_revenue COMPLETO (halo incluido; CONTEXTO.md manda):
  `revenue_same_sku` solo atribuye, JAMAS entra al ACoS. La comparacion es
  por MULTIPLICACION exacta (`cost > mult * target_pct / 100 * ad_revenue`):
  PROHIBIDO dividir -- evita la division por cero (ad_revenue=0 con cost>0
  dispara la banda de baja) y mantiene Decimal exacto. target llega como
  pct (ej 25).
- Precedencia EXPLICITA: PAUSE gana a cualquier ajuste; -25 gana a -12;
  -12 y +15 son mutuamente excluyentes (0.85 < 1.15 en aritmetica exacta).
- Clamps: factor por decision a [-30%, +20%]; resultado a [floor, ceiling].
  El CAMBIO FINAL (new - bid_actual) tambien debe obedecer el clamp por
  decision: con el bid ya fuera de [floor, ceiling] el clamp de resultado
  puede invertir la direccion (un -25% que SUBE al floor, un +15% que BAJA
  al ceiling) -- ese ajuste NO se emite (no-op 'rango_bloquea_ajuste'; el
  diseno exige cambio en [-30%, +20%] Y resultado en [floor, ceiling], y no
  existe valor que cumpla ambos). Despues de todo, |new - bid_actual| <
  0.01 (estricto) -> no-op. `ResultadoBid.factor` reporta la banda ANTES de
  los clamps. El bid NO se redondea (sin quantize): la presentacion la
  decide el apply de PR2.

Semantica de None (regla 3 del repo: dato faltante != cero):

- orders=None es DESCONOCIDO, no 0: jamas PAUSE por orders None; tampoco
  satisface orders>=1 ni >=3.
- clicks/cost None -> no PAUSE. cost o ad_revenue None -> no banda (ACoS
  desconocido).
- bid_actual None -> no se puede ajustar (skip con motivo); bid_actual <= 0
  es dato roto (skip 'bid_actual_invalido'). PAUSE no lo necesita. Para kind
  'bid', `bid_moneda` debe coincidir con la moneda del agregado (mismo
  criterio que el agregado).
- Ventana incompleta (`completa` False, <7 fechas): ESA ventana no decide
  -- pause exige `cortes.completa`, bandas exigen `bids.completa`, cada una
  con su motivo. ASIMETRIA INTENCIONAL: cortes incompleto NO impide evaluar
  bandas sobre bids (ventanas independientes; si bids se bloqueara por
  cortes, un corte inmaduro apagaria tambien los bids ya maduros).
- Agregados None (sin observaciones) -> skip con motivo.

Coherencia de moneda (defensa en profundidad: el trigger
`metric_moneda_de_plataforma` ya la sella en DB): `metric_currency` del
agregado debe ser 'USD' si platform=amazon_us, 'MXN' si amazon_mx; si no
coincide o es None -> skip con motivo (fail-closed).

Vocabulario cerrado (ValueError, ruidoso y temprano): plataforma fuera de
{amazon_us, amazon_mx}; floor > ceiling (el CHECK `goal_piso_bajo_techo` lo
impide en DB; aqui es defensa).

`motivo` es un vocabulario CERRADO (constantes MOTIVO_*; los tests lo fijan
literal): 3.1 lo persiste en decision/optimizer_cycle. En un no-op, el
motivo es la PRIMERA guarda que bloqueo en orden de evaluacion (pause antes
que bids); un no-op no lleva ventana: ninguna decidio (window_start/end y
data_observed_at en None).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from app.optimizer.cortes import LEGACY_PAUSE
from app.optimizer.windows import AgregadoMetricas

# ---------------------------------------------------------------------------
# Constantes selladas (fuente: diseno v2, reglas 1-5; el diseno manda)
# ---------------------------------------------------------------------------

PLATAFORMAS_MONEDA: dict[str, str] = {"amazon_us": "USD", "amazon_mx": "MXN"}

# CORTES 01 (1.3): el umbral de clicks del PAUSE ya NO vive aqui. Llega
# RESUELTO por parametro (cortes.umbral_corte(evidencia del grupo, 'pause')
# en cycle.py; el motor sigue puro). El default es el LEGACY VIGENTE con UNA
# fuente (cortes.py; 100 desde CORTES 03): los callers que no optimizan por
# producto (replay de filas historicas sin inputs.corte) rejuegan con el
# default VIGENTE, NO con el historico -- para las pauses reales pre-CORTES
# (119 clicks / 45.80 USD) el replay sigue reproduciendo; una fila
# hipotetica de < 100 clicks ya no (efecto documentado en CORTES 03;
# decision de compat pendiente del lead). Mismo patron que hygiene con
# negative (1.2).
PAUSE_COST_MIN: dict[str, Decimal] = {  # CORTES 03 (dueno 2026-08-28; antes 12/200)
    "amazon_us": Decimal("40"),
    "amazon_mx": Decimal("500"),
}

MULT_BAJA_FUERTE = Decimal("1.35")  # ACoS > 1.35x target (estricto)
MULT_BAJA_SUAVE = Decimal("1.15")  # ACoS > 1.15x target (estricto)
MULT_SUBIDA = Decimal("0.85")  # ACoS < 0.85x target (estricto)
FACTOR_BAJA_FUERTE = Decimal("-0.25")
FACTOR_BAJA_SUAVE = Decimal("-0.12")
FACTOR_SUBIDA = Decimal("0.15")
ORDERS_MIN_BAJA_FUERTE = 1
ORDERS_MIN_SUBIDA = 3

CLAMP_FACTOR_MIN = Decimal("-0.30")  # clamp por decision
CLAMP_FACTOR_MAX = Decimal("0.20")
MIN_DELTA_ABSOLUTO = Decimal("0.01")  # |new - bid_actual| < 0.01 -> no-op (estricto)

# Motivo de decision (vocabulario cerrado; los tests lo fijan literal)
MOTIVO_PAUSE = "pause_umbral"
_MOTIVO_BANDA: dict[Decimal, str] = {
    FACTOR_BAJA_FUERTE: "banda_menos_25",
    FACTOR_BAJA_SUAVE: "banda_menos_12",
    FACTOR_SUBIDA: "banda_mas_15",
}

# Motivo de skip/no-op (vocabulario cerrado; los tests lo fijan literal)
MOTIVO_PAUSE_CORTES_INCOMPLETO = "pause_cortes_incompleto"
MOTIVO_PAUSE_MONEDA_INVALIDA = "pause_moneda_agregado_invalida"
MOTIVO_PAUSE_ORDERS_DESCONOCIDO = "pause_orders_desconocido"
MOTIVO_PAUSE_CLICKS_COST_DESCONOCIDOS = "pause_clicks_o_cost_desconocidos"
MOTIVO_BIDS_SIN_OBSERVACIONES = "bids_sin_observaciones"
MOTIVO_BIDS_INCOMPLETO = "bids_incompleto"
MOTIVO_BIDS_MONEDA_INVALIDA = "bids_moneda_agregado_invalida"
MOTIVO_ACOS_DESCONOCIDO = "acos_desconocido"
MOTIVO_RANGO_BLOQUEA_AJUSTE = "rango_bloquea_ajuste"
MOTIVO_BID_ACTUAL_INVALIDO = "bid_actual_invalido"
MOTIVO_BID_ACTUAL_AUSENTE = "bid_actual_ausente"
MOTIVO_BID_MONEDA_INVALIDA = "bid_moneda_invalida"
MOTIVO_SIN_BANDA = "sin_banda"
MOTIVO_DELTA_BAJO_UMBRAL = "delta_bajo_umbral"

_CIEN = Decimal("100")
_UNO = Decimal("1")


# ---------------------------------------------------------------------------
# Resultado auditable
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResultadoBid:
    """Resultado de decidir UNA entidad. `kind` None = no-op/skip con
    `motivo` del vocabulario cerrado. `factor` es la banda aplicada ANTES de
    los clamps (-0.25/-0.12/0.15). `window_*` y `data_observed_at` vienen del
    agregado QUE decidio (cortes para pause, bids para bid; insumo de
    decision.data_observed_at); en un no-op nada decidio, van en None.
    `value_currency` lleva moneda solo en kind 'bid' (el esquema exige
    value_currency NULL en pause: un pause no mueve dinero)."""

    kind: str | None  # 'pause' | 'bid' | None (no-op/skip)
    motivo: str | None
    old_value: Decimal | None
    new_value: Decimal | None
    value_currency: str | None
    factor: Decimal | None
    window_start: dt.date | None
    window_end: dt.date | None
    data_observed_at: dt.datetime | None


# ---------------------------------------------------------------------------
# Motor
# ---------------------------------------------------------------------------


def _factor_banda(agregado: AgregadoMetricas, target_acos_pct: Decimal) -> Decimal | None:
    """Factor de banda que dispara sobre el agregado de BIDS (o None).

    ACoS COMPLETO (cost / ad_revenue; `revenue_same_sku` jamas) comparado por
    multiplicacion exacta: PROHIBIDO dividir (evita division por cero y
    mantiene Decimal exacto). orders None no satisface ningun orders>=N
    (regla 3). Precedencia explicita: -25 antes que -12; +15 excluido de -12
    por aritmetica exacta (0.85 < 1.15)."""
    costo = agregado.cost
    ingreso = agregado.ad_revenue
    orders = agregado.orders
    target = target_acos_pct / _CIEN
    if (
        costo > MULT_BAJA_FUERTE * target * ingreso
        and orders is not None
        and orders >= ORDERS_MIN_BAJA_FUERTE
    ):
        return FACTOR_BAJA_FUERTE
    if costo > MULT_BAJA_SUAVE * target * ingreso:
        return FACTOR_BAJA_SUAVE
    if (
        costo < MULT_SUBIDA * target * ingreso
        and orders is not None
        and orders >= ORDERS_MIN_SUBIDA
    ):
        return FACTOR_SUBIDA
    return None


def decide_bid(
    *,
    platform: str,
    bids: AgregadoMetricas | None,
    cortes: AgregadoMetricas | None,
    target_acos_pct: Decimal,
    bid_actual: Decimal | None,
    bid_moneda: str | None,
    floor: Decimal,
    ceiling: Decimal,
    umbral_pause: int = LEGACY_PAUSE,
) -> ResultadoBid:
    """Decide PAUSE o ajuste de bid para UNA entidad, puro y determinista.

    `umbral_pause` (CORTES 01 1.3) es el umbral de clicks del PAUSE YA
    RESUELTO (inclusivo >=): el orquestador lo calcula con
    cortes.umbral_corte(evidencia del ad group, 'pause') y el replay lee el
    congelado en inputs.corte.umbral_clicks_usado; el default es el legacy
    vigente, 100 desde CORTES 03 (filas historicas sin la clave). Este
    modulo jamas recalcula.

    Orden interno sellado:
    (1) PAUSE sobre el agregado de CORTES (si existe, esta completo, su
        moneda es la de la plataforma, orders==0, clicks>=umbral_pause y
        cost>= umbral de la plataforma) -> resultado pause con la ventana
        de CORTES.
    (2) Si no: bandas sobre el agregado de BIDS (si existe, esta completo y
        su moneda es la de la plataforma): evalua -25/-12/+15 con
        precedencia, aplica factor -> clamp por decision [-0.30, +0.20] ->
        clamp a [floor, ceiling] -> |delta| < 0.01 -> no-op.
    (3) Sin banda o con guarda bloqueada -> ResultadoBid(None, motivo, ...).

    ASIMETRIA INTENCIONAL: la ventana de cortes incompleta (o ausente) NO
    impide evaluar bandas sobre la ventana de bids -- son ventanas
    independientes; solo la ventana que va a decidir exige su completitud y
    su moneda. El no-op final reporta la primera guarda que bloqueo en orden
    de evaluacion (pause antes que bids).

    El bid resultante NO se redondea (sin quantize): la presentacion la
    decide el apply de PR2.
    """
    if platform not in PLATAFORMAS_MONEDA:
        raise ValueError(
            f"plataforma fuera del vocabulario sellado {{amazon_us, amazon_mx}}: {platform!r}"
        )
    if not target_acos_pct.is_finite() or target_acos_pct <= 0:
        # target 0 haria que cualquier cost>0 dispare una baja; negativo
        # invierte las bandas. La cascada de 2.4 ya valida sus peldanos, pero
        # el motor no puede confiar en eso (hallazgo codex+grok, ronda 1).
        raise ValueError(f"target_acos_pct invalido: {target_acos_pct!r} (debe ser > 0)")
    if floor > ceiling:
        raise ValueError(f"floor {floor} > ceiling {ceiling}: rango de bid invalido")
    moneda = PLATAFORMAS_MONEDA[platform]

    # (1) PAUSE sobre la ventana de CORTES (un pause es un corte: regla 6)
    motivo_pause_bloqueado: str | None = None
    if cortes is not None:
        if not cortes.completa:
            motivo_pause_bloqueado = MOTIVO_PAUSE_CORTES_INCOMPLETO
        elif cortes.metric_currency != moneda:
            motivo_pause_bloqueado = MOTIVO_PAUSE_MONEDA_INVALIDA
        elif cortes.orders is None:
            motivo_pause_bloqueado = MOTIVO_PAUSE_ORDERS_DESCONOCIDO
        elif cortes.clicks is None or cortes.cost is None:
            motivo_pause_bloqueado = MOTIVO_PAUSE_CLICKS_COST_DESCONOCIDOS
        elif (
            cortes.orders == 0
            and cortes.clicks >= umbral_pause
            and cortes.cost >= PAUSE_COST_MIN[platform]
        ):
            return ResultadoBid(
                kind="pause",
                motivo=MOTIVO_PAUSE,
                old_value=None,
                new_value=None,
                value_currency=None,  # pause no lleva dinero (esquema: NULL)
                factor=None,
                window_start=cortes.window_start,
                window_end=cortes.window_end,
                data_observed_at=cortes.observed_at_max,
            )

    # (2) Bandas sobre la ventana de BIDS (independiente de cortes)
    motivo_bids_bloqueado: str | None = None
    if bids is None:
        motivo_bids_bloqueado = MOTIVO_BIDS_SIN_OBSERVACIONES
    elif not bids.completa:
        motivo_bids_bloqueado = MOTIVO_BIDS_INCOMPLETO
    elif bids.metric_currency != moneda:
        motivo_bids_bloqueado = MOTIVO_BIDS_MONEDA_INVALIDA
    elif bids.cost is None or bids.ad_revenue is None:
        motivo_bids_bloqueado = MOTIVO_ACOS_DESCONOCIDO
    else:
        factor = _factor_banda(bids, target_acos_pct)
        if factor is None:
            motivo_bids_bloqueado = MOTIVO_SIN_BANDA
        elif bid_actual is None:
            motivo_bids_bloqueado = MOTIVO_BID_ACTUAL_AUSENTE
        elif bid_actual <= 0:
            motivo_bids_bloqueado = MOTIVO_BID_ACTUAL_INVALIDO
        elif bid_moneda != moneda:
            motivo_bids_bloqueado = MOTIVO_BID_MONEDA_INVALIDA
        else:
            factor_clamped = min(max(factor, CLAMP_FACTOR_MIN), CLAMP_FACTOR_MAX)
            nuevo = bid_actual * (_UNO + factor_clamped)
            nuevo = min(max(nuevo, floor), ceiling)
            if abs(nuevo - bid_actual) < MIN_DELTA_ABSOLUTO:
                motivo_bids_bloqueado = MOTIVO_DELTA_BAJO_UMBRAL
            else:
                delta = nuevo - bid_actual
                direccion_ok = (
                    delta < 0 if factor_clamped < 0 else delta > 0
                )  # la banda bajo -> baja; la banda subio -> sube
                magnitud_ok = delta <= CLAMP_FACTOR_MAX * bid_actual and (
                    -delta <= -CLAMP_FACTOR_MIN * bid_actual
                )  # cambio final dentro de [-30%, +20%] (multiplicacion, sin division)
                if not (direccion_ok and magnitud_ok):
                    # El clamp a [floor, ceiling] puede dejar un cambio que ya
                    # no es el que la banda decidio (bid fuera de rango: -25%
                    # que termina SUBIENDO al floor, +15% que termina BAJANDO
                    # al ceiling). Si no hay valor que cumpla el cambio por
                    # decision Y el rango del goal, no-op (hallazgo codex
                    # [alta], cross-review ronda 1).
                    motivo_bids_bloqueado = MOTIVO_RANGO_BLOQUEA_AJUSTE
                else:
                    return ResultadoBid(
                        kind="bid",
                        motivo=_MOTIVO_BANDA[factor],
                        old_value=bid_actual,
                        new_value=nuevo,  # sin quantize: presentacion la decide el apply (PR2)
                        value_currency=bid_moneda,
                        factor=factor,
                        window_start=bids.window_start,
                        window_end=bids.window_end,
                        data_observed_at=bids.observed_at_max,
                    )

    # (3) Nada decidio: no-op con la primera guarda que bloqueo (pause -> bids)
    return ResultadoBid(
        kind=None,
        motivo=motivo_pause_bloqueado or motivo_bids_bloqueado,
        old_value=None,
        new_value=None,
        value_currency=None,
        factor=None,
        window_start=None,
        window_end=None,
        data_observed_at=None,
    )
