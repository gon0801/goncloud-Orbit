#!/usr/bin/env bash
# Corre cada bloque de BASE/previa.sql contra produccion, SOLO LECTURA, por
# docs/evidencia/arreglos-01/lee_prod.sh (rol orbit_read, transaccion read_only).
# Uso desde la raiz: bash docs/evidencia/arreglos-05/BASE/previa.sh > previa.txt
# ORBIT_PREVIA_LEE=<script> sustituye a lee_prod.sh (p. ej. uno que imprima su entrada)
# para ver que se enviaria, sin tocar produccion.
# Formato de previa.sql: cada bloque empieza con "-- @<etiqueta>" y trae UNA
# sentencia. "-- @sonda <archivo-de-migracion>" corre su linea sonda-aplicada.
# Un bloque que falla se imprime como ERROR y la corrida termina distinta de 0.
set -euo pipefail

raiz=$(cd -- "$(dirname -- "$0")/../../../.." && pwd)
lee=${ORBIT_PREVIA_LEE:-$raiz/docs/evidencia/arreglos-01/lee_prod.sh}
sql="$raiz/docs/evidencia/arreglos-05/BASE/previa.sql"

bloque() {
  awk -v e="$1" '$0 == "-- @" e { p = 1; next } /^-- @/ { p = 0 } p' "$sql"
}

sonda_de() {
  sed -n 's/^-- sonda-aplicada: *//p' "$raiz/migrations/$1" | head -n 1 | tr -d '\r'
}

echo "# Lectura previa de produccion (solo SELECT). SHA $(cd "$raiz" && git rev-parse HEAD)"
fallos=0
while IFS= read -r linea; do
  if [[ $linea == "-- @sonda "* ]]; then
    etiqueta="sonda_${linea#-- @sonda }"
    consulta=$(sonda_de "${linea#-- @sonda }")
  else
    etiqueta=${linea#-- @}
    consulta=$(bloque "$etiqueta")
  fi
  echo
  echo "== $etiqueta"
  if [[ -z ${consulta:-} ]]; then
    echo "ERROR: bloque vacio"
    fallos=$((fallos + 1))
    continue
  fi
  if salida=$(printf '%s\n' "$consulta" | bash "$lee" 2>&1); then
    printf '%s\n' "$salida"
  else
    echo "ERROR: $salida"
    fallos=$((fallos + 1))
  fi
done < <(grep -E '^-- @' "$sql")
echo
echo "# bloques con error: $fallos"
[[ $fallos == 0 ]]
