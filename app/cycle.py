"""Orquestador del ciclo del optimizador (ORBIT 03, task 3.1).

Une las ventanas (2.1), el motor puro (2.2-2.4) y la auditoria: claim atomico
del lock, envelope de ciclo, elegibilidad, decisiones con inputs CONGELADOS y
skips estructurados. Vive FUERA de app/optimizer/ a proposito: es el unico
componente del optimizador que escribe (test_architecture sella que el motor
es puro; este modulo importa psycopg).

Diseño sellado (plans/orbit-03.md task 3.1 + diseno v2):

- TRES FASES DE TRANSACCION sobre la conexion de trabajo (llega conectada; el
  modulo gestiona sus transacciones y la deja en IDLE al empezar):
  * TX1 (commit): claim atomico + envelope 'running' + rastro de ciclos
    muertos. El claim es UNA sola sentencia (INSERT ... ON CONFLICT ... WHERE
    heartbeat_at + ttl vencido) -- JAMAS SELECT-luego-INSERT: la expiracion
    vive en el WHERE y el reloj del lock es now() de la DB a proposito (una
    sentencia, una sola fuente de tiempo; el reloj de las DECISIONES es
    `decided_at` por parametro, jamas un now() escondido).
  * TX2: TODA la fase de lecturas en REPEATABLE READ (contrato sellado en el
    docstring de windows.py: un solo snapshot uniforme para ventanas, terminos
    y guardas; una ingesta concurrente no puede desalinear completitud y
    agregados dentro de una misma llamada). CERO escrituras. `SET TRANSACTION
    ISOLATION LEVEL REPEATABLE READ` como PRIMERA sentencia de la transaccion
    (psycopg3 abre el BEGIN al entrar al bloque).
  * TX3 (commit): INSERT de cada decision (batch) + cierre del envelope, en
    una sola transaccion.
- HEARTBEAT en conexion corta aparte (autocommit) cada `heartbeat_cada`
  entidades procesadas. Stance DECLARADA fail-open: si un heartbeat falla se
  loggea warning y el ciclo SIGUE -- matar un ciclo shadow por un latido
  transitorio es peor que dejar que el TTL (30 min) sea el backstop.
- SELLO EN TODOS LOS CAMINOS (patron ingest_run de app.ads.reports): tras el
  envelope-running, cualquier BaseException sella best-effort el envelope
  'failed' con el error scrubbado, libera el lock y RE-LANZA la original.
- LIBERACION DEL LOCK por owner (DELETE ... WHERE owner = %s): nunca borra el
  lock de un sucesor que lo reclamo legitimamente tras el TTL.
- RASTRO DEL CICLO MUERTO: al ganar un claim tras TTL vencido, los envelopes
  'running' huerfanos del mismo motor+plataforma se cierran 'failed' con nota
  'rastro' y sus ids viajan en notes.ciclos_muertos del ciclo nuevo.
- SKIPS ESTRUCTURADOS: optimizer_cycle.notes es un JSON con contadores por
  motivo (vocabulario CERRADO: MOTIVO_* de bid/hygiene importados + los
  propios del orquestador). Sin PII de terminos saltados: SOLO contadores
  (para un termino saltado no existe fila en decision, asi que el contador
  con motivo del vocabulario cerrado ES toda la evidencia — y para 4.4
  basta). Es la fuente del spot-check humano (4.4) y del endpoint de estado
  (3.2).
- ELEGIBILIDAD (precedencia campaña > plataforma resuelta EN LA APP con
  goals.resuelve_goal, COMMENT del esquema; NADA de coalesce en SQL): sin goal
  -> 'sin_goal'; goal resuelto deshabilitado -> 'goal_disabled' (ESTE es el
  opt-out auditable del Spec delta); goal.mode 'off' -> 'goal_mode_off';
  entidad sin state o status != ENABLED -> 'estado_no_enabled' (para ad
  groups: sus terminos TODOS skip con ese motivo); cooldown 7d -> 'cooldown_7d'.
  Orden de gateS del orquestador: campaña primero (la hace invisible al
  optimizador por completo), luego estado, luego cooldown (la ultima guarda
  por decision). Desviacion declarada del orden literal del spec (que lista
  el cooldown antes): en shadow el cooldown JAMAS dispara (solo cuentan
  applies de ciclos live, regla 2.4) y asi no se paga una query EXISTS por
  entidad de campañas fuera del ciclo.
- REPLAY PUBLICO: reproduce(inputs) re-decide una decision desde SU JSON
  congelado (agregados sinteticos: el CONTEO de fechas es lo que replayea
  `completa`). Es la funcion del spot-check humano de 4.4.

CORTES 01 (1.2/1.3): umbrales de clicks adaptativos por producto. cycle
resuelve cortes.umbral_corte con la evidencia del ad group
(windows.ventanas_evidencia_ad_group, UNA consulta por plataforma dentro de
TX2) -- 'negative' para hygiene (1.2) y 'pause' para el motor de bids (1.3,
via k.parent_id de _SQL_DECISORAS) -- y los motores RECIBEN el int
resuelto. Toda decision que consulta umbral de clicks congela inputs.corte
TOP-LEVEL (shape del spec: umbral_clicks_usado FINAL con piso, elegible,
expected_clicks como string, evidencia con observed_at_max) -- en hygiene
las negative (las harvest NO lo llevan) y en bids TODAS, incluidas las de
kind final 'bid': decide_bid evalua PAUSE antes de las bandas y, sin el
freeze, el replay de un bid cuyo umbral adaptativo bloqueo el pause
rejugaria como pause con el legacy 25. data_observed_at =
LEAST(decided_at, max(obs directo, observed_at_max de la evidencia)) -- el
clamp es obligatorio (CHECK decision_dato_no_del_futuro): sin el, un
observed_at posterior a decided_at aborta el executemany de TX3.
reproduce() LEE el umbral congelado (fila historica sin la clave ->
legacy 20 negative / 25 pause); jamas recalcula evidencia.

Semantica de status del envelope: 'done' si el ciclo corrio completo (aunque
todo haya sido skips), 'degraded' si disparo una guarda de plataforma (dato
stale ES alarma) o la fase de apply aborto (2.4), 'skipped' para escalera off
/ lock ajeno (este ultimo ni abre envelope), 'failed' solo via sello de
excepcion (la excepcion sube: el status 'failed' nunca se devuelve, se
persiste).

ORBIT 04 2.4 — FASE DE APPLY DENTRO DEL LOCK (decisiones 11, 21, 22; APPLY.md
§9). Tras TX3 (decisiones commitadas) y ANTES del return (el lock se libera en
el `finally` de corre_ciclo — el apply corre con el lock NUESTRO):

- TX4 (transaccion REAL que commitea, como TX1/TX3; ADV-01 de la review
  adversaria de phase 2): `encola_cortes` con TODA decision de corte del
  ciclo (shadow-mark por decision via modo_efectivo del Aplicador; corre en
  shadow Y live — en shadow con un Aplicador SIN credenciales: cero HTTP por
  construccion). El bloque envuelve el encolado ENTERO desde IDLE; los
  savepoints por fila (choques del unico parcial) se conservan DENTRO. El
  camino de exito de la fase cierra con conn.commit() y cada except arranca
  con conn.rollback() — con la conexion de produccion (sin autocommit) un
  commit que falta revierte TODO lo escrito de la fase en el close() del CLI.
- SKIP POR CLAVE DE EFECTO en la FASE DE DECISION (2.2 sellado 5): al empezar
  `_fase_lecturas` se cargan las claves bloqueadas (en-vuelo o veto vigente,
  `apply_cola.claves_bloqueadas`) y `_procesa_decisora`/`_procesa_grupo`
  saltan la entidad/termino bloqueado con motivo `veto_pendiente` (funciona en
  shadow y live: el bloqueo es por clave de efecto, no por modo). DECLARADO:
  el bloqueo entity_cut salta la entidad ENTERA del motor de bids — decide_bid
  evalua pause y banda en UNA llamada y no existe forma de prohibir solo el
  pause sin inventar un umbral (regla 3); su bid vuelve al ciclo siguiente.
- Si el modo del envelope es 'live': la fase de apply propiamente — la
  FABRICA (`_aplicador_real`, inyectable via `aplicador_factory` para tests)
  resuelve credenciales (`AdsCredentials.from_secrets_dir`) y profile por
  plataforma (GET /v2/profiles + `evaluar_perfiles` de structure, la MISMA
  fuente del sync — regla 2). Sin perfil aceptado -> la fase ABORTA con nota
  `apply_error` (fail-closed: cero HTTP de mutacion) y el ciclo SIGUE. Luego
  `reconcilia_harvest` (inicio de la fase), `aplica_bids` (seleccion bajo cap)
  y `libera_vencidos` (FIFO).
- OWNERSHIP-CHECK PRE-HTTP + HEARTBEAT (decision 11): el `tick` del Aplicador
  ES el guard — heartbeat y `SELECT owner FROM ads_optimizer_lock`; si el
  owner ya no es el nuestro -> `ApplyAbortado`: aborta la fase de apply
  FAIL-CLOSED (sin mas HTTP; el guard vive en el transport del write client y
  cubre mutacion, readback, listas y token), el envelope se sella con nota
  `apply_abortado_owner` y status 'degraded'. Stance DECLARADA
  FAIL-CLOSED-AUDITADO: el ciclo NO re-lanza (las decisiones ya estan; el
  aborto es auditado y el siguiente ciclo reconcilia) y CUALQUIER otra
  excepcion de la fase de apply se captura igual (nota `apply_error` +
  degraded) — un fallo de apply no borra las decisiones ya tomadas. Solo el
  GET /v2/profiles de la fabrica queda fuera del guard (lectura previa a la
  existencia del Aplicador; residual declarado).
- GUARD `status='running'` EN EL CIERRE (la mejora que el rastro anunciaba):
  `_cierra_envelope` solo cierra envelopes 'running' — 0 filas = alguien ya
  cerro (rastro de un sucesor): warning y NO pisar, jamas sobre-cerrar. El
  sello post-apply (notes['apply'] + degraded) tampoco toca un envelope ya
  cerrado por rastro. `applied_count` se actualiza POR COLUMNA al final de la
  fase (permitido por GRANT), nunca como cierre.
- NOTES del apply: vocabulario CERRADO bajo `notes["apply"]` —
  bids_aplicados, bids_descartados, bids_reconciliados, pausas_reconciliadas,
  cortes_encolados, cortes_liberados, apply_error, apply_abortado_owner
  (bids_reconciliados/pausas_reconciliadas llegan con la reconciliacion de
  ledger sin sello de bids y de pausas applying huerfanas: ADV-03/ADV-04).
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from decimal import Decimal

import httpx
import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Json

from app import apply, apply_cola, apply_harvest
from app.ads.config import AdsCredentials
from app.apply import Aplicador
from app.optimizer import bid, cortes, hygiene, windows
from app.optimizer import goals as g
from app.optimizer.bid import PLATAFORMAS_MONEDA
from app.redaction import install_scrub_filter, scrub

logger = logging.getLogger(__name__)
install_scrub_filter(logger)

MOTOR = "ads_optimizer"


def job_key_de(platform: str) -> str:
    """job_key del lock del ciclo para `platform` (UNA fuente, regla 2).

    El cron de 4.2 y el CLI de 3.3 comparten este MISMO job_key: si
    divergieran, dos procesos correrian ciclos paralelos sin claim comun
    (dos envelopes 'running' sobre la misma plataforma). El claim atomico de
    _SQL_CLAIM se toma contra esta clave.
    """
    return f"{MOTOR}:{platform}"


# Motivos de skip del ORQUESTADOR (vocabulario cerrado; ademas se importan los
# MOTIVO_* de bid/hygiene tal cual a los contadores de notes). veto_pendiente
# viene de apply_cola (2.2 sellado 5): UNA fuente del vocabulario.
MOTIVO_SIN_GOAL = "sin_goal"
MOTIVO_GOAL_DISABLED = "goal_disabled"  # el opt-out auditable del Spec delta
MOTIVO_GOAL_MODE_OFF = "goal_mode_off"
MOTIVO_ESTADO_NO_ENABLED = "estado_no_enabled"
MOTIVO_COOLDOWN_7D = "cooldown_7d"
MOTIVO_ESCALERA_OFF = "escalera_off"
MOTIVO_VETO_PENDIENTE = apply_cola.MOTIVO_VETO_PENDIENTE

# Modo tope del goal para el envelope del ciclo: la escalera global es el
# techo, y el modo por goal solo puede BAJARLO (goal 'off' deja entidades
# fuera, no sube el ciclo). Para toda entidad que decide, el modo efectivo
# coincide con el del envelope. El RESIDUAL PR2 (hallazgo reviewer 3.1) ya
# esta RESUELTO desde 2.4: con la escalera 'live' y un goal 'shadow' el
# envelope dice 'live' y las decisiones llevan inputs.modo 'live' — y el
# aplicador JAMAS filtra por inputs.modo/cycle.mode: re-resuelve goal.mode
# POR DECISION (Aplicador.modo_efectivo, sellado 2.1).
_MODO_TOPE_ENVELOPE = "live"

# JSON con Decimals como STRING (regla 4), fechas ISO y ASCII libre.


def _dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


class CicloOcupado(Exception):
    """El claim del lock se perdio: hay un ciclo vigente de OTRO owner. Se
    lanza SIN envelope (nada corrio) y sin liberar lock ajeno."""


class ApplyAbortado(Exception):
    """Ownership-check pre-HTTP (decision 11): el lock del job ya NO es
    nuestro (lease perdido, un sucesor lo reclamo tras el TTL). Aborta la
    fase de apply FAIL-CLOSED — sin mas HTTP — sin tumbar el ciclo: las
    decisiones ya estan commitadas y el aborto es auditado (nota
    apply_abortado_owner + degraded); el siguiente ciclo reconcilia."""


class SinPerfilAplicar(Exception):
    """La fabrica del aplicador no encontro perfil ACEPTADO para la plataforma
    en GET /v2/profiles (evaluar_perfiles, la misma fuente del sync). La fase
    de apply aborta fail-closed (cero HTTP de mutacion) y el ciclo sigue con
    nota apply_error (2.4)."""


@dataclass(frozen=True)
class ResultadoCiclo:
    """Resumen del ciclo corrido. `notes` es el JSON tal como quedo persistido
    en optimizer_cycle.notes (skips, decisiones, contadores, ciclos_muertos,
    degradacion_live). status 'failed' nunca se devuelve: ese camino sella el
    envelope y RE-LANZA la excepcion original."""

    cycle_id: int
    status: str  # done | degraded | skipped
    decisions_count: int
    notes: str


# ---------------------------------------------------------------------------
# SQL del modulo (parsea el test de sintaxis con pglast)
# ---------------------------------------------------------------------------

# Claim ATOMICO en UNA sentencia (el COMMENT de ads_optimizer_lock sella el
# patron; la expiracion vive en el WHERE, jamas SELECT-luego-INSERT). Sin fila
# devuelta = lock vigente ajeno.
_SQL_CLAIM = """
INSERT INTO ads_optimizer_lock (job_key, owner)
VALUES (%s, %s)
ON CONFLICT (job_key) DO UPDATE
   SET owner = EXCLUDED.owner, claimed_at = now(), heartbeat_at = now()
 WHERE ads_optimizer_lock.heartbeat_at
       + make_interval(secs => ads_optimizer_lock.ttl_seconds) <= now()
