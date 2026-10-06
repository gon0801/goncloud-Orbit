#!/usr/bin/env bash
# Reversa del despliegue S.4: SOLO CODIGO (S.4 no trae migracion).
# Orden de la guia Despliega 1: PRIMERO quita la linea de cron de
# jev-senales (si el dueno ya la instalo; si no hay nada que quitar lo dice
# y sigue), y DESPUES restaura el codigo del respaldo predeploy-<STAMP>.
# Las tablas jev_* quedan intactas (el job apagado no escribe) y el login
# orbit_jev tambien (es de S.3).
# Guardas: sin app.cli corriendo, solo con el contenedor arriba.
# Uso: cd ~/dev/goncloud-Orbit && bash docs/evidencia/jev-ads-02/ejecucion/S.4/rollback.sh <STAMP de desplegar.sh>
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

echo "== 1) PRIMERO: respaldar el crontab de gon y quitar la linea de jev-senales"
ssh goncloud "set -e; cd $SRV; \
  crontab -u gon -l > predeploy-$STAMP/crontab-gon.antes 2>/dev/null || echo '(sin crontab previo)' > predeploy-$STAMP/crontab-gon.antes; \
  ANTES=\$(grep -c jev-senales predeploy-$STAMP/crontab-gon.antes || true); echo lineas-antes=\$ANTES; \
  if [ \"\$ANTES\" = '0' ]; then echo 'sin linea de jev-senales: crontab sin tocar'; \
  else grep -v jev-senales predeploy-$STAMP/crontab-gon.antes | crontab -u gon -; fi; \
  DESPUES=\$(crontab -u gon -l 2>/dev/null | grep -c jev-senales || true); echo lineas-despues=\$DESPUES; \
  [ \"\$DESPUES\" = '0' ] || { echo 'ABORTA: la linea de jev-senales sigue en el crontab'; exit 1; }; \
  echo \"cron rollback: \$ANTES -> \$DESPUES (respaldo en predeploy-$STAMP/crontab-gon.antes)\""

echo "== 2) DESPUES: restaurar el codigo y docker-compose.yml de predeploy-$STAMP y reconstruir"
ssh goncloud "set -e; cd $SRV; \
  rm -rf app tools; cp -a predeploy-$STAMP/app predeploy-$STAMP/tools .; \
  cp -a predeploy-$STAMP/Dockerfile predeploy-$STAMP/.dockerignore predeploy-$STAMP/pyproject.toml predeploy-$STAMP/uv.lock predeploy-$STAMP/docker-compose.yml .; \
  echo DIGEST antes=\$(docker inspect -f '{{.Image}}' orbit-app-1); \
  docker compose up -d --no-deps --build app; \
  echo DIGEST despues=\$(docker inspect -f '{{.Image}}' orbit-app-1); \
  sleep 5; curl -sS http://127.0.0.1:8010/health; echo"

echo "== REVERSA LISTA."
echo "Crontab respaldado en $SRV/predeploy-$STAMP/crontab-gon.antes; tablas jev_* y login orbit_jev intactos."
