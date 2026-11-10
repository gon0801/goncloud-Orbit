#!/usr/bin/env bash
# Comprueba, sin escribir nada, que el código que corre en producción trae (o ya no trae) un texto.
# Uso: bash docs/evidencia/arreglos-01/desplegado.sh texto1 texto2      -> sale 0 si están todos
#      bash docs/evidencia/arreglos-01/desplegado.sh --no texto1 texto2 -> sale 0 si no está ninguno
# Busca en los .py del paquete `app` dentro del contenedor orbit-app-1.
set -euo pipefail
quiero=si
if [ "${1:-}" = "--no" ]; then quiero=no; shift; fi
[ "$#" -gt 0 ] || { echo "uso: desplegado.sh [--no] texto..." >&2; exit 2; }
for texto in "$@"; do
  n=$(printf '%s' "$texto" | ssh goncloud "docker exec -i orbit-app-1 python -c 'import sys,pathlib,app; t=sys.stdin.read(); print(sum(t in p.read_text(errors=\"ignore\") for p in pathlib.Path(app.__file__).parent.rglob(\"*.py\")))'")
  if [ "$quiero" = si ] && [ "$n" -eq 0 ]; then echo "falta en producción: $texto" >&2; exit 1; fi
  if [ "$quiero" = no ] && [ "$n" -gt 0 ]; then echo "sigue en producción ($n archivos): $texto" >&2; exit 1; fi
done
