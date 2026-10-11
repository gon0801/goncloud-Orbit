#!/usr/bin/env bash
# Checklist post-deploy de D.3 (BIDS 02 seccion 4): SOLO LECTURA. Corre UNA
# vez despues de desplegar.sh y deja su salida en un txt.
# Patron de salidas del de S.3: 0 = todo comprobado; 1 = alguna FALLA
# medida; 4 = no pudo medir (el servidor no contesta o una lectura salio
# vacia, sin ninguna FALLA medida). Una FALLA medida sale con 1 aunque otra
# lectura quede vacia. D.3 no tiene gates temporales: la vista previa y los
# botones se verifican contra lo que ya dejo D.2 (config vigente y campanas
# ENABLED). En simulacion (ORBIT_SIMULACION=1) la base es ORBIT_SIM_DSN y el
# HTTP es ORBIT_SIM_API: no hay ssh.
# Comprueba (guia D.3): /health en 200; campana_ajuste existe y esta vacia;
# la vista previa de un ajuste (clase de CLASES_SELLADAS, campana ENABLED)
# responde 200 y deja el conteo en 0; /donde-poner-el-dinero en 200 en MX y
# US con un boton data-ajuste-clase por cada clase sellada; imprime si
# MUTATION_REQUEST_TYPES trae PUT /sp/campaigns.
# Uso: bash docs/evidencia/bids-02/ejecucion/D.3/checklist.sh <STAMP> > checklist-salida.txt; echo "exit=$?"
set -uo pipefail

SELLO=${1:-sin-sello}
REPO=$(git rev-parse --show-toplevel)
DIR=docs/evidencia/bids-02/ejecucion/D.3
SIM=${ORBIT_SIMULACION:-0}
DSN_SIM=${ORBIT_SIM_DSN:-postgresql://orbit:orbit@127.0.0.1:5433/orbit_sim_d3}
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
trae_put() {  # true si MUTATION_REQUEST_TYPES trae PUT /sp/campaigns
  if [ "$SIM" = 1 ]; then
    PY="$REPO/.venv/bin/python"
    [ -x "$PY" ] || PY=python3
    (cd "$REPO" && "$PY" -c 'from app.ads.write import MUTATION_REQUEST_TYPES; print(str(("PUT", "/sp/campaigns") in MUTATION_REQUEST_TYPES).lower())') || true
  else
    ssh goncloud 'docker exec orbit-app-1 python -c '\''from app.ads.write import MUTATION_REQUEST_TYPES; print(str(("PUT", "/sp/campaigns") in MUTATION_REQUEST_TYPES).lower())'\''' || true
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

echo "== Checklist D.3 (solo lectura; sello $SELLO)"

echo "== 1) HTTP: /health"
revisa "GET /health" 200 "$(http /health)"

echo "== 2) Esquema de D.3: campana_ajuste existe y esta vacia"
revisa "tabla campana_ajuste" t \
  "$(echo "SELECT to_regclass('public.campana_ajuste') IS NOT NULL;" | lee || true)"
revisa "campana_ajuste vacia antes" 0 \
  "$(echo 'SELECT count(*) FROM campana_ajuste;' | lee || true)"

echo "== 3) Vista previa de un ajuste sellado sobre una campana ENABLED (no escribe)"
CAMP=$(echo "SELECT c.id FROM ad_entity c JOIN ad_entity_state s ON s.ad_entity_id = c.id AND s.status = 'ENABLED' JOIN v_campana_config_vigente v ON v.ad_entity_id = c.id WHERE c.kind = 'campaign' ORDER BY c.id LIMIT 1;" | lee || true)
if [ -z "$CAMP" ]; then
  echo "NO MEDIDO vista previa (sin campana ENABLED con config vigente)"; NOMEDIDO=$((NOMEDIDO + 1))
else
  echo "campana ENABLED con vigente: id interno $CAMP (el external_id no se imprime)"
  MONTO=$(echo "SELECT coalesce(presupuesto_diario, 99) + 1 FROM v_campana_config_vigente WHERE ad_entity_id = $CAMP;" | lee || true)
  if [ -z "$MONTO" ]; then
    echo "NO MEDIDO vista previa (presupuesto vigente vacio)"; NOMEDIDO=$((NOMEDIDO + 1))
  else
    revisa "GET campana-ajuste/plan" 200 \
      "$(http "/api/ads-optimizer/campana-ajuste/plan?campana_id=$CAMP&clase=presupuesto&presupuesto=$MONTO")"
    revisa "campana_ajuste vacia despues" 0 \
      "$(echo 'SELECT count(*) FROM campana_ajuste;' | lee || true)"
  fi
fi

echo "== 4) La pantalla de dinero trae un boton por clase sellada en cada mercado"
for MERCADO in amazon_mx amazon_us; do
  revisa "HTML donde-poner-el-dinero $MERCADO" 200 "$(http "/donde-poner-el-dinero?plataforma=$MERCADO")"
  HAY=$(echo "SELECT count(*) FROM ad_entity c JOIN ad_entity_state s ON s.ad_entity_id = c.id AND s.status = 'ENABLED' WHERE c.platform = '$MERCADO' AND c.kind = 'campaign';" | lee || true)
  if [ "$HAY" = 0 ]; then
    echo "NO MEDIDO botones $MERCADO (sin campanas ENABLED: no hay filas que los traigan)"; NOMEDIDO=$((NOMEDIDO + 1))
    continue
  fi
  HTML=$(cuerpo "/donde-poner-el-dinero?plataforma=$MERCADO" || true)
  if [ -z "$HTML" ]; then
    echo "NO MEDIDO botones $MERCADO (respuesta vacia)"; NOMEDIDO=$((NOMEDIDO + 1))
  else
    for CLASE in presupuesto ajuste_ubicacion fuera_de_amazon; do
      positivo "botones $CLASE $MERCADO" "$(printf '%s' "$HTML" | grep -c "data-ajuste-clase=\"$CLASE\"" || true)"
    done
  fi
done

echo "== 5) MUTATION_REQUEST_TYPES trae PUT /sp/campaigns"
revisa "PUT /sp/campaigns en allowlist" true "$(trae_put)"

echo "== RESULTADO: $FALLAS falla(s), $NOMEDIDO sin medir"
[ "$FALLAS" -eq 0 ] || exit 1
[ "$NOMEDIDO" = 0 ] || exit 4