RETURNING claimed_at
"""

# Rastro de ciclos muertos: SOLO alcanzable tras ganar el claim (si el lock
# estaba tomado, no hay ciclo vivo QUE NOS DEJE CERRAR). Residual declarado:
# con heartbeat fail-open y latidos fallando >TTL pero conexion principal
# viva, un ciclo ZOMBIE puede seguir corriendo; este rastro lo marca failed.
# Desde 2.4 el guard `status='running'` del cierre (_SQL_CERRAR_ENVELOPE) y
# el ownership-check pre-HTTP del apply CIERRAN la ventana del zombie: este
# ya NO pisa el rastro del sucesor al cerrarse (0 filas, warning, no pisa).
# El id nuevo se excluye.
_SQL_RASTRO = """
UPDATE optimizer_cycle
   SET status = 'failed', finished_at = now(),
       notes = coalesce(notes || E'\n', '')
            || 'rastro: ciclo muerto (lock expirado, reclamado por ' || %s || ')'
 WHERE motor = 'ads_optimizer' AND platform = %s::platform
   AND status = 'running' AND id <> %s
RETURNING id
"""

_SQL_ABRIR_ENVELOPE = """
INSERT INTO optimizer_cycle (motor, mode, platform)
VALUES ('ads_optimizer', %s, %s::platform)
RETURNING id
"""

# GUARD status='running' (2.4, decision 11): solo se cierra un envelope
# ABIERTO — 0 filas = alguien ya lo cerro (rastro del sucesor sobre nuestro
# zombie) y JAMAS se sobre-cierra.
_SQL_CERRAR_ENVELOPE = """
UPDATE optimizer_cycle
   SET status = %s, finished_at = now(), decisions_count = %s, notes = %s
 WHERE id = %s AND status = 'running'
"""

# Ownership-check pre-HTTP (decision 11): SELECT fresco del owner del lock.
# Fuera de transaccion explicita (READ COMMITTED de la sesion): ve el UPDATE
# commitado del sucesor aunque nuestra sesion tenga una transaccion abierta.
_SQL_OWNER_LOCK = """
SELECT owner FROM ads_optimizer_lock WHERE job_key = %s
"""

# Sello post-apply (2.4): notes['apply'] y, si la fase aborto (%s), degraded.
# Solo toca envelopes 'done'/'degraded' — si el rastro de un sucesor ya cerro
# el nuestro en 'failed', NO se pisa (jamas sobre-cerrar; auditoria del zombie).
_SQL_SELLA_APPLY = """
UPDATE optimizer_cycle
   SET status = CASE WHEN %s AND status = 'done' THEN 'degraded' ELSE status END,
       notes = %s
 WHERE id = %s AND status IN ('done', 'degraded')
"""

# applied_count por COLUMNA al final de la fase de apply (2.4, sellado 21:
# cuadra por ciclo EJECUTOR; el GRANT de 0001 cubre esta columna). No es un
# cierre: no toca status ni finished_at.
_SQL_APPLIED_COUNT_CICLO = """
UPDATE optimizer_cycle SET applied_count = %s WHERE id = %s
"""

# Guard status='running' (ADV-09, review adversaria): mismo candado que el
# cierre y el sello de apply — el rastro del sucesor puede haber cerrado YA el
# envelope del zombie en 'failed' con su nota; el sello del zombie NO pisa esa
# evidencia (la nota del rastro es el unico indicio de que hubo dos procesos).
_SQL_SELLAR_FALLIDO = """
UPDATE optimizer_cycle
   SET status = 'failed', finished_at = now(), notes = %s
 WHERE id = %s AND status = 'running'
