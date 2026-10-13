"""Filtro de saneo de las salidas de correr.sh (F.0, repo publico).

Copia de E.0 en el pipeline, con una etapa que E.0 no tiene: antes de tocar
disco, redacta de cada linea los ids de orden de Amazon y los SKUs con la
forma de odoo_sku (dos letras/digitos, guion, cuatro, guion, cuatro). Lo
demas pasa literal. Lo llama correr.sh entre ssh y el archivo de salida:
la salida cruda jamas llega a salidas/.

Uso: python3 sanear_salidas.py < archivo_crudo > archivo_sano
"""

from __future__ import annotations

import re
import sys

_ORDEN = re.compile(r"\b\d{3}-\d{7}-\d{7}\b")
_SKU = re.compile(r"\b[A-Z0-9]{2}-[A-Z0-9]{4}-[A-Z0-9]{4}\b")


def sanear(texto: str) -> str:
    return _SKU.sub("<sku>", _ORDEN.sub("<orden>", texto))


def main() -> int:
    sys.stdout.write(sanear(sys.stdin.read()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
