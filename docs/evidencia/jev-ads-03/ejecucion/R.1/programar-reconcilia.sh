#!/usr/bin/env bash
# Agrega, si no esta, la linea de bloqueo-reconcilia al crontab de gon.
# Mismo patron que la linea de spapi_vigilante (flock, docker exec, log).
# Simula por omision: sin --acepto-mutacion-real no hay ssh y el crontab
# es un archivo local. --quitar es la reversa (saca la linea, no el resto).
# Imprime el crontab resultante.
# Uso sim:  bash docs/evidencia/jev-ads-03/ejecucion/R.1/programar-reconcilia.sh
# Uso real: bash docs/evidencia/jev-ads-03/ejecucion/R.1/programar-reconcilia.sh --acepto-mutacion-real
# Reversa:  bash docs/evidencia/jev-ads-03/ejecucion/R.1/programar-reconcilia.sh --quitar
#           (con --acepto-mutacion-real si hay que escribir en gon)
set -euo pipefail

LINEA='17 * * * * /usr/bin/flock -n /tmp/orbit-bloqueo-reconcilia.lock docker exec orbit-app-1 python -m app.cli bloqueo-reconcilia >> /mnt/data/appdata/orbit/logs/bloqueo-reconcilia.log 2>&1'
H=${ORBIT_SSH:-goncloud}
MUTAR=0
QUITAR=0
DIR=docs/evidencia/jev-ads-03/ejecucion/R.1
SIM_DIR=${ORBIT_SIM_DIR:-$DIR/.sim}

while [ $# -gt 0 ]; do
  case "$1" in
    --acepto-mutacion-real) MUTAR=1 ;;
    --quitar) QUITAR=1 ;;
    *) echo "uso: programar-reconcilia.sh [--acepto-mutacion-real] [--quitar]"; exit 2 ;;
  esac
  shift
done

sembrar() {
  mkdir -p "$SIM_DIR"
  if [ ! -f "$SIM_DIR/crontab-gon" ]; then
    printf '%s\n' \
      '# job_key=spapi:vigilante  aviso si el cron spapi:diario no dejo sus 8 corridas' \
      '30 7 * * * /usr/bin/flock -n /tmp/spapi-vigilante.lock docker exec orbit-app-1 python -m app.cli spapi_vigilante >> /mnt/data/appdata/orbit/logs/spapi-vigilante.log 2>&1' \
      > "$SIM_DIR/crontab-gon"
  fi
}

leer() {
  if [ "$MUTAR" = 1 ]; then
    ssh "$H" "crontab -l -u gon 2>/dev/null" || true
  else
    sembrar
    cat "$SIM_DIR/crontab-gon"
  fi
}

escribir() {
  if [ "$MUTAR" = 1 ]; then
    ssh "$H" "crontab -u gon -"
  else
    cat > "$SIM_DIR/crontab-gon"
  fi
}

actual=$(leer)
if [ "$QUITAR" = 1 ]; then
  nuevo=$(printf '%s\n' "$actual" | grep -v -F 'app.cli bloqueo-reconcilia' || true)
  echo "reversa: se quita la linea de bloqueo-reconcilia"
else
  if printf '%s\n' "$actual" | grep -q -F 'app.cli bloqueo-reconcilia'; then
    nuevo=$actual
    echo "ya estaba: no se duplica"
  else
    if [ -n "$actual" ]; then
      nuevo=$(printf '%s\n' "$actual" "$LINEA")
    else
      nuevo=$LINEA
    fi
    echo "se agrega la linea de bloqueo-reconcilia"
  fi
fi

printf '%s\n' "$nuevo" | escribir
echo "== crontab resultante"
printf '%s\n' "$nuevo"
if [ "$MUTAR" = 1 ]; then
  echo "ESCRITO en el crontab de gon ($H)"
else
  echo "SIMULACION: sin --acepto-mutacion-real no se toco el crontab de gon"
fi
