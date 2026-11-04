#!/usr/bin/env bash
# Ensayo de D.3 (BIDS 02 seccion 4) en una base DESECHABLE local.
# Patron: docs/evidencia/bids-02/ejecucion/D.1/ensayo.sh.
# En modo real toma el esquema de prod con solo lectura, lo carga en una base
# nueva y comprueba:
#   1) las _bids02_ no aplicadas (la de D.3 es 0067; 0060, 0061, 0062 y
#      las de otra seccion si siguen pendientes, como las lista
#      desplegar.sh)
#      aplican con psql -1 sobre el esquema de prod;
#   2) las reversas en orden inverso devuelven el esquema EXACTO de prod;
#   3) reaplicar da el mismo esquema que la primera vez;
#   4) cada migracion aplicada deja su huella (la consulta de "aplicada" de
#      la guia da t, como la verifica desplegar.sh).
# En simulacion (ORBIT_SIMULACION=1) el "prod" es la base local
# ORBIT_ENSAYO_DSN (una copia prod-like, p. ej. orbit_copia_p1) y no se toca
# la red: todo es psql/pg_dump local.
# Uso real: cd ~/dev/goncloud-Orbit && bash docs/evidencia/bids-02/ejecucion/D.3/ensayo.sh
# Uso sim:  ORBIT_SIMULACION=1 ORBIT_ENSAYO_DSN=postgresql://orbit:orbit@127.0.0.1:5433/orbit_copia_p1 bash docs/evidencia/bids-02/ejecucion/D.3/ensayo.sh
set -euo pipefail

REPO=$(git rev-parse --show-toplevel)
SIM=${ORBIT_SIMULACION:-0}
DSN_LOCAL=${ORBIT_TEST_DSN:-postgresql://orbit:orbit@localhost:5432/postgres}
DSN_PROD_SIM=${ORBIT_ENSAYO_DSN:-$DSN_LOCAL}
DB=orbit_ensayo_bids_d3
TMP=$(mktemp -d)
CREADA=0
limpiar() {
  [ "$CREADA" = 1 ] && psql "$DSN_LOCAL" -qc "DROP DATABASE IF EXISTS $DB WITH (FORCE)" >/dev/null
  rm -rf "$TMP"
}
trap limpiar EXIT
DSN_DB="${DSN_LOCAL%/*}/$DB"
cd "$REPO"
# aplicada <numero>: t si la migracion ya esta aplicada (tabla de la guia).
aplicada() {
  case "$1" in
    0060) echo "SELECT to_regclass('public.v_hoja_activa') IS NOT NULL;" ;;
    0061) echo "SELECT to_regclass('public.ads_campana_config_observation') IS NOT NULL;" ;;
    0062) echo "SELECT to_regclass('public.ads_placement_observation') IS NOT NULL;" ;;
    0063) echo "SELECT pg_get_constraintdef(oid) NOT LIKE '%cache_estado%' FROM pg_constraint WHERE conname = 'target_acos_ciclo_procedencia_check';" ;;
    0064) echo "SELECT count(*) = 1 FROM information_schema.columns WHERE table_name = 'campana_grupo' AND column_name = 'tipo';" ;;
    0065) echo "SELECT to_regclass('public.impulso') IS NOT NULL;" ;;
    0066) echo "SELECT to_regclass('public.anuncio_retiro') IS NOT NULL;" ;;
    0067) echo "SELECT to_regclass('public.campana_ajuste') IS NOT NULL;" ;;
    *) echo "ABORTA: migracion $1 sin consulta de aplicada en la guia" >&2; exit 1 ;;
  esac | psql "$DSN_DB" -X -q -tA -v ON_ERROR_STOP=1
}

# Mismo filtro que S.3: quita las restrict con token aleatorio y ordena cada
# bloque de GRANT seguidos (el orden no es semantico). Un GRANT de mas, de
# menos o distinto sigue rompiendo el diff.
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

