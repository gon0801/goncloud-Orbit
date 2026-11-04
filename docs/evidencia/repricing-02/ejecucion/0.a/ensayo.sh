#!/usr/bin/env bash
# Ensayo de 0.a (REPRICING 02) en una base DESECHABLE local con el esquema y
# los DATOS de produccion (orbit_read, solo lectura). Parte del de
# docs/evidencia/jev-ads-02/ejecucion/S.3/ensayo.sh y le agrega la carga de
# datos. Comprueba:
#   1) antes de aplicar: precio_envio_muestra esta vacia (como en prod);
#   2) la 0053 aplica en su propia corrida de psql y la 0054 en otra;
#   3) despues: config_version crecio EXACTAMENTE una fila, la vigente trae
#      precio_modo_global = shadow y conserva lo previo;
#   4) despues: precio_decision conserva su conteo (la 0054 no toca filas).
# Uso: cd <repo> && bash docs/evidencia/repricing-02/ejecucion/0.a/ensayo.sh
set -euo pipefail

REPO=$(git rev-parse --show-toplevel)
DSN_LOCAL=${ORBIT_TEST_DSN:-postgresql://orbit:orbit@localhost:5432/postgres}
DB=orbit_ensayo_r02_0a
TMP=$(mktemp -d)
CREADA=0
limpiar() {
  [ "$CREADA" = 1 ] && psql "$DSN_LOCAL" -qc "DROP DATABASE IF EXISTS $DB WITH (FORCE)" >/dev/null
  rm -rf "$TMP"
}
trap limpiar EXIT
DSN_DB="${DSN_LOCAL%/*}/$DB"
PSQLDB=(psql "$DSN_DB" -X -q -v ON_ERROR_STOP=1)

echo "== esquema y datos de prod (orbit_read, solo lectura)"
ssh goncloud 'DSN=$(docker exec orbit-app-1 printenv ORBIT_DSN_READ); docker exec -i orbit-db-1 pg_dump "$DSN" --schema-only' \
  > "$TMP/prod.sql"
grep -q 'CREATE TABLE public.precio_decision ' "$TMP/prod.sql" || { echo "ABORTA: dump de prod invalido"; exit 1; }
if grep -q 'CREATE TABLE public.precio_corrida ' "$TMP/prod.sql"; then
  echo "ABORTA: prod ya tiene la 0054"; exit 1
fi
# El pg_dump de datos corre dentro del ssh entre comillas simples: los
# patrones -t van entre comillas dobles (el rol de lectura no puede leer
# secuencias: sin --exclude-table-data falla con permission denied).
ssh goncloud 'DSN=$(docker exec orbit-app-1 printenv ORBIT_DSN_READ); docker exec -i orbit-db-1 pg_dump "$DSN" --data-only --exclude-table-data="*_seq" -t product -t listing -t config_version -t estimacion_politica_version -t estimacion_escenario -t "precio_*"' \
  > "$TMP/datos.sql"
grep -q '^COPY public.product ' "$TMP/datos.sql" || { echo "ABORTA: volcado de datos invalido"; exit 1; }

if [ -n "$(psql "$DSN_LOCAL" -tAc "SELECT 1 FROM pg_database WHERE datname = '$DB'")" ]; then
  echo "ABORTA: ya existe la base local $DB (corrida viva o restos de una muerta); revisala"; exit 1
fi
psql "$DSN_LOCAL" -qc "CREATE DATABASE $DB" >/dev/null
CREADA=1
"${PSQLDB[@]}" -f "$TMP/prod.sql" >/dev/null

echo "== carga de datos con triggers apagados y secuencias ajustadas"
{ echo "SET session_replication_role = replica;"; cat "$TMP/datos.sql"; } | psql "$DSN_DB" -X -q -v ON_ERROR_STOP=1 >/dev/null
for tabla in product listing config_version estimacion_politica_version estimacion_escenario precio_goal precio_decision precio_cotizacion precio_envio_muestra precio_cambio; do
  psql "$DSN_DB" -q -c "select setval(pg_get_serial_sequence('$tabla', 'id'), (select coalesce(max(id), 1) from $tabla));" >/dev/null
done

echo "== 0) estado ANTES de aplicar las migraciones"
ANTES_CONFIG=$(psql "$DSN_DB" -tAc "SELECT count(*) FROM config_version")
ANTES_DECISIONES=$(psql "$DSN_DB" -tAc "SELECT count(*) FROM precio_decision")
ANTES_MUESTRAS=$(psql "$DSN_DB" -tAc "SELECT count(*) FROM precio_envio_muestra")
[ "$ANTES_MUESTRAS" = "0" ] || { echo "FALLA: precio_envio_muestra trae $ANTES_MUESTRAS filas y se esperaba vacia"; exit 1; }
echo "antes: config=$ANTES_CONFIG decisiones=$ANTES_DECISIONES muestras=$ANTES_MUESTRAS"

echo "== 1) aplicar la 0053 (corrida propia: ADD VALUE no se usa en la misma transaccion)"
psql "$DSN_DB" -X -q -v ON_ERROR_STOP=1 -f "$REPO/migrations/0053_precio02_canal_meli.sql" >/dev/null
echo "aplicada 0053"

echo "== 2) aplicar la 0054 (corrida separada)"
psql "$DSN_DB" -X -q -v ON_ERROR_STOP=1 -f "$REPO/migrations/0054_precio02_base.sql" >/dev/null
echo "aplicada 0054"

echo "== 3) config: una fila mas, modo global shadow, conserva lo previo"
DESPUES_CONFIG=$(psql "$DSN_DB" -tAc "SELECT count(*) FROM config_version")
ESPERADA=$((ANTES_CONFIG + 1))
[ "$DESPUES_CONFIG" = "$ESPERADA" ] || { echo "FALLA: config_version paso de $ANTES_CONFIG a $DESPUES_CONFIG (se esperaba $ESPERADA)"; exit 1; }
MODO=$(psql "$DSN_DB" -tAc "SELECT settings ->> 'precio_modo_global' FROM config_version ORDER BY id DESC LIMIT 1")
[ "$MODO" = "shadow" ] || { echo "FALLA: precio_modo_global = '$MODO' y se esperaba shadow"; exit 1; }
CONSERVA=$(psql "$DSN_DB" -tAc "SELECT count(*) = 0 FROM config_version previa, config_version nueva WHERE previa.id = (SELECT max(id) - 1 FROM config_version) AND nueva.id = (SELECT max(id) FROM config_version) AND NOT previa.settings <@ nueva.settings")
[ "$CONSERVA" = "t" ] || { echo "FALLA: la config nueva no conserva todas las claves de la previa"; exit 1; }
echo "la config nueva conserva todas las claves de la previa"

echo "== 4) precio_decision conserva su conteo"
DESPUES_DECISIONES=$(psql "$DSN_DB" -tAc "SELECT count(*) FROM precio_decision")
[ "$DESPUES_DECISIONES" = "$ANTES_DECISIONES" ] || { echo "FALLA: precio_decision paso de $ANTES_DECISIONES a $DESPUES_DECISIONES"; exit 1; }
echo "despues: config=$DESPUES_CONFIG decisiones=$DESPUES_DECISIONES modo=$MODO"
echo "ENSAYO OK"
