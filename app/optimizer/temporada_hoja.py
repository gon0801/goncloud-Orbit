"""Reglas por keyword (hoja) de Temporada: reduccion, pendiente, frontera y techos.

Extraido de `app/optimizer/temporada.py` (Ola D2). PURO: sin IO, sin reloj, Decimal.
`evalua_hoja()` resuelve `reduccion_vigente` UNA sola vez para derivar tanto el
`Pendiente` como la `FronteraHoja`.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import TYPE_CHECKING, Literal

from app.optimizer import recuperacion
from app.optimizer.centavos import CENTAVO, centavo_hacia
from app.optimizer.temporada_config import (
    EpisodioVigente,
    Estacion,
    Fase,
    FinReduccion,
    FronteraHoja,
)

if TYPE_CHECKING:
    from app.optimizer.caso import CasoHoja
    from app.optimizer.recuperacion import CadenaBid, ReduccionVigente

DIAS_CERCA = 90  # la cerca caduca a los 90 dias (caso.DIAS_LOOKBACK)


@dataclass(frozen=True)
class EntradaPendiente:
    destino: Decimal


@dataclass(frozen=True)
class AjustePendiente:
    destino: Decimal


@dataclass(frozen=True)
class RestauracionPendiente:
    nivel: Decimal
    episodio_id: int


SinPendienteMotivo = Literal[
    "fuera_de_temporada",
    "ya_entro",
    "ya_arriba",
    "dueno_decidio",
    "bid_heredado",
    "sin_centavo_factible",
    "dato_faltante",
]


@dataclass(frozen=True)
class SinPendiente:
    motivo: SinPendienteMotivo


Pendiente = EntradaPendiente | AjustePendiente | RestauracionPendiente | SinPendiente


def _centavos_del_rango(piso: Decimal, techo: Decimal) -> tuple[Decimal, Decimal] | None:
    piso_c = piso.quantize(CENTAVO, rounding=ROUND_CEILING)
    techo_c = techo.quantize(CENTAVO, rounding=ROUND_FLOOR)
    if piso_c > techo_c:
        return None
    return piso_c, techo_c


def destino_entrada(
    bid: Decimal, reduccion: Decimal, piso: Decimal, techo: Decimal
) -> Decimal | None:
    rango = _centavos_del_rango(piso, techo)
    if rango is None:
        return None
    piso_c, techo_c = rango
    d = centavo_hacia(bid * (Decimal(1) - reduccion), desde=bid)
    d = min(max(d, piso_c), techo_c)
    if bid - d < CENTAVO:
        return None
    return d


def inicio_episodio(episodio: EpisodioVigente | None, dia: dt.date) -> dt.datetime:
    base = episodio.abierto_el.astimezone(dt.UTC).date() if episodio is not None else dia
    return dt.datetime.combine(base, dt.time(0), tzinfo=dt.UTC)


def nivel_de_regreso(
    red: ReduccionVigente, bid: Decimal | None, piso: Decimal, techo: Decimal
) -> Decimal | None:
    rango = _centavos_del_rango(piso, techo)
    if rango is None:
        return None
    piso_c, techo_c = rango
    if red.solo_temporada_despues or bid is None or bid >= red.ultimo_bid:
        factor = Decimal(1)
    else:
        factor = bid / red.ultimo_bid
    nivel = (red.base * factor).quantize(CENTAVO, rounding=ROUND_FLOOR)
    return min(max(nivel, piso_c), techo_c)


def techo_de_baja(caso: CasoHoja, *, cadena: CadenaBid | None = None) -> Decimal | None:
    t = caso.temporada
    if t is None or t.frontera.fase_hoja != "baja" or t.frontera.fin is not None:
        return None
    cad = cadena if cadena is not None else recuperacion.cadena_bid(caso)
    red = cad.reduccion if cad is not None else None
    if red is None:
        return None
    r_efectiva = Decimal(1) - red.ultimo_bid / red.base
    techo_eco = recuperacion.techo_conocido(caso, hasta=red.primer_el, cadena=cad) or red.base
    techo_eco = max(techo_eco, red.base)
    return (techo_eco * (Decimal(1) - r_efectiva)).quantize(CENTAVO, rounding=ROUND_FLOOR)


def meta_del_juicio(caso: CasoHoja) -> Decimal:
    base = caso.economia.target_acos_pct
    t = caso.temporada
    if t is None or t.frontera.fase_hoja != "alta" or t.meta_manual:
        return base
    return max(base, t.meta_alta_pct)


def equilibrio_del_juicio(caso: CasoHoja) -> Decimal | None:
    eq = caso.economia.plataforma.equilibrio_acos_pct
    if eq is None or eq <= 0:
        return eq
    t = caso.temporada
    if t is None or t.frontera.fase_hoja != "alta" or t.meta_manual:
        return eq
    return max(eq, meta_del_juicio(caso))


def reduccion_vigente(
    cadena: CadenaBid,
    *,
    en_baja: bool,
    dia: dt.date,
    episodio: EpisodioVigente | None,
    bid: Decimal | None,
    piso: Decimal,
    techo: Decimal,
) -> tuple[ReduccionVigente | None, FinReduccion | None]:
    """La unica resolucion de «¿esta hoja sigue reducida?»."""
    red = cadena.reduccion
    if red is None or (episodio is not None and red.episodio_id == episodio.id):
        return red, None
    manos = [m for m in cadena.manos_del_dueno if m > red.ultimo_el]
    if manos or cadena.dueno_al_final:
        ahora = cadena.dueno_al_final or (
            en_baja and bool(manos) and max(manos) >= inicio_episodio(episodio, dia)
        )
        return None, FinReduccion(
            red.episodio_id,
            "mano_del_dueno",
            excluye_abierto=bool(en_baja and ahora),
            bid_visto=bid,
        )
    nivel = nivel_de_regreso(red, bid, piso, techo)
    if bid is not None and nivel is not None and bid >= nivel:
        return None, FinReduccion(red.episodio_id, "ya_arriba", excluye_abierto=False)
    return red, None


def es_terreno_del_dueno(
    estacion: Estacion,
    cadena: CadenaBid | None,
    *,
    red: ReduccionVigente | None,
    fin: FinReduccion | None,
    episodio: EpisodioVigente | None,
    excluida_en: frozenset[int],
) -> bool:
    """Unica fuente de «terreno del dueno» (decision 7 del plan): en esta
    temporada la hoja no recibe bajada, regreso, subida rapida ni reglas de la
    baja (retencion de recortes o multiplicador de cortes). Cubre exclusion
    persistida, bid cambiado fuera (`dueno_al_final`) y manos fechadas del
    dueno (`regreso_del_dueno` y `reactivacion_manual`)."""
    manos = cadena.manos_del_dueno if cadena is not None else ()
    dueno_al_final = cadena.dueno_al_final if cadena is not None else False
    if fin is not None and fin.excluye_abierto:
        return True
    if red is not None:
        return (
            red.episodio_id in excluida_en
            or any(m > red.ultimo_el for m in manos)
            or dueno_al_final
        )
    if estacion.fase != "baja":
        return False
    return (
        (episodio is not None and episodio.id in excluida_en)
        or any(m >= inicio_episodio(episodio, estacion.dia) for m in manos)
        or dueno_al_final
    )


def en_terreno_del_dueno(caso: CasoHoja, cadena: CadenaBid | None) -> bool:
    """Puerta de «terreno del dueno» sobre un `CasoHoja` (la usa R6r en
    `recuperacion.destino_recuperacion` para compartir exactamente la misma
    regla que `evalua_hoja` y `_pendiente_resuelto`). Conserva el veto cuando
    el episodio aun abierto figura en `excluida_en`."""
    t = caso.temporada
    if t is None:
        return False
    if t.episodio is not None and t.episodio.id in t.excluida_en:
        return True
    if cadena is None:
        red, fin = None, None
    else:
        red, fin = reduccion_vigente(
            cadena,
            en_baja=(t.estacion.fase == "baja"),
            dia=t.estacion.dia,
            episodio=t.episodio,
            bid=caso.bid.valor,
            piso=caso.bid.piso,
            techo=caso.bid.techo,
        )
    return es_terreno_del_dueno(
        t.estacion,
        cadena,
        red=red,
        fin=fin,
        episodio=t.episodio,
        excluida_en=t.excluida_en,
    )


def _pendiente_resuelto(
    estacion: Estacion,
    cadena: CadenaBid | None,
    *,
    red: ReduccionVigente | None,
    fin: FinReduccion | None,
    episodio: EpisodioVigente | None,
    excluida_en: frozenset[int],
    bid: Decimal | None,
    piso: Decimal,
    techo: Decimal,
) -> Pendiente:
    if bid is None:
        return SinPendiente("bid_heredado")
    en_baja = estacion.fase == "baja"
    if es_terreno_del_dueno(
        estacion, cadena, red=red, fin=fin, episodio=episodio, excluida_en=excluida_en
    ):
        return SinPendiente("dueno_decidio")
    if red is not None:
        if en_baja:
            if (
                episodio is not None
                and red.episodio_id == episodio.id
                and red.solo_temporada_despues
            ):
                d = destino_entrada(red.base, estacion.reduccion_baja, piso, techo)
                if d is not None and abs(d - bid) >= CENTAVO:
                    return AjustePendiente(d)
            return SinPendiente("ya_entro")
        nivel = nivel_de_regreso(red, bid, piso, techo)
        if nivel is None:
            return SinPendiente("dato_faltante")
        if bid >= nivel:
            return SinPendiente("ya_arriba")
        return RestauracionPendiente(nivel, red.episodio_id)
    if not en_baja:
        return SinPendiente("fuera_de_temporada")
    d = destino_entrada(bid, estacion.reduccion_baja, piso, techo)
    if d is None:
        return SinPendiente("sin_centavo_factible")
    return EntradaPendiente(d)


def _pendiente_desde(
    estacion: Estacion,
    cadena: CadenaBid | None,
    *,
    episodio: EpisodioVigente | None,
    excluida_en: frozenset[int],
    bid: Decimal | None,
    piso: Decimal,
    techo: Decimal,
) -> Pendiente:
    if bid is None:
        return SinPendiente("bid_heredado")
    en_baja = estacion.fase == "baja"
    if cadena is None:
        red, fin = None, None
    else:
        red, fin = reduccion_vigente(
            cadena,
            en_baja=en_baja,
            dia=estacion.dia,
            episodio=episodio,
            bid=bid,
            piso=piso,
            techo=techo,
        )
    return _pendiente_resuelto(
        estacion,
        cadena,
        red=red,
        fin=fin,
        episodio=episodio,
        excluida_en=excluida_en,
        bid=bid,
        piso=piso,
        techo=techo,
    )


def pendiente(caso: CasoHoja) -> Pendiente:
    t = caso.temporada
    if t is None:
        raise ValueError("pendiente exige caso.temporada (decide solo la llama con el marcador)")
    bid = caso.bid.valor
    if bid is None:
        return SinPendiente("bid_heredado")
    cad = recuperacion.cadena_bid(caso)
    if cad is None:
        return SinPendiente("dato_faltante")
    return _pendiente_desde(
        t.estacion,
        cad,
        episodio=t.episodio,
        excluida_en=t.excluida_en,
        bid=bid,
        piso=caso.bid.piso,
        techo=caso.bid.techo,
    )


def cerca_hoja_de(cadena: CadenaBid | None, dia: dt.date) -> dt.date | None:
    if cadena is None:
        return None
    temporadas = [c for c in cadena.cambios if c.origen == "temporada"]
    if not temporadas:
        return None
    cerca = temporadas[-1].confirmado_el.astimezone(dt.UTC).date() + dt.timedelta(days=1)
    if cerca < dia - dt.timedelta(days=DIAS_CERCA):
        return None
    return cerca


def evalua_hoja(
    estacion: Estacion,
    cadena: CadenaBid | None,
    *,
    episodio: EpisodioVigente | None,
    excluida_en: frozenset[int],
    cerca_plataforma: dt.date | None,
    bid: Decimal | None,
    piso: Decimal,
    techo: Decimal,
) -> tuple[Pendiente, FronteraHoja]:
    """Evalua una hoja UNA sola vez: devuelve `(Pendiente, FronteraHoja)`."""
    en_baja = estacion.fase == "baja"
    dueno_al_final = cadena.dueno_al_final if cadena is not None else False
    if cadena is None:
        red, fin = None, None
    else:
        red, fin = reduccion_vigente(
            cadena,
            en_baja=en_baja,
            dia=estacion.dia,
            episodio=episodio,
            bid=bid,
            piso=piso,
            techo=techo,
        )
    p = _pendiente_resuelto(
        estacion,
        cadena,
        red=red,
        fin=fin,
        episodio=episodio,
        excluida_en=excluida_en,
        bid=bid,
        piso=piso,
        techo=techo,
    )
    terreno_de_david = en_baja and es_terreno_del_dueno(
        estacion, cadena, red=red, fin=fin, episodio=episodio, excluida_en=excluida_en
    )
    sin_nivel = red is not None and _centavos_del_rango(piso, techo) is None
    if terreno_de_david:
        fase_hoja: Fase = "normal"
    elif en_baja or isinstance(p, RestauracionPendiente) or sin_nivel:
        fase_hoja = "baja"
    else:
        fase_hoja = estacion.fase
    mult = estacion.mult_cortes_baja if fase_hoja == "baja" else Decimal(1)
    episodio_id = (
        red.episodio_id if red is not None else (episodio.id if episodio is not None else None)
    )
    cerca_hoja = cerca_hoja_de(cadena, estacion.dia)
    exclusion_hoy = (
        bid
        if (
            en_baja
            and dueno_al_final
            and bid is not None
            and (episodio is None or episodio.id not in excluida_en)
        )
        else None
    )
    return p, FronteraHoja(
        fase_hoja=fase_hoja,
        mult_cortes=mult,
        cerca_hoja=cerca_hoja,
        cerca_plataforma=cerca_plataforma,
        episodio_id=episodio_id,
        fin=fin,
        exclusion_hoy=exclusion_hoy,
    )


def frontera(
    estacion: Estacion,
    cadena: CadenaBid | None,
    *,
    episodio: EpisodioVigente | None,
    excluida_en: frozenset[int],
    cerca_plataforma: dt.date | None,
    bid: Decimal | None,
    piso: Decimal,
    techo: Decimal,
) -> FronteraHoja:
    """Una sola funcion para el ciclo (decidir) y la cola (liberar)."""
    return evalua_hoja(
        estacion,
        cadena,
        episodio=episodio,
        excluida_en=excluida_en,
        cerca_plataforma=cerca_plataforma,
        bid=bid,
        piso=piso,
        techo=techo,
    )[1]
