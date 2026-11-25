#!/usr/bin/env bash
# Recibo de solo lectura del esquema y huella del codigo desplegado del bloque H.
# Uso desde la raiz: bash docs/evidencia/arreglos-05/H/lee_esquema_ro.sh > .../H/esquema-ro.txt
# Cada bloque de esquema-ro.sql corre por lee_prod.sh (orbit_read, read_only); la
# huella del codigo se lee del servidor y del arbol local. Sale distinto de 0 si
# falta un bloque, si uno falla o si la huella no cuadra.
set -euo pipefail

raiz=$(cd -- "$(dirname -- "$0")/../../../.." && pwd)
lee=${ORBIT_PREVIA_LEE:-$raiz/docs/evidencia/arreglos-01/lee_prod.sh}
sql=${ORBIT_PREVIA_SQL:-$raiz/docs/evidencia/arreglos-05/H/esquema-ro.sql}
sha=${MOTOR_SHA:-$(cd "$raiz" && git rev-parse HEAD)}
archivos="app/apply.py app/apply_cuota.py app/apply_estados.py"

obligatorios=(
  momento
  sonda_0099
  motivos_check
  conteo_tablas
  historia_totales
  historia_motivos
  historia_maximos
  historia_despues_del_despliegue
  tope_ciclo
  permisos_historia
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
repetidos=$(( ${#etiquetas[@]} - $(printf '%s\n' "${etiquetas[@]}" | sort -u | wc -l) ))
if (( ${#etiquetas[@]} != ${#obligatorios[@]} || ${#faltan[@]} || ${#ajenos[@]} || repetidos != 0 )); then
  {
    echo "ABORTA antes de consultar produccion: esquema-ro.sql trae ${#etiquetas[@]} bloques"
    echo "y la lectura exige ${#obligatorios[@]}."
    (( ${#faltan[@]} )) && echo "  faltan: ${faltan[*]}"
    (( ${#ajenos[@]} )) && echo "  no estan en la lista obligatoria: ${ajenos[*]}"
    (( ${repetidos} != 0 )) && echo "  etiquetas repetidas: ${repetidos}"
  } >&2
  exit 2
fi

fallos=0

echo "# Esquema y huella de la fase 2 del bloque H (solo lectura)"
echo "# MOTOR_SHA: $sha"
echo "# SHA local: $(cd "$raiz" && git rev-parse HEAD)"
echo "# Comando: bash docs/evidencia/arreglos-05/H/lee_esquema_ro.sh"
echo "# Referencia previa al despliegue: H/previa.txt (2026-11-25 04:34 UTC)"
echo

for etiqueta in "${etiquetas[@]}"; do
  consulta=$(bloque "$etiqueta")
  echo "== $etiqueta"
  if [[ -z ${consulta:-} ]]; then
    echo "ERROR: bloque vacio"
    fallos=$((fallos + 1))
    echo
    continue
  fi
  if salida=$(printf '%s\n' "$consulta" | bash "$lee" 2>&1); then
    printf '%s\n' "$salida"
  else
    echo "ERROR: $salida"
    fallos=$((fallos + 1))
  fi
  echo
done

echo "# Huella del codigo desplegado"
echo
echo "## despliegue/sha en el servidor"
ssh goncloud 'cat /mnt/data/appdata/orbit/despliegue/sha' || fallos=$((fallos + 1))
echo
echo "## /health en el servidor (8010)"
ssh goncloud "curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8010/health" \
  || fallos=$((fallos + 1))
echo
echo "## md5 de los archivos que toca H (servidor)"
ssh goncloud "cd /mnt/data/appdata/orbit && md5sum $archivos" || fallos=$((fallos + 1))
echo
echo "## md5 de los mismos archivos en el arbol local ($sha)"
(cd "$raiz" && md5sum $archivos) || fallos=$((fallos + 1))
echo
echo "## simbolos de H dentro del contenedor vivo"
ssh goncloud 'docker exec orbit-app-1 python -c "
from app import apply_cuota, apply_estados
print(\"tope=\", apply_cuota.TOPE_PUJAS_ORDINARIAS_CICLO)
print(\"motivo=\", apply_cuota.MOTIVO_TOPE_CICLO)
print(\"evento=\", apply_estados.EventoCambio.CUPO_CICLO_AGOTADO.value)
print(\"estado=\", apply_estados.EstadoCambio.CUPO_CICLO_AGOTADO.value)
print(\"clasificador=\", hasattr(apply_estados, \"transicion_de_reserva_cupo\"))
print(\"exenta=\", hasattr(apply_cuota, \"puja_exenta_del_cupo\"))
"' || fallos=$((fallos + 1))

echo
echo "# bloques procesados: ${#obligatorios[@]} de ${#obligatorios[@]}; con error: $fallos"
[[ $fallos == 0 ]]
