#!/usr/bin/env bash
# Checklist post-deploy de R.1 (JEV ADS 03): SOLO LECTURA, salvo
# bloqueo-reconcilia, que cierra intenciones huerfanas ya visibles en
# Amazon y no crea negatives. Partida: S.5/checklist.sh, sin la pantalla
# de gasto-sin-venta (R.1 no la toca).
# Comprueba /cortes en 200 y que bloqueo-reconcilia sale 0, mas health
# y que el contenedor sigue arriba.
# Salidas: 0 = todo comprobado; 1 = alguna FALLA; 4 = no pude medir.
# Simulacion: ORBIT_SIMULACION=1 no hace ssh. Imprime las mismas
# comprobaciones con el valor que el script exige, para ver el contrato
# sin tocar produccion.
# Uso: bash docs/evidencia/jev-ads-03/ejecucion/R.1/checklist.sh <STAMP>
set -uo pipefail

SELLO=${1:-sin-sello}
H=${ORBIT_SSH:-goncloud}
REPO=$(git rev-parse --show-toplevel)
SIM=${ORBIT_SIMULACION:-0}
FALLAS=0
NOMEDIDO=0
cd "$REPO"

revisa() {
  if [ -z "${3:-}" ]; then
    echo "NO MEDIDO $1 (lectura vacia)"
    NOMEDIDO=$((NOMEDIDO + 1))
  elif [ "$2" = "$3" ]; then
    echo "OK    $1 = $3"
  else
    echo "FALLA $1: esperado '$2', obtenido '$3'"
    FALLAS=$((FALLAS + 1))
  fi
}

http_code() {
  local path=$1
  if [ "$SIM" = 1 ]; then
    echo 200
    return
  fi
  ssh "$H" "curl -s -o /dev/null -w %{http_code} http://127.0.0.1:8010$path"
}

echo "== 0) El servidor contesta"
if [ "$SIM" = 1 ]; then
  RUNNING=true
  echo "SIMULACION: sin docker inspect"
else
  RUNNING=$(ssh "$H" "docker inspect -f '{{.State.Running}}' orbit-app-1" || true)
fi
if [ -z "$RUNNING" ]; then
  echo "NO MEDIDO el servidor no contesta"
  exit 4
fi
revisa "orbit-app-1 corriendo" true "$RUNNING"

echo "== Checklist R.1 (sello $SELLO, modo ${SIM:-real})"

echo "== 1) HTTP: health y /cortes"
revisa "GET /health" 200 "$(http_code /health)"
revisa "GET /cortes" 200 "$(http_code /cortes)"

echo "== 2) bloqueo-reconcilia sale 0"
if [ "$SIM" = 1 ]; then
  RC=0
  echo "SIMULACION: python -m app.cli bloqueo-reconcilia no se ejecuta en prod"
else
  ssh "$H" 'docker exec orbit-app-1 python -m app.cli bloqueo-reconcilia >/dev/null'
  RC=$?
fi
revisa "bloqueo-reconcilia exit" 0 "$RC"

echo "== RESULTADO: $FALLAS falla(s), $NOMEDIDO sin medir"
[ "$FALLAS" -eq 0 ] || exit 1
[ "$NOMEDIDO" = 0 ] || exit 4
exit 0
