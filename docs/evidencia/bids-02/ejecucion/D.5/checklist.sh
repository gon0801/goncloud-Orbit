#!/usr/bin/env bash
# Checklist post-deploy de D.5 (BIDS 02 seccion 6): SOLO LECTURA. Corre
# UNA vez despues de desplegar.sh y deja su salida en un txt.
# Patron de salidas del de S.3: 0 = todo comprobado; 1 = alguna FALLA
# medida; 4 = no pudo medir (el servidor no contesta o una lectura
# salio vacia, sin ninguna FALLA medida). Una FALLA medida sale con 1
# aunque otra lectura quede vacia. D.5 no tiene gates temporales. En
# simulacion (ORBIT_SIMULACION=1) la base es ORBIT_SIM_DSN y el HTTP es
# ORBIT_SIM_API: no hay ssh.
# Comprueba (guia D.5): /health en 200; /busquedas-sin-venta en 200
# en MX y US y trae su frase de referencia (la del porcentaje o la de
# sin referencia); /productos-sin-clics en 200 en MX y US; y que el
# checklist no escribio (conteo de search_term_observation igual antes
# y despues).
# Uso: bash docs/evidencia/bids-02/ejecucion/D.5/checklist.sh <STAMP> > checklist-salida.txt; echo "exit=$?"
set -uo pipefail

SELLO=${1:-sin-sello}
REPO=$(git rev-parse --show-toplevel)
DIR=docs/evidencia/bids-02/ejecucion/D.5
SIM=${ORBIT_SIMULACION:-0}
DSN_SIM=${ORBIT_SIM_DSN:-postgresql://orbit:orbit@127.0.0.1:5433/orbit_sim_d5}
API_SIM=${ORBIT_SIM_API:-http://127.0.0.1:8012}
FALLAS=0
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
cuerpo() {  # cuerpo <ruta> -> body
  if [ "$SIM" = 1 ]; then
    curl -s "$API_SIM$1" || true
  else
    ssh goncloud "curl -s 'http://127.0.0.1:8010$1'" || true
  fi
}

echo "== 0) El servidor contesta"
if [ "$SIM" = 1 ]; then
  echo "MODO SIMULACION: base $DSN_SIM, HTTP $API_SIM"
  H=$(http /health)
  if [ -z "$H" ]; then echo "NO MEDIDO la app local no contesta"; exit 4; fi
else
  RUNNING=$(ssh goncloud "docker inspect -f '{{.State.Running}}' orbit-app-1" || true)
  if [ -z "$RUNNING" ]; then echo "NO MEDIDO el servidor no contesta (docker inspect vacio)"; exit 4; fi
  revisa "orbit-app-1 corriendo" true "$RUNNING"
fi

echo "== Checklist D.5 (solo lectura; sello $SELLO)"

echo "== 1) HTTP: /health"
revisa "GET /health" 200 "$(http /health)"

OBS_ANTES="$(echo 'SELECT count(*) FROM search_term_observation;' | lee || true)"
if [ -z "$OBS_ANTES" ]; then
  echo "NO MEDIDO search_term_observation antes (lectura vacia)"; NOMEDIDO=$((NOMEDIDO + 1))
else
  echo "OK    search_term_observation antes = $OBS_ANTES"
fi

echo "== 2) La pantalla de busquedas responde en cada mercado y trae su frase"
for MERCADO in amazon_mx amazon_us; do
  revisa "HTML busquedas-sin-venta $MERCADO" 200 "$(http "/busquedas-sin-venta?plataforma=$MERCADO")"
  HTML=$(cuerpo "/busquedas-sin-venta?plataforma=$MERCADO")
  if [ -z "$HTML" ]; then
    echo "NO MEDIDO frase de referencia $MERCADO (lectura vacia)"; NOMEDIDO=$((NOMEDIDO + 1))
  elif printf '%s' "$HTML" | grep -q "de tu gasto cayó en búsquedas que no vendieron"; then
    echo "OK    frase de referencia $MERCADO (con porcentajes)"
  elif printf '%s' "$HTML" | grep -q "no hay referencia de azar"; then
    echo "OK    frase de referencia $MERCADO (sin referencia)"
  else
    echo "FALLA frase de referencia $MERCADO: ninguna de las dos frases en la pagina"
    FALLAS=$((FALLAS + 1))
  fi
done

echo "== 3) La pantalla de productos sigue respondiendo en cada mercado"
for MERCADO in amazon_mx amazon_us; do
  revisa "HTML productos-sin-clics $MERCADO" 200 "$(http "/productos-sin-clics?plataforma=$MERCADO")"
done

echo "== 4) search_term_observation sin escrituras del checklist"
if [ -z "$OBS_ANTES" ]; then
  echo "NO MEDIDO search_term_observation despues (no hubo conteo antes)"
  NOMEDIDO=$((NOMEDIDO + 1))
else
  revisa "search_term_observation sin escrituras del checklist" "$OBS_ANTES" \
    "$(echo 'SELECT count(*) FROM search_term_observation;' | lee || true)"
fi

echo "== RESULTADO: $FALLAS falla(s), $NOMEDIDO sin medir"
[ "$FALLAS" -eq 0 ] || exit 1
[ "$NOMEDIDO" = 0 ] || exit 4
