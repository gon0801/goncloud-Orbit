"""Cola de cortes del aplicador (ORBIT 04, task 2.2).

La cola de CORTES (pause/negative/harvest) con ventana de veto: los bids
aplican en su ciclo SIN tocar esta cola (sellado 1). Este modulo NO importa
app.ads.write (candado de test_architecture: solo app/apply.py): el cliente
de escritura llega por parametro o se le pide al Aplicador, y quota/ledger/
readback se REUSAN de app/apply — la cola no reimplementa nada de eso.

Diseno SELLADO (plans/orbit-04.md decisiones 1-6, 8, 17; docs/APPLY.md §1-§3,
§5.4; contrato cross-plan de CORTES 01):

- ENCOLA LA APP (sellado 4): toda decision de corte del ciclo deja fila en
  apply_queue (invariante corte<->cola; no hay skip posible al ENCOLAR — el
  skip por clave va en el ciclo siguiente, antes de decidir). El modo se
  re-resuelve POR DECISION (escalera + goal.mode + enabled + existencia,
  JAMAS inputs.modo): live -> modo='live'; no-live -> modo='shadow' (el dueno
  practica el veto con candidatos reales). Si el INSERT choca el unico parcial
  por clave de efecto, la decision queda (append-only) y el choque viaja en
  el resumen. vence_el = ahora + 48h.
- SKIP POR CLAVE DE EFECTO (sellado 5): en-vuelo (fila NO terminal) o bloqueo
  vigente (vetoed con vence_el > ahora) bloquean la clave (platform,
  ad_entity_id, familia, search_term); un veto VENCIDO no bloquea.
- LIBERACION FIFO de vencidas (pending_veto, modo live) con el ORDEN SELLADO
  del brief §3: liberacion atomica -> re-validacion PRE-claim sobre released
  -> cobro de quota -> claim atomico released->applying -> ledger PRE-HTTP ->
  HTTP -> readback + sello. Un descarte SIEMPRE antes del claim y NUNCA
  despues del cobro (la quota no se quema en descartes).
- RE-VALIDACION (sellado 6 + contrato cross-plan CORTES 01, ronda 2 qwen): la
  regla completa del motor SE RE-DECIDE con evidencia FRESCA anclada al reloj
  de LIBERACION — ventanas_evidencia_ad_group(conn, platform, decided_at) se
  llama con el instante de LIBERAR como decided_at, NO el de decidir, y el
  umbral/piso se RE-RESUELVEN (cortes.umbral_corte / cortes.piso_corte). Se
  JAMAS reusa inputs.corte.umbral_clicks_usado congelado (y NO se limita a
  "orders>0": el umbral fresco tambien descarta — pause: decide_bid con
  umbral fresco; negative: decide_hygiene con umbral+piso frescos).
- RE-CHECK DE ESTADO VIVO por GET fresco (jamas el cache, sellado 16) en la
  familia entity_cut (pause): entidad no viva -> el corte es moot; ENABLED +
  pause propio verificado -> INSERT reactivacion_manual (idempotente por PK,
  sellado 17) y el corte se descarta: la gracia de 7d desde detectada_en no
  vuelve a cortar la entidad.
- CAP AGOTADO -> cortes esperan FIFO en released y SIGUEN vetables (§5.4).
- REVERSAS pause/negative (regla 7, sellado 12): ledger tipo 'reversa'
  EXENTAS de quota, mismo readback; no limpian cooldown.
- HARVEST (2.3): hook delegado a apply_harvest.aplica_harvest — harvest_job
  nace AL LIBERAR (sellado 13) y TODA la ejecucion (fases, bid sugerido
  clampeado, reversas) vive en ese modulo; la cola solo conecta el hook.

Elecciones DECLARADAS de esta task:

- Re-validacion sobre la fila RELEASED (se libera pending_veto->released y
  ahi se re-valida): es la letra del brief §3.1 y deja el discard vivir en la
  transicion released->discarded que el GRANT del motor ya tiene. El discard
  del motor es SOLO de filas LIVE (el de filas shadow exige admin por trigger,
  hallazgo post-merge PR #25) — y esta cola solo selecciona live.
- El cobro de quota va ANTES del claim (ver docstring de libera_vencidos):
  la maquina de 0002 no tiene applying -> released y §5.4 exige que la fila
  que espera quota quede released y vetable.
- La escalera de un corte de TERMINO mira la existencia/state del GRUPO y su
  campana por el salto grupo->campana (Aplicador.modo_efectivo esta sellado
  para hojas keyword/product_target; las pausas delegan en el VERBATIM).
- Encola necesita las ids de las decisiones del ciclo recien commitado: las
  lee por SELECT del ciclo (cycle.py NO se toca — _inserta_decisiones no
  expone RETURNING y aqui no hace falta).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

import psycopg
from psycopg.types.json import Json

from app import apply, apply_harvest
from app.apply import Aplicador, DecisionBid, consume_quota
from app.optimizer import bid as motor_bid
from app.optimizer import cortes, hygiene, windows
from app.optimizer import goals as g

# Ventana de veto de 48h al encolar (sellado 2: el reloj NO se detiene por
# infra caida). Gracia de reactivacion 7d DESDE detectada_en (sellado 17).
VENTANA_VETO = dt.timedelta(hours=48)
GRACIA_REACTIVACION = dt.timedelta(days=7)

# Skip del ciclo por clave de efecto (sellado 5): motivo del vocabulario
# cerrado con el que el ciclo NO re-decide una clave bloqueada.
MOTIVO_VETO_PENDIENTE = "veto_pendiente"

# Descartes del motor por re-validacion fallida (sellado 6; filas LIVE: el
# discard de filas shadow exige admin por trigger, hallazgo post-merge PR #25).
MOTIVO_VENDIO_EN_VENTANA = "vendio_en_ventana"
MOTIVO_YA_NO_CALIFICA = "ya_no_califica"
MOTIVO_ENTIDAD_NO_VIVA = "entidad_no_viva"
MOTIVO_REACTIVACION_MANUAL = "reactivacion_manual"

# La re-decision de la regla llama al motor PURO por su firma completa; la
# rama pause/negative NO consume target/floor/ceiling (bids=None cierra la
# rama de bandas y config_harvest=None la de harvest). Estos valores solo
# satisfacen la validacion de firma del motor: JAMAS se persisten (regla 3).
_TARGET_REVALIDA = Decimal("100")
_FLOOR_REVALIDA = Decimal("0.01")
_CEILING_REVALIDA = Decimal("10000")

# ---------------------------------------------------------------------------
# SQL del modulo (parsea el test de sintaxis con pglast)
# ---------------------------------------------------------------------------

_SQL_CLAVES_BLOQUEADAS = """
SELECT ad_entity_id, familia, search_term
  FROM apply_queue
 WHERE platform = %s::platform
   AND (estado NOT IN ('applied', 'failed', 'vetoed', 'discarded')
        OR (estado = 'vetoed' AND vence_el > %s))
