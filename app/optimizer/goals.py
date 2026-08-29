"""Goals y modo efectivo del optimizador (ORBIT 03, task 2.4).

Resuelve QUE config gobierna a cada entidad y con que modo corre el ciclo.
No decide bids ni cortes: 2.2/2.3 consumen lo que aqui se resuelve, y 3.1
(orquestador) es quien lo congela en decision.inputs. La UNICA IO del
modulo es en_cooldown (LECTURA sobre decision_application).

Reglas selladas (plans/orbit-03.md task 2.4 + Spec delta de CONTEXTO.md):

- PRECEDENCIA campana > plataforma, INCLUIDO enabled (opt-out del Spec
  delta): el goal de campana PISA SIEMPRE que exista, INCLUSO deshabilitado
  -- eso ES el opt-out: un goal de campana enabled=false deja la entidad
  EXCLUIDA aunque la plataforma este habilitada (elegible False). Sin goal
  de campana manda el de plataforma; ninguno -> None (3.1 excluye con su
  motivo). La precedencia se resuelve EN LA APP, no en SQL (COMMENT de
  ads_optimizer_goal).
- CASCADA de target ACoS, peldano por peldano: cada peldano decide SOLO si
  el anterior es None (regla 3: faltante != valor):
  goal.target_acos_pct -> setting `ads_target_acos_pct_<platform>` de
  config_version.settings (clave sellada en docs/DATABASE.md; por
  plataforma DEL ENUM, amazon_us/amazon_mx) -> ad_entity_state.acos_target
  (cache de lo publicado en Amazon; NO es la fuente, ver su COMMENT) ->
  DEFAULT_TARGET_PCT 55.
- VARIANTE CON PROCEDENCIA (ORBIT 16, task 1.2): `cascada_target_acos_con_procedencia`
  devuelve (valor, peldaño) con los CINCO peldaños (goal_campana, goal_plataforma,
  setting_plataforma, cache_estado, default) para el dashboard. Compatible con el
  camino del motor (misma aritmetica; la campana PISA siempre que exista: con un
  goal de campana de target None, el target del goal de plataforma NO entra --
  testeado por equivalencia). La capa web la REUTILIZA, jamas la reimplementa.
- FLOOR/CEILING: los defaults son POR MONEDA (DEFAULTS_POR_MONEDA: USD
  0.10/2.50 con max real observado 2.00, MXN 1.00/45.00; OTRA moneda =
  ValueError explicito -- sellado 2 del plan plans/orbit-05-preflight.md,
  decision del dueno 2026-08-28 tras el spot-check 4.4: con el default
  unico pensado en USD, 144/233 keywords y 44/51 targets MX tenian bid >
  2.50 MXN y el techo los habria aplastado en vivo). Desde 0003 la DB ya
  NO tiene DEFAULT en ads_optimizer_goal: la tabla es la UNICA fuente y
  resuelve_floor_ceiling exige la moneda EXPLICITA; un None solo puede
  venir de un Goal construido a mano (esta capa pura) y recibe el default
  de SU moneda -- un None pasando al clamp de 2.2 seria un bid sin piso.
- MODO EFECTIVO = escalera global (config_version.settings, clave sellada
  `ads_optimizer_mode` -- NUEVA en esta task, la siembra humana la hace 4.3;
  valores off|shadow|live) AND goal.mode: el INFIMO (meet) del reticulo
  off < shadow < live. Valor fuera de vocabulario -> ValueError ruidoso
  (las funciones puras no adivinan). modo_desde_settings es fail-closed:
  sin clave o valor invalido -> 'off' (una config corrupta JAMAS habilita
  live por accidente).
- FAIL-CLOSED PR1 (historico, ya resuelto): HAY_MODULO_APPLY fue False hasta
  ORBIT 04 2.4 (sellado 22) mientras el modulo apply no existia; resuelve_modo
  degradaba un meet 'live' a 'shadow' con la nota estable NOTA_LIVE_DEGRADADO.
  Desde 2.4 el flag vive en True y la degradacion YA NO dispara: el residuo
  envelope-live/goal-shadow lo resuelve el aplicador re-resolviendo el modo
  POR DECISION (app/apply.py, JAMAS inputs.modo ni cycle.mode). La rama y la
  nota se conservan como candado de un flip-back deliberado. Un meet 'shadow'
  por la escalera NO lleva nota: es el meet pedido, no una degradacion.
- COOLDOWN 7d por ENTIDAD: en_cooldown pregunta si la entidad tiene ALGUNA
  decision con apply VERIFICADO dentro de los ultimos 7 dias (umbral
  confirmed_at > ahora - 7d, comparador ESTRICTO como los de windows:
  confirmado EXACTAMENTE hace 7d ya NO enfria). Se consulta verify_ok IS
  TRUE directamente y confirmed_at (no attempted_at): la terna
  confirmed_at + platform_ack + verify_ok se sella JUNTA en el readback
  (CHECKs del esquema), asi que confirmed_at es el instante en que el apply
  quedo VERIFICADO. FALSE (divergencia, se reintenta) NO enfria; NULL (en
  vuelo) tampoco. En shadow nunca enfria POR QUERY (JOIN a optimizer_cycle
  mode='live', regla sellada del diseno v2 -- hallazgo codex+grok ronda 1):
  aunque un dry-run de PR2 escribiera decision_application en shadow, ese
  apply JAMAS enfria. Desde ORBIT 04 phase 2 (ADV-06, sellado 21) el JOIN es
  contra applied_cycle_id (el ciclo que EJECUTO, sellado con verify_ok en
  0002), no contra d.cycle_id (el que decidio): la decision shadow aplicada
  en live SI enfria desde el apply.
- DETERMINISMO: no hay now() escondido; `ahora` llega por parametro y DEBE
  ser tz-aware (mismo principio que windows._fecha_utc, replicado aqui
  localmente: no se importan privados de otro modulo).

SQL del modulo (LECTURA; la parsea el test de sintaxis con pglast):
_SQL_EN_COOLDOWN.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import psycopg

# ---------------------------------------------------------------------------
# Constantes selladas (fuente: plans/orbit-03.md task 2.4 + diseno v2)
# ---------------------------------------------------------------------------

DEFAULT_TARGET_PCT = Decimal("55")  # ultimo peldano de la cascada

# Defaults de piso/techo POR MONEDA (ORBIT 05 preflight 1.2, sellado 2 del
# plan plans/orbit-05-preflight.md; spot-check 4.4: con el default unico
# 0.10/2.50 pensado en USD, el goal 4 MXN aplastaba 144/233 keywords y
# 44/51 targets MX con bid > 2.50 MXN; decision del dueno 2026-08-28).
# UNICA fuente de defaults: OTRA moneda = ValueError explicito, no se
# inventan numeros (regla 3). La DB ya NO tiene DEFAULT (0003): todo INSERT
# de goal lleva piso/techo explicitos y esta tabla resuelve solo los None
# de la capa pura.
DEFAULTS_POR_MONEDA: dict[str, tuple[Decimal, Decimal]] = {
    "USD": (Decimal("0.10"), Decimal("2.50")),  # max real observado 2.00
    "MXN": (Decimal("1.00"), Decimal("45.00")),
}

# Clave NUEVA de config_version.settings (sellada en esta task; la siembra
# humana de 4.3 escribe la escalera global aqui; valores off|shadow|live).
CLAVE_SETTING_MODO = "ads_optimizer_mode"

# Encendido en ORBIT 04 2.4 (sellado 22: la tarea de integracion lo voltea):
# con True, resuelve_modo YA NO degrada live->shadow — la fase de apply vive
# en app/cycle.py y el residual envelope-live/goal-shadow lo resuelve el
# aplicador por decision. Un flip-back a False reactivaria la degradacion con
# nota (candado deliberado, no codigo muerto).
HAY_MODULO_APPLY = True

COOLDOWN = dt.timedelta(days=7)  # por ENTIDAD; comparador ESTRICTO en el umbral

# Reticulo de modos: off < shadow < live. modo_efectivo es el INFIMO (meet).
MODOS = ("off", "shadow", "live")
_ORDEN_MODO: dict[str, int] = {modo: indice for indice, modo in enumerate(MODOS)}

# Nota del vocabulario estable para la degradacion live->shadow de PR1.
NOTA_LIVE_DEGRADADO = "live degradado a shadow: sin modulo apply en PR1"

# ---------------------------------------------------------------------------
# Estructuras
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Goal:
    """Goal resuelto de una entidad, espejo de ads_optimizer_goal (la fila la
    lee 3.1 y la reconstruye aqui; target/floor/ceiling None = "este peldano
    no decide", la cascada/defaults de este modulo los resuelven).
    bid_floor/bid_ceiling son NOT NULL en DB y SIN DEFAULT desde 0003: None
    solo si se construye a mano (resuelve_floor_ceiling los resuelve por
    moneda). `mode` es la escalera PROPIA del goal."""

    scope: str  # 'campaign' | 'platform'
    ad_entity_id: int | None  # scope campaign
    platform: str | None  # scope platform
    target_acos_pct: Decimal | None
    bid_floor: Decimal | None
    bid_ceiling: Decimal | None
    bid_currency: str
    harvest_campaign_id: str | None
    harvest_ad_group_id: str | None
    harvest_default_bid: Decimal | None
    enabled: bool
    mode: str  # 'off' | 'shadow' | 'live'


@dataclass(frozen=True)
class ModoEfectivo:
    """Modo con el que corre el ciclo. `nota` es None salvo en la degradacion
    live->shadow de PR1 (NOTA_LIVE_DEGRADADO): 3.1 la persiste en
    optimizer_cycle.notes para que la degradacion sea auditable."""

    modo: str  # 'off' | 'shadow' | 'live'
    nota: str | None


# ---------------------------------------------------------------------------
# Parte pura: precedencia, cascada, defaults de bid, escalera de modos
# ---------------------------------------------------------------------------


def resuelve_goal(goal_campana: Goal | None, goal_plataforma: Goal | None) -> Goal | None:
    """Precedencia campana > plataforma INCLUIDO enabled: el goal de campana
    PISA SIEMPRE que existe, aun deshabilitado (el opt-out del Spec delta:
    campaign_optimization_state del viejo se traduce a goal de campana
    enabled=false). Sin campana manda el de plataforma; ninguno -> None."""
    return goal_campana if goal_campana is not None else goal_plataforma


def elegible(goal: Goal | None) -> bool:
    """Elegibilidad dura: solo un goal RESUELTO y habilitado entra al ciclo
    (3.1 combina con ad_entity_state.status ENABLED y persiste el motivo)."""
    return goal is not None and goal.enabled


def clave_target_plataforma(platform: str) -> str:
    """Clave sellada del setting de target por plataforma
    (ads_target_acos_pct_<platform>, docs/DATABASE.md)."""
    return f"ads_target_acos_pct_{platform}"


def target_desde_settings(settings: Mapping, platform: str) -> Decimal | None:
    """Target ACoS de config_version.settings para la plataforma: None si la
    clave no esta (el default NO se aplica aqui: eso es del ultimo peldano de
    la cascada). El valor JSON se normaliza via str a Decimal exacto, nunca
    por binario float (regla 4 de estilo numerico del repo). Un valor PRESENTE
    pero no numerico, NaN/Inf, 0 o negativo es config CORRUPTA, no dato
    faltante: ValueError ruidoso (regla 3 -- camuflarlo de ausente para caer
    al default 55 decidiria con un target que nadie configuro; hallazgo
    codex+grok, cross-review ronda 1)."""
    clave = clave_target_plataforma(platform)
    valor = settings.get(clave)
    if valor is None:
        return None
    try:
        target = Decimal(str(valor))
    except ArithmeticError as exc:  # InvalidOperation: basura no numerica
        raise ValueError(f"setting {clave}: valor no numerico: {valor!r}") from exc
    if not target.is_finite() or target <= 0:
        raise ValueError(f"setting {clave}: target debe ser > 0 y finito, llego {target}")
    return target


def _valida_target_peldano(valor: Decimal | None, peldano: str) -> Decimal | None:
    """Validacion comun de la cascada: None pasa (sigue al siguiente peldano);
    un valor presente pero <= 0 o no finito revienta (regla 3: invalido !=
    ausente). El CHECK goal_target_acos_positivo cubre el goal en DB; el
    setting JSONB y el cache de ad_entity_state NO tienen candado."""
    if valor is None:
        return None
    if not valor.is_finite() or valor <= 0:
        raise ValueError(f"target de {peldano} invalido: {valor!r} (debe ser > 0)")
    return valor


def cascada_target_acos(
    target_goal: Decimal | None,
    setting_plataforma: Decimal | None,
    cache_acos_target: Decimal | None,
) -> Decimal:
    """Cascada sellada, peldano por peldano: goal -> setting de plataforma ->
    cache ad_entity_state.acos_target -> default 55. Cada peldano decide SOLO
    si el anterior es None (regla 3: dato faltante != valor); un peldano
    presente pero invalido (<= 0, no finito) revienta, NO cae al siguiente."""
    for valor, peldano in (
        (target_goal, "goal.target_acos_pct"),
        (setting_plataforma, "setting ads_target_acos_pct"),
        (cache_acos_target, "ad_entity_state.acos_target"),
    ):
        valido = _valida_target_peldano(valor, peldano)
        if valido is not None:
            return valido
    return DEFAULT_TARGET_PCT


# ---------------------------------------------------------------------------
# Variante con PROCEDENCIA (ORBIT 16, task 1.2): el dashboard expone QUE
# peldano gano. Compatible con el camino del motor (cero cambio de
# comportamiento: misma aritmetica, testeada por equivalencia); la capa web
# jamas la reimplementa.
# ---------------------------------------------------------------------------

# Vocabulario sellado de los CINCO peldaños: el dashboard los muestra tal
# cual; un nombre distinto aqui rompe el contrato de 1.4.
PELDANOS_CASCADA = (
    "goal_campana",
    "goal_plataforma",
    "setting_plataforma",
    "cache_estado",
    "default",
)


def cascada_target_acos_con_procedencia(
    goal_campana: Goal | None,
    goal_plataforma: Goal | None,
    settings: Mapping,
    cache_acos_target: Decimal | None,
    platform: str,
) -> tuple[Decimal, str]:
    """Cascada sellada con PROCEDENCIA: devuelve (valor, peldaño) con los
    CINCO peldaños y estos nombres EXACTOS: goal_campana, goal_plataforma,
    setting_plataforma, cache_estado, default. Cada peldaño decide SOLO si el
    anterior es None (regla 3); un valor PRESENTE pero invalido (<= 0, no
    finito) revienta, jamas cae al siguiente. La clave del setting sale de
    clave_target_plataforma(platform) via target_desde_settings -- REUTILIZADA,
    no reimplementada (regla 2).

    SEMANTICA DE LOS DOS GOALS, identica al camino del motor: el goal de
    campana PISA SIEMPRE que exista (resuelve_goal, INCLUIDO enabled); si su
    target es None, el target del goal de plataforma NO entra -- el motor
    resuelve goal_campana y su cascada vieja salta el target None al setting.
    El peldaño goal_plataforma solo gana cuando NO existe goal de campana.
    Reportar 'goal_plataforma' cuando existe un goal de campana con target
    None mentiria sobre lo que el motor decidio (regla 2: un numero, una
    fuente; el test de equivalencia lo sella)."""
    if goal_campana is not None:
        valido = _valida_target_peldano(goal_campana.target_acos_pct, "goal_campana")
        if valido is not None:
            return (valido, "goal_campana")
    elif goal_plataforma is not None:
        valido = _valida_target_peldano(goal_plataforma.target_acos_pct, "goal_plataforma")
        if valido is not None:
            return (valido, "goal_plataforma")
    setting = target_desde_settings(settings, platform)
    if setting is not None:
        return (setting, "setting_plataforma")
    valido = _valida_target_peldano(cache_acos_target, "cache_estado")
    if valido is not None:
        return (valido, "cache_estado")
    return (DEFAULT_TARGET_PCT, "default")


def resuelve_floor_ceiling(goal: Goal | None, moneda: str) -> tuple[Decimal, Decimal]:
    """(floor, ceiling) del goal con los defaults DE SU MONEDA aplicados donde
    venga None. `moneda` es OBLIGATORIA y la pasa el llamador EXPLICITA
    (bid.PLATAFORMAS_MONEDA[platform] en el ciclo; goal.bid_currency donde
    solo hay goal) -- sin moneda no hay default (regla 3). El bid_currency
    del goal es LA moneda: si `goal` trae otra, ValueError ruidoso
    (desalineacion del llamador, jamas se mezclan monedas en un clamp). Una
    moneda fuera de DEFAULTS_POR_MONEDA -> ValueError explicito (no se
    inventan numeros). Tras 0003 la DB ya NO tiene DEFAULT 0.10/2.50: esta
    tabla es la UNICA fuente. goal None (entidad sin goal) tambien devuelve
    los defaults de la moneda: el par acota SIEMPRE (un clamp sin piso en
    2.2 seria un bid sin suelo). Sin quantize: la presentacion la decide el
    apply."""
    if goal is not None and goal.bid_currency != moneda:
        raise ValueError(
            f"moneda {moneda!r} != goal.bid_currency {goal.bid_currency!r}: "
            "el bid_currency del goal es LA moneda (desalineacion del llamador)"
        )
    try:
        piso, techo = DEFAULTS_POR_MONEDA[moneda]
    except KeyError:
        raise ValueError(
            f"sin defaults inventados para {moneda!r}; agregala a "
            "DEFAULTS_POR_MONEDA con decision del dueno"
        ) from None
    if goal is None:
        return (piso, techo)
    return (
        goal.bid_floor if goal.bid_floor is not None else piso,
        goal.bid_ceiling if goal.bid_ceiling is not None else techo,
    )


def modo_desde_settings(settings: Mapping) -> str:
    """Escalera global desde config_version.settings (clave sellada
    ads_optimizer_mode). Fail-closed: sin clave, valor invalido o NULL ->
    'off' (una config corrupta jamas habilita live por accidente)."""
    valor = settings.get(CLAVE_SETTING_MODO)
    if isinstance(valor, str) and valor in _ORDEN_MODO:
        return valor
    return "off"


def _chequea_modo(nombre: str, modo: str) -> None:
    """Vocabulario cerrado {off, shadow, live}: fuera de el, ValueError
    ruidoso y temprano (mismo estilo que PLATAFORMAS_MONEDA en bid)."""
    if not (isinstance(modo, str) and modo in _ORDEN_MODO):
        raise ValueError(f"{nombre} fuera del vocabulario sellado {{off, shadow, live}}: {modo!r}")


def modo_efectivo(escalera_global: str, modo_goal: str) -> str:
    """Infimo (meet) del reticulo off < shadow < live: off con cualquier cosa
    es off; shadow y live dan shadow; live solo si ambos viven. Valor invalido
    en cualquiera de los dos -> ValueError."""
    _chequea_modo("escalera_global", escalera_global)
    _chequea_modo("modo_goal", modo_goal)
    if _ORDEN_MODO[escalera_global] <= _ORDEN_MODO[modo_goal]:
        return escalera_global
    return modo_goal


def resuelve_modo(escalera_global: str, modo_goal: str) -> ModoEfectivo:
    """modo_efectivo + fail-closed historico de PR1: con HAY_MODULO_APPLY en
    False, un meet 'live' se degrada a 'shadow' con la nota estable
    NOTA_LIVE_DEGRADADO. Desde 2.4 el flag vive en True (sellado 22) y el meet
    live YA NO se degrada: existe modulo de aplicacion. Un meet 'shadow' u
    'off' por la escalera NO lleva nota: no es degradacion."""
    modo = modo_efectivo(escalera_global, modo_goal)
    if modo == "live" and not HAY_MODULO_APPLY:
        return ModoEfectivo(modo="shadow", nota=NOTA_LIVE_DEGRADADO)
    return ModoEfectivo(modo=modo, nota=None)


# ---------------------------------------------------------------------------
# Parte con IO (SOLO lectura; el conn llega por parametro)
# ---------------------------------------------------------------------------

# Cooldown de 7d por ENTIDAD sobre applies VERIFICADOS de ciclos LIVE.
# verify_ok IS TRUE directo (nunca parsear platform_ack: el ack es evidencia,
# no senal de control -- COMMENT de la columna), umbral confirmed_at > ahora
# - 7d ESTRICTO: confirmado exactamente hace 7d ya NO enfria (consistente
# con los comparadores estrictos de windows). FALSE (divergencia) y NULL (en
# vuelo) no enfrian: solo un apply verificado dentro de la ventana cuenta.
# El JOIN a optimizer_cycle con mode='live' es del diseno v2 ("solo cuenta
# mode='live' AND applied=1 AND verify_ok<>0") y hace al query inmune a que
# un dry-run de PR2 escriba decision_application en shadow (hallazgo
# codex+grok, cross-review ronda 1): una decision shadow con apply
# verificado JAMAS enfria.
# Desde la review adversaria de ORBIT 04 phase 2 (ADV-06, sellado 21) el JOIN
# mira da.applied_cycle_id — el ciclo que EJECUTO el apply (se sella SOLO con
# verify_ok, 0002) — y NO d.cycle_id (el que decidio): la decision nacida en
# ciclo shadow y aplicada por un ciclo live SI enfria desde el apply; mirar
# al decisor dejaba el anti-loop roto justo en ese caso (la entidad se
# re-decidia y re-aplicaba al dia siguiente).
_SQL_EN_COOLDOWN = """
SELECT EXISTS (
    SELECT 1
      FROM decision_application da
      JOIN decision d ON d.id = da.decision_id
      JOIN optimizer_cycle oc ON oc.id = da.applied_cycle_id AND oc.mode = 'live'
     WHERE d.ad_entity_id = %s
       AND da.verify_ok IS TRUE
       AND da.confirmed_at > %s
)
"""


def en_cooldown(conn: psycopg.Connection, ad_entity_id: int, *, ahora: dt.datetime) -> bool:
    """True si la ENTIDAD tiene alguna decision con apply VERIFICADO
    (verify_ok IS TRUE) EJECUTADO por un ciclo LIVE (applied_cycle_id, el
    ciclo ejecutor — no el decisor) y confirmado hace <7d respecto de
    `ahora`. Reloj por parametro (sin now() escondido), DEBE ser tz-aware
    (en cualquier zona: la comparacion es entre instantes): un naive
    evaluaria segun la TZ local del proceso y se rechaza ruidosamente
    (mismo principio que windows._fecha_utc, replicado sin importar su
    privado). En shadow nunca enfria POR QUERY: el filtro del ciclo EJECUTOR
    mode='live' lo hace inmune a applies de dry-run (regla sellada del
    diseno v2)."""
    if ahora.tzinfo is None:
        raise ValueError(
            "ahora debe ser tz-aware (UTC): un naive evaluaria segun la TZ local del proceso"
        )
    return conn.execute(_SQL_EN_COOLDOWN, (ad_entity_id, ahora - COOLDOWN)).fetchone()[0]
