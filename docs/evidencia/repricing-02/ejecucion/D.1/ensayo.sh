#!/usr/bin/env bash
# Ensayo de D.1 (REPRICING 02 corte 0) en una base DESECHABLE local: aplica
# la 0053 y la 0054 sobre el esquema real de produccion (que ya trae de la
# 0060 a la 0072: esa es la prueba del orden de produccion), verifica las
# senales de cada una, aplica las reversas en orden inverso y exige el
# esquema EXACTO de partida, y reaplica. Patron:
# docs/evidencia/bids-02/ejecucion/D.6/ensayo.sh. D.1 lleva migracion
# 0053+0054 y codigo de 0.b; no cambia el cron.
# Uso: cd <repo> && bash docs/evidencia/repricing-02/ejecucion/D.1/ensayo.sh
set -euo pipefail

REPO=$(git rev-parse --show-toplevel)
DSN_LOCAL=${ORBIT_TEST_DSN:-postgresql://orbit:orbit@localhost:5432/postgres}
DB=orbit_ensayo_r02_d1
TMP=$(mktemp -d)
CREADA=0
limpiar() {
  [ "$CREADA" = 1 ] && psql "$DSN_LOCAL" -qc "DROP DATABASE IF EXISTS $DB WITH (FORCE)" >/dev/null
  rm -rf "$TMP"
}
trap limpiar EXIT
DSN_DB="${DSN_LOCAL%/*}/$DB"
cd "$REPO"
MIGS="0053_precio02_canal_meli.sql 0054_precio02_base.sql"

volcar() {
  pg_dump "$DSN_DB" --schema-only | grep -v -E '^\\(un)?restrict ' | python3 -c "
import sys
bloque = []
def vaciar():
    for ln in sorted(bloque):
        sys.stdout.write(ln)
    bloque.clear()
for ln in sys.stdin:
    if ln.startswith('GRANT '):
        bloque.append(ln)
    else:
        vaciar()
        sys.stdout.write(ln)
vaciar()
" > "$TMP/$1.sql"
}
aplicar() { psql "$DSN_DB" -q -v ON_ERROR_STOP=1 -1 -f "$REPO/migrations/$1" >/dev/null; echo "aplicada $1"; }
reversa_de() { # reversa_de <migracion>: imprime su archivo de reversa
  n=$(basename "$1" .sql | cut -c1-4)
  resto=$(basename "$1" .sql | cut -c6-)
  echo "${n}_reversa_${resto}.sql"
}
senales() {
  CANAL=$(psql "$DSN_DB" -X -q -tA -v ON_ERROR_STOP=1 -c "SELECT EXISTS (SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid WHERE t.typname = 'estimacion_canal' AND e.enumlabel = 'meli');")
  COLS=$(psql "$DSN_DB" -X -q -tA -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM information_schema.columns WHERE table_name = 'precio_decision' AND column_name IN ('goal_id', 'l_origen', 'verificacion');")
  MODO=$(psql "$DSN_DB" -X -q -tA -v ON_ERROR_STOP=1 -c "SELECT settings ->> 'precio_modo_global' FROM config_version ORDER BY id DESC LIMIT 1;")
  echo "0053 (meli en canal): $CANAL"
  echo "0054 (3 columnas en decision): $COLS"
  echo "0054 (modo global): $MODO"
}

echo "== esquema de prod (solo lectura)"
ssh goncloud 'DSN=$(docker exec orbit-app-1 printenv ORBIT_DSN_READ); docker exec -i orbit-db-1 pg_dump "$DSN" --schema-only' \
  > "$TMP/prod.sql"
grep -q 'CREATE TABLE public.precio_decision ' "$TMP/prod.sql" || { echo "ABORTA: el esquema base no trae precio_decision"; exit 1; }
if grep -q "meli" "$TMP/prod.sql" | grep -q "canal"; then
  echo "ABORTA: el esquema base ya trae el valor meli (la base no sirve de partida)"
  exit 1
fi

if [ -n "$(psql "$DSN_LOCAL" -tAc "SELECT 1 FROM pg_database WHERE datname = '$DB'")" ]; then
  echo "ABORTA: ya existe la base local $DB; revisala y borrala a mano si es de un ensayo"; exit 1
fi
psql "$DSN_LOCAL" -qc "CREATE DATABASE $DB" >/dev/null
CREADA=1
psql "$DSN_DB" -q -v ON_ERROR_STOP=1 -f "$TMP/prod.sql" >/dev/null
volcar a_base
echo "esquema base cargado"

echo "== 1) aplicar la 0053 y la 0054 (dos corridas de psql separadas)"
for m in $MIGS; do aplicar "$m"; done

echo "== 2) senales de cada migracion"
senales
CANAL=$(psql "$DSN_DB" -X -q -tA -v ON_ERROR_STOP=1 -c "SELECT EXISTS (SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid WHERE t.typname = 'estimacion_canal' AND e.enumlabel = 'meli');")
COLS=$(psql "$DSN_DB" -X -q -tA -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM information_schema.columns WHERE table_name = 'precio_decision' AND column_name IN ('goal_id', 'l_origen', 'verificacion');")
[ "$CANAL" = "t" ] && [ "$COLS" = "3" ] || { echo "FALLA: senales tras aplicar"; exit 1; }
volcar b_aplicado

echo "== 3) reversas en orden inverso: esquema identico al base"
REVS=""
for m in $MIGS; do REVS="$(reversa_de "$m") $REVS"; done
for r in $REVS; do aplicar "$r"; done
# La reversa de la 0054 quita tambien el SELECT previo sobre
# precio_envio_muestra (patron D.6/0071): si el base lo traia, se
# re-otorga suelto antes de comparar.
BASE_GRANT=$(grep -c 'GRANT SELECT ON TABLE public.precio_envio_muestra TO app_ingest;' "$TMP/a_base.sql" || true)
if [ "$BASE_GRANT" != "0" ]; then
  psql "$DSN_DB" -q -v ON_ERROR_STOP=1 -c "GRANT SELECT ON precio_envio_muestra TO app_ingest;" >/dev/null
  echo "re-otorgado el SELECT previo sobre precio_envio_muestra"
fi
volcar c_revertido
if diff -u "$TMP/a_base.sql" "$TMP/c_revertido.sql"; then
  echo "OK: las reversas dejan el esquema identico al base"
elif diff -u "$TMP/a_base.sql" "$TMP/c_revertido.sql" | grep -v -E '^[+-]{3} ' | grep -E '^[+-]' | grep -v -q 'precio_decision_por_dia'; then
  echo "FALLA: las reversas dejan diferencias fuera del indice huerfano conocido"; exit 1
else
  echo "OK con excepcion documentada: la unica diferencia es el indice"
  echo "huerfano precio_decision_por_dia (la reversa de la 0054 no lo borra;"
  echo "contrato de 0.a sellado, se declara en Desviaciones del PR)"
fi

echo "== 4) reaplicar (solo la 0054: el valor meli del enum no se puede"
echo "quitar ni reagregar —ADD VALUE no es re-ejecutable— y sigue efectivo;"
echo "el indice huerfano se borra a mano antes, como haria el operador)"
psql "$DSN_DB" -q -v ON_ERROR_STOP=1 -c "DROP INDEX IF EXISTS precio_decision_por_dia;" >/dev/null
aplicar "0054_precio02_base.sql"
volcar d_reaplicado
if diff -u "$TMP/b_aplicado.sql" "$TMP/d_reaplicado.sql"; then
  echo "OK: reaplicar da el mismo esquema que la primera vez"
else
  echo "FALLA: reaplicar no reproduce el esquema"; exit 1
fi
senales

echo "ENSAYO OK"
