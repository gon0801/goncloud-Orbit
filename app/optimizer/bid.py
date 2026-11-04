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
  cost_min (CORTES 03: piso de costo RESUELTO por parametro -- None = el
  vigente PAUSE_COST_MIN de abajo, {amazon_us: 40 USD, amazon_mx: 500 MXN}
  desde el 2026-08-28, antes 25 / 12 USD / 200 MXN; el ciclo vivo lo pasa y
  lo congela en inputs.corte.cost_min_usado, el replay pasa el congelado o
  el historico de su era).
- ADS PROTECCION 01: despues del corte antiguo, PAUSE economica si cost
  > 3 * target_pct/100 * revenue y exceso >= 80 USD / 1000 MXN. Revenue
  cero medido participa; None abstiene. La version de politica viaja en
  inputs para que el replay historico no adopte la regla nueva.
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
- Precedencia EXPLICITA: PAUSE gana a cualquier ajuste; la regla A' de
  cero ventas (BIDS 01) gana a las bandas; -25 gana a -12;
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

from app.optimizer.windows import AgregadoMetricas

# ---------------------------------------------------------------------------
# Constantes selladas (fuente: diseno v2, reglas 1-5; el diseno manda)
# ---------------------------------------------------------------------------

PLATAFORMAS_MONEDA: dict[str, str] = {"amazon_us": "USD", "amazon_mx": "MXN"}

