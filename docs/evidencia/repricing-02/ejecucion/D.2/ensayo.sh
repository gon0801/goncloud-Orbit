#!/usr/bin/env bash
# Ensayo de D.2 (REPRICING 02, S.1-S.6 + carril G) en una base DESECHABLE
# local: aplica la 0059 y la 0058 sobre el esquema real de produccion,
# verifica las senales de cada una, aplica las reversas en orden inverso
# y exige el esquema EXACTO de partida, y reaplica. Patron:
# docs/evidencia/repricing-02/ejecucion/D.1/ensayo.sh.
# DESVIACION contra la guia (documentada en evidencia.md): la guia dice que
# D.2 lleva solo la 0058, pero produccion no trae aplicada la 0059 (R03k,
# PR #453, verificada ausente por lectura el dia del paquete) y el codigo
# de S.2+ exige el trigger reparado: el ensayo y desplegar.sh aplican la
# 0059 ANTES de la 0058. La reversa de la 0059 restaura byte-exacto el
# cuerpo roto de la 0054 (verificado en el paquete), asi que el round-trip
# deja el esquema identico al base sin filtros.
# Uso: cd <repo> && bash docs/evidencia/repricing-02/ejecucion/D.2/ensayo.sh
set -euo pipefail

REPO=$(git rev-parse --show-toplevel)
DSN_LOCAL=${ORBIT_TEST_DSN:-postgresql://orbit:orbit@localhost:5432/postgres}
DB=orbit_ensayo_r02_d2
TMP=$(mktemp -d)
CREADA=0
limpiar() {
  [ "$CREADA" = 1 ] && psql "$DSN_LOCAL" -qc "DROP DATABASE IF EXISTS $DB WITH (FORCE)" >/dev/null
  rm -rf "$TMP"
}
trap limpiar EXIT
DSN_DB="${DSN_LOCAL%/*}/$DB"
cd "$REPO"
MIGS="0059_precio02_repara_retencion_coherente.sql 0058_precio02_decision_exige.sql"

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
  CHECKS=$(psql "$DSN_DB" -X -q -tA -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM pg_constraint WHERE conname IN ('precio_decision_l_exige_origen', 'precio_decision_mover_exige_verificacion');")
  REPARADO=$(psql "$DSN_DB" -X -q -tA -v ON_ERROR_STOP=1 -c "SELECT pg_get_functiondef('precio_retencion_coherente()'::regprocedure) LIKE '%v_corr_fecha%';")
  echo "0058 (2 CHECK en decision): $CHECKS"
  echo "0059 (trigger reparado): $REPARADO"
}

echo "== esquema de prod (solo lectura)"
ssh goncloud 'DSN=$(docker exec orbit-app-1 printenv ORBIT_DSN_READ); docker exec -i orbit-db-1 pg_dump "$DSN" --schema-only' \
  > "$TMP/prod.sql"
grep -q 'CREATE TABLE public.precio_decision ' "$TMP/prod.sql" || { echo "ABORTA: el esquema base no trae precio_decision"; exit 1; }
if grep -q 'precio_decision_l_exige_origen' "$TMP/prod.sql"; then
  echo "ABORTA: el esquema base ya trae la 0058 (nada que ensayar de D.2)"
  exit 1
fi
if grep -q 'v_corr_fecha' "$TMP/prod.sql"; then
  echo "ABORTA: el esquema base ya trae la 0059 (nada que ensayar de la desviacion)"
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

echo "== 1) aplicar la 0059 y la 0058 (dos corridas de psql separadas)"
for m in $MIGS; do aplicar "$m"; done
echo "(la 0059 trae candado propio: su INSERT positivo y sus 4 rechazos corrieron adentro)"

echo "== 2) senales de cada migracion"
senales
CHECKS=$(psql "$DSN_DB" -X -q -tA -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM pg_constraint WHERE conname IN ('precio_decision_l_exige_origen', 'precio_decision_mover_exige_verificacion');")
REPARADO=$(psql "$DSN_DB" -X -q -tA -v ON_ERROR_STOP=1 -c "SELECT pg_get_functiondef('precio_retencion_coherente()'::regprocedure) LIKE '%v_corr_fecha%';")
[ "$CHECKS" = "2" ] && [ "$REPARADO" = "t" ] || { echo "FALLA: senales tras aplicar"; exit 1; }
ZZ=$(psql "$DSN_DB" -X -q -tA -v ON_ERROR_STOP=1 -c "SELECT (SELECT count(*) FROM config_version WHERE label = 'zz-candado-0059') + (SELECT count(*) FROM product WHERE odoo_sku = 'zz-candado-0059');")
[ "$ZZ" = "0" ] || { echo "FALLA: el candado de la 0059 dejo $ZZ fila(s) zz"; exit 1; }
echo "OK: el candado de la 0059 no dejo filas zz"
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

echo "== 4) reaplicar la 0059 y la 0058"
for m in $MIGS; do aplicar "$m"; done
volcar d_reaplicado
if diff -u "$TMP/b_aplicado.sql" "$TMP/d_reaplicado.sql"; then
  echo "OK: reaplicar da el mismo esquema que la primera vez"
else
  echo "FALLA: reaplicar no reproduce el esquema"; exit 1
fi
senales

echo "ENSAYO OK"
