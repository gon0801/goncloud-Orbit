"""La memoria del bid vista como cadena y la subida rapida R6r (TEMPORADA 01
B/C). PURO.

`cadena_bid` deriva de la historia congelada (HistoriaBid) la cadena de
cambios de una hoja: donde intervinieron las manos del dueno (con fecha o
detectadas como discontinuidad de la cadena, tolerancia 0.005), si el bid
vigente ya no es el ultimo que Orbit confirmo (`dueno_al_final`) y si la hoja
todavia lleva una reduccion de temporada (`reduccion`). Derivado, no se
guarda: el mismo caso rejuega igual.

El bloque C agrega la subida rapida (R6r): `techo_conocido` (el bid mas alto
que la hoja tuvo desde la ultima mano del dueno, dentro de memoria_dias),
`multiplicador_sostenible` (m* que la evidencia sostiene con gamma_q),
`destino_recuperacion` (el destino topado del salto) y `presupuesto_topado`
(freno BUD). Politica llama a destino_recuperacion en R6: None = decide el
R6 de hoy, la recuperacion solo acelera y nunca frena.

Sin IO, sin reloj, Decimal. DAG aciclico: `temporada_config` -> `caso` ->
`recuperacion` -> `temporada_hoja` -> `politica`.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal, localcontext

from app.optimizer.caso import (
    DIAS_MADUREZ,
    PRECISION,
    CambioFechado,
    CasoHoja,
    HistoriaBid,
    ManoExterna,
    PresupuestoCampana,
)
from app.optimizer.evidencia import gamma_q

# write.py redondea a 0.01 HALF_EVEN: mas que esto es otra mano.
TOLERANCIA_CADENA = Decimal("0.005")
# El paso de R6 (politica.PASO_SUBIDA, espejo pineado en tests/test_arq_bids_m.py):
# R6r solo decide cuando la evidencia sostiene MAS que un paso normal.
PASO_SUBIDA = Decimal("0.15")
CENTAVO = Decimal("0.01")
# Motivo del movimiento R6r. Vive aqui porque `ultimo_salto_recuperacion` se
# deriva de el; politica lo reexporta al vocabulario cerrado de motivos.
MOTIVO_RECUPERA_NIVEL = "recupera_nivel"
# Los movimientos del motor: un cambio DE ESTOS despues del ultimo movimiento
# de temporada rompe `solo_temporada_despues`. La mano del dueno (con fecha)
# y los ajustes de campana no son movimientos de Orbit.
ORIGENES_ORBIT = frozenset({"motor", "regreso_por_desplome", "temporada"})


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


def _ordenados(historia: HistoriaBid) -> tuple[CambioFechado | ManoExterna, ...]:
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
        if isinstance(evento, ManoExterna):
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
    # TEMPORADA 01 C: el ultimo cambio de Orbit lleva el motivo del salto de
    # recuperacion. Los casos congelados antes de C traen motivo None en sus
    # cambios (el congelado lo deja ausente) y derivan False: replay byte a byte.
    ultimo_salto = False
    for cambio in reversed(cambios):
        if cambio.origen in ORIGENES_ORBIT:
            ultimo_salto = cambio.motivo == MOTIVO_RECUPERA_NIVEL
            break
    return CadenaBid(
        cambios=tuple(cambios),
        manos_del_dueno=tuple(manos),
        bid_de_la_ultima_mano=bid_de_la_ultima_mano,
        dueno_al_final=dueno_al_final,
        reduccion=_reduccion_vigente(historia, tuple(cambios)),
        ultimo_salto_recuperacion=ultimo_salto,
    )


def cadena_bid(caso: CasoHoja) -> CadenaBid | None:
    """historia.cambios sin ajustes de campana, intercalados por instante con
    historia.manos_externas. None sin historia."""
    return _cadena_desde(caso.historia, caso.bid.valor)


def techo_conocido(
    caso: CasoHoja,
    *,
    hasta: dt.datetime | None = None,
    cadena: CadenaBid | None = None,
) -> Decimal | None:
    """El bid mas alto que la hoja tuvo desde la ultima mano del dueno, dentro de
    la memoria del caso (`memoria_dias`): el lector puede extender la historia
    mas atras (un episodio abierto no caduca, lo pide la temporada), pero el
    techo NUNCA autoriza saltos con pujas fuera de la memoria elegida.
    `hasta` excluye los cambios desde ese instante (techo_de_baja lo usa para
    dejar fuera la propia entrada de temporada). None sin nivel conocido o en
    terreno del dueno (dueno_al_final): ahi solo decide el R6 de hoy.
    # ancla = ultima mano del dueno dentro de la memoria, o el inicio de la memoria
    # niveles = [bid al ancla] + [bid_despues de cada cambio posterior al ancla y < hasta]
    #   (bid al ancla = bid_de_la_ultima_mano, o bid_antes del primer cambio si no hubo mano)
    # -> min(max(niveles), caso.bid.techo)"""
    cad = cadena if cadena is not None else cadena_bid(caso)
    if cad is None or cad.dueno_al_final:
        return None
    inicio_memoria = None
    if caso.recuperacion is not None:
        # hoy = ventana_hasta + DIAS_MADUREZ (la ventana madura congela el dia
        # del caso); la memoria alcanza memoria_dias hacia atras.
        inicio = caso.ventana_hasta + dt.timedelta(
            days=DIAS_MADUREZ - caso.recuperacion.memoria_dias
        )
        inicio_memoria = dt.datetime.combine(inicio, dt.time(0), tzinfo=dt.UTC)
    en_memoria = (
        cad.manos_del_dueno
        if inicio_memoria is None
        else tuple(m for m in cad.manos_del_dueno if m >= inicio_memoria)
    )
    ancla = max(en_memoria) if en_memoria else None
    niveles: list[Decimal] = []
    if ancla is not None and cad.bid_de_la_ultima_mano is not None:
        niveles.append(cad.bid_de_la_ultima_mano)
    cambios_memoria = tuple(
        c
        for c in cad.cambios
        if (inicio_memoria is None or c.confirmado_el.astimezone(dt.UTC) >= inicio_memoria)
    )
    if ancla is None and cambios_memoria and cambios_memoria[0].bid_antes is not None:
        # Sin mano del dueno en memoria, el bid del ancla es el bid_antes del
        # primer cambio dentro de la memoria.
        niveles.append(cambios_memoria[0].bid_antes)
    for cambio in cambios_memoria:
        instante = cambio.confirmado_el.astimezone(dt.UTC)
        if (ancla is not None and instante <= ancla) or (hasta is not None and instante >= hasta):
            continue
        niveles.append(cambio.bid_despues)
    if not niveles:
        return None
    return min(max(niveles), caso.bid.techo)


