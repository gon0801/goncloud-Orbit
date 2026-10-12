#!/usr/bin/env bash
# Checklist post-deploy de D.4 (BIDS 02 seccion 5): SOLO LECTURA. Corre UNA
# vez despues de desplegar.sh y deja su salida en un txt.
# Patron de salidas del de S.3: 0 = todo comprobado; 1 = alguna FALLA
# medida; 4 = no pudo medir (el servidor no contesta o una lectura salio
# vacia, sin ninguna FALLA medida). Una FALLA medida sale con 1 aunque otra
# lectura quede vacia. D.4 no tiene gates temporales: la vista previa y los
# botones se verifican contra lo que ya dejo D.2 (config vigente y campanas
# ENABLED). En simulacion (ORBIT_SIMULACION=1) la base es ORBIT_SIM_DSN y el
# HTTP es ORBIT_SIM_API: no hay ssh.
# Comprueba (guia D.4): /health en 200; impulso, impulso_lectura,
# anuncio_retiro, anuncio_reposicion y anuncio_retiro_intencion existen
# (el dueno ya pudo lanzar:
# se guardan sus conteos y al final se verifica que el checklist no
# escribio); /productos-sin-clics en 200 en MX y
# US; crontab -u gon -l trae UNA linea impulso-vigia; la vista previa
# del impulso con un candidato del dashboard responde 200 y no escribe
# (conteos de fabrica_lote e impulso iguales antes y despues); imprime
# si MUTATION_REQUEST_TYPES trae PUT /sp/productAds.
# Uso: bash docs/evidencia/bids-02/ejecucion/D.4/checklist.sh <STAMP> > checklist-salida.txt; echo "exit=$?"
set -uo pipefail

