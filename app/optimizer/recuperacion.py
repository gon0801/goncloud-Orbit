"""La memoria del bid vista como cadena (TEMPORADA 01 B). PURO.

`cadena_bid` deriva de la historia congelada (HistoriaBid) la cadena de
cambios de una hoja: donde intervinieron las manos del dueno (con fecha o
detectadas como discontinuidad de la cadena, tolerancia 0.005), si el bid
vigente ya no es el ultimo que Orbit confirmo (`dueno_al_final`) y si la hoja
todavia lleva una reduccion de temporada (`reduccion`). Derivado, no se
guarda: el mismo caso rejuega igual.

Sin IO, sin reloj, Decimal. No importa `app.optimizer.caso` en runtime (el
modulo solo lee duck-typed `caso.historia` y `caso.bid.valor`): caso.py
importa temporada.py y temporada.py importa este modulo; el ciclo de imports
se rompe aqui con TYPE_CHECKING.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.optimizer.caso import CambioFechado, CasoHoja, HistoriaBid

# write.py redondea a 0.01 HALF_EVEN: mas que esto es otra mano.
TOLERANCIA_CADENA = Decimal("0.005")
# Los movimientos del motor: un cambio DE ESTOS despues del ultimo movimiento
# de temporada rompe `solo_temporada_despues`. La mano del dueno (con fecha)
# y los ajustes de campana no son movimientos de Orbit.
ORIGENES_ORBIT = frozenset({"motor", "regreso_por_desplome", "temporada"})

_ORIGENES_MANO = ("exclusion", "reactivacion_manual")


@dataclass(frozen=True)
class ReduccionVigente:
    """La reduccion de temporada que la hoja todavia lleva: su ultimo movimiento
    de temporada es una bajada o un ajuste, no un regreso."""

    episodio_id: int
    base: Decimal  # bid_antes del primer movimiento de temporada de ese episodio
    primer_el: dt.datetime  # instante de esa entrada confirmada
    ultimo_bid: Decimal  # bid_despues del ultimo movimiento de temporada
    ultimo_el: dt.datetime
    solo_temporada_despues: bool  # ningun otro cambio de Orbit despues del ultimo


@dataclass(frozen=True)
class CadenaBid:
    """La historia del bid vista como cadena. Derivada, no se guarda."""

    cambios: tuple[CambioFechado, ...]  # sin ajuste_de_campana, ascendentes
    manos_del_dueno: tuple[dt.datetime, ...]  # regreso_del_dueno o discontinuidad
    bid_de_la_ultima_mano: Decimal | None
    dueno_al_final: bool  # bid vigente != ultimo bid conocido de Orbit
    reduccion: ReduccionVigente | None
    # El ultimo cambio fue un recupera_nivel (R6r, bloque C): con el motivo
    # general de los cambios de motor congelado por el bloque C se deriva; en B
    # R6r no existe y queda False.
    ultimo_salto_recuperacion: bool


def _ordenados(historia: HistoriaBid) -> tuple:
    """Cambios (sin ajustes de campana) y manos externas intercalados por
    instante. En empate exacto primero el cambio, despues la mano (la mano
    observada gana al final del dia)."""
    pares = [
        (c.confirmado_el.astimezone(dt.UTC), 0, i, c)
        for i, c in enumerate(historia.cambios)
        if c.origen != "ajuste_de_campana"
    ] + [(m.instante.astimezone(dt.UTC), 1, i, m) for i, m in enumerate(historia.manos_externas)]
    pares.sort(key=lambda p: (p[0], p[1], p[2]))
    return tuple(p[3] for p in pares)


def _es_mano(evento) -> bool:
    """ManoExterna (exclusion con bid_visto o reactivacion manual). CambioFechado
    jamas lleva esos valores en `origen`, asi que el chequeo no necesita importar
    el tipo (romperia el ciclo caso -> temporada -> recuperacion)."""
    return getattr(evento, "origen", None) in _ORIGENES_MANO


def _reduccion_vigente(
    historia: HistoriaBid, cambios: tuple[CambioFechado, ...]
) -> ReduccionVigente | None:
    """La reduccion de temporada que la hoja todavia lleva: el ultimo cambio de
    temporada con motivo entra/ajusta cuyo episodio no tiene fin guardado. Los
    episodios en historia.fines ya terminaron: un fin guardado no revive."""
    indices_temporada = [i for i, c in enumerate(cambios) if c.origen == "temporada"]
    if not indices_temporada:
        return None
    ultimo_i = indices_temporada[-1]
    ultimo = cambios[ultimo_i]
    if ultimo.motivo_temporada not in ("entra", "ajusta"):
        return None  # el regreso ya salio
    if ultimo.episodio_id is None or ultimo.episodio_id in historia.fines:
        return None
    primero = next(
        c
        for c in cambios[: ultimo_i + 1]
        if c.origen == "temporada"
        and c.episodio_id == ultimo.episodio_id
        and c.motivo_temporada in ("entra", "ajusta")
    )
    solo_temporada = not any(c.origen in ORIGENES_ORBIT for c in cambios[ultimo_i + 1 :])
    return ReduccionVigente(
        episodio_id=ultimo.episodio_id,
        base=primero.bid_antes if primero.bid_antes is not None else primero.bid_despues,
        primer_el=primero.confirmado_el.astimezone(dt.UTC),
        ultimo_bid=ultimo.bid_despues,
        ultimo_el=ultimo.confirmado_el.astimezone(dt.UTC),
        solo_temporada_despues=solo_temporada,
    )


def _cadena_desde(historia: HistoriaBid | None, bid_vigente: Decimal | None) -> CadenaBid | None:
    """La cadena de UNA hoja desde su historia congelada y el bid vigente.
    None sin historia (dato faltante; quien decide dinero se abstiene)."""
    if historia is None:
        return None
    manos: list[dt.datetime] = []
    bid_de_la_ultima_mano: Decimal | None = None
    conocido: Decimal | None = None  # el ultimo bid que Orbit sabe que hubo
    cambios: list[CambioFechado] = []
    for evento in _ordenados(historia):
        if _es_mano(evento):
            manos.append(evento.instante.astimezone(dt.UTC))
            bid_de_la_ultima_mano = evento.bid_visto
            if evento.bid_visto is not None:
                conocido = evento.bid_visto  # la exclusion fecha la mano
            continue
        cambio = evento
        if cambio.origen == "regreso_del_dueno":
            manos.append(cambio.confirmado_el.astimezone(dt.UTC))
            bid_de_la_ultima_mano = cambio.bid_despues
        elif (
            conocido is not None
            and cambio.bid_antes is not None
            and abs(cambio.bid_antes - conocido) > TOLERANCIA_CADENA
        ):
            # Discontinuidad de la cadena: el bid de Amazon ya no era el que
            # Orbit dejo; la mano se fecha en el cambio posterior.
            manos.append(cambio.confirmado_el.astimezone(dt.UTC))
            bid_de_la_ultima_mano = cambio.bid_antes
        cambios.append(cambio)
        conocido = cambio.bid_despues
    dueno_al_final = (
        conocido is not None
        and bid_vigente is not None
        and abs(bid_vigente - conocido) > TOLERANCIA_CADENA
    )
    return CadenaBid(
        cambios=tuple(cambios),
        manos_del_dueno=tuple(manos),
        bid_de_la_ultima_mano=bid_de_la_ultima_mano,
        dueno_al_final=dueno_al_final,
        reduccion=_reduccion_vigente(historia, tuple(cambios)),
        ultimo_salto_recuperacion=False,
    )


def cadena_bid(caso: CasoHoja) -> CadenaBid | None:
    """historia.cambios sin ajustes de campana, intercalados por instante con
    historia.manos_externas. None sin historia."""
    return _cadena_desde(caso.historia, caso.bid.valor)
