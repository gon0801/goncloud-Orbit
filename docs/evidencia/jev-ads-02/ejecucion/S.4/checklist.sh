#!/usr/bin/env bash
# Checklist post-deploy de S.4 (JEV ADS 02, guia Despliega): SOLO LECTURA (el
# seco del job no escribe por diseno). Corre UNA vez despues de desplegar.sh
# + la linea de cron del dueno, y deja su salida en checklist-salida.txt.
# Salidas: 0 = todo comprobado; 1 = alguna FALLA medida; 3 = sin fallas
# pero todavia sin ciclo posterior al arranque (o con uno en curso); 4 = no
# pude medir (el servidor no contesta o una lectura salio vacia, sin
# ninguna FALLA medida). Una FALLA medida sale con 1 aunque otra lectura
# quede vacia. "Posterior" cuenta desde el arranque del contenedor nuevo
# (docker inspect StartedAt); el sello es solo informativo.
# Modo --antes: la misma lectura ANTES del deploy, SIN ejecutar
# `python -m app.cli jev-senales` en prod (lo corre desplegar.sh en su
# paso 7) y sin tocar el crontab. En ese modo el seco se sustituye por un
# probe de presencia del codigo (test -f, no ejecuta nada): cada FALLA del
# antes es algo que el despliegue cambia (codigo, bloque jev, cron).
# Uso post:  cd ~/dev/goncloud-Orbit && bash docs/evidencia/jev-ads-02/ejecucion/S.4/checklist.sh <STAMP> > docs/evidencia/jev-ads-02/ejecucion/S.4/checklist-salida.txt; echo "exit=$?"
# Uso antes: cd ~/dev/goncloud-Orbit && bash docs/evidencia/jev-ads-02/ejecucion/S.4/checklist.sh <STAMP> --antes > docs/evidencia/jev-ads-02/ejecucion/S.4/checklist-antes-del-deploy.txt; echo "exit=$?"
set -uo pipefail

SELLO=${1:-sin-sello}
MODO=${2:-}
case "$MODO" in ''|--antes) ;; *) echo "ABORTA: opcion desconocida '$MODO' (solo --antes)"; exit 2 ;; esac
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
estado_jev() {  # lee stdin (JSON de /api/dashboard/salud) e imprime apagado|encendido|ausente
  python3 -c "import json,sys
try: d = json.load(sys.stdin)
except Exception: print('ILEGIBLE'); raise SystemExit
j = d.get('jev')
if not isinstance(j, dict): print('ausente'); raise SystemExit
print('encendido' if (j.get('interruptor') or {}).get('encendido') else 'apagado')"
}

echo "== 0) El servidor contesta"
RUNNING=$(ssh goncloud "docker inspect -f '{{.State.Running}}' orbit-app-1" || true)
if [ -z "$RUNNING" ]; then echo "NO MEDIDO el servidor no contesta (docker inspect vacio)"; exit 4; fi
revisa "orbit-app-1 corriendo" true "$RUNNING"
DESDE=$(ssh goncloud "docker inspect -f '{{.State.StartedAt}}' orbit-app-1" || true)
if [ -z "$DESDE" ]; then echo "NO MEDIDO arranque del contenedor (StartedAt vacio)"; NOMEDIDO=$((NOMEDIDO + 1)); fi

echo "== Checklist S.4 (solo lectura; sello $SELLO, contenedor desde ${DESDE:-sin-dato}, modo ${MODO:-post})"

echo "== 1) HTTP: health, /cortes, /salud"
revisa "GET /health" 200 "$(ssh goncloud 'curl -s -o /dev/null -w %{http_code} http://127.0.0.1:8010/health')"
revisa "GET /cortes" 200 "$(ssh goncloud 'curl -s -o /dev/null -w %{http_code} http://127.0.0.1:8010/cortes')"
revisa "GET /salud" 200 "$(ssh goncloud 'curl -s -o /dev/null -w %{http_code} http://127.0.0.1:8010/salud')"

if [ "$MODO" = "--antes" ]; then
  echo "== 2) Codigo del job en el contenedor (presencia, SIN ejecutarlo: el seco lo corre desplegar.sh)"
  revisa "codigo jev-senales en el contenedor" presente \
    "$(ssh goncloud 'docker exec orbit-app-1 test -f app/jev_senales.py && echo presente || echo ausente')"
else
  echo "== 2) Seco del job apagado (exit 0, dice apagado)"
  SECO=$(ssh goncloud 'docker exec orbit-app-1 python -m app.cli jev-senales' 2>&1)
  RC=$?
  echo "$SECO"
  revisa "seco exit" 0 "$RC"
  revisa "seco dice apagado" apagado "$(echo "$SECO" | grep -o apagado | head -1)"
fi

echo "== 3) Bloque jev de /api/dashboard/salud dice apagado"
SALUD=$(ssh goncloud 'curl -s http://127.0.0.1:8010/api/dashboard/salud' || true)
if [ -z "$SALUD" ]; then
  echo "NO MEDIDO bloque jev (salud vacia)"; NOMEDIDO=$((NOMEDIDO + 1))
else
  revisa "bloque jev" apagado "$(echo "$SALUD" | estado_jev)"
fi

echo "== 4) Las cinco tablas de S.3 con cero filas; cero filas lote"
# En dos lecturas: un CASE con las cinco tablas en la rama ELSE no sirve,
# porque el planificador valida que existan antes de ejecutar el CASE.
NUEVAS_T=$(echo "SELECT count(*) FROM pg_tables WHERE schemaname = 'public' AND tablename IN ('jev_roster', 'jev_senal', 'jev_aviso', 'jev_aviso_entrega', 'jev_corrida');" | lee || true)
if [ -z "$NUEVAS_T" ]; then FILAS=""
elif [ "$NUEVAS_T" = "5" ]; then
  FILAS=$(echo "SELECT (SELECT count(*) FROM jev_roster) + (SELECT count(*) FROM jev_senal) + (SELECT count(*) FROM jev_aviso) + (SELECT count(*) FROM jev_aviso_entrega) + (SELECT count(*) FROM jev_corrida);" | lee || true)
else FILAS="sin-tablas"; fi
revisa "filas en las 5 tablas nuevas" 0 "$FILAS"
revisa "filas lote" 0 "$(echo "SELECT count(*) FROM jev_revision WHERE sujeto_tipo = 'lote';" | lee || true)"

echo "== 5) El ciclo no importa Jev; el crontab de gon trae la linea del job"
revisa "cycle/apply_cola/apply_harvest importan app.jev_*" False \
  "$(ssh goncloud 'docker exec orbit-app-1 python -c "import sys, app.cycle, app.apply_cola, app.apply_harvest; print(any(m.startswith(\"app.jev\") for m in sys.modules))"')"
revisa "lineas jev-senales en el crontab de gon" 1 \
  "$(ssh goncloud 'crontab -u gon -l 2>/dev/null | grep -c jev-senales || true')"

echo "== 6) Primer ciclo del optimizador posterior al arranque"
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
