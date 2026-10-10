#!/usr/bin/env bash
# Reversa del despliegue D.1 (BIDS 02 seccion 2): restaura el codigo de
# predeploy-<sello> y aplica las reversas de aplicadas-<sello>.txt en orden
# inverso. Patron: docs/evidencia/jev-ads-02/ejecucion/S.3/rollback.sh.
# Dos diferencias propias de D.1. (1) Si una plataforma trae la clave de
# politica, X.1 ya corrio: el rollback corre primero
# docs/evidencia/bids-02/ejecucion/X.1/apagar.sh, que quita la clave (el
# codigo anterior falla cerrado con niveles_v3). Sin apgar.sh a la mano,
# aborta antes de tocar nada. (2) NO regresa la fraccion: 0.8 es la
# decision D1 y vale tambien con el codigo anterior. D.1 no instalo lineas
# de cron: no hay nada que quitar.
# En simulacion (ORBIT_SIMULACION=1) la base es ORBIT_SIM_DSN, el respaldo
# y aplicadas-<sello>.txt viven en ${TMPDIR:-/tmp}/orbit-d1-sim y el codigo
# no se restaura a ningun servidor (no hay). Solo en simulacion se acepta
# ORBIT_APAGAR_SH_OVERRIDE para probar el orden de llamada; en modo real la
# ruta de apgar.sh es siempre la fija.
# Uso: bash docs/evidencia/bids-02/ejecucion/D.1/rollback.sh <STAMP impreso por desplegar.sh>
set -euo pipefail

