#!/usr/bin/env bash
# Reversa de B.3: restaura el codigo, docker-compose.yml y el guion nocturno del
# predeploy-<STAMP> MAS RECIENTE del servidor (sin argumentos; el motor corre
# `bash rollback.sh` con MOTOR_SHA) y reconstruye la imagen anterior. Sin
# migracion ni crontab que revertir. Aborta si hay app.cli o un ciclo corriendo.
# Si MOTOR_SHA esta definido y ese respaldo es de otro commit, este despliegue no
# llego a crear el suyo: no hay nada que revertir y se sale 0.
set -euo pipefail

H=${ORBIT_SSH:-gonserver}
REPO=$(git rev-parse --show-toplevel)
SRV=/mnt/data/appdata/orbit
cd "$REPO"

LISTA=$(ssh "$H" "sudo ls -1d $SRV/predeploy-2* 2>/dev/null | sort | tail -1" || true)
LISTA=$(printf '%s' "$LISTA" | tr -d '[:space:]')
if [ -z "$LISTA" ]; then echo "nada que revertir: no hay predeploy en el servidor"; exit 0; fi
STAMP=$(basename "$LISTA"); STAMP=${STAMP#predeploy-}
if [ -n "${MOTOR_SHA:-}" ]; then
  SHA_RESP=$(ssh "$H" "sudo cat $SRV/predeploy-$STAMP/SHA" | tr -d '[:space:]')
  if [ "$SHA_RESP" != "$MOTOR_SHA" ]; then
    echo "nada que revertir: el predeploy mas reciente ($STAMP, SHA $SHA_RESP) no es de este despliegue ($MOTOR_SHA)"
    exit 0
  fi
fi

echo "== 0) Respaldo presente ANTES de tocar nada: predeploy-$STAMP"
ssh "$H" "sudo test -d $SRV/predeploy-$STAMP/app && sudo test -f $SRV/predeploy-$STAMP/docker-compose.yml" || { echo "ABORTA: predeploy-$STAMP incompleto"; exit 1; }

echo "== 1) Guardas"
CLI=$(ssh "$H" "sudo docker top orbit-app-1" | grep -c 'app\.cli' || true)
[ "$CLI" = "0" ] || { echo "ABORTA: hay app.cli corriendo"; exit 1; }
N=$(ssh "$H" "sudo docker exec -i orbit-db-1 psql -U orbit -d orbit -tA -c \"SELECT count(*) FROM optimizer_cycle WHERE status='running' AND started_at > now() - interval '3 hours'\"")
[ "$N" = "0" ] || { echo "ABORTA: hay $N ciclo(s) running"; exit 1; }

echo "== 2) Restaurar codigo, imagen y guion nocturno de predeploy-$STAMP"
ssh "$H" "set -e; cd $SRV; \
  sudo rm -rf app tools; sudo cp -a predeploy-$STAMP/app predeploy-$STAMP/tools .; \
  sudo cp -a predeploy-$STAMP/Dockerfile predeploy-$STAMP/.dockerignore predeploy-$STAMP/pyproject.toml predeploy-$STAMP/uv.lock predeploy-$STAMP/docker-compose.yml .; \
  if sudo test -f predeploy-$STAMP/spapi-diario.sh; then sudo cp -a predeploy-$STAMP/spapi-diario.sh spapi-diario.sh && sudo chmod +x spapi-diario.sh; fi; \
  sudo docker compose up -d --no-deps --build app; \
  sleep 5; curl -sS http://127.0.0.1:8010/health; echo"

echo "== 3) Verificacion: contenedor == predeploy-$STAMP y /health 200"
CNT=$(ssh "$H" "sudo docker exec orbit-app-1 sh -c 'cd /app && find app tools -name \"*.py\" | LC_ALL=C sort | xargs -r md5sum'")
BAK=$(ssh "$H" "cd $SRV/predeploy-$STAMP && sudo find app tools -name '*.py' | LC_ALL=C sort | xargs -r sudo md5sum")
[ -n "$CNT" ] && [ -n "$BAK" ] || { echo "FALLA: sin lectura"; exit 1; }
[ "$CNT" = "$BAK" ] || { echo "FALLA: el contenedor difiere de predeploy-$STAMP"; exit 1; }
C=$(ssh "$H" 'curl -s -o /dev/null -w %{http_code} http://127.0.0.1:8010/health')
[ "$C" = "200" ] || { echo "FALLA: /health=$C"; exit 1; }
echo "== REVERSA LISTA (predeploy-$STAMP; /health 200)"