SELLO=${1:-sin-sello}
REPO=$(git rev-parse --show-toplevel)
DIR=docs/evidencia/bids-02/ejecucion/D.4
SIM=${ORBIT_SIMULACION:-0}
DSN_SIM=${ORBIT_SIM_DSN:-postgresql://orbit:orbit@127.0.0.1:5433/orbit_sim_d4}
API_SIM=${ORBIT_SIM_API:-http://127.0.0.1:8012}
FALLAS=0
NOMEDIDO=0
cd "$REPO"

revisa() {  # revisa <nombre> <esperado> <obtenido>
  if [ -z "$3" ]; then echo "NO MEDIDO $1 (lectura vacia)"; NOMEDIDO=$((NOMEDIDO + 1));
  elif [ "$2" = "$3" ]; then echo "OK    $1 = $3";
  else echo "FALLA $1: esperado '$2', obtenido '$3'"; FALLAS=$((FALLAS + 1)); fi
}
positivo() {  # positivo <nombre> <obtenido>: OK si es un entero > 0
  case "$2" in
    ''|*[!0-9]*) echo "NO MEDIDO $1 (lectura vacia)"; NOMEDIDO=$((NOMEDIDO + 1)) ;;
    0) echo "FALLA $1: esperado > 0, obtenido 0"; FALLAS=$((FALLAS + 1)) ;;
    *) echo "OK    $1 = $2" ;;
  esac
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
trae_put() {  # true si MUTATION_REQUEST_TYPES trae PUT /sp/productAds
  if [ "$SIM" = 1 ]; then
    PY="$REPO/.venv/bin/python"
    [ -x "$PY" ] || PY=python3
    (cd "$REPO" && "$PY" -c 'from app.ads.write import MUTATION_REQUEST_TYPES; print(str(("PUT", "/sp/productAds") in MUTATION_REQUEST_TYPES).lower())') || true
  else
    ssh goncloud 'docker exec orbit-app-1 python -c '\''from app.ads.write import MUTATION_REQUEST_TYPES; print(str(("PUT", "/sp/productAds") in MUTATION_REQUEST_TYPES).lower())'\''' || true
  fi
}
post() {  # post <ruta> <json> -> codigo
  if [ "$SIM" = 1 ]; then
    curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -d "$2" "$API_SIM$1" || true
  else
    ssh goncloud "curl -s -o /dev/null -w %{http_code} -X POST -H 'Content-Type: application/json' -d '$2' 'http://127.0.0.1:8010$1'" || true
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

echo "== Checklist D.4 (solo lectura; sello $SELLO)"

echo "== 1) HTTP: /health"
revisa "GET /health" 200 "$(http /health)"

echo "== 2) Esquema de D.4: las cinco tablas existen (el dueno ya pudo lanzar)"
for TABLA in impulso impulso_lectura anuncio_retiro anuncio_reposicion anuncio_retiro_intencion; do
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
revisa "columna fabrica_lote.pausado_por (0068)" t \
  "$(echo "SELECT count(*) = 1 FROM information_schema.columns WHERE table_name = 'fabrica_lote' AND column_name = 'pausado_por';" | lee || true)"
revisa "app_admin DELETE en fabrica_lote (0070)" t \
  "$(echo "SELECT has_table_privilege('app_admin', 'public.fabrica_lote', 'DELETE');" | lee || true)"

echo "== 3) La pantalla de productos responde en cada mercado"
for MERCADO in amazon_mx amazon_us; do
  revisa "HTML productos-sin-clics $MERCADO" 200 "$(http "/productos-sin-clics?plataforma=$MERCADO")"
done

echo "== 4) El crontab trae UNA linea impulso-vigia"
if [ "$SIM" = 1 ]; then
  revisa "lineas impulso-vigia" 1 \
    "$(grep -c impulso-vigia "${TMPDIR:-/tmp}/orbit-d4-sim/crontab-gon" 2>/dev/null || true)"
else
  revisa "lineas impulso-vigia" 1 \
    "$(ssh goncloud 'crontab -l -u gon 2>/dev/null' | grep -c impulso-vigia || true)"
fi

echo "== 5) Vista previa del impulso con un candidato del dashboard (no escribe)"
LOTES_ANTES="$(echo 'SELECT count(*) FROM fabrica_lote;' | lee || true)"
IMP_ANTES="$(echo 'SELECT count(*) FROM impulso;' | lee || true)"
DASH=$(cuerpo "/api/dashboard/productos-sin-clics?plataforma=amazon_mx" || true)
CAND=$(printf '%s' "$DASH" | python3 -c "
import json, sys
try:
    datos = json.load(sys.stdin)
except ValueError:
    raise SystemExit(1)
candidatos = datos.get('candidatos') or []
if not candidatos or not candidatos[0].get('listing_ids'):
    raise SystemExit(2)
lid = candidatos[0]['listing_ids'][0]
print(json.dumps({'plataforma': 'amazon_mx', 'listing_ids': [lid],
                  'subfamilia_por_listing': {str(lid): 'prueba_checklist'}}))
" 2>/dev/null || true)
if [ -z "$CAND" ]; then
  echo "NO MEDIDO vista previa del impulso (sin candidato con listings en MX)"; NOMEDIDO=$((NOMEDIDO + 1))
else
  revisa "POST impulso/plan" 200 "$(post /api/fabrica/impulso/plan "$CAND")"
  revisa "fabrica_lote sin escrituras de la vista previa" "$LOTES_ANTES" \
    "$(echo 'SELECT count(*) FROM fabrica_lote;' | lee || true)"
  revisa "impulso sin escrituras de la vista previa" "$IMP_ANTES" \
    "$(echo 'SELECT count(*) FROM impulso;' | lee || true)"
fi

echo "== 6) MUTATION_REQUEST_TYPES trae PUT /sp/productAds"
revisa "PUT /sp/productAds en allowlist" true "$(trae_put)"

echo "== 7) Las cinco tablas sin escrituras del checklist"
for TABLA in impulso impulso_lectura anuncio_retiro anuncio_reposicion anuncio_retiro_intencion; do
  eval "ANTES=\$ANTES_$TABLA"
  if [ -z "$ANTES" ]; then
    echo "NO MEDIDO $TABLA despues (no hubo conteo antes)"; NOMEDIDO=$((NOMEDIDO + 1))
  else
    revisa "$TABLA sin escrituras del checklist" "$ANTES" \
      "$(echo "SELECT count(*) FROM $TABLA;" | lee || true)"
  fi
done

echo "== RESULTADO: $FALLAS falla(s), $NOMEDIDO sin medir"
[ "$FALLAS" -eq 0 ] || exit 1
[ "$NOMEDIDO" = 0 ] || exit 4
