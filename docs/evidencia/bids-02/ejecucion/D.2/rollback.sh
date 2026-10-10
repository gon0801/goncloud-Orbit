#!/usr/bin/env bash
# Reversa del despliegue D.2 (BIDS 02 seccion 3): restaura el crontab de
# gon del respaldo pre-d2-<sello>, restaura el codigo de predeploy-<sello>
# y aplica las reversas de aplicadas-<sello>.txt en orden inverso. Patron:
# docs/evidencia/bids-02/ejecucion/D.1/rollback.sh.
# D.2 si instalo lineas de cron: el respaldo del crontab lo guardo
# desplegar.sh en $SRV/backups/crontab-gon-pre-d2-<sello>.txt y restaurarlo
# quita EXACTAMENTE lo instalado (verificado por diff antes de instalar).
# Las reversas dropean las tablas 0061/0062 con sus filas: son observaciones
# re-derivables (el siguiente sync de las 06:45 y el siguiente reporte de
# las 07:25 las vuelven a escribir) y el codigo anterior no las lee.
# En simulacion (ORBIT_SIMULACION=1) la base es ORBIT_SIM_DSN, el respaldo
# y aplicadas-<sello>.txt viven en ${TMPDIR:-/tmp}/orbit-d2-sim y el codigo
# no se restaura a ningun servidor (no hay).
# Uso: bash docs/evidencia/bids-02/ejecucion/D.2/rollback.sh <STAMP impreso por desplegar.sh>
set -euo pipefail

STAMP=${1:?uso: rollback.sh <STAMP impreso por desplegar.sh>}
REPO=$(git rev-parse --show-toplevel)
SRV=/mnt/data/appdata/orbit
DIR=docs/evidencia/bids-02/ejecucion/D.2
SIM=${ORBIT_SIMULACION:-0}
DSN_SIM=${ORBIT_SIM_DSN:-postgresql://orbit:orbit@127.0.0.1:5433/orbit_sim_d2}
TMPD=${TMPDIR:-/tmp}/orbit-d2-sim
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
  RESPALDO_CRON="$TMPD/crontab-gon-pre-d2-$STAMP.txt"
  CRON_SIM="$TMPD/crontab-gon"
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

echo "== 1) Cron: restaurar el respaldo pre-d2 (quita lo instalado) + restaurar codigo de predeploy-$STAMP"
# Sin respaldo, desplegar.sh no llego a 7b (el crontab jamas se toco):
# nada que restaurar, no aborta.
if [ "$SIM" = 1 ]; then
  if [ ! -f "$RESPALDO_CRON" ]; then
    echo "SIMULACION: sin respaldo cron (desplegar no llego a 7b): crontab intacto, nada que restaurar"
  else
    cp "$RESPALDO_CRON" "$CRON_SIM"
    echo "SIMULACION: crontab restaurado del respaldo; respaldo $PRE presente; sin servidor, nada que reconstruir"
    if grep -q -- --placements "$CRON_SIM" || grep -q avisos-campana "$CRON_SIM"; then
      echo "ABORTA: el crontab restaurado todavia trae lineas de D.2"; exit 1
    fi
    echo "crontab sin lineas de D.2 OK"
  fi
else
  if ! ssh goncloud "test -f $SRV/backups/crontab-gon-pre-d2-$STAMP.txt"; then
    echo "sin respaldo cron en el servidor (desplegar no llego a 7b): crontab intacto, nada que restaurar"
  else
    ssh goncloud "crontab -u gon $SRV/backups/crontab-gon-pre-d2-$STAMP.txt && crontab -l -u gon | grep -c -E 'cycle --platform'" || { echo "ABORTA: el crontab restaurado perdio las lineas del ciclo"; exit 1; }
    if ssh goncloud "crontab -l -u gon | grep -E -e --placements -e avisos-campana"; then
      echo "ABORTA: el crontab restaurado todavia trae lineas de D.2"; exit 1
    fi
    echo "crontab restaurado sin lineas de D.2"
  fi
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

echo "== 3) Verificacion (esperado: vista y tablas f|f|f)"
VISTAS=$(echo "SELECT to_regclass('public.v_campana_config_vigente') IS NOT NULL, to_regclass('public.ads_campana_config_observation') IS NOT NULL, to_regclass('public.ads_placement_observation') IS NOT NULL;" | q)
echo "vista|tabla61|tabla62=$VISTAS"
# Solo se exige f en lo que este despliegue aplico (aplicadas-STAMP.txt);
# una 0060/0063 que ya venia aplicada la deja otro despliegue.
case " $REVS " in
  *"0061_reversa_"*) [ "$(printf '%s' "$VISTAS" | cut -d'|' -f1,2)" = "f|f" ] || { echo "FALLA: la vista o tabla de la 0061 siguen"; exit 1; } ;;
esac
case " $REVS " in
  *"0062_reversa_"*) [ "$(printf '%s' "$VISTAS" | cut -d'|' -f3)" = "f" ] || { echo "FALLA: la tabla de la 0062 sigue"; exit 1; } ;;
esac
echo "== REVERSA LISTA. Las tablas 0061/0062 se fueron con sus filas; el siguiente sync (06:45) y el siguiente reporte (07:25) las vuelven a escribir."
