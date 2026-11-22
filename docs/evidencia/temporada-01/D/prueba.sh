#!/usr/bin/env bash
# TEMPORADA 01, bloque D.3 (prueba-real, solo lectura): la temporada puede
# decidir con EL calendario que decidio David. Corre el bloque P antes de
# encender live. Lee el config_version mas reciente con lee_prod.sh (rol
# orbit_read, transaccion read_only) y sale 0 solo si los 12 meses de
# `ads_temporada` son los de la decision 1 del plan EN ORDEN y
# `ads_temporada_amazon_mx` y `ads_temporada_amazon_us` valen `shadow` o live.
# Uso (desde la raiz del repo): bash docs/evidencia/temporada-01/D/prueba.sh
set -euo pipefail
aqui=$(cd "$(dirname "$0")" && pwd)
sql="SELECT (settings->'ads_temporada'->'meses')
     = '[\"baja\",\"baja\",\"alta\",\"alta\",\"baja\",\"baja\",\"normal\",\"normal\",\"normal\",\"alta\",\"alta\",\"normal\"]'::jsonb
     AND (settings->>'ads_temporada_amazon_mx' IN ('shadow','live'))
     AND (settings->>'ads_temporada_amazon_us' IN ('shadow','live'))
     FROM config_version ORDER BY id DESC LIMIT 1"
salida=$(printf '%s\n' "$sql" | bash "$aqui/../../arreglos-01/lee_prod.sh")
case "$salida" in
  True | t | true) exit 0 ;;
  *) echo "la temporada no puede decidir: calendario distinto al de la decision 1 o interruptor fuera de shadow/live: $salida" >&2; exit 1 ;;
esac
