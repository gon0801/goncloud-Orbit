#!/usr/bin/env bash
# Comprueba F.1 sobre una copia de produccion (guia F.1 "Comprueba"): cuenta
# las ventas por `resuelto_por`, anota las sin atribuir y demuestra el
# reintento al llegar el dato que faltaba. Solo LEE produccion (orbit_read
# por ssh); la copia vive en el 5432 del loop y se borra al terminar.
# Uso: cd <repo> && bash docs/evidencia/repricing-02/ejecucion/F.1/comprobar.sh
set -euo pipefail

REPO=$(git rev-parse --show-toplevel)
DSN_LOCAL=${ORBIT_TEST_DSN:-postgresql://orbit:orbit@localhost:5432/postgres}
DB=orbit_f1_comp
TMP=$(mktemp -d)
limpiar() {
  psql "$DSN_LOCAL" -qc "DROP DATABASE IF EXISTS $DB WITH (FORCE)" >/dev/null
  rm -rf "$TMP"
}
trap limpiar EXIT

echo "== esquema y datos de prod (orbit_read, solo lectura)"
ssh goncloud 'DSN=$(docker exec orbit-app-1 printenv ORBIT_DSN_READ); docker exec -i orbit-db-1 pg_dump "$DSN" --schema-only' \
  > "$TMP/prod.sql"
grep -q 'CREATE TABLE public.ledger_event ' "$TMP/prod.sql" || { echo "ABORTA: dump de prod invalido"; exit 1; }
ssh goncloud 'DSN=$(docker exec orbit-app-1 printenv ORBIT_DSN_READ); docker exec -i orbit-db-1 pg_dump "$DSN" --data-only --exclude-table-data="*_seq" -t product -t listing -t ledger_event -t spapi_order_observation -t spapi_inventario_observation -t ingest_run' \
  > "$TMP/datos.sql"
grep -q '^COPY public.ledger_event ' "$TMP/datos.sql" || { echo "ABORTA: volcado de datos invalido"; exit 1; }

echo "== carga la copia local"
psql "$DSN_LOCAL" -qc "CREATE DATABASE $DB" >/dev/null
psql "${DSN_LOCAL%/*}/$DB" -q -v ON_ERROR_STOP=1 -f "$TMP/prod.sql" >/dev/null
{ echo "SET session_replication_role = replica;"; cat "$TMP/datos.sql"; } | psql "${DSN_LOCAL%/*}/$DB" -X -q -v ON_ERROR_STOP=1 >/dev/null
for tabla in product listing ledger_event spapi_order_observation spapi_inventario_observation ingest_run; do
  psql "${DSN_LOCAL%/*}/$DB" -q -c "select setval(pg_get_serial_sequence('$tabla', 'id'), (select coalesce(max(id), 1) from $tabla));" >/dev/null
done

echo "== atribuye en seco"
PYTHONPATH="$REPO" uv run --frozen python docs/evidencia/repricing-02/ejecucion/F.1/atribuir_copia.py "${DSN_LOCAL%/*}/$DB" --reintento