"""

_SQL_LIBERAR_LOCK = """
DELETE FROM ads_optimizer_lock WHERE job_key = %s AND owner = %s
"""

_SQL_HEARTBEAT = """
UPDATE ads_optimizer_lock SET heartbeat_at = now() WHERE job_key = %s AND owner = %s
"""

# La escalera global y el setting de target viven en la config_version MAS
# RECIENTE (la config viva vigente). Sin filas -> escalera 'off' fail-closed.
_SQL_CONFIG_RECIENTE = """
SELECT id, settings FROM config_version ORDER BY id DESC LIMIT 1
"""

# Estructura: campanas de la plataforma con su cache de target publicado
# (tercer peldano de la cascada; NULL si la campana no tiene state).
_SQL_CAMPANAS = """
SELECT c.id, s.acos_target
  FROM ad_entity c
  LEFT JOIN ad_entity_state s ON s.ad_entity_id = c.id
 WHERE c.platform = %s::platform AND c.kind = 'campaign'
"""

# Goals de la plataforma y de las campanas encontradas (la precedencia
# campaña > plataforma se resuelve EN LA APP con goals.resuelve_goal).
_SQL_GOALS = """
SELECT scope, ad_entity_id, platform, target_acos_pct, bid_floor, bid_ceiling,
       bid_currency, harvest_campaign_id, harvest_ad_group_id,
       harvest_default_bid, enabled, mode
  FROM ads_optimizer_goal
 WHERE platform = %s::platform OR ad_entity_id = ANY(%s::bigint[])
"""

# Entidades decisoras (keyword/product_target) con SU campaña (por el ad
# group) y SU state: bid actual+moneda, status y cache de target.
# CORTES 01 (1.2): k.parent_id AS ad_group_id -- LITERAL con el alias k.
# calificado: en esta query ag.parent_id YA existe como campaign_id, y un
# parent_id desnudo seria ambiguo o, peor, mapearia la CAMPAÑA como grupo
# del termino y toda la evidencia caeria a fallback en silencio (ronda 2
# qwen). El freeze del motor de bids (1.3) consume esta columna.
_SQL_DECISORAS = """
SELECT k.id, k.parent_id AS ad_group_id, ag.parent_id AS campaign_id,
       s.current_bid, s.bid_currency, s.status, s.acos_target
  FROM ad_entity k
  JOIN ad_entity ag ON ag.id = k.parent_id AND ag.kind = 'ad_group'
  LEFT JOIN ad_entity_state s ON s.ad_entity_id = k.id
 WHERE k.platform = %s::platform AND k.kind IN ('keyword', 'product_target')
 ORDER BY k.id
"""

# Ad groups con SU campaña y state: son las entidades que portean terminos.
_SQL_GRUPOS = """
SELECT ag.id, ag.parent_id AS campaign_id, s.status
  FROM ad_entity ag
  LEFT JOIN ad_entity_state s ON s.ad_entity_id = ag.id
 WHERE ag.platform = %s::platform AND ag.kind = 'ad_group'
 ORDER BY ag.id
"""

_SQL_INSERT_DECISION = """
INSERT INTO decision (cycle_id, ad_entity_id, kind, decided_at, config_version_id,
                      data_observed_at, window_start, window_end, search_term,
                      old_value, new_value, value_currency, inputs)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


# ---------------------------------------------------------------------------
# Estructuras internas
# ---------------------------------------------------------------------------


@dataclass
class _Contadores:
    """Contadores mutables que las fases van llenando; al fallar el ciclo, el
    sello muestra CUANTO se habia procesado (parcial, honesto)."""

    skips_entidad: Counter = field(default_factory=Counter)
    skips_termino: Counter = field(default_factory=Counter)
    decisiones: Counter = field(default_factory=Counter)
    entidades: int = 0
    ad_groups: int = 0
    terminos: int = 0


@dataclass(frozen=True)
class _Pendiente:
    """Una decision lista para escribir en TX3 (la fila se congela en TX2)."""

    ad_entity_id: int
    kind: str
    data_observed_at: dt.datetime
    window_start: dt.date
    window_end: dt.date
    search_term: str | None
    old_value: Decimal | None
    new_value: Decimal | None
    value_currency: str | None
    inputs: dict


@dataclass(frozen=True)
class _Lecturas:
    """Salida de TX2: decisiones pendientes + contadores + guarda disparada
    (None = el ciclo corrio completo)."""

    pendientes: list[_Pendiente]
    guarda: windows.MotivoSkip | None


# ---------------------------------------------------------------------------
# Serializacion congelada (regla 4: Decimal como STRING; fechas ISO)
# ---------------------------------------------------------------------------


def _dec_str(valor: Decimal | None) -> str | None:
    """Decimal -> string TAL CUAL llega de la DB: la escala del string
    (ej '1.0000', '25.00') es artefacto deterministico del NUMERIC de origen
    (money_amount NUMERIC(14,4), acos NUMERIC(6,2)) y hay que CONSERVARLA —
    una "normalizacion" futura aqui romperia las comparaciones de auditoria
    y el golden replay (hallazgo reviewer 3.1)."""
    return str(valor) if valor is not None else None


def _ts(momento: dt.datetime | None) -> str | None:
    return momento.isoformat() if momento is not None else None


def _fecha_iso(fecha: dt.date | None) -> str | None:
    return fecha.isoformat() if fecha is not None else None


def _corte_json(
    corte: cortes.UmbralResuelto,
    evidencia: windows.EvidenciaAdGroup | None,
    piso: cortes.PisoResuelto | None = None,
) -> dict:
    """Freeze de `inputs.corte` TOP-LEVEL (CORTES 01; shape EXACTO del spec
    v3): umbral_clicks_usado es el FINAL con piso aplicado, expected_clicks
    viaja como string Decimal (regla 4) y evidencia es null cuando el grupo
    no esta en el dict (fallback; jamas un numero inventado). Desde 1.4, el
    camino negative congela ADEMAS el piso de cost resuelto (piso_cost_usado
    + aov como string Decimal|null; misma regla 4). `piso` None deja el
    shape EXACTO de 1.2/1.3: solo lo congela quien lo consumo (pause/bid NO
    llevan piso, sellado). Lo consumen _pendiente_bid (toda decision del
    motor de bids, 1.3) y _pendiente_termino (negative, 1.2/1.4) -- un solo
    sello, una sola fuente."""
    freeze = {
        "umbral_clicks_usado": corte.umbral,
        "elegible": corte.elegible,
        "expected_clicks": _dec_str(corte.expected_clicks),
        "evidencia": (
            {
                "clicks": evidencia.clicks,
                "orders": evidencia.orders,
                "fechas": evidencia.fechas_distintas,
                "ventana_desde": evidencia.ventana_desde.isoformat(),
                "ventana_hasta": evidencia.ventana_hasta.isoformat(),
                "observed_at_max": _ts(evidencia.observed_at_max),
            }
            if evidencia is not None
            else None
        ),
    }
    if piso is not None:
        freeze["piso_cost_usado"] = _dec_str(piso.piso_cost)
        freeze["aov"] = _dec_str(piso.aov)
    return freeze


def _sello_bitemporal(
    decided_at: dt.datetime,
    obs_directo: dt.datetime | None,
    evidencia: windows.EvidenciaAdGroup | None,
) -> dt.datetime | None:
    """data_observed_at = LEAST(decided_at, max(obs directo, observed_at_max
    de la evidencia)) -- el CLAMP es OBLIGATORIO: el CHECK
    decision_dato_no_del_futuro exige data_observed_at <= decided_at y un
    backfill que re-observa fechas viejas, una ingesta concurrente o skew de
    relojes puede producir un observed_at posterior; sin clamp, UNA fila
    aborta el executemany de TX3 (el ciclo entero de la plataforma). El
    clamp es honesto: la evidencia era visible en el snapshot de TX2, asi
    que logicamente se observo antes de decidir (ronda 2 qwen)."""
    base = obs_directo
    if evidencia is not None and evidencia.observed_at_max is not None:
        base = evidencia.observed_at_max if base is None else max(base, evidencia.observed_at_max)
    if base is None:
        return None
    return min(decided_at, base)


def _agregado_json(agg: windows.AgregadoMetricas | None) -> dict | None:
    if agg is None:
        return None
    return {
        "window_start": agg.window_start.isoformat(),
        "window_end": agg.window_end.isoformat(),
        "fechas": len(agg.fechas),
        "cost": _dec_str(agg.cost),
        "ad_revenue": _dec_str(agg.ad_revenue),
        "revenue_same_sku": _dec_str(agg.revenue_same_sku),
        "clicks": agg.clicks,
        "orders": agg.orders,
        "moneda": agg.metric_currency,
        "observed_at_max": _ts(agg.observed_at_max),
    }


def _goal_json(goal: g.Goal) -> dict:
    """Goal resuelto congelado (estructura sellada que consume el replay).
    bid_floor/bid_ceiling se congelan EFECTIVOS (resuelve_floor_ceiling):
    exactamente los que decide_bid consumio. Congelar los crudos divergiria
    del replay ante cualquier default/clamp futuro, y un None crudo romperia
    Decimal(None) en reproduce() (hallazgo CodeRabbit major)."""
    completa = (
        goal.harvest_campaign_id is not None
        or goal.harvest_ad_group_id is not None
        or goal.harvest_default_bid is not None
    )
    floor, ceiling = g.resuelve_floor_ceiling(goal)
    return {
        "scope": goal.scope,
        "target_acos_pct": _dec_str(goal.target_acos_pct),
        "bid_floor": _dec_str(floor),
        "bid_ceiling": _dec_str(ceiling),
        "harvest": (
            {
                "campaign_id": goal.harvest_campaign_id,
                "ad_group_id": goal.harvest_ad_group_id,
                "default_bid": _dec_str(goal.harvest_default_bid),
                "moneda": goal.bid_currency,
            }
            if completa
            else None
        ),
    }


