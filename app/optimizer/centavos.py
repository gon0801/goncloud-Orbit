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
