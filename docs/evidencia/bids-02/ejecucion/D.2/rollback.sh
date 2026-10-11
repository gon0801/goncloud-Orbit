#!/usr/bin/env bash
# Reversa del despliegue D.2 (BIDS 02 seccion 3): quita las dos lineas de
# cron de D.2 del crontab ACTUAL de gon, restaura el codigo de
# predeploy-<sello> y aplica las reversas de aplicadas-<sello>.txt en
# orden inverso. Patron: docs/evidencia/bids-02/ejecucion/D.1/rollback.sh.
# D.2 si instalo lineas de cron: el respaldo del crontab lo guardo
# desplegar.sh en $SRV/backups/crontab-gon-pre-d2-<sello>.txt pero la
# reversa NO lo restaura completo (E7: otro loop pudo cambiar el crontab
# despues; restaurarlo borraria lo ajeno). Quita EXACTAMENTE las dos
# lineas instaladas, por texto exacto del DEPLOY.md del SHA.
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

quitar_cron() {  # quitar_cron <actual> <nuevo> <placements> <avisos>: quita solo esas dos
  # Comparacion por texto exacto (sin espacios extremos): un comentario
  # ajeno que nombre --placements o avisos-campana NO se toca. Preserva
  # el terminador final del archivo.
  python3 - "$1" "$2" "$3" "$4" <<'PY'
import sys
actual, nuevo, placements, avisos = sys.argv[1:5]
with open(actual, "rb") as f:
    crudo = f.read().decode("utf-8")
termina_nl = crudo.endswith("\n")
lineas = crudo.splitlines()
blanco = [placements.strip(), avisos.strip()]
quedan = [ln for ln in lineas if ln.strip() not in blanco]
print(f"quitadas: {len(lineas) - len(quedan)}")
with open(nuevo, "w") as f:
    f.write("\n".join(quedan) + ("\n" if termina_nl and quedan else ""))
PY
}

echo "== 1) Cron: quitar SOLO las dos lineas de D.2 del crontab actual + restaurar codigo de predeploy-$STAMP"
# E7: la reversa puede correr un dia despues; el crontab actual manda y
# el respaldo queda como evidencia. Sin lineas de D.2 no hay nada que
# quitar (desplegar no llego a 7b): no aborta.
LINEAS_R=$(git show "$SHA:docs/DEPLOY.md" | grep -E '^[0-9].*(--placements|avisos-campana)')
[ "$(printf '%s\n' "$LINEAS_R" | grep -c .)" = 2 ] || { echo "ABORTA: DEPLOY.md del SHA no trae exactamente las dos lineas de cron"; exit 1; }
PLACEMENTS_R=$(printf '%s\n' "$LINEAS_R" | grep -- --placements)
AVISOS_R=$(printf '%s\n' "$LINEAS_R" | grep avisos-campana)
if [ "$SIM" = 1 ]; then
  if [ ! -f "$CRON_SIM" ]; then
    echo "SIMULACION: sin crontab simulado: nada que quitar"
  else
    CICLO_ANTES=$(grep -c -E 'cycle --platform' "$CRON_SIM" || true)
    quitar_cron "$CRON_SIM" "$CRON_SIM.nuevo" "$PLACEMENTS_R" "$AVISOS_R"
    if grep -q -- --placements "$CRON_SIM.nuevo" || grep -q avisos-campana "$CRON_SIM.nuevo"; then
      echo "ABORTA: tras quitar, el crontab todavia trae lineas de D.2"; exit 1
    fi
    CICLO_DESPUES=$(grep -c -E 'cycle --platform' "$CRON_SIM.nuevo" || true)
    [ "$CICLO_ANTES" = "$CICLO_DESPUES" ] || { echo "ABORTA: la quita toco las lineas del ciclo"; exit 1; }
    mv "$CRON_SIM.nuevo" "$CRON_SIM"
    echo "SIMULACION: crontab sin lineas de D.2 OK; respaldo $PRE presente; sin servidor, nada que reconstruir"
  fi
else
  mkdir -p "$TMPD"
  ssh goncloud "crontab -l -u gon 2>/dev/null" > "$TMPD/cron-actual-$STAMP.txt" || { echo "ABORTA: no se pudo leer el crontab de gon"; exit 1; }
  CICLO_ANTES=$(grep -c -E 'cycle --platform' "$TMPD/cron-actual-$STAMP.txt" || true)
  quitar_cron "$TMPD/cron-actual-$STAMP.txt" "$TMPD/cron-nuevo-$STAMP.txt" "$PLACEMENTS_R" "$AVISOS_R"
  if grep -q -- --placements "$TMPD/cron-nuevo-$STAMP.txt" || grep -q avisos-campana "$TMPD/cron-nuevo-$STAMP.txt"; then
    echo "ABORTA: tras quitar, el crontab todavia trae lineas de D.2"; exit 1
  fi
  CICLO_DESPUES=$(grep -c -E 'cycle --platform' "$TMPD/cron-nuevo-$STAMP.txt" || true)
  [ "$CICLO_ANTES" = "$CICLO_DESPUES" ] || { echo "ABORTA: la quita toco las lineas del ciclo"; exit 1; }
  cat "$TMPD/cron-nuevo-$STAMP.txt" | ssh goncloud "crontab -u gon -"
  echo "crontab sin lineas de D.2 (respaldo pre-d2-$STAMP queda como evidencia)"
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
