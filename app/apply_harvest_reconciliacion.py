"""Revalidacion y reconciliacion del harvest (FABRICA 02, A.3a).

Particion SIN cambio de comportamiento de `app.apply_harvest`: este modulo
concentra la revalidacion PRE-claim y los barridos de reconciliacion de inicio
de ciclo; la ejecucion (nacimiento y avance del job, contexto, bid,
identidad/acks compartidos, reversas, `_paso_*`, `_continua_job`,
`aplica_harvest`) sigue en `app.apply_harvest`.

Direccion de imports en top-level (una sola, sin ciclo):

```text
apply_harvest_reconciliacion -> apply_harvest
```

(En grafo de llamadas hay ida y vuelta por diseno: las envolturas
compatibles de `app.apply_harvest` delegan aqui con import local, ya con el
modulo de ejecucion inicializado. La aciclicidad depende de que ese import
siga siendo local: no subirlo a top-level.)

Los helpers y SQL compartidos por ejecucion y reconciliacion SE QUEDAN en
`app.apply_harvest` y se reutilizan desde ahi via el alias `_ejecucion` (sin
duplicar SQL, parsers de ack, identidad, sellos, quota ni transacciones).
`app.apply_harvest` no importa este modulo en top-level: sus dos funciones
compatibles (`revalida_harvest`, `reconcilia_harvest`) delegan con import
local. Los callers existentes siguen importando SOLO `app.apply_harvest`.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import TYPE_CHECKING

import psycopg

from app import apply
from app import apply_harvest as _ejecucion
from app.ads.client import AdsApiError
from app.optimizer import cortes, harvest_destino, hygiene, windows

if TYPE_CHECKING:
    from app.apply import Aplicador, CapSaturado
    from app.apply_cola import FilaCola

# ---------------------------------------------------------------------------
# SQL exclusivo de revalidacion y reconciliacion (movido de apply_harvest
# en A.3a sin reescribir: los comentarios que lo documentan viajan con el)
# ---------------------------------------------------------------------------

_SQL_JOBS_EN_VUELO = """
SELECT id, decision_id, search_term, ad_entity_id, fase, external_ids, platform::text
  FROM harvest_job
 WHERE platform = %s::platform
   AND fase IN ('pending', 'negative_created', 'exact_created')
 ORDER BY id
"""

# CAMPANA ACTIVA 01 · 1.6: espejo de apply_cola._SQL_DESCARTA (mismo ciclo de
# imports): discard PRE-claim del job cuya campana/grupo de ORIGEN dejo de
# estar ENABLED mientras esperaba quota.
_SQL_DESCARTA = """
UPDATE apply_queue SET estado = 'discarded', discarded_at = now(), discard_motivo = %s
 WHERE id = %s AND estado = 'released'
"""

_SQL_COLA_DE = """
SELECT id, estado FROM apply_queue WHERE decision_id = %s ORDER BY id DESC LIMIT 1
"""

_SQL_NEGATIVAS_APLICANDO = """
SELECT id, ad_entity_id, search_term, decision_id
  FROM apply_queue
 WHERE platform = %s::platform AND estado = 'applying' AND kind = 'negative'
 ORDER BY id
"""

# GK4 (cross-review del dueno): red de seguridad — filas apply_queue applying
# de kind harvest SIN job en vuelo que las conduzca (p.ej. un cierre de job
# que dejo la cola viva). Sin este barrido la clave term_cut quedaria
# bloqueada para siempre (applying eterno, no terminal).
_SQL_HARVEST_APLICANDO = """
SELECT id, decision_id
  FROM apply_queue
 WHERE platform = %s::platform AND estado = 'applying' AND kind = 'harvest'
 ORDER BY id
