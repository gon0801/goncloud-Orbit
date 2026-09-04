#!/bin/bash
# Refresco diario de costos + FX + ledger (ORBIT 06 0.1 + 2.2).
# Runbook: docs/DEPLOY.md "Ingesta de costos/FX/ledger desde contabilidad".
#
# Los tres pipelines leen el MISMO snapshot SQLite de accounting (spec D4:
# un snapshot por corrida, API .backup() consistente con WAL; chmod 644 por
# el UID 10001 de la app — ver runbook).
#
# Despliegue (server goncloud): copia canonica en
# /mnt/data/appdata/orbit/refresh_costos.sh, cron del usuario `gon`
# (la linea NO se toca):
#   30 7 * * * /mnt/data/appdata/orbit/refresh_costos.sh >> /mnt/data/appdata/orbit/logs/costos.log 2>&1
# 07:30 UTC: despues del sync de accounting de las 06:30
# (`sync_ads_to_ledger.py`, :30 cada 6 h) y antes de los ciclos 08:40/08:41.
#
# Un pipeline caido NO tumba a los otros: cada uno sella su propio
# ingest_run ok/false y el script sigue con el siguiente; el exit final es
# != 0 si alguno fallo (el cron lo ve). La fase de snapshot sigue fatal
# bajo `set -e`: sin snapshot no hay corrida honesta (infra compartida,
# no "un pipeline caido").
set -euo pipefail
SNAP=/tmp/accounting-snapshot.db
LOGDIR=/mnt/data/appdata/orbit/logs
HOY=$(date -u +%F)
limpiar() {
  rm -f "$SNAP" 2>/dev/null || true
  docker exec -u 0 orbit-app-1 rm -f "$SNAP" 2>/dev/null || true
}
trap limpiar EXIT
# 1) snapshot compartido (host, stdlib, no toca la original).
python3 -c "import sqlite3; src=sqlite3.connect(\"file:/mnt/data/appdata/accounting/data/accounting.db?mode=ro\", uri=True); dst=sqlite3.connect(\"$SNAP\"); src.backup(dst); dst.close(); src.close()"
chmod 644 "$SNAP"
docker cp "$SNAP" orbit-app-1:"$SNAP"
# 2) los tres pipelines, aislados entre si. Sin rewrite de DSN:
# app/db.py ya reescribe @127.0.0.1: -> @db: con ORBIT_PG_HOST (D-2.2.2).
corre() {
  local sal
  docker exec orbit-app-1 python -m app.cli ingest "$1" --sqlite "$SNAP" >>"$LOGDIR/$2" 2>&1
  sal=$?
  echo "$HOY $1 rc=$sal (detalle: $LOGDIR/$2)"
  return $sal
}
rc=0
# OJO: `corre` SIEMPRE en lista `||` — solo asi `set -e` queda suspendido
# en su cuerpo y el fallo llega a `sal`; una llamada pelada abortaria.
corre costs costos.log || rc=1
corre fx fx.log || rc=1
corre ledger ledger.log || rc=1
exit $rc
