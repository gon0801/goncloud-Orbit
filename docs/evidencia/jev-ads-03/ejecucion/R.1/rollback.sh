#!/usr/bin/env bash
# Reversa del despliegue R.1: SOLO CODIGO. R.1 no trae migracion.
# No toca el cron: la reversa de la linea es programar-reconcilia.sh --quitar.
# Restaura el codigo de predeploy-<STAMP> y reconstruye.
# Simulacion: ORBIT_SIMULACION=1. Exige que desplegar.sh en simulacion
# haya dejado predeploy-<STAMP>/SHA. No hay ssh ni docker.
# Uso: bash docs/evidencia/jev-ads-03/ejecucion/R.1/rollback.sh <STAMP>
set -euo pipefail

STAMP=${1:?uso: rollback.sh <STAMP impreso por desplegar.sh>}
H=${ORBIT_SSH:-goncloud}
REPO=$(git rev-parse --show-toplevel)
SRV=/mnt/data/appdata/orbit
SIM=${ORBIT_SIMULACION:-0}
SIM_DIR=${ORBIT_SIM_DIR:-docs/evidencia/jev-ads-03/ejecucion/R.1/.sim}
cd "$REPO"

echo "== 0) Respaldo existe"
if [ "$SIM" = 1 ]; then
  [ -f "$SIM_DIR/predeploy-$STAMP/SHA" ] || { echo "ABORTA: no existe predeploy-$STAMP"; exit 1; }
  echo "SIMULACION SHA=$(cat "$SIM_DIR/predeploy-$STAMP/SHA")"
else
  ssh "$H" "set -e; cd $SRV; [ -d predeploy-$STAMP/app ] || { echo 'ABORTA: no existe predeploy-$STAMP'; exit 1; }; \
    [ -f predeploy-$STAMP/docker-compose.yml ] || { echo 'ABORTA: predeploy-$STAMP sin docker-compose.yml'; exit 1; }; \
    echo SHA=\$(cat predeploy-$STAMP/SHA)"
fi

echo "== 0b) Guardas (solo con el contenedor arriba, sin app.cli)"
if [ "$SIM" = 1 ]; then
  echo "SIMULACION: guardas no se miden; no hay contenedor que interrumpir"
else
  RUNNING=$(ssh "$H" "docker inspect -f '{{.State.Running}}' orbit-app-1") || { echo "ABORTA: docker inspect no contesta"; exit 1; }
  if [ "$RUNNING" = "true" ]; then
    TOP=$(ssh "$H" "docker top orbit-app-1") || { echo "ABORTA: docker top no contesta"; exit 1; }
    CLI=$(printf '%s\n' "$TOP" | grep -c 'app\.cli' || true)
    [ "$CLI" = "0" ] || { echo "ABORTA: hay app.cli corriendo"; exit 1; }
    echo "guardas OK (contenedor arriba, sin app.cli)"
  else
    echo "contenedor parado: la reversa corre sin guardas"
  fi
fi

echo "== 1) Restaurar el codigo de predeploy-$STAMP y reconstruir"
echo "(R.1 no toca el cron aqui. Reversa de la linea: programar-reconcilia.sh --quitar)"
if [ "$SIM" = 1 ]; then
  echo "SIMULACION DIGEST antes=no-tocado"
  echo "SIMULACION: no se restaura el servidor ni se reconstruye"
  echo "SIMULACION DIGEST despues=no-tocado"
  echo "SIMULACION health=ok"
else
  ssh "$H" "set -e; cd $SRV; \
    for pieza in app tools Dockerfile .dockerignore pyproject.toml uv.lock docker-compose.yml; do \
      [ -e predeploy-$STAMP/\$pieza ] || { echo ABORTA: predeploy-$STAMP sin \$pieza; exit 1; }; \
    done; \
    rm -rf app tools; cp -a predeploy-$STAMP/app predeploy-$STAMP/tools .; \
    cp -a predeploy-$STAMP/Dockerfile predeploy-$STAMP/.dockerignore predeploy-$STAMP/pyproject.toml predeploy-$STAMP/uv.lock predeploy-$STAMP/docker-compose.yml .; \
    echo DIGEST antes=\$(docker inspect -f '{{.Image}}' orbit-app-1); \
    docker compose up -d --no-deps --build app; \
    echo DIGEST despues=\$(docker inspect -f '{{.Image}}' orbit-app-1); \
    sleep 5; curl -fsS http://127.0.0.1:8010/health; echo"
fi

echo "== REVERSA LISTA."
echo "Sin migracion que deshacer. Cron intacto en este script."
