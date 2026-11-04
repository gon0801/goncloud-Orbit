#!/usr/bin/env bash
# Checklist post-deploy de D.1 (BIDS 02 seccion 2): SOLO LECTURA. Corre UNA
# vez despues de desplegar.sh y del primer ciclo posterior al arranque (el
# de las 08:40 UTC del dia de D.1), y deja su salida en un txt.
# Patron de salidas del de S.3: 0 = todo comprobado; 1 = alguna FALLA
# medida; 3 = sin fallas pero todavia sin ciclo posterior al arranque (o
# con uno en curso); 4 = no pudo medir (el servidor no contesta o una
# lectura salio vacia, sin ninguna FALLA medida). Una FALLA medida sale
# con 1 aunque otra lectura quede vacia. "Posterior" cuenta desde el
# arranque del contenedor nuevo (docker inspect StartedAt), no desde el
# sello: el sello que toma como argumento es solo informativo. En
# simulacion (ORBIT_SIMULACION=1) el arranque lo da ORBIT_SIM_DESDE
# (timestamp ISO con zona), la base es ORBIT_SIM_DSN y el HTTP es
# ORBIT_SIM_API: no hay ssh.
# Ademas del Comprueba del paso, imprime los conteos de pause, negative y
# harvest del primer ciclo posterior de cada plataforma junto a los del
# ciclo anterior, para que el lead los compare a ojo.
# Uso: bash docs/evidencia/bids-02/ejecucion/D.1/checklist.sh <STAMP> > checklist-salida.txt; echo "exit=$?"
set -uo pipefail

