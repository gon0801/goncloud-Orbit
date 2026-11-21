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
  campaña (o, para hojas, ad group) sin state o status != ENABLED ->
  'campana_no_enabled' / 'grupo_no_enabled' (CAMPANA ACTIVA 01: el cache de
  estructura, no un LIST; un ancestro pausado hace moot todo lo de abajo);
  entidad sin state o status != ENABLED -> 'estado_no_enabled' (para ad
  groups: sus terminos TODOS skip con ese motivo); hoja sin impresiones en
  14d desde el watermark -> 'entidad_inerte' (BIDS 01: vista
  v_entidad_inerte leida UNA vez por ciclo en TX2; salta ANTES de resolver
  ventanas, no aplica al camino de terminos); cooldown 7d -> 'cooldown_7d'.
  En hojas, PAUSE se decide con ventana madura antes de consultar su propio
  cooldown; un BID previo solo enfria BID. En grupos, el gate sigue igual.
  B.2a: ese parrafo rige solo con el flag ads_pause_sin_cooldown_bid en
  true; apagado (default), el cooldown generico pre-B.2 bloquea tambien la
  PAUSE en el gate. El flag contiene el cooldown por kind; el tope
  confirmed_at <= decided_at de B.2 queda vigente en la query.
  Orden de gates del orquestador: campaña (goal) primero (la hace invisible al
  optimizador por completo), luego ancestros y estado. El veto pendiente por
  clave de efecto corre despues; en hojas, cooldown depende del tipo de
  decision ya calculada; en grupos, sigue antes del veto
  (CAMPANA ACTIVA 01 · 1.6: una hoja o termino dentro de una campana/grupo
  apagado se cuenta con el motivo del ANCESTRO, y un grupo gateado cuenta
  TODOS sus terminos con ese motivo — incluidos los bloqueados; solo cambian
  contadores de notes.skips, ninguna decision ni mutacion). Desviacion
  declarada del orden literal del spec (que lista
  el cooldown antes): en shadow el cooldown JAMAS dispara (solo cuentan
  applies de ciclos live, regla 2.4) y asi no se paga una query EXISTS por
  entidad de campañas fuera del ciclo.
- REPLAY PUBLICO: `app.optimizer.replay.reproduce(inputs)` re-decide una
  decision desde SU JSON congelado. Este modulo lo reexporta para conservar
  el spot-check humano de 4.4 sin duplicar la logica.

CORTES 01 (1.2/1.3): umbrales de clicks adaptativos por producto. cycle
resuelve cortes.umbral_corte con la evidencia del ad group
(windows.ventanas_evidencia_ad_group, UNA consulta por plataforma dentro de
TX2) -- 'negative' para hygiene (1.2) y 'pause' para el motor de bids (1.3,
via k.parent_id de _SQL_DECISORAS) -- y los motores RECIBEN el int
resuelto. Toda decision que consulta umbral de clicks congela inputs.corte
TOP-LEVEL (shape del spec: umbral_clicks_usado FINAL con piso, elegible,
expected_clicks como string, evidencia con observed_at_max) -- en hygiene
las negative (las harvest NO lo llevan) y en bids TODAS, incluidas las de
kind final 'bid': R0 evalua PAUSE antes del juicio y, sin el
freeze, el replay de un bid cuyo umbral adaptativo bloqueo el pause
rejugaria como pause con la historia sin la clave (25, REPLAY_PAUSE_
CLICKS_PRE_CORTES01), no con el umbral que SU decision resolvio.
data_observed_at =
LEAST(decided_at, max(obs directo, observed_at_max de la evidencia)) -- el
clamp es obligatorio (CHECK decision_dato_no_del_futuro): sin el, un
observed_at posterior a decided_at aborta el executemany de TX3.
reproduce() LEE lo congelado -- umbral_clicks_usado y, desde CORTES 03,
cost_min_usado (cierre replay: decision del lead 2026-08-28, replay FIEL
POR CONSTRUCCION); fila historica sin la clave rejuega con la HISTORIA de
su era (REPLAY_PAUSE_CLICKS_PRE_CORTES01=25 y
REPLAY_PAUSE_COST_PRE_CORTES03=12/200 para pause; legacy 20 y 8/130 para
negative), JAMAS con un valor vigente del PAUSE; jamas recalcula evidencia.
OJO negative: no tiene constantes REPLAY_* congeladas -- hoy es fiel porque
sus numeros nunca cambiaron (20 y 8/130); si una fase futura los toca,
congelalos igual que el pause o la misma clase de bug volvera (deuda
declarada en el spec, cierre CORTES 03).

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
  el bloqueo entity_cut salta la entidad ENTERA del motor de bids — decide
  da pause o juicio en UNA llamada y no existe forma de prohibir solo el
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
- ORBIT 04 3.3 — NOTIFICA (sellados 2 y 19): canal Telegram fail-silent con
  UN aviso por corte NUEVO encolado (tras el commit de TX4, con el
  vencimiento de la ventana 48h) y UN digest al final del ciclo ejecutor.
  Un fallo del canal JAMAS tumba ni degrada el ciclo: deja la NOTA
  `notes["telegram"]` (solo claves de lo que fallo, regla 3), persistida por
  el mismo sello post-apply y VISIBLE en Salud — la NOTA es la UNICA
  visibilidad del fallo del canal: el silencio del canal jamas es invisible
  (sellado 2). Canal deshabilitado (sin secrets) no es fallo: no genera
  NOTA. Las alertas de harvest failed salen en el punto de fallo definitivo
  (app.apply_harvest._falla_job) y su bandera de envio fallido llega aqui
  para la misma NOTA.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from decimal import Decimal

import httpx
import psycopg
from psycopg import pq
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Json

from app import (
    apply,
    apply_cola,
    apply_harvest,
    notifica,
    propuestas_campana,
    proteccion_venta,
)
from app.ads.config import AdsCredentials
from app.apply import Aplicador
from app.apply_cuota import CapSaturado
from app.cycle_notifica import _fase_notifica
from app.cycle_serializacion import (  # noqa: F401 - re-export estable para tests/callers
    _agregado_json,
    _corte_json,
    _dec_str,
    _fecha_iso,
    _goal_json,
    _notas_cuerpo,
    _notas_json,
    _Pendiente,
    _pendiente_pause_apagada,
    _pendiente_termino,
    _proteccion_activa,
    _registra_proteccion,
    _sello_bitemporal,
    _ts,
    pendiente_bid,
)
from app.cycle_sombra import (
    MOTIVO_PRODUCTO_SIN_EVIDENCIA,
    _anota_sombra,
    _descarta_sin_evidencia,
    _evalua_productos,
    _insumos_pausa,
    _juicio_hoja,
    _meta_viva,
    _notas_productos,
    _pendientes_pausa_producto,
    _ProductosCiclo,
)
from app.cycle_target import (
    _FamiliasCiclo,
    _lee_familias_ciclo,
    _margen_familia_de_hoja,
    _resuelve_target_ciclo,
    _TargetCiclo,
)
from app.db import tx_durable
from app.lecturas_caso import LecturasPlataforma, lee_plataforma
from app.optimizer import bid, cortes, harvest_destino, hygiene, productos, windows
from app.optimizer import goals as g
from app.optimizer import proteccion as subproteccion
from app.optimizer.bid import PLATAFORMAS_MONEDA
from app.optimizer.caso import BidVigente, EconomiaPlataforma
from app.optimizer.politica import Mantener, Pausar, decide
from app.optimizer.replay import reproduce as reproduce
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


# Guarda del cron (ingesta-01 A.3): una plataforma con ciclo done/degraded
# hoy no repite, y un running de hace menos de 3 h se respeta (puede estar
# vivo); uno failed, skipped o running mas viejo es un huerfano de proceso
# muerto y SI se reintenta (el rastro lo cierra al ganar el claim nuevo).
# El reloj es now() de la DB, el mismo del claim: sin skew Python-DB.
_SQL_ULTIMO_CICLO_HOY = """
SELECT status, started_at > now() - interval '3 hours'
  FROM optimizer_cycle
 WHERE platform = %s
   AND (started_at AT TIME ZONE 'UTC')::date = (now() AT TIME ZONE 'UTC')::date
 ORDER BY started_at DESC
 LIMIT 1
"""


def motivo_de_salto(conn: psycopg.Connection, platform: str) -> str | None:
    """Razon por la que hoy NO se abre un ciclo nuevo para `platform`, o
    None si se puede correr. La consulta es deliberadamente la del ultimo
    ciclo de HOY: un running viejo no bloquea (es huerfano reclamable)."""
    fila = conn.execute(_SQL_ULTIMO_CICLO_HOY, (platform,)).fetchone()
    if fila is None:
        return None
    status, corriendo_reciente = fila
    if status in ("done", "degraded"):
        return f"cycle {platform}: ya hay un ciclo {status} hoy; espera al siguiente horario"
    if status == "running" and corriendo_reciente:
        return f"cycle {platform}: ciclo running iniciado hace menos de 3 h"
    return None


