#!/usr/bin/env bash
# Despliegue B.3 de sp-api-02: el cliente SP-API (app/spapi/client.py y el resto
# del codigo) y el guion nocturno scripts/spapi-diario.sh con su repeticion.
# Sin migracion, sin tocar el crontab. Solo lectura con Amazon (no corre ninguna
# ingesta). Patron de docs/evidencia/ingesta-01/ejecucion/D.1/desplegar.sh. En el
# servidor docker exige sudo (usuario claw).
# Contrato del motor: `bash desplegar.sh` SIN argumentos, desde una copia del
# commit mergeado, con MOTOR_SHA=ese commit. Quality de ese push suele seguir
# in_progress justo despues del merge: se espera hasta 12 min. Las guardas de
# ingesta-01 (lock de la ingesta diaria, ciclo `running`, app.cli corriendo) se
# esperan hasta 10 min antes de recrear orbit-app-1.
set -euo pipefail
trap 'echo "FALLO en la linea $LINENO. Si el codigo ya se copio: bash $DIR/rollback.sh"' ERR

H=${ORBIT_SSH:-gonserver}
REPO=$(git rev-parse --show-toplevel)
SRV=/mnt/data/appdata/orbit
STAMP=$(date -u +%Y%m%d-%H%M%S)
DIR=docs/evidencia/sp-api-02/ejecucion/B.3
cd "$REPO"
md5l() { python3 -c "import hashlib,sys; print(hashlib.md5(sys.stdin.buffer.read()).hexdigest())"; }

[ -n "${MOTOR_SHA:-}" ] || { echo "ABORTA: falta MOTOR_SHA"; exit 1; }
APROBADO=$MOTOR_SHA

guardas() {  # 0 = se puede recrear; imprime el motivo si no
  if ! ssh "$H" "sudo -u gon flock -n /tmp/orbit-ingesta-diaria.lock true"; then echo "flock ocupado"; return 1; fi
  local n cli
  n=$(ssh "$H" "sudo docker exec -i orbit-db-1 psql -U orbit -d orbit -tA -c \"SELECT count(*) FROM optimizer_cycle WHERE status='running' AND started_at > now() - interval '3 hours'\"") || { echo "sin respuesta de la DB"; return 1; }
  [ "$n" = "0" ] || { echo "hay $n ciclo(s) running de menos de 3 h"; return 1; }
  cli=$(ssh "$H" "sudo docker top orbit-app-1" | grep -c 'app\.cli' || true)
  [ "$cli" = "0" ] || { echo "hay app.cli corriendo"; return 1; }
}

