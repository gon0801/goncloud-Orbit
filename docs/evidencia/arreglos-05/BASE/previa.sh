#!/usr/bin/env bash
# Corre cada bloque de BASE/previa.sql contra produccion, SOLO LECTURA, por
# docs/evidencia/arreglos-01/lee_prod.sh (rol orbit_read, transaccion read_only).
# Uso desde la raiz: bash docs/evidencia/arreglos-05/BASE/previa.sh > previa.txt
# ORBIT_PREVIA_LEE=<script> sustituye a lee_prod.sh (p. ej. uno que imprima su entrada)
# para ver que se enviaria, sin tocar produccion. ORBIT_PREVIA_SQL=<archivo> senala
# otro previa.sql, solo para probar este lanzador.
# Formato de previa.sql: cada bloque empieza con "-- @<etiqueta>" y trae UNA
# sentencia. "-- @sonda <archivo-de-migracion>" corre su linea sonda-aplicada.
# Sale distinto de 0 si previa.sql no trae exactamente los bloques obligatorios
# (se aborta antes de consultar), si un bloque falla o si al cierre no se
# procesaron todos.
set -euo pipefail

raiz=$(cd -- "$(dirname -- "$0")/../../../.." && pwd)
lee=${ORBIT_PREVIA_LEE:-$raiz/docs/evidencia/arreglos-01/lee_prod.sh}
sql=${ORBIT_PREVIA_SQL:-$raiz/docs/evidencia/arreglos-05/BASE/previa.sql}

# Lo que la lectura debe cubrir. Un previa.sql vacio, ausente, truncado o al que
# le falte un marcador no puede salir con cero consultas y darse por leido.
obligatorios=(
  momento_utc esquema_columnas
  sonda_0083_arreglos01_costos_sin_compuerta.sql
  sonda_0084_arreglos01_impulso_desarmado.sql
  sonda_0085_arreglos01_ledger_reatribucion.sql
  sonda_0086_arreglos02_cargos.sql
  sonda_0087_arreglos03_devoluciones_meli.sql
  sonda_0088_arreglos03_ledger_original.sql
  sonda_0089_arreglos03_reputacion_avisada.sql
  sonda_0090_arreglos03_error_cierra_por_observacion.sql
  sonda_0091_arreglos03_sin_precio_cap.sql
  sonda_0092_temporada01_margen_vigente.sql
  sonda_0093_estimacion_s4_caja_real_mx.sql
  sonda_0093_temporada01_episodio.sql
  ciclos_por_modo_estado ciclos_decisiones_y_aplicacion desenlaces_con_ciclo_distinto
  ciclos_sin_cerrar lock_optimizador intentos_por_estado aplicacion_por_ack_y_verificacion
  negatives_reconciliados harvest_por_fase reposicion_estados reposicion_ids_duplicados
  reposicion_intentos_existe colas_por_modo_estado colas_en_vuelo_y_vencidas
  colas_clave_con_historia_y_practica inputs_claves_por_tipo inputs_version_del_caso
  inputs_target_por_procedencia intentos_bid_por_ciclo decisiones_de_temporada
  temporada_registros temporada_efectos_por_desenlace config_versiones
  settings_modos_y_topes goals
)

bloque() {
  awk -v e="$1" '$0 == "-- @" e { p = 1; next } /^-- @/ { p = 0 } p' "$sql"
}

sonda_de() {
  sed -n 's/^-- sonda-aplicada: *//p' "$raiz/migrations/$1" | head -n 1 | tr -d '\r'
}

etiquetas=()
while IFS= read -r linea; do
  [[ -n $linea ]] || continue
  if [[ $linea == "-- @sonda "* ]]; then
    etiquetas+=("sonda_${linea#-- @sonda }")
  else
    etiquetas+=("${linea#-- @}")
  fi
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

echo "# Lectura previa de produccion (solo SELECT). SHA $(cd "$raiz" && git rev-parse HEAD)"
fallos=0
procesados=0
for etiqueta in "${etiquetas[@]}"; do
  if [[ $etiqueta == sonda_* ]]; then
    consulta=$(sonda_de "${etiqueta#sonda_}")
  else
    consulta=$(bloque "$etiqueta")
  fi
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
