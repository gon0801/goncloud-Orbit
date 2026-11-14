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
    `destino`. Un recorte redondea hacia arriba y una subida hacia abajo: de
    los dos centavos vecinos, el que no agranda el paso."""
    modo = ROUND_CEILING if destino < desde else ROUND_FLOOR
    return destino.quantize(CENTAVO, rounding=modo)


def centavo_en_rango(
    destino: Decimal, *, desde: Decimal, piso: Decimal, techo: Decimal
) -> Decimal | None:
    """El centavo de `destino` que queda dentro de [piso, techo]; None solo si
    ninguno de los dos vecinos cabe.

    El rango del goal manda (ARREGLOS 02 B, F1 y F3): primero el centavo que
    no se aleja de `desde` mas que `destino`; si ese cae fuera del rango, el
    del lado contrario, que obedece al goal aunque se aleje un centavo mas
    (sin el, un bid fuera del rango quedaria congelado para siempre: ningun
    redondeo hacia el bid de hoy cabe). None exige un rango sin ningun
    centavo dentro; los clamps de direccion y magnitud de `_paso` siguen
    validando el resultado."""
    hacia = centavo_hacia(destino, desde=desde)
    if piso <= hacia <= techo:
        return hacia
    otro = destino.quantize(CENTAVO, rounding=ROUND_FLOOR if hacia > techo else ROUND_CEILING)
    return otro if piso <= otro <= techo else None