def _pendiente_bid(
    entidad_id: int,
    resultado: bid.ResultadoBid,
    *,
    platform: str,
    modo: str,
    goal: g.Goal,
    ventanas: windows.VentanasEntidad,
    target: Decimal,
    bid_actual: Decimal | None,
    bid_moneda: str | None,
    decided_at: dt.datetime,
    corte: cortes.UmbralResuelto,
    evidencia: windows.EvidenciaAdGroup | None,
) -> _Pendiente:
    """El freeze de CORTES 01 (1.3): `inputs.corte` se congela en TODA
    decision del motor de bids -- INCLUIDAS las de kind final 'bid' -- porque
    decide_bid evalua PAUSE ANTES de las bandas: sin el freeze, el replay de
    un bid historico cuyo umbral adaptativo de pause BLOQUEO el corte
    rejugaria como pause con el legacy 25 y la auditoria divergiria (spec
    v3). El sello bitemporal (_sello_bitemporal) aplica al obs directo del
    agregado que decidio (cortes para pause, bids para bid) mezclado con la
    evidencia del grupo, clampeado a decided_at."""
    inputs = {
        "motor": "bid",
        "platform": platform,
        "ventanas": {
            "bids": _agregado_json(ventanas.bids),
            "cortes": _agregado_json(ventanas.cortes),
        },
        "goal": _goal_json(goal),
        "target_acos_pct_usado": _dec_str(target),
        "bid_actual": _dec_str(bid_actual),
        "bid_moneda": bid_moneda,
        "factor": _dec_str(resultado.factor),
        "motivo": resultado.motivo,
        "modo": modo,
        "corte": _corte_json(corte, evidencia),
    }
    return _Pendiente(
        ad_entity_id=entidad_id,
        kind=resultado.kind,
        data_observed_at=_sello_bitemporal(decided_at, resultado.data_observed_at, evidencia),
        window_start=resultado.window_start,
        window_end=resultado.window_end,
        search_term=None,  # bid/pause deciden sobre la entidad (CHECK del esquema)
        old_value=resultado.old_value,
        new_value=resultado.new_value,
        value_currency=resultado.value_currency,
        inputs=inputs,
    )


def _pendiente_termino(
    grupo_id: int,
    termino: windows.AgregadoTermino,
    resultado: hygiene.ResultadoTermino,
    *,
    platform: str,
    modo: str,
    goal: g.Goal,
    terminos: windows.TerminosCortes,
    target: Decimal,
    decided_at: dt.datetime,
    corte: cortes.UmbralResuelto | None = None,
    evidencia: windows.EvidenciaAdGroup | None = None,
    piso: cortes.PisoResuelto | None = None,
) -> _Pendiente:
    """El freeze de CORTES 01 (spec v3): `corte` y `evidencia` llegan SOLO
    en decisiones que consultan umbral de clicks (kind 'negative'; las
    harvest NO lo llevan, sellado). En esas decisiones se congela
    inputs.corte TOP-LEVEL (_corte_json, shape exacto del spec) y se aplica
    el sello bitemporal (_sello_bitemporal) mezclando el obs directo del
    termino con la evidencia del grupo, clampeado a decided_at. Desde 1.4,
    `piso` (PisoResuelto de cortes.piso_corte) viaja con el corte para
    congelar piso_cost_usado/aov en el MISMO freeze."""
    data_observed = resultado.data_observed_at
    inputs = {
        "motor": "hygiene",
        "platform": platform,
        "ventana_terminos": {
            "window_start": _fecha_iso(terminos.window_start),
            "window_end": _fecha_iso(terminos.window_end),
            "fechas": len(terminos.fechas_entidad),
        },
        "termino": {
            "search_term": termino.search_term,
            "cost": _dec_str(termino.cost),
            "ad_revenue": _dec_str(termino.ad_revenue),
            "clicks": termino.clicks,
            "orders": termino.orders,
            "fechas_distintas": termino.fechas_distintas,
            "moneda": termino.metric_currency,
            "observed_at_max": _ts(termino.observed_at_max),
        },
        "goal": _goal_json(goal),
        "target_acos_pct_usado": _dec_str(target),
        "motivo": resultado.motivo,
        "modo": modo,
    }
    if corte is not None:
        inputs["corte"] = _corte_json(corte, evidencia, piso)
        # sello bitemporal compartido con el motor de bids (1.3): la
        # evidencia entra al max y el LEAST clampea a decided_at
        data_observed = _sello_bitemporal(decided_at, data_observed, evidencia)
    return _Pendiente(
        ad_entity_id=grupo_id,
        kind=resultado.kind,
        data_observed_at=data_observed,
        window_start=resultado.window_start,
        window_end=resultado.window_end,
        search_term=resultado.search_term,
        old_value=None,  # negative sin dinero; harvest nace con bid nuevo
        new_value=resultado.new_value,
        value_currency=resultado.value_currency,
        inputs=inputs,
    )


def _notas_cuerpo(
    contadores: _Contadores,
    ciclos_muertos: list[int],
    degradacion_live: str | None,
    motivo_skip: str | None,
    detalle: str | None,
) -> dict:
    cuerpo = {
        "skips": {
            "entidad": dict(contadores.skips_entidad),
            "termino": dict(contadores.skips_termino),
        },
        "decisiones": dict(contadores.decisiones),
        "entidades": contadores.entidades,
        "ad_groups": contadores.ad_groups,
        "terminos": contadores.terminos,
        "ciclos_muertos": ciclos_muertos,
        "degradacion_live": degradacion_live,
    }
    if motivo_skip is not None:
        cuerpo["motivo_skip"] = motivo_skip
        cuerpo["detalle"] = detalle
    return cuerpo


def _notas_json(
    contadores: _Contadores,
    ciclos_muertos: list[int],
    degradacion_live: str | None,
    motivo_skip: str | None = None,
    detalle: str | None = None,
) -> str:
    return json.dumps(
        _notas_cuerpo(contadores, ciclos_muertos, degradacion_live, motivo_skip, detalle),
        ensure_ascii=False,
        default=str,
    )


# ---------------------------------------------------------------------------
# Heartbeat (conexion corta aparte; stance fail-open DECLARADA)
# ---------------------------------------------------------------------------


def _abre_heartbeat(conn: psycopg.Connection) -> psycopg.Connection | None:
    """Conexion corta para latidos durante TX2 (la principal sostiene un
    snapshot REPEATABLE READ: escribir el lock ahi romperia la lectura pura).
    conn.info.dsn NO trae la password (psycopg la excluye): se reinyecta desde
    PQpass. Si abrir falla: warning y None -- fail-open, el TTL es el backstop."""
    try:
        dsn = conn.info.dsn
        if conn.info.password:
            dsn = make_conninfo(dsn, password=conn.info.password)
        return psycopg.connect(dsn, autocommit=True, connect_timeout=5)
    except psycopg.Error as exc:
        logger.warning(
            "heartbeat no disponible (fail-open; el TTL es el backstop): %s", scrub(str(exc))
        )
        return None


def _tick_heartbeat(hb, job_key: str, owner: str, cada: int):
    """Closure contadora: dispara el latido cada `cada` entidades procesadas."""
    estado = {"procesadas": 0}

    def tick() -> None:
        estado["procesadas"] += 1
        if hb is None or cada <= 0 or estado["procesadas"] % cada != 0:
            return
        try:
            hb.execute(_SQL_HEARTBEAT, (job_key, owner))
        except psycopg.Error as exc:
            # FAIL-OPEN declarado: un latido transitorio no mata el ciclo.
            logger.warning("heartbeat fallo (TTL es el backstop): %s", scrub(str(exc)))

    return tick


def _guard_apply(
    conn: psycopg.Connection, job_key: str, owner: str, tick_heartbeat: Callable[[], None]
) -> Callable[[], None]:
    """El `tick` del Aplicador en la fase de apply (decision 11): (a) heartbeat
    — mutaciones lentas no dejan morir el lease; (b) ownership-check fresco
    contra ads_optimizer_lock. Lease perdido -> ApplyAbortado: aborto
    fail-closed de la fase (el guard tambien vive en el transport del write
    client, ver app/apply 2.4), SIN tumbar el ciclo."""

    def guard() -> None:
        tick_heartbeat()
        fila = conn.execute(_SQL_OWNER_LOCK, (job_key,)).fetchone()
        if fila is None or fila[0] != owner:
            dueno = fila[0] if fila is not None else "sin lock"
            raise ApplyAbortado(f"lock {job_key} ya no es nuestro (owner: {dueno})")

    return guard


def _aplicador_real(
    conn: psycopg.Connection,
    *,
    platform: str,
    cycle_id_ejecutor: int,
    owner: str,
    job_key: str,
    tick: Callable[[], None] | None,
    transport: httpx.BaseTransport | None = None,
) -> Aplicador:
    """Fabrica por defecto del aplicador (2.4). Credenciales del secrets dir
    (`AdsCredentials.from_secrets_dir`) y profile por plataforma desde GET
    /v2/profiles + `evaluar_perfiles` — via `apply.perfil_aceptado_de` (la
    MISMA fuente del sync y de la reversa manual de 3.1, regla 2; el
    profile_id NO se inventa ni se hardcodea). Sin perfil aceptado ->
    SinPerfilAplicar (la fase aborta fail-closed). `transport` es la puerta de
    tests (MockTransport); `tick` viaja como tick Y guard pre-HTTP del write
    client (heartbeat + ownership-check, decision 11)."""
    credentials = AdsCredentials.from_secrets_dir()
    perfil = apply.perfil_aceptado_de(credentials, platform, transport=transport)
    if perfil is None:
        raise SinPerfilAplicar(f"sin perfil aceptado para {platform} en /v2/profiles")
    return Aplicador(
        conn,
        platform=platform,
        cycle_id_ejecutor=cycle_id_ejecutor,
        owner=owner,
        job_key=job_key,
        tick=tick,
        guard_http=tick,
        transport=transport,
        credentials=credentials,
        profile_id=perfil.profile_id,
    )


