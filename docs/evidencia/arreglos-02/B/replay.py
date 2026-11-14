"""Rejuega las decisiones niveles_v3 con el codigo de ARREGLOS 02 B (fila B.4).

Lee por la entrada estandar la salida de lee_prod.sh (decisiones.sql): una
linea por decision, con id, kind, new_value e inputs::text separados por la
barra vertical; el inputs es el resto de la linea (partida con maxsplit=3,
por si el JSON trae barras). Corre reproduce(json.loads(inputs)), compara
tipo y bid con lo guardado, imprime las que cambian y al final
`distintas <n>`; sale 1 si n > 0.

Con el marcador revision ausente en las decisiones viejas, cada una se
rejuega con la semantica de su era: el veredicto guardado no cambia.
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal

from app.optimizer.replay import reproduce


def main() -> int:
    distintas = 0
    for linea in sys.stdin:
        linea = linea.rstrip("\n")
        if not linea:
            continue
        id_, kind, new_value, inputs = linea.split("|", maxsplit=3)
        guardado = None if new_value in ("", "None") else Decimal(new_value)
        tipo, nuevo, _moneda = reproduce(json.loads(inputs))
        if tipo != kind or nuevo != guardado:
            distintas += 1
            print(f"decision {id_}: guardado {kind} {guardado}, rejuega {tipo} {nuevo}")
    print(f"distintas {distintas}")
    return 1 if distintas else 0


if __name__ == "__main__":
    sys.exit(main())
