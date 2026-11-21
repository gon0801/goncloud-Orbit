#!/usr/bin/env bash
# TEMPORADA 01, bloque C.2 (prueba-real, solo lectura): la subida rapida puede
# decidir en las dos plataformas. Corre el bloque P antes de encender live.
# Lee el config_version mas reciente con lee_prod.sh (rol orbit_read,
# transaccion read_only) y sale 0 solo si `ads_bid_recuperacion_amazon_mx` y
# `ads_bid_recuperacion_amazon_us` valen `shadow` o `live`.
# Uso (desde la raiz del repo): bash docs/evidencia/temporada-01/C/prueba.sh
set -euo pipefail
aqui=$(cd "$(dirname "$0")" && pwd)
sql="SELECT (settings->>'ads_bid_recuperacion_amazon_mx' IN ('shadow','live'))
     AND (settings->>'ads_bid_recuperacion_amazon_us' IN ('shadow','live'))
     FROM config_version ORDER BY id DESC LIMIT 1"
salida=$(printf '%s\n' "$sql" | bash "$aqui/../../arreglos-01/lee_prod.sh")
case "$salida" in
  True | t | true) exit 0 ;;
  *) echo "la subida rapida no esta encendida en MX y US: $salida" >&2; exit 1 ;;
esac
