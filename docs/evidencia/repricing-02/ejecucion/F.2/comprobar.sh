#!/usr/bin/env bash
# Corre el job de muestras sobre una copia de produccion (REPRICING 02 F.2):
# dos corridas por plataforma (la segunda no escribe), distribucion por
# origen y exclusiones. Solo LEE produccion (orbit_read por ssh); la copia
# vive en el 5432 del loop y se borra al terminar.
# Uso: cd <repo> && bash docs/evidencia/repricing-02/ejecucion/F.2/comprobar.sh
set -euo pipefail

REPO=$(git rev-parse --show-toplevel)
DSN_LOCAL=${ORBIT_TEST_DSN:-postgresql://orbit:orbit@localhost:5432/postgres}
DB=orbit_f2_comp
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
ssh goncloud 'DSN=$(docker exec orbit-app-1 printenv ORBIT_DSN_READ); docker exec -i orbit-db-1 pg_dump "$DSN" --data-only --exclude-table-data="*_seq" -t product -t listing -t ledger_event -t familia -t producto_familia -t config_version' \
  > "$TMP/datos.sql"
grep -q '^COPY public.ledger_event ' "$TMP/datos.sql" || { echo "ABORTA: volcado de datos invalido"; exit 1; }

echo "== carga la copia local"
psql "$DSN_LOCAL" -qc "CREATE DATABASE $DB" >/dev/null
psql "${DSN_LOCAL%/*}/$DB" -q -v ON_ERROR_STOP=1 -f "$TMP/prod.sql" >/dev/null
{ echo "SET session_replication_role = replica;"; cat "$TMP/datos.sql"; } | psql "${DSN_LOCAL%/*}/$DB" -X -q -v ON_ERROR_STOP=1 >/dev/null
for tabla in product listing ledger_event familia config_version precio_envio_muestra; do
  psql "${DSN_LOCAL%/*}/$DB" -q -c "select setval(pg_get_serial_sequence('$tabla', 'id'), (select coalesce(max(id), 1) from $tabla));" >/dev/null
done

export ORBIT_DSN_DECIDE="${DSN_LOCAL%/*}/$DB"
export PYTHONPATH="$REPO"
for plat in amazon_mx amazon_us; do
  echo "== $plat primera corrida"
  time uv run --frozen python -m app.envio_muestras --platform "$plat" --canal fbm
  antes=$(psql "$ORBIT_DSN_DECIDE" -tAc "SELECT count(*) FROM precio_envio_muestra WHERE platform = '$plat'")
  echo "== $plat segunda corrida (no escribe)"
  uv run --frozen python -m app.envio_muestras --platform "$plat" --canal fbm
  despues=$(psql "$ORBIT_DSN_DECIDE" -tAc "SELECT count(*) FROM precio_envio_muestra WHERE platform = '$plat'")
  [ "$antes" = "$despues" ] || { echo "ABORTA: la segunda corrida escribio ($antes -> $despues)"; exit 1; }
  echo "filas $plat: $despues (igual)"
done

echo "== distribucion por origen (ultima muestra por producto)"
psql "$ORBIT_DSN_DECIDE" -X -q -c "SELECT platform::text, origen, count(*) FROM (SELECT DISTINCT ON (product_id, platform) platform, origen FROM precio_envio_muestra ORDER BY product_id, platform, calculada_at DESC, id DESC) u GROUP BY 1, 2 ORDER BY 1, 2;"
echo "== ventana y productos con publicacion"
psql "$ORBIT_DSN_DECIDE" -X -q -tAc "SELECT platform::text, count(DISTINCT product_id) FROM listing WHERE platform::text LIKE 'amazon%' GROUP BY 1 ORDER BY 1;"
