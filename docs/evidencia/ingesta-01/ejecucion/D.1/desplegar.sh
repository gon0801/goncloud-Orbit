#!/usr/bin/env bash
# Despliegue D.1 de ingesta-01: el codigo del SHA aprobado (descarga en paralelo,
# --solo-faltantes, guardas de metricas) y el bloque instalador de crontab de
# docs/DEPLOY.md. Sin migracion. Nada escribe a Amazon.
# Patron de docs/evidencia/jev-ads-03/ejecucion/D.1/desplegar.sh. En el servidor
# docker y el crontab de gon exigen sudo (usuario claw).
# Guarda tecnica: NO se recrea orbit-app-1 si `flock -n /tmp/orbit-ingesta-diaria.lock true`
# falla (como gon, dueno de los demas locks), si hay un ciclo `running` iniciado hace
# menos de 3 h o si corre app.cli dentro del contenedor; se reintenta cada 15 min.
# Uso: cd <repo> && bash docs/evidencia/ingesta-01/ejecucion/D.1/desplegar.sh <sha aprobado en origin/master>
set -euo pipefail
trap 'echo "FALLO en la linea $LINENO. Si el codigo ya se copio: bash $DIR/rollback.sh $STAMP"' ERR

APROBADO=${1:?uso: desplegar.sh <sha aprobado con CI verde en origin/master>}
H=${ORBIT_SSH:-gonserver}
REPO=$(git rev-parse --show-toplevel)
SRV=/mnt/data/appdata/orbit
STAMP=$(date -u +%Y%m%d-%H%M%S)
DIR=docs/evidencia/ingesta-01/ejecucion/D.1
REINTENTOS=${REINTENTOS:-12}   # x 15 min = 3 h
cd "$REPO"
md5l() { python3 -c "import hashlib,sys; print(hashlib.md5(sys.stdin.buffer.read()).hexdigest())"; }

guardas() {  # 0 = se puede recrear; imprime el motivo si no
  if ! ssh "$H" "sudo -u gon flock -n /tmp/orbit-ingesta-diaria.lock true"; then echo "flock ocupado"; return 1; fi
  local n cli
  n=$(ssh "$H" "sudo docker exec -i orbit-db-1 psql -U orbit -d orbit -tA -c \"SELECT count(*) FROM optimizer_cycle WHERE status='running' AND started_at > now() - interval '3 hours'\"") || { echo "sin respuesta de la DB"; return 1; }
  [ "$n" = "0" ] || { echo "hay $n ciclo(s) running de menos de 3 h"; return 1; }
  cli=$(ssh "$H" "sudo docker top orbit-app-1" | grep -c 'app\.cli' || true)
  [ "$cli" = "0" ] || { echo "hay app.cli corriendo"; return 1; }
}

echo "== 0) SHA aprobado: ancestro de origin/master con Quality verde"
git fetch -q origin
git merge-base --is-ancestor "$APROBADO" origin/master || { echo "ABORTA: $APROBADO no esta en origin/master"; exit 1; }
CI=$(gh run list --commit "$APROBADO" --event push --workflow Quality --json conclusion --jq '.[0].conclusion')
[ "$CI" = "success" ] || { echo "ABORTA: Quality sobre $APROBADO es '$CI'"; exit 1; }
echo "APROBADO=$APROBADO CI=$CI"

echo "== 1) Preflight (contenedor arriba; sin migracion; el codigo nuevo aun no esta)"
RUNNING=$(ssh "$H" "sudo docker inspect -f '{{.State.Running}}' orbit-app-1")
echo "orbit-app-1 running=$RUNNING"
[ "$RUNNING" = "true" ] || { echo "ABORTA: orbit-app-1 parado"; exit 1; }

echo "== 2) Respaldo del codigo actual, compose, SHA e imagen (el .env NO se copia)"
ssh "$H" "set -e; cd $SRV; ! sudo test -e predeploy-$STAMP || { echo 'ABORTA: predeploy-$STAMP ya existe (no se pisa el respaldo)'; exit 1; }; sudo mkdir -p predeploy-$STAMP; \
  sudo cp -a app Dockerfile .dockerignore pyproject.toml uv.lock tools docker-compose.yml predeploy-$STAMP/; \
  echo $APROBADO | sudo tee predeploy-$STAMP/SHA >/dev/null; \
  sudo docker inspect -f '{{.Image}}' orbit-app-1 | sudo tee predeploy-$STAMP/IMAGE >/dev/null; ls predeploy-$STAMP"