"""

_SQL_JOB_EN_VUELO_DE = """
SELECT EXISTS (
    SELECT 1 FROM harvest_job
     WHERE decision_id = %s AND fase IN ('pending', 'negative_created', 'exact_created')
)
"""

# ADV-03 (review adversaria, matriz §6.1 fila faltante): pausas applying
# huerfanas (fallo ambiguo 5xx/red en el PUT). Espejos de apply_cola (el
# ciclo de imports apply_cola -> apply_harvest obliga a NO importarlo):
# seleccion, lectura del estado del readback, gracia y cache.
_SQL_PAUSES_APLICANDO = """
SELECT id, ad_entity_id, decision_id
  FROM apply_queue
 WHERE platform = %s::platform AND estado = 'applying' AND kind = 'pause'
 ORDER BY id
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

_SQL_CACHE_ESTADO = """
UPDATE ad_entity_state SET status = %s, synced_at = now() WHERE ad_entity_id = %s
"""

# ADV-05: constantes de firma de la re-decision (mismo trato declarado que
# apply_cola._TARGET/_FLOOR/_CEILING_REVALIDA: JAMAS se persisten, regla 3).
_TARGET_REVALIDA = Decimal("100")
_FLOOR_REVALIDA = Decimal("0.01")
_CEILING_REVALIDA = Decimal("10000")


def revalida_harvest(
    conn: psycopg.Connection, platform: str, fila: FilaCola, ahora: dt.datetime
) -> str | None:
    """Re-validacion PRE-claim del corte HARVEST (ADV-05 de la review
    adversaria; sellado 6: 'jamas se corta por silencio contra la regla').
    El hogar natural de la regla del harvest es este modulo: re-evalua
    decide_hygiene con la ventana FRESCA del termino al reloj de LIBERACION,
    umbral/piso RE-RESUELTOS de la evidencia fresca del grupo y config de
    harvest + keywords de destino resueltas FRESCAS del goal.

    None = sigue calificando (la cadena corre). Motivo = discard: el
    vocabulario es el de apply_cola (vendio_en_ventana / ya_no_califica /
    ...), espejado como string porque apply_cola importa este modulo. El
    target/floor/ceiling de firma son los mismos valores declarados de
    apply_cola (_TARGET_REVALIDA=100 deja el tope ACoS del harvest en el cap
    fijo 35, igual que cualquier target real >= 35): JAMAS se persisten
    (regla 3)."""
    grupo = fila.ad_entity_id
    padre = conn.execute(_ejecucion._SQL_PADRE, (grupo,)).fetchone()
    # FABRICA 02 (A.1): destino RESUELTO (grupo > excepcion > terna vigente
    # > skip) en vez del goal fresco; el dedupe mira el destino resuelto
    # (si no, un termino cosechado se re-propondria cada dia ~90 dias). Sin
    # destino -> el motivo del salto es el discard (PRE-claim, sin cobro).
    # Sin 0018 (fixtures pre-F1) el resolutor revienta con UndefinedTable:
    # savepoint + camino del goal fresco, como antes de F2.
    try:
        with conn.transaction():
            destino = (
                harvest_destino.resolver_destino(conn, platform, padre[0])
                if padre is not None
                else harvest_destino.SaltoHarvest(motivo=hygiene.MOTIVO_SIN_DESTINO_HARVEST)
            )
    except psycopg.errors.UndefinedTable:
        destino = None
    config: hygiene.ConfigHarvest | None = None
    keywords: frozenset[str] = frozenset()
    if destino is None:
        goal = _ejecucion._goal_del_grupo(conn, platform, padre[0]) if padre is not None else None
        if (
            goal is not None
            and goal.harvest_campaign_id is not None
            and goal.harvest_ad_group_id is not None
            and goal.harvest_default_bid is not None
        ):
            config = hygiene.ConfigHarvest(
                campaign_id=goal.harvest_campaign_id,
                ad_group_id=goal.harvest_ad_group_id,
                default_bid=goal.harvest_default_bid,
                moneda=goal.bid_currency,
            )
            keywords = hygiene.keywords_campana_destino(conn, platform, goal.harvest_campaign_id)
    elif isinstance(destino, harvest_destino.SaltoHarvest):
        return destino.motivo
    elif destino.bid is not None and destino.moneda is not None:
        config = hygiene.ConfigHarvest(
            campaign_id=destino.campaign_external,
            ad_group_id=destino.ad_group_external,
            default_bid=destino.bid,
            moneda=destino.moneda,
        )
        keywords = hygiene.keywords_campana_destino(conn, platform, destino.campaign_external)
    evidencia = windows.ventanas_evidencia_ad_group(conn, platform, ahora).get(grupo)
    umbral = cortes.umbral_corte(evidencia, "negative").umbral
    piso = cortes.piso_corte(evidencia, platform).piso_cost
    terminos = windows.terminos_cortes(conn, grupo, ahora)
    term = next((t for t in terminos.terminos if t.search_term == fila.search_term), None)
    if term is None:
        # Sin observaciones frescas del termino en la ventana: ausencia, no
        # ceros inventados (regla 3) — el harvest de evidencia rancia muere.
        return "ya_no_califica"
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
        config_harvest=config,
        keywords_existentes=keywords,
        umbral_negative=umbral,
        piso_negative=piso,
    )
    if resultado.kind == "harvest":
        return None
    # Para el harvest TODO motivo de no-calificacion (ACoS sobre tope, sin
    # banda, config, duplicado) es el mismo discard: la regla fresca dijo no.
    return "ya_no_califica"


