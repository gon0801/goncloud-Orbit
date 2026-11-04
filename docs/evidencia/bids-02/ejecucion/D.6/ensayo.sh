#!/usr/bin/env bash
# Ensayo conjunto de D.6 (BIDS 02 remate R14) en una base DESECHABLE
# local: aplica la 0071 y la 0072 sobre el esquema base, verifica las
# senales de cada una (permisos.sql de R09-E y R09-J), aplica las
# reversas en orden inverso y exige el esquema EXACTO de partida, y
# reaplica. Patron: docs/evidencia/bids-02/ejecucion/R09-E/ensayo.sh.
# La base se prepara sin el GRANT explicito de la 0071 (en produccion
# la vista ya se lee por default privileges; el REVOKE preparatorio
# corre solo en la base del ensayo). En simulacion
# (ORBIT_SIMULACION=1) el "prod" es la base local ORBIT_ENSAYO_DSN
# (esquema completo sin la 0071 ni la 0072) y no se toca la red.
# Uso real: cd ~/dev/goncloud-Orbit && bash docs/evidencia/bids-02/ejecucion/D.6/ensayo.sh
# Uso sim:  ORBIT_SIMULACION=1 ORBIT_ENSAYO_DSN=postgresql://orbit:orbit@127.0.0.1:5433/orbit_copia_d6 bash docs/evidencia/bids-02/ejecucion/D.6/ensayo.sh
set -euo pipefail

REPO=$(git rev-parse --show-toplevel)
SIM=${ORBIT_SIMULACION:-0}
DSN_LOCAL=${ORBIT_TEST_DSN:-postgresql://orbit:orbit@localhost:5432/postgres}
DSN_PROD_SIM=${ORBIT_ENSAYO_DSN:-$DSN_LOCAL}
DB=orbit_ensayo_bids_d6
TMP=$(mktemp -d)
CREADA=0
limpiar() {
  [ "$CREADA" = 1 ] && psql "$DSN_LOCAL" -qc "DROP DATABASE IF EXISTS $DB WITH (FORCE)" >/dev/null
  rm -rf "$TMP"
}
trap limpiar EXIT
DSN_DB="${DSN_LOCAL%/*}/$DB"
cd "$REPO"
DIR=docs/evidencia/bids-02/ejecucion/D.6
MIGS="0071_bids02_grant_vista_impulso.sql 0072_bids02_impulso_grupo_tipo.sql"

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
  P71=$(psql "$DSN_DB" -X -q -tA -v ON_ERROR_STOP=1 -f "$REPO/docs/evidencia/bids-02/ejecucion/R09-E/permisos.sql")
  P72=$(psql "$DSN_DB" -X -q -tA -v ON_ERROR_STOP=1 -f "$REPO/docs/evidencia/bids-02/ejecucion/R09-J/permisos.sql")
  echo "0071: $P71"
  echo "0072: $P72"
}

if [ "$SIM" = 1 ]; then
  echo "== MODO SIMULACION: el esquema base sale de $DSN_PROD_SIM, no de prod"
  pg_dump "$DSN_PROD_SIM" --schema-only > "$TMP/prod.sql"
else
  echo "== esquema de prod (solo lectura)"
  ssh goncloud 'DSN=$(grep -E "^ORBIT_DSN_READ=" /mnt/data/appdata/orbit/.env | head -1 | cut -d= -f2-); docker exec orbit-db-1 pg_dump "$DSN" --schema-only' \
    > "$TMP/prod.sql"
fi
grep -q 'CREATE VIEW public.v_impulso_estado' "$TMP/prod.sql" || { echo "ABORTA: el esquema base no trae v_impulso_estado (falta la 0065)"; exit 1; }
if grep -q 'impulso_grupo_tipo' "$TMP/prod.sql"; then
  echo "ABORTA: el esquema base ya trae el candado de la 0072 (la base no sirve de partida)"; exit 1
fi

if [ -n "$(psql "$DSN_LOCAL" -tAc "SELECT 1 FROM pg_database WHERE datname = '$DB'")" ]; then
  echo "ABORTA: ya existe la base local $DB; revisala y borrala a mano si es de un ensayo"; exit 1
fi
psql "$DSN_LOCAL" -qc "CREATE DATABASE $DB" >/dev/null
CREADA=1
psql "$DSN_DB" -q -v ON_ERROR_STOP=1 -f "$TMP/prod.sql" >/dev/null
psql "$DSN_DB" -q -v ON_ERROR_STOP=1 -c "REVOKE SELECT ON v_impulso_estado FROM app_read, app_admin" >/dev/null
volcar a_base
echo "esquema base cargado y sin el GRANT explicito"

echo "== 1) aplicar la 0071 y la 0072"
for m in $MIGS; do aplicar "$m"; done

echo "== 2) senales de cada migracion (esperado: permisos OK en las dos)"
senales
P71=$(psql "$DSN_DB" -X -q -tA -v ON_ERROR_STOP=1 -f "$REPO/docs/evidencia/bids-02/ejecucion/R09-E/permisos.sql")
P72=$(psql "$DSN_DB" -X -q -tA -v ON_ERROR_STOP=1 -f "$REPO/docs/evidencia/bids-02/ejecucion/R09-J/permisos.sql")
[ "$P71" = "permisos OK" ] && [ "$P72" = "permisos OK" ] || { echo "FALLA: senales tras aplicar"; exit 1; }
volcar b_aplicado

echo "== 3) reversas en orden inverso: esquema identico al base"
REVS=""
for m in $MIGS; do REVS="$(reversa_de "$m") $REVS"; done
for r in $REVS; do aplicar "$r"; done
volcar c_revertido
if diff -u "$TMP/a_base.sql" "$TMP/c_revertido.sql"; then
  echo "OK: las reversas dejan el esquema identico al base"
else
  echo "FALLA: las reversas no devuelven el esquema base"; exit 1
fi
senales
P71=$(psql "$DSN_DB" -X -q -tA -v ON_ERROR_STOP=1 -f "$REPO/docs/evidencia/bids-02/ejecucion/R09-E/permisos.sql")
P72=$(psql "$DSN_DB" -X -q -tA -v ON_ERROR_STOP=1 -f "$REPO/docs/evidencia/bids-02/ejecucion/R09-J/permisos.sql")
[ "$P71" != "permisos OK" ] && [ "$P72" != "permisos OK" ] || { echo "FALLA: las senales siguen OK tras reversar"; exit 1; }
echo "OK: tras reversar, las senales de la 0071 y la 0072 ya no dan permisos OK"

echo "== 4) reaplicar la 0071 y la 0072"
for m in $MIGS; do aplicar "$m"; done
volcar d_reaplicado
if diff -u "$TMP/b_aplicado.sql" "$TMP/d_reaplicado.sql"; then
  echo "OK: reaplicar da el mismo esquema que la primera vez"
else
  echo "FALLA: reaplicar no reproduce el esquema"; exit 1
fi
senales

echo "ENSAYO OK"
