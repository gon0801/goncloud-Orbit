#!/usr/bin/env bash
# SP-API: las 8 ingestas diarias, EN SERIE y en un solo proceso.
# Versionado aqui; el servidor lo recibe en /mnt/data/appdata/orbit/spapi-diario.sh
# (docs/DEPLOY.md). Sin `set -e`: una fuente que falla no cancela las siguientes
# (cada una sella su propio ingest_run y alerta sola).
# Repeticion: lo que sale distinto de 0 se repite UNA vez al final, en el mismo
# orden, salvo que arrancaria despues de las 06:15 UTC (tope = ventana antes del
# sync de estructura de 06:45). La hora sale de `date -u`; en pruebas,
# SPAPI_DIARIO_AHORA=HH:MM.
set -uo pipefail

LIMITE=06:15
FUENTES="spapi_orders spapi_listings spapi_inventario spapi_pricing"
MERCADOS="amazon_mx amazon_us"
FALLADAS=()

ingesta() {  # ingesta <fuente> <mercado>
  echo "=== $(date -u +%FT%TZ) $1 $2 ==="
  docker exec orbit-app-1 python -m app.cli ingest "$1" --platform "$2"
}

for fuente in $FUENTES; do
  for mercado in $MERCADOS; do
    ingesta "$fuente" "$mercado" || FALLADAS+=("$fuente $mercado")
  done
done

if [ "${#FALLADAS[@]}" -gt 0 ]; then
  for par in "${FALLADAS[@]}"; do
    ahora=${SPAPI_DIARIO_AHORA:-$(date -u +%H:%M)}
    if [[ "$ahora" > "$LIMITE" ]]; then
      echo "no repito $par: ya son las $ahora UTC"
      continue
    fi
    echo "repito $par (segundo y ultimo intento)"
    ingesta $par || true
  done
fi