# ---------------------------------------------------------------------------
# Fases de TX1
# ---------------------------------------------------------------------------


def _config_reciente(conn: psycopg.Connection) -> tuple[int | None, dict]:
    fila = conn.execute(_SQL_CONFIG_RECIENTE).fetchone()
    if fila is None:
        return (None, {})  # sin config -> escalera 'off' fail-closed
    return (fila[0], fila[1])


def _toma_claim(conn: psycopg.Connection, job_key: str, owner: str) -> bool:
    return conn.execute(_SQL_CLAIM, (job_key, owner)).fetchone() is not None


def _abre_envelope(conn: psycopg.Connection, modo: str, platform: str) -> int:
    return conn.execute(_SQL_ABRIR_ENVELOPE, (modo, platform)).fetchone()[0]


def _cierra_rastro(
    conn: psycopg.Connection, owner: str, platform: str, ciclo_nuevo: int
) -> list[int]:
    filas = conn.execute(_SQL_RASTRO, (owner, platform, ciclo_nuevo)).fetchall()
    return [fila[0] for fila in filas]


# ---------------------------------------------------------------------------
# Fase de escrituras (TX3)
# ---------------------------------------------------------------------------


def _inserta_decisiones(
    conn: psycopg.Connection,
    cycle_id: int,
    config_version_id: int,
    decided_at: dt.datetime,
    pendientes: list[_Pendiente],
) -> None:
    if not pendientes:
        return
    filas = [
        (
            cycle_id,
            p.ad_entity_id,
            p.kind,
            decided_at,
            config_version_id,
            p.data_observed_at,
            p.window_start,
            p.window_end,
            p.search_term,
            p.old_value,
            p.new_value,
            p.value_currency,
            Json(p.inputs, dumps=_dumps),
        )
        for p in pendientes
    ]
    conn.cursor().executemany(_SQL_INSERT_DECISION, filas)


def _cierra_envelope(
    conn: psycopg.Connection, cycle_id: int, status: str, decisions_count: int, notes: str
) -> bool:
    """Cierra el envelope SOLO si sigue 'running' (guard 2.4). False = 0 filas:
    alguien ya lo cerro (rastro de un sucesor sobre nuestro zombie) — warning
    y NO pisar; las decisiones ya commitadas no se borran por esto."""
    filas = conn.execute(_SQL_CERRAR_ENVELOPE, (status, decisions_count, notes, cycle_id)).rowcount
    if filas == 0:
        logger.warning(
            "cierre del envelope %s ignorado: ya no esta 'running' (rastro de un "
            "sucesor o cierre previo) — no se pisa",
            cycle_id,
        )
        return False
    return True


def _sella_apply(conn: psycopg.Connection, cycle_id: int, notes: str, *, degradar: bool) -> bool:
    """Sello post-apply: notes['apply'] y degraded si la fase aborto (2.4).
    Misma regla de no-sobre-cerrar: un envelope cerrado en 'failed' por el
    rastro de un sucesor NO se toca."""
    filas = conn.execute(_SQL_SELLA_APPLY, (degradar, notes, cycle_id)).rowcount
    if filas == 0:
        logger.warning(
            "sello de apply del envelope %s ignorado: cerrado por otro (rastro)",
            cycle_id,
        )
        return False
    return True


def _sello_fallido(
    conn: psycopg.Connection,
    cycle_id: int,
    exc: BaseException,
    contadores: _Contadores,
    ciclos_muertos: list[int],
) -> None:
    """Sello best-effort (patron ingest_run): 'failed' + error scrubbado. Si la
    conexion tambien esta muerta, queda el warning y el rastro del ciclo muerto
    lo cierra el SIGUIENTE ciclo que gane el claim tras el TTL. Guard
    status='running' (ADV-09): un envelope ya cerrado en 'failed' por el rastro
    del sucesor NO se pisa (jamas sobre-cerrar)."""
    cuerpo = _notas_cuerpo(contadores, ciclos_muertos, None, None, None)
    cuerpo["error"] = scrub(str(exc)) or type(exc).__name__
    try:
        with conn.transaction():
            filas = conn.execute(
                _SQL_SELLAR_FALLIDO, (json.dumps(cuerpo, ensure_ascii=False, default=str), cycle_id)
            ).rowcount
        if filas == 0:
            logger.warning(
                "sello del ciclo fallido %s ignorado: ya no esta 'running' (rastro del"
                " sucesor) — no se pisa",
                cycle_id,
            )
    except Exception:  # noqa: BLE001 - el sello es best-effort, la original sube
        logger.warning(
            "sello del ciclo fallido tambien fallo (ciclo %s): %s",
            cycle_id,
            scrub(str(exc)),
        )


def _libera_lock(hb, conn: psycopg.Connection, job_key: str, owner: str) -> None:
    """Liberacion por OWNER: jamas borra el lock de un sucesor que lo reclamo
    legitimamente tras el TTL. Se prefiere la conexion de heartbeat (viva aunque
    la principal murio); best-effort con warning."""
    try:
        if hb is not None:
            hb.execute(_SQL_LIBERAR_LOCK, (job_key, owner))
        else:
            with conn.transaction():
                conn.execute(_SQL_LIBERAR_LOCK, (job_key, owner))
    except psycopg.Error as exc:
        logger.warning("liberacion del lock fallo (TTL es el backstop): %s", scrub(str(exc)))


# ---------------------------------------------------------------------------
# Fase de lecturas (TX2, REPEATABLE READ, CERO escrituras)
# ---------------------------------------------------------------------------


def _lee_goals(
    conn: psycopg.Connection, platform: str, ids_campanas: list[int]
) -> tuple[g.Goal | None, dict[int, g.Goal]]:
    fila_goal = conn.execute(_SQL_GOALS, (platform, ids_campanas)).fetchall()
    goal_plataforma: g.Goal | None = None
    por_campana: dict[int, g.Goal] = {}
    for fila in fila_goal:
        goal = g.Goal(
            scope=fila[0],
            ad_entity_id=fila[1],
            platform=fila[2],
            target_acos_pct=fila[3],
            bid_floor=fila[4],
            bid_ceiling=fila[5],
            bid_currency=fila[6],
            harvest_campaign_id=fila[7],
            harvest_ad_group_id=fila[8],
            harvest_default_bid=fila[9],
            enabled=fila[10],
            mode=fila[11],
        )
        if goal.scope == "platform":
            goal_plataforma = goal
        elif goal.ad_entity_id is not None:
            por_campana[goal.ad_entity_id] = goal
    return (goal_plataforma, por_campana)


def _porta_goal_campana(
    por_campana: dict[int, g.Goal], goal_plataforma: g.Goal | None, campaign_id
) -> tuple[g.Goal | None, str | None]:
    """Gate de campaña (2.4 resuelto EN LA APP). None + motivo = fuera."""
    resuelto = g.resuelve_goal(por_campana.get(campaign_id), goal_plataforma)
    if resuelto is None:
        return (None, MOTIVO_SIN_GOAL)
    if not resuelto.enabled:
        return (None, MOTIVO_GOAL_DISABLED)
    if resuelto.mode == "off":
        return (None, MOTIVO_GOAL_MODE_OFF)
    return (resuelto, None)


def _config_harvest_de(
    conn: psycopg.Connection, goal: g.Goal, platform: str
) -> tuple[hygiene.ConfigHarvest | None, frozenset[str]]:
    """Config de harvest del goal resuelto + keywords EXACT de la campaña
    destino (dedupe). Incompleta -> (None, frozenset()) y decide_hygiene salta
    CON MOTIVO 'harvest_sin_config' (jamas placeholder; CHECK goal_harvest_completo)."""
    if (
        goal.harvest_campaign_id is None
        or goal.harvest_ad_group_id is None
        or goal.harvest_default_bid is None
    ):
        return (None, frozenset())
    config = hygiene.ConfigHarvest(
        campaign_id=goal.harvest_campaign_id,
        ad_group_id=goal.harvest_ad_group_id,
        default_bid=goal.harvest_default_bid,
        moneda=goal.bid_currency,
    )
    return (config, hygiene.keywords_campana_destino(conn, platform, goal.harvest_campaign_id))


def _gates_entidad(
    conn: psycopg.Connection,
    goals: tuple[g.Goal | None, dict[int, g.Goal]],
    *,
    campaign_id,
    entidad_id: int,
    status,
    decided_at: dt.datetime,
) -> tuple[g.Goal | None, str | None]:
    """Cascada de gates del orquestador (orden sellado, ver docstring del
    modulo): campaña -> estado -> cooldown. None = elegible."""
    goal_plataforma, por_campana = goals
    goal, motivo = _porta_goal_campana(por_campana, goal_plataforma, campaign_id)
    if motivo is None and status != "ENABLED":
        motivo = MOTIVO_ESTADO_NO_ENABLED  # None (sin state) tambien queda fuera
    if motivo is None and g.en_cooldown(conn, entidad_id, ahora=decided_at):
        motivo = MOTIVO_COOLDOWN_7D
    return (goal, motivo)


