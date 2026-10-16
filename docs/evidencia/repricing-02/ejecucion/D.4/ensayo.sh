#!/usr/bin/env bash
# Ensayo de D.4 (REPRICING 02, carril M) en una base DESECHABLE
# local: aplica la 0073 sobre el esquema real de produccion, verifica
# su senal (la funcion trae la rama meli), aplica la reversa y exige
# el esquema EXACTO de partida, y reaplica. Patron: ../D.3/ensayo.sh.
# La 0073 solo reemplaza una funcion (BEFORE INSERT, sin revalidar
# historia): la senal es su texto en `pg_get_functiondef`.
# Uso: cd <repo> && bash docs/evidencia/repricing-02/ejecucion/D.4/ensayo.sh
set -euo pipefail

REPO=$(git rev-parse --show-toplevel)
DSN_LOCAL=${ORBIT_TEST_DSN:-postgresql://orbit:orbit@localhost:5432/postgres}
DB=orbit_ensayo_r02_d4
TMP=$(mktemp -d)
CREADA=0
limpiar() {
  [ "$CREADA" = 1 ] && psql "$DSN_LOCAL" -qc "DROP DATABASE IF EXISTS $DB WITH (FORCE)" >/dev/null
  rm -rf "$TMP"
}
trap limpiar EXIT
DSN_DB="${DSN_LOCAL%/*}/$DB"
cd "$REPO"
MIGS="0073_precio02_meli_miembro_costo.sql"
SENAL="disponible meli exige miembro"

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
senales() {
  CUENTA=$(psql "$DSN_DB" -X -q -tA -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM pg_proc WHERE proname = 'estimacion_escenario_referencias_coherentes' AND pg_get_functiondef(oid) LIKE '%$SENAL%';")
  echo "0073 (rama meli en la funcion): $CUENTA"
}

echo "== esquema de prod (solo lectura)"
ssh goncloud 'DSN=$(docker exec orbit-app-1 printenv ORBIT_DSN_READ); docker exec -i orbit-db-1 pg_dump "$DSN" --schema-only' \
  > "$TMP/prod.sql"
grep -q 'CREATE TABLE public.precio_decision ' "$TMP/prod.sql" || { echo "ABORTA: el esquema base no trae precio_decision"; exit 1; }
if grep -q "$SENAL" "$TMP/prod.sql"; then
  echo "ABORTA: el esquema base ya trae la 0073 (nada que ensayar de D.4)"
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

echo "== 1) aplicar la 0073"
for m in $MIGS; do aplicar "$m"; done

echo "== 2) senal de la migracion"
senales
CUENTA=$(psql "$DSN_DB" -X -q -tA -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM pg_proc WHERE proname = 'estimacion_escenario_referencias_coherentes' AND pg_get_functiondef(oid) LIKE '%$SENAL%';")
[ "$CUENTA" = "1" ] || { echo "FALLA: senal tras aplicar"; exit 1; }
volcar b_aplicado

echo "== 3) reversa: esquema identico al base"
aplicar "0073_reversa_precio02_meli_miembro_costo.sql"
volcar c_revertido
if diff -u "$TMP/a_base.sql" "$TMP/c_revertido.sql"; then
  echo "OK: la reversa deja el esquema identico al base"
else
  echo "FALLA: la reversa no devuelve el esquema base"; exit 1
fi

echo "== 4) reaplicar la 0073"
for m in $MIGS; do aplicar "$m"; done
volcar d_reaplicado
if diff -u "$TMP/b_aplicado.sql" "$TMP/d_reaplicado.sql"; then
  echo "OK: reaplicar da el mismo esquema que la primera vez"
else
  echo "FALLA: reaplicar no reproduce el esquema"; exit 1
fi
senales

echo "ENSAYO OK"
