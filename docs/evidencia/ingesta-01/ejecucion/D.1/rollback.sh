#!/usr/bin/env bash
# Reversa de D.1: restaura el codigo + docker-compose.yml de predeploy-<STAMP>
# (reconstruye la imagen anterior) y el crontab de gon desde crontab-antes.txt.
# Sin migracion que revertir. Aborta si hay un ciclo/ingesta corriendo.
# Uso: cd <repo> && bash docs/evidencia/ingesta-01/ejecucion/D.1/rollback.sh <STAMP>
set -euo pipefail

STAMP=${1:?uso: rollback.sh <STAMP impreso por desplegar.sh>}
H=${ORBIT_SSH:-gonserver}
REPO=$(git rev-parse --show-toplevel)
SRV=/mnt/data/appdata/orbit
DIR=docs/evidencia/ingesta-01/ejecucion/D.1
cd "$REPO"

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

echo "== 4) Verificacion: codigo anterior (sin descarga.py) y app sana"
ssh "$H" "sudo docker exec orbit-app-1 test ! -e /app/app/ads/descarga.py" || { echo "FALLA: descarga.py sigue en el contenedor"; exit 1; }
C=$(ssh "$H" 'curl -s -o /dev/null -w %{http_code} http://127.0.0.1:8010/health')
[ "$C" = "200" ] || { echo "FALLA: /health=$C"; exit 1; }
echo "== REVERSA LISTA (imagen y crontab previos; /health 200)"
