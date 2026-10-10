#!/usr/bin/env bash
# Copia de produccion para 0.b (regla 11): el esquema real mas los datos de
# las tablas que leen las vistas, en una base local desechable. Cada paso
# que la usa le agrega sus tablas con -t (P.3a agrego ads_metric_observation,
# M.2 agrega ingest_run).
# Solo LEE produccion (orbit_read por ssh); jamas escribe ahi. No imprime
# DSNs ni credenciales.
# Uso: cd ~/dev/goncloud-Orbit && bash docs/evidencia/bids-02/ejecucion/0.b/copia.sh
set -euo pipefail

REPO=$(git rev-parse --show-toplevel)
DSN_LOCAL=${ORBIT_TEST_DSN:-postgresql://orbit:orbit@localhost:5432/postgres}
DB=orbit_copia_bids02_0b
TMP=$(mktemp -d)
DSN_DB="${DSN_LOCAL%/*}/$DB"
OK=0
CREATED=0
limpiar() {
  if [ "$CREATED" = 1 ] && [ "$OK" = 0 ]; then
    psql "$DSN_LOCAL" -qc "DROP DATABASE IF EXISTS $DB WITH (FORCE)" >/dev/null
  fi
}
trap limpiar EXIT

echo "== esquema de prod (orbit_read, solo lectura)"
ssh goncloud 'DSN=$(docker exec orbit-app-1 printenv ORBIT_DSN_READ); docker exec orbit-db-1 pg_dump "$DSN" --schema-only' \
  > "$TMP/prod.sql"
grep -q 'CREATE TABLE public.decision ' "$TMP/prod.sql" || { echo "ABORTA: dump de prod invalido"; exit 1; }
if grep -q 'v_hoja_activa' "$TMP/prod.sql"; then echo "ABORTA: prod ya tiene las vistas de 0.b"; exit 1; fi

echo "== datos de prod (solo lectura)"
ssh goncloud 'DSN=$(docker exec orbit-app-1 printenv ORBIT_DSN_READ); docker exec orbit-db-1 pg_dump "$DSN" --data-only --exclude-table-data="*_seq" -t ad_entity -t ad_entity_state -t decision -t decision_application -t optimizer_cycle -t apply_attempt -t ads_metric_observation -t ingest_run' \
  > "$TMP/datos.sql"
grep -q 'COPY public.ad_entity ' "$TMP/datos.sql" || { echo "ABORTA: dump de datos invalido"; exit 1; }

if [ -n "$(psql "$DSN_LOCAL" -tAc "SELECT 1 FROM pg_database WHERE datname = '$DB'")" ]; then
  echo "ABORTA: ya existe la base local $DB (otra copia viva o una que murio sin limpiar); revisala y borrala a mano si es de un ensayo"; exit 1
fi
psql "$DSN_LOCAL" -qc "CREATE DATABASE $DB" >/dev/null
CREATED=1
psql "$DSN_DB" -q -v ON_ERROR_STOP=1 -f "$TMP/prod.sql" >/dev/null
# replica: la carga salta FKs y triggers (el orden del dump no es el de
# insercion). El archivo se queda en $TMP (regla 16: ningun volcado entra al repo).
{ echo "SET session_replication_role = replica;"; cat "$TMP/datos.sql"; } | psql "$DSN_DB" -q -v ON_ERROR_STOP=1 >/dev/null
for t in ad_entity ad_entity_state decision decision_application optimizer_cycle apply_attempt ads_metric_observation ingest_run; do
  echo "$t: $(psql "$DSN_DB" -tAc "SELECT count(*) FROM $t")"
done
echo "dumps en: $TMP"
OK=1
echo "COPIA OK"