def _procesa_decisora(
    conn: psycopg.Connection,
    *,
    fila,
    platform: str,
    setting_target: Decimal | None,
    goals: tuple[g.Goal | None, dict[int, g.Goal]],
    modo: str,
    decided_at: dt.datetime,
    contadores: _Contadores,
    pendientes: list[_Pendiente],
    tick,
    evidencia_ad_groups: dict[int, windows.EvidenciaAdGroup],
    corte_pause_por_grupo: dict[int, tuple[cortes.UmbralResuelto, windows.EvidenciaAdGroup | None]],
    bloqueadas: set[tuple[int, str, str | None]],
) -> None:
    entidad_id, ad_group_id, campaign_id, current_bid, bid_currency, status, acos_cache = fila
    if (entidad_id, "entity_cut", None) in bloqueadas:
        # 2.2 sellado 5 / 2.4: clave de efecto en vuelo (fila NO terminal o
        # veto vigente) — el ciclo NO re-decide esa clave. Salta la entidad
        # ENTERA del motor de bids (decide_bid evalua pause y banda en una
        # sola llamada; prohibir solo el pause exigiria inventar un umbral,
        # regla 3 — declarado en el docstring del modulo).
        contadores.skips_entidad[MOTIVO_VETO_PENDIENTE] += 1
        tick()
        return
    goal, motivo = _gates_entidad(
        conn,
        goals,
        campaign_id=campaign_id,
        entidad_id=entidad_id,
        status=status,
        decided_at=decided_at,
    )
    if motivo is not None:
        contadores.skips_entidad[motivo] += 1
        tick()
        return
    assert goal is not None  # _porta_goal_campana: motivo None implica goal
    # CORTES 01 (1.3): umbral pause del GRUPO (k.parent_id de
    # _SQL_DECISORAS) resuelto con LA MISMA funcion que negative, UNA vez
    # por ad group y ciclo (cache lazy del recorrido); entidad cuyo grupo
    # no esta en el dict -> evidencia None -> fallback 50 con piso legacy
    # 25 (regla 3: jamas un numero inventado)
    if ad_group_id not in corte_pause_por_grupo:
        evidencia = evidencia_ad_groups.get(ad_group_id)
        corte_pause_por_grupo[ad_group_id] = (cortes.umbral_corte(evidencia, "pause"), evidencia)
    corte_pause, evidencia = corte_pause_por_grupo[ad_group_id]
    ventanas = windows.ventanas_entidad(conn, entidad_id, decided_at)
    target = g.cascada_target_acos(goal.target_acos_pct, setting_target, acos_cache)
    floor, ceiling = g.resuelve_floor_ceiling(goal)
    resultado = bid.decide_bid(
        platform=platform,
        bids=ventanas.bids,
        cortes=ventanas.cortes,
        target_acos_pct=target,
        bid_actual=current_bid,
        bid_moneda=bid_currency,
        floor=floor,
        ceiling=ceiling,
        umbral_pause=corte_pause.umbral,
    )
    tick()
    if resultado.kind is None:
        contadores.skips_entidad[resultado.motivo] += 1
        return
    pendientes.append(
        _pendiente_bid(
            entidad_id,
            resultado,
            platform=platform,
            modo=modo,
            goal=goal,
            ventanas=ventanas,
            target=target,
            bid_actual=current_bid,
            bid_moneda=bid_currency,
            decided_at=decided_at,
            corte=corte_pause,
            evidencia=evidencia,
        )
    )
    contadores.decisiones[resultado.kind] += 1


def _procesa_grupo(
    conn: psycopg.Connection,
    *,
    fila,
    platform: str,
    setting_target: Decimal | None,
    goals: tuple[g.Goal | None, dict[int, g.Goal]],
    acos_campanas: dict[int, Decimal | None],
    modo: str,
    decided_at: dt.datetime,
    contadores: _Contadores,
    pendientes: list[_Pendiente],
    tick,
    evidencia_ad_groups: dict[int, windows.EvidenciaAdGroup],
    bloqueadas: set[tuple[int, str, str | None]],
) -> None:
    grupo_id, campaign_id, status = fila
    terminos = windows.terminos_cortes(conn, grupo_id, decided_at)
    contadores.terminos += len(terminos.terminos)
    # 2.2 sellado 5 / 2.4: los terminos cuya clave de efecto (grupo,
    # term_cut, search_term) esta bloqueada NO se re-deciden; los demas
    # avanzan. La ventana/fechas de la entidad se conservan: el filtro es de
    # terminos, no de la ventana (mismo snapshot de TX2).
    libres = tuple(
        t for t in terminos.terminos if (grupo_id, "term_cut", t.search_term) not in bloqueadas
    )
    bloqueados = len(terminos.terminos) - len(libres)
    if bloqueados:
        contadores.skips_termino[MOTIVO_VETO_PENDIENTE] += bloqueados
        terminos = replace(terminos, terminos=libres)
    goal, motivo = _gates_entidad(
        conn,
        goals,
        campaign_id=campaign_id,
        entidad_id=grupo_id,
        status=status,
        decided_at=decided_at,
    )
    if motivo is not None:
        # ad group fuera: sus terminos TODOS skip con ese motivo
        contadores.skips_termino[motivo] += len(terminos.terminos)
        tick()
        return
    assert goal is not None
    # CORTES 01 (1.2/1.4): umbral de clicks Y piso de cost del GRUPO
    # resueltos UNA vez por ciclo (misma evidencia de la ventana D-90..D-10;
    # grupo ausente del dict -> evidencia None -> fallback/respaldo con piso
    # legacy). El piso es por plataforma (moneda manda) y SOLO del camino
    # negative.
    evidencia = evidencia_ad_groups.get(grupo_id)
    corte_negativo = cortes.umbral_corte(evidencia, "negative")
    piso_neg = cortes.piso_corte(evidencia, platform)
    cache_campana = acos_campanas.get(campaign_id)
    target = g.cascada_target_acos(goal.target_acos_pct, setting_target, cache_campana)
    config_harvest, keywords = _config_harvest_de(conn, goal, platform)
    resultados = hygiene.decide_hygiene(
        platform=platform,
        terminos=terminos,
        target_acos_pct=target,
        config_harvest=config_harvest,
        keywords_existentes=keywords,
        umbral_negative=corte_negativo.umbral,
        piso_negative=piso_neg.piso_cost,
    )
    tick()
    for termino, resultado in zip(terminos.terminos, resultados, strict=True):
        if resultado.kind is None:
            contadores.skips_termino[resultado.motivo] += 1
            continue
        pendientes.append(
            _pendiente_termino(
                grupo_id,
                termino,
                resultado,
                platform=platform,
                modo=modo,
                goal=goal,
                terminos=terminos,
                target=target,
                decided_at=decided_at,
                # el freeze SOLO en decisiones que consultan umbral de
                # clicks: negative (las harvest NO llevan inputs.corte)
                corte=corte_negativo if resultado.kind == "negative" else None,
                evidencia=evidencia if resultado.kind == "negative" else None,
                piso=piso_neg if resultado.kind == "negative" else None,
            )
        )
        contadores.decisiones[resultado.kind] += 1


def _recorre_plataforma(
    conn: psycopg.Connection,
    *,
    platform: str,
    settings: dict,
    modo: str,
    decided_at: dt.datetime,
    contadores: _Contadores,
    pendientes: list[_Pendiente],
    tick,
    bloqueadas: set[tuple[int, str, str | None]],
) -> None:
    setting_target = g.target_desde_settings(settings, platform)
    acos_campanas = {
        fila[0]: fila[1] for fila in conn.execute(_SQL_CAMPANAS, (platform,)).fetchall()
    }
    goals = _lee_goals(conn, platform, list(acos_campanas))
    evidencia_ad_groups = windows.ventanas_evidencia_ad_group(conn, platform, decided_at)
    # CORTES 01 (1.2/1.3): evidencia por ad group, UNA consulta por
    # plataforma DENTRO de TX2 junto a las demas lecturas (mismo snapshot
    # REPEATABLE READ; spec: una ventana, una elegibilidad, un multiplicador).
    # Cache lazy de la resolucion pause POR GRUPO (hallazgo codex+kimi,
    # cross-review 1.3): el spec sella "por ad group, una vez por ciclo" --
    # la primera entidad decisora del grupo resuelve, las demas reusan el
    # MISMO UmbralResuelto (la funcion es pura, pero la letra del contrato
    # y la simetria con negative -- una vez por grupo en _procesa_grupo --
    # exigen no recalcular por entidad).
    corte_pause_por_grupo: dict[
        int, tuple[cortes.UmbralResuelto, windows.EvidenciaAdGroup | None]
    ] = {}
    comunes = dict(
        platform=platform,
        setting_target=setting_target,
        goals=goals,
        modo=modo,
        decided_at=decided_at,
        contadores=contadores,
        pendientes=pendientes,
        tick=tick,
        evidencia_ad_groups=evidencia_ad_groups,
        bloqueadas=bloqueadas,
    )
    for fila in conn.execute(_SQL_DECISORAS, (platform,)).fetchall():
        contadores.entidades += 1
        _procesa_decisora(conn, fila=fila, corte_pause_por_grupo=corte_pause_por_grupo, **comunes)
    for fila in conn.execute(_SQL_GRUPOS, (platform,)).fetchall():
        contadores.ad_groups += 1
        _procesa_grupo(conn, fila=fila, acos_campanas=acos_campanas, **comunes)


def _fase_lecturas(
    conn: psycopg.Connection,
    *,
    platform: str,
    settings: dict,
    modo: str,
    decided_at: dt.datetime,
    contadores: _Contadores,
    pendientes: list[_Pendiente],
    tick,
) -> windows.MotivoSkip | None:
    """TX2: guarda de plataforma + claves de efecto bloqueadas + recorrido
    completo, TODO en una transaccion REPEATABLE READ (snapshot uniforme; SET
    TRANSACTION como PRIMERA sentencia). Devuelve la guarda disparada (None =
    ciclo completo)."""
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        guarda = windows.guarda_plataforma(conn, platform, ahora=decided_at)
        if guarda is not None:
            return guarda
        bloqueadas = apply_cola.claves_bloqueadas(conn, platform, decided_at)
        _recorre_plataforma(
            conn,
            platform=platform,
            settings=settings,
            modo=modo,
            decided_at=decided_at,
            contadores=contadores,
            pendientes=pendientes,
            tick=tick,
            bloqueadas=bloqueadas,
        )
    return None


