"""Propuestas de presupuesto de temporada (TEMPORADA 01 G). PURO.

Montos absolutos, uno por momento: baja, fin de la baja y alta sin
presupuesto. Sin dato no se inventa: la propuesta es None. No escribe nada;
el unico camino de escritura sigue siendo el de ajustes de campana con
`APLICAR AJUSTE` del dueno.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from typing import TYPE_CHECKING, Literal

from app.formato import dinero
from app.optimizer.temporada_config import CampanaTopada, ConfigTemporada, Estacion

if TYPE_CHECKING:
    from app.temporada_io import EpisodioPresupuesto

CENTAVO = Decimal("0.01")
MotivoPropuesta = Literal["baja", "fin_de_baja", "alta_sin_presupuesto"]


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


def _vigente_en(historial: tuple[tuple[dt.date, Decimal], ...], dia: dt.date) -> Decimal | None:
    valor = None
    for vigente_desde, monto in historial:
        if vigente_desde > dia:
            break
        valor = monto
    return valor


def _frase(motivo: MotivoPropuesta, actual: Decimal, propuesto: Decimal, moneda: str) -> str:
    sube = propuesto > actual
    verbo = "subir" if sube else "bajar"
    prefijo = {
        "baja": "Temporada baja: ",
        "fin_de_baja": "Fin de la baja: ",
        "alta_sin_presupuesto": "Temporada alta: se queda sin presupuesto; ",
    }[motivo]
    return f"{prefijo}{verbo} de {dinero(actual)} a {dinero(propuesto)} {moneda} al día"


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
    # el mismo dia son dos filas y la ultima manda; ordenar por (fecha, monto)
    # perderia cual fue el ultimo aplicado.
    ordenado = tuple(historial)
    hoy_vigente = _vigente_en(ordenado, hoy)
    if hoy_vigente is None or hoy_vigente <= 0:
        return None
    antes = _vigente_en(ordenado, episodio.abierto_el) if episodio is not None else None
    candidatas: list[tuple[Decimal, MotivoPropuesta, int | None]] = []

    if episodio is not None and antes is not None and antes > 0:
        bajo = _q(antes * (Decimal(1) - config.reduccion_baja))
        cambios = tuple((d, m) for d, m in ordenado if d >= episodio.abierto_el)
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
        piso = antes if antes is not None else Decimal(0)
        subidas = tuple(
            (d, m) for d, m in ordenado if d >= inicio_bloque_alto and m is not None and m > piso
        )
        if not subidas:
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
