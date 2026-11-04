#!/usr/bin/env bash
# Checklist post-deploy de S.5 segunda mitad (JEV ADS 02, guia Despliega):
# SOLO LECTURA. Corre UNA vez despues de desplegar.sh y deja su salida en
# checklist-salida.txt.
# Comprueba (guia S.5 Despliega):
# - /cortes, /gasto-sin-venta y /salud responden 200.
# - /api/dashboard/cortes trae `senal_disponible` en true y `senal` en null
#   en todos sus items, porque el job sigue apagado.
# - La pantalla nueva sale sin filas en las dos plataformas.
# Salidas: 0 = todo comprobado; 1 = alguna FALLA medida; 3 = sin fallas
# pero todavia sin ciclo posterior al arranque (o con uno en curso); 4 = no
# pude medir (el servidor no contesta o una lectura salio vacia, sin
# ninguna FALLA medida). Una FALLA medida sale con 1 aunque otra lectura
# quede vacia. "Posterior" cuenta desde el arranque del contenedor nuevo
# (docker inspect StartedAt); el sello es solo informativo.
# Modo --antes: la misma lectura ANTES del deploy, SIN ejecutar
# `python -m app.cli jev-senales` en prod (lo corre desplegar.sh en su
# paso 7). En ese modo el seco se sustituye por un probe de presencia del
# codigo (test -f, no ejecuta nada): cada FALLA del antes es algo que el
# despliegue cambia (codigo, senal en la API, pantalla nueva).
# Uso post:  cd ~/dev/goncloud-Orbit && bash docs/evidencia/jev-ads-02/ejecucion/S.5/checklist.sh <STAMP> > docs/evidencia/jev-ads-02/ejecucion/S.5/checklist-salida.txt; echo "exit=$?"
# Uso antes: cd ~/dev/goncloud-Orbit && bash docs/evidencia/jev-ads-02/ejecucion/S.5/checklist.sh <STAMP> --antes > docs/evidencia/jev-ads-02/ejecucion/S.5/checklist-antes-del-deploy.txt; echo "exit=$?"
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
cortes_senal() {  # lee stdin (JSON de /api/dashboard/cortes): "disponible items_con_senal"
  python3 -c "import json,sys
try: d = json.load(sys.stdin)
except Exception: print('ILEGIBLE -1'); raise SystemExit
items = d.get('items')
if not isinstance(items, list): print('ILEGIBLE -1'); raise SystemExit
con = sum(1 for i in items if i.get('senal') is not None)
print(('true' if d.get('senal_disponible') else 'false'), con)"
}
gasto_filas() {  # lee stdin (JSON de /api/dashboard/gasto-sin-venta): total_filas
  python3 -c "import json,sys
try: d = json.load(sys.stdin)
except Exception: print('ILEGIBLE'); raise SystemExit
print(d.get('total_filas', 'ILEGIBLE'))"
}

echo "== 0) El servidor contesta"
RUNNING=$(ssh goncloud "docker inspect -f '{{.State.Running}}' orbit-app-1" || true)
if [ -z "$RUNNING" ]; then echo "NO MEDIDO el servidor no contesta (docker inspect vacio)"; exit 4; fi
revisa "orbit-app-1 corriendo" true "$RUNNING"
DESDE=$(ssh goncloud "docker inspect -f '{{.State.StartedAt}}' orbit-app-1" || true)
if [ -z "$DESDE" ]; then echo "NO MEDIDO arranque del contenedor (StartedAt vacio)"; NOMEDIDO=$((NOMEDIDO + 1)); fi

echo "== Checklist S.5 (solo lectura; sello $SELLO, contenedor desde ${DESDE:-sin-dato}, modo ${MODO:-post})"

echo "== 1) HTTP: health, /cortes, /gasto-sin-venta (dos mercados), /salud"
revisa "GET /health" 200 "$(ssh goncloud 'curl -s -o /dev/null -w %{http_code} http://127.0.0.1:8010/health')"
revisa "GET /cortes" 200 "$(ssh goncloud 'curl -s -o /dev/null -w %{http_code} http://127.0.0.1:8010/cortes')"
revisa "GET /gasto-sin-venta mx" 200 "$(ssh goncloud 'curl -s -o /dev/null -w %{http_code} "http://127.0.0.1:8010/gasto-sin-venta?plataforma=amazon_mx"')"
revisa "GET /gasto-sin-venta us" 200 "$(ssh goncloud 'curl -s -o /dev/null -w %{http_code} "http://127.0.0.1:8010/gasto-sin-venta?plataforma=amazon_us"')"
revisa "GET /salud" 200 "$(ssh goncloud 'curl -s -o /dev/null -w %{http_code} http://127.0.0.1:8010/salud')"

if [ "$MODO" = "--antes" ]; then
  echo "== 2) Codigo de S.5 en el contenedor (presencia, SIN ejecutarlo: el seco lo corre desplegar.sh)"
  revisa "plantilla gasto-sin-venta en el contenedor" presente \
    "$(ssh goncloud 'docker exec orbit-app-1 test -f app/templates/gasto_sin_venta.html && echo presente || echo ausente')"
else
  echo "== 2) Seco del job apagado (exit 0, dice apagado)"
  SECO=$(ssh goncloud 'docker exec orbit-app-1 python -m app.cli jev-senales' 2>&1)
  RC=$?
  echo "$SECO"
  revisa "seco exit" 0 "$RC"
  revisa "seco dice apagado" apagado "$(echo "$SECO" | grep -o apagado | head -1)"
fi

echo "== 3) /api/dashboard/cortes: senal_disponible true y senal null en todo (job apagado)"
CORTES=$(ssh goncloud 'curl -s http://127.0.0.1:8010/api/dashboard/cortes' || true)
if [ -z "$CORTES" ]; then
  echo "NO MEDIDO cortes (respuesta vacia)"; NOMEDIDO=$((NOMEDIDO + 1))
else
  SENAL=$(echo "$CORTES" | cortes_senal || true)
  revisa "cortes senal + items con senal" "true 0" "$SENAL"
fi

echo "== 4) La pantalla nueva sale sin filas en las dos plataformas"
for MERCADO in amazon_mx amazon_us; do
  GASTO=$(ssh goncloud "curl -s 'http://127.0.0.1:8010/api/dashboard/gasto-sin-venta?plataforma=$MERCADO'" || true)
  if [ -z "$GASTO" ]; then
    echo "NO MEDIDO gasto-sin-venta $MERCADO (respuesta vacia)"; NOMEDIDO=$((NOMEDIDO + 1))
  else
    revisa "gasto-sin-venta $MERCADO total_filas" 0 "$(echo "$GASTO" | gasto_filas || true)"
  fi
  revisa "HTML gasto-sin-venta $MERCADO sin filas" "Sin filas en este mercado" \
    "$(ssh goncloud "curl -s 'http://127.0.0.1:8010/gasto-sin-venta?plataforma=$MERCADO'" | grep -o 'Sin filas en este mercado' | head -1 || true)"
done

echo "== 5) Bloque jev de /api/dashboard/salud dice apagado"
SALUD=$(ssh goncloud 'curl -s http://127.0.0.1:8010/api/dashboard/salud' || true)
if [ -z "$SALUD" ]; then
  echo "NO MEDIDO bloque jev (salud vacia)"; NOMEDIDO=$((NOMEDIDO + 1))
else
  revisa "bloque jev" apagado "$(echo "$SALUD" | estado_jev)"
fi

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
