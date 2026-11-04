#!/usr/bin/env bash
# Ensayo de V.2 en una base DESECHABLE local con el esquema REAL de produccion.
# Toma el esquema de prod con orbit_read (solo lectura), lo carga en una base
# nueva y comprueba:
#   1) 0062 aplica con psql -1 sobre el esquema de prod;
#   2) la reversa de 0062 devuelve el esquema EXACTO de prod;
#   3) reaplicar 0062 da el mismo esquema que la primera vez;
#   4) permisos.sql dice "permisos OK" sobre el esquema con 0062.
# Uso: desde la raiz del repo, bash docs/evidencia/bids-02/ejecucion/V.2/ensayo.sh
set -euo pipefail

REPO=$(git rev-parse --show-toplevel)
DSN_LOCAL=${ORBIT_TEST_DSN:-postgresql://orbit:orbit@localhost:5432/postgres}
DB=orbit_ensayo_bids_v2
TMP=$(mktemp -d)
CREADA=0
limpiar() {
  [ "$CREADA" = 1 ] && psql "$DSN_LOCAL" -qc "DROP DATABASE IF EXISTS $DB WITH (FORCE)" >/dev/null
  rm -rf "$TMP"
}
trap limpiar EXIT
DSN_DB="${DSN_LOCAL%/*}/$DB"

# El volcado lleva '\restrict TOKEN' con token aleatorio (pg_dump 16.15).
# El orden de los GRANT consecutivos no es semantico: revocar y reotorgar
# mueve la entrada ACL al final aunque el conjunto quede identico. Se
# quitan las restrict y se ordena cada bloque de GRANT seguidos; cualquier
# GRANT de mas, de menos o distinto sigue rompiendo el diff.
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

echo "== esquema de prod (orbit_read, solo lectura)"
ssh goncloud 'DSN=$(docker exec orbit-app-1 printenv ORBIT_DSN_READ); docker exec orbit-db-1 pg_dump "$DSN" --schema-only' \
  > "$TMP/prod.sql"
grep -q 'CREATE TABLE public.decision ' "$TMP/prod.sql" || { echo "ABORTA: dump de prod invalido"; exit 1; }
if grep -q 'ads_placement_observation' "$TMP/prod.sql"; then echo "ABORTA: prod ya tiene la tabla de V.2"; exit 1; fi

if [ -n "$(psql "$DSN_LOCAL" -tAc "SELECT 1 FROM pg_database WHERE datname = '$DB'")" ]; then
  echo "ABORTA: ya existe la base local $DB (otra corrida viva, una que murio sin limpiar o una ajena); revisala y borrala a mano si es de un ensayo"; exit 1
fi
psql "$DSN_LOCAL" -qc "CREATE DATABASE $DB" >/dev/null
CREADA=1
psql "$DSN_DB" -q -v ON_ERROR_STOP=1 -f "$TMP/prod.sql" >/dev/null
volcar a_prod
echo "esquema de prod cargado: $(grep -c '^CREATE TABLE' "$TMP/a_prod.sql") tablas"

echo "== 1) aplicar 0062"
aplicar 0062_bids02_placement.sql
volcar b_con_placements
echo "tabla 0062: $(grep -c '^CREATE TABLE public.ads_placement_observation ' "$TMP/b_con_placements.sql") (1 de 0062)"

echo "== 2) reversa de 0062"
aplicar 0062_reversa_bids02_placement.sql
volcar c_revertido
if diff -u "$TMP/a_prod.sql" "$TMP/c_revertido.sql"; then
  echo "OK: la reversa deja el esquema identico al de prod"
else
  echo "FALLA: la reversa no deja el esquema de prod"; exit 1
fi

echo "== 3) reaplicar 0062"
aplicar 0062_bids02_placement.sql
volcar d_reaplicado
if diff -u "$TMP/b_con_placements.sql" "$TMP/d_reaplicado.sql"; then
  echo "OK: reaplicar da el mismo esquema que la primera vez"
else
  echo "FALLA: reaplicar no reproduce el esquema"; exit 1
fi

echo "== 4) permisos esperados (los mismos que verifica el bloque DO de 0062)"
R=$(psql "$DSN_DB" -tA -v ON_ERROR_STOP=1 -f "$REPO/docs/evidencia/bids-02/ejecucion/V.2/permisos.sql")
echo "$R"
[ "$R" = "permisos OK" ] || exit 1
echo "ENSAYO OK"
