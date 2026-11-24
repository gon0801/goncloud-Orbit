"""Apply del ciclo del optimizador Ads: conciliacion previa, TX4, ejecucion y sellos.

Extraido de `app/cycle.py` (Ola D1): concentra la construccion del `Aplicador`,
el guard de ownership/heartbeat pre-HTTP, la conciliacion del ledger que corre
ANTES de decidir, la ejecucion de TX4 + bids + cola de cortes por fases, y la
unificacion del sello post-apply sobre `optimizer_cycle.notes`.

Orden desde ARREGLOS 05 A: claim -> conciliacion -> snapshot y decision (TX2,
TX3) -> apply -> cierre. Antes la conciliacion corria dentro del apply, ya
despues de decidir, y la decision saltaba como «bid en vuelo» hojas cuyo
resultado una lectura habria resuelto.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx
import psycopg
from psycopg import pq

from app import apply, apply_cola, apply_harvest, notifica
from app.ads.client import AdsClientError
from app.ads.config import AdsConfigError, AdsCredentials
from app.apply import Aplicador, DuenoDelClaimCambio
from app.apply_cuota import CapSaturado
from app.apply_estados import AccionCambio
from app.db import tx_durable
from app.lock import SQL_OWNER as _SQL_OWNER_LOCK
from app.optimizer import goals as g
from app.redaction import install_scrub_filter, scrub

logger = logging.getLogger(__name__)
install_scrub_filter(logger)


class ApplyAbortado(Exception):
    """Ownership-check pre-HTTP (decision 11): el lock del job ya NO es nuestro."""


class SinPerfilAplicar(Exception):
    """La fabrica del aplicador no encontro perfil ACEPTADO para la plataforma."""


_SQL_SELLA_APPLY = """
UPDATE optimizer_cycle
   SET status = CASE WHEN %s AND status = 'done' THEN 'degraded' ELSE status END,
       notes = %s
 WHERE id = %s AND status IN ('done', 'degraded')
