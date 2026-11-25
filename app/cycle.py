"""Orquestador del ciclo del optimizador Ads (TX1 -> TX2 -> TX3 -> TX4/Apply -> Notifica).

`atender_plataforma` es la operacion de UN horario (ARREGLOS 05 A): toma el
claim, mira si hoy ya hubo decision y elige entre abrir un origen nuevo, abrir
un ejecutor que complete lo pendiente del origen de hoy, o no abrir nada.
`corre_ciclo` corre siempre un origen nuevo (corrida manual y el arnes de
aceptacion). Flujo: claim -> conciliacion previa a TX2 -> snapshot y decision
-> TX3 con su sello -> apply -> cierre.

Mantiene la superficie publica sellada (`corre_ciclo`, `atender_plataforma`,
`reproduce`, `job_key_de`, `motivo_de_salto`, `lee_goals`, `CicloOcupado`,
`ApplyAbortado`, `SinPerfilAplicar`, `ResultadoCiclo`, `ResultadoAtencion`,
`MOTIVO_*`). El recorrido TX2 vive en `app.cycle_recorrido`, la conciliacion
previa y la fase de apply en `app.cycle_apply`.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

import psycopg
from psycopg import pq
from psycopg.conninfo import make_conninfo
from psycopg.errors import LockNotAvailable
from psycopg.types.json import Json

from app import apply, lock, pantalla_dinero, propuestas_campana, proteccion_venta, temporada_io
from app.apply import Aplicador, DuenoDelClaimCambio
from app.cycle_apply import (  # noqa: F401 - re-export estable
    _SQL_APPLIED_COUNT_CICLO,
    _SQL_NOTAS_VIGENTES,
    _SQL_SELLA_APPLY,
    ApplyAbortado,
    SinPerfilAplicar,
    _aplicador_real,
    _fase_apply,
    _fase_reconciliacion,
    _FaseApply,
    _guard_apply,
    _mezcla_evidencias_persistidas,
    _Reconciliacion,
    _sella_apply,
    sella_notas_post_apply,
)
from app.cycle_grupos import (  # noqa: F401 - re-export estable
    _MOTIVOS_AVISO_DESTINO,
    _SQL_GOALS,
    _SQL_GRUPOS,
    _SQL_MEMBRESIA_GRUPO,
    _SQL_SALTOS_PREVIOS,
    MOTIVO_BID_EN_VUELO,
    MOTIVO_CAMPANA_NO_ENABLED,
    MOTIVO_COOLDOWN_7D,
    MOTIVO_ENTIDAD_INERTE,
    MOTIVO_ESCALERA_OFF,
    MOTIVO_ESTADO_NO_ENABLED,
    MOTIVO_GOAL_DISABLED,
    MOTIVO_GOAL_MODE_OFF,
    MOTIVO_GRUPO_NO_ENABLED,
    MOTIVO_PAUSA_PRODUCTO_PENDIENTE,
    MOTIVO_POLITICA_APAGADA,
    MOTIVO_SIN_GOAL,
    MOTIVO_SIN_TARGET,
    MOTIVO_VETO_PENDIENTE,
    _config_harvest_de,
    _Contadores,
    _gates_entidad,
    _harvest_destino_notes,
    _porta_goal_campana,
    _procesa_grupo,
    _registra_destino,
    _saltos_previos,
    lee_goals,
)
from app.cycle_notifica import _fase_notifica
from app.cycle_recorrido import (  # noqa: F401 - re-export estable
    _SQL_CAMPANAS,
    _SQL_DECISORAS,
    _SQL_GRUPOS_PAUSA_PRODUCTO,
    _SQL_INERTES,
    _SQL_TIENE_GRUPOS_FABRICA,
    _fase_lecturas,
    _procesa_decisora,
    _recorre_plataforma,
)
from app.cycle_serializacion import (  # noqa: F401 - re-export estable
    _agregado_json,
    _corte_json,
    _dec_str,
    _fecha_iso,
    _goal_json,
    _notas_cuerpo,
    _notas_json,
    _Pendiente,
    _pendiente_termino,
    _proteccion_activa,
    _registra_proteccion,
    _sello_bitemporal,
    _ts,
    pendiente_bid,
)
from app.cycle_sombra import (  # noqa: F401 - re-export estable
    MOTIVO_PRODUCTO_SIN_EVIDENCIA,
    _anota_sombra,
    _descarta_sin_evidencia,
    _evalua_productos,
    _insumos_pausa,
    _meta_viva,
    _notas_productos,
    _pendientes_pausa_producto,
    _ProductosCiclo,
)
from app.cycle_target import (  # noqa: F401 - re-export estable
    _FamiliasCiclo,
    _lee_familias_ciclo,
    _margen_familia_de_hoja,
    _resuelve_target_ciclo,
    _TargetCiclo,
)
from app.cycle_temporada import (  # noqa: F401 - re-export estable
    _anota_regreso_retenido,
    _compara_temporada,
    _estampa_temporada,
    _frontera_temporada,
    _pausa_sombra,
)
from app.db import tx_durable
from app.lock import (
    SQL_CLAIM as _SQL_CLAIM,
)
from app.lock import (
    SQL_HEARTBEAT as _SQL_HEARTBEAT,
)
from app.lock import (
    SQL_LIBERAR as _SQL_LIBERAR_LOCK,
)
from app.lock import (  # noqa: F401 - re-export estable para tests
    SQL_OWNER as _SQL_OWNER_LOCK,
)
from app.optimizer import bid, cortes, temporada, windows  # noqa: F401 - re-export
from app.optimizer import goals as g
from app.optimizer import proteccion as subproteccion
from app.optimizer.bid import PLATAFORMAS_MONEDA
from app.optimizer.caso import _a_json as _caso_json
from app.optimizer.politica import MOTIVO_REGRESO_ESPERA_PRESUPUESTO, decide
from app.optimizer.replay import reproduce as reproduce
from app.redaction import install_scrub_filter, scrub

logger = logging.getLogger(__name__)
install_scrub_filter(logger)

MOTOR = "ads_optimizer"
_MODO_TOPE_ENVELOPE = "live"


def job_key_de(platform: str) -> str:
    """job_key del lock del ciclo para `platform` (UNA fuente, regla 2)."""
    return f"{MOTOR}:{platform}"


# Origen de hoy: el ultimo ciclo dueno de sus decisiones (`resume_of` NULL) con
# la TX3 comprometida hoy. El dia lo mide el reloj de la base porque el sello
# lleva ese reloj. `pendientes` pregunta a la vista de auditoria si alguna de
# sus decisiones sigue sin desenlace y sin fila en la cola: es lo que un
# ejecutor puede completar. Un ciclo muerto antes de TX3 no tiene sello y no
# cuenta: hoy se puede decidir de nuevo.
_SQL_ORIGEN_HOY = """
SELECT c.id, c.mode, c.status,
       EXISTS (SELECT 1 FROM v_decision_huerfana h
                WHERE h.cycle_id = c.id AND h.origen = 'huerfana') AS pendientes,
       CASE WHEN c.notes IS NOT NULL AND left(ltrim(c.notes), 1) = '{'
            THEN coalesce(c.notes::jsonb #> '{apply,completo}', 'false'::jsonb)
            ELSE 'false'::jsonb END = 'true'::jsonb AS completo
  FROM optimizer_cycle c
 WHERE c.motor = 'ads_optimizer' AND c.platform = %s::platform
   AND c.resume_of IS NULL
   AND (c.decision_committed_at AT TIME ZONE 'UTC')::date = %s::date
 ORDER BY c.id DESC
 LIMIT 1
"""


@dataclass(frozen=True)
class _PlanHoy:
    """Lo que le toca a un horario para una plataforma. Nunca los dos a la vez:
    `origen` es el ciclo de hoy con decisiones live sin completar o con fases
    sin terminar (el horario abre un ejecutor ligado a el); `motivo` es la
    razon por la que hoy no se abre nada."""

    origen: int | None = None
    motivo: str | None = None


# El ejecutor que termina sin fallo marca el origen: sus fases ya corrieron.
# `apply` puede no existir en las notas del origen (murio antes de la fase),
# asi que el objeto se arma completo en vez de jsonb_set sobre un camino corto.
_SQL_MARCA_APLICADO = """
UPDATE optimizer_cycle
   SET notes = jsonb_set(
           coalesce(notes::jsonb, '{}'::jsonb), '{apply}',
           coalesce(notes::jsonb #> '{apply}', '{}'::jsonb) || '{"completo": true}'::jsonb
       )::text
 WHERE id = %s
"""


def _plan_de_hoy(
    conn: psycopg.Connection, platform: str, *, hoy: dt.date | None = None
) -> _PlanHoy:
    """El plan del dia para `platform` (ARREGLOS 05 A.2).

    Manda el claim, no la edad de 3 h: un `running` huerfano no bloquea nada
    porque su lock vence y el siguiente horario lo reclama. Un ciclo cerrado
    hoy sin decisiones pendientes significa que ya se decidio. Un ciclo cuyo
    apply no termino deja decisiones sin desenlace o fases incompletas, y eso
    (no su estado) es lo que abre un ejecutor en el horario siguiente.

    `hoy` es el dia (UTC) del reloj de la corrida. Sin el, el de la base: es
    el que usa el cron y el de las consultas sueltas."""
    dia = (
        hoy
        if hoy is not None
        else conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
    )
    fila = conn.execute(_SQL_ORIGEN_HOY, (platform, dia)).fetchone()
    if fila is None:
        return _PlanHoy()
    id_origen, mode, status, pendientes, completo = fila
    if mode == "live" and (pendientes or not completo):
        return _PlanHoy(origen=id_origen)
    return _PlanHoy(
        motivo=f"cycle {platform}: ya hay un ciclo {status} hoy; espera al siguiente horario"
    )


def motivo_de_salto(
    conn: psycopg.Connection, platform: str, *, hoy: dt.date | None = None
) -> str | None:
    """Razon por la que hoy NO se abre nada para `platform`, o None.

    None tambien cuando queda trabajo por completar: entonces lo que se abre
    es un ejecutor, y eso lo decide `atender_plataforma` bajo el claim."""
    return _plan_de_hoy(conn, platform, hoy=hoy).motivo


def _dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


class CicloOcupado(Exception):
    """El claim del lock se perdio: hay un ciclo vigente de OTRO owner."""


@dataclass(frozen=True)
class ResultadoCiclo:
    """Resumen del ciclo corrido (done | degraded | skipped). `resume_of` es el
    ciclo de origen cuando este envelope es un ejecutor de recuperacion."""

    cycle_id: int
    status: str
    decisions_count: int
    notes: str
    resume_of: int | None = None


@dataclass(frozen=True)
class ResultadoAtencion:
    """Lo que hizo `atender_plataforma` en un horario: el ciclo que corrio, o
    la razon por la que no abrio ninguno. Uno de los dos es None."""

    ciclo: ResultadoCiclo | None
    motivo: str | None


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
INSERT INTO optimizer_cycle (motor, mode, platform, resume_of)
VALUES ('ads_optimizer', %s, %s::platform, %s)
RETURNING id
"""

# Cierre de la TX3: la MISMA sentencia cierra el envelope y sella que sus
# decisiones quedaron comprometidas (ARREGLOS 05 A.2), aunque sean cero. El
# sello lleva el RELOJ DE LA DECISION de esa corrida, acotado al ahora de la
# base: es el mismo reloj que fecha las decisiones y el que rige su vigencia.
# Con la hora de la base, un ciclo corrido con `--decided-at` de ayer pero
# terminado hoy pasaria por la decision de hoy y suprimiria el origen de hoy.
# Si el envelope ya no es `running`, otro owner lo reclamo y dejo el rastro:
# ninguna fila vuelve y la transaccion entera se deshace, decisiones incluidas.
_SQL_CERRAR_TX3 = """
UPDATE optimizer_cycle
   SET status = %s, finished_at = now(), decisions_count = %s, notes = %s,
       decision_committed_at = least(%s::timestamptz, now())
 WHERE id = %s AND status = 'running'
RETURNING id
"""

# Cierre sin sello: la escalera off, o el cierre final de un ejecutor (que no
# decide, asi que no compromete decisiones).
_SQL_CERRAR_ENVELOPE = """
UPDATE optimizer_cycle
   SET status = %s, finished_at = now(), decisions_count = %s, notes = %s
 WHERE id = %s AND status = 'running'
"""

_SQL_SELLAR_FALLIDO = """
UPDATE optimizer_cycle
   SET status = 'failed', finished_at = now(), notes = %s
 WHERE id = %s AND status = 'running'
"""

_SQL_CONFIG_RECIENTE = "SELECT id, settings FROM config_version ORDER BY id DESC LIMIT 1"

_SQL_INSERT_DECISION = """
INSERT INTO decision (cycle_id, ad_entity_id, kind, decided_at, config_version_id,
                      data_observed_at, window_start, window_end, search_term,
                      old_value, new_value, value_currency, inputs,
                      evaluacion_producto_id)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

_SQL_INSERT_TARGET_CICLO = """
INSERT INTO target_acos_ciclo (cycle_id, ad_entity_id, decided_at,
                               target_acos_pct, procedencia)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (cycle_id, ad_entity_id) DO NOTHING
"""


def _abre_heartbeat(conn: psycopg.Connection) -> psycopg.Connection | None:
    """Conexion autocommit corta para latidos durante TX2 (fail-open)."""
    try:
        dsn = conn.info.dsn
        if conn.info.password:
            dsn = make_conninfo(dsn, password=conn.info.password)
        latido = psycopg.connect(dsn, autocommit=True, connect_timeout=5)
        # El latido nunca espera un cerrojo del claim para siempre: si la fila
        # esta retenida falla rapido y el TTL sigue siendo el respaldo.
        lock.acota_espera_de_cerrojos(latido, lock.TIEMPO_MAXIMO_LATIDO, transaccional=False)
        return latido
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
            logger.warning("heartbeat fallo (TTL es el backstop): %s", scrub(str(exc)))

    return tick


def _config_reciente(conn: psycopg.Connection) -> tuple[int | None, dict]:
    fila = conn.execute(_SQL_CONFIG_RECIENTE).fetchone()
    if fila is None:
        return (None, {})
    return (fila[0], fila[1])


def _toma_claim(conn: psycopg.Connection, job_key: str, owner: str) -> bool:
    return conn.execute(_SQL_CLAIM, (job_key, owner)).fetchone() is not None


def _abre_envelope(
    conn: psycopg.Connection, modo: str, platform: str, *, resume_of: int | None = None
) -> int:
    """Abre el envelope de esta corrida: origen nuevo, o ejecutor de `resume_of`."""
    return conn.execute(_SQL_ABRIR_ENVELOPE, (modo, platform, resume_of)).fetchone()[0]


# Dentro de la TX3, antes de sellar: la fila del claim BLOQUEADA. El cierre se
# serializa con la toma del claim del sucesor (ARREGLOS 05 A, revision): quien
# claima actualiza esa misma fila y espera a que la TX3 comprometa, asi que su
# plan ya ve el sello; y una TX3 cuyo claim cambio de dueno no se compromete.
_SQL_CLAIM_EN_TX3 = "SELECT owner FROM ads_optimizer_lock WHERE job_key = %s FOR UPDATE"


def _compromete_tx3(
    conn: psycopg.Connection,
    cycle_id: int,
    status: str,
    decisions_count: int,
    notas: str,
    *,
    decided_at: dt.datetime,
    job_key: str,
    owner: str,
) -> None:
    """Cierra el envelope en la TX3 que inserto sus decisiones y lo sella con
    el reloj de la decision de la corrida.

    Corre DENTRO de esa transaccion. Antes de sellar comprueba y bloquea la
    fila del claim: si ya no nombra a este owner (un sucesor lo tomo al vencer
    el TTL, o el claim se solto) levanta `CicloOcupado`. Lo mismo si el
    envelope ya no es `running` (el sucesor lo marco con el rastro). En ambos
    casos la transaccion se deshace y ese proceso no deja decisiones
    comprometidas: nunca dos origenes el mismo dia."""
    lock.acota_espera_de_cerrojos(conn)
    try:
        dueno = conn.execute(_SQL_CLAIM_EN_TX3, (job_key,)).fetchone()
    except LockNotAvailable as exc:
        raise CicloOcupado(
            f"claim {job_key} retenido por otra transaccion:"
            f" su TX3 del ciclo {cycle_id} no se compromete"
        ) from exc
    if dueno is None or dueno[0] != owner:
        raise CicloOcupado(
            f"el claim {job_key} ya no es de este owner ({owner}):"
            f" su TX3 del ciclo {cycle_id} no se compromete"
        )
    fila = conn.execute(
        _SQL_CERRAR_TX3, (status, decisions_count, notas, decided_at, cycle_id)
    ).fetchone()
    if fila is None:
        raise CicloOcupado(
            f"el ciclo {cycle_id} ya no esta running (otro owner lo reclamo):"
            " su TX3 no se compromete"
        )


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
) -> list[int]:
    """Inserta las decisiones y devuelve sus ids en el mismo orden de `pendientes`."""
    if not pendientes:
        return []
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
    return [
        fila[0]
        for fila in conn.execute(
            "SELECT id FROM decision WHERE cycle_id = %s ORDER BY id", (cycle_id,)
        ).fetchall()
    ]


def _inserta_targets(
    conn: psycopg.Connection, cycle_id: int, decided_at: dt.datetime, contadores: _Contadores
) -> None:
    if not contadores.targets:
        return
    filas = [
        (cycle_id, entidad, decided_at, target, procedencia)
        for entidad, (target, procedencia) in sorted(contadores.targets.items())
    ]
    conn.cursor().executemany(_SQL_INSERT_TARGET_CICLO, filas)


_SQL_TEMPORADA_CICLO = """
SELECT count(*) FILTER (
           WHERE d.inputs->>'motivo' = 'entra_temporada_baja'
             AND EXISTS (SELECT 1 FROM decision_application da
                          WHERE da.decision_id = d.id AND da.verify_ok IS TRUE)
       ) AS entradas,
       count(*) FILTER (
           WHERE d.inputs->>'motivo' = 'sale_temporada_baja'
             AND EXISTS (SELECT 1 FROM decision_application da
                          WHERE da.decision_id = d.id AND da.verify_ok IS TRUE)
       ) AS regresos,
       count(*) FILTER (
           WHERE NOT EXISTS (SELECT 1 FROM decision_application da
                              WHERE da.decision_id = d.id)
             AND NOT EXISTS (SELECT 1 FROM decision_sin_aplicar sa
                              WHERE sa.decision_id = d.id)
       ) AS pendientes
  FROM decision d
 WHERE d.cycle_id = %s AND d.kind = 'bid'
   AND d.inputs->>'motivo' IN ('entra_temporada_baja', 'ajusta_temporada_baja',
                               'sale_temporada_baja')
"""


_SQL_EXCLUSIONES_APLICADAS = """
SELECT DISTINCT x.ad_entity_id
  FROM temporada_exclusion x
  JOIN decision d ON d.id = x.decision_id
 WHERE d.cycle_id = %s
   AND d.inputs->>'motivo' = 'entra_temporada_baja'
   AND x.motivo = 'mano_del_dueno'
"""


def pendiente_reactivacion(
    r: temporada_io.Reactivacion, estacion: temporada.Estacion, *, decidido_el: dt.datetime
) -> _Pendiente:
    """Decision kind 'resume' (sin dinero): reactiva la pausa que Orbit hizo en
    una baja ya cerrada. `inputs` lleva la pausa, su fila de cola y el episodio
    que la origino, con la estacion congelada para el replay."""
    return _Pendiente(
        ad_entity_id=r.ad_entity_id,
        kind="resume",
        data_observed_at=decidido_el,
        window_start=decidido_el.date(),
        window_end=decidido_el.date(),
        search_term=None,
        old_value=None,
        new_value=None,
        value_currency=None,
        inputs={
            "motor": "temporada",
            "version": "temporada_v1",
            "pausa_decision_id": r.pausa_decision_id,
            "queue_id": r.queue_id,
            "temporada_episodio_id": r.episodio_id,
            "estacion": _caso_json(estacion),
        },
    )


def _notas_temporada(contadores: _Contadores) -> dict | None:
    """Seccion notes.temporada: reactivaciones pendientes y la comparacion de
    la practica (B/D). None sin temporada encendida ni practica que comparar
    (la subida rapida tambien se compara con la temporada apagada)."""
    plat_t = contadores.temporada_plat
    comparadas = contadores.temporada_sombra_comparadas
    if plat_t is None and not comparadas:
        return None
    notas: dict = {}
    if plat_t is not None:
        notas["reactivaciones"] = len(contadores.reactivaciones)
    if comparadas:
        notas.update(
            {
                "comparadas": contadores.temporada_sombra_comparadas,
                "coinciden": contadores.temporada_sombra_coinciden,
                "diferencias": contadores.temporada_sombra_diferencias,
            }
        )
    return notas


def _conteo_temporada(
    conn: psycopg.Connection,
    cycle_id: int,
    *,
    reactivaciones: int,
    retenidos: int,
    excluidas: dict[int, str],
) -> temporada.ConteoTemporada:
    """Lo que conto el ciclo para el aviso del dia: confirmaciones del ciclo,
    pendientes, exclusiones reales y reactivaciones confirmadas.

    Las exclusiones se juntan de dos fuentes y cada hoja cuenta UNA vez: las
    que vio el recorrido (mano registrada, bid heredado, sin centavo) y las que
    descubrio la lectura fresca del aplicador (mano del dueno en Amazon), que
    se registran despues del recorrido."""
    entradas, regresos, pendientes = conn.execute(_SQL_TEMPORADA_CICLO, (cycle_id,)).fetchone()
    aplicadas = {f[0] for f in conn.execute(_SQL_EXCLUSIONES_APLICADAS, (cycle_id,)).fetchall()}
    hojas = dict(excluidas)
    for hoja in aplicadas:
        hojas.setdefault(hoja, "dueno_decidio")
    conteo = Counter(hojas.values())
    return temporada.ConteoTemporada(
        entradas_confirmadas=int(entradas or 0),
        regresos_confirmados=int(regresos or 0),
        pendientes=int(pendientes or 0),
        retenidos_por_presupuesto=retenidos,
        excluidas={motivo: cantidad for motivo, cantidad in conteo.items() if cantidad},
        reactivaciones=reactivaciones,
    )


def _cierra_envelope(
    conn: psycopg.Connection, cycle_id: int, status: str, decisions_count: int, notes: str
) -> bool:
    """Cierra el envelope SOLO si sigue 'running' (guard 2.4)."""
    filas = conn.execute(_SQL_CERRAR_ENVELOPE, (status, decisions_count, notes, cycle_id)).rowcount
    if filas == 0:
        logger.warning(
            "cierre del envelope %s ignorado: ya no esta 'running' (rastro de un "
            "sucesor o cierre previo) — no se pisa",
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
    """Sello best-effort en 'failed' + error scrubbado sin sobre-cerrar."""
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
    """Liberacion por OWNER (best-effort; TTL es el backstop)."""
    try:
        if hb is not None:
            hb.execute(_SQL_LIBERAR_LOCK, (job_key, owner))
        else:
            with conn.transaction():
                conn.execute(_SQL_LIBERAR_LOCK, (job_key, owner))
    except psycopg.Error as exc:
        logger.warning("liberacion del lock fallo (TTL es el backstop): %s", scrub(str(exc)))


def _corre_fases(
    conn: psycopg.Connection,
    *,
    cycle_id: int,
    origen: int | None,
    config_id: int | None,
    settings: dict,
    modo: g.ModoEfectivo,
    platform: str,
    decided_at: dt.datetime,
    ciclos_muertos: list[int],
    contadores: _Contadores,
    job_key: str,
    owner: str,
    reconciliacion,
    tick,
    guard,
) -> ResultadoCiclo:
    """Conduce un envelope ya abierto bajo el claim y con la conciliacion previa
    ya ejecutada: decide (si es un origen), aplica por fases y cierra.

    `origen` None: este envelope es dueno de sus decisiones. Si no: es un
    ejecutor de recuperacion de ese ciclo; no vuelve a tomar el snapshot ni
    inserta decisiones (`decisions_count` 0) y aplica lo pendiente del origen.
    """
    if origen is None and modo.modo == "off":
        notas = _notas_json(
            contadores, ciclos_muertos, modo.nota, MOTIVO_ESCALERA_OFF, "escalera global off"
        )
        with conn.transaction():
            _cierra_envelope(conn, cycle_id, "skipped", 0, notas)
        return ResultadoCiclo(cycle_id, "skipped", 0, notas)
    apply.expira_no_intentadas(
        conn, platform, hoy=decided_at.astimezone(dt.UTC).date(), cycle_id_ejecutor=cycle_id
    )
    pendientes: list[_Pendiente] = []
    target_ciclo = None
    avisos_destino: tuple = ()
    if origen is None:
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
            decisor=decide,
        )
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
            harvest_destino=(
                _harvest_destino_notes(contadores) if target_ciclo is not None else None
            ),
            temporada=_notas_temporada(contadores),
        )
        status = "degraded" if guarda is not None else "done"
        with conn.transaction():
            plat_t = contadores.temporada_plat
            ep_abierto = None
            temporada_live = plat_t is not None and plat_t.en_vivo and modo.modo == "live"
            if temporada_live:
                ep = temporada_io.registra_ciclo(
                    conn,
                    platform,
                    cycle_id=cycle_id,
                    decidido_el=decided_at,
                    plataforma=plat_t,
                    config_version_id=config_id,
                )
                ep_abierto = ep.id if ep is not None else None
                temporada_io.registra_fines(
                    conn,
                    platform,
                    cycle_id=cycle_id,
                    episodio_abierto=ep_abierto,
                    fines=contadores.fines,
                    exclusiones=contadores.exclusiones,
                    moneda=PLATAFORMAS_MONEDA[platform],
                )
                # TEMPORADA 01 F: una decision resume por pausa de la baja que
                # toca reactivar. Se lee tras registra_ciclo para capturar el
                # episodio cerrado hoy mismo. El efecto de temporada no aplica.
                contadores.reactivaciones = temporada_io.reactivaciones_pendientes(
                    conn, platform, plat_t.config.mantener_pausadas
                )
                if cuerpo.get("temporada") is not None:
                    cuerpo["temporada"]["reactivaciones"] = len(contadores.reactivaciones)
                pendientes.extend(
                    pendiente_reactivacion(r, plat_t.estacion, decidido_el=decided_at)
                    for r in contadores.reactivaciones
                )
                pendientes = [temporada_io.estampa(p, ep_abierto) for p in pendientes]
            ids_evaluacion: dict[int, int] = {}
            if contadores.productos is not None:
                cuerpo["productos"], ids_evaluacion = _notas_productos(
                    conn, cycle_id, config_id, contadores.productos
                )
                _descarta_sin_evidencia(contadores, pendientes, ids_evaluacion, cuerpo)
            ids_decisiones = _inserta_decisiones(
                conn, cycle_id, config_id, decided_at, pendientes, ids_evaluacion
            )
            if temporada_live:
                temporada_io.abre_efectos(conn, ep_abierto, pendientes, ids_decisiones)
            _inserta_targets(conn, cycle_id, decided_at, contadores)
            proteccion_venta.inserta_transiciones(conn, contadores.protecciones)
            if target_ciclo is not None and g.propuestas_campana_desde_settings(settings):
                cuerpo["propuestas_campana"] = propuestas_campana.guarda_evaluaciones(
                    conn, contadores.propuestas_campana, decided_at
                )
            notas = json.dumps(cuerpo, ensure_ascii=False, default=str)
            _compromete_tx3(
                conn,
                cycle_id,
                status,
                len(pendientes),
                notas,
                decided_at=decided_at,
                job_key=job_key,
                owner=owner,
            )
    else:
        cuerpo = {"resume_of": origen}
        status = "done"

    fase = _fase_apply(
        conn,
        cycle_id=cycle_id,
        origen=origen,
        modo=modo,
        platform=platform,
        decided_at=decided_at,
        job_key=job_key,
        owner=owner,
        guard=guard,
        reconciliacion=reconciliacion,
        proteccion_activa=subproteccion.activa_desde_settings(settings),
        avisos_destino=avisos_destino,
    )
    notas_apply = fase.notas
    if notas_apply:
        cuerpo["apply"] = notas_apply
        if fase.fallo:
            status = "degraded"
    # Una reactivacion cuya lectura no trajo el estado dejo su intento abierto:
    # falta leerla, asi que el origen no esta completo (la vista de huerfanas
    # no la ve y sin esta marca nadie volveria a hacerlo ese dia).
    pendientes_de_lectura = bool((fase.reactivaciones or {}).get("pendientes_de_lectura"))
    if not fase.fallo and not pendientes_de_lectura:
        # Progreso durable de las fases (ARREGLOS 05 A): `apply.completo` en el
        # ORIGEN dice que encolado, pujas, cortes y reactivaciones corrieron.
        # Sin esa marca el plan del dia abre un ejecutor aunque ninguna
        # decision este huerfana: la vista de auditoria no ve reactivaciones
        # ni cortes en cola.
        notas_apply["completo"] = True
        if origen is not None:
            with tx_durable(conn):
                conn.execute(_SQL_MARCA_APLICADO, (origen,))
    aviso_temporada = _aviso_temporada_del_dia(
        conn,
        origen=origen,
        contadores=contadores,
        cuerpo=cuerpo,
        notas_apply=notas_apply,
        cycle_id=cycle_id,
        platform=platform,
        settings=settings,
        modo=modo,
    )
    nota_propuesta = _avisa_propuestas_pendientes(conn, settings, tick)
    telegram = _fase_notifica(
        fase.avisos,
        fase.alertas,
        caps_saturados=fase.caps_saturados,
        avisos_destino=fase.avisos_destino,
        aviso_temporada=aviso_temporada,
        cycle_id=cycle_id,
        platform=platform,
        modo=modo.modo,
        status=status,
        decisions_count=len(pendientes),
        notas_apply=notas_apply,
        skips=cuerpo.get("skips"),
        target=cuerpo.get("target"),
        target_ancla=target_ciclo.ancla if target_ciclo is not None else None,
        tick=tick,
        resume_of=origen,
    )
    ancla_avanzo = (
        target_ciclo is not None
        and isinstance(cuerpo.get("target"), dict)
        and cuerpo["target"].get("ultimo_avisado") != target_ciclo.ancla
    )
    if nota_propuesta is not None:
        telegram["aviso_propuesta"] = nota_propuesta
    if origen is not None:
        # El ejecutor se cierra una sola vez, al terminar, con lo que hizo.
        if telegram:
            cuerpo["telegram"] = telegram
        notas = json.dumps(cuerpo, ensure_ascii=False, default=str)
        with tx_durable(conn):
            _cierra_envelope(conn, cycle_id, status, 0, notas)
        return ResultadoCiclo(cycle_id, status, 0, notas, resume_of=origen)
    if notas_apply or telegram or ancla_avanzo:
        if telegram:
            cuerpo["telegram"] = telegram
        notas = sella_notas_post_apply(conn, cycle_id, cuerpo, degradar=fase.fallo)
    return ResultadoCiclo(cycle_id, status, len(pendientes), notas)


def _avisa_propuestas_pendientes(conn: psycopg.Connection, settings: dict, tick) -> str | None:
    """Envia los avisos de propuestas de campana pendientes y devuelve la nota
    de lo que fallo (None si salieron o no habia nada que enviar)."""
    if not g.propuestas_campana_desde_settings(settings):
        return None
    try:
        resultado_avisos = propuestas_campana.envia_avisos_pendientes(conn)
    except Exception as exc:
        logger.warning("notifica: fallo enviando avisos de propuesta: %s", scrub(str(exc)))
        return "fallo: avisos de propuesta no enviados (ver log)"
    nota = None
    if resultado_avisos["fallos"]:
        nota = (
            "fallo: aviso de propuesta nueva no enviado por Telegram "
            f"(propuestas {resultado_avisos['fallos']}: quedan pending "
            "y reintentan en el ciclo siguiente)"
        )
    for _ in range(len(resultado_avisos["enviados"]) + len(resultado_avisos["fallos"])):
        tick()
    return nota


def _aviso_temporada_del_dia(
    conn: psycopg.Connection,
    *,
    origen: int | None,
    contadores: _Contadores,
    cuerpo: dict,
    notas_apply: dict,
    cycle_id: int,
    platform: str,
    settings: dict,
    modo: g.ModoEfectivo,
) -> str | None:
    """El aviso del dia de temporada, con lo que el ciclo confirmo. Es del ciclo
    que decidio: un ejecutor (origen distinto de None) no lo repite."""
    if origen is not None or modo.modo != "live" or cuerpo.get("temporada") is None:
        return None
    plat_t = contadores.temporada_plat
    if plat_t is None or not plat_t.en_vivo:
        return None
    reactivaciones = (
        int(notas_apply["reactivaciones"]["confirmadas"])
        if notas_apply and notas_apply.get("reactivaciones")
        else 0
    )
    cuerpo["temporada"]["reactivaciones"] = reactivaciones
    retenidos = int(
        cuerpo.get("skips", {}).get("entidad", {}).get(MOTIVO_REGRESO_ESPERA_PRESUPUESTO, 0)
    )
    ayer = plat_t.estacion.dia - dt.timedelta(days=1)
    propuestas_t = sum(
        1
        for fila in pantalla_dinero.lee_campanas(
            conn,
            plataforma=platform,
            desde=ayer - dt.timedelta(days=pantalla_dinero.DIAS_UBICACION - 1),
            hasta=ayer,
            settings=settings,
        )
        if fila.propuesta_temporada is not None
    )
    aviso = temporada.aviso_del_dia(
        plat_t.config,
        plat_t.estacion,
        conteo=_conteo_temporada(
            conn,
            cycle_id,
            reactivaciones=reactivaciones,
            retenidos=retenidos,
            excluidas=contadores.excluidas_temporada,
        ),
        propuestas=propuestas_t,
    )
    if aviso:
        cuerpo["temporada"]["aviso"] = aviso
    return aviso


def _validar_entrada(platform: str, decided_at: dt.datetime, heartbeat_cada: int) -> None:
    if decided_at.tzinfo is None:
        raise ValueError("decided_at debe ser tz-aware (UTC): un naive evaluaria segun la TZ local")
    if platform not in PLATAFORMAS_MONEDA:
        raise ValueError(f"plataforma fuera del vocabulario sellado: {platform!r}")
    if heartbeat_cada <= 0:
        raise ValueError("heartbeat_cada debe ser > 0")


def _atiende(
    conn: psycopg.Connection,
    *,
    platform: str,
    owner: str,
    decided_at: dt.datetime,
    heartbeat_cada: int,
    aplicador_factory: Callable[..., Aplicador] | None,
    plan_del_dia: bool,
) -> ResultadoAtencion:
    """Un envelope bajo un claim unico (ARREGLOS 05 A.2). Con `plan_del_dia` el
    plan se lee YA bajo el claim: nadie mas puede abrir un ciclo de la
    plataforma mientras se decide que abrir."""
    _validar_entrada(platform, decided_at, heartbeat_cada)
    job_key = job_key_de(platform)
    conn.commit()
    hb = _abre_heartbeat(conn)
    ciclo_id: int | None = None
    tomado = False
    contadores = _Contadores()
    muertos: list[int] = []
    try:
        with conn.transaction():
            # Tope de espera: un claim retenido por otra transaccion no cuelga el
            # horario; a los 5 s esta corrida se trata como ocupada.
            lock.acota_espera_de_cerrojos(conn)
            config_id, settings = _config_reciente(conn)
            modo = g.resuelve_modo(g.modo_desde_settings(settings), _MODO_TOPE_ENVELOPE)
            try:
                tomado = _toma_claim(conn, job_key, owner)
            except LockNotAvailable as exc:
                raise CicloOcupado(f"claim {job_key} retenido por otra transaccion: {exc}") from exc
            if not tomado:
                raise CicloOcupado(f"lock vigente de otro owner para {job_key}")
            plan = (
                _plan_de_hoy(conn, platform, hoy=decided_at.astimezone(dt.UTC).date())
                if plan_del_dia
                else _PlanHoy()
            )
            origen, motivo = plan.origen, plan.motivo
            if origen is not None and modo.modo != "live":
                motivo = (
                    f"cycle {platform}: quedan decisiones del ciclo {origen} por completar;"
                    f" la escalera esta en {modo.modo} y no aplica nada"
                )
            if motivo is None:
                ciclo_id = _abre_envelope(conn, modo.modo, platform, resume_of=origen)
                muertos = _cierra_rastro(conn, owner, platform, ciclo_id)
        # Primer paso de TODO horario bajo el claim (antes de saltar o cerrar en off):
        # en live concilia todo; sin ciclo o en shadow/off solo lee reversas abiertas.
        tick = _tick_heartbeat(hb, job_key, owner, heartbeat_cada)
        guard = _guard_apply(conn, job_key, owner, tick, hb=hb)
        modo_rec = modo if ciclo_id is not None else g.ModoEfectivo(modo="shadow", nota=modo.nota)
        try:
            reconciliacion = _fase_reconciliacion(
                conn,
                modo=modo_rec,
                platform=platform,
                cycle_id_ejecutor=ciclo_id or 0,
                job_key=job_key,
                owner=owner,
                guard=guard,
                aplicador_factory=aplicador_factory,
                origen=origen,
            )
        except (ApplyAbortado, DuenoDelClaimCambio) as exc:
            raise CicloOcupado(f"claim perdido antes de decidir: {exc}") from exc
        if ciclo_id is None:
            return ResultadoAtencion(None, motivo)
        resultado = _corre_fases(
            conn,
            cycle_id=ciclo_id,
            origen=origen,
            config_id=config_id,
            settings=settings,
            modo=modo,
            platform=platform,
            decided_at=decided_at,
            ciclos_muertos=muertos,
            contadores=contadores,
            job_key=job_key,
            owner=owner,
            reconciliacion=reconciliacion,
            tick=tick,
            guard=guard,
        )
        return ResultadoAtencion(resultado, None)
    except BaseException as exc:
        if ciclo_id is not None:
            _sello_fallido(conn, ciclo_id, exc, contadores, muertos)
        raise
    finally:
        if tomado:
            _libera_lock(hb, conn, job_key, owner)
        if hb is not None:
            hb.close()


def atender_plataforma(
    conn: psycopg.Connection,
    plataforma: str,
    *,
    owner: str,
    ahora: dt.datetime,
    heartbeat_cada: int = 25,
    aplicador_factory: Callable[..., Aplicador] | None = None,
    forzar: bool = False,
) -> ResultadoAtencion:
    """Atende UN horario del optimizador para `plataforma` (ARREGLOS 05 A).

    El caller no coordina pasos: la operacion toma el claim y decide que toca.

    - Sin decision comprometida hoy: abre un origen y lo corre entero.
    - Decision comprometida hoy con decisiones live sin completar (apply que
      no termino, proceso muerto despues de TX3): abre un ejecutor ligado a ese
      origen. No vuelve a decidir ni renueva el cupo; completa lo pendiente.
    - Decision comprometida hoy y nada pendiente: no abre nada y devuelve la
      razon. Un `running` huerfano no cuenta como decision: manda el claim.
    - La escalera en off o shadow no abre ejecutores porque no habria
      mutaciones que hacer.

    `forzar` salta el plan y abre un origen nuevo (corrida manual): es la
    unica forma de decidir dos veces el mismo dia. `ahora` es el reloj de la
    decision (tz-aware). El resultado conserva el ciclo de origen y el
    ejecutor de esta invocacion.
    """
    return _atiende(
        conn,
        platform=plataforma,
        owner=owner,
        decided_at=ahora,
        heartbeat_cada=heartbeat_cada,
        aplicador_factory=aplicador_factory,
        plan_del_dia=not forzar,
    )


def corre_ciclo(
    conn: psycopg.Connection,
    *,
    platform: str,
    owner: str,
    decided_at: dt.datetime,
    heartbeat_cada: int = 25,
    aplicador_factory: Callable[..., Aplicador] | None = None,
) -> ResultadoCiclo:
    """Corre UN ciclo del optimizador para `platform` (amazon_us|amazon_mx),
    siempre como origen nuevo y sin consultar el plan del dia."""
    atencion = _atiende(
        conn,
        platform=platform,
        owner=owner,
        decided_at=decided_at,
        heartbeat_cada=heartbeat_cada,
        aplicador_factory=aplicador_factory,
        plan_del_dia=False,
    )
    assert atencion.ciclo is not None, "sin plan del dia siempre se abre un origen"
    return atencion.ciclo
