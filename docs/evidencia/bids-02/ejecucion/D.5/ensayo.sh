#!/usr/bin/env bash
# Ensayo de D.5 (BIDS 02 seccion 6) en una base DESECHABLE local.
# Patron: docs/evidencia/bids-02/ejecucion/D.4/ensayo.sh con sus
# aflojaduras: D.5 no trae migracion propia, asi que aqui no se aplica
# ni se revierte nada. En modo real toma el esquema de prod con solo
# lectura, lo carga en una base nueva y comprueba:
#   1) ninguna _bids02_ del arbol esta pendiente (si alguna lo esta,
#      es trabajo de otro despliegue: aborta, no la aplica);
#   2) el arbol trae los archivos del paso y las rutas (guardas
#      propias de D.5, las mismas de desplegar.sh);
#   3) lee_busquedas corre contra el esquema de prod en los dos
#      mercados y trae las listas vacias (la base desechable solo
#      carga el esquema, sin datos).
# En simulacion (ORBIT_SIMULACION=1) el "prod" es la base local
# ORBIT_ENSAYO_DSN (una copia prod-like, p. ej. orbit_copia_p1) y no se
# toca la red: todo es psql/pg_dump local.
# Uso real: cd ~/dev/goncloud-Orbit && bash docs/evidencia/bids-02/ejecucion/D.5/ensayo.sh
# Uso sim:  ORBIT_SIMULACION=1 ORBIT_ENSAYO_DSN=postgresql://orbit:orbit@127.0.0.1:5433/orbit_copia_p1 bash docs/evidencia/bids-02/ejecucion/D.5/ensayo.sh
set -euo pipefail

REPO=$(git rev-parse --show-toplevel)
SIM=${ORBIT_SIMULACION:-0}
DSN_LOCAL=${ORBIT_TEST_DSN:-postgresql://orbit:orbit@localhost:5432/postgres}
DSN_PROD_SIM=${ORBIT_ENSAYO_DSN:-$DSN_LOCAL}
DB=orbit_ensayo_bids_d5
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
    0068) echo "SELECT count(*) = 1 FROM information_schema.columns WHERE table_name = 'fabrica_lote' AND column_name = 'pausado_por';" ;;
    0069) echo "SELECT to_regclass('public.anuncio_retiro_intencion') IS NOT NULL;" ;;
    0070) echo "SELECT has_table_privilege('app_admin', 'public.fabrica_lote', 'DELETE');" ;;
    *) echo "ABORTA: migracion $1 sin consulta de aplicada en la guia" >&2; exit 1 ;;
  esac | psql "$DSN_DB" -X -q -tA -v ON_ERROR_STOP=1
}

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
echo "esquema base cargado"

echo "== 1) ninguna _bids02_ pendiente (D.5 no aplica migraciones)"
PENDIENTES=0
for m in migrations/*_bids02_*.sql; do
  case "$m" in *_reversa_*) continue ;; esac
  n=$(basename "$m" | cut -c1-4)
  a=$(aplicada "$n")
  [ -n "$a" ] || { echo "ABORTA: la consulta de aplicada de $n salio vacia"; exit 1; }
  if [ "$a" = "t" ]; then echo "ya aplicada: $m"; else echo "pendiente: $m"; PENDIENTES=$((PENDIENTES + 1)); fi
done
[ "$PENDIENTES" = 0 ] || { echo "ABORTA: hay $PENDIENTES migracion(es) pendiente(s); las aplica su propio despliegue, no D.5"; exit 1; }
echo "OK: 0 pendientes"

echo "== 2) guardas propias de D.5 en el arbol"
[ -f app/pantalla_busquedas.py ] || { echo "ABORTA: falta app/pantalla_busquedas.py"; exit 1; }
[ -f app/templates/busquedas_sin_venta.html ] || { echo "ABORTA: falta la plantilla busquedas_sin_venta.html"; exit 1; }
grep -q '"/busquedas-sin-venta"' app/api_dashboard.py || { echo "ABORTA: app/api_dashboard.py no trae la ruta"; exit 1; }
grep -q '"/busquedas-sin-venta"' app/ui.py || { echo "ABORTA: app/ui.py no trae la ruta"; exit 1; }
echo "OK: archivos y rutas presentes"

echo "== 3) lee_busquedas contra el esquema de prod (listas vacias sin datos)"
PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY=python3
DSN_ENSAYO="$DSN_DB" "$PY" - <<'PY'
import datetime as dt
import os

import psycopg

from app.pantalla_busquedas import lee_busquedas

hoy = dt.datetime.now(dt.UTC).date()
with psycopg.connect(os.environ["DSN_ENSAYO"]) as conn:
    for plataforma in ("amazon_mx", "amazon_us"):
        pantalla = lee_busquedas(conn, plataforma=plataforma, hoy=hoy)
        assert pantalla.asins == () and pantalla.palabras == (), pantalla
        assert pantalla.referencia.visto_pct is None, pantalla.referencia
        print(f"OK {plataforma}: moneda {pantalla.moneda}, ventana "
              f"{pantalla.desde}..{pantalla.hasta}, listas vacias")
PY
echo "ENSAYO OK"