STAMP=${1:?uso: rollback.sh <STAMP impreso por desplegar.sh>}
REPO=$(git rev-parse --show-toplevel)
SRV=/mnt/data/appdata/orbit
DIR=docs/evidencia/bids-02/ejecucion/D.1
SIM=${ORBIT_SIMULACION:-0}
DSN_SIM=${ORBIT_SIM_DSN:-postgresql://orbit:orbit@127.0.0.1:5433/orbit_sim_d1}
TMPD=${TMPDIR:-/tmp}/orbit-d1-sim
cd "$REPO"

q() {
  if [ "$SIM" = 1 ]; then
    psql "$DSN_SIM" -X -q -tA -v ON_ERROR_STOP=1
  else
    ssh goncloud 'DSN=$(docker exec orbit-app-1 printenv ORBIT_DSN_READ); docker exec -i orbit-db-1 psql "$DSN" -X -q -tA -v ON_ERROR_STOP=1'
  fi
}
w() {
  if [ "$SIM" = 1 ]; then
    psql "$DSN_SIM" -q -v ON_ERROR_STOP=1 -1
  else
    ssh goncloud 'docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1 -1'
  fi
}

if [ "$SIM" = 1 ]; then
  echo "== MODO SIMULACION: datos reales contra $DSN_SIM, servidor intacto"
  PRE="$TMPD/predeploy-$STAMP"
  APLICADAS="$TMPD/aplicadas-$STAMP.txt"
  [ -d "$PRE" ] || { echo "ABORTA: no existe $PRE (corre desplegar.sh en simulacion primero)"; exit 1; }
  SHA=$(cat "$PRE/SHA") || { echo "ABORTA: $PRE sin SHA"; exit 1; }
else
  PRE="$SRV/predeploy-$STAMP"
  APLICADAS="$DIR/aplicadas-$STAMP.txt"
  SHA=$(ssh goncloud "cat $PRE/SHA") || { echo "ABORTA: no existe $PRE/SHA"; exit 1; }
  git fetch -q origin
fi
[ -f "$APLICADAS" ] || { echo "ABORTA: no existe $APLICADAS"; exit 1; }
echo "SHA=$SHA"

echo "== 0) Reversas de aplicadas-$STAMP.txt existen en el SHA, ANTES de tocar nada"
REVS=""
while IFS= read -r m; do
  [ -n "$m" ] || continue
  n=$(basename "$m" .sql | cut -c1-4)
  resto=$(basename "$m" .sql | cut -c6-)
  r="${n}_reversa_${resto}.sql"
  git cat-file -e "$SHA:migrations/$r" || { echo "ABORTA: la reversa $r no existe en $SHA"; exit 1; }
  REVS="$r $REVS"
done < "$APLICADAS"
echo "reversas en orden: ${REVS:-ninguna}"

echo "== 0b) Guardas (ciclos en running abortan)"
if [ "$SIM" = 1 ]; then
  RUNNING=$(echo "SELECT count(*) FROM optimizer_cycle WHERE status = 'running';" | q)
  [ "$RUNNING" = "0" ] || { echo "ABORTA: hay $RUNNING ciclo(s) en running"; exit 1; }
  echo "guardas OK (sin ciclos en running)"
else
  RUNNING=$(ssh goncloud "docker inspect -f '{{.State.Running}}' orbit-app-1") || { echo "ABORTA: docker inspect no contesta"; exit 1; }
  if [ "$RUNNING" = "true" ]; then
    TOP=$(ssh goncloud "docker top orbit-app-1") || { echo "ABORTA: docker top no contesta"; exit 1; }
    CLI=$(printf '%s\n' "$TOP" | grep -c 'app\.cli' || true)
    [ "$CLI" = "0" ] || { echo "ABORTA: hay app.cli corriendo"; exit 1; }
    echo "guardas OK (contenedor arriba, sin app.cli)"
  else
    echo "contenedor parado: la reversa corre sin guardas de proceso (no hay nada que interrumpir)"
  fi
  RC=$(echo "SELECT count(*) FROM optimizer_cycle WHERE status = 'running';" | q)
  [ "$RC" = "0" ] || { echo "ABORTA: hay $RC ciclo(s) en running"; exit 1; }
fi

echo "== 0c) Clave de politica: si X.1 ya corrio, apgar.sh va primero"
CLAVES=$(q <<'SQL'
SELECT settings ? 'ads_bid_politica_amazon_mx', settings ? 'ads_bid_politica_amazon_us' FROM config_version ORDER BY id DESC LIMIT 1;
SQL
)
echo "claves de politica mx|us: ${CLAVES:-vacio}"
if [ "$CLAVES" != "f|f" ]; then
  echo "X.1 ya corrio en alguna plataforma: el codigo anterior falla cerrado con niveles_v3, asi que primero se quita la clave"
  APAGAR="docs/evidencia/bids-02/ejecucion/X.1/apagar.sh"
  if [ "$SIM" = 1 ] && [ -n "${ORBIT_APAGAR_SH_OVERRIDE:-}" ]; then
    APAGAR="$ORBIT_APAGAR_SH_OVERRIDE"
    echo "SIMULACION: override de pruebas -> $APAGAR"
  fi
  [ -f "$APAGAR" ] || { echo "ABORTA: falta $APAGAR (lo escribe X.1); sin el no se puede reversar con la politica encendida"; exit 1; }
  bash "$APAGAR"
  CLAVES=$(q <<'SQL'
SELECT settings ? 'ads_bid_politica_amazon_mx', settings ? 'ads_bid_politica_amazon_us' FROM config_version ORDER BY id DESC LIMIT 1;
SQL
)
  [ "$CLAVES" = "f|f" ] || { echo "ABORTA: apgar.sh corrio pero las claves siguen en $CLAVES"; exit 1; }
  echo "apgar.sh OK: claves de vuelta en f|f"
else
  echo "X.1 no ha corrido: nada que apagar"
fi

echo "== 1) Cron (D.1 no instalo lineas: nada que quitar) + restaurar codigo de predeploy-$STAMP"
if [ "$SIM" = 1 ]; then
  echo "SIMULACION: respaldo $PRE presente; sin servidor, nada que restaurar ni reconstruir"
else
  ssh goncloud "crontab -l -u gon | grep -c -E 'cycle --platform'" || { echo "ABORTA: el crontab perdio las lineas del ciclo"; exit 1; }
  ssh goncloud "set -e; cd $SRV; [ -d predeploy-$STAMP/app ] || { echo 'ABORTA: no existe predeploy-$STAMP'; exit 1; }; \
    [ -f predeploy-$STAMP/docker-compose.yml ] || { echo 'ABORTA: predeploy-$STAMP sin docker-compose.yml'; exit 1; }; \
    rm -rf app tools; cp -a predeploy-$STAMP/app predeploy-$STAMP/tools .; \
    cp -a predeploy-$STAMP/Dockerfile predeploy-$STAMP/.dockerignore predeploy-$STAMP/pyproject.toml predeploy-$STAMP/uv.lock predeploy-$STAMP/docker-compose.yml .; \
    echo DIGEST antes=\$(docker inspect -f '{{.Image}}' orbit-app-1); \
    docker compose up -d --no-deps --build app; \
    echo DIGEST despues=\$(docker inspect -f '{{.Image}}' orbit-app-1); \
    sleep 5; curl -sS http://127.0.0.1:8010/health; echo"
fi

echo "== 2) Reversas en orden inverso"
if [ -z "$REVS" ]; then echo "ninguna migracion aplicada por este despliegue: nada que reversar"; else
  for r in $REVS; do
    git show "$SHA:migrations/$r" | w
    echo "reversa aplicada $r"
  done
fi

echo "== 3) Verificacion (esperado: vistas f|f, CHECK viejo f, fraccion 0.8, claves f|f)"
VISTAS=$(echo "SELECT to_regclass('public.v_hoja_activa') IS NOT NULL, to_regclass('public.v_cambio_bid') IS NOT NULL;" | q)
CHECK63=$(echo "SELECT pg_get_constraintdef(oid) NOT LIKE '%cache_estado%' FROM pg_constraint WHERE conname = 'target_acos_ciclo_procedencia_check';" | q)
FRACC=$(echo "SELECT settings ->> 'ads_target_fraccion_margen_amazon_us' FROM config_version ORDER BY id DESC LIMIT 1;" | q)
CLAVES=$(q <<'SQL'
SELECT settings ? 'ads_bid_politica_amazon_mx', settings ? 'ads_bid_politica_amazon_us' FROM config_version ORDER BY id DESC LIMIT 1;
SQL
)
echo "vistas=$VISTAS check63_nuevo=$CHECK63 fraccion=$FRACC claves=$CLAVES"
[ "$VISTAS" = "f|f" ] || { echo "FALLA: las vistas de la 0060 siguen"; exit 1; }
[ "$CHECK63" = "f" ] || { echo "FALLA: el CHECK de la 0063 no volvio al de 7 peldanos"; exit 1; }
[ "$FRACC" = "0.8" ] || { echo "FALLA: la fraccion se movio a '$FRACC' (debio quedar en 0.8: decision D1)"; exit 1; }
[ "$CLAVES" = "f|f" ] || { echo "FALLA: las claves quedaron en $CLAVES"; exit 1; }
echo "== REVERSA LISTA. La fraccion 0.8 queda a proposito (decision D1: vale tambien con el codigo anterior)."
