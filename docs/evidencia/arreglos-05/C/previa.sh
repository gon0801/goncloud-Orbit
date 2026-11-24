#!/usr/bin/env bash
# Corre cada bloque de C/previa.sql contra produccion, SOLO LECTURA, por
# docs/evidencia/arreglos-01/lee_prod.sh (rol de solo lectura, transaccion
# read_only). Uso desde la raiz:
#   bash docs/evidencia/arreglos-05/C/previa.sh > docs/evidencia/arreglos-05/C/previa.txt
# ORBIT_PREVIA_LEE=<script> sustituye a lee_prod.sh (p. ej. uno que imprima su
# entrada) para ver que se enviaria, sin tocar produccion.
# Formato de previa.sql: cada bloque empieza con "-- @<etiqueta>" y trae UNA
# sentencia. Sale distinto de 0 si previa.sql no trae exactamente los bloques
# obligatorios (se aborta antes de consultar), si un bloque falla o si al
# cierre no se procesaron todos.
set -euo pipefail

raiz=$(cd -- "$(dirname -- "$0")/../../../.." && pwd)
lee=${ORBIT_PREVIA_LEE:-$raiz/docs/evidencia/arreglos-01/lee_prod.sh}
sql=$raiz/docs/evidencia/arreglos-05/C/previa.sql

# Lo que la lectura debe cubrir. Un previa.sql vacio, ausente, truncado o al
# que le falte un marcador no puede salir con cero consultas y darse por leido.
obligatorios=(
  momento_utc tabla_madre_existe tabla_madre_columnas tabla_madre_conteos
  tabla_madre_ids reposiciones_ya_marcadas lotes tabla_nueva_ausente
  indices_y_triggers privilegios_tabla roles_servicio secuencia
  sonda_0096 referencias_a_la_madre
)

bloque() {
  awk -v e="$1" '$0 == "-- @" e { p = 1; next } /^-- @/ { p = 0 } p' "$sql"
}

etiquetas=()
while IFS= read -r linea; do
  [[ -n $linea ]] || continue
  etiquetas+=("${linea#-- @}")
done < <(grep -E '^-- @' "$sql" 2>/dev/null || true)

faltan=()
ajenos=()
for o in "${obligatorios[@]}"; do
  [[ " ${etiquetas[*]} " == *" $o "* ]] || faltan+=("$o")
done
for e in "${etiquetas[@]}"; do
  [[ " ${obligatorios[*]} " == *" $e "* ]] || ajenos+=("$e")
done
if [[ ${#etiquetas[@]} -gt 0 ]]; then
  repetidos=$(( ${#etiquetas[@]} - $(printf '%s\n' "${etiquetas[@]}" | sort -u | wc -l) ))
else
  repetidos=0
fi
if (( ${#etiquetas[@]} != ${#obligatorios[@]} || ${#faltan[@]} || ${#ajenos[@]} || repetidos != 0 )); then
  {
    echo "ABORTA antes de consultar produccion: previa.sql trae ${#etiquetas[@]} bloques"
    echo "y la lectura exige ${#obligatorios[@]}."
    (( ${#faltan[@]} )) && echo "  faltan: ${faltan[*]}"
    (( ${#ajenos[@]} )) && echo "  no estan en la lista obligatoria: ${ajenos[*]}"
    (( repetidos != 0 )) && echo "  etiquetas repetidas: $repetidos"
  } >&2
  exit 2
fi

echo "# Lectura previa de ARREGLOS 05 C (solo SELECT). SHA $(cd "$raiz" && git rev-parse HEAD)"
fallos=0
procesados=0
for etiqueta in "${etiquetas[@]}"; do
  consulta=$(bloque "$etiqueta")
  echo
  echo "== $etiqueta"
  procesados=$((procesados + 1))
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
done
echo
echo "# bloques procesados: $procesados de ${#obligatorios[@]}; con error: $fallos"
[[ $procesados == "${#obligatorios[@]}" && $fallos == 0 ]]