if [ "$SIM" = 1 ]; then
  echo "== MODO SIMULACION: el esquema base sale de $DSN_PROD_SIM, no de prod"
  pg_dump "$DSN_PROD_SIM" --schema-only > "$TMP/prod.sql"
else
  echo "== esquema de prod (solo lectura)"
  ssh goncloud 'DSN=$(docker exec orbit-app-1 printenv ORBIT_DSN_READ); docker exec orbit-db-1 pg_dump "$DSN" --schema-only' \
    > "$TMP/prod.sql"
fi
grep -q 'CREATE TABLE public.decision ' "$TMP/prod.sql" || { echo "ABORTA: dump base invalido"; exit 1; }

if [ -n "$(psql "$DSN_LOCAL" -tAc "SELECT 1 FROM pg_database WHERE datname = '$DB'")" ]; then
  echo "ABORTA: ya existe la base local $DB (otra corrida viva, una que murio sin limpiar o una ajena); revisala y borrala a mano si es de un ensayo"; exit 1
fi
psql "$DSN_LOCAL" -qc "CREATE DATABASE $DB" >/dev/null
CREADA=1
psql "$DSN_DB" -q -v ON_ERROR_STOP=1 -f "$TMP/prod.sql" >/dev/null
volcar a_base
echo "esquema base cargado: $(grep -c '^CREATE TABLE' "$TMP/a_base.sql") tablas"

echo "== lista: _bids02_ del arbol no aplicadas en la base (como desplegar.sh)"
MIGS=""
for m in migrations/*_bids02_*.sql; do
  case "$m" in *_reversa_*) continue ;; esac
  n=$(basename "$m" | cut -c1-4)
  a=$(aplicada "$n")
  [ -n "$a" ] || { echo "ABORTA: la consulta de aplicada de $n salio vacia"; exit 1; }
  if [ "$a" = "t" ]; then echo "ya aplicada: $m"; else echo "pendiente: $m"; MIGS="$MIGS $m"; fi
done
case "$MIGS" in
  *0067_*) ;;
  *) echo "ABORTA: la base ya trae 0067, nada de D.3 que ensayar" >&2; exit 1 ;;
esac
REVS=""
for m in $MIGS; do
  base=$(basename "$m" .sql)
  r="$(echo "$base" | cut -c1-4)_reversa_$(echo "$base" | cut -c6-).sql"
  [ -f "migrations/$r" ] || { echo "ABORTA: falta migrations/$r"; exit 1; }
  REVS="$r $REVS"
done
echo "reversas: $REVS"

echo "== 1) aplicar la lista"
for m in $MIGS; do aplicar "$(basename "$m")"; done
volcar b_aplicado

echo "== 2) reversas en orden inverso"
for r in $REVS; do aplicar "$r"; done
volcar c_revertido
if diff -u "$TMP/a_base.sql" "$TMP/c_revertido.sql"; then
  echo "OK: las reversas dejan el esquema identico al base"
else
  echo "FALLA: las reversas no devuelven el esquema base"; exit 1
fi

echo "== 3) reaplicar la lista"
for m in $MIGS; do aplicar "$(basename "$m")"; done
volcar d_reaplicado
if diff -u "$TMP/b_aplicado.sql" "$TMP/d_reaplicado.sql"; then
  echo "OK: reaplicar da el mismo esquema que la primera vez"
else
  echo "FALLA: reaplicar no reproduce el esquema"; exit 1
fi

echo "== 4) huellas de lo aplicado (esperado: todo t)"
TODO_T=1
for m in $MIGS; do
  n=$(basename "$m" | cut -c1-4)
  h=$(aplicada "$n")
  echo "huella $n: ${h:-vacia}"
  [ "$h" = "t" ] || TODO_T=0
done
[ "$TODO_T" = 1 ] || { echo "FALLA: alguna huella no quedo en t"; exit 1; }
echo "ENSAYO OK"
