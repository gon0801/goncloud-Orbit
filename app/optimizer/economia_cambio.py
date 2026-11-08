"""Regla pura de repeticion con evidencia economica del ultimo cambio (M6).

La trayectoria vigente frena repetir un recorte que costo trafico (R16) y una
subida que no trajo trafico (R16), pero no mira el dinero. Este modulo agrega
la evidencia economica madura del episodio M5, solo cuando es comparable y
sigue aplicable al ultimo cambio:

- Un recorte puede repetirse si conservo las ventas y mejoro la economia
  (ACoS posterior menor). En ese caso el freno de trafico de R16 no aplica:
  el recorte podo trafico que no vendia. Si no conservo ventas o no mejoro la
  economia, se frena con `recorte_sin_economia`.
- Una subida se frena con `subida_sin_economia` cuando el trafico adicional no
  compenso el gasto: no crecio la venta, o con margen compatible la
  contribucion estimada no mejoro, o sin margen el ACoS empeoro y supera el
  target.
- Sin episodio maduro, comparable y aplicable no hay juicio nuevo: la
  trayectoria vigente (R14-R17) decide como antes. La falta de datos no es un
  permiso para gastar ni un bloqueo para todas las hojas.

Sin IO, reloj, float ni red. El target es el ya resuelto para el caso.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Literal

from app.optimizer.caso import (
    PRECISION,
    VERSION_ECONOMIA_CAMBIO,
    EpisodioEconomico,
    VentanaEconomica,
)

MOTIVO_RECORTE_SIN_ECONOMIA = "recorte_sin_economia"
MOTIVO_SUBIDA_SIN_ECONOMIA = "subida_sin_economia"

_CIEN = Decimal(100)


@dataclass(frozen=True)
class JuicioRepeticion:
    """`sin_evidencia`: la trayectoria vigente sigue decidiendo.

    `permite`: el episodio maduro respalda repetir un recorte (R16 no aplica).
    `frena`: el episodio maduro se opone a la repeticion, con su motivo.
    """

    veredicto: Literal["sin_evidencia", "permite", "frena"]
    motivo: str | None = None


SIN_EVIDENCIA = JuicioRepeticion("sin_evidencia")
PERMITE = JuicioRepeticion("permite")


def juicio_repeticion(
    episodio: EpisodioEconomico | None,
    *,
    target_acos_pct: Decimal,
    factor: Decimal,
) -> JuicioRepeticion:
    """Juicio de repeticion para un movimiento de la misma direccion.

    El llamador solo lo usa con `factor` del mismo signo que el ultimo cambio.
    Un factor cero o un episodio no utilizable devuelven `sin_evidencia`.
    """
    if not _utilizable(episodio):
        return SIN_EVIDENCIA
    assert episodio is not None
    pre = episodio.pre
    post = episodio.post
    assert post is not None
    if not _ventanas_medidas(pre, post):
        return SIN_EVIDENCIA
    if factor < 0:
        return _juicio_recorte(pre, post)
    if factor > 0:
        return _juicio_subida(episodio, pre, post, target_acos_pct)
    return SIN_EVIDENCIA


def _juicio_recorte(pre: VentanaEconomica, post: VentanaEconomica) -> JuicioRepeticion:
    assert pre.venta is not None and post.venta is not None
    if pre.venta <= 0:
        return SIN_EVIDENCIA
    conserva_ventas = post.venta >= pre.venta
    if not conserva_ventas:
        return JuicioRepeticion("frena", MOTIVO_RECORTE_SIN_ECONOMIA)
    acos_pre = _acos(pre)
    acos_post = _acos(post)
    if acos_pre is None or acos_post is None:
        return SIN_EVIDENCIA
    if acos_post < acos_pre:
        return PERMITE
    return JuicioRepeticion("frena", MOTIVO_RECORTE_SIN_ECONOMIA)


def _juicio_subida(
    episodio: EpisodioEconomico,
    pre: VentanaEconomica,
    post: VentanaEconomica,
    target_acos_pct: Decimal,
) -> JuicioRepeticion:
    assert pre.venta is not None and post.venta is not None
    if pre.venta <= 0:
        return SIN_EVIDENCIA
    if post.venta <= pre.venta:
        # El trafico adicional no compro una sola venta mas.
        return JuicioRepeticion("frena", MOTIVO_SUBIDA_SIN_ECONOMIA)
    if episodio.contribucion_pre is not None and episodio.contribucion_post is not None:
        if episodio.contribucion_post > episodio.contribucion_pre:
            return SIN_EVIDENCIA
        return JuicioRepeticion("frena", MOTIVO_SUBIDA_SIN_ECONOMIA)
    acos_pre = _acos(pre)
    acos_post = _acos(post)
    if acos_pre is None or acos_post is None:
        return SIN_EVIDENCIA
    if acos_post <= target_acos_pct:
        # La economia sigue dentro del goal: el gasto extra esta compensado.
        return SIN_EVIDENCIA
    if acos_post <= acos_pre:
        # No empeoro la economia, aunque el margen no alcance para contribuir.
        return SIN_EVIDENCIA
    return JuicioRepeticion("frena", MOTIVO_SUBIDA_SIN_ECONOMIA)


def _utilizable(episodio: EpisodioEconomico | None) -> bool:
    return (
        episodio is not None
        and episodio.version == VERSION_ECONOMIA_CAMBIO
        and episodio.estado == "maduro"
        and episodio.motivo is None
        and episodio.aplicable
        and episodio.post is not None
    )


def _ventanas_medidas(pre: VentanaEconomica, post: VentanaEconomica) -> bool:
    """Solo con gasto, venta, pedidos y clics conocidos en AMBAS ventanas.

    Un pre sin gasto no tiene ACoS que comparar; un post sin venta no demuestra
    conservacion. No se rellenan None con cero (regla 3).
    """
    if pre.gasto is None or pre.venta is None or pre.pedidos is None or pre.clics is None:
        return False
    if post.gasto is None or post.venta is None or post.pedidos is None or post.clics is None:
        return False
    return pre.gasto > 0


def _acos(ventana: VentanaEconomica) -> Decimal | None:
    if ventana.venta is None or ventana.gasto is None or ventana.venta <= 0:
        return None
    with localcontext(prec=PRECISION):
        return _CIEN * ventana.gasto / ventana.venta