espera_quality() {  # 0 cuando el push de Quality termina en success
  local tope=${QUALITY_ESPERA_S:-720} pausa=${QUALITY_PAUSA_S:-20}
  local inicio=$SECONDS raw status conclusion err
  err=$(mktemp)
  while true; do
    if ! raw=$(gh run list --commit "$APROBADO" --event push --workflow Quality \
        --json status,conclusion --jq \
        'if .[0] == null then "|" else [.[0].status // "", .[0].conclusion // ""] | join("|") end' \
        2>"$err"); then
      echo "ABORTA: no pude leer Quality sobre $APROBADO"; sed -n '1,5p' "$err"; rm -f "$err"; exit 1
    fi
    status=${raw%%|*}
    conclusion=${raw#*|}
    if [ "$conclusion" = "success" ]; then rm -f "$err"; echo "APROBADO=$APROBADO CI=success"; return 0; fi
    if [ -n "$conclusion" ]; then echo "ABORTA: Quality sobre $APROBADO es '$conclusion'"; rm -f "$err"; exit 1; fi
    if [ $((SECONDS - inicio)) -ge "$tope" ]; then
      echo "ABORTA: Quality sobre $APROBADO no termino en ${tope}s (status=${status:-sin-run})"; rm -f "$err"; exit 1
    fi
    echo "Quality sobre $APROBADO sigue ${status:-sin-run}; espero ${pausa}s ($((SECONDS - inicio))s/${tope}s)"
    sleep "$pausa"
  done
}

echo "== 0) SHA aprobado: ancestro de origin/master; Quality verde (espera hasta 12 min)"
git fetch -q origin || { echo "ABORTA: git fetch origin fallo"; exit 1; }
git merge-base --is-ancestor "$APROBADO" origin/master || { echo "ABORTA: $APROBADO no esta en origin/master"; exit 1; }
espera_quality

echo "== 1) Preflight (contenedor arriba; sin migracion)"
RUNNING=$(ssh "$H" "sudo docker inspect -f '{{.State.Running}}' orbit-app-1")
echo "orbit-app-1 running=$RUNNING"
[ "$RUNNING" = "true" ] || { echo "ABORTA: orbit-app-1 parado"; exit 1; }

echo "== 2) Respaldo predeploy-$STAMP: codigo, compose, guion nocturno, SHA e imagen (el .env NO se copia)"
ssh "$H" "set -e; cd $SRV; ! sudo test -e predeploy-$STAMP || { echo 'ABORTA: predeploy-$STAMP ya existe (no se pisa el respaldo)'; exit 1; }; sudo mkdir -p predeploy-$STAMP; \
  sudo cp -a app Dockerfile .dockerignore pyproject.toml uv.lock tools docker-compose.yml predeploy-$STAMP/; \
  if sudo test -f spapi-diario.sh; then sudo cp -a spapi-diario.sh predeploy-$STAMP/; fi; \
  echo $APROBADO | sudo tee predeploy-$STAMP/SHA >/dev/null; \
  sudo docker inspect -f '{{.Image}}' orbit-app-1 | sudo tee predeploy-$STAMP/IMAGE >/dev/null; ls predeploy-$STAMP"

echo "== 3) Copiar el codigo y docker-compose.yml del SHA (git archive, LF)"
git archive --format=tar "$APROBADO" app Dockerfile .dockerignore pyproject.toml uv.lock docker-compose.yml \
  | ssh "$H" "cd $SRV && sudo tar --no-same-owner -xf -"

echo "== 4) md5 del client.py: server vs SHA aprobado"
L=$(git show "$APROBADO:app/spapi/client.py" | md5l)
S=$(ssh "$H" "sudo md5sum $SRV/app/spapi/client.py" | cut -d' ' -f1)
[ "$L" = "$S" ] || { echo "ABORTA: md5 distinto en app/spapi/client.py"; exit 1; }
echo "md5 OK client.py=$L"

echo "== 5) Guardas antes de recrear (espera hasta 10 min)"
TOPE=${GUARDAS_ESPERA_S:-600}; PAUSA=${GUARDAS_PAUSA_S:-30}; INICIO=$SECONDS
until MOTIVO=$(guardas); do
  if [ $((SECONDS - INICIO)) -ge "$TOPE" ]; then
    echo "DETENIDO sin recrear (${TOPE}s): $MOTIVO. El contenedor viejo sigue con su imagen. Reintenta el despliegue, o: bash $DIR/rollback.sh"
    exit 1
  fi
  echo "guardas: $MOTIVO; espero ${PAUSA}s ($((SECONDS - INICIO))s/${TOPE}s)"
  sleep "$PAUSA"
done
echo "guardas OK"

echo "== 6) Build + recrear app (digest antes/despues) + health"
ssh "$H" "set -e; cd $SRV; \
  echo DIGEST antes=\$(sudo docker inspect -f '{{.Image}}' orbit-app-1); \
  sudo docker compose up -d --no-deps --build app; \
  echo DIGEST despues=\$(sudo docker inspect -f '{{.Image}}' orbit-app-1); \
  sleep 5; curl -sS http://127.0.0.1:8010/health; echo; \
  sudo docker ps --filter name=orbit-app-1 --format '{{.Names}} {{.Status}}'"

echo "== 7) Guion nocturno: scripts/spapi-diario.sh -> $SRV/spapi-diario.sh (chmod +x)"
git show "$APROBADO:scripts/spapi-diario.sh" | ssh "$H" "sudo tee $SRV/spapi-diario.sh >/dev/null && sudo chmod +x $SRV/spapi-diario.sh"
LG=$(git show "$APROBADO:scripts/spapi-diario.sh" | md5l)
SG=$(ssh "$H" "sudo md5sum $SRV/spapi-diario.sh" | cut -d' ' -f1)
[ "$LG" = "$SG" ] || { echo "ABORTA: md5 distinto en spapi-diario.sh"; exit 1; }
echo "md5 OK spapi-diario.sh=$LG"
echo "$APROBADO" > "$DIR/sha-desplegado.txt"

echo "== LISTO. STAMP=$STAMP"
echo "Siguiente: bash $DIR/checklist.sh"
echo "Reversa:   bash $DIR/rollback.sh"
