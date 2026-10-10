#!/usr/bin/env bash
# Ensayo de 0.b sobre la copia de produccion (la arma copia.sh). Comprueba:
#   1) 0060 aplica con psql -1 sobre el esquema de prod;
#   2) control.sql: difieren da 0 y control es igual a vista;
#   3) permisos.sql dice "permisos OK" sobre el esquema con 0060;
#   4) la reversa de 0060 devuelve el esquema EXACTO de prod.
# No toca produccion (la copia ya existe); jamas escribe ahi. No imprime
# DSNs ni credenciales. Deja la copia en su sitio, revertida.
# Uso: cd ~/dev/goncloud-Orbit && bash docs/evidencia/bids-02/ejecucion/0.b/copia.sh && bash docs/evidencia/bids-02/ejecucion/0.b/ensayo.sh
set -euo pipefail

REPO=$(git rev-parse --show-toplevel)
DIR="$REPO/docs/evidencia/bids-02/ejecucion/0.b"
DSN_LOCAL=${ORBIT_TEST_DSN:-postgresql://orbit:orbit@localhost:5432/postgres}
DB=orbit_copia_bids02_0b
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
DSN_DB="${DSN_LOCAL%/*}/$DB"

# Como S.3: se quitan las restrict y se ordena cada bloque de GRANT seguidos;
# cualquier GRANT de mas, de menos o distinto sigue rompiendo el diff.
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

if [ -z "$(psql "$DSN_LOCAL" -tAc "SELECT 1 FROM pg_database WHERE datname = '$DB'")" ]; then
  echo "ABORTA: no existe la base local $DB; corre copia.sh primero"; exit 1
fi
if [ -n "$(psql "$DSN_DB" -tAc "SELECT 1 FROM pg_views WHERE schemaname = 'public' AND viewname = 'v_hoja_activa'")" ]; then
  echo "ABORTA: la copia ya trae v_hoja_activa (un ensayo anterior murio a medias); borra $DB a mano y corre copia.sh de nuevo"; exit 1
fi
volcar a_copia
echo "esquema de la copia: $(grep -c '^CREATE TABLE' "$TMP/a_copia.sql") tablas"

echo "== 1) aplicar 0060"
aplicar 0060_bids02_base_lectura.sql

echo "== 2) control: vista contra tablas base"
R=$(psql "$DSN_DB" -tA -F'|' -v ON_ERROR_STOP=1 -f "$DIR/control.sql")
echo "control|vista|difieren = $R"
CONTROL=${R%%|*}; RESTO=${R#*|}; VISTA=${RESTO%%|*}; DIFIEREN=${RESTO#*|}
[ "$DIFIEREN" = 0 ] && [ "$CONTROL" = "$VISTA" ] || { echo "FALLA: la vista difiere del control"; exit 1; }
echo "OK: difieren = 0 y control = vista ($CONTROL hojas)"

echo "== 3) permisos esperados (los mismos que verifica el DO de 0060)"
P=$(psql "$DSN_DB" -tA -v ON_ERROR_STOP=1 -f "$DIR/permisos.sql")
echo "$P"
[ "$P" = "permisos OK" ] || exit 1

echo "== 4) reversa de 0060"
aplicar 0060_reversa_bids02_base_lectura.sql
volcar c_revertido
if diff -u "$TMP/a_copia.sql" "$TMP/c_revertido.sql"; then
  echo "OK: la reversa deja el esquema identico al de produccion"
else
  echo "FALLA: la reversa no deja el esquema de prod"; exit 1
fi
echo "ENSAYO OK"
