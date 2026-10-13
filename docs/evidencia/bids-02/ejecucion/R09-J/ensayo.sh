#!/usr/bin/env bash
# Ensayo de la 0072 (BIDS 02 R09-J) en una base DESECHABLE local.
# Patron: docs/evidencia/bids-02/ejecucion/R09-E/ensayo.sh. Comprueba:
#   1) la 0072 aplica con psql -1 sobre el esquema base;
#   2) permisos.sql dice "permisos OK" (trigger y funcion presentes);
#   3) la reversa devuelve el esquema EXACTO de partida;
#   4) reaplicar deja el mismo esquema y permisos.sql vuelve a OK.
# La conducta (rechazar un impulso sobre un grupo fabrica5) la fija
# tests/test_migracion_0072.py. En simulacion (ORBIT_SIMULACION=1) el
# "prod" es la base local ORBIT_ENSAYO_DSN (esquema sin la 0072).
# Uso real: cd ~/dev/goncloud-Orbit && bash docs/evidencia/bids-02/ejecucion/R09-J/ensayo.sh
# Uso sim:  ORBIT_SIMULACION=1 ORBIT_ENSAYO_DSN=postgresql://orbit:orbit@127.0.0.1:5433/orbit_copia_r09e bash docs/evidencia/bids-02/ejecucion/R09-J/ensayo.sh
set -euo pipefail

REPO=$(git rev-parse --show-toplevel)
SIM=${ORBIT_SIMULACION:-0}
DSN_LOCAL=${ORBIT_TEST_DSN:-postgresql://orbit:orbit@localhost:5432/postgres}
DSN_PROD_SIM=${ORBIT_ENSAYO_DSN:-$DSN_LOCAL}
DB=orbit_ensayo_bids_r09j
TMP=$(mktemp -d)
CREADA=0
limpiar() {
  [ "$CREADA" = 1 ] && psql "$DSN_LOCAL" -qc "DROP DATABASE IF EXISTS $DB WITH (FORCE)" >/dev/null
  rm -rf "$TMP"
}
trap limpiar EXIT
DSN_DB="${DSN_LOCAL%/*}/$DB"
cd "$REPO"
DIR=docs/evidencia/bids-02/ejecucion/R09-J
MIG=0072_bids02_impulso_grupo_tipo.sql
REV=0072_reversa_bids02_impulso_grupo_tipo.sql

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
permisos() { psql "$DSN_DB" -X -q -tA -v ON_ERROR_STOP=1 -f "$REPO/$DIR/permisos.sql"; }

if [ "$SIM" = 1 ]; then
  echo "== MODO SIMULACION: el esquema base sale de $DSN_PROD_SIM, no de prod"
  pg_dump "$DSN_PROD_SIM" --schema-only > "$TMP/prod.sql"
else
  echo "== esquema de prod (solo lectura)"
  ssh goncloud 'DSN=$(docker exec orbit-app-1 printenv ORBIT_DSN_READ); docker exec orbit-db-1 pg_dump "$DSN" --schema-only' \
    > "$TMP/prod.sql"
fi
grep -q 'CREATE TABLE public.impulso ' "$TMP/prod.sql" || { echo "ABORTA: el esquema base no trae la tabla impulso (falta la 0065)"; exit 1; }
if grep -q 'impulso_grupo_tipo' "$TMP/prod.sql"; then
  echo "ABORTA: el esquema base ya trae el candado de la 0072 (la base no sirve de partida)"; exit 1
fi

if [ -n "$(psql "$DSN_LOCAL" -tAc "SELECT 1 FROM pg_database WHERE datname = '$DB'")" ]; then
  echo "ABORTA: ya existe la base local $DB (otra corrida viva, una que murio sin limpiar o una ajena); revisala y borrala a mano si es de un ensayo"; exit 1
fi
psql "$DSN_LOCAL" -qc "CREATE DATABASE $DB" >/dev/null
CREADA=1
psql "$DSN_DB" -q -v ON_ERROR_STOP=1 -f "$TMP/prod.sql" >/dev/null
volcar a_base
echo "esquema base cargado"

echo "== 1) aplicar la 0072"
aplicar "$MIG"

echo "== 2) senales del candado (esperado: permisos OK)"
P=$(permisos)
echo "$P"
[ "$P" = "permisos OK" ] || { echo "FALLA: senales tras aplicar"; exit 1; }
volcar b_aplicado

echo "== 3) reversa: esquema identico al base"
aplicar "$REV"
volcar c_revertido
if diff -u "$TMP/a_base.sql" "$TMP/c_revertido.sql"; then
  echo "OK: la reversa deja el esquema identico al base"
else
  echo "FALLA: la reversa no devuelve el esquema base"; exit 1
fi
P=$(permisos)
echo "$P"
[ "$P" != "permisos OK" ] || { echo "FALLA: la reversa dejo el candado puesto"; exit 1; }

echo "== 4) reaplicar la 0072"
aplicar "$MIG"
volcar d_reaplicado
if diff -u "$TMP/b_aplicado.sql" "$TMP/d_reaplicado.sql"; then
  echo "OK: reaplicar da el mismo esquema que la primera vez"
else
  echo "FALLA: reaplicar no reproduce el esquema"; exit 1
fi
P=$(permisos)
echo "$P"
[ "$P" = "permisos OK" ] || { echo "FALLA: senales tras reaplicar"; exit 1; }

echo "ENSAYO OK"
