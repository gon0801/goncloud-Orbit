#!/bin/bash
# Refresco de insumos MARGEN ESTIMADO (A.3): ofertas frescas + cotizacion Product Fees.
# Runbook: docs/DEPLOY.md "Ingesta de estimacion desde bridge".
#
# Lee un snapshot SQLite CONSISTENTE del bridge (API .backup() con WAL).
# No toca bridge/accounting; solo Orbit + SP-API Product Fees (consulta).
#
# Despliegue (server goncloud): copia canonica en
# /mnt/data/appdata/orbit/refresh_estimacion.sh, cron del usuario `gon`:
#   45 */6 * * * /mnt/data/appdata/orbit/refresh_estimacion.sh >> /mnt/data/appdata/orbit/logs/estimacion.log 2>&1
# Cadencia: cada 6 h a :45, despues del refresco acreditado del bridge a :35,
# dejando 10 min para que persista amazon_listing_prices antes del snapshot.
set -euo pipefail
SNAP=$(mktemp /tmp/bridge-estimacion-snapshot.XXXXXXXX.db)
LOGDIR=/mnt/data/appdata/orbit/logs
HOY=$(date -u +%F)
limpiar() {
  rm -f "$SNAP" "$SNAP-wal" "$SNAP-shm" 2>/dev/null || true
  docker exec -u 0 orbit-app-1 rm -f "$SNAP" "$SNAP-wal" "$SNAP-shm" 2>/dev/null || true
}
trap limpiar EXIT
python3 -c "import sqlite3; src=sqlite3.connect(\"file:/mnt/data/appdata/bridge/data/bridge.db?mode=ro\", uri=True); dst=sqlite3.connect(\"$SNAP\"); src.backup(dst); dst.close(); src.close()"
docker cp "$SNAP" orbit-app-1:"$SNAP"
docker exec -u 0 orbit-app-1 chmod 644 "$SNAP"
docker exec orbit-app-1 python -m app.cli ingest estimacion --sqlite "$SNAP" >>"$LOGDIR/estimacion.log" 2>&1
sal=$?
echo "$HOY estimacion rc=$sal (detalle: $LOGDIR/estimacion.log)"
exit $sal
