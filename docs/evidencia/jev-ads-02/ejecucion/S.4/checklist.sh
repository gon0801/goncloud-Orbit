#!/usr/bin/env bash
# Checklist post-deploy de S.4 (JEV ADS 02): SOLO LECTURA (el seco del job
# no escribe por diseno). Corre UNA vez despues de desplegar.sh y deja su
# salida en checklist-salida.txt.
# Salidas: 0 = todo comprobado; 1 = alguna FALLA medida; 3 = sin fallas
# pero todavia sin ciclo posterior al arranque (o con uno en curso); 4 = no
# pude medir (el servidor no contesta o una lectura salio vacia, sin
# ninguna FALLA medida). Una FALLA medida sale con 1 aunque otra lectura
# quede vacia. "Posterior" cuenta desde el arranque del contenedor nuevo
# (docker inspect StartedAt); el sello es solo informativo.
# Uso: cd ~/dev/goncloud-Orbit && bash docs/evidencia/jev-ads-02/ejecucion/S.4/checklist.sh <STAMP> > docs/evidencia/jev-ads-02/ejecucion/S.4/checklist-salida.txt; echo "exit=$?"
set -uo pipefail

SELLO=${1:-sin-sello}
REPO=$(git rev-parse --show-toplevel)
FALLAS=0
PENDIENTE=0
NOMEDIDO=0
cd "$REPO"

revisa() {  # revisa <nombre> <esperado> <obtenido>
  if [ -z "$3" ]; then echo "NO MEDIDO $1 (lectura vacia)"; NOMEDIDO=$((NOMEDIDO + 1));
  elif [ "$2" = "$3" ]; then echo "OK    $1 = $3";
  else echo "FALLA $1: esperado '$2', obtenido '$3'"; FALLAS=$((FALLAS + 1)); fi
}
lee() { ssh goncloud 'DSN=$(docker exec orbit-app-1 printenv ORBIT_DSN_READ); docker exec -i orbit-db-1 psql "$DSN" -X -q -tA -v ON_ERROR_STOP=1'; }

echo "== 0) El servidor contesta"
RUNNING=$(ssh goncloud "docker inspect -f '{{.State.Running}}' orbit-app-1" || true)
if [ -z "$RUNNING" ]; then echo "NO MEDIDO el servidor no contesta (docker inspect vacio)"; exit 4; fi
revisa "orbit-app-1 corriendo" true "$RUNNING"
DESDE=$(ssh goncloud "docker inspect -f '{{.State.StartedAt}}' orbit-app-1" || true)
if [ -z "$DESDE" ]; then echo "NO MEDIDO arranque del contenedor (StartedAt vacio)"; NOMEDIDO=$((NOMEDIDO + 1)); fi

echo "== Checklist S.4 (solo lectura; sello $SELLO, contenedor desde ${DESDE:-sin-dato})"

echo "== 1) HTTP: health, /cortes, /salud"
revisa "GET /health" 200 "$(ssh goncloud 'curl -s -o /dev/null -w %{http_code} http://127.0.0.1:8010/health')"
revisa "GET /cortes" 200 "$(ssh goncloud 'curl -s -o /dev/null -w %{http_code} http://127.0.0.1:8010/cortes')"
revisa "GET /salud" 200 "$(ssh goncloud 'curl -s -o /dev/null -w %{http_code} http://127.0.0.1:8010/salud')"

echo "== 2) Seco del job apagado (exit 0, dice apagado, cero filas lote)"
SECO=$(ssh goncloud 'docker exec orbit-app-1 python -m app.cli jev-senales' || true)
echo "$SECO"
revisa "seco exit" 0 "$(ssh goncloud 'docker exec orbit-app-1 python -m app.cli jev-senales >/dev/null 2>&1; echo $?')"
revisa "seco dice apagado" apagado "$(echo "$SECO" | grep -o apagado | head -1)"
revisa "filas lote" 0 "$(echo "SELECT count(*) FROM jev_revision WHERE sujeto_tipo = 'lote';" | lee || true)"
revisa "filas en las 5 tablas nuevas" 0 "$(echo "SELECT (SELECT count(*) FROM jev_roster) + (SELECT count(*) FROM jev_senal) + (SELECT count(*) FROM jev_aviso) + (SELECT count(*) FROM jev_aviso_entrega) + (SELECT count(*) FROM jev_corrida);" | lee || true)"

echo "== 3) El ciclo no importa Jev; el cron sigue sin instalar"
revisa "cycle/apply_cola/apply_harvest importan app.jev_*" False \
  "$(ssh goncloud 'docker exec orbit-app-1 python -c "import sys, app.cycle, app.apply_cola, app.apply_harvest; print(any(m.startswith(\"app.jev\") for m in sys.modules))"')"
revisa "lineas jev-senales en el crontab de gon" 0 \
  "$(ssh goncloud 'crontab -u gon -l 2>/dev/null | grep -c jev-senales || true')"

echo "== 4) Primer ciclo del optimizador posterior al arranque"
if [ -z "$DESDE" ]; then
  echo "NO MEDIDO ciclos posteriores (sin arranque del contenedor)"
  NOMEDIDO=$((NOMEDIDO + 1))
elif [ -z "$(echo 'SELECT 1;' | lee || true)" ]; then
  echo "NO MEDIDO no se pudo leer optimizer_cycle"
  NOMEDIDO=$((NOMEDIDO + 1))
elif ! CICLOS=$(echo "SELECT id || ' ' || coalesce(platform::text, '-') || ' ' || status || ' ' || coalesce(decisions_count, 0) FROM optimizer_cycle WHERE started_at > '$DESDE' ORDER BY id;" | lee); then
  CICLOS=""
  echo "NO MEDIDO no se pudo leer optimizer_cycle"
  NOMEDIDO=$((NOMEDIDO + 1))
elif [ -z "$CICLOS" ]; then
  echo "INCOMPLETO: todavia no corre un ciclo despues de $DESDE; vuelve a correr el checklist despues del siguiente ciclo"
  PENDIENTE=1
else
  echo "$CICLOS"
  revisa "ciclos posteriores fallidos" 0 "$(echo "$CICLOS" | grep -c -E ' failed ' || true)"
  ENCURSO=$(echo "$CICLOS" | grep -c -E ' running ' || true)
  if [ "$ENCURSO" != "0" ]; then
    echo "INCOMPLETO: hay un ciclo en curso, todavia no termino; vuelve a correr el checklist cuando acabe"
    PENDIENTE=1
  fi
fi

echo "== RESULTADO: $FALLAS falla(s), $NOMEDIDO sin medir, ciclo posterior $([ "$PENDIENTE" = 1 ] && echo PENDIENTE || echo comprobado)"
[ "$FALLAS" -eq 0 ] || exit 1
[ "$NOMEDIDO" = 0 ] || exit 4
[ "$PENDIENTE" = 0 ] || exit 3
