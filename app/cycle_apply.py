"""Fase de apply del ciclo del optimizador Ads (TX4 + ejecucion + sello post-apply).

Extraido de `app/cycle.py` (Ola D1): concentra la construccion del `Aplicador`,
el guard de ownership/heartbeat pre-HTTP, la ejecucion de TX4 + reconciliaciones
+ bids + cola de cortes, y la unificacion del sello post-apply sobre
`optimizer_cycle.notes`.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass

import httpx
import psycopg
from psycopg import pq

from app import apply, apply_cola, apply_harvest, notifica
from app.ads.config import AdsCredentials
from app.apply import Aplicador
from app.apply_cuota import CapSaturado
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
    """TX4 + fase de apply fail-closed-auditada dentro del lock del ciclo."""
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
        aplicador = Aplicador(
            conn,
            platform=platform,
            cycle_id_ejecutor=cycle_id,
            owner=owner,
            job_key=job_key,
            tick=guard,
        )
    try:
        with conn.transaction():
            resumen = apply_cola.encola_cortes(
                conn, aplicador, cycle_id, modo_envelope=modo.modo, ahora=decided_at
            )
        avisos = resumen.avisos
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
            res_react = aplicador.aplica_reactivaciones(
                conn, apply.resumes_del_ciclo(conn, cycle_id), tick=guard
            )
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
                "espera_target": res_cola.espera_target,
            }
            if res_cola.revalidaciones_economicas:
                notas["revalidaciones_economicas"] = res_cola.revalidaciones_economicas
            notas["reactivaciones"] = {
                "confirmadas": res_react.confirmadas,
                "fallidas": res_react.fallidas,
                "sin_quota": res_react.sin_quota,
                "omitidas": res_react.omitidas,
            }
            total = (
                res_bids.aplicadas
                + res_cola.aplicadas
                + res_react.confirmadas
                + reconciliacion.jobs_done
                + reconciliacion.negativas_confirmadas
                + reconciliacion.pausas_confirmadas
                + rec_bids[0]
            )
            with conn.transaction():
                conn.execute(_SQL_APPLIED_COUNT_CICLO, (total, cycle_id))
        conn.commit()
    except ApplyAbortado as exc:
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
