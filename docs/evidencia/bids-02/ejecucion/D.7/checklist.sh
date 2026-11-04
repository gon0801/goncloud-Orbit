#!/usr/bin/env bash
# Checklist post-deploy de D.7 (BIDS 02 seccion 9): SOLO LECTURA. Corre
# UNA vez despues de desplegar.sh y deja su salida en un txt con su
# linea exit= al final (R13-M).
# Patron de salidas del de S.3: 0 = todo comprobado; 1 = alguna FALLA
# medida; 4 = no pudo medir (el servidor no contesta o una lectura salio
# vacia, sin ninguna FALLA medida). Una FALLA medida sale con 1 aunque
# otra lectura quede vacia. D.7 no tiene gates temporales ni
# migraciones que verificar. En simulacion (ORBIT_SIMULACION=1) la
# base es ORBIT_SIM_DSN y el HTTP es ORBIT_SIM_API: no hay ssh.
# Comprueba: /health en 200; campana_ajuste y
# ads_campana_config_observation existen (se guardan sus conteos y al
# final se verifica que el checklist no escribio);
# /productos-sin-clics en 200 en MX y US; crontab -u gon -l trae UNA
# linea impulso-vigia (nadie la toco); y la pantalla de busquedas de
# cada mercado trae su moneda y su plataforma, con cada gasto en dos
# decimales (R13-K).
# Uso: bash docs/evidencia/bids-02/ejecucion/D.7/checklist.sh <STAMP> > checklist-<sello>.txt 2>&1; echo "exit=$?" >> checklist-<sello>.txt
set -uo pipefail

SELLO=${1:-sin-sello}
REPO=$(git rev-parse --show-toplevel)
DIR=docs/evidencia/bids-02/ejecucion/D.7
SIM=${ORBIT_SIMULACION:-0}
DSN_SIM=${ORBIT_SIM_DSN:-postgresql://orbit:orbit@127.0.0.1:5433/orbit_sim_d7}
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

echo "== Checklist D.7 (solo lectura; sello $SELLO)"

echo "== 1) HTTP: /health"
revisa "GET /health" 200 "$(http /health)"

echo "== 2) Esquema: las tablas de la seccion existen"
for TABLA in campana_ajuste ads_campana_config_observation; do
  revisa "tabla $TABLA" t \
    "$(echo "SELECT to_regclass('public.$TABLA') IS NOT NULL;" | lee || true)"
  N_ANTES="$(echo "SELECT count(*) FROM $TABLA;" | lee || true)"
  if [ -z "$N_ANTES" ]; then
    echo "NO MEDIDO $TABLA antes (lectura vacia)"; NOMEDIDO=$((NOMEDIDO + 1))
  else
    echo "OK    $TABLA antes = $N_ANTES"
  fi
  eval "ANTES_$TABLA='$N_ANTES'"
done

echo "== 3) La pantalla de productos responde en cada mercado"
for MERCADO in amazon_mx amazon_us; do
  revisa "HTML productos-sin-clics $MERCADO" 200 "$(http "/productos-sin-clics?plataforma=$MERCADO")"
done

echo "== 4) El crontab trae UNA linea impulso-vigia"
if [ "$SIM" = 1 ]; then
  revisa "lineas impulso-vigia" 1 \
    "$(grep -c impulso-vigia "${TMPDIR:-/tmp}/orbit-d7-sim/crontab-gon" 2>/dev/null || true)"
else
  revisa "lineas impulso-vigia" 1 \
    "$(ssh goncloud 'crontab -l -u gon 2>/dev/null' | grep -c impulso-vigia || true)"
fi

echo "== 5) Las dos tablas sin escrituras del checklist"
for TABLA in campana_ajuste ads_campana_config_observation; do
  eval "ANTES=\$ANTES_$TABLA"
  if [ -z "$ANTES" ]; then
    echo "NO MEDIDO $TABLA despues (no hubo conteo antes)"; NOMEDIDO=$((NOMEDIDO + 1))
  else
    revisa "$TABLA sin escrituras del checklist" "$ANTES" \
      "$(echo "SELECT count(*) FROM $TABLA;" | lee || true)"
  fi
done

moneda_y_plataforma() {  # stdin: cuerpo JSON -> "moneda|plataforma" o vacio
  python3 -c "
import json, sys
try:
    datos = json.load(sys.stdin)
except ValueError:
    raise SystemExit(0)
print(str(datos.get('moneda') or '') + '|' + str(datos.get('plataforma') or ''))
"
}

gastos_en_dos_decimales() {  # stdin: cuerpo JSON -> ok|mal|vacio
  python3 -c "
import json, re, sys
try:
    datos = json.load(sys.stdin)
except ValueError:
    raise SystemExit(0)
gastos = [f.get('gasto') for f in (datos.get('asins') or []) + (datos.get('palabras') or [])]
gastos = [g for g in gastos if g is not None]
if not gastos:
    print('vacio')
elif all(re.fullmatch(r'-?[0-9]+\.[0-9]{2}', str(g)) for g in gastos):
    print('ok')
else:
    print('mal')
"
}

echo "== 6) JSON de la pantalla de busquedas por mercado: su moneda y su plataforma (R13-E), gastos en dos decimales (R13-K)"
for PAR in "amazon_mx MXN" "amazon_us USD"; do
  MERCADO=${PAR%% *}
  MONEDA=${PAR##* }
  CUERPO=$(cuerpo "/api/dashboard/busquedas-sin-venta?plataforma=$MERCADO" || true)
  DATO=$(printf '%s' "$CUERPO" | moneda_y_plataforma || true)
  revisa "busquedas $MERCADO moneda" "$MONEDA" "$(printf '%s' "$DATO" | cut -d'|' -f1)"
  revisa "busquedas $MERCADO plataforma" "$MERCADO" "$(printf '%s' "$DATO" | cut -d'|' -f2)"
  FORMATO=$(printf '%s' "$CUERPO" | gastos_en_dos_decimales || true)
  if [ "$FORMATO" = "vacio" ]; then
    echo "NO MEDIDO busquedas $MERCADO gastos en dos decimales (sin filas con gasto)"; NOMEDIDO=$((NOMEDIDO + 1))
  else
    revisa "busquedas $MERCADO gastos en dos decimales" "ok" "$FORMATO"
  fi
done

echo "== RESULTADO: $FALLAS falla(s), $NOMEDIDO sin medir"
[ "$FALLAS" -eq 0 ] || exit 1
[ "$NOMEDIDO" = 0 ] || exit 4
