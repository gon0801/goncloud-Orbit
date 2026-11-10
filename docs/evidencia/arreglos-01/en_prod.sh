#!/usr/bin/env bash
# Corre un script de Python dentro del contenedor de producción, con el código desplegado.
# Es para comprobar funciones puras del motor (sin base ni red): el script no debe escribir nada.
# Uso: bash docs/evidencia/arreglos-01/en_prod.sh docs/evidencia/arreglos-01/B/prueba.py
# Sale con el código del script: 0 si todas sus aserciones pasan.
set -euo pipefail
[ "$#" -eq 1 ] && [ -s "$1" ] || { echo "uso: en_prod.sh <script.py>" >&2; exit 2; }
ssh goncloud "docker exec -i orbit-app-1 python -" < "$1"
