#!/usr/bin/env bash
# Reversa del despliegue S.5 segunda mitad: SOLO CODIGO (S.5 no trae
# migracion y no toca el cron: la linea de jev-senales sigue instalada y
# las tablas jev_* quedan intactas). Restaura el codigo del respaldo
# predeploy-<STAMP>.
# Guardas: sin app.cli corriendo, solo con el contenedor arriba.
# Uso: cd ~/dev/goncloud-Orbit && bash docs/evidencia/jev-ads-02/ejecucion/S.5/rollback.sh <STAMP de desplegar.sh>
set -euo pipefail

STAMP=${1:?uso: rollback.sh <STAMP impreso por desplegar.sh>}
REPO=$(git rev-parse --show-toplevel)
SRV=/mnt/data/appdata/orbit
cd "$REPO"

echo "== 0) Respaldo existe"
ssh goncloud "set -e; cd $SRV; [ -d predeploy-$STAMP/app ] || { echo 'ABORTA: no existe predeploy-$STAMP'; exit 1; }; \
  [ -f predeploy-$STAMP/docker-compose.yml ] || { echo 'ABORTA: predeploy-$STAMP sin docker-compose.yml'; exit 1; }; \
  echo SHA=$(cat predeploy-$STAMP/SHA)"

echo "== 0b) Guardas (solo con el contenedor arriba)"
RUNNING=$(ssh goncloud "docker inspect -f '{{.State.Running}}' orbit-app-1") || { echo "ABORTA: docker inspect no contesta"; exit 1; }
if [ "$RUNNING" = "true" ]; then
  TOP=$(ssh goncloud "docker top orbit-app-1") || { echo "ABORTA: docker top no contesta"; exit 1; }
  CLI=$(printf '%s\n' "$TOP" | grep -c 'app\.cli' || true)
  [ "$CLI" = "0" ] || { echo "ABORTA: hay app.cli corriendo"; exit 1; }
  echo "guardas OK (contenedor arriba, sin app.cli)"
else
  echo "contenedor parado: la reversa corre sin guardas (no hay nada que interrumpir)"
fi

echo "== 1) Restaurar el codigo y docker-compose.yml de predeploy-$STAMP y reconstruir"
echo "(S.5 no toco el cron: la linea de jev-senales se deja tal cual.)"
ssh goncloud "set -e; cd $SRV; \
  rm -rf app tools; cp -a predeploy-$STAMP/app predeploy-$STAMP/tools .; \
  cp -a predeploy-$STAMP/Dockerfile predeploy-$STAMP/.dockerignore predeploy-$STAMP/pyproject.toml predeploy-$STAMP/uv.lock predeploy-$STAMP/docker-compose.yml .; \
  echo DIGEST antes=\$(docker inspect -f '{{.Image}}' orbit-app-1); \
  docker compose up -d --no-deps --build app; \
  echo DIGEST despues=\$(docker inspect -f '{{.Image}}' orbit-app-1); \
  sleep 5; curl -sS http://127.0.0.1:8010/health; echo"

echo "== REVERSA LISTA."
echo "Tablas jev_*, login orbit_jev y cron de jev-senales intactos."
