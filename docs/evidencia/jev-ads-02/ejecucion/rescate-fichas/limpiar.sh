#!/usr/bin/env bash
# limpiar.sh — borra SOLO las bases temporales del rescate, por nombre exacto.
# No toca la carpeta apartada ni ninguna otra base.
set -euo pipefail

ssh goncloud bash -s <<'EOS'
set -euo pipefail
for db in orbit_rescate_jev orbit_rescate_ensayo; do
  docker exec orbit-db-1 psql -U orbit -d postgres -X -q -v ON_ERROR_STOP=1 \
    -c "DROP DATABASE IF EXISTS $db"
done
echo "LIMPIEZA OK (orbit_rescate_jev y orbit_rescate_ensayo fuera)"
EOS
