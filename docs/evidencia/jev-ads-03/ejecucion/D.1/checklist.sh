#!/usr/bin/env bash
# Checklist post-deploy de D.1 (JEV ADS 03): SOLO LECTURA salvo
# bloqueo-reconcilia, que con las tablas vacias no escribe ni llama a Amazon.
# Patron de jev-ads-02/ejecucion/S.3/checklist.sh.
# Salidas: 0 = todo comprobado; 1 = alguna FALLA medida; 4 = no pude medir.
# Uso: cd <repo> && bash docs/evidencia/jev-ads-03/ejecucion/D.1/checklist.sh <STAMP> > docs/evidencia/jev-ads-03/ejecucion/D.1/checklist-<STAMP>.txt; echo "exit=$?"
set -uo pipefail

SELLO=${1:-sin-sello}
H=${ORBIT_SSH:-gonserver}
REPO=$(git rev-parse --show-toplevel)
SRV=/mnt/data/appdata/orbit
DIR=docs/evidencia/jev-ads-03/ejecucion/D.1
FALLAS=0
NOMEDIDO=0
cd "$REPO"

revisa() {  # revisa <nombre> <esperado> <obtenido>
  if [ -z "$3" ]; then echo "NO MEDIDO $1 (lectura vacia)"; NOMEDIDO=$((NOMEDIDO + 1));
  elif [ "$2" = "$3" ]; then echo "OK    $1 = $3";
  else echo "FALLA $1: esperado '$2', obtenido '$3'"; FALLAS=$((FALLAS + 1)); fi
}
como() {  # como <VAR_DSN>: psql con el login de esa variable del contenedor
  ssh "$H" "DSN=\$(docker exec orbit-app-1 printenv $1); docker exec -i orbit-db-1 psql \"\$DSN\" -X -q -tA -v ON_ERROR_STOP=1"
}

echo "== 0) El servidor contesta"
RUNNING=$(ssh "$H" "docker inspect -f '{{.State.Running}}' orbit-app-1" || true)
[ -n "$RUNNING" ] || { echo "NO MEDIDO el servidor no contesta"; exit 4; }
revisa "orbit-app-1 corriendo" true "$RUNNING"
SHA=$(ssh "$H" "cat $SRV/predeploy-$SELLO/SHA" 2>/dev/null || true)
echo "== Checklist D.1 (sello $SELLO, SHA ${SHA:-sin-dato})"

echo "== 1) md5 server vs SHA desplegado"
if [ -z "$SHA" ]; then echo "NO MEDIDO md5 (sin predeploy-$SELLO/SHA)"; NOMEDIDO=$((NOMEDIDO + 1));
else
  for f in $(cat "$DIR/archivos.txt"); do
    revisa "md5 $f" "$(git show "$SHA:$f" | md5 -q)" "$(ssh "$H" "md5sum $SRV/$f" | cut -d' ' -f1)"
  done
fi

echo "== 2) HTTP: health, /cortes y su API sin propuestas"
revisa "GET /health" 200 "$(ssh "$H" 'curl -s -o /dev/null -w %{http_code} http://127.0.0.1:8010/health')"
revisa "GET /cortes" 200 "$(ssh "$H" 'curl -s -o /dev/null -w %{http_code} http://127.0.0.1:8010/cortes')"
API=$(ssh "$H" 'curl -s http://127.0.0.1:8010/api/dashboard/cortes' | python3 -c '
import json, sys
d = json.load(sys.stdin)
b = d["bloqueos_jev"]
n = sum(len(p.get("propuestas", [])) for p in b["plataformas"])
print(d["bloqueos_disponibles"], b["interruptor"], n, len(b["bloqueos"]))' 2>/dev/null || true)
revisa "API cortes: disponibles interruptor propuestas bloqueos" "True False 0 0" "$API"

echo "== 3) Tablas nuevas vacias (como orbit_read)"
revisa "filas en bloqueo_veredicto + bloqueo_efecto" 0 \
  "$(echo "SELECT (SELECT count(*) FROM bloqueo_veredicto) + (SELECT count(*) FROM bloqueo_efecto);" | como ORBIT_DSN_READ || true)"

echo "== 4) bloqueo-reconcilia sale 0"
revisa "python -m app.cli bloqueo-reconcilia (exit)" 0 \
  "$(ssh "$H" 'docker exec orbit-app-1 python -m app.cli bloqueo-reconcilia >/dev/null 2>&1; echo $?')"

echo "== 5) Permisos como orbit_read y orbit_decide"
revisa "usuario de ORBIT_DSN_READ" orbit_read "$(echo 'SELECT current_user;' | como ORBIT_DSN_READ || true)"
revisa "permisos.sql como orbit_read" "permisos OK" "$(como ORBIT_DSN_READ < "$DIR/permisos.sql" || true)"
revisa "usuario de ORBIT_DSN_DECIDE" orbit_decide "$(echo 'SELECT current_user;' | como ORBIT_DSN_DECIDE || true)"
revisa "orbit_decide privilegios SELECT veredicto|efecto" "f|f" \
  "$(echo "SELECT has_table_privilege('public.bloqueo_veredicto', 'SELECT'), has_table_privilege('public.bloqueo_efecto', 'SELECT');" | como ORBIT_DSN_DECIDE || true)"
DEN=$(echo 'SELECT count(*) FROM bloqueo_veredicto;' | como ORBIT_DSN_DECIDE 2>&1 || true)
revisa "orbit_decide lee bloqueo_veredicto" denegado "$(printf '%s' "$DEN" | grep -q 'permission denied' && echo denegado || echo "${DEN:-vacio}")"

echo "== 6) Interruptor jev.bloqueo sigue apagado"
revisa "jev.bloqueo = true en la config vigente" f \
  "$(echo "SELECT coalesce((SELECT settings -> 'jev.bloqueo' = 'true'::jsonb FROM config_version ORDER BY id DESC LIMIT 1), false);" | como ORBIT_DSN_READ || true)"

echo "== RESULTADO: $FALLAS falla(s), $NOMEDIDO sin medir"
[ "$FALLAS" -eq 0 ] || exit 1
[ "$NOMEDIDO" = 0 ] || exit 4
