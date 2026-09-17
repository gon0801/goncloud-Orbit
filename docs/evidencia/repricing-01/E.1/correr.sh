#!/usr/bin/env bash
# ORBIT · fase 8 · repricing-01 · E.1
#
# Corredor de las consultas de solo lectura de E.1 contra producción.
# NO lo corre el implementador de esta tarea (el clasificador de permisos
# de esa sesión negó la lectura de producción el 2026-09-17); lo corre el
# dueño, con:
#
#   ! bash docs/evidencia/repricing-01/E.1/correr.sh
#
# Cada consulta corre dentro de BEGIN READ ONLY (aborta si algo intenta
# escribir) y su salida se guarda en salidas/<nombre>.txt.

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONSULTAS_DIR="$DIR/consultas"
SALIDAS_DIR="$DIR/salidas"
mkdir -p "$SALIDAS_DIR"

# Candado: ninguna consulta puede contener una palabra de escritura.
# grep -iE con salida = aborta ANTES de tocar producción.
if grep -liE '\b(insert|update|delete|truncate|alter|drop|create|grant|copy)\b' "$CONSULTAS_DIR"/*.sql; then
    echo "ATORADO: una o más consultas en $CONSULTAS_DIR contienen una palabra de escritura prohibida (ver arriba)." >&2
    exit 1
fi

for archivo in "$CONSULTAS_DIR"/*.sql; do
    nombre="$(basename "${archivo%.sql}")"
    echo "== $nombre =="
    { echo 'BEGIN READ ONLY;'; cat "$archivo"; } | ssh goncloud '
        set -eu
        DSN=$(docker exec orbit-app-1 printenv ORBIT_DSN_READ)
        test -n "$DSN" || { echo "ATORADO: ORBIT_DSN_READ vacio"; exit 1; }
        docker exec -i orbit-db-1 psql "$DSN" -X -P pager=off -tA -v ON_ERROR_STOP=1
    ' > "$SALIDAS_DIR/$nombre.txt"
done

echo "Listo. Salidas en $SALIDAS_DIR/"
