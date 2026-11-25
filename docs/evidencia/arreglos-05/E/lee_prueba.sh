#!/usr/bin/env bash
# Corre cada bloque de E/prueba_real.sql contra produccion, SOLO LECTURA, por
# docs/evidencia/arreglos-01/lee_prod.sh (rol orbit_read, transaccion read_only).
# Uso desde la raiz: bash docs/evidencia/arreglos-05/E/lee_prueba.sh
set -euo pipefail

raiz=$(cd -- "$(dirname -- "$0")/../../../.." && pwd)
lee=${ORBIT_PREVIA_LEE:-$raiz/docs/evidencia/arreglos-01/lee_prod.sh}
sql=${ORBIT_PREVIA_SQL:-$raiz/docs/evidencia/arreglos-05/E/prueba_real.sql}

obligatorios=(
  momento indices triggers funcion_0098 sonda_0098 colas_por_modo_estado
  practicas_shadow practicas_vencidas_historia claves_no_terminales_duplicadas
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
    echo "ABORTA antes de consultar produccion: prueba_real.sql trae ${#etiquetas[@]} bloques"
    echo "y la lectura exige ${#obligatorios[@]}."
    (( ${#faltan[@]} )) && echo "  faltan: ${faltan[*]}"
    (( ${#ajenos[@]} )) && echo "  no estan en la lista obligatoria: ${ajenos[*]}"
    (( ${repetidos} != 0 )) && echo "  etiquetas repetidas: $repetidos"
  } >&2
  exit 2
fi

echo "# Lectura RO de produccion tras el despliegue del bloque E. SHA $(cd "$raiz" && git rev-parse HEAD)"
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