"""

_SQL_APPLIED_COUNT_CICLO = """
UPDATE optimizer_cycle SET applied_count = %s WHERE id = %s
"""

_SQL_NOTAS_VIGENTES = "SELECT notes FROM optimizer_cycle WHERE id = %s"


@dataclass(frozen=True)
class _FaseApply:
    """Resultado de la fase de apply para `_corre_fases`."""

    notas: dict
    fallo: bool
    avisos: tuple[notifica.CorteEncolado, ...] = ()
    alertas: tuple = ()
    caps_saturados: tuple[CapSaturado, ...] = ()
    avisos_destino: tuple[notifica.SaltoDestinoGrupo, ...] = ()
    reactivaciones: dict | None = None


@dataclass(frozen=True)
class _Reconciliacion:
    """Lo que la conciliacion previa a la decision dejo hecho en esta corrida."""

    aplicador: Aplicador | None
    notas: dict
    alertas: tuple = ()
    caps_saturados: tuple[CapSaturado, ...] = ()
    jobs_done: int = 0
    negativas_confirmadas: int = 0
    pausas_confirmadas: int = 0
    bids_confirmados: int = 0
    reversas_confirmadas: int = 0
    reversas_pendientes_de_lectura: int = 0
    reversas_por_queue_id: dict = field(default_factory=dict)


def _guard_apply(
    conn: psycopg.Connection,
    job_key: str,
    owner: str,
    tick_heartbeat: Callable[[], None],
    *,
    hb: psycopg.Connection | None = None,
) -> Callable[[], None]:
    """`tick` del Aplicador en apply: heartbeat + ownership-check fresco."""

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
    """Fabrica por defecto del aplicador (credenciales + perfil aceptado)."""
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


def _mezcla_evidencias_persistidas(conn: psycopg.Connection, cycle_id: int, cuerpo: dict) -> None:
    """Preserva `revalidaciones_economicas` persistidas durante apply en `notes`."""
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
        destino = list(destino)
        seccion["revalidaciones_economicas"] = destino
    vistos = {e.get("decision_id") for e in destino if isinstance(e, dict)}
    for evidencia in persistidas:
        if isinstance(evidencia, dict) and evidencia.get("decision_id") not in vistos:
            destino.append(evidencia)
            vistos.add(evidencia.get("decision_id"))


def _sella_apply(conn: psycopg.Connection, cycle_id: int, notes: str, *, degradar: bool) -> bool:
    """Sello post-apply: notes['apply'] y degraded si la fase aborto."""
    filas = conn.execute(_SQL_SELLA_APPLY, (degradar, notes, cycle_id)).rowcount
    if filas == 0:
        logger.warning(
            "sello de apply del envelope %s ignorado: cerrado por otro (rastro)",
            cycle_id,
        )
        return False
    return True


def sella_notas_post_apply(
    conn: psycopg.Connection,
    cycle_id: int,
    cuerpo: dict,
    *,
    degradar: bool,
) -> str:
    """Punto unico de escritura final de `optimizer_cycle.notes` tras apply/notifica."""
    _mezcla_evidencias_persistidas(conn, cycle_id, cuerpo)
    notas = json.dumps(cuerpo, ensure_ascii=False, default=str)
    with tx_durable(conn):
        _sella_apply(conn, cycle_id, notas, degradar=degradar)
    return notas


def _fase_reconciliacion(
    conn: psycopg.Connection,
    *,
    modo: g.ModoEfectivo,
    platform: str,
    cycle_id_ejecutor: int,
    job_key: str,
    owner: str,
    guard: Callable[[], None],
    aplicador_factory: Callable[..., Aplicador] | None,
    origen: int | None = None,
) -> _Reconciliacion:
    """Concilia el ledger y la cola contra Amazon VIVO antes de decidir (ARREGLOS
    05 A.2; CONTEXTO: «al inicio de cada ciclo»). La decision parte de lo que
    Amazon ya tiene: una puja que salio y quedo incierta deja de estar «en
    vuelo» en cuanto una lectura la resuelve. `reconcilia_bids` solo lee; nunca
    reenvia. Corre con el claim en poder de este owner.

    Lo que NO detiene la decision: la fabrica sin perfil aceptado, o un fallo
    de la API, la identidad o los permisos de Amazon. Se anotan como
    `apply_error`, no hay aplicador, ninguna mutacion sale y las decisiones se
    comprometen igual: un ejecutor del siguiente horario las completa. Lo que
    SI detiene todo: perder el claim (`ApplyAbortado`), la base indisponible o
    cualquier error que no venga de la API, que abortan antes de comprometer
    decisiones en vez de decidir a ciegas."""
    if modo.modo != "live":
        if not apply_cola.hay_reversas_pause_abiertas(conn, platform):
            return _Reconciliacion(aplicador=None, notas={})
        try:
            fabrica = aplicador_factory if aplicador_factory is not None else _aplicador_real
            aplicador_lectura = fabrica(
                conn,
                platform=platform,
                cycle_id_ejecutor=cycle_id_ejecutor,
                owner=owner,
                job_key=job_key,
                tick=guard,
            )
            rec_rev = apply_cola.reconcilia_reversas_pause(
                conn, aplicador_lectura, platform, tick=guard
            )
        except (AdsClientError, SinPerfilAplicar, AdsConfigError) as exc:
            conn.rollback()
            return _Reconciliacion(
                aplicador=None, notas={"apply_error": scrub(str(exc)) or type(exc).__name__}
            )
        conn.commit()
        res_rev = rec_rev.resumen
        return _Reconciliacion(
            aplicador=None,
            notas={
                "reversas_reconciliadas": {
                    "confirmadas": res_rev.confirmadas,
                    "fallidas": res_rev.fallidas,
                    "omitidas": res_rev.omitidas,
                    "pendientes_de_lectura": res_rev.pendientes_de_lectura,
                }
            },
            reversas_confirmadas=res_rev.confirmadas,
            reversas_pendientes_de_lectura=res_rev.pendientes_de_lectura,
            reversas_por_queue_id=dict(rec_rev.por_queue_id),
        )
    try:
        fabrica = aplicador_factory if aplicador_factory is not None else _aplicador_real
        aplicador = fabrica(
            conn,
            platform=platform,
            cycle_id_ejecutor=cycle_id_ejecutor,
            owner=owner,
            job_key=job_key,
            tick=guard,
        )
        if origen is not None:
            # La revalidacion de cortes lee el target del ciclo que decidio.
            aplicador.ciclo_origen = origen
        reconciliacion = apply_harvest.reconcilia_harvest(conn, aplicador, platform)
        confirmados, fallidos = apply.reconcilia_bids(conn, aplicador, platform)
        rec_rev = apply_cola.reconcilia_reversas_pause(conn, aplicador, platform, tick=guard)
    except (AdsClientError, SinPerfilAplicar, AdsConfigError) as exc:
        conn.rollback()
        return _Reconciliacion(
            aplicador=None, notas={"apply_error": scrub(str(exc)) or type(exc).__name__}
        )
    conn.commit()
    res_rev = rec_rev.resumen
    notas_rec: dict = {
        "bids_reconciliados": {"confirmados": confirmados, "fallidos": fallidos},
        "pausas_reconciliadas": {
            "confirmadas": reconciliacion.pausas_confirmadas,
            "fallidas": reconciliacion.pausas_fallidas,
        },
    }
    if res_rev.confirmadas or res_rev.fallidas or res_rev.omitidas or res_rev.pendientes_de_lectura:
        notas_rec["reversas_reconciliadas"] = {
            "confirmadas": res_rev.confirmadas,
            "fallidas": res_rev.fallidas,
            "omitidas": res_rev.omitidas,
            "pendientes_de_lectura": res_rev.pendientes_de_lectura,
        }
    return _Reconciliacion(
        aplicador=aplicador,
        notas=notas_rec,
        alertas=reconciliacion.alertas,
        caps_saturados=reconciliacion.caps_saturados,
        jobs_done=reconciliacion.jobs_done,
        negativas_confirmadas=reconciliacion.negativas_confirmadas,
        pausas_confirmadas=reconciliacion.pausas_confirmadas,
        bids_confirmados=confirmados,
        reversas_confirmadas=res_rev.confirmadas,
        reversas_pendientes_de_lectura=res_rev.pendientes_de_lectura,
        reversas_por_queue_id=dict(rec_rev.por_queue_id),
    )


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
    aplicador_factory: Callable[..., Aplicador] | None = None,
    proteccion_activa: bool = False,
    avisos_destino: tuple[notifica.SaltoDestinoGrupo, ...] = (),
    origen: int | None = None,
    reconciliacion: _Reconciliacion | None = None,
) -> _FaseApply:
    """Cortes a la cola y, en live, pujas, liberacion de cortes y reactivaciones.

    `cycle_id` es el envelope de esta corrida: quien confirma y a quien se
    atribuyen los desenlaces. `origen` es el ciclo que decidio; por omision es
    el propio, y en un ejecutor de recuperacion es el ciclo que recupera.

    `reconciliacion` es la conciliacion que el ciclo ya corrio ANTES de decidir
    (ARREGLOS 05 A.2). Sin ella esta fase la corre aqui misma, como siempre:
    conciliar y aplicar es una unidad para quien la llama suelta.

    Por fases (ARREGLOS 05 A.3): cada fase deja su resultado en notes['apply']
    antes de pasar a la siguiente, y un fallo global aborta las que faltan sin
    borrar lo ya hecho. Dentro de cada fase el aislamiento es por entidad: un
    fallo remoto de UNA puja o de UN corte no detiene a las demas (ver
    `Aplicador.aplica_bids`, `apply_cola.libera_vencidos` y
    `aplica_reactivaciones`). Fallo global es perder el claim, la base
    indisponible o la identidad y permisos: despues ninguno sale otra
    mutacion. Un 5xx no autoriza repetir la misma mutacion: la decision
    incierta queda sin sello y solo la conciliacion la resuelve."""
    origen = cycle_id if origen is None else origen
    notas: dict = {}
    fallo = False
    avisos: tuple[notifica.CorteEncolado, ...] = ()
    alertas_harvest: list = []
    caps_saturados: list = []
    aplicador = None
    conciliar_aqui = reconciliacion is None
    reconciliacion = reconciliacion or _Reconciliacion(aplicador=None, notas={})
    try:
        if conciliar_aqui:
            reconciliacion = _fase_reconciliacion(
                conn,
                modo=modo,
                platform=platform,
                cycle_id_ejecutor=cycle_id,
                job_key=job_key,
                owner=owner,
                guard=guard,
                aplicador_factory=aplicador_factory,
                origen=origen if origen != cycle_id else None,
            )
        fallo = modo.modo == "live" and reconciliacion.aplicador is None
        notas.update(reconciliacion.notas)
        alertas_harvest = list(reconciliacion.alertas)
        caps_saturados = list(reconciliacion.caps_saturados)
        aplicador = reconciliacion.aplicador
        if aplicador is None:
            aplicador = Aplicador(
                conn,
                platform=platform,
                cycle_id_ejecutor=cycle_id,
                owner=owner,
                job_key=job_key,
                tick=guard,
            )
        with conn.transaction():
            resumen = apply_cola.encola_cortes(
                conn, aplicador, origen, modo_envelope=modo.modo, ahora=decided_at
            )
        avisos = resumen.avisos
        notas["cortes_encolados"] = {
            "live": resumen.encoladas_live,
            "shadow": resumen.encoladas_shadow,
            "choques": len(resumen.choques),
        }
        if modo.modo == "live" and reconciliacion.aplicador is not None:
            res_bids = aplicador.aplica_bids(
                apply.bids_recuperables(conn, origen), escalera_global=modo.modo
            )
            notas["bids_aplicados"] = res_bids.aplicadas
            notas["bids_descartados"] = len(res_bids.descartadas)
            notas["bids_divergentes"] = res_bids.divergencias
            if res_bids.inciertos:
                notas["bids_inciertos"] = res_bids.inciertos
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
            notas["cortes_liberados"] = {
                "liberadas": res_cola.liberadas,
                "aplicadas": res_cola.aplicadas,
                "fallidas": res_cola.fallidas,
                "sin_quota": res_cola.sin_quota,
                "carreras_perdidas": res_cola.carreras_perdidas,
                "espera_target": res_cola.espera_target,
            }
            if res_cola.revalidaciones_economicas:
                notas["revalidaciones_economicas"] = res_cola.revalidaciones_economicas

            resumes_origen = apply_cola.resumes_del_ciclo(conn, origen)
            res_react = apply_cola.aplica_reactivaciones(
                conn,
                aplicador,
                resumes_origen,
                tick=guard,
                reconciliadas_en_corrida=reconciliacion.reversas_por_queue_id,
            )
            caps_saturados += res_react.caps_saturados
            qids_origen = {
                int(q) for r in resumes_origen if (q := r.inputs.get("queue_id")) is not None
            }
            extras_confirmadas = sum(
                1
                for qid, tr in reconciliacion.reversas_por_queue_id.items()
                if qid not in qids_origen
                and tr is not None
                and tr.accion == AccionCambio.SELLAR_APLICADO
            )
            extras_pendientes = sum(
                1
                for qid, tr in reconciliacion.reversas_por_queue_id.items()
                if qid not in qids_origen and (tr is None or tr.origen_incompleto)
            )
            notas["reactivaciones"] = {
                "confirmadas": res_react.confirmadas,
                "fallidas": res_react.fallidas,
                "sin_quota": res_react.sin_quota,
                "omitidas": res_react.omitidas,
                "pendientes_de_lectura": res_react.pendientes_de_lectura + extras_pendientes,
            }
            total = (
                res_bids.aplicadas
                + res_cola.aplicadas
                + res_react.confirmadas
                + reconciliacion.jobs_done
                + reconciliacion.negativas_confirmadas
                + reconciliacion.pausas_confirmadas
                + reconciliacion.bids_confirmados
                + extras_confirmadas
            )
            with conn.transaction():
                conn.execute(_SQL_APPLIED_COUNT_CICLO, (total, cycle_id))
        conn.commit()
    except (ApplyAbortado, DuenoDelClaimCambio) as exc:
        conn.rollback()
        notas["apply_abortado_owner"] = True
        fallo = True
        logger.warning("fase de apply abortada (lease perdido): %s", scrub(str(exc)))
    except Exception as exc:
        conn.rollback()
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
        reactivaciones=notas.get("reactivaciones"),
    )
