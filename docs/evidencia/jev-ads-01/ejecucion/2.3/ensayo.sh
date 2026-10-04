#!/usr/bin/env bash
# Ensayo de 2.3 en una base DESECHABLE local con el esquema REAL de produccion.
# Toma el esquema de prod con orbit_read (solo lectura), lo carga en una base
# nueva y comprueba:
#   1) 0049 + 0050 aplican con psql -1 sobre el esquema de prod;
#   2) las reversas 0050 y 0049 devuelven el esquema EXACTO de prod;
#   3) reaplicar 0049 + 0050 da el mismo esquema que la primera vez.
# Uso: cd ~/dev/goncloud-Orbit && bash docs/evidencia/jev-ads-01/ejecucion/2.3/ensayo.sh
set -euo pipefail

REPO=$(git rev-parse --show-toplevel)
DSN_LOCAL=${ORBIT_TEST_DSN:-postgresql://orbit:orbit@localhost:5432/postgres}
DB=orbit_ensayo_jev_2_3
TMP=$(mktemp -d)
CREADA=0
limpiar() {
  [ "$CREADA" = 1 ] && psql "$DSN_LOCAL" -qc "DROP DATABASE IF EXISTS $DB WITH (FORCE)" >/dev/null
  rm -rf "$TMP"
}
trap limpiar EXIT
DSN_DB="${DSN_LOCAL%/*}/$DB"

volcar() { pg_dump "$DSN_DB" --schema-only | grep -v -E '^\\(un)?restrict ' > "$TMP/$1.sql"; }
aplicar() { psql "$DSN_DB" -q -v ON_ERROR_STOP=1 -1 -f "$REPO/migrations/$1" >/dev/null; echo "aplicada $1"; }

echo "== esquema de prod (orbit_read, solo lectura)"
ssh goncloud 'DSN=$(docker exec orbit-app-1 printenv ORBIT_DSN_READ); docker exec orbit-db-1 pg_dump "$DSN" --schema-only' \
  > "$TMP/prod.sql"
grep -q 'CREATE TABLE public.decision ' "$TMP/prod.sql" || { echo "ABORTA: dump de prod invalido"; exit 1; }
if grep -q 'jev_revision' "$TMP/prod.sql"; then echo "ABORTA: prod ya tiene tablas Jev"; exit 1; fi

if [ -n "$(psql "$DSN_LOCAL" -tAc "SELECT 1 FROM pg_database WHERE datname = '$DB'")" ]; then
  echo "ABORTA: ya existe la base local $DB; no es de este ensayo y no se toca"; exit 1
fi
psql "$DSN_LOCAL" -qc "CREATE DATABASE $DB" >/dev/null
CREADA=1
psql "$DSN_DB" -q -v ON_ERROR_STOP=1 -f "$TMP/prod.sql" >/dev/null
volcar a_prod
echo "esquema de prod cargado: $(grep -c '^CREATE TABLE' "$TMP/a_prod.sql") tablas"

echo "== 1) aplicar 0049 y 0050"
aplicar 0049_jev_ads.sql
aplicar 0050_jev_revision_created_at.sql
volcar b_con_jev
echo "tablas Jev: $(grep -c '^CREATE TABLE public.jev_' "$TMP/b_con_jev.sql")"

echo "== 2) reversas 0050 y 0049"
aplicar 0050_reversa_jev_revision_created_at.sql
aplicar 0049_reversa_jev_ads.sql
volcar c_revertido
if diff -u "$TMP/a_prod.sql" "$TMP/c_revertido.sql"; then
  echo "OK: la reversa deja el esquema identico al de prod"
else
  echo "FALLA: la reversa no deja el esquema de prod"; exit 1
fi

echo "== 3) reaplicar 0049 y 0050"
aplicar 0049_jev_ads.sql
aplicar 0050_jev_revision_created_at.sql
volcar d_reaplicado
if diff -u "$TMP/b_con_jev.sql" "$TMP/d_reaplicado.sql"; then
  echo "OK: reaplicar da el mismo esquema que la primera vez"
else
  echo "FALLA: reaplicar no reproduce el esquema"; exit 1
fi

echo "== 4) permisos esperados (los mismos que verifica checklist.sh en prod)"
R=$(psql "$DSN_DB" -tA -v ON_ERROR_STOP=1 -f "$REPO/docs/evidencia/jev-ads-01/ejecucion/2.3/permisos.sql")
echo "$R"
[ "$R" = "permisos OK" ] || exit 1
echo "ENSAYO OK"
