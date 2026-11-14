"""El centavo del motor de Ads (ARREGLOS 02 B). PURO.

El motor decide bids en centavos: lo decidido es lo que Amazon recibe.
`centavo_hacia` es la UNICA regla de redondeo de un paso (TEMPORADA 01
importa la misma funcion; si viviera en politica.py habria import
circular). Sin IO, sin reloj, sin imports de app: solo Decimal.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

CENTAVO = Decimal("0.01")


def centavo_hacia(destino: Decimal, *, desde: Decimal) -> Decimal:
    """El centavo mas cercano a `destino` que no se aleja de `desde` mas que
    `destino`. Un recorte redondea hacia arriba y una subida hacia abajo: el
    bid que recibe Amazon nunca se mueve mas que el paso decidido ni cruza
    un piso por redondeo."""
    modo = ROUND_CEILING if destino < desde else ROUND_FLOOR
    return destino.quantize(CENTAVO, rounding=modo)


def centavo_en_rango(
    destino: Decimal, *, desde: Decimal, piso: Decimal, techo: Decimal
) -> Decimal | None:
    """El centavo de `centavo_hacia` cuando queda dentro de [piso, techo]; None
    si el redondeo hacia `desde` lo saca del rango del goal (ARREGLOS 02 B, F1:
    un piso o techo que no es centavo puede dejar al redondeo fuera; el centavo
    del otro lado se aleja del bid de hoy mas que el paso, asi que ningun bid
    de Amazon cumple a la vez el paso y el rango y el ajuste no sale)."""
    redondeado = centavo_hacia(destino, desde=desde)
    return redondeado if piso <= redondeado <= techo else None
