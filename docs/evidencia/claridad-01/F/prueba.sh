#!/usr/bin/env bash
# CLARIDAD 01 F: prueba real, en produccion y solo lectura, de lo que el arreglo cambia el mismo dia
# del despliegue. La decision de precios de US es una al dia (13:10 UTC); esto no la espera.
# 1. El cron de root corre /mnt/data/appdata/orbit/refresh_estimacion.sh: debe ser el desplegado
#    (igual a tools/ del servidor) y esperar a que termine amazon-prices-sync antes de copiar.
# 2. La unidad que espera existe en el servidor (un nombre mal escrito se leeria como inactive).
# 3. La ingesta desplegada en orbit-app-1 trae el aviso de plataforma sin oferta fresca.
# Sale 0 solo si las tres se cumplen.
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

if $S "docker exec orbit-app-1 python -c 'from app.estimacion_ingest import plataformas_sin_oferta_fresca'" 2>/dev/null; then
  echo "OK    la ingesta desplegada sale 1 si una plataforma Amazon queda sin oferta fresca"
else
  falla "la ingesta desplegada no trae plataformas_sin_oferta_fresca"
fi

[ "$fallas" -eq 0 ] && echo "== F en produccion" || echo "== F falta en produccion: $fallas falla(s)"
[ "$fallas" -eq 0 ]
