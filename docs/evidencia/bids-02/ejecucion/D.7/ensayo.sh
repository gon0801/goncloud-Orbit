#!/usr/bin/env bash
# Ensayo de D.7 (BIDS 02 seccion 9) en una base DESECHABLE local:
# este bloque no trae migraciones, asi que el ensayo corre el ciclo
# completo del despliegue en simulacion (desplegar.sh y rollback.sh
# contra la base desechable) y exige el esquema EXACTO de partida
# tras los dos: el despliegue solo mueve codigo y no deja huella en
# la base. En simulacion (ORBIT_SIMULACION=1) el "prod" es la base
# local ORBIT_ENSAYO_DSN (esquema completo vigente) y no se toca la
# red. El modo real esta bloqueado a proposito: sin migraciones no
# hay nada que ensayar contra produccion que la simulacion no cubra.
# Uso sim:  ORBIT_SIMULACION=1 ORBIT_ENSAYO_DSN=postgresql://orbit:orbit@127.0.0.1:5433/orbit_copia_d7 bash docs/evidencia/bids-02/ejecucion/D.7/ensayo.sh <sha>
set -euo pipefail

APROBADO=${1:?uso: ensayo.sh <sha a desplegar>}
SIM=${ORBIT_SIMULACION:-0}
[ "$SIM" = 1 ] || { echo "ABORTA: el ensayo de D.7 solo corre en simulacion (ORBIT_SIMULACION=1)"; exit 1; }
REPO=$(git rev-parse --show-toplevel)
DSN_LOCAL=${ORBIT_TEST_DSN:-postgresql://orbit:orbit@localhost:5432/postgres}
DSN_PROD_SIM=${ORBIT_ENSAYO_DSN:-$DSN_LOCAL}
DB=orbit_ensayo_bids_d7
TMP=$(mktemp -d)
CREADA=0
limpiar() {
  [ "$CREADA" = 1 ] && psql "$DSN_LOCAL" -qc "DROP DATABASE IF EXISTS $DB WITH (FORCE)" >/dev/null
  rm -rf "$TMP"
}
trap limpiar EXIT
DSN_DB="${DSN_LOCAL%/*}/$DB"
cd "$REPO"
DIR=docs/evidencia/bids-02/ejecucion/D.7

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

echo "== MODO SIMULACION: el esquema base sale de $DSN_PROD_SIM, no de prod"
pg_dump "$DSN_PROD_SIM" --schema-only > "$TMP/prod.sql"
grep -q 'CREATE TABLE public.campana_ajuste' "$TMP/prod.sql" || { echo "ABORTA: el esquema base no trae campana_ajuste (falta la 0067)"; exit 1; }

if [ -n "$(psql "$DSN_LOCAL" -tAc "SELECT 1 FROM pg_database WHERE datname = '$DB'")" ]; then
  echo "ABORTA: ya existe la base local $DB; revisala y borrala a mano si es de un ensayo"; exit 1
fi
psql "$DSN_LOCAL" -qc "CREATE DATABASE $DB" >/dev/null
CREADA=1
psql "$DSN_DB" -q -v ON_ERROR_STOP=1 -f "$TMP/prod.sql" >/dev/null
volcar a_base
echo "esquema base cargado"

echo "== 1) desplegar.sh en simulacion contra la base del ensayo"
ORBIT_SIMULACION=1 ORBIT_SIM_DSN="$DSN_DB" bash "$DIR/desplegar.sh" "$APROBADO" > "$TMP/desplegar.txt" 2>&1 || {
  echo "FALLA: desplegar.sh no salio 0 en el ensayo"; tail -5 "$TMP/desplegar.txt"; exit 1
}
STAMP=$(grep -o 'STAMP=[0-9-]*' "$TMP/desplegar.txt" | tail -1 | cut -d= -f2)
[ -n "$STAMP" ] || { echo "FALLA: desplegar.sh no imprimio su STAMP"; exit 1; }
echo "desplegar OK (STAMP=$STAMP)"
volcar b_desplegado
if diff -u "$TMP/a_base.sql" "$TMP/b_desplegado.sql" > "$TMP/diff-b.txt"; then
  echo "OK: tras desplegar, el esquema es identico al base (sin migraciones)"
else
  echo "FALLA: el despliegue movio el esquema"; head -20 "$TMP/diff-b.txt"; exit 1
fi

echo "== 2) rollback.sh en simulacion con el mismo sello"
ORBIT_SIMULACION=1 ORBIT_SIM_DSN="$DSN_DB" bash "$DIR/rollback.sh" "$STAMP" > "$TMP/rollback.txt" 2>&1 || {
  echo "FALLA: rollback.sh no salio 0 en el ensayo"; tail -5 "$TMP/rollback.txt"; exit 1
}
echo "rollback OK"
volcar c_revertido
if diff -u "$TMP/a_base.sql" "$TMP/c_revertido.sql" > "$TMP/diff-c.txt"; then
  echo "OK: tras la reversa, el esquema es identico al base"
else
  echo "FALLA: la reversa movio el esquema"; head -20 "$TMP/diff-c.txt"; exit 1
fi

echo "ENSAYO OK"