# ---------------------------------------------------------------------------
# Fase de apply (2.4): TX4 + apply propio, DENTRO del lock
# ---------------------------------------------------------------------------


def _fase_apply(
    conn: psycopg.Connection,
    *,
    cycle_id: int,
    modo: g.ModoEfectivo,
    platform: str,
    decided_at: dt.datetime,
    job_key: str,
    owner: str,
    guard: Callable[[], None],
    aplicador_factory: Callable[..., Aplicador] | None,
) -> tuple[dict, bool]:
    """TX4 + fase de apply propiamente (decisiones 11/21; docstring del
    modulo). Devuelve (seccion notes['apply'], fallo). Stance
    FAIL-CLOSED-AUDITADO: CUALQUIER excepcion de la fase (fabrica incluida) se
    captura como nota + degraded — un fallo de apply no borra las decisiones
    ya tomadas; el siguiente ciclo reconcilia. El invariante corte<->cola
    (2.2 sellado 4) se conserva incluso si la fabrica aborta: TX4 corre con
    un Aplicador sin credenciales (solo modo_efectivo, cero HTTP).

    DISCIPLINA DE TRANSACCIONES (ADV-01, review adversaria): con la conexion
    de produccion (SIN autocommit, como app.db.connect) todo `with
    conn.transaction():` que entra con una tx implicita abierta es un
    SAVEPOINT que JAMAS commitea — y el CLI cierra sin commit. Por eso: (a)
    TX4 envuelve `encola_cortes` ENTERO y entra desde IDLE (commit real como
    TX1/TX3; los savepoints por fila del encolado se conservan DENTRO); (b) el
    camino de EXITO hace conn.commit() al final (lo pre-HTTP ya era durable
    por los commits del ledger, esto deja IDLE lo restante) y cada `except`
    arranca con conn.rollback() — asi el sello `_sella_apply` (y el
    `_sello_fallido` de corre_ciclo) commitean DE VERDAD. NADA de commit por
    fila en TX4 ni dentro de _sella_apply."""
    notas: dict = {}
    fallo = False
    aplicador: Aplicador | None = None
    if modo.modo == "live":
        try:
            fabrica = aplicador_factory if aplicador_factory is not None else _aplicador_real
            aplicador = fabrica(
                conn,
                platform=platform,
                cycle_id_ejecutor=cycle_id,
                owner=owner,
                job_key=job_key,
                tick=guard,
            )
        except Exception as exc:  # noqa: BLE001 - fail-closed-auditado (docstring)
            notas["apply_error"] = scrub(str(exc)) or type(exc).__name__
            fallo = True
    if aplicador is None:
        # Shadow (o fabrica abortada): Aplicador SOLO modo_efectivo, sin
        # credenciales ni profile — jamas construye cliente (fail-closed).
        aplicador = Aplicador(
            conn,
            platform=platform,
            cycle_id_ejecutor=cycle_id,
            owner=owner,
            job_key=job_key,
            tick=guard,
        )
    try:
        with conn.transaction():  # TX4 REAL (ADV-01): entra desde IDLE, commitea
            resumen = apply_cola.encola_cortes(
                conn, aplicador, cycle_id, modo_envelope=modo.modo, ahora=decided_at
            )
        notas["cortes_encolados"] = {
            "live": resumen.encoladas_live,
            "shadow": resumen.encoladas_shadow,
            "choques": len(resumen.choques),
        }
        if modo.modo == "live" and "apply_error" not in notas:
            reconciliacion = apply_harvest.reconcilia_harvest(conn, aplicador, platform)
            rec_bids = apply.reconcilia_bids(conn, aplicador, platform)
            res_bids = aplicador.aplica_bids(
                apply.bids_del_ciclo(conn, cycle_id), escalera_global=modo.modo
            )
            res_cola = apply_cola.libera_vencidos(
                conn, platform, ahora=decided_at, aplicador=aplicador
            )
            notas["bids_aplicados"] = res_bids.aplicadas
            notas["bids_descartados"] = len(res_bids.descartadas)
            # GK10/QW4 (cross-review): la divergencia de readback es observable
            # en notes['apply'] (la mutacion salio y Amazon quedo con OTRO bid).
            notas["bids_divergentes"] = res_bids.divergencias
            notas["bids_reconciliados"] = {
                "confirmados": rec_bids[0],
                "fallidos": rec_bids[1],
            }
            notas["pausas_reconciliadas"] = {
                "confirmadas": reconciliacion.pausas_confirmadas,
                "fallidas": reconciliacion.pausas_fallidas,
            }
            notas["cortes_liberados"] = {
                "liberadas": res_cola.liberadas,
                "aplicadas": res_cola.aplicadas,
                "fallidas": res_cola.fallidas,
                "sin_quota": res_cola.sin_quota,
                "carreras_perdidas": res_cola.carreras_perdidas,
            }
            # applied_count por COLUMNA al final de la fase (sellado 21): el
            # total confirmado de ESTE ciclo ejecutor. En aborto no se toca:
            # los incrementos por mutacion de _confirma_resumen ya quedaron
            # (parcial honesto; la fuente de verdad es decision_application).
            total = (
                res_bids.aplicadas
                + res_cola.aplicadas
                + reconciliacion.jobs_done
                + reconciliacion.negativas_confirmadas
                + reconciliacion.pausas_confirmadas
                + rec_bids[0]
            )
            with conn.transaction():
                conn.execute(_SQL_APPLIED_COUNT_CICLO, (total, cycle_id))
        conn.commit()  # ADV-01: la fase deja la conexion IDLE (sello real)
    except ApplyAbortado as exc:
        conn.rollback()  # ADV-01: IDLE para que el sello de apply commitee
        notas["apply_abortado_owner"] = True
        fallo = True
        logger.warning("fase de apply abortada (lease perdido): %s", scrub(str(exc)))
    except Exception as exc:  # noqa: BLE001 - fail-closed-auditado (docstring)
        conn.rollback()  # ADV-01: lo pre-HTTP ya es durable (commits del ledger)
        notas["apply_error"] = scrub(str(exc)) or type(exc).__name__
        fallo = True
        logger.warning("fase de apply abortada por error: %s", scrub(str(exc)))
    return notas, fallo


# ---------------------------------------------------------------------------
# Orquestacion de fases tras TX1
# ---------------------------------------------------------------------------


def _corre_fases(
    conn: psycopg.Connection,
    *,
    cycle_id: int,
    config_id: int | None,
    settings: dict,
    modo: g.ModoEfectivo,
    platform: str,
    decided_at: dt.datetime,
    ciclos_muertos: list[int],
    contadores: _Contadores,
    job_key: str,
    owner: str,
    hb,
    heartbeat_cada: int,
    aplicador_factory: Callable[..., Aplicador] | None = None,
) -> ResultadoCiclo:
    if modo.modo == "off":
        notas = _notas_json(
            contadores, ciclos_muertos, modo.nota, MOTIVO_ESCALERA_OFF, "escalera global off"
        )
        with conn.transaction():
            _cierra_envelope(conn, cycle_id, "skipped", 0, notas)
        return ResultadoCiclo(cycle_id, "skipped", 0, notas)
    tick = _tick_heartbeat(hb, job_key, owner, heartbeat_cada)
    pendientes: list[_Pendiente] = []
    guarda = _fase_lecturas(
        conn,
        platform=platform,
        settings=settings,
        modo=modo.modo,
        decided_at=decided_at,
        contadores=contadores,
        pendientes=pendientes,
        tick=tick,
    )
    motivo = f"guarda_{guarda.guarda}" if guarda is not None else None
    cuerpo = _notas_cuerpo(
        contadores,
        ciclos_muertos,
        modo.nota,
        motivo,
        guarda.detalle if guarda is not None else None,
    )
    status = "degraded" if guarda is not None else "done"
    notas = json.dumps(cuerpo, ensure_ascii=False, default=str)
    with conn.transaction():  # TX3: decisiones + cierre del envelope, atomicos
        _inserta_decisiones(conn, cycle_id, config_id, decided_at, pendientes)
        _cierra_envelope(conn, cycle_id, status, len(pendientes), notas)
    # FASE DE APPLY DENTRO DEL LOCK (2.4): TX4 + apply propio, DESPUES de TX3
    # (las decisiones ya estan commitadas) y ANTES del return — el lock se
    # libera recien en el finally de corre_ciclo, con el apply ya corrido.
    notas_apply, fallo_apply = _fase_apply(
        conn,
        cycle_id=cycle_id,
        modo=modo,
        platform=platform,
        decided_at=decided_at,
        job_key=job_key,
        owner=owner,
        guard=_guard_apply(conn, job_key, owner, tick),
        aplicador_factory=aplicador_factory,
    )
    if notas_apply:
        cuerpo["apply"] = notas_apply
        notas = json.dumps(cuerpo, ensure_ascii=False, default=str)
        if fallo_apply:
            status = "degraded"
        with conn.transaction():
            _sella_apply(conn, cycle_id, notas, degradar=fallo_apply)
    return ResultadoCiclo(cycle_id, status, len(pendientes), notas)


# ---------------------------------------------------------------------------
# API publica
# ---------------------------------------------------------------------------