def multiplicador_sostenible(a_subida: Decimal, arg_subida: Decimal, confianza: Decimal) -> Decimal:
    """m* tal que gamma_q(a_subida, m* x arg_subida) == confianza. `arg_subida` es el
    argumento que `politica.py` pasa a gamma_q al CPC de hoy: clics_sub x cpc /
    (0.85 x meta/100 x ticket_sub). gamma_q es decreciente en x, asi que gamma_q(a, 0) = 1
    >= confianza y gamma_q(a, a + 40) < confianza para el rango operativo: la biseccion
    de 60 iteraciones parte [0, a_subida + 40] y conserva en `lo` el lado que sigue
    sosteniendo la confianza; determinista, replay fiel."""
    assert arg_subida > 0, "arg_subida > 0: _fondo_juicio exige pedidos y venta > 0"
    with localcontext(prec=PRECISION):
        lo = Decimal(0)
        hi = a_subida + 40
        for _ in range(60):
            medio = (lo + hi) / 2
            if gamma_q(a_subida, medio) >= confianza:
                lo = medio
            else:
                hi = medio
        return lo / arg_subida


def destino_recuperacion(
    caso: CasoHoja, *, a_subida: Decimal, arg_subida: Decimal
) -> Decimal | None:
    """R6r (TEMPORADA 01 C): el bid mas alto que la evidencia sostiene en un paso.
    None = decide el R6 de hoy (la recuperacion solo acelera, nunca frena). Topes
    todos a la vez: techo_conocido, techo del goal, techo_de_baja y la regla de
    que solo actua si pasa de bid x (1 + PASO_SUBIDA)."""
    from app.optimizer import temporada_hoja

    if caso.recuperacion is None:
        return None
    cad = cadena_bid(caso)
    if temporada_hoja.en_terreno_del_dueno(caso, cad):
        return None  # terreno del dueno (decision 7 del plan)
    bid = caso.bid.valor
    techo = techo_conocido(caso, cadena=cad)
    if techo is None or bid is None:
        return None
    tb = temporada_hoja.techo_de_baja(caso, cadena=cad)
    techo = min(techo, caso.bid.techo, tb if tb is not None else techo)
    if techo <= bid * (Decimal(1) + PASO_SUBIDA):
        return None
    m = multiplicador_sostenible(a_subida, arg_subida, caso.economia.plataforma.confianza_subida)
    if m <= Decimal(1) + PASO_SUBIDA:
        return None
    destino = min(bid * m, techo).quantize(CENTAVO, rounding=ROUND_FLOOR)
    if destino < caso.bid.piso:
        # El dueno elevo el piso del goal por encima del nivel que la
        # evidencia sostiene: el salto no cabe en el rango y subir hasta el
        # piso seria inventarle evidencia. Lo decide el R6 de hoy (que aplica
        # su ajuste de rango).
        return None
    return destino if destino > bid * (Decimal(1) + PASO_SUBIDA) else None


def presupuesto_topado_de(p: PresupuestoCampana | None) -> bool | None:
    """BUD (TEMPORADA 01 C): True si la campana gasto >= topada.pct de su
    presupuesto en al menos topada.dias de los ultimos 7 con ingesta; None si el
    dato no alcanza a medir la racha (o falta el presupuesto vigente); False sin
    presupuesto (la lectura no corre: semantica de hoy). UNA fuente: la usan
    `presupuesto_topado` (el caso congelado) y las reactivaciones (bloque F)."""
    if p is None:
        return False
    if p.presupuesto_diario is None or p.dias_topados is None or p.dias_medidos < p.topada.dias:
        return None
    return p.dias_topados >= p.topada.dias


def presupuesto_topado(caso: CasoHoja) -> bool | None:
    """BUD sobre el presupuesto congelado del caso (TEMPORADA 01 C)."""
    return presupuesto_topado_de(caso.presupuesto)