def _reconcilia_pauses(conn: psycopg.Connection, aplicador, platform: str) -> tuple[int, int]:
    """Cola applying huerfana kind PAUSE (ADV-03; matriz §6.1): un fallo
    ambiguo (5xx/red) dejo la fila applying con su ledger sin sello y su
    clave entity_cut bloqueada para siempre. LIST FRESCO de estado decide
    (probe 2.5: el GET directo esta retirado; wire UPPER): PAUSED (Amazon SI
    proceso) → confirmar/applied; ENABLED (no proceso, o el dueno re-activo)
    → failed y, con pause propio verificado, INSERT reactivacion_manual
    (gracia 7d, sellado 17). Lectura ilegible/ambigua → se salta al ciclo
    siguiente (la fila sin sello ES el rastro)."""
    confirmadas = fallidas = 0
    cliente = None
    filas = conn.execute(_SQL_PAUSES_APLICANDO, (platform,)).fetchall()
    for q_id, entidad, decision_id in filas:
        identidad = apply._identidad(conn, entidad)
        if identidad is None or identidad[0] not in apply._KINDS_DECISORAS:
            _ejecucion._sella_pendientes(conn, decision_id, "fallo:entidad_sin_identidad")
            _ejecucion._termina_cola(conn, q_id, "failed")
            fallidas += 1
            continue
        if cliente is None:
            cliente = aplicador._cliente()
        _, path, contenedor, param = apply._KINDS_DECISORAS[identidad[0]]
        try:
            # CX1/QW1: el LIST PAGINA — la entidad puede vivir en la pagina
            # 2+ y leer solo la primera la daba por ausente (estado ilegible,
            # fila applying eterna).
            estado = apply._estado_de_readback(cliente, path, contenedor, param, identidad[1])
        except AdsApiError:
            continue  # lectura ambigua: proximo ciclo (la fila ES el rastro)
        if estado == apply.ESTADO_WIRE_PAUSED:
            ack = {"fuente": "list", "state": estado}
            with conn.transaction():
                _ejecucion._sella_pendientes(conn, decision_id, "ok:reconciliado")
                apply._confirma_resumen(conn, decision_id, ack, True, aplicador.cycle_id_ejecutor)
                conn.execute(_SQL_CACHE_ESTADO, (estado, entidad))
                _ejecucion._termina_cola(conn, q_id, "applied")
            confirmadas += 1
            continue
        if estado == apply.ESTADO_WIRE_ENABLED:
            with conn.transaction():
                _ejecucion._sella_pendientes(conn, decision_id, "fallo:reconciliado_enabled")
                if conn.execute(_SQL_PAUSE_PROPIO, (entidad,)).fetchone()[0]:
                    # Pause propio verificado + ENABLED vivo: el dueno
                    # re-activo a mano (sellado 17) — gracia de 7d.
                    conn.execute(_SQL_INSERT_REACTIVACION, (entidad,))
                _ejecucion._termina_cola(conn, q_id, "failed")
            fallidas += 1
        # Otro estado (ARCHIVED/ilegible): ambiguo, proximo ciclo.
    return (confirmadas, fallidas)


