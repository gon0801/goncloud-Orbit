#!/usr/bin/env bash
# Checklist post-deploy de 2.3 (DoD de la fila): SOLO LECTURA. Corre UNA vez
# despues de desplegar.sh y del siguiente ciclo del optimizador, y deja su
# salida en checklist-salida.txt. Exit 0 = todo comprobado; 1 = alguna falla;
# 3 = sin fallas pero todavia sin ciclo posterior (no cuenta como verde).
# Uso: cd ~/dev/goncloud-Orbit && bash docs/evidencia/jev-ads-01/ejecucion/2.3/checklist.sh <STAMP> > docs/evidencia/jev-ads-01/ejecucion/2.3/checklist-salida.txt; echo "exit=$?"
set -uo pipefail

STAMP=${1:?uso: checklist.sh <STAMP impreso por desplegar.sh>}
DESDE=$(date -u -j -f '%Y%m%d-%H%M' "$STAMP" '+%Y-%m-%dT%H:%M:00Z' 2>/dev/null || date -u -d "${STAMP:0:8} ${STAMP:9:2}:${STAMP:11:2}" '+%Y-%m-%dT%H:%M:00Z')
REPO=$(git rev-parse --show-toplevel)
DIR=docs/evidencia/jev-ads-01/ejecucion/2.3
FALLAS=0
PENDIENTE=0
cd "$REPO"

revisa() {  # revisa <nombre> <esperado> <obtenido>
  if [ "$2" = "$3" ]; then echo "OK    $1 = $3"; else echo "FALLA $1: esperado '$2', obtenido '$3'"; FALLAS=$((FALLAS + 1)); fi
}
lee() { ssh goncloud 'DSN=$(docker exec orbit-app-1 printenv ORBIT_DSN_READ); docker exec -i orbit-db-1 psql "$DSN" -X -q -tA -v ON_ERROR_STOP=1'; }

echo "== Despliegue $STAMP (desde $DESDE)"

echo "== 1) HTTP: health, /cortes, asesoria inexistente"
revisa "GET /health" 200 "$(ssh goncloud 'curl -s -o /dev/null -w %{http_code} http://127.0.0.1:8010/health')"
revisa "GET /cortes" 200 "$(ssh goncloud 'curl -s -o /dev/null -w %{http_code} http://127.0.0.1:8010/cortes')"
revisa "GET /api/dashboard/cortes" 200 "$(ssh goncloud 'curl -s -o /dev/null -w %{http_code} http://127.0.0.1:8010/api/dashboard/cortes')"
revisa "asesorias no nulas en cortes (sin revisiones aun)" 0 \
  "$(ssh goncloud 'curl -s http://127.0.0.1:8010/api/dashboard/cortes' | python3 -c 'import json,sys; d=json.load(sys.stdin); print(sum(1 for f in d["items"] if f.get("asesoria") is not None))')"
revisa "GET /api/fabrica/asesoria/<64 ceros>" 404 \
  "$(ssh goncloud 'curl -s -o /dev/null -w %{http_code} http://127.0.0.1:8010/api/fabrica/asesoria/0000000000000000000000000000000000000000000000000000000000000000')"
revisa "logs 'asesoria Jev ilegible' desde el deploy" 0 \
  "$(ssh goncloud "L=\$(docker logs orbit-app-1 --since $DESDE 2>&1) || { echo 'docker logs fallo'; exit 0; }; printf '%s\n' \"\$L\" | grep -c 'asesoria Jev ilegible'")"

echo "== 2) CLI dentro del contenedor, sin credenciales"
ssh goncloud 'docker exec orbit-app-1 env -u ORBIT_DSN_ADMIN python -m tools.jev_ads evaluar --plataforma amazon_mx --grupo-id 1 --termino t --solicitud 00000000-0000-0000-0000-000000000001 --aplicar' 2>&1 | tail -1
revisa "tools.jev_ads sin ORBIT_DSN_ADMIN (exit)" 2 \
  "$(ssh goncloud 'docker exec orbit-app-1 env -u ORBIT_DSN_ADMIN python -m tools.jev_ads evaluar --plataforma amazon_mx --grupo-id 1 --termino t --solicitud 00000000-0000-0000-0000-000000000001 --aplicar >/dev/null 2>&1; echo $?')"
revisa "tools.jev_fichas --help (exit)" 0 "$(ssh goncloud 'docker exec orbit-app-1 python -m tools.jev_fichas --help >/dev/null 2>&1; echo $?')"
revisa "clave TypeSafe en el contenedor" ausente \
  "$(ssh goncloud 'docker exec orbit-app-1 sh -c "test -s \${ORBIT_SECRETS_DIR:-/run/secrets}/typesafe.json && echo presente || echo ausente"')"

echo "== 3) Permisos (consulta como orbit_read)"
revisa "permisos.sql" "permisos OK" "$(lee < "$DIR/permisos.sql")"

echo "== 4) Jev apagado: ninguna tarea automatica lo importa y no hay filas"
revisa "cycle/apply_cola/apply_harvest importan app.jev_*" False \
  "$(ssh goncloud 'docker exec orbit-app-1 python -c "import sys, app.cycle, app.apply_cola, app.apply_harvest; print(any(m.startswith(\"app.jev\") for m in sys.modules))"')"
revisa "filas Jev (fichas|revocaciones|revisiones|eventos)" "0|0|0|0" \
  "$(echo "SELECT (SELECT count(*) FROM jev_ficha_version), (SELECT count(*) FROM jev_ficha_revocacion), (SELECT count(*) FROM jev_revision), (SELECT count(*) FROM jev_par_evento);" | lee)"

echo "== 5) Primer ciclo del optimizador despues del deploy"
if ! CICLOS=$(echo "SELECT id || ' ' || coalesce(platform::text, '-') || ' ' || status || ' ' || coalesce(decisions_count, 0) FROM optimizer_cycle WHERE started_at > '$DESDE' ORDER BY id;" | lee); then
  echo "FALLA no se pudo leer optimizer_cycle"
  FALLAS=$((FALLAS + 1))
elif [ -z "$CICLOS" ]; then
  echo "INCOMPLETO: todavia no corre un ciclo despues de $DESDE; vuelve a correr el checklist despues del siguiente ciclo"
  PENDIENTE=1
else
  echo "$CICLOS"
  revisa "ciclos posteriores fallidos o colgados" 0 "$(echo "$CICLOS" | grep -c -E ' (failed|running) ')"
fi

echo "== RESULTADO: $FALLAS falla(s), ciclo posterior $([ "$PENDIENTE" = 1 ] && echo PENDIENTE || echo comprobado)"
[ "$FALLAS" -eq 0 ] || exit 1
[ "$PENDIENTE" = 0 ] || exit 3
