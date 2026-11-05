#!/usr/bin/env bash
# Sonda AUTO-03 (B.4). Uso:
#   bash tools/auto03/sonda.sh <nombre>
# Corre tools/auto03/sondas/<nombre>.sql con ORBIT_DSN_READ en transaccion
# READ ONLY (patron: el DSN sale de printenv dentro del contenedor y nunca se
# imprime). Cada .sql devuelve una fila t|<nota> o f|<nota> por hecho; sin
# datos que revisar devuelve t|sin filas, nunca cero filas. Sale distinto de
# cero si ssh o psql fallan, si la salida no trae ninguna fila o si alguna
# fila no empieza con t|. ORBIT_SIMULACION=1 corre contra ORBIT_SIM_DSN.
set -euo pipefail

NOMBRE=${1:?uso: bash tools/auto03/sonda.sh <nombre>}
REPO=$(git rev-parse --show-toplevel)
ARCHIVO="$REPO/tools/auto03/sondas/$NOMBRE.sql"
[ -f "$ARCHIVO" ] || { echo "ABORTA: no existe $ARCHIVO" >&2; exit 2; }
SIM=${ORBIT_SIMULACION:-0}
DSN_SIM=${ORBIT_SIM_DSN:-postgresql://orbit:orbit@127.0.0.1:5433/orbit_sim_auto03}
TMP=$(mktemp -d "${TMPDIR:-/tmp}/auto03-sonda.XXXXXX")
trap 'rm -rf "$TMP"' EXIT

{
  printf "BEGIN TRANSACTION READ ONLY;\n"
  cat "$ARCHIVO"
  printf "\nROLLBACK;\n"
} > "$TMP/sql"

if [ "$SIM" = 1 ]; then
  SAL=$(psql "$DSN_SIM" -X -q -tA -v ON_ERROR_STOP=1 < "$TMP/sql")
else
  SAL=$(ssh goncloud 'DSN=$(docker exec orbit-app-1 printenv ORBIT_DSN_READ); docker exec -i orbit-db-1 psql "$DSN" -X -q -tA -v ON_ERROR_STOP=1' < "$TMP/sql")
fi

[ -n "$SAL" ] || { echo "ABORTA: la sonda $NOMBRE no trajo ninguna fila" >&2; exit 1; }
FALLA=0
FILAS=0
while IFS= read -r fila; do
  [ -n "$fila" ] || continue
  FILAS=$((FILAS + 1))
  case "$fila" in
    t\|*) echo "$fila" ;;
    *) echo "$fila"; FALLA=1 ;;
  esac
done <<<"$SAL"
[ "$FILAS" -gt 0 ] || { echo "ABORTA: la sonda $NOMBRE no trajo ninguna fila" >&2; exit 1; }
[ "$FALLA" -eq 0 ] || exit 1
exit 0
