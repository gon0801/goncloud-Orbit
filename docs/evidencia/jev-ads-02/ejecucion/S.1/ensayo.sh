#!/usr/bin/env bash
# Ensayo de S.1 en una base DESECHABLE local con el esquema REAL de produccion.
# Toma el esquema de prod con orbit_read (solo lectura), lo carga en una base
# nueva y comprueba:
#   1) 0051 aplica con psql -1 sobre el esquema de prod;
#   2) la reversa 0051 devuelve el esquema EXACTO de prod;
#   3) reaplicar 0051 da el mismo esquema que la primera vez;
#   4) permisos.sql reporta "permisos OK" con el acta aplicada.
# A diferencia de 2.3, prod YA tiene tablas Jev (se exigen) y el volcado trae
# permisos de app_jev, asi que ese rol se crea en el cluster local antes de
# cargarlo (si falta). Nunca imprime el DSN local.
# Uso: cd ~/dev/goncloud-Orbit && bash docs/evidencia/jev-ads-02/ejecucion/S.1/ensayo.sh
set -euo pipefail

REPO=$(git rev-parse --show-toplevel)
DSN_LOCAL=${ORBIT_TEST_DSN:-postgresql://orbit:orbit@localhost:5432/postgres}
DB=orbit_ensayo_jev_s_1
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
grep -q 'CREATE TABLE public.jev_revision ' "$TMP/prod.sql" || { echo "ABORTA: el dump no trae tablas Jev (prod sin 0049?)"; exit 1; }
if grep -q 'ads_listado_plataforma' "$TMP/prod.sql"; then echo "ABORTA: prod ya tiene el acta (0051 ya aplicada)"; exit 1; fi

if [ -n "$(psql "$DSN_LOCAL" -tAc "SELECT 1 FROM pg_database WHERE datname = '$DB'")" ]; then
  echo "ABORTA: ya existe la base local $DB (otra corrida viva, una que murio sin limpiar o una ajena); revisala y borrala a mano si es de un ensayo"; exit 1
fi
psql "$DSN_LOCAL" -qc "CREATE DATABASE $DB" >/dev/null
CREADA=1
psql "$DSN_LOCAL" -q -v ON_ERROR_STOP=1 -c "DO \$\$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_jev') THEN CREATE ROLE app_jev NOLOGIN; END IF; END \$\$;" >/dev/null
echo "rol app_jev garantizado en el cluster local"
psql "$DSN_DB" -q -v ON_ERROR_STOP=1 -f "$TMP/prod.sql" >/dev/null
volcar a_prod
echo "esquema de prod cargado: $(grep -c '^CREATE TABLE' "$TMP/a_prod.sql") tablas"

echo "== 1) aplicar 0051"
aplicar 0051_ads_acta_listado.sql
volcar b_con_acta
echo "tablas del acta: $(grep -c '^CREATE TABLE public.ads_listado_' "$TMP/b_con_acta.sql")"

echo "== 2) reversa 0051"
aplicar 0051_reversa_ads_acta_listado.sql
volcar c_revertido
if diff -u "$TMP/a_prod.sql" "$TMP/c_revertido.sql"; then
  echo "OK: la reversa deja el esquema identico al de prod"
else
  echo "FALLA: la reversa no deja el esquema de prod"; exit 1
fi

echo "== 3) reaplicar 0051"
aplicar 0051_ads_acta_listado.sql
volcar d_reaplicado
if diff -u "$TMP/b_con_acta.sql" "$TMP/d_reaplicado.sql"; then
  echo "OK: reaplicar da el mismo esquema que la primera vez"
else
  echo "FALLA: reaplicar no reproduce el esquema"; exit 1
fi

echo "== 4) permisos esperados (los mismos que verifica checklist.sh en prod)"
R=$(psql "$DSN_DB" -tA -v ON_ERROR_STOP=1 -f "$REPO/docs/evidencia/jev-ads-02/ejecucion/S.1/permisos.sql")
echo "$R"
[ "$R" = "permisos OK" ] || exit 1
echo "ENSAYO OK"
