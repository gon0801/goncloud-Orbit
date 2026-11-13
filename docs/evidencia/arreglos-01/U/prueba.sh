#!/usr/bin/env bash
# U (AD-U6b): solo lectura. Sale 0 solo si cada plataforma que en antes.txt
# tenia economia_cambio_v1 tiene ahora economia_cambio_v2 en la config vigente.
set -euo pipefail
aqui=$(cd "$(dirname "$0")" && pwd)
fallo=0

compara() {
  local clave="$1" esperado="$2" actual
  actual=$(printf "%s\n" "SELECT coalesce(settings->>'$clave', 'ausente') FROM config_version ORDER BY id DESC LIMIT 1" | bash "$aqui/../lee_prod.sh")
  printf '%s=%s (esperado %s)\n' "$clave" "$actual" "$esperado"
  if [ "$actual" != "$esperado" ]; then
    fallo=1
  fi
}

compara ads_bid_economia_amazon_mx economia_cambio_v2
compara ads_bid_economia_amazon_us economia_cambio_v2
exit "$fallo"
