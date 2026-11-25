#!/usr/bin/env bash
# Huella del codigo desplegado de la fase 2 del bloque I. Compara local, host y
# contenedor para los cuatro archivos que cambia I, y lee el SHA/ESTADO del
# despliegue. Solo lectura.
set -euo pipefail
cd "$(dirname -- "$0")/../../../.."
unset MSYS_NO_PATHCONV MSYS2_ARG_CONV_EXCL

: "${MOTOR_SHA:?falta MOTOR_SHA}"
echo "# Huella de la fase 2 del bloque I. SHA integrado: $MOTOR_SHA; HEAD: $(git rev-parse HEAD)"
echo "# Fecha UTC: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo
echo "archivo | md5_local | md5_host | md5_contenedor"
for f in app/config_write.py app/api_write.py app/static/js/settings.js app/templates/settings.html; do
  local=$(md5sum "$f" | awk '{print $1}')
  host=$(ssh -o BatchMode=yes goncloud "md5sum /mnt/data/appdata/orbit/$f" | awk '{print $1}')
  contenedor=$(ssh -o BatchMode=yes goncloud "docker exec orbit-app-1 md5sum /app/$f" | awk '{print $1}')
  echo "$f | $local | $host | $contenedor"
done
echo
echo "sha_desplegado | $(ssh -o BatchMode=yes goncloud 'cat /mnt/data/appdata/orbit/despliegue/sha')"
echo
echo "# ultimo.env (solo estado y cambios)"
ssh -o BatchMode=yes goncloud 'grep -E "^(MOTOR_SHA|SHA_PREVIO|CODIGO|MIGRACION|ESTADO)=" /mnt/data/appdata/orbit/despliegue/ultimo.env' | tail -20
echo
echo "# marcas del bloque I dentro del contenedor"
echo "settings.js cuota=$(ssh -o BatchMode=yes goncloud "docker exec orbit-app-1 grep -c 'cuota de' /app/app/static/js/settings.js")"
echo "settings.html cap_text=$(ssh -o BatchMode=yes goncloud "docker exec orbit-app-1 grep -c 'type=\"text\" name=\"cap_' /app/app/templates/settings.html")"
echo "config_write _cap_entrante=$(ssh -o BatchMode=yes goncloud "docker exec orbit-app-1 grep -c '_cap_entrante' /app/app/config_write.py")"
echo "api_write cuota_cruda=$(ssh -o BatchMode=yes goncloud "docker exec orbit-app-1 grep -c 'Cuotas por kind' /app/app/api_write.py")"
echo
echo "# health"
ssh -o BatchMode=yes goncloud 'curl -sS -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8010/health'
