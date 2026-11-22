"""Orquestador del ciclo del optimizador Ads (TX1 -> TX2 -> TX3 -> TX4/Apply -> Notifica).

Mantiene la superficie publica sellada (`corre_ciclo`, `reproduce`, `job_key_de`,
`motivo_de_salto`, `lee_goals`, `CicloOcupado`, `ApplyAbortado`,
`SinPerfilAplicar`, `ResultadoCiclo`, `MOTIVO_*`). El recorrido TX2 vive en
`app.cycle_recorrido` y la fase de apply TX4+ en `app.cycle_apply`.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass

import psycopg
from psycopg import pq
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Json

from app import propuestas_campana, proteccion_venta, temporada_io
from app.apply import Aplicador
from app.cycle_apply import (  # noqa: F401 - re-export estable
    _SQL_APPLIED_COUNT_CICLO,
    _SQL_NOTAS_VIGENTES,
    _SQL_SELLA_APPLY,
    ApplyAbortado,
    SinPerfilAplicar,
    _aplicador_real,
    _fase_apply,
    _FaseApply,
    _guard_apply,
    _mezcla_evidencias_persistidas,
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
    _pendiente_pause_apagada,
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
    _juicio_hoja,
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


_SQL_ULTIMO_CICLO_HOY = """
SELECT status, started_at > now() - interval '3 hours'
  FROM optimizer_cycle
 WHERE platform = %s
   AND (started_at AT TIME ZONE 'UTC')::date = (now() AT TIME ZONE 'UTC')::date
 ORDER BY started_at DESC
 LIMIT 1
"""


def motivo_de_salto(conn: psycopg.Connection, platform: str) -> str | None:
    """Razon por la que hoy NO se abre un ciclo nuevo para `platform`, o None."""
    fila = conn.execute(_SQL_ULTIMO_CICLO_HOY, (platform,)).fetchone()
    if fila is None:
        return None
    status, corriendo_reciente = fila
    if status in ("done", "degraded"):
        return f"cycle {platform}: ya hay un ciclo {status} hoy; espera al siguiente horario"
    if status == "running" and corriendo_reciente:
        return f"cycle {platform}: ciclo running iniciado hace menos de 3 h"
    return None


def _dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


class CicloOcupado(Exception):
    """El claim del lock se perdio: hay un ciclo vigente de OTRO owner."""


@dataclass(frozen=True)
class ResultadoCiclo:
    """Resumen del ciclo corrido (done | degraded | skipped)."""

    cycle_id: int
    status: str
    decisions_count: int
    notes: str


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
            logger.warning("heartbeat fallo (TTL es el backstop): %s", scrub(str(exc)))

    return tick


def _config_reciente(conn: psycopg.Connection) -> tuple[int | None, dict]:
    fila = conn.execute(_SQL_CONFIG_RECIENTE).fetchone()
    if fila is None:
        return (None, {})
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
    la practica (B/D). None sin temporada encendida."""
    plat_t = contadores.temporada_plat
    if plat_t is None:
        return None
    notas: dict = {"reactivaciones": len(contadores.reactivaciones)}
    if contadores.temporada_sombra_comparadas:
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
    excluidas: dict[str, int],
) -> temporada.ConteoTemporada:
    """Lo que conto el ciclo para el aviso del dia: confirmaciones del ciclo,
    pendientes, exclusiones reales y reactivaciones confirmadas."""
    entradas, regresos, pendientes = conn.execute(_SQL_TEMPORADA_CICLO, (cycle_id,)).fetchone()
    return temporada.ConteoTemporada(
        entradas_confirmadas=int(entradas or 0),
        regresos_confirmados=int(regresos or 0),
        pendientes=int(pendientes or 0),
        retenidos_por_presupuesto=retenidos,
        excluidas={motivo: cantidad for motivo, cantidad in excluidas.items() if cantidad},
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
        harvest_destino=(_harvest_destino_notes(contadores) if target_ciclo is not None else None),
        temporada=_notas_temporada(contadores),
    )
    status = "degraded" if guarda is not None else "done"
    with conn.transaction():
        plat_t = contadores.temporada_plat
        ep_abierto = None
        if plat_t is not None and plat_t.modo == "live" and modo.modo == "live":
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
            # TEMPORADA 01 F: una decision resume por pausa de la baja que toca
            # reactivar. El efecto de temporada no aplica: es una reactivacion.
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
        temporada_io.abre_efectos(conn, ep_abierto, pendientes, ids_decisiones)
        _inserta_targets(conn, cycle_id, decided_at, contadores)
        proteccion_venta.inserta_transiciones(conn, contadores.protecciones)
        if target_ciclo is not None and g.propuestas_campana_desde_settings(settings):
            cuerpo["propuestas_campana"] = propuestas_campana.guarda_evaluaciones(
                conn, contadores.propuestas_campana, decided_at
            )
        notas = json.dumps(cuerpo, ensure_ascii=False, default=str)
        _cierra_envelope(conn, cycle_id, status, len(pendientes), notas)

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
    plat_t = contadores.temporada_plat
    aviso_temporada: str | None = None
    if plat_t is not None and plat_t.modo == "live" and cuerpo.get("temporada") is not None:
        reactivaciones = (
            int(notas_apply["reactivaciones"]["confirmadas"])
            if notas_apply and notas_apply.get("reactivaciones")
            else 0
        )
        cuerpo["temporada"]["reactivaciones"] = reactivaciones
        retenidos = int(
            cuerpo.get("skips", {}).get("entidad", {}).get(MOTIVO_REGRESO_ESPERA_PRESUPUESTO, 0)
        )
        aviso = temporada.aviso_del_dia(
            plat_t.config,
            plat_t.estacion,
            conteo=_conteo_temporada(
                conn,
                cycle_id,
                reactivaciones=reactivaciones,
                retenidos=retenidos,
                excluidas=dict(contadores.excluidas_temporada),
            ),
            propuestas=len(contadores.propuestas_campana),
        )
        if aviso:
            cuerpo["temporada"]["aviso"] = aviso
            aviso_temporada = aviso
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
            for _ in range(len(resultado_avisos["enviados"]) + len(resultado_avisos["fallos"])):
                tick()
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
        skips=cuerpo["skips"],
        target=cuerpo.get("target"),
        target_ancla=target_ciclo.ancla if target_ciclo is not None else None,
        tick=tick,
    )
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
        notas = sella_notas_post_apply(conn, cycle_id, cuerpo, degradar=fase.fallo)
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
    """Corre UN ciclo del optimizador para `platform` (amazon_us|amazon_mx)."""
    if decided_at.tzinfo is None:
        raise ValueError("decided_at debe ser tz-aware (UTC): un naive evaluaria segun la TZ local")
    if platform not in PLATAFORMAS_MONEDA:
        raise ValueError(f"plataforma fuera del vocabulario sellado: {platform!r}")
    if heartbeat_cada <= 0:
        raise ValueError("heartbeat_cada debe ser > 0")
    job_key = job_key_de(platform)
    conn.commit()
    hb = _abre_heartbeat(conn)
    cycle_id: int | None = None
    gano = False
    contadores = _Contadores()
    muertos: list[int] = []
    try:
        with conn.transaction():
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
        if cycle_id is not None:
            _sello_fallido(conn, cycle_id, exc, contadores, muertos)
        raise
    finally:
        if gano:
            _libera_lock(hb, conn, job_key, owner)
        if hb is not None:
            hb.close()
