#!/usr/bin/env bash
# Ensayo de BIDS 02 T.1 en una base DESECHABLE local con el esquema REAL de produccion.
# Toma el esquema de prod con orbit_read (solo lectura), lo carga en una base
# nueva y comprueba:
#   1) 0063 aplica con psql -1 sobre el esquema de prod;
#   2) la reversa de 0063 devuelve el esquema EXACTO de prod;
#   3) reaplicar 0063 da el mismo esquema que la primera vez;
#   4) el CHECK vigente lista los CINCO peldanos (espejo de PELDANOS_CASCADA).
# Uso: desde la raiz del worktree (rama bids-02/s2-motor):
#   bash docs/evidencia/bids-02/ejecucion/T.1/ensayo.sh
# Jamas escribe en prod: alla solo pg_dump con el rol lector.
set -euo pipefail

REPO=$(git rev-parse --show-toplevel)
DSN_LOCAL=${ORBIT_TEST_DSN:-postgresql://orbit:orbit@localhost:5432/postgres}
DB=orbit_ensayo_bids02_t1
TMP=$(mktemp -d)
CREADA=0
limpiar() {
  [ "$CREADA" = 1 ] && psql "$DSN_LOCAL" -qc "DROP DATABASE IF EXISTS $DB WITH (FORCE)" >/dev/null
  rm -rf "$TMP"
}
trap limpiar EXIT
DSN_DB="${DSN_LOCAL%/*}/$DB"

# Normalizacion del volcado (misma que S.3): sin '\\restrict' y con cada
# bloque de GRANT seguidos ordenado (revocar y reotorgar mueve la entrada
# ACL al final aunque el conjunto quede identico). Cualquier GRANT de mas,
# de menos o distinto sigue rompiendo el diff.
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
if grep -q '0063\|bids02_peldanos' "$TMP/prod.sql"; then echo "ABORTA: prod ya trae la 0063"; exit 1; fi

if [ -n "$(psql "$DSN_LOCAL" -tAc "SELECT 1 FROM pg_database WHERE datname = '$DB'")" ]; then
  echo "ABORTA: ya existe la base local $DB (otra corrida viva, una que murio sin limpiar o una ajena); revisala y borrala a mano si es de un ensayo"; exit 1
fi
psql "$DSN_LOCAL" -qc "CREATE DATABASE $DB" >/dev/null
CREADA=1
psql "$DSN_DB" -q -v ON_ERROR_STOP=1 -f "$TMP/prod.sql" >/dev/null
volcar a_prod
echo "esquema de prod cargado: $(grep -c '^CREATE TABLE' "$TMP/a_prod.sql") tablas"

echo "== 1) aplicar 0063"
aplicar 0063_bids02_peldanos_target.sql
volcar b_con_0063
echo "CHECK tras 0063: $(psql "$DSN_DB" -tA -c "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'target_acos_ciclo_procedencia_check'")"

echo "== 2) reversa de 0063"
aplicar 0063_reversa_bids02_peldanos_target.sql
volcar c_revertido
if diff -u "$TMP/a_prod.sql" "$TMP/c_revertido.sql"; then
  echo "OK: la reversa deja el esquema identico al de prod"
else
  echo "FALLA: la reversa no deja el esquema de prod"; exit 1
fi

echo "== 3) reaplicar 0063"
aplicar 0063_bids02_peldanos_target.sql
volcar d_reaplicado
if diff -u "$TMP/b_con_0063.sql" "$TMP/d_reaplicado.sql"; then
  echo "OK: reaplicar da el mismo esquema que la primera vez"
else
  echo "FALLA: reaplicar no reproduce el esquema"; exit 1
fi

echo "== 4) el CHECK vigente lista los cinco peldanos"
R=$(psql "$DSN_DB" -tA -v ON_ERROR_STOP=1 -c "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'target_acos_ciclo_procedencia_check'" | grep -o "'[a-z_]*'" | tr '\n' ' ')
echo "$R"
[ "$R" = "'goal_campana' 'goal_plataforma' 'margen_familia' 'margen_plataforma' 'setting_plataforma' " ] || exit 1
echo "ENSAYO OK"