SELLO=${1:-sin-sello}
REPO=$(git rev-parse --show-toplevel)
DIR=docs/evidencia/bids-02/ejecucion/D.1
SIM=${ORBIT_SIMULACION:-0}
DSN_SIM=${ORBIT_SIM_DSN:-postgresql://orbit:orbit@127.0.0.1:5433/orbit_sim_d1}
API_SIM=${ORBIT_SIM_API:-http://127.0.0.1:8011}
FALLAS=0
PENDIENTE=0
NOMEDIDO=0
cd "$REPO"

revisa() {  # revisa <nombre> <esperado> <obtenido>
  if [ -z "$3" ]; then echo "NO MEDIDO $1 (lectura vacia)"; NOMEDIDO=$((NOMEDIDO + 1));
  elif [ "$2" = "$3" ]; then echo "OK    $1 = $3";
  else echo "FALLA $1: esperado '$2', obtenido '$3'"; FALLAS=$((FALLAS + 1)); fi
}
lee() {
  if [ "$SIM" = 1 ]; then
    psql "$DSN_SIM" -X -q -tA -v ON_ERROR_STOP=1
  else
    ssh goncloud 'DSN=$(docker exec orbit-app-1 printenv ORBIT_DSN_READ); docker exec -i orbit-db-1 psql "$DSN" -X -q -tA -v ON_ERROR_STOP=1'
  fi
}
http() {  # http <ruta> -> codigo
  if [ "$SIM" = 1 ]; then
    curl -s -o /dev/null -w '%{http_code}' "$API_SIM$1" || true
  else
    ssh goncloud "curl -s -o /dev/null -w %{http_code} 'http://127.0.0.1:8010$1'" || true
  fi
}

echo "== 0) El servidor contesta"
if [ "$SIM" = 1 ]; then
  echo "MODO SIMULACION: base $DSN_SIM, HTTP $API_SIM"
  DESDE=${ORBIT_SIM_DESDE:-}
  if [ -z "$DESDE" ]; then echo "NO MEDIDO ORBIT_SIM_DESDE vacia (el arranque simulado es obligatorio)"; exit 4; fi
  H=$(http /health)
  if [ -z "$H" ]; then echo "NO MEDIDO la app local no contesta"; exit 4; fi
  echo "arranque simulado: $DESDE"
else
  RUNNING=$(ssh goncloud "docker inspect -f '{{.State.Running}}' orbit-app-1" || true)
  if [ -z "$RUNNING" ]; then echo "NO MEDIDO el servidor no contesta (docker inspect vacio)"; exit 4; fi
  revisa "orbit-app-1 corriendo" true "$RUNNING"
  DESDE=$(ssh goncloud "docker inspect -f '{{.State.StartedAt}}' orbit-app-1" || true)
  if [ -z "$DESDE" ]; then echo "NO MEDIDO arranque del contenedor (StartedAt vacio)"; NOMEDIDO=$((NOMEDIDO + 1)); fi
fi

echo "== Checklist D.1 (solo lectura; sello $SELLO, contenedor desde ${DESDE:-sin-dato})"

echo "== 1) HTTP: /health, /keywords-danadas, /ruido"
revisa "GET /health" 200 "$(http /health)"
revisa "GET /keywords-danadas" 200 "$(http /keywords-danadas)"
revisa "GET /ruido" 200 "$(http /ruido)"

echo "== 2) Esquema de D.1 (esperado t|t)"
revisa "v_hoja_activa y v_cambio_bid creadas" "t|t" \
  "$(echo "SELECT to_regclass('public.v_hoja_activa') IS NOT NULL, to_regclass('public.v_cambio_bid') IS NOT NULL;" | lee || true)"

echo "== 3) Clave ausente y fraccion US (esperado f|f y 0.8)"
revisa "claves de politica mx|us" "f|f" \
  "$(q_out=$(echo "SELECT settings ? 'ads_bid_politica_amazon_mx', settings ? 'ads_bid_politica_amazon_us' FROM config_version ORDER BY id DESC LIMIT 1;" | lee || true); printf '%s' "$q_out")"
revisa "fraccion US" "0.8" \
  "$(echo "SELECT settings ->> 'ads_target_fraccion_margen_amazon_us' FROM config_version ORDER BY id DESC LIMIT 1;" | lee || true)"

echo "== 4) Primer ciclo posterior vs ciclo anterior, por plataforma"
if [ -z "${DESDE:-}" ]; then
  echo "NO MEDIDO ciclos posteriores (sin arranque del contenedor)"
  NOMEDIDO=$((NOMEDIDO + 1))
elif ! CICLOS=$(echo "SELECT id || '|' || platform::text || '|' || status || '|' || to_char(started_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI') FROM optimizer_cycle WHERE started_at > '$DESDE' AND platform IN ('amazon_mx', 'amazon_us') ORDER BY platform, started_at;" | lee); then
  CICLOS=""
  echo "NO MEDIDO no se pudo leer optimizer_cycle"
  NOMEDIDO=$((NOMEDIDO + 1))
elif [ -z "$CICLOS" ]; then
  echo "INCOMPLETO: todavia no corre un ciclo despues de $DESDE; vuelve a correr el checklist despues del ciclo de las 08:40 UTC"
  PENDIENTE=1
else
  echo "$CICLOS"
  revisa "ciclos posteriores fallidos" 0 "$(printf '%s\n' "$CICLOS" | grep -c -E '\|failed\|' || true)"
  ENCURSO=$(printf '%s\n' "$CICLOS" | grep -c -E '\|running\|' || true)
  if [ "$ENCURSO" != "0" ]; then
    echo "INCOMPLETO: hay un ciclo en curso, todavia no termino; vuelve a correr el checklist cuando acabe"
    PENDIENTE=1
  fi
  for plat in amazon_mx amazon_us; do
    POST=$(printf '%s\n' "$CICLOS" | grep -E "\|$plat\|" | head -1 || true)
    if [ -z "$POST" ]; then
      echo "INCOMPLETO: $plat todavia sin ciclo posterior; vuelve a correr el checklist despues del ciclo"
      PENDIENTE=1
      continue
    fi
    POST_ID=$(printf '%s' "$POST" | cut -d'|' -f1)
    POST_HORA=$(printf '%s' "$POST" | cut -d'|' -f4 | cut -d' ' -f2)
    ANT=$(echo "SELECT id FROM optimizer_cycle WHERE started_at <= '$DESDE' AND platform = '$plat' ORDER BY started_at DESC LIMIT 1;" | lee || true)
    echo "-- $plat: posterior id=$POST_ID hora_utc=$POST_HORA, anterior id=${ANT:-sin-dato}"
    case "$POST_HORA" in
      08:4*|08:5*) echo "OK    hora del primer ciclo $plat = $POST_HORA (ventana 08:40-08:59 UTC)" ;;
      *) echo "FALLA hora del primer ciclo $plat: esperado ventana 08:40-08:59 UTC, obtenido '$POST_HORA'"; FALLAS=$((FALLAS + 1)) ;;
    esac
    revisa "decisiones bid del ciclo posterior $plat" 0 \
      "$(echo "SELECT count(*) FROM decision WHERE cycle_id = $POST_ID AND kind = 'bid';" | lee || true)"
    revisa "notes con politica_apagada $plat" t \
      "$(echo "SELECT notes LIKE '%politica_apagada%' FROM optimizer_cycle WHERE id = $POST_ID;" | lee || true)"
    if [ -z "$ANT" ]; then
      echo "NO MEDIDO ciclo anterior de $plat (no se puede comparar)"
      NOMEDIDO=$((NOMEDIDO + 1))
      continue
    fi
    for kind in pause negative harvest; do
      C_ANT=$(echo "SELECT count(*) FROM decision WHERE cycle_id = $ANT AND kind = '$kind';" | lee || true)
      C_POST=$(echo "SELECT count(*) FROM decision WHERE cycle_id = $POST_ID AND kind = '$kind';" | lee || true)
      echo "conteo $kind $plat: anterior=${C_ANT:-?} posterior=${C_POST:-?}"
      [ -n "$C_ANT" ] && [ -n "$C_POST" ] || NOMEDIDO=$((NOMEDIDO + 1))
    done
    SUMA_POST=$(echo "SELECT count(*) FROM decision WHERE cycle_id = $POST_ID AND kind IN ('pause','negative','harvest');" | lee || true)
    SUMA_ANT=$(echo "SELECT count(*) FROM decision WHERE cycle_id = $ANT AND kind IN ('pause','negative','harvest');" | lee || true)
    if [ -z "$SUMA_POST" ] || [ -z "$SUMA_ANT" ]; then
      echo "NO MEDIDO sumas de $plat (lectura vacia)"; NOMEDIDO=$((NOMEDIDO + 1))
    elif [ "$SUMA_POST" != "0" ] || [ "$SUMA_ANT" = "0" ]; then
      echo "OK    $plat decide como antes (anterior=$SUMA_ANT posterior=$SUMA_POST)"
    else
      echo "FALLA $plat dejo de decidir: anterior=$SUMA_ANT posterior=0"; FALLAS=$((FALLAS + 1))
    fi
  done
fi

echo "== RESULTADO: $FALLAS falla(s), $NOMEDIDO sin medir, ciclo posterior $([ "$PENDIENTE" = 1 ] && echo PENDIENTE || echo comprobado)"
[ "$FALLAS" -eq 0 ] || exit 1
[ "$NOMEDIDO" = 0 ] || exit 4
[ "$PENDIENTE" = 0 ] || exit 3
