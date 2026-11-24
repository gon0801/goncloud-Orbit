"""Fase de avisos Telegram al final del ciclo (extraido de app/cycle.py, Fase 3.4).

Envuelve de forma fail-silent el envio de cortes encolados, avisos de destino
de harvest, caps de quota agotados, digest del ciclo y alertas de harvest,
devolviendo el diccionario `notes["telegram"]` con solo las claves fallidas.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from app import apply_harvest, notifica
from app.redaction import install_scrub_filter, scrub

logger = logging.getLogger(__name__)
install_scrub_filter(logger)


def _acumula(notas: dict[str, str], clave: str, detalle: str) -> None:
    notas[clave] = f"{notas[clave]} | {detalle}" if clave in notas else detalle


def _notifica_alertas_harvest(alertas: tuple, notas: dict[str, str]) -> None:
    try:
        for alerta in alertas:
            if not getattr(alerta, "envio_fallido", False):
                continue
            if getattr(alerta, "motivo", "") == apply_harvest.MOTIVO_HERMANAS_PENDIENTES:
                notas["harvest_hermanas"] = (
                    "fallo: aviso de hermanas pendientes no enviado por Telegram "
                    "(el harvest quedo aplicado; revisar harvest_job)"
                )
            else:
                notas["harvest_failed"] = (
                    "fallo: alerta de harvest failed no enviada por Telegram (revisar harvest_job)"
                )
    except Exception as exc:
        notas["harvest_failed"] = "fallo: la alerta de harvest failed no salio (ver log)"
        logger.warning("notifica: fallo en alertas de harvest: %s", scrub(str(exc)))


def _fase_notifica(
    avisos: tuple[notifica.CorteEncolado, ...],
    alertas: tuple,
    *,
    cycle_id: int,
    platform: str,
    modo: str,
    status: str,
    decisions_count: int,
    notas_apply: dict,
    skips: dict | None = None,
    target: dict | None = None,
    target_ancla: str | None = None,
    caps_saturados: tuple = (),
    avisos_destino: tuple[notifica.SaltoDestinoGrupo, ...] = (),
    aviso_temporada: str | None = None,
    tick: Callable[[], None] | None = None,
    resume_of: int | None = None,
) -> dict:
    """Avisos Telegram del ciclo (3.3, sellados 2/19): UN aviso por ciclo con sus cortes
    + UN aviso por campana de grupo sin destino + UN aviso por cap de quota saturado
    + UN digest final. Devuelve la seccion notes['telegram'] con SOLO claves de lo
    que fallo (regla 3); vacio = todo bien o canal deshabilitado.

    Con `resume_of` el digest es de un ejecutor de recuperacion (ARREGLOS 05
    A): nombra el ciclo de origen y no repite el contexto diario (la
    contribucion) que ya mando el ciclo que decidio."""
    notas: dict[str, str] = {}

    def _latido() -> None:
        if tick is not None:
            tick()

    try:
        if not notifica.notifica_cortes_encolados(avisos):
            notas["aviso_encola"] = (
                "fallo: aviso de corte encolado no enviado por Telegram "
                "(revisar la cola de veto: la ventana 48h corre igual)"
            )
        if avisos:
            _latido()
    except Exception as exc:
        notas["aviso_encola"] = "fallo: el aviso de corte encolado no salio (ver log)"
        logger.warning("notifica: fallo en avisos de encola: %s", scrub(str(exc)))
    try:
        for salto in avisos_destino:
            if not notifica.notifica_destino_grupo(salto):
                _acumula(
                    notas,
                    "harvest_destino",
                    "fallo: aviso de campana de grupo sin destino no enviado por Telegram "
                    f"(campana #{salto.campaign_ad_entity_id}, motivo {salto.motivo})",
                )
            _latido()
    except Exception as exc:
        _acumula(
            notas,
            "harvest_destino",
            "fallo: el aviso de campana de grupo sin destino no salio (ver log)",
        )
        logger.warning("notifica: fallo en avisos de destino: %s", scrub(str(exc)))
    try:
        for evento in caps_saturados:
            if not notifica.notifica_cap_agotado(
                evento.platform, evento.kind, evento.used, evento.cap
            ):
                _acumula(
                    notas,
                    "cap_agotado",
                    "fallo: aviso de cap agotado no enviado por Telegram "
                    f"(cap {evento.platform}/{evento.kind} agotado: "
                    f"{evento.used}/{evento.cap})",
                )
            _latido()
    except Exception as exc:
        _acumula(notas, "cap_agotado", "fallo: el aviso de cap agotado no salio (ver log)")
        logger.warning("notifica: fallo en avisos de cap agotado: %s", scrub(str(exc)))
    if aviso_temporada:
        try:
            if not notifica.notifica_temporada(aviso_temporada):
                notas["aviso_temporada"] = "fallo: aviso de temporada no enviado por Telegram"
            _latido()
        except Exception as exc:
            notas["aviso_temporada"] = "fallo: el aviso de temporada no salio (ver log)"
            logger.warning("notifica: fallo en el aviso de temporada: %s", scrub(str(exc)))
    try:
        resumen = {
            "cycle_id": cycle_id,
            "plataforma": platform,
            "modo": modo,
            "status": status,
            "decisions_count": decisions_count,
            "apply": notas_apply,
        }
        if skips is not None:
            resumen["skips"] = skips
        if resume_of is not None:
            resumen["resume_of"] = resume_of
        emitir = False
        nuevo_ancla = None
        if target is not None:
            resumen["target"] = target
            aplicado = target.get("target_aplicado")
            emitir, nuevo_ancla = notifica.decide_aviso_target(aplicado, target_ancla)
            if target_ancla is not None:
                resumen["target_ancla"] = target_ancla
        if not notifica.notifica_digest(resumen, con_contribucion=resume_of is None):
            notas["digest"] = "fallo: digest del ciclo no enviado por Telegram"
        elif emitir and target is not None:
            target["ultimo_avisado"] = nuevo_ancla
        _latido()
    except Exception as exc:
        notas["digest"] = "fallo: el digest del ciclo no salio (ver log)"
        logger.warning("notifica: fallo en el digest: %s", scrub(str(exc)))
    _notifica_alertas_harvest(alertas, notas)
    return notas
