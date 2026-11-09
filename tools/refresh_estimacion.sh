#!/bin/bash
# Refresco de insumos MARGEN ESTIMADO (A.3): ofertas frescas + cotizacion Product Fees.
# Runbook: docs/DEPLOY.md "Ingesta de estimacion desde bridge".
#
# Lee un snapshot SQLite CONSISTENTE del bridge (API .backup() con WAL).
# No toca bridge/accounting; solo Orbit + SP-API Product Fees (consulta).
#
# Despliegue (server goncloud): copia en /mnt/data/appdata/orbit/refresh_estimacion.sh,
# que instala tools/despliegue/desplegar.sh; cron de root envuelto en cron-avisa:
#   45 */6 * * * /usr/local/bin/cron-avisa --nombre "Actualización de estimaciones de Orbit" --cada 6 -- /bin/bash -c '/mnt/data/appdata/orbit/refresh_estimacion.sh' >> /mnt/data/appdata/orbit/logs/estimacion.log 2>&1
# Cadencia: cada 6 h a :45, despues del refresco del bridge (amazon-prices-sync, :35).
# CLARIDAD 01 F: a las 12:35 los reportes de Amazon tardan mas y US terminaba despues de
# las 12:45 (12:45:38 a 12:51:45 del 2 al 9-nov); la copia salia sin US y el motor de
# precios de las 13:10 no evaluaba ninguna publicacion de US. Por eso se espera al bridge.
# La ingesta sale 1 si una plataforma Amazon queda sin oferta fresca y cron-avisa avisa.
set -euo pipefail
LOGDIR=${ORBIT_LOGDIR:-/mnt/data/appdata/orbit/logs}
HOY=$(date -u +%F)
for _ in $(seq 80); do
  [ "$(systemctl show -p ActiveState --value amazon-prices-sync.service 2>/dev/null)" = activating ] || break
  sleep 15
done
SNAP=$(mktemp /tmp/bridge-estimacion-snapshot.XXXXXXXX.db)
limpiar() {
  rm -f "$SNAP" "$SNAP-wal" "$SNAP-shm" 2>/dev/null || true
  docker exec -u 0 orbit-app-1 rm -f "$SNAP" "$SNAP-wal" "$SNAP-shm" 2>/dev/null || true
}
trap limpiar EXIT
python3 -c "import sqlite3; src=sqlite3.connect(\"file:/mnt/data/appdata/bridge/data/bridge.db?mode=ro\", uri=True); dst=sqlite3.connect(\"$SNAP\"); src.backup(dst); dst.close(); src.close()"
docker cp "$SNAP" orbit-app-1:"$SNAP"
docker exec -u 0 orbit-app-1 chmod 644 "$SNAP"
sal=0
docker exec orbit-app-1 python -m app.cli ingest estimacion --sqlite "$SNAP" >>"$LOGDIR/estimacion.log" 2>&1 || sal=$?
echo "$HOY estimacion rc=$sal (detalle: $LOGDIR/estimacion.log)"
exit $sal
