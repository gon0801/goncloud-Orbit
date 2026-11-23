"""Propuestas de presupuesto de temporada (TEMPORADA 01 G). PURO.

Montos absolutos, uno por momento: baja, fin de la baja y alta sin
presupuesto. Sin dato no se inventa: la propuesta es None. No escribe nada;
el unico camino de escritura sigue siendo el de ajustes de campana con
`APLICAR AJUSTE` del dueno.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Literal

from app.optimizer.temporada_config import (
    CampanaTopada,
    ConfigTemporada,
    EpisodioPresupuesto,
    Estacion,
)

CENTAVO = Decimal("0.01")
MotivoPropuesta = Literal["baja", "fin_de_baja", "alta_sin_presupuesto"]


def presupuesto_en_dia(
    historial: Sequence[tuple[dt.date, Decimal | None]],
    dia: dt.date,
) -> Decimal | None:
    """Ultimo presupuesto observado en o antes de `dia` (UTC); None si no hay."""
    vigente = None
    for vigente_desde, presupuesto in historial:
        if vigente_desde <= dia:
            vigente = presupuesto
        else:
            break
    return vigente


def cuenta_dias_topados(
    ultimos_7: Sequence[dt.date],
    gasto_por_dia: Mapping[dt.date, Decimal | None],
    historial: Sequence[tuple[dt.date, Decimal | None]],
    topada: CampanaTopada,
) -> int | None:
    """Unica definicion de «dias al tope» en los ultimos 7 dias con ingesta
    (compartida por el motor, «Donde poner el dinero» y los avisos de Telegram).

    Compara el gasto de cada dia de `ultimos_7` contra el presupuesto vigente
    ese mismo dia (`presupuesto_en_dia`). Un gasto NULL en algun dia de la
    ventana (dato venenoso) devuelve None (regla 3: nunca inventar 0)."""
    if any(dia in gasto_por_dia and gasto_por_dia[dia] is None for dia in ultimos_7):
        return None
    topados = 0
    for dia in ultimos_7:
        gasto_dia = gasto_por_dia.get(dia, Decimal(0))
        assert gasto_dia is not None
        presup_dia = presupuesto_en_dia(historial, dia)
        if presup_dia is not None and gasto_dia >= topada.pct * presup_dia:
            topados += 1
    return topados


def _dinero(valor: Decimal) -> str:
    """Formato puro de dinero con miles y 2 decimales (espejo de app.formato.dinero)."""
    return f"{valor:,.2f}"


def _q(valor: Decimal) -> Decimal:
    """Monto absoluto en centavos, HALF_EVEN (mismo redondeo que la pantalla)."""
    return valor.quantize(CENTAVO, rounding=ROUND_HALF_EVEN)


@dataclass(frozen=True)
class PropuestaPresupuesto:
    actual: Decimal
    propuesto: Decimal
    motivo: MotivoPropuesta
    episodio_id: int | None
    frase: str

    def como_dict(self) -> dict:
        return {
            "actual": str(self.actual),
            "propuesto": str(self.propuesto),
            "motivo": self.motivo,
            "episodio_id": self.episodio_id,
            "frase": self.frase,
        }


def _transiciones_presupuesto(
    historial: tuple[tuple[dt.date, Decimal | None], ...],
    hasta: dt.date,
) -> tuple[tuple[dt.date, Decimal | None], ...]:
    """Colapsa observaciones consecutivas con el mismo presupuesto (p. ej.
    cuando solo cambio un placement o la estrategia en
    `ads_campana_config_observation`), quedandose con las transiciones reales
    hasta `hasta` inclusive en orden cronologico."""
    salida: list[tuple[dt.date, Decimal | None]] = []
    for dia, monto in historial:
        if dia > hasta:
            break
        if salida and salida[-1][1] == monto:
            continue
        salida.append((dia, monto))
    return tuple(salida)


def _base_y_cambios(
    transiciones: tuple[tuple[dt.date, Decimal | None], ...],
    corte: dt.date,
) -> tuple[Decimal | None, tuple[tuple[dt.date, Decimal | None], ...]]:
    """Presupuesto vigente al corte y cambios reales posteriores a esa base.

    Si hubo observaciones antes de `corte`, la ultima `< corte` es la base y
    toda transicion `>= corte` es un cambio. Si la primera observacion de la
    campana ocurrio exactamente el dia `corte`, esa primera fila es la base y
    solo cuentan las transiciones posteriores a ella."""
    idx_base = None
    for i, (dia, _monto) in enumerate(transiciones):
        if dia < corte:
            idx_base = i
        else:
            break
    if idx_base is not None:
        return transiciones[idx_base][1], transiciones[idx_base + 1 :]
    if transiciones and transiciones[0][0] == corte:
        return transiciones[0][1], transiciones[1:]
    return None, transiciones


def _frase(motivo: MotivoPropuesta, actual: Decimal, propuesto: Decimal, moneda: str) -> str:
    sube = propuesto > actual
    verbo = "subir" if sube else "bajar"
    prefijo = {
        "baja": "Temporada baja: ",
        "fin_de_baja": "Fin de la baja: ",
        "alta_sin_presupuesto": "Temporada alta: se queda sin presupuesto; ",
    }[motivo]
    return f"{prefijo}{verbo} de {_dinero(actual)} a {_dinero(propuesto)} {moneda} al día"


def propuesta_presupuesto(
    *,
    estacion: Estacion | None,
    config: ConfigTemporada | None,
    episodio: EpisodioPresupuesto | None,
    historial: tuple[tuple[dt.date, Decimal], ...],
    hoy: dt.date,
    inicio_bloque_alto: dt.date | None,
    dias_topados_7d: int | None,
    topada: CampanaTopada,
    techo_humano: Decimal,
    moneda: str,
) -> PropuestaPresupuesto | None:
    """Seccion «Presupuestos». Sin dato -> None. Una propuesta sola hace cero
    escrituras; regresa el monto absoluto y su frase."""
    if config is None or estacion is None or not historial:
        return None
    # El orden es el cronologico que entrega SQL (`observed_at`): dos cambios
    # el mismo dia son dos filas y la ultima manda; colapsamos repeticiones del
    # mismo presupuesto (cambios de placement/estrategia) para ver solo cambios
    # reales de presupuesto.
    transiciones = _transiciones_presupuesto(tuple(historial), hoy)
    if not transiciones:
        return None
    hoy_vigente = transiciones[-1][1]
    if hoy_vigente is None or hoy_vigente <= 0:
        return None
    antes, cambios = (
        _base_y_cambios(transiciones, episodio.abierto_el) if episodio is not None else (None, ())
    )
    candidatas: list[tuple[Decimal, MotivoPropuesta, int | None]] = []

    if episodio is not None and antes is not None and antes > 0:
        bajo = _q(antes * (Decimal(1) - config.reduccion_baja))
        if episodio.cerrado_el is None:
            if hoy_vigente > bajo and not cambios:
                candidatas.append((bajo, "baja", episodio.id))
        elif (hoy - episodio.cerrado_el).days < 30 and (
            len(cambios) == 1 and cambios[0][1] == bajo and hoy_vigente == bajo
        ):
            candidatas.append((antes, "fin_de_baja", episodio.id))

    if (
        inicio_bloque_alto is not None
        and dias_topados_7d is not None
        and dias_topados_7d >= topada.dias
        and config.aumento_presupuesto_alta > 0
    ):
        base_alto, cambios_alto = _base_y_cambios(transiciones, inicio_bloque_alto)
        bajo_episodio = (
            _q(antes * (Decimal(1) - config.reduccion_baja))
            if episodio is not None
            and episodio.cerrado_el is not None
            and antes is not None
            and antes > 0
            else None
        )
        previo = base_alto
        ya_subio = False
        for _d, m in cambios_alto:
            if m is None:
                continue
            es_restauracion = bajo_episodio is not None and previo == bajo_episodio and m == antes
            if previo is not None and m > previo and not es_restauracion:
                ya_subio = True
                break
            previo = m
        if not ya_subio:
            objetivo = _q(hoy_vigente * (Decimal(1) + config.aumento_presupuesto_alta))
            propuesto = min(objetivo, techo_humano)
            if propuesto > hoy_vigente:
                candidatas.append((propuesto, "alta_sin_presupuesto", None))

    if not candidatas:
        return None
    # Si aplican fin de la baja y alta, gana el monto mayor.
    propuesto, motivo, episodio_id = max(candidatas, key=lambda c: c[0])
    frase = _frase(motivo, hoy_vigente, propuesto, moneda)
    if motivo == "alta_sin_presupuesto" and propuesto == techo_humano:
        objetivo = _q(hoy_vigente * (Decimal(1) + config.aumento_presupuesto_alta))
        if objetivo > techo_humano:
            frase += " (topado por el techo humano)"
    return PropuestaPresupuesto(
        actual=hoy_vigente,
        propuesto=propuesto,
        motivo=motivo,
        episodio_id=episodio_id,
        frase=frase,
    )