# Motivos de skip del ORQUESTADOR (vocabulario cerrado; ademas se importan los
# MOTIVO_* de bid/hygiene tal cual a los contadores de notes). veto_pendiente
# viene de apply_cola (2.2 sellado 5): UNA fuente del vocabulario.
MOTIVO_SIN_GOAL = "sin_goal"
MOTIVO_GOAL_DISABLED = "goal_disabled"  # el opt-out auditable del Spec delta
MOTIVO_GOAL_MODE_OFF = "goal_mode_off"
MOTIVO_ESTADO_NO_ENABLED = "estado_no_enabled"
# CAMPANA ACTIVA 01: ancestros no ENABLED (o sin fila de state) sacan a la
# hoja/grupo del motor ANTES de mirar su propio estado. Fuente: el cache
# ad_entity_state (sync diario; guarda de 48h del ciclo). Desde 1.6 son alias
# de apply (funcion UNICA gate_ancestros): UNA fuente del vocabulario.
MOTIVO_CAMPANA_NO_ENABLED = apply.MOTIVO_CAMPANA_NO_ENABLED
MOTIVO_GRUPO_NO_ENABLED = apply.MOTIVO_GRUPO_NO_ENABLED
# ARREGLOS 01 K (E-H3): la hoja tiene un bid 'normal' sin sello (resultado
# dudoso): no recibe otro movimiento de bid hasta que se confirme o se cierre.
# Fuente unica: apply (misma constante y mismo SQL que reconcilia_bids).
MOTIVO_BID_EN_VUELO = apply.MOTIVO_BID_EN_VUELO
# BIDS 01 (D3): hoja sin impresiones en 14d desde el watermark (vista
# v_entidad_inerte, migracion 0013): ajustar su bid seria inerte, Amazon no
# la sirve. Se salta ANTES de resolver ventanas (no gasta consultas ni cupo).
MOTIVO_ENTIDAD_INERTE = "entidad_inerte"
MOTIVO_COOLDOWN_7D = "cooldown_7d"
# BIDS 02 T.1: ningun peldano resolvio target (el default 55 era inventado,
# regla 3): la hoja y el ad group de terminos se saltan sin decidir.
MOTIVO_SIN_TARGET = "sin_target"
# BIDS 02 M.3: la clave ads_bid_politica_<p> esta ausente: el motor no mueve
# bids en esa plataforma (solo decide la PAUSE).
MOTIVO_POLITICA_APAGADA = "politica_apagada"
MOTIVO_ESCALERA_OFF = "escalera_off"
MOTIVO_VETO_PENDIENTE = apply_cola.MOTIVO_VETO_PENDIENTE
# AUTO-03 I.5: en un grupo exclusivo con una pausa de producto live en vuelo,
# una subida de bid se omite (las bajadas siguen): no se sube lo que se va a
# pausar.
MOTIVO_PAUSA_PRODUCTO_PENDIENTE = "pausa_producto_pendiente"

# Modo tope para el envelope del ciclo: la escalera global es el techo y el
# modo por goal solo puede bajarlo. El aplicador no filtra por el modo del
# envelope ni por inputs.modo: re-resuelve goal.mode por decision.
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

# Solo sella si sigue en 'running': el rastro de un sucesor puede haber cerrado
# ya el envelope del zombie en 'failed' y esa nota no debe pisarse.
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
# (NULL si la campana no tiene state).
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

# Entidades decisoras con su ad group, campana y state. k.parent_id va
# calificado para no confundirse con ag.parent_id (campaign_id).
_SQL_DECISORAS = """
SELECT k.id, k.parent_id AS ad_group_id, ag.parent_id AS campaign_id,
       s.current_bid, s.bid_currency, s.status, s.acos_target,
       sg.status AS status_grupo, sc.status AS status_campana
  FROM ad_entity k
  JOIN ad_entity ag ON ag.id = k.parent_id AND ag.kind = 'ad_group'
  LEFT JOIN ad_entity_state s ON s.ad_entity_id = k.id
  LEFT JOIN ad_entity_state sg ON sg.ad_entity_id = ag.id
  LEFT JOIN ad_entity_state sc ON sc.ad_entity_id = ag.parent_id
 WHERE k.platform = %s::platform AND k.kind IN ('keyword', 'product_target')
 ORDER BY k.id
"""

# BIDS 01 (D2/D3): conjunto de hojas inertes de LA plataforma, UNA consulta
# por ciclo en TX2 junto a la evidencia (misma fuente que la pagina, el
# digest y la herramienta: v_entidad_inerte, migracion 0013).
_SQL_INERTES = """
SELECT id FROM v_entidad_inerte WHERE platform = %s::platform
"""

# AUTO-03 I.5: ad groups exclusivos (todos sus product ads no archivados con
# listing del mismo producto, la regla de productos_io) con una pausa de
# producto live en vuelo: pending_veto, released o applying, sin vetar.
_SQL_GRUPOS_PAUSA_PRODUCTO = """
SELECT DISTINCT pa.parent_id
  FROM apply_queue q
  JOIN ad_entity pa ON pa.id = q.ad_entity_id AND pa.kind = 'product_ad'
 WHERE q.platform = %s::platform AND q.kind = 'pause' AND q.modo = 'live'
   AND q.estado IN ('pending_veto', 'released', 'applying') AND q.vetoed_at IS NULL
   AND (SELECT count(*) FILTER (WHERE li.product_id IS NULL) = 0
               AND count(DISTINCT li.product_id) = 1
          FROM ad_entity o
          LEFT JOIN ad_entity_state s ON s.ad_entity_id = o.id
          LEFT JOIN listing li ON li.id = o.listing_id AND li.platform = o.platform
         WHERE o.parent_id = pa.parent_id AND o.kind = 'product_ad'
           AND s.status IS DISTINCT FROM 'ARCHIVED')
"""

# Ad groups con SU campaña y state: son las entidades que portean terminos.
_SQL_GRUPOS = """
SELECT ag.id, ag.parent_id AS campaign_id, s.status, sc.status AS status_campana,
       ag.external_id
  FROM ad_entity ag
  LEFT JOIN ad_entity_state s ON s.ad_entity_id = ag.id
  LEFT JOIN ad_entity_state sc ON sc.ad_entity_id = ag.parent_id
 WHERE ag.platform = %s::platform AND ag.kind = 'ad_group'
 ORDER BY ag.id
"""

# FABRICA 02 (A.6): membresia de la campana para el aviso en flanco
# (grupo_id + rol + externos). Toca las tablas de F1: en esquemas sin 0018
# revienta con UndefinedTable y el caller la absorbe en savepoint, igual
# que al resolutor.
_SQL_MEMBRESIA_GRUPO = """
SELECT r.grupo_id, r.rol::text, c.external_id, c.name
  FROM campana_grupo_rol r
  JOIN ad_entity c ON c.id = r.ad_entity_id
 WHERE r.ad_entity_id = %s
"""

# Motivos del resolutor que avisan en flanco por campana de grupo (A.6).
# El resto (origen_es_destino, destino_desincronizado...) solo vive en
# skips: no hay nada que corregir en el grupo.
_MOTIVOS_AVISO_DESTINO = frozenset(
    {hygiene.MOTIVO_DESTINO_INCONSISTENTE, hygiene.MOTIVO_SIN_DESTINO_HARVEST}
)

# Notas de ciclos previos para el FLANCO del aviso de destino (A.6): misma
# plataforma+motor, anteriores al actual, con recorrido (done/degraded —
# en live sin credenciales el recorrido y sus notes existen igual;
# skipped no lleva la clave). Ventana de 20: basta para la racha viva.
_SQL_SALTOS_PREVIOS = """
SELECT notes
  FROM optimizer_cycle
 WHERE platform = %s::platform AND motor = 'ads_optimizer' AND id < %s
   AND status IN ('done', 'degraded')
 ORDER BY id DESC
 LIMIT 20
"""

_SQL_INSERT_DECISION = """
INSERT INTO decision (cycle_id, ad_entity_id, kind, decided_at, config_version_id,
                      data_observed_at, window_start, window_end, search_term,
                      old_value, new_value, value_currency, inputs,
                      evaluacion_producto_id)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

# C.2a: el freeze es idempotente por PK (un re-run del mismo ciclo no
# duplica); la fila nace en TX2 (mismo snapshot que la decision) y se
# escribe en TX3 aunque la hoja no haya dejado decision.
_SQL_INSERT_TARGET_CICLO = """
INSERT INTO target_acos_ciclo (cycle_id, ad_entity_id, decided_at,
                               target_acos_pct, procedencia)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (cycle_id, ad_entity_id) DO NOTHING
