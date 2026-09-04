#!/bin/bash
# Refresco diario de costos + FX + ledger (ORBIT 06 0.1 + 2.2).
# Runbook: docs/DEPLOY.md "Ingesta de costos/FX/ledger desde contabilidad".
#
# Los tres pipelines leen el MISMO snapshot SQLite de accounting (spec D4:
# un snapshot por corrida, API .backup() consistente con WAL).
#
# Review PR #144 (CodeRabbit, 2 Major):
# - El snapshot es UNICO POR CORRIDA (`mktemp`, mismo nombre dentro del
#   contenedor). Con la ruta fija `/tmp/accounting-snapshot.db`, una corrida
#   manual y la del cron se pisaban: el `docker cp` de una reemplazaba el
#   archivo que la otra estaba leyendo, y su `trap` lo borraba a media
#   corrida. Dos corridas simultaneas ahora son inocuas: cada una con su
#   archivo, y los tres pipelines son idempotentes (dedupe por PK/indice:
#   verificado, `rows_written=0` al re-correr).
# - El archivo del HOST nace 600 (`mktemp`): los datos contables no quedan
#   legibles para todo el mundo en /tmp. El 644 que necesita el UID 10001 de
#   la app se aplica DENTRO del contenedor, despues del `docker cp`.
#
# Despliegue (server goncloud): copia canonica en
# /mnt/data/appdata/orbit/refresh_costos.sh, cron del usuario `gon`
# (la linea NO se toca):
#   15 8 * * * /mnt/data/appdata/orbit/refresh_costos.sh >> /mnt/data/appdata/orbit/logs/costos.log 2>&1
# 08:15 UTC (movido del 07:30 por el lead en la review del PR #144): la
# cadena completa exige correr DESPUES de los DOS syncs de accounting —
# `sync_ads_to_ledger.py` (:30 cada 6 h -> 06:30) Y `sync_fx_rates.py`
# (08:00). A las 07:30 el snapshot se tomaba 30 min ANTES del sync de FX,
# asi que el FX de Orbit quedaba siempre un dia atrasado. 08:15 deja 25 min
# antes de los ciclos 08:40/08:41 y los tres pipelines tardan segundos.
#
# Un pipeline caido NO tumba a los otros: cada uno sella su propio
# ingest_run ok/false y el script sigue con el siguiente; el exit final es
# != 0 si alguno fallo (el cron lo ve). La fase de snapshot sigue fatal
# bajo `set -e`: sin snapshot no hay corrida honesta (infra compartida,
# no "un pipeline caido").
set -euo pipefail
# Snapshot UNICO por corrida (mktemp: 600 y nombre irrepetible).
SNAP=$(mktemp /tmp/accounting-snapshot.XXXXXXXX.db)
LOGDIR=/mnt/data/appdata/orbit/logs
HOY=$(date -u +%F)
limpiar() {
  # Los laterales -wal/-shm los crea SQLite al abrir en modo WAL: si no se
  # borran, cada corrida deja un par dentro del contenedor para siempre
  # (medido en la review del PR #144). Se borran en el host Y en el
  # contenedor, junto con el snapshot.
  rm -f "$SNAP" "$SNAP-wal" "$SNAP-shm" 2>/dev/null || true
  docker exec -u 0 orbit-app-1 rm -f "$SNAP" "$SNAP-wal" "$SNAP-shm" 2>/dev/null || true
}
trap limpiar EXIT
# 1) snapshot compartido por los tres pipelines (host, stdlib, no toca la
# original; el archivo ya existe vacio por mktemp y .backup() lo llena).
python3 -c "import sqlite3; src=sqlite3.connect(\"file:/mnt/data/appdata/accounting/data/accounting.db?mode=ro\", uri=True); dst=sqlite3.connect(\"$SNAP\"); src.backup(dst); dst.close(); src.close()"
docker cp "$SNAP" orbit-app-1:"$SNAP"
# El 644 va DENTRO del contenedor (lo necesita el UID 10001 de la app);
# en el host el archivo se queda 600 (mktemp).
docker exec -u 0 orbit-app-1 chmod 644 "$SNAP"
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
