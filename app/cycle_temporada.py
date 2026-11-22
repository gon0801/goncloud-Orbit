"""Puente de temporada dentro del recorrido TX2 del ciclo Ads (TEMPORADA 01 B/D).

Extraido de `app/cycle.py` (Ola D1): estampado de episodio, frontera por hoja,
pausa de sombra (modo shadow), comparacion sombra vs real y anotacion de
regresos retenidos por campana topada.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import replace
from decimal import Decimal

import psycopg

from app import proteccion_venta, temporada_io
from app.cycle_grupos import _Contadores
from app.cycle_serializacion import _Pendiente
from app.cycle_sombra import _insumos_pausa
from app.lecturas_caso import LecturasPlataforma
from app.optimizer import cortes, temporada, windows
from app.optimizer import politica as politica_mod
from app.optimizer.caso import InsumosPausa
from app.optimizer.politica import MOTIVO_REGRESO_ESPERA_PRESUPUESTO
from app.optimizer.recuperacion import presupuesto_topado


def _estampa_temporada(
    pendiente: _Pendiente, frontera_h: temporada.FronteraHoja | None
) -> _Pendiente:
    motivo = (pendiente.inputs or {}).get("motivo")
    es_temporada = motivo in temporada_io.MOTIVOS_TEMPORADA
    es_pausa_de_baja = (
        pendiente.kind == "pause"
        and (pendiente.inputs or {})
        .get("caso", {})
        .get("temporada", {})
        .get("frontera", {})
        .get("fase_hoja")
        == "baja"
    )
    if (
        frontera_h is None
        or frontera_h.episodio_id is None
        or not (es_temporada or es_pausa_de_baja)
    ):
        return pendiente
    return replace(
        pendiente,
        inputs={**pendiente.inputs, "temporada_episodio_id": frontera_h.episodio_id},
    )


def _frontera_temporada(
    lecturas: LecturasPlataforma | None,
    contadores: _Contadores,
    entidad_id: int,
    current_bid: Decimal | None,
    *,
    floor: Decimal,
    ceiling: Decimal,
) -> tuple[temporada.Pendiente | None, temporada.FronteraHoja | None, bool]:
    if lecturas is None:
        return None, None, False
    plat_t = contadores.temporada_plat
    en_vuelo = plat_t is not None and plat_t.estado.efectos_abiertos.get(entidad_id) == "en_vuelo"
    if hasattr(lecturas, "evalua_temporada"):
        evaluacion = lecturas.evalua_temporada(entidad_id, current_bid, piso=floor, techo=ceiling)
        pendiente_t, frontera_h = evaluacion if evaluacion is not None else (None, None)
    else:
        pendiente_t = None
        frontera_h = lecturas.frontera(entidad_id, current_bid, piso=floor, techo=ceiling)
    if frontera_h is not None and not en_vuelo:
        if frontera_h.fin is not None:
            contadores.fines[entidad_id] = frontera_h.fin
        if frontera_h.exclusion_hoy is not None:
            contadores.exclusiones[entidad_id] = frontera_h.exclusion_hoy
    return pendiente_t, frontera_h, en_vuelo


def _pausa_sombra(
    conn: psycopg.Connection,
    *,
    contadores: _Contadores,
    platform: str,
    entidad_id: int,
    decided_at: dt.datetime,
    frontera_h: temporada.FronteraHoja | None,
    en_vuelo: bool,
    evidencia: windows.EvidenciaAdGroup | None,
    pause_economica: bool,
    proteccion_cache: proteccion_venta.CacheProteccion | None,
) -> InsumosPausa | None:
    """Pausa de la sombra (ads_temporada en shadow): x mult y cerca de la hoja."""
    plat_t = contadores.temporada_plat
    if plat_t is None or plat_t.en_vivo or frontera_h is None or en_vuelo:
        return None
    mult_sombra = frontera_h.mult_cortes if frontera_h.fase_hoja == "baja" else None
    corte_pause_sombra = cortes.umbral_corte(evidencia, "pause", mult_baja=mult_sombra)
    _costo_s, _cortes_s, pausa_sombra = _insumos_pausa(
        conn,
        platform=platform,
        entidad_id=entidad_id,
        decided_at=decided_at,
        corte_pause=corte_pause_sombra,
        pause_economica=pause_economica,
        proteccion_cache=proteccion_cache,
        mult_baja=mult_sombra,
        cerca=frontera_h.cerca_hoja,
    )
    return pausa_sombra


def _compara_temporada(
    contadores: _Contadores, entidad_id: int, caso, veredicto, veredicto_sombra, caso_sombra
) -> None:
    """En practica (shadow) solo registra diferencias en notes.temporada.

    Guarda el caso real en `caso` y el caso CON marcadores en `caso_sombra`: sin
    el segundo, un `recupera_nivel` no es reproducible (sus topes y su redondeo
    esconden el multiplicador con el que R6r lo decidio)."""
    contadores.temporada_sombra_comparadas += 1
    if veredicto_sombra != veredicto:
        destino = getattr(veredicto_sombra, "bid_nuevo", None)
        contadores.temporada_sombra_diferencias.append(
            {
                "hoja_id": entidad_id,
                "real": veredicto.__class__.__name__,
                "motivo_real": getattr(veredicto, "motivo", None),
                "sombra": veredicto_sombra.__class__.__name__,
                "motivo_sombra": getattr(veredicto_sombra, "motivo", None),
                # El destino de la sombra (Mover/Regresar) es lo que hace
                # revisable el m* de una subida rapida en la practica.
                "destino_sombra": None if destino is None else str(destino),
                "caso": caso.como_json(),
                "caso_sombra": caso_sombra.como_json(),
            }
        )
    else:
        contadores.temporada_sombra_coinciden += 1


def _anota_regreso_retenido(
    contadores: _Contadores,
    lecturas: LecturasPlataforma | None,
    caso,
    veredicto,
    frontera_h: temporada.FronteraHoja | None,
    entidad_id: int,
    current_bid: Decimal | None,
    *,
    floor: Decimal,
    techo: Decimal,
    pendiente_t: temporada.Pendiente | None = None,
) -> None:
    """Anota en notes.skips el regreso retenido por campana topada (bloque D)."""
    plat_t = contadores.temporada_plat
    if (
        plat_t is None
        or not plat_t.en_vivo
        or frontera_h is None
        or frontera_h.fase_hoja != "baja"
        or isinstance(veredicto, politica_mod.Regresar)
        or (pendiente_t is None and lecturas is None)
    ):
        return
    pendiente_temporada = (
        pendiente_t
        if pendiente_t is not None
        else lecturas.pendiente(entidad_id, current_bid, piso=floor, techo=techo)
    )
    if isinstance(pendiente_temporada, temporada.RestauracionPendiente) and (
        caso.presupuesto is not None and presupuesto_topado(caso) is True
    ):
        contadores.skips_entidad[MOTIVO_REGRESO_ESPERA_PRESUPUESTO] += 1
