#!/usr/bin/env bash
# K.1: lee produccion solo en lectura (rol orbit_read, transaccion read_only) por
# el lector comun de ARREGLOS 01 y deja una salida por consulta en esta carpeta.
# Cada bloque "-- ==" se ejecuta como un statement propio.
# Uso: bash docs/evidencia/arreglos-05/K/lee_previa.sh
set -euo pipefail
cd "$(dirname "$0")/../../../.." || exit 1
LEE="bash docs/evidencia/arreglos-01/lee_prod.sh"
BASE="docs/evidencia/arreglos-05/K"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

for nombre in cotizacion cargo escenario politica; do
  {
    echo "# K/$nombre.sql"
    echo "# generado: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "# base: $(git rev-parse HEAD)"
  } > "$BASE/$nombre.txt"
  awk -v dir="$TMP" '
    /^-- == / { n += 1; archivo = dir "/" n ".sql"; etiqueta = substr($0, 7); print etiqueta > (archivo ".lbl"); next }
    n > 0 { print > archivo }
  ' "$BASE/$nombre.sql"
  for archivo in $(ls -1 "$TMP"/*.sql | sort -V); do
    echo >> "$BASE/$nombre.txt"
    echo "-- $(cat "$archivo.lbl")" >> "$BASE/$nombre.txt"
    $LEE < "$archivo" >> "$BASE/$nombre.txt"
  done
  rm -f "$TMP"/*.sql "$TMP"/*.lbl
done
echo "listo: $(ls -1 "$BASE"/*.txt | wc -l) archivos"