# ---------------------------------------------------------------------------
# Reconciliacion al inicio del ciclo (sellado 13; matriz §6.1)
# ---------------------------------------------------------------------------


def _cola_de(conn: psycopg.Connection, decision_id: int) -> tuple[int | None, str | None]:
    fila = conn.execute(_SQL_COLA_DE, (decision_id,)).fetchone()
    return (fila[0], fila[1]) if fila is not None else (None, None)


def _reconcilia_negativas(conn: psycopg.Connection, aplicador, platform: str) -> tuple[int, int]:
    """Cola applying huerfana kind NEGATIVE (matriz §6.1): existe con
    identidad → confirmar; SOLO en otro ad group (señuelo) → failed; no
    existe → reintento (tope 3) o failed. El applying conserva su cobro:
    el reintento NO recobra.

    CAMPANA ACTIVA 01 · 1.6: gate de ancestros PRIMERO (la fila ES el ad
    group, semantica D3): grupo o campaña no ENABLED en el cache → sello
    'fallo:ancestro_no_enabled' + fila failed, sin HTTP ni recobro."""
    confirmadas = fallidas = 0
    cliente = None
    filas = conn.execute(_SQL_NEGATIVAS_APLICANDO, (platform,)).fetchall()
    for q_id, entidad, term, decision_id in filas:
        if apply.gate_ancestros(conn, entidad) is not None:
            with conn.transaction():
                _ejecucion._sella_pendientes(conn, decision_id, apply.RESULTADO_ANCESTRO_NO_ENABLED)
                _ejecucion._termina_cola(conn, q_id, "failed")
            fallidas += 1
            continue
        if cliente is None:
            cliente = aplicador._cliente()
        externos = conn.execute(_ejecucion._SQL_EXTERNALES, (entidad,)).fetchone()
        if externos is None:
            _ejecucion._sella_pendientes(conn, decision_id, "fallo:entidad_sin_externos")
            _ejecucion._termina_cola(conn, q_id, "failed")
            fallidas += 1
            continue
        grupo_ext, campana_ext = externos
        try:
            items = _ejecucion._lista_todos(
                cliente, "/sp/negativeKeywords/list", aplicador._profile_id
            )
        except AdsApiError:
            continue  # lectura ambigua: proximo ciclo
        propio = _ejecucion._identidad(items, grupo_ext, term)
        if propio is not None:
            ack = {"fuente": "list", "keywordId": propio.get("keywordId")}
            with conn.transaction():
                _ejecucion._sella_pendientes(conn, decision_id, "ok:reconciliado")
                apply._confirma_resumen(conn, decision_id, ack, True, aplicador.cycle_id_ejecutor)
                _ejecucion._termina_cola(conn, q_id, "applied")
            confirmadas += 1
            continue
        if _ejecucion._solo_en_otro_ad_group(items, grupo_ext, term):
            _ejecucion._sella_pendientes(conn, decision_id, "fallo:senuelo_otro_ad_group")
            _ejecucion._termina_cola(conn, q_id, "failed")
            fallidas += 1
            continue
        # El tope cuenta SOLO intentos 'normal' (CX1/GK1, apply._SQL_COUNT_INTENTOS):
        # las reversas son el mecanismo de seguridad y jamas consumen
        # presupuesto de intentos (bug PR27-2: el count(*) crudo cerraba en
        # tope_intentos con 2 normales + 1 reversa).
        total = conn.execute(apply._SQL_COUNT_INTENTOS, (decision_id,)).fetchone()[0]
        if total >= apply.TOPE_INTENTOS:
            _ejecucion._sella_pendientes(conn, decision_id, "fallo:tope_intentos")
            _ejecucion._termina_cola(conn, q_id, "failed")
            fallidas += 1
            continue
        payload = {
            # Espejo del wire REAL (probe 2.5, apply_attempt 13): enums UPPER.
            "adGroupId": grupo_ext,
            "campaignId": campana_ext,
            "keywordText": term,
            "matchType": "NEGATIVE_EXACT",
            "state": "ENABLED",
        }
        id_attempt = apply._ledger(conn, decision_id, "normal", payload, quota_cobrada=False)
        if id_attempt is None:
            _ejecucion._termina_cola(conn, q_id, "failed")
            fallidas += 1
            continue
        conn.commit()  # intencion durable PRE-HTTP
        try:
            resp = cliente.crear_negative_exacto(grupo_ext, campana_ext, term)
        except apply.AdsApiErrorMutacion as exc:
            apply._sella_ledger(
                conn, id_attempt, ack=None, resultado=f"fallo http {exc.status}: {exc.cuerpo}"
            )
            conn.commit()
            _ejecucion._termina_cola(conn, q_id, "failed")
            fallidas += 1
            continue
        except AdsApiError:
            continue  # ambiguo: la fila sin sello ES el rastro
        ack = apply._json_seguro(resp)
        errores = _ejecucion._errores_de_ack(ack)
        neg_id = _ejecucion._id_de_ack(ack, "negativeKeywordId")
        if errores or neg_id is None:
            # CX2 de la cross-review: el 207 NO es exito automatico — la fila
            # rechazada viaja en error[] (fallo CON el cuerpo) y un ack sin id
            # no es evidencia del corte (GK2: fail-closed).
            resultado = (
                f"fallo:ack_con_error: {str(errores)[:300]}" if errores else "fallo:ack_sin_id"
            )
            with conn.transaction():
                apply._sella_ledger(conn, id_attempt, ack=ack, resultado=resultado)
                _ejecucion._termina_cola(conn, q_id, "failed")
            fallidas += 1
            continue
        with conn.transaction():
            apply._sella_ledger(conn, id_attempt, ack=ack, resultado="ok")
            _ejecucion._sella_pendientes(conn, decision_id, "ok:reconciliado")
            apply._confirma_resumen(conn, decision_id, ack, True, aplicador.cycle_id_ejecutor)
            _ejecucion._termina_cola(conn, q_id, "applied")
        confirmadas += 1
    return confirmadas, fallidas


