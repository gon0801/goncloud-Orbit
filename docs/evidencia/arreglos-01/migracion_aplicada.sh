#!/usr/bin/env bash
# Comprueba, sin escribir nada, que una migración está aplicada en producción.
# Corre el SELECT de su cabecera `-- sonda-aplicada:` con lee_prod.sh; sale 0 solo si devuelve verdadero.
# Uso (desde la raíz del repo): bash docs/evidencia/arreglos-01/migracion_aplicada.sh arreglos01_impulso_desarmado
set -euo pipefail
aqui=$(cd "$(dirname "$0")" && pwd)
archivo=$(ls migrations/*_"$1".sql | grep -v _reversa_ | head -n 1)
sonda=$(sed -n '1,10s/^-- sonda-aplicada: *//p' "$archivo" | head -n 1)
[ -n "$sonda" ] || { echo "sin cabecera sonda-aplicada: $archivo" >&2; exit 2; }
salida=$(printf '%s\n' "$sonda" | bash "$aqui/lee_prod.sh")
case "$salida" in True|t|true) exit 0 ;; *) echo "no aplicada ($archivo): $salida" >&2; exit 1 ;; esac
