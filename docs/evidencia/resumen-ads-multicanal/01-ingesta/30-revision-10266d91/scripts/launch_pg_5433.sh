#!/bin/bash
# Cluster temporal en 5433 y esquema completo (87 tablas), receta de verify/Launch.md.
set -euo pipefail
PGDATA=$(mktemp -d /tmp/orbit-pgdata.XXXXXX)
SOCK=$(mktemp -d /tmp/orbit-pgsock.XXXXXX)
export LC_ALL=en_US.UTF-8
initdb -D "$PGDATA" -U orbit --auth=trust -E UTF8 >/dev/null
pg_ctl -D "$PGDATA" -o "-p 5433 -k $SOCK -c listen_addresses=127.0.0.1" \
  -l /tmp/orbit-pg.log start >/dev/null
sleep 2
pg_isready -h 127.0.0.1 -p 5433
for f in migrations/*.sql; do
  case "$f" in *0011_*|*_reversa_*) continue ;; esac
  psql -h 127.0.0.1 -p 5433 -U orbit -d postgres -v ON_ERROR_STOP=1 -q -f "$f"
done
TABS=$(psql -h 127.0.0.1 -p 5433 -U orbit -d postgres -tAc \
  "select count(*) from information_schema.tables where table_schema='public' and table_type='BASE TABLE'")
echo "PGDATA=$PGDATA SOCK=$SOCK TABS=$TABS"
