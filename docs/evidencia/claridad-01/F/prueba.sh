#!/usr/bin/env bash
# CLARIDAD 01 F: prueba real, en produccion y solo lectura, de lo que el arreglo cambia el mismo dia
# del despliegue. La decision de precios de US es una al dia (13:10 UTC); esto no la espera.
# 1. El cron de root corre /mnt/data/appdata/orbit/refresh_estimacion.sh: debe ser el desplegado
#    (igual a tools/ del servidor) y esperar a que termine amazon-prices-sync antes de copiar.
# 2. La unidad que espera existe en el servidor (un nombre mal escrito se leeria como inactive).
# 3. La ingesta desplegada en orbit-app-1 sale 1 y nombra amazon_us con la copia de las 12:45 del
#    9-nov (US de hace 6 h 33 min) y sale 0 con US fresca (salida_ingesta.py por stdin: corre main y
#    ejecutar_ingesta reales con la base, Product Fees y MeLi sustituidos; no escribe nada).
# 4. Esa salida llega al aviso: el cron de root corre el script dentro de cron-avisa y el script
#    sale con el codigo de la ingesta.
# Sale 0 solo si las cuatro se cumplen.
set -uo pipefail
S="ssh -o ConnectTimeout=20 -o BatchMode=yes goncloud"
SRV=/mnt/data/appdata/orbit
fallas=0
falla() { echo "FALLA $*"; fallas=$((fallas + 1)); }

if $S "grep -q 'ActiveState --value amazon-prices-sync.service' $SRV/refresh_estimacion.sh \
    && cmp -s $SRV/refresh_estimacion.sh $SRV/tools/refresh_estimacion.sh"; then
  echo "OK    $SRV/refresh_estimacion.sh es el desplegado y espera a amazon-prices-sync"
else
  falla "$SRV/refresh_estimacion.sh no espera a amazon-prices-sync o no es el de tools/"
fi

carga=$($S "systemctl show -p LoadState --value amazon-prices-sync.service" 2>/dev/null)
if [ "$carga" = loaded ]; then
  echo "OK    amazon-prices-sync.service existe (LoadState=loaded)"
else
  falla "amazon-prices-sync.service LoadState='$carga'"
fi

if salida=$($S "docker exec -i orbit-app-1 python -" < "$(dirname "$0")/salida_ingesta.py" 2>&1); then
  echo "OK    la ingesta desplegada sale 1 con US de hace 6 h 33 min y 0 con US fresca"
else
  falla "la ingesta desplegada no distingue la copia vieja de la fresca: $(printf '%s' "$salida" | tail -n 3 | tr '\n' ' ')"
fi

if $S "crontab -l | grep -F $SRV/refresh_estimacion.sh | grep -qF /usr/local/bin/cron-avisa \
    && grep -qF '|| sal=\$?' $SRV/refresh_estimacion.sh && grep -qxF 'exit \$sal' $SRV/refresh_estimacion.sh"; then
  echo "OK    el cron de root corre el script dentro de cron-avisa y el script sale con el codigo de la ingesta"
else
  falla "la salida 1 de la ingesta no llega a cron-avisa (cron sin cron-avisa o script que no propaga el codigo)"
fi

[ "$fallas" -eq 0 ] && echo "== F en produccion" || echo "== F falta en produccion: $fallas falla(s)"
[ "$fallas" -eq 0 ]