"""


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
    # FABRICA 02 (A.6): destinos resueltos por ad group evaluado
    # (resuelto_por -> n) + saltos con aviso por campana de grupo
    # (campaign ad_entity_id -> SaltoDestinoGrupo; el dict dedupea).
    destinos: Counter = field(default_factory=Counter)
    saltos_grupo: dict[int, notifica.SaltoDestinoGrupo] = field(default_factory=dict)
    propuestas_campana: tuple[propuestas_campana.Evaluacion, ...] = ()
    # C.2a: freeze del target por hoja (ad_entity_id -> (target, procedencia))
    # capturado donde la cascada lo calculo; TX3 lo escribe aunque la hoja no
    # deje decision (no-op y cooldown incluidos).
    targets: dict[int, tuple[Decimal, str]] = field(default_factory=dict)
    # AUTO-03 D.4: evaluacion por producto del ciclo (None = meta en off).
    productos: _ProductosCiclo | None = None
    # ORBIT 07 M1: episodios de venta reciente abiertos en TX2, escritos en
    # TX3 (append-only, idempotente por ancla).
    protecciones: list[proteccion_venta.TransicionProteccion] = field(default_factory=list)


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
    conn: psycopg.Connection,
    job_key: str,
    owner: str,
    tick_heartbeat: Callable[[], None],
    *,
    hb: psycopg.Connection | None = None,
) -> Callable[[], None]:
    """El `tick` del Aplicador en la fase de apply (decision 11): (a) heartbeat
    — mutaciones lentas no dejan morir el lease; (b) ownership-check fresco
    contra ads_optimizer_lock. Usa la conexion `hb` (autocommit) cuando existe
    y, si consulta sobre `conn`, devuelve `conn` a IDLE para no convertir los
    `with conn.transaction():` siguientes en SAVEPOINTs sin COMMIT."""

    def guard() -> None:
        tick_heartbeat()
        if hb is not None:
            fila = hb.execute(_SQL_OWNER_LOCK, (job_key,)).fetchone()
        else:
            estaba_idle = (
                not conn.autocommit and conn.info.transaction_status == pq.TransactionStatus.IDLE
            )
            try:
                fila = conn.execute(_SQL_OWNER_LOCK, (job_key,)).fetchone()
            finally:
                if estaba_idle:
                    conn.rollback()
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


def _inserta_decisiones(
    conn: psycopg.Connection,
    cycle_id: int,
    config_version_id: int,
    decided_at: dt.datetime,
    pendientes: list[_Pendiente],
    ids_evaluacion: dict[int, int] | None = None,
) -> None:
    if not pendientes:
        return
    ids_evaluacion = ids_evaluacion or {}
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
            ids_evaluacion[p.evaluacion_producto] if p.evaluacion_producto is not None else None,
        )
        for p in pendientes
    ]
    conn.cursor().executemany(_SQL_INSERT_DECISION, filas)


def _inserta_targets(
    conn: psycopg.Connection, cycle_id: int, decided_at: dt.datetime, contadores: _Contadores
) -> None:
    """C.2a: el freeze capturado en TX2 se persiste en TX3 (junto a las
    decisiones, atomico con el cierre del envelope)."""
    if not contadores.targets:
        return
    filas = [
        (cycle_id, entidad, decided_at, target, procedencia)
        for entidad, (target, procedencia) in sorted(contadores.targets.items())
    ]
    conn.cursor().executemany(_SQL_INSERT_TARGET_CICLO, filas)


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


_SQL_NOTAS_VIGENTES = "SELECT notes FROM optimizer_cycle WHERE id = %s"


def _mezcla_evidencias_persistidas(conn: psycopg.Connection, cycle_id: int, cuerpo: dict) -> None:
    """Obs3r2: el sello post-apply REEMPLAZA notes — sin este merge, las
    evidencias economicas anexadas DURANTE la fase (misma TX que cada
    descarte/aplicacion, ya commitadas) se pierden cuando la fase aborta
    (el cuerpo del aborto no trae la lista en memoria). Lo persistido
    (top-level `revalidaciones_economicas`) se mezcla a
    `apply.revalidaciones_economicas` con dedupe por decision_id (la
    version en memoria manda; en exito son identicas). El destino puede
    llegar como la TUPLA del camino de exito
    (ResultadoLiberacion.revalidaciones_economicas) y se normaliza a lista
    antes de agregar. El SELECT va en su PROPIA transaccion (la conexion de
    prod no es autocommit: un SELECT suelto abriria la TX implicita y el
    `with` del sello seria un savepoint jamas commiteado — el BN1 de C.4
    ronda 2)."""
    with conn.transaction():
        fila = conn.execute(_SQL_NOTAS_VIGENTES, (cycle_id,)).fetchone()
    if fila is None or not isinstance(fila[0], str) or not fila[0]:
        return
    try:
        notas = json.loads(fila[0])
    except ValueError:
        return
    if not isinstance(notas, dict):
        return
    persistidas = notas.get("revalidaciones_economicas")
    if not isinstance(persistidas, list) or not persistidas:
        return
    seccion = cuerpo.setdefault("apply", {})
    if not isinstance(seccion, dict):
        return
    destino = seccion.setdefault("revalidaciones_economicas", [])
    if not isinstance(destino, list):
        # R-C3-1: el append sobre la tupla del camino de exito daria
        # AttributeError fuera de todo try (el ciclo se sellaria failed).
        destino = list(destino)
        seccion["revalidaciones_economicas"] = destino
    vistos = {e.get("decision_id") for e in destino if isinstance(e, dict)}
    for evidencia in persistidas:
        if isinstance(evidencia, dict) and evidencia.get("decision_id") not in vistos:
            destino.append(evidencia)
            vistos.add(evidencia.get("decision_id"))


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
        if not conn.autocommit and conn.info.transaction_status != pq.TransactionStatus.IDLE:
            conn.rollback()
        with tx_durable(conn):
            filas = conn.execute(
                _SQL_SELLAR_FALLIDO, (json.dumps(cuerpo, ensure_ascii=False, default=str), cycle_id)
            ).rowcount
        if filas == 0:
            logger.warning(
                "sello del ciclo fallido %s ignorado: ya no esta 'running' (rastro del"
                " sucesor) — no se pisa",
                cycle_id,
            )
    except Exception:
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


def lee_goals(
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
    conn: psycopg.Connection,
    goal: g.Goal,
    platform: str,
    destino: harvest_destino.DestinoHarvest | harvest_destino.SaltoHarvest | None,
) -> tuple[hygiene.ConfigHarvest | None, frozenset[str], str | None]:
    """Config de harvest del DESTINO resuelto (FABRICA 02, A.1) + keywords
    EXACT de la campana destino (dedupe, re-apuntado al `campaign_id`
    RESUELTO: sin esto un termino cosechado se re-propondria cada dia
    durante la ventana de ~90 dias) + motivo de salto (None si hay
    destino). Sin destino o sin monto -> (None, frozenset(), motivo) y
    decide_hygiene salta CON MOTIVO 'harvest_sin_config' (jamas
    placeholder); el llamador traduce ese motivo al del salto F2.
    `destino=None` = esquema sin 0018 (fixtures pre-F1): terna del goal
    como antes de F2."""
    if destino is None:
        if (
            goal.harvest_campaign_id is None
            or goal.harvest_ad_group_id is None
            or goal.harvest_default_bid is None
        ):
            return (None, frozenset(), None)
        config = hygiene.ConfigHarvest(
            campaign_id=goal.harvest_campaign_id,
            ad_group_id=goal.harvest_ad_group_id,
            default_bid=goal.harvest_default_bid,
            moneda=goal.bid_currency,
        )
        return (
            config,
            hygiene.keywords_campana_destino(conn, platform, goal.harvest_campaign_id),
            None,
        )
    if not isinstance(destino, harvest_destino.DestinoHarvest):
        return (None, frozenset(), destino.motivo)
    if destino.bid is None or destino.moneda is None:
        return (None, frozenset(), hygiene.MOTIVO_HARVEST_SIN_CONFIG)
    config = hygiene.ConfigHarvest(
        campaign_id=destino.campaign_external,
        ad_group_id=destino.ad_group_external,
        default_bid=destino.bid,
        moneda=destino.moneda,
    )
    return (
        config,
        hygiene.keywords_campana_destino(conn, platform, destino.campaign_external),
        None,
    )


def _registra_destino(
    conn: psycopg.Connection,
    platform: str,
    campaign_id: int,
    destino: harvest_destino.DestinoHarvest | harvest_destino.SaltoHarvest | None,
    contadores: _Contadores,
) -> None:
    """Cuenta del destino resuelto por ad group evaluado (FABRICA 02, A.6):
    DestinoHarvest suma en `destinos[resuelto_por]`; SaltoHarvest con motivo
    de aviso suma en `saltos_grupo` SOLO si la campana es de grupo (una por
    campana aunque tenga varios ad groups; el dict dedupea). Un
    sin_destino_de_harvest de campana suelta es el estado normal de hoy:
    ya vive en skips y aqui no entra. La membresia corre en savepoint
    propio con la misma absorcion de UndefinedTable que el resolutor: el
    savepoint de este ya cerro cuando se conoce el salto (esquemas sin
    0018: fixtures pre-F1)."""
    if isinstance(destino, harvest_destino.DestinoHarvest):
        contadores.destinos[destino.resuelto_por] += 1
        return
    if not isinstance(destino, harvest_destino.SaltoHarvest):
        return
    if destino.motivo not in _MOTIVOS_AVISO_DESTINO:
        return
    try:
        with conn.transaction():
            fila = conn.execute(_SQL_MEMBRESIA_GRUPO, (campaign_id,)).fetchone()
    except psycopg.errors.UndefinedTable:
        return
    if fila is None:
        return
    grupo_id, rol, external, nombre = fila
    contadores.saltos_grupo[campaign_id] = notifica.SaltoDestinoGrupo(
        platform=platform,
        grupo_id=grupo_id,
        campaign_ad_entity_id=campaign_id,
        campaign_external=external,
        nombre=nombre,
        rol=rol,
        motivo=destino.motivo,
    )


def _harvest_destino_notes(contadores: _Contadores) -> dict:
    """Bloque notes.harvest_destino (A.6): contadores por ad group evaluado
    (las tres claves SIEMPRE: ausente en el Counter = 0, es un contador del
    mismo ciclo, no un dato faltante) + saltos de grupo por campana (claves
    string por JSON: campaign ad_entity_id -> motivo)."""
    return {
        "resueltos": {
            procedencia: contadores.destinos.get(procedencia, 0)
            for procedencia in ("grupo", "excepcion", "terna")
        },
        "saltos_grupo": {
            str(camp): salto.motivo for camp, salto in contadores.saltos_grupo.items()
        },
    }


def _saltos_previos(
    conn: psycopg.Connection, platform: str, cycle_id: int
) -> dict[str, str] | None:
    """saltos_grupo del PRIMER ciclo previo con la clave harvest_destino
    (A.6, flanco del aviso de destino): parseo tolerante como
    `_target_de_notas` (TEXT no-JSON, no-dict o sin la clave = seguir
    buscando); None si ninguno de los 20 la trae. Se llama en TX2, junto
    a `_resuelve_target_ciclo` (mismo snapshot)."""
    for (notes,) in conn.execute(_SQL_SALTOS_PREVIOS, (platform, cycle_id)).fetchall():
        if not isinstance(notes, str):
            continue
        try:
            cuerpo = json.loads(notes)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(cuerpo, dict):
            continue
        bloque = cuerpo.get("harvest_destino")
        if not isinstance(bloque, dict):
            continue
        saltos = bloque.get("saltos_grupo")
        return dict(saltos) if isinstance(saltos, dict) else {}
    return None


def _gates_entidad(
    conn: psycopg.Connection,
    goals: tuple[g.Goal | None, dict[int, g.Goal]],
    *,
    campaign_id,
    entidad_id: int,
    status,
    ancestros: tuple[tuple[str, str | None], ...],
    decided_at: dt.datetime,
    comprobar_cooldown: bool = True,
) -> tuple[g.Goal | None, str | None]:
    """Cascada de gates del orquestador (orden sellado, ver docstring del
    modulo): goal de campaña -> ancestros ENABLED (CAMPANA ACTIVA 01: campaña
    y, para hojas, ad group; `ancestros` = ((motivo, status), ...) de afuera
    hacia adentro) -> estado propio -> cooldown. None = elegible. Un ancestro
    sin fila de state (status None) tambien queda fuera (regla 3)."""
    goal_plataforma, por_campana = goals
    goal, motivo = _porta_goal_campana(por_campana, goal_plataforma, campaign_id)
    for motivo_ancestro, status_ancestro in ancestros:
        if motivo is None and status_ancestro != "ENABLED":
            motivo = motivo_ancestro
    if motivo is None and status != "ENABLED":
        motivo = MOTIVO_ESTADO_NO_ENABLED  # None (sin state) tambien queda fuera
    if motivo is None and comprobar_cooldown and g.en_cooldown(conn, entidad_id, ahora=decided_at):
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
    inertes: set[int],
    margen_plataforma: Decimal | None,
    snapshot_margen: dict,
    familias_ciclo: _FamiliasCiclo,
    pause_economica: bool,
    politica: str | None,
    lecturas: LecturasPlataforma | None,
    productos_ciclo: _ProductosCiclo | None = None,
    grupos_pausa_producto: frozenset[int] = frozenset(),
    hojas_bid_en_vuelo: frozenset[int] = frozenset(),
    proteccion_cache: proteccion_venta.CacheProteccion | None = None,
    claves_released: frozenset[tuple[int, str, str | None]] = frozenset(),
) -> None:
    (
        entidad_id,
        ad_group_id,
        campaign_id,
        current_bid,
        bid_currency,
        status,
        _acos_cache,
        status_grupo,
        status_campana,
    ) = fila
    goal, motivo = _gates_entidad(
        conn,
        goals,
        campaign_id=campaign_id,
        entidad_id=entidad_id,
        status=status,
        ancestros=(
            (MOTIVO_CAMPANA_NO_ENABLED, status_campana),
            (MOTIVO_GRUPO_NO_ENABLED, status_grupo),
        ),
        decided_at=decided_at,
        # BIDS 02 M.3 (cambio 9): el gate queda fijo en el camino que
        # produccion corre hoy (flag encendido): sin cooldown generico
        # aqui; el cooldown de la PAUSE se evalua por kind despues de
        # decidir. La espera de un bid la decide R2, no este gate.
        comprobar_cooldown=False,
    )
    if motivo is not None:
        contadores.skips_entidad[motivo] += 1
        tick()
        return
    assert goal is not None  # _porta_goal_campana: motivo None implica goal
    clave_pausa = (entidad_id, "entity_cut", None)
    bloqueada = clave_pausa in bloqueadas
    espera_released = clave_pausa in claves_released
    protegible = proteccion_cache is not None and proteccion_cache.tiene_proteccion(entidad_id)
    if bloqueada and not (espera_released and protegible):
        # 2.2 sellado 5 / 2.4: clave de efecto en vuelo (fila NO terminal o
        # veto vigente) — el ciclo NO re-decide esa clave. Salta la entidad
        # ENTERA del motor de bids (decide da pause o juicio en una
        # sola llamada; prohibir solo el pause exigiria inventar un umbral,
        # regla 3 — declarado en el docstring del modulo). CAMPANA ACTIVA 01
        # · 1.6: va DESPUES de los gates — una hoja en campana/grupo no
        # ENABLED se cuenta con el motivo del ANCESTRO, no como veto.
        # ORBIT 07 M1: una fila `released` con venta reciente NO congela las
        # pujas; solo se prohibe re-decidir la pausa (el veredicto Pausar se
        # descarta abajo). Veto pendiente, veto del dueno y applying siguen
        # bloqueando la hoja entera.
        contadores.skips_entidad[MOTIVO_VETO_PENDIENTE] += 1
        tick()
        return
    if entidad_id in inertes:
        # BIDS 01 (D3): sin impresiones en 14d desde el watermark -> el ajuste
        # seria inerte (Amazon no sirve la hoja); no gasta consultas ni cupo.
        contadores.skips_entidad[MOTIVO_ENTIDAD_INERTE] += 1
        tick()
        return
    # CORTES 01 (1.3): umbral pause del GRUPO (k.parent_id de
    # _SQL_DECISORAS) resuelto con LA MISMA funcion que negative, UNA vez
    # por ad group y ciclo (cache lazy del recorrido); entidad cuyo grupo
    # no esta en el dict -> evidencia None -> fallback 100 con piso legacy
    # 100 (CORTES 03; antes 50/25; regla 3: jamas un numero inventado).
    # Cierre CORTES 03 (decision del lead 2026-08-28): el piso de costo
    # VIGENTE tambien va EXPLICITO al juicio (InsumosPausa) y al freeze
    # (cost_min_usado) -- replay fiel por construccion.
    if ad_group_id not in corte_pause_por_grupo:
        evidencia = evidencia_ad_groups.get(ad_group_id)
        corte_pause_por_grupo[ad_group_id] = (cortes.umbral_corte(evidencia, "pause"), evidencia)
    corte_pause, evidencia = corte_pause_por_grupo[ad_group_id]
    # ORBIT 06 2.3: el peldano entra a la cascada del motor; la procedencia
    # sale del mismo nucleo (peldano_target_acos) para el freeze.
    # A5: el peldano familiar se resuelve por hoja (su ancla es el ultimo
    # aplicado DE LA HOJA); el snapshot viaja al freeze si gana.
    target_margen_familia, snapshot_fam = _margen_familia_de_hoja(
        familias_ciclo, campaign_id, entidad_id
    )
    previo = familias_ciclo.previos.get(entidad_id)
    target = g.cascada_target_acos(
        goal.target_acos_pct,
        setting_target,
        margen_plataforma,
        goal.scope,
        target_margen_familia,
    )
    # AUTO-03 F.2: con la meta en live, la hoja de grupo apto toma la meta de
    # su producto como peldano (destino = la cascada sin producto).
    meta_viva = _meta_viva(
        productos_ciclo, goal=goal, ad_group_id=ad_group_id, previo=previo, destino=target
    )
    if meta_viva == MOTIVO_PRODUCTO_SIN_EVIDENCIA:
        contadores.skips_entidad[MOTIVO_PRODUCTO_SIN_EVIDENCIA] += 1
        tick()
        return
    assert not isinstance(meta_viva, str)
    target_margen_producto = meta_viva.resolucion.aplicado if meta_viva is not None else None
    if meta_viva is not None:
        assert productos_ciclo is not None
        productos_ciclo.hojas_aptas[entidad_id] = meta_viva.product_id
        target = g.cascada_target_acos(
            goal.target_acos_pct,
            setting_target,
            margen_plataforma,
            goal.scope,
            target_margen_familia,
            target_margen_producto=target_margen_producto,
        )
    if target is None:
        # BIDS 02 T.1: sin peldano no hay target ni fila de freeze (regresa
        # antes de contadores.targets).
        # AUTO-03 D.4: con la meta en shadow, el veredicto que la meta del
        # producto habria dado se congela igual; sus lecturas (ventana de
        # cortes, efecto del caso) van en un SAVEPOINT para que un error no
        # aborte TX2. La hoja sigue saltada con sin_target.
        def juicio_hipotetico(t: Decimal, equilibrio: Decimal | None):
            with conn.transaction():
                return _juicio_hoja(
                    conn,
                    t,
                    platform=platform,
                    goal=goal,
                    entidad_id=entidad_id,
                    ad_group_id=ad_group_id,
                    current_bid=current_bid,
                    bid_currency=bid_currency,
                    decided_at=decided_at,
                    corte_pause=corte_pause,
                    pause_economica=pause_economica,
                    politica=politica,
                    lecturas=lecturas,
                    proteccion_cache=proteccion_cache,
                    equilibrio_acos_pct=equilibrio,
                )

        _anota_sombra(
            productos_ciclo,
            entidad_id=entidad_id,
            ad_group_id=ad_group_id,
            goal=goal,
            setting_target=setting_target,
            margen_plataforma=margen_plataforma,
            target_margen_familia=target_margen_familia,
            previo=previo,
            meta_viva=meta_viva,
            target=None,
            procedencia=None,
            actual=Mantener(MOTIVO_SIN_TARGET),
            decide_con=juicio_hipotetico,
        )
        contadores.skips_entidad[MOTIVO_SIN_TARGET] += 1
        tick()
        return
    procedencia = g.peldano_target_acos(
        goal.target_acos_pct,
        goal.scope,
        margen_plataforma,
        setting_target,
        target_margen_familia,
        target_margen_producto=target_margen_producto,
    )
    snapshot = {
        "margen_plataforma": snapshot_margen,
        "margen_familia": snapshot_fam,
        "margen_producto": meta_viva.snapshot() if meta_viva is not None else None,
    }.get(procedencia)
    evaluacion_producto = meta_viva.product_id if meta_viva is not None else None
    # C.2a: el freeze va AQUI, no mas arriba — cascada_target_acos revienta
    # con un peldano invalido y meterla en hojas que hoy salen limpias
    # (veto/inerte) inventaria fallas nuevas.
    contadores.targets[entidad_id] = (target, procedencia)
    floor, ceiling = g.resuelve_floor_ceiling(goal, PLATAFORMAS_MONEDA[platform])
    costo_piso, cortes_hoja, pausa = _insumos_pausa(
        conn,
        platform=platform,
        entidad_id=entidad_id,
        decided_at=decided_at,
        corte_pause=corte_pause,
        pause_economica=pause_economica,
        proteccion_cache=proteccion_cache,
    )
    if politica is None:
        # Cambio 4: con la politica apagada solo se decide la PAUSE; la
        # hoja que no pausa se cuenta con politica_apagada (no hay bid).
        resultado = bid.decide_pause(
            platform=platform,
            bids=None,
            cortes=cortes_hoja,
            target_acos_pct=target,
            umbral_pause=corte_pause.umbral,
            cost_min=costo_piso,
            policy_version=pausa.politica_economica,
            proteccion=pausa.proteccion,
        )
        _anota_sombra(
            productos_ciclo,
            entidad_id=entidad_id,
            ad_group_id=ad_group_id,
            goal=goal,
            setting_target=setting_target,
            margen_plataforma=margen_plataforma,
            target_margen_familia=target_margen_familia,
            previo=previo,
            meta_viva=meta_viva,
            target=target,
            procedencia=procedencia,
            actual=resultado,
            decide_con=lambda t, equilibrio: bid.decide_pause(
                platform=platform,
                bids=None,
                cortes=cortes_hoja,
                target_acos_pct=t,
                umbral_pause=corte_pause.umbral,
                cost_min=costo_piso,
                policy_version=pausa.politica_economica,
                proteccion=pausa.proteccion,
            ),
        )
        tick()
        if resultado.kind != "pause":
            if resultado.proteccion is not None and resultado.motivo in (
                subproteccion.MOTIVO_PROTEGIDA,
                subproteccion.MOTIVO_INDETERMINADA,
            ):
                _registra_proteccion(
                    contadores,
                    proteccion_cache,
                    entidad_id,
                    resultado.proteccion,
                    decidido_el=decided_at,
                )
                contadores.skips_entidad[resultado.motivo] += 1
                return
            contadores.skips_entidad[MOTIVO_POLITICA_APAGADA] += 1
            return
        if bloqueada:
            contadores.skips_entidad[MOTIVO_VETO_PENDIENTE] += 1
            return
        if g.en_cooldown(conn, entidad_id, ahora=decided_at, kind="pause"):
            contadores.skips_entidad[MOTIVO_COOLDOWN_7D] += 1
            return
        pendiente = _pendiente_pause_apagada(
            entidad_id,
            resultado,
            platform=platform,
            modo=modo,
            goal=goal,
            cortes=cortes_hoja,
            target=target,
            procedencia=procedencia,
            snapshot=snapshot,
            bid_actual=current_bid,
            bid_moneda=bid_currency,
            decided_at=decided_at,
            corte=corte_pause,
            evidencia=evidencia,
            cost_min=costo_piso,
            policy_version=pausa.politica_economica,
            proteccion=pausa.proteccion,
        )
        pendientes.append(replace(pendiente, evaluacion_producto=evaluacion_producto))
        contadores.decisiones["pause"] += 1
        return
    assert lecturas is not None  # _recorre_plataforma: politica => lecturas
    caso = lecturas.caso(
        conn,
        hoja_id=entidad_id,
        ad_group_id=ad_group_id,
        bid=BidVigente(valor=current_bid, moneda=bid_currency, piso=floor, techo=ceiling),
        target_acos_pct=target,
        pausa=pausa,
        equilibrio_acos_pct=(
            meta_viva.equilibrio_pct
            if meta_viva is not None
            and meta_viva.version == productos.POLITICA_ECONOMIA
            and procedencia == "margen_producto"
            else None
        ),
    )
    veredicto = decide(caso)
    _anota_sombra(
        productos_ciclo,
        entidad_id=entidad_id,
        ad_group_id=ad_group_id,
        goal=goal,
        setting_target=setting_target,
        margen_plataforma=margen_plataforma,
        target_margen_familia=target_margen_familia,
        previo=previo,
        meta_viva=meta_viva,
        target=target,
        procedencia=procedencia,
        actual=veredicto,
        decide_con=lambda t, equilibrio: decide(
            replace(
                caso,
                economia=replace(
                    caso.economia,
                    target_acos_pct=t,
                    plataforma=replace(
                        caso.economia.plataforma,
                        equilibrio_acos_pct=(
                            equilibrio
                            if equilibrio is not None
                            else caso.economia.plataforma.equilibrio_acos_pct
                        ),
                    ),
                ),
            )
        ),
    )
    tick()
    # ORBIT 07 M1: la subpolitica pura decide la espera y su transicion. Se
    # consulta aparte del veredicto para que un Mover autorizado durante la
    # espera siga su camino (R2: la proteccion solo suprime la pausa).
    protegida = _proteccion_activa(
        platform=platform,
        entidad_id=entidad_id,
        target=target,
        corte_pause=corte_pause,
        cortes_hoja=cortes_hoja,
        costo_piso=costo_piso,
        pausa=pausa,
        proteccion_cache=proteccion_cache,
        contadores=contadores,
        decided_at=decided_at,
    )
    if isinstance(veredicto, Pausar) and (bloqueada or protegida):
        # ORBIT 07 M1: la fila released de una pausa protegida conserva el
        # permiso de pausar. El ciclo no duplica la decision; la cola la
        # revalida y la aplica o la descarta.
        contadores.skips_entidad[MOTIVO_VETO_PENDIENTE] += 1
        return
    if isinstance(veredicto, Mantener):
        contadores.skips_entidad[veredicto.motivo] += 1
        return
    if entidad_id in hojas_bid_en_vuelo and not isinstance(veredicto, Pausar):
        # ARREGLOS 01 K (E-H3): un bid sin sello (resultado dudoso) espera a
        # confirmarse en la fase de apply. Solo se bloquean los movimientos de
        # bid; la pausa va a la cola con su veto y revalidacion.
        contadores.skips_entidad[MOTIVO_BID_EN_VUELO] += 1
        return
    if (
        ad_group_id in grupos_pausa_producto
        and not isinstance(veredicto, Pausar)
        and current_bid is not None
        and veredicto.bid_nuevo > current_bid
    ):
        # I.5: la pausa de producto en vuelo del grupo exclusivo gana a la
        # subida; una bajada sigue su camino.
        contadores.skips_entidad[MOTIVO_PAUSA_PRODUCTO_PENDIENTE] += 1
        return
    # Cambio 9: despues de decidir una PAUSE, su propio cooldown la salta
    # con cooldown_7d (fijo como produccion con el flag encendido; la rama
    # de bids kind=None se borro: R2 espera).
    if isinstance(veredicto, Pausar) and g.en_cooldown(
        conn, entidad_id, ahora=decided_at, kind="pause"
    ):
        contadores.skips_entidad[MOTIVO_COOLDOWN_7D] += 1
        return
    pendiente = pendiente_bid(
        caso,
        veredicto,
        modo=modo,
        goal=goal,
        decidido_el=decided_at,
        procedencia=procedencia,
        snapshot=snapshot,
        corte=corte_pause,
        evidencia=evidencia,
        cost_min=costo_piso,
    )
    pendientes.append(replace(pendiente, evaluacion_producto=evaluacion_producto))
    contadores.decisiones["pause" if isinstance(veredicto, Pausar) else "bid"] += 1


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
    keywords_propias: Mapping[int, frozenset[str]],
    bloqueadas: set[tuple[int, str, str | None]],
    margen_plataforma: Decimal | None,
    snapshot_margen: dict,
    familias_ciclo: _FamiliasCiclo,
) -> None:
    grupo_id, campaign_id, status, status_campana, grupo_external = fila
    terminos = windows.terminos_cortes(conn, grupo_id, decided_at)
    contadores.terminos += len(terminos.terminos)
    goal, motivo = _gates_entidad(
        conn,
        goals,
        campaign_id=campaign_id,
        entidad_id=grupo_id,
        status=status,
        ancestros=((MOTIVO_CAMPANA_NO_ENABLED, status_campana),),
        decided_at=decided_at,
    )
    if motivo is not None:
        # ad group fuera: sus terminos TODOS skip con ese motivo (CAMPAÑA
        # ACTIVA 01 · 1.6: incluidos los de clave bloqueada — el gate corre
        # ANTES del veto, que seria moot dentro de una campana apagada)
        contadores.skips_termino[motivo] += len(terminos.terminos)
        tick()
        return
    assert goal is not None
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
    # CORTES 01 (1.2/1.4): umbral de clicks Y piso de cost del GRUPO
    # resueltos UNA vez por ciclo (misma evidencia de la ventana D-90..D-10;
    # grupo ausente del dict -> evidencia None -> fallback/respaldo con piso
    # legacy). El piso es por plataforma (moneda manda) y SOLO del camino
    # negative.
    evidencia = evidencia_ad_groups.get(grupo_id)
    corte_negativo = cortes.umbral_corte(evidencia, "negative")
    piso_neg = cortes.piso_corte(evidencia, platform)
    # A7: el grupo gana trayectoria familiar: converge desde el ancla
    # (ultimo margen_familia entre sus hojas) o camina desde el destino o
    # cae, como hoy, sin ancla.
    target_margen_familia, snapshot_fam = _margen_familia_de_hoja(
        familias_ciclo, campaign_id, None, ancla_grupo=familias_ciclo.anclas_grupo.get(grupo_id)
    )
    target = g.cascada_target_acos(
        goal.target_acos_pct,
        setting_target,
        margen_plataforma,
        goal.scope,
        target_margen_familia,
    )
    if target is None:
        contadores.skips_termino[MOTIVO_SIN_TARGET] += len(terminos.terminos)
        tick()
        return
    procedencia = g.peldano_target_acos(
        goal.target_acos_pct,
        goal.scope,
        margen_plataforma,
        setting_target,
        target_margen_familia,
    )
    destino = None
    try:
        # Savepoint: en esquemas sin 0018 (fixtures pre-F1) el resolutor
        # revienta con UndefinedTable; se absorbe aqui para no abortar TX2
        # (REPEATABLE READ) y se sigue con la terna del goal, como antes
        # de F2. En produccion 0018 existe desde F1: inalcanzable.
        with conn.transaction():
            destino = harvest_destino.resolver_destino(
                conn, platform, campaign_id, origen_ad_group_external=grupo_external
            )
    except psycopg.errors.UndefinedTable:
        destino = None
    config_harvest, keywords, motivo_salto = _config_harvest_de(conn, goal, platform, destino)
    _registra_destino(conn, platform, campaign_id, destino, contadores)
    resultados = hygiene.decide_hygiene(
        platform=platform,
        terminos=terminos,
        target_acos_pct=target,
        config_harvest=config_harvest,
        keywords_existentes=keywords,
        keywords_propias=keywords_propias.get(grupo_id, frozenset()),
        umbral_negative=corte_negativo.umbral,
        piso_negative=piso_neg.piso_cost,
    )
    tick()
    for termino, resultado in zip(terminos.terminos, resultados, strict=True):
        if resultado.kind is None:
            # FABRICA 02 (A.1): con salto del resolutor, el candidato a
            # harvest (que decide_hygiene marca 'harvest_sin_config' por
            # config None) cuenta con el motivo DEL SALTO
            # (origen_es_destino / destino_inconsistente /
            # sin_destino_de_harvest); el resto de motivos pasa intacto y
            # decide_hygiene no se toca.
            motivo = resultado.motivo
            if motivo_salto is not None and motivo == hygiene.MOTIVO_HARVEST_SIN_CONFIG:
                motivo = motivo_salto
            contadores.skips_termino[motivo] += 1
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
                procedencia=procedencia,
                snapshot=(
                    snapshot_margen
                    if procedencia == "margen_plataforma"
                    else (snapshot_fam if procedencia == "margen_familia" else None)
                ),
                decided_at=decided_at,
                # el freeze SOLO en decisiones que consultan umbral de
                # clicks: negative (las harvest NO llevan inputs.corte)
                corte=corte_negativo if resultado.kind == "negative" else None,
                evidencia=evidencia if resultado.kind == "negative" else None,
                piso=piso_neg if resultado.kind == "negative" else None,
                destino=destino,
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
    cycle_id: int,
) -> _TargetCiclo:
    setting_target = g.target_desde_settings(settings, platform)
    # ORBIT 06 2.3: el peldano se resuelve UNA vez por ciclo en TX2 (misma
    # lectura que la evidencia) y viaja EXPLICITO a los dos caminos.
    target_ciclo = _resuelve_target_ciclo(
        conn,
        platform=platform,
        settings=settings,
        setting_target=setting_target,
        cycle_id=cycle_id,
        decided_at=decided_at,
    )
    # ORBIT 07 M2: la version economica se selecciona por setting (ausente =
    # auto03) y se valida UNA vez por plataforma; un valor ajeno falla cerrado.
    modo_economia = (
        productos.economia_desde_settings(settings, platform)
        if platform in productos.PLATAFORMAS
        else productos.ECONOMIA_AUTO03
    )
    # A5: insumos del peldano margen_familia, UNA vez por ciclo en TX2
    # (mismo snapshot); el destino del reemplazo es plataforma ?? setting.
    destino_familia = target_ciclo.margen if target_ciclo.margen is not None else setting_target
    familias_ciclo = _lee_familias_ciclo(
        conn,
        platform=platform,
        cycle_id=cycle_id,
        hoy=decided_at.date(),
        fraccion=g.fraccion_desde_settings(settings, platform),
        destino=destino_familia,
        economia=modo_economia,
    )
    target_ciclo = replace(
        target_ciclo, snapshot={**target_ciclo.snapshot, "familias": familias_ciclo.notas}
    )
    acos_campanas = {
        fila[0]: fila[1] for fila in conn.execute(_SQL_CAMPANAS, (platform,)).fetchall()
    }
    goals = lee_goals(conn, platform, list(acos_campanas))
    # AUTO-03 D.4: el interruptor es fail-closed como los demas settings
    # (valor ajeno = ValueError = ciclo failed); la lectura y el resolutor no.
    if platform in productos.PLATAFORMAS:
        modos = productos.modos_desde_settings(settings, platform)
        if modos != productos.ModosProducto("off", "off"):
            contadores.productos = _evalua_productos(
                conn,
                platform=platform,
                settings=settings,
                decided_at=decided_at,
                goals=goals,
                modos=modos,
                economia=modo_economia,
            )
    # C.4 B3: sin flag, ni se evaluan (fail-closed: un deploy no avisa
    # propuestas sin el INSERT deliberado de ads_propuestas_campana).
    if g.propuestas_campana_desde_settings(settings):
        contadores.propuestas_campana = propuestas_campana.lee_evaluaciones(
            conn,
            platform=platform,
            decidido=decided_at,
            settings=settings,
            target_margen=target_ciclo.margen,
        )
    evidencia_ad_groups = windows.ventanas_evidencia_ad_group(conn, platform, decided_at)
    # ARREGLOS 01 B (E-S1): keywords EXACT por ad group de ORIGEN, UNA
    # consulta por plataforma en TX2 (mismo snapshot REPEATABLE READ) junto a
    # la evidencia. Un termino que es keyword propia de su ad group no se
    # negativiza ni se harvestea.
    keywords_propias = hygiene.keywords_exactas_por_grupo(conn, platform)
    # ORBIT 07 M1: lectura comun de venta reciente y episodios, UNA vez por
    # plataforma en TX2. El setting selecciona la politica: ausente = historica
    # (ni lectura ni excepcion de la fila released); `proteccion_venta_v1` =
    # regla M1. Disponible tambien con la politica de bids apagada.
    if subproteccion.activa_desde_settings(settings):
        proteccion_cache = proteccion_venta.lee_cache(conn, platform, decided_at)
        claves_released = apply_cola.claves_released(conn, platform)
    else:
        proteccion_cache = None
        claves_released = frozenset()
    # BIDS 01 (D3): hojas inertes de la plataforma, UNA vez por ciclo en TX2
    # (mismo snapshot que la evidencia). Se pasa EXPLICITO a
    # _procesa_decisora (no en `comunes`: el camino de terminos no lo usa).
    inertes = {f[0] for f in conn.execute(_SQL_INERTES, (platform,)).fetchall()}
    # AUTO-03 I.5: grupos exclusivos con pausa de producto live en vuelo, UNA
    # vez por ciclo en TX2 (mismo snapshot).
    grupos_pausa_producto = frozenset(
        f[0] for f in conn.execute(_SQL_GRUPOS_PAUSA_PRODUCTO, (platform,)).fetchall()
    )
    # ARREGLOS 01 K (E-H3): hojas con un bid sin sello, UNA vez por ciclo en TX2
    # (mismo snapshot REPEATABLE READ que el resto de la decision).
    hojas_bid_en_vuelo = apply.hojas_con_bid_sin_sello(conn, platform)
    # CORTES 01 (1.2/1.3): evidencia por ad group, UNA consulta por
    # plataforma DENTRO de TX2 junto a las demas lecturas (mismo snapshot
    # REPEATABLE READ; spec: una ventana, una elegibilidad, un multiplicador).
    # Cache por ad group para resolver el umbral de pause una sola vez por grupo
    # en el ciclo, en simetria con negative.
    corte_pause_por_grupo: dict[
        int, tuple[cortes.UmbralResuelto, windows.EvidenciaAdGroup | None]
    ] = {}
    pause_economica = g.pause_economica_desde_settings(settings)
    # BIDS 02 M.3: el interruptor se resuelve UNA vez por plataforma en TX2
    # (fail-closed: valor ajeno = ValueError = ciclo failed, igual que un
    # target/fraccion/confianza corruptos). Con la politica apagada no se
    # leen lecturas (el ciclo solo decide PAUSE por hoja).
    politica = g.politica_bid_desde_settings(settings, platform)
    # ORBIT 07 M5/M6: la regla economica de repeticion y su version se
    # seleccionan por plataforma. Apagada no se leen ni congelan episodios (el
    # replay de las filas historicas conserva su semantica por la ausencia del
    # marcador); v1 y v2 estampan el episodio nuevo.
    version_economia = g.version_economia_cambio_desde_settings(settings, platform)
    lecturas: LecturasPlataforma | None = None
    if politica is not None:
        # Economia resuelta UNA vez por plataforma (las confianzas A3
        # corruptas tumban igual que hoy; ausentes usan los defaults
        # 0.80/0.70) y cuatro consultas.
        economia = EconomiaPlataforma(
            moneda=PLATAFORMAS_MONEDA[platform],
            equilibrio_acos_pct=target_ciclo.margen_neto_pct,
            gasto_para_concluir=g.gasto_para_concluir_desde_settings(settings, platform),
            confianza_recorte=g.confianza_recorte_desde_settings(settings, platform),
            confianza_subida=g.confianza_subida_desde_settings(settings, platform),
        )
        lecturas = lee_plataforma(
            conn,
            platform,
            decided_at,
            economia=economia,
            version_economia=version_economia,
        )
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
        margen_plataforma=target_ciclo.margen,
        # Las notas de familias viven solo en notes.target para no multiplicar
        # su JSON en el inputs de cada decision.
        snapshot_margen={k: v for k, v in target_ciclo.snapshot.items() if k != "familias"},
        familias_ciclo=familias_ciclo,
    )
    for fila in conn.execute(_SQL_DECISORAS, (platform,)).fetchall():
        contadores.entidades += 1
        _procesa_decisora(
            conn,
            fila=fila,
            corte_pause_por_grupo=corte_pause_por_grupo,
            inertes=inertes,
            pause_economica=pause_economica,
            politica=politica,
            lecturas=lecturas,
            productos_ciclo=contadores.productos,
            grupos_pausa_producto=grupos_pausa_producto,
            hojas_bid_en_vuelo=hojas_bid_en_vuelo,
            proteccion_cache=proteccion_cache,
            claves_released=claves_released,
            **comunes,
        )
    for fila in conn.execute(_SQL_GRUPOS, (platform,)).fetchall():
        contadores.ad_groups += 1
        _procesa_grupo(
            conn,
            fila=fila,
            acos_campanas=acos_campanas,
            keywords_propias=keywords_propias,
            **comunes,
        )
    if contadores.productos is not None and contadores.productos.modo_pausa != "off":
        for pendiente in _pendientes_pausa_producto(contadores.productos, decided_at=decided_at):
            pendientes.append(pendiente)
            contadores.decisiones["pause"] += 1
    return target_ciclo


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
    cycle_id: int,
) -> tuple[windows.MotivoSkip | None, _TargetCiclo | None, dict[str, str] | None]:
    """TX2: guarda de plataforma + claves de efecto bloqueadas + recorrido
    completo, TODO en una transaccion REPEATABLE READ (snapshot uniforme; SET
    TRANSACTION como PRIMERA sentencia). Devuelve (guarda disparada, peldano
    resuelto, saltos de destino previos): guarda None = ciclo completo; con
    guarda, el recorrido no corre y el peldano es None (el ciclo saltado no
    resuelve target) y los previos tambien (sin recorrido no hay avisos que
    filtrar). Los previos se leen en el MISMO snapshot (flanco del aviso)."""
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        guarda = windows.guarda_plataforma(conn, platform, ahora=decided_at)
        if guarda is not None:
            return (guarda, None, None)
        bloqueadas = apply_cola.claves_bloqueadas(conn, platform, decided_at)
        target_ciclo = _recorre_plataforma(
            conn,
            platform=platform,
            settings=settings,
            modo=modo,
            decided_at=decided_at,
            contadores=contadores,
            pendientes=pendientes,
            tick=tick,
            bloqueadas=bloqueadas,
            cycle_id=cycle_id,
        )
        return (None, target_ciclo, _saltos_previos(conn, platform, cycle_id))


@dataclass(frozen=True)
class _FaseApply:
    """Resultado de la fase de apply para _corre_fases: la seccion
    notes['apply'], si aborto (degraded) y lo que 3.3 notifica — UN aviso por
    corte NUEVO encolado (TX4 commitado) y las alertas de harvest failed con
    la bandera de envio fallido del canal (sellados 13/19)."""

    notas: dict
    fallo: bool
    avisos: tuple[notifica.CorteEncolado, ...] = ()
    alertas: tuple = ()
    # Preflight 1.4 (D3d): caps de quota llevados a su transicion por la fase
    # (reconciliacion + bids + cola) — UN aviso fail-silent por evento en 3.3.
    caps_saturados: tuple[CapSaturado, ...] = ()
    # FABRICA 02 (A.6): saltos de destino YA filtrados por el flanco. Los
    # produce el recorrido (TX2), no el apply: _fase_apply los re-emite
    # para que viajen a _fase_notifica por el mismo camino que los demas
    # avisos (todos salen de fase.*).
    avisos_destino: tuple[notifica.SaltoDestinoGrupo, ...] = ()


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
    proteccion_activa: bool = False,
    avisos_destino: tuple[notifica.SaltoDestinoGrupo, ...] = (),
) -> _FaseApply:
    """TX4 + fase de apply propiamente (decisiones 11/21; docstring del
    modulo). Devuelve _FaseApply (seccion notes['apply'], fallo, avisos de
    encola y alertas de harvest para 3.3). Stance
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
    avisos: tuple[notifica.CorteEncolado, ...] = ()
    alertas_harvest: list = []
    caps_saturados: list = []
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
        except Exception as exc:
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
        with conn.transaction():  # TX4 real: entra desde IDLE y commitea
            resumen = apply_cola.encola_cortes(
                conn, aplicador, cycle_id, modo_envelope=modo.modo, ahora=decided_at
            )
        avisos = resumen.avisos  # 3.3: solo lo COMMITADO se avisa (sellado 2)
        notas["cortes_encolados"] = {
            "live": resumen.encoladas_live,
            "shadow": resumen.encoladas_shadow,
            "choques": len(resumen.choques),
        }
        if modo.modo == "live" and "apply_error" not in notas:
            reconciliacion = apply_harvest.reconcilia_harvest(conn, aplicador, platform)
            alertas_harvest += reconciliacion.alertas
            caps_saturados += reconciliacion.caps_saturados
            rec_bids = apply.reconcilia_bids(conn, aplicador, platform)
            res_bids = aplicador.aplica_bids(
                apply.bids_del_ciclo(conn, cycle_id), escalera_global=modo.modo
            )
            caps_saturados += res_bids.caps_saturados
            res_cola = apply_cola.libera_vencidos(
                conn,
                platform,
                ahora=decided_at,
                aplicador=aplicador,
                cycle_id=cycle_id,
                proteccion_activa=proteccion_activa,
            )
            alertas_harvest += res_cola.alertas
            caps_saturados += res_cola.caps_saturados
            notas["bids_aplicados"] = res_bids.aplicadas
            notas["bids_descartados"] = len(res_bids.descartadas)
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
                # Obs4: economicas en espera de target (released, reintentan).
                "espera_target": res_cola.espera_target,
            }
            if res_cola.revalidaciones_economicas:
                notas["revalidaciones_economicas"] = res_cola.revalidaciones_economicas
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
        conn.commit()  # deja la conexion IDLE para que el sello siguiente commitee
    except ApplyAbortado as exc:
        conn.rollback()  # deja la conexion IDLE para que el sello commitee
        notas["apply_abortado_owner"] = True
        fallo = True
        logger.warning("fase de apply abortada (lease perdido): %s", scrub(str(exc)))
    except Exception as exc:
        conn.rollback()  # lo pre-HTTP ya quedo commitado en el ledger
        notas["apply_error"] = scrub(str(exc)) or type(exc).__name__
        fallo = True
        logger.warning("fase de apply abortada por error: %s", scrub(str(exc)))
    return _FaseApply(
        notas=notas,
        fallo=fallo,
        avisos=avisos,
        alertas=tuple(alertas_harvest),
        caps_saturados=tuple(caps_saturados),
        avisos_destino=avisos_destino,
    )


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
    guarda, target_ciclo, saltos_previos = _fase_lecturas(
        conn,
        platform=platform,
        settings=settings,
        modo=modo.modo,
        decided_at=decided_at,
        contadores=contadores,
        pendientes=pendientes,
        tick=tick,
        cycle_id=cycle_id,
    )
    # FABRICA 02 (A.6): FLANCO del aviso de destino — la campana C con
    # motivo M avisa si y solo si no hay previo con la clave o el previo
    # trae otro motivo para C (segunda corrida con el mismo salto = cero
    # mensajes; corregido y roto de nuevo = vuelve a avisar; cambio de
    # motivo = informacion nueva, criterio de evaluar_alertas de SP-API).
    avisos_destino = tuple(
        salto
        for camp, salto in contadores.saltos_grupo.items()
        if saltos_previos is None or saltos_previos.get(str(camp)) != salto.motivo
    )
    motivo = f"guarda_{guarda.guarda}" if guarda is not None else None
    cuerpo = _notas_cuerpo(
        contadores,
        ciclos_muertos,
        modo.nota,
        motivo,
        guarda.detalle if guarda is not None else None,
        target_ciclo.snapshot if target_ciclo is not None else None,
        harvest_destino=(_harvest_destino_notes(contadores) if target_ciclo is not None else None),
    )
    status = "degraded" if guarda is not None else "done"
    with conn.transaction():  # TX3: decisiones + cierre del envelope, atomicos
        # AUTO-03 D.4: las evaluaciones van antes que las decisiones (F las
        # referencia por FK) y en su propio SAVEPOINT.
        ids_evaluacion: dict[int, int] = {}
        if contadores.productos is not None:
            cuerpo["productos"], ids_evaluacion = _notas_productos(
                conn, cycle_id, config_id, contadores.productos
            )
            _descarta_sin_evidencia(contadores, pendientes, ids_evaluacion, cuerpo)
        _inserta_decisiones(conn, cycle_id, config_id, decided_at, pendientes, ids_evaluacion)
        _inserta_targets(conn, cycle_id, decided_at, contadores)
        # ORBIT 07 M1: los episodios de venta reciente abiertos en TX2 se
        # escriben aqui, en la misma transaccion que las decisiones.
        proteccion_venta.inserta_transiciones(conn, contadores.protecciones)
        # C.4 B3: la guarda va con el mismo flag que la lectura (con flag
        # apagado no hay nada que persistir; la condicion es cinturon).
        # Obs1: TX3 vuelve a READ COMMITTED (el REPEATABLE READ no tenia
        # prueba ni justificacion y el descarte concurrente de C.5 lo
        # abortaria con SerializationFailure; la concurrencia la cubre el
        # FOR UPDATE de _SQL_ULTIMO).
        if target_ciclo is not None and g.propuestas_campana_desde_settings(settings):
            cuerpo["propuestas_campana"] = propuestas_campana.guarda_evaluaciones(
                conn, contadores.propuestas_campana, decided_at
            )
        notas = json.dumps(cuerpo, ensure_ascii=False, default=str)
        _cierra_envelope(conn, cycle_id, status, len(pendientes), notas)
    # FASE DE APPLY DENTRO DEL LOCK (2.4): TX4 + apply propio, DESPUES de TX3
    # (las decisiones ya estan commitadas) y ANTES del return — el lock se
    # libera recien en el finally de corre_ciclo, con el apply ya corrido.
    fase = _fase_apply(
        conn,
        cycle_id=cycle_id,
        modo=modo,
        platform=platform,
        decided_at=decided_at,
        job_key=job_key,
        owner=owner,
        guard=_guard_apply(conn, job_key, owner, tick, hb=hb),
        aplicador_factory=aplicador_factory,
        proteccion_activa=subproteccion.activa_desde_settings(settings),
        avisos_destino=avisos_destino,
    )
    notas_apply = fase.notas
    if notas_apply:
        cuerpo["apply"] = notas_apply
        if fase.fallo:
            status = "degraded"
    # C.4 B1: avisos Telegram de propuestas de campana nuevas, con el
    # mismo flag que la evaluacion (B3) y la misma filosofia fail-silent
    # que _fase_notifica: un fallo del canal deja pending (+reintento) y
    # NOTA, jamas rompe el ciclo ni degrada el status.
    nota_propuesta: str | None = None
    if g.propuestas_campana_desde_settings(settings):
        try:
            resultado_avisos = propuestas_campana.envia_avisos_pendientes(conn)
        except Exception as exc:
            logger.warning("notifica: fallo enviando avisos de propuesta: %s", scrub(str(exc)))
            nota_propuesta = "fallo: avisos de propuesta no enviados (ver log)"
        else:
            if resultado_avisos["fallos"]:
                nota_propuesta = (
                    "fallo: aviso de propuesta nueva no enviado por Telegram "
                    f"(propuestas {resultado_avisos['fallos']}: quedan pending "
                    "y reintentan en el ciclo siguiente)"
                )
            # Un latido por mensaje (mismo patron que _fase_notifica: N
            # avisos con el canal lento no comen el lease).
            for _ in range(len(resultado_avisos["enviados"]) + len(resultado_avisos["fallos"])):
                tick()
    # NOTIFICA (3.3, sellados 2/19): la NOTA telegram se integra al notes ANTES
    # del sello post-apply (la escritura final de notes del ciclo) para que el
    # fallo del canal viva en la MISMA notes — un fallo de Telegram JAMAS
    # degrada el status (degradar sigue siendo solo cosa del apply).
    telegram = _fase_notifica(
        fase.avisos,
        fase.alertas,
        caps_saturados=fase.caps_saturados,
        avisos_destino=fase.avisos_destino,
        cycle_id=cycle_id,
        platform=platform,
        modo=modo.modo,
        status=status,
        decisions_count=len(pendientes),
        notas_apply=notas_apply,
        skips=cuerpo["skips"],
        # ORBIT 06 2.3 segunda vuelta: el digest compara contra el ancla
        # del acumulado (ausente = primera vez y se avisa).
        target=cuerpo.get("target"),
        target_ancla=target_ciclo.ancla if target_ciclo is not None else None,
        tick=tick,
    )
    # ORBIT 06 2.3 segunda vuelta: si el ancla del digest avanzo, el sello
    # principal (previo al digest) lleva el ancla vieja y HAY que re-sellar
    # aunque el ciclo este silencioso (sin apply ni fallos de canal).
    ancla_avanzo = (
        target_ciclo is not None
        and isinstance(cuerpo.get("target"), dict)
        and cuerpo["target"].get("ultimo_avisado") != target_ciclo.ancla
    )
    if nota_propuesta is not None:
        telegram["aviso_propuesta"] = nota_propuesta
    if notas_apply or telegram or ancla_avanzo:
        if telegram:
            cuerpo["telegram"] = telegram
        # Obs3r2: lo anexado durante la fase sobrevive al reemplazo del sello.
        _mezcla_evidencias_persistidas(conn, cycle_id, cuerpo)
        notas = json.dumps(cuerpo, ensure_ascii=False, default=str)
        with tx_durable(conn):
            _sella_apply(conn, cycle_id, notas, degradar=fase.fallo)
    return ResultadoCiclo(cycle_id, status, len(pendientes), notas)


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
    gano = False
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
    except BaseException as exc:
        if cycle_id is not None:  # CicloOcupado no tiene envelope que sellar
            _sello_fallido(conn, cycle_id, exc, contadores, muertos)
        raise
    finally:
        # Solo libera quien gano el claim: si dos procesos comparten owner,
        # el perdedor de CicloOcupado borraria el lock del ganador.
        if gano:
            _libera_lock(hb, conn, job_key, owner)
        if hb is not None:
            hb.close()