echo "== 3) Respaldo del crontab de gon en $DIR/crontab-antes.txt (no se pisa si ya existe)"
if [ -s "$DIR/crontab-antes.txt" ]; then echo "ya existe: se conserva";
else ssh "$H" "sudo crontab -u gon -l" > "$DIR/crontab-antes.txt"; fi
wc -l "$DIR/crontab-antes.txt"

echo "== 4) Copiar el codigo y docker-compose.yml del SHA (git archive, LF)"
git archive --format=tar "$APROBADO" app Dockerfile .dockerignore pyproject.toml uv.lock docker-compose.yml \
  | ssh "$H" "cd $SRV && sudo tar --no-same-owner -xf -"

echo "== 5) md5 server vs SHA aprobado"
for f in $(cat "$DIR/archivos.txt"); do
  l=$(git show "$APROBADO:$f" | md5l)
  s=$(ssh "$H" "sudo md5sum $SRV/$f" | cut -d' ' -f1)
  [ "$l" = "$s" ] || { echo "ABORTA: md5 distinto en $f"; exit 1; }
done
echo "md5 OK ($(wc -l < "$DIR/archivos.txt") archivos)"

echo "== 6) Guardas antes de recrear (reintento cada 15 min, hasta $REINTENTOS veces)"
LISTO=0
for i in $(seq 1 "$REINTENTOS"); do
  if MOTIVO=$(guardas); then LISTO=1; break; fi
  echo "intento $i/$REINTENTOS: $MOTIVO; no se recrea, reintento en 15 min"
  sleep 900
done
[ "$LISTO" = "1" ] || { echo "DETENIDO sin recrear: el contenedor viejo sigue con su imagen. Reintenta: bash $DIR/desplegar.sh $APROBADO"; exit 1; }
echo "guardas OK"

echo "== 7) Build + recrear app (digest antes/despues) + health"
ssh "$H" "set -e; cd $SRV; \
  echo DIGEST antes=\$(sudo docker inspect -f '{{.Image}}' orbit-app-1); \
  sudo docker compose up -d --no-deps --build app; \
  echo DIGEST despues=\$(sudo docker inspect -f '{{.Image}}' orbit-app-1); \
  sleep 5; curl -sS http://127.0.0.1:8010/health; echo; \
  sudo docker ps --filter name=orbit-app-1 --format '{{.Names}} {{.Status}}'"

echo "== 8) Bloque instalador de crontab (docs/DEPLOY.md, tal cual, via sudo en el servidor)"
python3 - "$APROBADO" <<'PY' > /tmp/instalador-ingesta01.sh
import re, subprocess, sys
doc = subprocess.run(["git", "show", f"{sys.argv[1]}:docs/DEPLOY.md"], capture_output=True, text=True, check=True).stdout
m = re.search(r"<<'SCRIPT'\n(.*?)\nSCRIPT\n", doc, re.S)
assert m, "no hay bloque instalador"
print(m.group(1))
PY
ssh "$H" 'sudo bash -s' < /tmp/instalador-ingesta01.sh > "$DIR/instalador-salida-$STAMP.txt"
rm -f /tmp/instalador-ingesta01.sh
echo "crontab instalado ($(grep -c 'job_key' "$DIR/instalador-salida-$STAMP.txt") marcas job_key)"
echo "$APROBADO" > "$DIR/sha-desplegado.txt"

echo "== 9) Aviso al dueno (Telegram)"
ssh "$H" "printf '%s\n' '<b>[Orbit] ingesta-01 D.1 — desplegado</b>' 'SHA $APROBADO: descarga en paralelo, --solo-faltantes, guardas de metricas y crontab nuevo (ingest diario 07:10/10:40/13:15).' | sudo tee /tmp/ingesta01-d1.txt >/dev/null; sudo python3 $SRV/recordatorios/envia_recordatorio.py /tmp/ingesta01-d1.txt; sudo rm -f /tmp/ingesta01-d1.txt" || echo "AVISO: Telegram no salio"

echo "== LISTO. STAMP=$STAMP"
echo "Siguiente: bash $DIR/checklist.sh"
echo "Reversa:   bash $DIR/rollback.sh $STAMP"
