#!/usr/bin/env bash
# Checklist post-deploy de B.3 (sp-api-02): SOLO LECTURA, no toca Amazon.
# Sale 0 solo si: app/spapi/client.py del contenedor y el guion del servidor
# tienen el md5 que en MOTOR_SHA, y la linea job_key=spapi:diario sigue en el
# crontab de gon. 1 = alguna FALLA medida; 4 = no pude medir.
# Uso: bash checklist.sh   (MOTOR_SHA en el entorno; si falta, HEAD)
set -uo pipefail

H=${ORBIT_SSH:-gonserver}
REPO=$(git rev-parse --show-toplevel)
SRV=/mnt/data/appdata/orbit
SHA=${MOTOR_SHA:-HEAD}
FALLAS=0
NOMEDIDO=0
cd "$REPO"
md5l() { python3 -c "import hashlib,sys; print(hashlib.md5(sys.stdin.buffer.read()).hexdigest())"; }

revisa() {  # revisa <nombre> <esperado> <obtenido>
  if [ -z "$3" ]; then echo "NO MEDIDO $1 (lectura vacia)"; NOMEDIDO=$((NOMEDIDO + 1));
  elif [ "$2" = "$3" ]; then echo "OK    $1 = $3";
  else echo "FALLA $1: esperado '$2', obtenido '$3'"; FALLAS=$((FALLAS + 1)); fi
}

echo "== Checklist B.3 sp-api-02 (SHA $SHA)"
RUNNING=$(ssh "$H" "sudo docker inspect -f '{{.State.Running}}' orbit-app-1" 2>/dev/null || true)
[ -n "$RUNNING" ] || { echo "NO MEDIDO el servidor no contesta"; exit 4; }
revisa "orbit-app-1 corriendo" true "$RUNNING"

echo "== 1) client.py del contenedor"
ESP=$(git show "$SHA:app/spapi/client.py" | md5l)
OBT=$(ssh "$H" "sudo docker exec orbit-app-1 md5sum /app/app/spapi/client.py" 2>/dev/null | cut -d' ' -f1 || true)
revisa "md5 app/spapi/client.py (contenedor)" "$ESP" "$OBT"

echo "== 2) guion nocturno del servidor"
ESP=$(git show "$SHA:scripts/spapi-diario.sh" | md5l)
OBT=$(ssh "$H" "sudo md5sum $SRV/spapi-diario.sh" 2>/dev/null | cut -d' ' -f1 || true)
revisa "md5 spapi-diario.sh (servidor)" "$ESP" "$OBT"

echo "== 3) crontab de gon conserva job_key=spapi:diario"
CRON=$(ssh "$H" "sudo crontab -u gon -l" 2>/dev/null || true)
if [ -z "$CRON" ]; then echo "NO MEDIDO crontab (lectura vacia)"; NOMEDIDO=$((NOMEDIDO + 1));
elif printf '%s\n' "$CRON" | grep -q 'job_key=spapi:diario'; then echo "OK    job_key=spapi:diario presente";
else echo "FALLA job_key=spapi:diario ausente del crontab de gon"; FALLAS=$((FALLAS + 1)); fi

echo "== Resumen: fallas=$FALLAS no-medido=$NOMEDIDO"
[ "$FALLAS" -eq 0 ] || exit 1
[ "$NOMEDIDO" = 0 ] || exit 4