def _reconcilia_harvest_huerfanas(conn: psycopg.Connection, platform: str) -> int:
    """Red de seguridad GK4 (cross-review del dueno): filas apply_queue
    applying de kind harvest cuyo job YA no esta en vuelo (failed/done o jamas
    nacio) → failed con nota en el ledger pendiente. Sin este barrido la
    clave term_cut quedaria bloqueada para siempre. La fila CON job en vuelo
    NO se toca: el job la conduce. Devuelve cuantas cerro."""
    cerradas = 0
    for q_id, decision_id in conn.execute(_SQL_HARVEST_APLICANDO, (platform,)).fetchall():
        if conn.execute(_SQL_JOB_EN_VUELO_DE, (decision_id,)).fetchone()[0]:
            continue  # el job en vuelo conduce esta fila
        with conn.transaction():
            _ejecucion._sella_pendientes(conn, decision_id, "fallo:huerfana_sin_job")
            _ejecucion._termina_cola(conn, q_id, "failed")
        cerradas += 1
    return cerradas


def reconcilia_harvest(
    conn: psycopg.Connection, aplicador: Aplicador, platform: str
) -> _ejecucion.ResumenReconciliacion:
    """El barrido de reconciliacion al INICIO del ciclo (2.2/2.4 la invocan),
    contra Amazon VIVO por lista con IDENTIDAD COMPLETA: jobs en vuelo (la
    matriz decide por fase: ya aplicada avanza por EVIDENCIA, falta
    reintenta el POST seguro), la cola applying huerfana de negatives
    normales, la de PAUSES por LIST fresco de estado (ADV-03, matriz §6.1),
    los jobs de filas muertas (vetoed/discarded → failed: la cola manda) y
    la red de seguridad de filas harvest applying sin job vivo (GK4).
    Quota SOLO la primera vez; antes de cualquier HTTP reclama la fila (el
    veto puede ganar el claim). Ambiguo por job → se salta al ciclo siguiente
    (la fila sin sello ES el rastro).

    CAMPANA ACTIVA 01 · 1.6: gate de ancestros del ORIGEN (job.ad_entity_id,
    el ad group de la fila, semantica D3; el destino del goal sigue SIN gate
    de codigo, D4) tras el chequeo de fila muerta y ANTES del cobro/claim/
    HTTP: job → failed, ledger pendiente sellado 'fallo:ancestro_no_enabled',
    fila applying → failed / fila released → discarded PRE-claim (la maquina
    de estados de 0002 no tiene released → failed). Cuenta en jobs_failed SIN
    alerta de Telegram: campana pausada por el dueno es condicion esperada."""
    alertas: list[_ejecucion.AlertaHarvest] = []
    caps_saturados: list[CapSaturado] = []
    jobs_done = jobs_failed = cerrados = 0
    for fila_job in conn.execute(_SQL_JOBS_EN_VUELO, (platform,)).fetchall():
        job = _ejecucion._job_de_fila(fila_job)
        queue_id, queue_estado = _cola_de(conn, job.decision_id)
        if queue_estado in ("vetoed", "discarded"):
            conn.execute(_ejecucion._SQL_JOB_FAILED, (job.id,))
            cerrados += 1
            continue
        motivo_gate = apply.gate_ancestros(conn, job.ad_entity_id)
        if motivo_gate is not None:
            with conn.transaction():
                conn.execute(_ejecucion._SQL_JOB_FAILED, (job.id,))
                _ejecucion._sella_pendientes(
                    conn, job.decision_id, apply.RESULTADO_ANCESTRO_NO_ENABLED
                )
                if queue_id is not None:
                    if queue_estado == "released":
                        conn.execute(_SQL_DESCARTA, (motivo_gate, queue_id))
                    else:
                        _ejecucion._termina_cola(conn, queue_id, "failed")
            jobs_failed += 1
            continue
        if queue_estado == "released":
            usada, saturada = apply.consume_quota_y_sello(conn, platform, "harvest")
            if not usada:
                continue  # sigue esperando quota (y vetable)
            if saturada:
                # Preflight 1.4 (D3a): el re-cobro que llevo used a cap es UN
                # evento por (motor, dia).
                evento = apply.evento_cap_saturado(conn, platform, "harvest")
                if evento is not None:
                    caps_saturados.append(evento)
            if (
                queue_id is None
                or conn.execute(_ejecucion._SQL_CLAIM, (queue_id,)).fetchone() is None
            ):
                continue  # un veto gano la carrera del claim: la cola manda
        try:
            estado, alerta = _ejecucion._continua_job(conn, aplicador, job, queue_id=queue_id)
        except AdsApiError:
            continue
        if estado == "done":
            jobs_done += 1
        elif estado == "failed":
            jobs_failed += 1
            if alerta is not None:
                alertas.append(alerta)
    negativas_confirmadas, negativas_fallidas = _reconcilia_negativas(conn, aplicador, platform)
    pausas_confirmadas, pausas_fallidas = _reconcilia_pauses(conn, aplicador, platform)
    huerfanas = _reconcilia_harvest_huerfanas(conn, platform)
    return _ejecucion.ResumenReconciliacion(
        jobs_done=jobs_done,
        jobs_failed=jobs_failed,
        negativas_confirmadas=negativas_confirmadas,
        negativas_fallidas=negativas_fallidas,
        jobs_cerrados_por_cola=cerrados,
        alertas=tuple(alertas),
        pausas_confirmadas=pausas_confirmadas,
        pausas_fallidas=pausas_fallidas,
        harvest_huerfanas_cerradas=huerfanas,
        caps_saturados=tuple(caps_saturados),
    )