def corre_ciclo(
    conn: psycopg.Connection,
    *,
    platform: str,
    owner: str,
    decided_at: dt.datetime,
    heartbeat_cada: int = 25,
    aplicador_factory: Callable[..., Aplicador] | None = None,
) -> ResultadoCiclo:
    """Corre UN ciclo del optimizador para `platform` (amazon_us|amazon_mx).

    `conn` llega conectada y el modulo gestiona sus transacciones (la deja en
    IDLE al empezar). `decided_at` es el RELOJ de las decisiones: tz-aware
    OBLIGATORIO (windows/goals lo exigen); el reloj del LOCK es now() de la DB
    (atomicidad del claim en una sola sentencia). `owner` identifica al
    proceso (ej hostname:pid). CicloOcupado si el lock esta vigente ajeno (sin
    envelope). Cualquier otra excepcion: envelope sellado 'failed' con el error
    scrubbado, lock liberado y la original RE-LANZADA (fail-closed). Desde
    2.4, la fase de apply (TX4 + aplicador) corre DENTRO del lock y sus
    fallos son fail-closed-auditados (nota + degraded, SIN re-lanzar).
    `aplicador_factory` inyecta la construccion del Aplicador para tests
    (default: `_aplicador_real`, credenciales del secrets dir + profile por
    GET /v2/profiles con evaluar_perfiles).
    """
    if decided_at.tzinfo is None:
        raise ValueError("decided_at debe ser tz-aware (UTC): un naive evaluaria segun la TZ local")
    if platform not in PLATAFORMAS_MONEDA:
        raise ValueError(f"plataforma fuera del vocabulario sellado: {platform!r}")
    if heartbeat_cada <= 0:
        raise ValueError("heartbeat_cada debe ser > 0")
    job_key = job_key_de(platform)
    conn.commit()  # no-op en IDLE: el modulo empieza desde estado limpio
    hb = _abre_heartbeat(conn)
    cycle_id: int | None = None
    gano = False  # quien NUNCA gano el claim no libera NADA (hallazgo CodeRabbit)
    contadores = _Contadores()
    muertos: list[int] = []
    try:
        with conn.transaction():  # TX1: claim + envelope + rastro (commit)
            config_id, settings = _config_reciente(conn)
            modo = g.resuelve_modo(g.modo_desde_settings(settings), _MODO_TOPE_ENVELOPE)
            if not _toma_claim(conn, job_key, owner):
                raise CicloOcupado(f"lock vigente de otro owner para {job_key}")
            gano = True
            cycle_id = _abre_envelope(conn, modo.modo, platform)
            muertos = _cierra_rastro(conn, owner, platform, cycle_id)
        return _corre_fases(
            conn,
            cycle_id=cycle_id,
            config_id=config_id,
            settings=settings,
            modo=modo,
            platform=platform,
            decided_at=decided_at,
            ciclos_muertos=muertos,
            contadores=contadores,
            job_key=job_key,
            owner=owner,
            hb=hb,
            heartbeat_cada=heartbeat_cada,
            aplicador_factory=aplicador_factory,
        )
    except BaseException as exc:  # noqa: BLE001 - sello + re-lanzamiento sellado
        if cycle_id is not None:  # CicloOcupado no tiene envelope que sellar
            _sello_fallido(conn, cycle_id, exc, contadores, muertos)
        raise
    finally:
        # Solo libera quien GANO el claim: con owners iguales entre procesos
        # (owner fijo de config, o hostname:pid repetido entre contenedores),
        # el perdedor de CicloOcupado borraria el lock del ganador y dejaria
        # dos ciclos escribiendo en paralelo (hallazgo CodeRabbit major). El
        # WHERE owner ya cubria al sucesor; esto cubre al "nunca lo tuve".
        if gano:
            _libera_lock(hb, conn, job_key, owner)
        if hb is not None:
            hb.close()


# ---------------------------------------------------------------------------
# Replay publico (corazon de la auditabilidad; spot-check humano de 4.4)
# ---------------------------------------------------------------------------


def _dec_de_json(valor) -> Decimal | None:
    """Decimal de vuelta desde el string congelado (regla 4; nunca float)."""
    return Decimal(str(valor)) if valor is not None else None


def _fechas_sinteticas(window_end: dt.date, n: int) -> tuple[dt.date, ...]:
    """n fechas dentro de la ventana terminando en window_end: el CONTEO es lo
    que replayea `completa` (>= 7 fechas); el replay sintetiza las fechas."""
    return tuple(window_end - dt.timedelta(days=n - 1 - i) for i in range(n))


def _agregado_sintetico(d: dict | None) -> windows.AgregadoMetricas | None:
    if d is None:
        return None
    fin = dt.date.fromisoformat(d["window_end"])
    observed = d["observed_at_max"]
    return windows.AgregadoMetricas(
        window_start=dt.date.fromisoformat(d["window_start"]),
        window_end=fin,
        fechas=_fechas_sinteticas(fin, d["fechas"]),
        metric_currency=d["moneda"],
        cost=_dec_de_json(d["cost"]),
        ad_revenue=_dec_de_json(d["ad_revenue"]),
        revenue_same_sku=_dec_de_json(d["revenue_same_sku"]),
        impressions=None,  # el motor de bids no lo consume; no se congelo
        clicks=d["clicks"],
        orders=d["orders"],
        observed_at_max=dt.datetime.fromisoformat(observed) if observed else None,
    )


def _replay_bid(inputs: dict) -> bid.ResultadoBid:
    goal = inputs["goal"]
    # CORTES 01 (spec): el replay LEE inputs.corte.umbral_clicks_usado, JAMAS
    # recalcula evidencia (el snapshot de la decision ya no existe). Fila
    # historica sin la clave (pre-CORTES) -> LEGACY_PAUSE 25, replay exacto.
    corte = inputs.get("corte")
    umbral_pause = corte["umbral_clicks_usado"] if corte is not None else cortes.LEGACY_PAUSE
    return bid.decide_bid(
        platform=inputs["platform"],
        bids=_agregado_sintetico(inputs["ventanas"]["bids"]),
        cortes=_agregado_sintetico(inputs["ventanas"]["cortes"]),
        target_acos_pct=Decimal(inputs["target_acos_pct_usado"]),
        bid_actual=_dec_de_json(inputs["bid_actual"]),
        bid_moneda=inputs["bid_moneda"],
        floor=Decimal(goal["bid_floor"]),
        ceiling=Decimal(goal["bid_ceiling"]),
        umbral_pause=umbral_pause,
    )


def _replay_hygiene(inputs: dict) -> hygiene.ResultadoTermino:
    vt = inputs["ventana_terminos"]
    td = inputs["termino"]
    fin = dt.date.fromisoformat(vt["window_end"])
    observed = td["observed_at_max"]
    termino = windows.AgregadoTermino(
        ad_entity_id=0,  # no consumido por el motor; identidad no congelada
        search_term=td["search_term"],
        metric_currency=td["moneda"],
        cost=_dec_de_json(td["cost"]),
        ad_revenue=_dec_de_json(td["ad_revenue"]),
        clicks=td["clicks"],
        orders=td["orders"],
        fechas_distintas=td["fechas_distintas"],
        is_asin_like=False,  # un termino ASIN-like JAMAS genera decision (2.3)
        observed_at_max=dt.datetime.fromisoformat(observed) if observed else None,
    )
    terminos = windows.TerminosCortes(
        ad_entity_id=0,
        window_start=dt.date.fromisoformat(vt["window_start"]),
        window_end=fin,
        fechas_entidad=_fechas_sinteticas(fin, vt["fechas"]),
        terminos=(termino,),
    )
    harvest = inputs["goal"]["harvest"]
    config = (
        hygiene.ConfigHarvest(
            campaign_id=harvest["campaign_id"],
            ad_group_id=harvest["ad_group_id"],
            default_bid=Decimal(harvest["default_bid"]),
            moneda=harvest["moneda"],
        )
        if harvest
        else None
    )
    # CORTES 01 (spec): el replay LEE inputs.corte.umbral_clicks_usado y
    # piso_cost_usado, JAMAS recalcula evidencia ni AOV (el snapshot de la
    # decision ya no existe). Fila historica sin la clave (pre-CORTES, o
    # congelada en 1.2/1.3 sin piso) -> legacy 20 y 8/130, replay exacto.
    corte = inputs.get("corte")
    umbral_negative = corte["umbral_clicks_usado"] if corte is not None else cortes.LEGACY_NEGATIVE
    piso = (
        Decimal(corte["piso_cost_usado"])
        if corte is not None and "piso_cost_usado" in corte
        else None
    )
    (resultado,) = hygiene.decide_hygiene(
        platform=inputs["platform"],
        terminos=terminos,
        target_acos_pct=Decimal(inputs["target_acos_pct_usado"]),
        config_harvest=config,
        # keywords_existentes vacio: una decision de harvest solo existe si el
        # termino NO estaba duplicado al decidir (replay contra nada bloquea).
        keywords_existentes=frozenset(),
        umbral_negative=umbral_negative,
        piso_negative=piso,
    )
    return resultado


def reproduce(inputs: dict) -> tuple[str | None, Decimal | None, str | None]:
    """Re-decide UNA decision desde sus inputs congelados y devuelve
    (kind, new_value, value_currency). Es la funcion del spot-check humano
    (4.4): reproduce(inputs) debe igualar la decision persistida.

    Reconstruye agregados SINTETICOS (fechas = n fechas dentro de la ventana:
    el conteo es lo que replayea `completa`) y llama al motor puro con los
    valores congelados (Decimal(str) de vuelta, jamas float)."""
    motor = inputs.get("motor")
    if motor == "bid":
        resultado = _replay_bid(inputs)
    elif motor == "hygiene":
        resultado = _replay_hygiene(inputs)
    else:
        raise ValueError(f"inputs.motor fuera del vocabulario {{bid, hygiene}}: {motor!r}")
    return (resultado.kind, resultado.new_value, resultado.value_currency)