"""

_SQL_CORTES_CICLO = """
SELECT id, ad_entity_id, kind, search_term
  FROM decision
 WHERE cycle_id = %s AND kind IN ('pause', 'negative', 'harvest')
 ORDER BY id
"""

_SQL_PLATFORM_CICLO = """
SELECT platform::text FROM optimizer_cycle WHERE id = %s
"""

_SQL_INSERT_COLA = """
INSERT INTO apply_queue (platform, ad_entity_id, kind, search_term, decision_id,
                         modo, estado, vence_el, request_payload)
VALUES (%s::platform, %s, %s, %s, %s, %s, 'pending_veto', %s, %s)
RETURNING id
"""

_SQL_VENCIDAS = """
SELECT id, kind, ad_entity_id, search_term, decision_id, request_payload
  FROM apply_queue
 WHERE platform = %s::platform AND estado = 'pending_veto' AND modo = 'live'
   AND vence_el <= %s
 ORDER BY encolado_at, id
"""

_SQL_FILA = """
SELECT id, kind, ad_entity_id, search_term, decision_id, request_payload
  FROM apply_queue WHERE id = %s
"""

# Transiciones atomicas: el WHERE del estado esperado ES la carrera (cero
# filas = alguien mas movio la fila: releer y saltar, brief §1.5/§3.2).
_SQL_LIBERA = """
UPDATE apply_queue SET estado = 'released', released_at = now()
 WHERE id = %s AND estado = 'pending_veto'
RETURNING id
"""

_SQL_CLAIM = """
UPDATE apply_queue SET estado = 'applying', applying_at = now()
 WHERE id = %s AND estado = 'released'
RETURNING id
"""

_SQL_DESCARTA = """
UPDATE apply_queue SET estado = 'discarded', discarded_at = now(), discard_motivo = %s
 WHERE id = %s AND estado = %s
RETURNING id
"""

# Sellado 17: la gracia vive en reactivacion_manual (detectada_en PK por
# entidad) y el pause propio verificado se lee del resumen (verify_ok TRUE).
_SQL_GRACIA = """
SELECT detectada_en FROM reactivacion_manual WHERE ad_entity_id = %s
"""

_SQL_PAUSE_PROPIO = """
SELECT EXISTS (
    SELECT 1
      FROM decision_application da
      JOIN decision d ON d.id = da.decision_id
     WHERE d.ad_entity_id = %s AND d.kind = 'pause' AND da.verify_ok IS TRUE
)
"""

_SQL_INSERT_REACTIVACION = """
INSERT INTO reactivacion_manual (ad_entity_id) VALUES (%s) ON CONFLICT DO NOTHING
"""

_SQL_PADRE = """
SELECT parent_id FROM ad_entity WHERE id = %s
"""

_SQL_EXTERNALES_GRUPO = """
SELECT grp.external_id, cam.external_id
  FROM ad_entity grp
  JOIN ad_entity cam ON cam.id = grp.parent_id AND cam.kind = 'campaign'
 WHERE grp.id = %s