# CORTES 01 (1.3): el umbral de clicks del PAUSE llega RESUELTO por
# parametro (cortes.umbral_corte(evidencia del grupo, 'pause') en cycle.py;
# el motor sigue puro). Contrato del PISO DE COSTO (cierre replay CORTES 03,
# decision del lead 2026-08-28): PAUSE_COST_MIN es la fuente UNICA del piso
# VIGENTE y `cost_min` de decide_bid lo recibe YA RESUELTO -- el ciclo vivo
# pasa el vigente y lo CONGELA en inputs.corte.cost_min_usado; el replay
# pasa el congelado o el HISTORICO de su era (REPLAY_PAUSE_COST_PRE_CORTES03
# de abajo). El default-vigente de `umbral_pause` ya NO es mecanismo de
# replay: sin la clave, el replay usa las constantes REPLAY_* (historia
# congelada, JAMAS el vigente LEGACY_PAUSE).
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
MOTIVO_PAUSE_ECONOMICA = "pause_economica"
MOTIVO_PAUSE_ECONOMICA_DATO_FALTANTE = "pause_economica_dato_faltante"
POLITICA_PAUSE_ECONOMICA = "economic_pause_v1"
EXCESO_MINIMO = {"USD": Decimal("80"), "MXN": Decimal("1000")}
MULT_PAUSE_ECONOMICA = Decimal("3")
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
    value_currency NULL en pause: un pause no mueve dinero). BIDS 02 M.3:
    las bandas viven en eras.py y la politica vigente en inputs.politica;
    este resultado solo decide PAUSE (o no-op con el motivo de bloqueo)."""

    kind: str | None  # 'pause' | None (no-op/skip)
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


def exceso_economico(
    cortes: AgregadoMetricas | None, target_acos_pct: Decimal, moneda: str
) -> Decimal | None:
    """Exceso en moneda original; None cuando el insumo maduro no es fiable."""
    if (
        cortes is None
        or not cortes.completa
        or cortes.metric_currency != moneda
        or cortes.cost is None
        or cortes.ad_revenue is None
        or not cortes.cost.is_finite()
        or not cortes.ad_revenue.is_finite()
        or cortes.cost < 0
        or cortes.ad_revenue < 0
    ):
        return None
    return cortes.cost - target_acos_pct * cortes.ad_revenue / _CIEN


def _decide_pause(
    cortes: AgregadoMetricas | None,
    moneda: str,
    umbral_pause: int,
    costo_piso: Decimal,
    target_acos_pct: Decimal,
    policy_version: str | None,
) -> tuple[ResultadoBid | None, str | None]:
    """El bloque pause: regla de cero pedidos con clicks/cost sobre el
    piso, y despues la economica versionada. El pause no consume bandas:
    es identico en todas las eras (lo usan niveles_v3, la revalidacion
    de la cola y el replay de historia). Devuelve (pause, None) o
    (None, motivo_de_bloqueo)."""
    if cortes is None:
        return (None, None)
    motivo: str | None = None
    if not cortes.completa:
        motivo = MOTIVO_PAUSE_CORTES_INCOMPLETO
    elif cortes.metric_currency != moneda:
        motivo = MOTIVO_PAUSE_MONEDA_INVALIDA
    elif cortes.orders is None:
        motivo = MOTIVO_PAUSE_ORDERS_DESCONOCIDO
    elif cortes.clicks is None or cortes.cost is None:
        motivo = MOTIVO_PAUSE_CLICKS_COST_DESCONOCIDOS
    elif cortes.orders == 0 and cortes.clicks >= umbral_pause and cortes.cost >= costo_piso:
        razon = MOTIVO_PAUSE
        return (
            ResultadoBid(
                "pause",
                razon,
                None,
                None,
                None,
                None,
                cortes.window_start,
                cortes.window_end,
                cortes.observed_at_max,
            ),
            None,
        )
    exceso = exceso_economico(cortes, target_acos_pct, moneda)
    # Obs4r2: la regla economica exige orders/clicks CONOCIDOS, como la
    # umbral (pausar con ventas desconocidas es justo lo que
    # pause_orders_desconocido prohibe; el motivo de abstencion ya quedo
    # en `motivo` arriba y manda al no disparar).
    if (
        policy_version == POLITICA_PAUSE_ECONOMICA
        and exceso is not None
        and cortes.orders is not None
        and cortes.clicks is not None
        and cortes.cost > MULT_PAUSE_ECONOMICA * target_acos_pct * cortes.ad_revenue / _CIEN
        and exceso >= EXCESO_MINIMO[moneda]
    ):
        return (
            ResultadoBid(
                "pause",
                MOTIVO_PAUSE_ECONOMICA,
                None,
                None,
                None,
                None,
                cortes.window_start,
                cortes.window_end,
                cortes.observed_at_max,
            ),
            None,
        )
    if policy_version == POLITICA_PAUSE_ECONOMICA and exceso is None and motivo is None:
        motivo = MOTIVO_PAUSE_ECONOMICA_DATO_FALTANTE
    return (None, motivo)


def decide_pause(
    *,
    platform: str,
    bids: AgregadoMetricas | None,
    cortes: AgregadoMetricas | None,
    target_acos_pct: Decimal,
    umbral_pause: int,
    cost_min: Decimal | None = None,
    policy_version: str | None = None,
) -> ResultadoBid:
    """Decide SOLO la PAUSE con _decide_pause (BIDS 02 M.3): la usan el
    ciclo con la politica apagada y la revalidacion de la cola. Si pausa,
    devuelve el pause con la ventana de cortes; si no, no-op con la
    primera guarda que bloqueo en orden de evaluacion (pause antes que
    bids, como decide_bid; `bids` solo aporta el motivo de bloqueo, jamas
    se juzga). `cost_min` None = el piso VIGENTE (PAUSE_COST_MIN)."""
    if platform not in PLATAFORMAS_MONEDA:
        raise ValueError(
            f"plataforma fuera del vocabulario sellado {{amazon_us, amazon_mx}}: {platform!r}"
        )
    if not target_acos_pct.is_finite() or target_acos_pct <= 0:
        raise ValueError(f"target_acos_pct invalido: {target_acos_pct!r} (debe ser > 0)")
    if policy_version not in (None, POLITICA_PAUSE_ECONOMICA):
        raise ValueError(f"politica pause desconocida: {policy_version!r}")
    moneda = PLATAFORMAS_MONEDA[platform]
    costo_piso = cost_min if cost_min is not None else PAUSE_COST_MIN[platform]
    pausa, motivo_pause_bloqueado = _decide_pause(
        cortes, moneda, umbral_pause, costo_piso, target_acos_pct, policy_version
    )
    if pausa is not None:
        return pausa
    motivo_bids_bloqueado: str | None = None
    if bids is None:
        motivo_bids_bloqueado = MOTIVO_BIDS_SIN_OBSERVACIONES
    elif not bids.completa:
        motivo_bids_bloqueado = MOTIVO_BIDS_INCOMPLETO
    elif bids.metric_currency != moneda:
        motivo_bids_bloqueado = MOTIVO_BIDS_MONEDA_INVALIDA
    elif bids.cost is None or bids.ad_revenue is None:
        motivo_bids_bloqueado = MOTIVO_ACOS_DESCONOCIDO
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
