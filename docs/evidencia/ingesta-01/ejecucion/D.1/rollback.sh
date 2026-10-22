#!/usr/bin/env bash
# Reversa de D.1: restaura el codigo + docker-compose.yml de predeploy-<STAMP>
# (reconstruye la imagen anterior) y el crontab de gon desde crontab-antes.txt.
# Sin migracion que revertir. Aborta si hay un ciclo/ingesta corriendo.
# ADR (intento 22, incidente 20261021-1814): el paso 4 verificaba "descarga.py
# NO existe en el contenedor", cierto solo la primera vez que se revierte D.1;
# al revertir un re-despliegue (el respaldo ya trae descarga.py) restauraba
# bien y aun asi salia 1. Ahora compara el contenedor contra predeploy-<STAMP>,
# que es lo que la reversa debe garantizar, en cualquier direccion.
# Uso: cd <repo> && bash docs/evidencia/ingesta-01/ejecucion/D.1/rollback.sh [STAMP]
# El motor corre `bash rollback.sh` sin STAMP (effects.py, mismo entorno con
# MOTOR_SHA). Sin argumento se toma el predeploy mas reciente del servidor.
# Si MOTOR_SHA esta definido y ese respaldo es de otro commit, este despliegue
# no llego a crear el suyo: no hay nada que revertir y se sale 0.
set -euo pipefail

H=${ORBIT_SSH:-gonserver}
REPO=$(git rev-parse --show-toplevel)
SRV=/mnt/data/appdata/orbit
DIR=${INGESTA01_D1_DIR:-docs/evidencia/ingesta-01/ejecucion/D.1}
cd "$REPO"
STAMP=${1:-}
if [ -z "$STAMP" ]; then
  LISTA=$(ssh "$H" "sudo ls -1d $SRV/predeploy-2* 2>/dev/null | sort | tail -1" || true)
  LISTA=$(printf '%s' "$LISTA" | tr -d '[:space:]')
  if [ -n "$LISTA" ]; then
    base=$(basename "$LISTA")
    STAMP=${base#predeploy-}
  fi
fi
if [ -z "$STAMP" ]; then
  echo "nada que revertir: no hay predeploy en el servidor"
  exit 0
fi
if [ -z "${1:-}" ] && [ -n "${MOTOR_SHA:-}" ]; then
  SHA_RESP=$(ssh "$H" "sudo cat $SRV/predeploy-$STAMP/SHA" | tr -d '[:space:]')
  if [ "$SHA_RESP" != "$MOTOR_SHA" ]; then
    echo "nada que revertir: el predeploy mas reciente ($STAMP, SHA $SHA_RESP) no es de este despliegue ($MOTOR_SHA)"
    exit 0
  fi
fi

echo "== 0) Respaldos presentes ANTES de tocar nada"
[ -s "$DIR/crontab-antes.txt" ] || { echo "ABORTA: falta $DIR/crontab-antes.txt"; exit 1; }
ssh "$H" "sudo test -d $SRV/predeploy-$STAMP/app && sudo test -f $SRV/predeploy-$STAMP/docker-compose.yml" || { echo "ABORTA: predeploy-$STAMP incompleto"; exit 1; }
echo "SHA previo al despliegue: $(ssh "$H" "sudo cat $SRV/predeploy-$STAMP/SHA")"

echo "== 1) Guardas"
CLI=$(ssh "$H" "sudo docker top orbit-app-1" | grep -c 'app\.cli' || true)
[ "$CLI" = "0" ] || { echo "ABORTA: hay app.cli corriendo"; exit 1; }
N=$(ssh "$H" "sudo docker exec -i orbit-db-1 psql -U orbit -d orbit -tA -c \"SELECT count(*) FROM optimizer_cycle WHERE status='running' AND started_at > now() - interval '3 hours'\"")
[ "$N" = "0" ] || { echo "ABORTA: hay $N ciclo(s) running"; exit 1; }
echo "guardas OK"

echo "== 2) Restaurar el codigo y la imagen de predeploy-$STAMP"
ssh "$H" "set -e; cd $SRV; \
  sudo rm -rf app tools; sudo cp -a predeploy-$STAMP/app predeploy-$STAMP/tools .; \
  sudo cp -a predeploy-$STAMP/Dockerfile predeploy-$STAMP/.dockerignore predeploy-$STAMP/pyproject.toml predeploy-$STAMP/uv.lock predeploy-$STAMP/docker-compose.yml .; \
  echo DIGEST antes=\$(sudo docker inspect -f '{{.Image}}' orbit-app-1); \
  sudo docker compose up -d --no-deps --build app; \
  echo DIGEST despues=\$(sudo docker inspect -f '{{.Image}}' orbit-app-1); \
  echo DIGEST predeploy=\$(sudo cat predeploy-$STAMP/IMAGE 2>/dev/null || echo sin-dato); \
  sleep 5; curl -sS http://127.0.0.1:8010/health; echo"

echo "== 3) Restaurar el crontab de gon desde crontab-antes.txt (el actual se archiva antes)"
ssh "$H" "sudo crontab -u gon -l | sudo tee $SRV/archive/crontab-gon.antes-rollback-$STAMP >/dev/null"
ssh "$H" "sudo crontab -u gon -" < "$DIR/crontab-antes.txt"
if diff <(ssh "$H" "sudo crontab -u gon -l") "$DIR/crontab-antes.txt" >/dev/null; then echo "crontab igual a crontab-antes.txt";
else echo "FALLA: el crontab no quedo igual a crontab-antes.txt"; exit 1; fi

echo "== 4) Verificacion: contenedor == predeploy-$STAMP (codigo restaurado) y app sana"
CNT=$(ssh "$H" "sudo docker exec orbit-app-1 sh -c 'cd /app && find app tools -name \"*.py\" | LC_ALL=C sort | xargs -r md5sum'")
BAK=$(ssh "$H" "cd $SRV/predeploy-$STAMP && sudo find app tools -name '*.py' | LC_ALL=C sort | xargs -r sudo md5sum")
[ -n "$CNT" ] || { echo "FALLA: sin lectura del contenedor"; exit 1; }
[ -n "$BAK" ] || { echo "FALLA: sin lectura de predeploy-$STAMP"; exit 1; }
if [ "$CNT" != "$BAK" ]; then
  echo "FALLA: el contenedor difiere de predeploy-$STAMP:"
  diff <(printf '%s\n' "$CNT") <(printf '%s\n' "$BAK") | sed -n '1,10p' || true
  exit 1
fi
echo "contenedor == predeploy-$STAMP ($(printf '%s\n' "$CNT" | grep -c .) archivos .py)"
C=$(ssh "$H" 'curl -s -o /dev/null -w %{http_code} http://127.0.0.1:8010/health')
[ "$C" = "200" ] || { echo "FALLA: /health=$C"; exit 1; }
echo "== REVERSA LISTA (codigo e imagen de predeploy-$STAMP, crontab previo; /health 200)"