"""

_SQL_CACHE_ESTADO = """
UPDATE ad_entity_state SET status = %s, synced_at = now() WHERE ad_entity_id = %s
"""

# La escalera de un corte de TERMINO mira la existencia/state del GRUPO (la
# entidad que decide el ciclo, _SQL_GRUPOS de cycle.py) y su campana es el
# padre directo del grupo — mismo criterio que Aplicador.modo_efectivo, que
# esta sellado para hojas keyword/product_target (decisiones de bid/pause).
_SQL_GRUPO_VIVO = """
SELECT e.parent_id AS campaign_id, (s.ad_entity_id IS NOT NULL) AS tiene_state
  FROM ad_entity e
  LEFT JOIN ad_entity_state s ON s.ad_entity_id = e.id
 WHERE e.id = %s AND e.kind = 'ad_group'
"""


# ---------------------------------------------------------------------------
# Estructuras de salida
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResumenEncolado:
    """Resultado del encolado de UN ciclo. `choques` declara las decisiones
    cuyo INSERT choco el unico parcial por clave de efecto (clave ya en
    vuelo): la decision NO se borra (decision es append-only) y el skip por
    clave lo hace el CICLO siguiente antes de decidir — semantica declarada
    del invariante corte<->cola (sellado 4)."""

    encoladas_live: int
    encoladas_shadow: int
    choques: list[str]


@dataclass(frozen=True)
class ResultadoLiberacion:
    """Resultado del barrido FIFO de vencidas. `sin_quota` son las que
    esperan en released (cap agotado: siguen vetables, brief §5.4);
    `carreras_perdidas` son los claims/liberaciones atomicos que vieron 0
    filas (un veto llego primero: pierden LIMPIO, sin HTTP)."""

    liberadas: int
    aplicadas: int
    fallidas: int
    descartadas: list[str]
    sin_quota: int
    carreras_perdidas: int


@dataclass(frozen=True)
class FilaCola:
    """Espejo de una fila de apply_queue tal como la consume el aplicador de
    cortes (reversas incluidas). `request_payload` es la intencion EXACTA que
    el ledger congela pre-HTTP (COMMENT de la columna en 0002)."""

    id: int
    kind: str
    ad_entity_id: int
    search_term: str | None
    decision_id: int
    request_payload: dict


def fila_cola(conn: psycopg.Connection, queue_id: int) -> FilaCola | None:
    """Carga UNA fila de la cola como FilaCola (None si no existe)."""
    fila = conn.execute(_SQL_FILA, (queue_id,)).fetchone()
    return FilaCola(*fila) if fila is not None else None


# ---------------------------------------------------------------------------
# Skip por clave de efecto (sellado 5)
# ---------------------------------------------------------------------------


def claves_bloqueadas(
    conn: psycopg.Connection, platform: str, ahora: dt.datetime
) -> set[tuple[int, str, str | None]]:
    """Claves de efecto (ad_entity_id, familia, search_term) con fila NO
    terminal en-vuelo O fila vetoed con bloqueo VIGENTE (vence_el > ahora).

    Un veto VENCIDO no bloquea: al vencer el motor re-propone con fila nueva
    (sellado 3). `ahora` llega por parametro (determinismo, regla 2)."""
    return {
        (fila[0], fila[1], fila[2])
        for fila in conn.execute(_SQL_CLAVES_BLOQUEADAS, (platform, ahora)).fetchall()
    }


def skip_por_clave(
    conn: psycopg.Connection,
    platform: str,
    claves,
    ahora: dt.datetime,
) -> set[tuple[int, str, str | None]]:
    """Cuales de `claves` estan bloqueadas (en-vuelo o veto vigente): el ciclo
    (2.4) las salta con motivo MOTIVO_VETO_PENDIENTE ANTES de decidir. Devuelve
    el subconjunto bloqueado de los candidatos."""
    return set(claves) & claves_bloqueadas(conn, platform, ahora)


# ---------------------------------------------------------------------------
# Encolado por la APP (sellado 4: invariante corte<->cola)
# ---------------------------------------------------------------------------


def _meet_goals(conn: psycopg.Connection, platform: str, campaign_id, escalera_global: str) -> str:
    """Meet escalera + goal (precedencia campana > plataforma con
    goals.resuelve_goal) para la campana dada. Es el mismo criterio (y el
    mismo SQL de goals) que Aplicador.modo_efectivo, extendido para el salto
    grupo->campana de los cortes de termino."""
    goal_campana: g.Goal | None = None
    goal_plataforma: g.Goal | None = None
    for f in conn.execute(apply._SQL_GOALS_ENTIDAD, (platform, campaign_id)).fetchall():
        goal = g.Goal(
            scope=f[0],
            ad_entity_id=f[1],
            platform=f[2],
            target_acos_pct=f[3],
            bid_floor=f[4],
            bid_ceiling=f[5],
            bid_currency=f[6],
            harvest_campaign_id=f[7],
            harvest_ad_group_id=f[8],
            harvest_default_bid=f[9],
            enabled=f[10],
            mode=f[11],
        )
        if goal.scope == "campaign":
            goal_campana = goal
        else:
            goal_plataforma = goal
    resuelto = g.resuelve_goal(goal_campana, goal_plataforma)
    if resuelto is None or not resuelto.enabled:
        return "shadow"
    return g.modo_efectivo(escalera_global, resuelto.mode)


def _modo_efectivo_corte(
    conn: psycopg.Connection,
    aplicador: Aplicador,
    platform: str,
    ad_entity_id: int,
    *,
    escalera_global: str,
) -> str:
    """Re-resuelve el modo POR DECISION para un corte. Las pausas viven sobre
    hojas keyword/product_target: delega VERBATIM en Aplicador.modo_efectivo
    (2.1). Los cortes de termino viven sobre el GRUPO: mismo criterio
    (escalera + goal.mode + enabled + existencia/state) con el salto
    grupo->campana que el SQL de hojas de 2.1 no tiene — DECLARADO."""
    identidad = apply._identidad(conn, ad_entity_id)
    if identidad is None:
        return "shadow"
    if identidad[0] in ("keyword", "product_target"):
        decision = DecisionBid(
            id=0,
            ad_entity_id=ad_entity_id,
            old_value=None,
            new_value=None,
            value_currency=None,
            inputs={},
        )
        return aplicador.modo_efectivo(conn, decision, escalera_global=escalera_global)
    fila = conn.execute(_SQL_GRUPO_VIVO, (ad_entity_id,)).fetchone()
    if fila is None or not fila[1]:
        return "shadow"  # grupo inexistente o sin state (regla 3)
    return _meet_goals(conn, platform, fila[0], escalera_global)


def _payload_pause(kind_entidad: str, external_id: str) -> dict:
    """Payload EXACTO del corte pause (mismo shape que _cambiar_estado del
    write client; PENDIENTE del probe 2.5 como todo shape de mutacion)."""
    campo = "keywordId" if kind_entidad == "keyword" else "targetId"
    return {campo: external_id, "state": "userPaused"}


def _payload_term(grupo_ext: str, campana_ext: str, term: str) -> dict:
    """Payload EXACTO del primer HTTP de un corte de termino: el negative
    exacto en el ad group ORIGEN (para harvest es el primer paso de su cadena,
    brief §6 — 2.3 extiende el segundo POST con su bid)."""
    return {
        "adGroupId": grupo_ext,
        "campaignId": campana_ext,
        "keywordText": term,
        "matchType": "exact",
    }


def encola_cortes(
    conn: psycopg.Connection,
    aplicador: Aplicador,
    cycle_id: int,
    *,
    modo_envelope: str,
    ahora: dt.datetime,
) -> ResumenEncolado:
    """Encola TODAS las decisiones de corte del ciclo (pause/negative/harvest).

    INVARIANTE corte<->cola (sellado 4): toda decision de corte del ciclo deja
    su fila — no hay skip posible al ENCOLAR (el skip por clave va en el ciclo
    siguiente, antes de decidir). El modo se re-resuelve POR DECISION (misma
    escalera de 2.1, JAMAS inputs.modo): live -> modo='live'; no-live ->
    modo='shadow' (el dueno practica el veto con candidatos reales, sellado 6).
    Si el INSERT choca el unico parcial (clave ya en vuelo), la decision queda
    (decision es append-only) y el choque viaja en el resumen. vence_el =
    ahora + 48h. `aplicador` llega por parametro porque modo_efectivo es
    metodo de Aplicador (plataforma atada a la instancia); la plataforma de la
    cola sale del ciclo (la fuente de sus decisiones)."""
    platform = conn.execute(_SQL_PLATFORM_CICLO, (cycle_id,)).fetchone()[0]
    live = 0
    shadow = 0
    choques: list[str] = []
    for dec_id, entidad, kind, term in conn.execute(_SQL_CORTES_CICLO, (cycle_id,)).fetchall():
        modo = _modo_efectivo_corte(
            conn, aplicador, platform, entidad, escalera_global=modo_envelope
        )
        identidad = apply._identidad(conn, entidad)
        if (
            kind == "pause"
            and identidad is not None
            and identidad[0]
            in (
                "keyword",
                "product_target",
            )
        ):
            payload = _payload_pause(identidad[0], identidad[1])
        elif kind == "pause":
            # Entidad de pause sin identidad conocida: fila shadow (nunca
            # vuela) con payload vacio — regla 3, jamas un id inventado.
            payload = {}
        else:
            fila = conn.execute(_SQL_EXTERNALES_GRUPO, (entidad,)).fetchone()
            payload = _payload_term(fila[0], fila[1], term) if fila is not None else {}
        try:
            with conn.transaction():
                conn.execute(
                    _SQL_INSERT_COLA,
                    (
                        platform,
                        entidad,
                        kind,
                        term,
                        dec_id,
                        modo,
                        ahora + VENTANA_VETO,
                        Json(payload),
                    ),
                )
            if modo == "live":
                live += 1
            else:
                shadow += 1
        except psycopg.errors.UniqueViolation:
            choques.append(
                f"decision {dec_id}: clave de efecto en vuelo"
                f" (entidad {entidad}, kind {kind}, termino {term!r})"
            )
    return ResumenEncolado(encoladas_live=live, encoladas_shadow=shadow, choques=choques)


# ---------------------------------------------------------------------------
# Re-validacion PRE-claim (sellado 6; contrato cross-plan de CORTES 01)
# ---------------------------------------------------------------------------


def _estado_leido(resp, contenedor: str) -> str | None:
    """El estado del READBACK fresco. PENDIENTE del probe 2.5: el shape
    (contenedor 'keywords'/'targets' con campo 'state') es supuesto sellado
    por tests; el probe lo fija contra la API real. None = ilegible/vacio."""
    try:
        filas = resp.json().get(contenedor)
        estado = filas[0].get("state") if filas else None
    except (ValueError, AttributeError, IndexError, KeyError, TypeError):
        return None
    return estado if isinstance(estado, str) else None


def _gracia_activa(conn: psycopg.Connection, ad_entity_id: int, ahora: dt.datetime) -> bool:
    fila = conn.execute(_SQL_GRACIA, (ad_entity_id,)).fetchone()
    return fila is not None and (ahora - fila[0]) < GRACIA_REACTIVACION


def _pause_propio_verificado(conn: psycopg.Connection, ad_entity_id: int) -> bool:
    return conn.execute(_SQL_PAUSE_PROPIO, (ad_entity_id,)).fetchone()[0]


def _revalida_pause(
    conn: psycopg.Connection,
    aplicador: Aplicador,
    platform: str,
    fila: FilaCola,
    ahora: dt.datetime,
) -> str | None:
    """Re-validacion de un pause: re-check de estado vivo por GET FRESCO
    (jamas el cache, sellado 16) y re-decision de la regla completa del motor
    con el umbral RE-RESUELTO a la evidencia FRESCA anclada al reloj de
    LIBERACION (el decided_at de ventanas_evidencia_ad_group es el instante de
    liberar, NO el de decidir — contrato cross-plan de CORTES 01)."""
    identidad = apply._identidad(conn, fila.ad_entity_id)
    if identidad is None or identidad[0] not in apply._KINDS_DECISORAS:
        return MOTIVO_ENTIDAD_NO_VIVA
    kind, external_id = identidad
    _, path, contenedor, param = apply._KINDS_DECISORAS[kind]
    resp = aplicador._cliente().get_sellado(path, params={param: external_id})
    estado = _estado_leido(resp, contenedor)
    if estado != "enabled":
        # Ya no existe / ARCHIVED / ya PAUSED: el corte es moot. La marca de
        # reactivacion_manual NO aplica aqui: es el caso pause-propio + ENABLED
        # (sellado 17), no el de entidad apagada.
        return MOTIVO_ENTIDAD_NO_VIVA
    if _gracia_activa(conn, fila.ad_entity_id, ahora):
        return MOTIVO_REACTIVACION_MANUAL
    if _pause_propio_verificado(conn, fila.ad_entity_id):
        # Pause propio verificado + estado vivo ENABLED: el dueno re-activo a
        # mano. Se marca el instante (idempotente por PK) y NO se vuelve a
        # cortar durante la gracia de 7d.
        conn.execute(_SQL_INSERT_REACTIVACION, (fila.ad_entity_id,))
        return MOTIVO_REACTIVACION_MANUAL
    grupo = conn.execute(_SQL_PADRE, (fila.ad_entity_id,)).fetchone()[0]
    evidencia = windows.ventanas_evidencia_ad_group(conn, platform, ahora).get(grupo)
    umbral = cortes.umbral_corte(evidencia, "pause").umbral
    fresco = windows.ventana_cortes(conn, fila.ad_entity_id, ahora)
    resultado = motor_bid.decide_bid(
        platform=platform,
        bids=None,  # la re-decision es SOLO de la regla pause (cortes)
        cortes=fresco,
        target_acos_pct=_TARGET_REVALIDA,
        bid_actual=None,
        bid_moneda=None,
        floor=_FLOOR_REVALIDA,
        ceiling=_CEILING_REVALIDA,
        umbral_pause=umbral,
    )
    if resultado.kind == "pause":
        return None
    if fresco is not None and fresco.orders is not None and fresco.orders > 0:
        return MOTIVO_VENDIO_EN_VENTANA
    return MOTIVO_YA_NO_CALIFICA


def _revalida_negative(
    conn: psycopg.Connection,
    platform: str,
    fila: FilaCola,
    ahora: dt.datetime,
) -> str | None:
    """Re-validacion de un negative: la regla completa del motor contra la
    ventana FRESCA del termino (terminos_cortes a la hora de liberar) con
    umbral Y piso RE-RESUELTOS de la evidencia fresca del grupo (CORTES 01
    1.2/1.4). Sin re-check HTTP de estado vivo: la familia term_cut no pausa
    una entidad — su re-validacion ES la regla con evidencia fresca."""
    grupo = fila.ad_entity_id
    evidencia = windows.ventanas_evidencia_ad_group(conn, platform, ahora).get(grupo)
    umbral = cortes.umbral_corte(evidencia, "negative").umbral
    piso = cortes.piso_corte(evidencia, platform).piso_cost
    terminos = windows.terminos_cortes(conn, grupo, ahora)
    term = next((t for t in terminos.terminos if t.search_term == fila.search_term), None)
    if term is None:
        # Sin observaciones frescas del termino en la ventana: no alcanza el
        # umbral por construccion (regla 3: ausencia, jamas ceros inventados).
        return MOTIVO_YA_NO_CALIFICA
    single = windows.TerminosCortes(
        ad_entity_id=grupo,
        window_start=terminos.window_start,
        window_end=terminos.window_end,
        fechas_entidad=terminos.fechas_entidad,
        terminos=(term,),
    )
    (resultado,) = hygiene.decide_hygiene(
        platform=platform,
        terminos=single,
        target_acos_pct=_TARGET_REVALIDA,
        config_harvest=None,
        keywords_existentes=frozenset(),
        umbral_negative=umbral,
        piso_negative=piso,
    )
    if resultado.kind == "negative":
        return None
    if term.orders is not None and term.orders > 0:
        return MOTIVO_VENDIO_EN_VENTANA
    return MOTIVO_YA_NO_CALIFICA


def _revalida(
    conn: psycopg.Connection,
    aplicador: Aplicador,
    platform: str,
    fila: FilaCola,
    ahora: dt.datetime,
) -> str | None:
    """None = el corte sigue calificando. Un motivo = discard (PRE-claim: el
    descarte ocurre SIEMPRE antes del cobro, brief §3)."""
    if fila.kind == "pause":
        return _revalida_pause(conn, aplicador, platform, fila, ahora)
    if fila.kind == "negative":
        return _revalida_negative(conn, platform, fila, ahora)
    return None  # harvest: la matriz del brief §6.1 es de 2.3


# ---------------------------------------------------------------------------
# Ejecucion sellada de un corte reclamado
# ---------------------------------------------------------------------------


def _termina(conn: psycopg.Connection, fila: FilaCola, estado: str) -> None:
    sello = "applied_at" if estado == "applied" else "failed_at"
    conn.execute(
        f"UPDATE apply_queue SET estado = %s, {sello} = now() WHERE id = %s", (estado, fila.id)
    )


def _cliente_mutacion(aplicador: Aplicador):
    """El write client vive DETRAS del Aplicador: el candado de
    test_architecture (solo app/apply.py importa app.ads.write) obliga a que
    este modulo reciba el cliente por parametro o lo pida al aplicador —
    nunca construirlo (ni importarlo) aqui."""
    return aplicador._cliente()


def _ejecuta_pause(conn: psycopg.Connection, aplicador: Aplicador, fila: FilaCola) -> str:
    identidad = apply._identidad(conn, fila.ad_entity_id)
    if identidad is None or identidad[0] not in apply._KINDS_DECISORAS:
        _termina(conn, fila, "failed")
        return "failed"
    id_attempt = apply._ledger(
        conn, fila.decision_id, "normal", fila.request_payload, quota_cobrada=True
    )
    if id_attempt is None:
        _termina(conn, fila, "failed")  # tope 3: no existe 4o intento
        return "failed"
    conn.commit()  # intencion durable PRE-HTTP
    cliente = _cliente_mutacion(aplicador)
    try:
        if identidad[0] == "keyword":
            resp_http = cliente.pausar_keyword(identidad[1])
        else:
            resp_http = cliente.pausar_target(identidad[1])
    except apply.AdsApiErrorMutacion as exc:
        apply._sella_ledger(
            conn, id_attempt, ack=None, resultado=f"fallo http {exc.status}: {exc.cuerpo}"
        )
        conn.commit()
        _termina(conn, fila, "failed")
        return "failed"
    # 5xx/fallo ambiguo (AdsApiError): SUBE — ledger sin sello y fila applying
    # huerfana: ES el rastro, la reconciliacion (2.3, matriz §6.1) decide.
    ack = apply._json_seguro(resp_http)
    _, path, contenedor, param = apply._KINDS_DECISORAS[identidad[0]]
    estado = _estado_leido(cliente.get_sellado(path, params={param: identidad[1]}), contenedor)
    verify = estado == "userPaused"
    resultado = "ok" if estado is not None else "fallo:readback_sin_estado"
    with conn.transaction():
        apply._sella_ledger(conn, id_attempt, ack=ack, resultado=resultado)
        apply._confirma_resumen(conn, fila.decision_id, ack, verify, aplicador.cycle_id_ejecutor)
        if estado is not None:
            # cache con LO LEIDO (sellado 16): el status queda como Amazon lo
            # devolvio en el readback, jamas como lo pedimos.
            conn.execute(_SQL_CACHE_ESTADO, (estado, fila.ad_entity_id))
        _termina(conn, fila, "applied" if verify else "failed")
    return "applied" if verify else "failed"


def _ejecuta_negative(conn: psycopg.Connection, aplicador: Aplicador, fila: FilaCola) -> str:
    payload = fila.request_payload
    id_attempt = apply._ledger(conn, fila.decision_id, "normal", payload, quota_cobrada=True)
    if id_attempt is None:
        _termina(conn, fila, "failed")
        return "failed"
    conn.commit()  # intencion durable PRE-HTTP
    try:
        resp_http = _cliente_mutacion(aplicador).crear_negative_exacto(
            payload["adGroupId"], payload["campaignId"], payload["keywordText"]
        )
    except apply.AdsApiErrorMutacion as exc:
        apply._sella_ledger(
            conn, id_attempt, ack=None, resultado=f"fallo http {exc.status}: {exc.cuerpo}"
        )
        conn.commit()
        _termina(conn, fila, "failed")
        return "failed"
    ack = apply._json_seguro(resp_http)
    # Readback del negative = el ack del POST de creacion. La verificacion por
    # lista con identidad completa es de la RECONCILIACION (2.3, §6.1) y los
    # shapes los fija el probe 2.5; aqui el ack 2xx con cuerpo es la evidencia.
    with conn.transaction():
        apply._sella_ledger(conn, id_attempt, ack=ack, resultado="ok")
        apply._confirma_resumen(
            conn, fila.decision_id, ack, isinstance(ack, dict), aplicador.cycle_id_ejecutor
        )
        _termina(conn, fila, "applied")
    return "applied"


# ---------------------------------------------------------------------------
# Liberacion FIFO de vencidas
# ---------------------------------------------------------------------------


def libera_vencidos(
    conn: psycopg.Connection,
    platform: str,
    *,
    ahora: dt.datetime,
    aplicador: Aplicador,
) -> ResultadoLiberacion:
    """Barrido FIFO de las filas vencidas (pending_veto, modo live, vence_el
    <= ahora; por encolado_at/id). ORDEN SELLADO por fila (brief §3):

    1. liberacion atomica pending_veto -> released;
    2. re-validacion PRE-claim SOBRE la fila released (evidencia FRESCA al
       reloj de LIBERACION + GET fresco de estado vivo): motivo -> discard
       (un descarte SIEMPRE antes del claim, NUNCA despues del cobro);
    3. cobro de quota (apply.consume_quota): sin quota la fila QUEDA en
       released (espera FIFO y SIGUE vetable, §5.4). Se cobra ANTES del claim
       porque la maquina no tiene applying -> released (0002): cobrar despues
       del claim dejaria atrapada la fila que espera quota — declarado; el
       invariante "descarte jamas post-cobro" se conserva (el discard es el
       paso 2). Residual aceptado: si un veto gana la carrera entre cobro y
       claim, esa unidad queda cobrada sin intento (ventana de microsegundos,
       una unidad, visible en apply_quota_state);
    4. claim atomico released -> applying (0 filas = perdio la carrera);
    5. fila del ledger PRE-HTTP + commit (apply._ledger);
    6. HTTP (write client del aplicador) + readback + sello -> applied|failed.

    Las filas shadow JAMAS se seleccionan (sellado 6). kind harvest delega al
    hook apply_harvest.aplica_harvest (2.3: harvest_job nace AL LIBERAR,
    sellado 13) DESPUES de la re-validacion y ANTES del cobro de la cola —
    la UNICA unidad de la operacion logica la cobra el hook."""
    filas = [FilaCola(*f) for f in conn.execute(_SQL_VENCIDAS, (platform, ahora)).fetchall()]
    liberadas = aplicadas = fallidas = sin_quota = carreras = 0
    descartadas: list[str] = []
    for fila in filas:
        if conn.execute(_SQL_LIBERA, (fila.id,)).fetchone() is None:
            carreras += 1
            continue
        liberadas += 1
        motivo = _revalida(conn, aplicador, platform, fila, ahora)
        if motivo is not None:
            conn.execute(_SQL_DESCARTA, (motivo, fila.id, "released"))
            descartadas.append(motivo)
            continue
        if fila.kind == "harvest":
            # ORBIT 04 2.3: harvest_job nace al liberar (sellado 13) y TODA
            # la ejecucion vive en apply_harvest (job → quota → claim →
            # ledger → 2 HTTPs → readback por identidad completa).
            resultado_h = apply_harvest.aplica_harvest(conn, aplicador, fila, platform=platform)
            if resultado_h.estado == "applied":
                aplicadas += 1
            elif resultado_h.estado == "failed":
                fallidas += 1
            elif resultado_h.estado == "perdida":
                carreras += 1
            else:
                sin_quota += 1
            continue
        if not consume_quota(conn, platform, fila.kind):
            sin_quota += 1
            continue  # queda en released: espera FIFO y SIGUE vetable
        if conn.execute(_SQL_CLAIM, (fila.id,)).fetchone() is None:
            carreras += 1
            continue
        if fila.kind == "pause":
            ok = _ejecuta_pause(conn, aplicador, fila)
        else:
            ok = _ejecuta_negative(conn, aplicador, fila)
        if ok == "applied":
            aplicadas += 1
        else:
            fallidas += 1
    return ResultadoLiberacion(
        liberadas=liberadas,
        aplicadas=aplicadas,
        fallidas=fallidas,
        descartadas=descartadas,
        sin_quota=sin_quota,
        carreras_perdidas=carreras,
    )


# ---------------------------------------------------------------------------
# Reversas de cortes (regla 7, sellado 12)
# ---------------------------------------------------------------------------


def reversa_pause(
    conn: psycopg.Connection,
    cliente,
    fila: FilaCola,
    *,
    tick=None,
) -> bool:
    """Reversa del pause: reanudar la keyword/target. Misma secuencia sellada
    (ledger tipo 'reversa', quota_cobrada=false EXENTA), readback y cache con
    lo leido. No toca decision_application ni applied_count; una reversa NO
    limpia el cooldown (sellado 12). True = confirmada por readback."""
    identidad = apply._identidad(conn, fila.ad_entity_id)
    if identidad is None or identidad[0] not in apply._KINDS_DECISORAS:
        return False
    campo = "keywordId" if identidad[0] == "keyword" else "targetId"
    payload = {campo: identidad[1], "state": "enabled"}
    id_attempt = apply._ledger(conn, fila.decision_id, "reversa", payload, quota_cobrada=False)
    if id_attempt is None:
        return False  # tope 3 del ledger: no existe 4o intento
    conn.commit()  # intencion durable PRE-HTTP
    try:
        if identidad[0] == "keyword":
            resp_http = cliente.reanudar_keyword(identidad[1])
        else:
            resp_http = cliente.reanudar_target(identidad[1])
    except apply.AdsApiErrorMutacion as exc:
        apply._sella_ledger(
            conn, id_attempt, ack=None, resultado=f"fallo http {exc.status}: {exc.cuerpo}"
        )
        conn.commit()
        return False
    ack = apply._json_seguro(resp_http)
    if tick is not None:
        tick()
    _, path, contenedor, param = apply._KINDS_DECISORAS[identidad[0]]
    estado = _estado_leido(cliente.get_sellado(path, params={param: identidad[1]}), contenedor)
    resultado = "ok" if estado is not None else "fallo:readback_sin_estado"
    with conn.transaction():
        apply._sella_ledger(conn, id_attempt, ack=ack, resultado=resultado)
        if estado is not None:
            conn.execute(_SQL_CACHE_ESTADO, (estado, fila.ad_entity_id))
    return estado == "enabled"


def reversa_negative(
    conn: psycopg.Connection,
    cliente,
    fila: FilaCola,
    negative_id: str | int,
    *,
    tick=None,
) -> bool:
    """Reversa del negative: DELETE del negativo creado (regla 7). Ledger tipo
    'reversa' EXENTO de quota; el `negative_id` es el que devolvio el ack del
    apply (shape pendiente del probe 2.5) — lo pasa el caller porque la fila
    de la cola no lo congela. True = el DELETE fue aceptado."""
    payload = {"keywordId": str(negative_id)}
    id_attempt = apply._ledger(conn, fila.decision_id, "reversa", payload, quota_cobrada=False)
    if id_attempt is None:
        return False
    conn.commit()  # intencion durable PRE-HTTP
    try:
        resp_http = cliente.borrar_negative(negative_id)
    except apply.AdsApiErrorMutacion as exc:
        apply._sella_ledger(
            conn, id_attempt, ack=None, resultado=f"fallo http {exc.status}: {exc.cuerpo}"
        )
        conn.commit()
        return False
    ack = apply._json_seguro(resp_http)
    if tick is not None:
        tick()
    with conn.transaction():
        apply._sella_ledger(conn, id_attempt, ack=ack, resultado="ok")
    return isinstance(ack, dict)
