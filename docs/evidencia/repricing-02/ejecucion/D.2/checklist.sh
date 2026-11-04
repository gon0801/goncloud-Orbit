#!/usr/bin/env bash
# Checklist post-deploy de D.2 (REPRICING 02 seccion 3): SOLO LECTURA.
# Corre UNA vez despues de desplegar.sh y deja su salida en un txt con su
# linea exit= al final. Patron:
# docs/evidencia/repricing-02/ejecucion/D.1/checklist.sh. 0 = todo
# comprobado; 1 = alguna FALLA medida; 4 = no pudo medir (el servidor no
# contesta o una lectura salio vacia, sin ninguna FALLA medida). Una FALLA
# medida sale con 1 aunque otra lectura quede vacia.
# En simulacion (ORBIT_SIMULACION=1) la base es ORBIT_SIM_DSN y el HTTP es
# ORBIT_SIM_API: no hay ssh.
# Comprueba: /health en 200; los 2 CHECK de la 0058; el trigger reparado
# de la 0059; precio_modo_global en shadow (D.2 no enciende) y
# amazon_mx/fba en shadow en precio_modo_universo; /precios en 200 con
# los modos, el bloque de seguridad y el bloque de goals; el crontab SIN
# la linea vieja y CON las 2 de precio-spapi.lock; y resumen_diario
# sellado en notifica.py (S.6). Que "el resumen diario llega" se verifica
# al dia siguiente (ver checklist-antes-del-deploy.txt): este checklist
# solo deja cableado el aviso.
# Uso: bash docs/evidencia/repricing-02/ejecucion/D.2/checklist.sh <STAMP> > checklist-<sello>.txt 2>&1; echo "exit=$?" >> checklist-<sello>.txt
set -uo pipefail

SELLO=${1:-sin-sello}
REPO=$(git rev-parse --show-toplevel)
SIM=${ORBIT_SIMULACION:-0}
DSN_SIM=${ORBIT_SIM_DSN:-postgresql://orbit:orbit@localhost:5432/orbit_sim_D2}
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
trae_resumen() {  # true si TIPOS_PRECIO_SELLADOS trae resumen_diario
  if [ "$SIM" = 1 ]; then
    PY="$REPO/.venv/bin/python"
    [ -x "$PY" ] || PY=python3
    (cd "$REPO" && "$PY" -c 'from app.notifica import TIPOS_PRECIO_SELLADOS; print(str("resumen_diario" in TIPOS_PRECIO_SELLADOS).lower())') || true
  else
    ssh goncloud 'docker exec orbit-app-1 python -c '\''from app.notifica import TIPOS_PRECIO_SELLADOS; print(str("resumen_diario" in TIPOS_PRECIO_SELLADOS).lower())'\''' || true
  fi
}

echo "== Checklist D.2 (solo lectura; sello $SELLO)"

echo "== 1) HTTP: /health"
revisa "GET /health" 200 "$(http /health)"

echo "== 2) Esquema: CHECK de la 0058, trigger reparado y modos en shadow"
revisa "2 CHECK de la 0058 en precio_decision" 2 \
  "$(echo "SELECT count(*) FROM pg_constraint WHERE conname IN ('precio_decision_l_exige_origen', 'precio_decision_mover_exige_verificacion');" | lee || true)"
revisa "trigger reparado (0059)" t \
  "$(echo "SELECT pg_get_functiondef('precio_retencion_coherente()'::regprocedure) LIKE '%v_corr_fecha%';" | lee || true)"
revisa "precio_modo_global en config vigente" shadow \
  "$(echo "SELECT settings ->> 'precio_modo_global' FROM config_version ORDER BY id DESC LIMIT 1;" | lee || true)"
revisa "amazon_mx/fba en shadow" shadow \
  "$(echo "SELECT settings -> 'precio_modo_universo' ->> 'amazon_mx/fba' FROM config_version ORDER BY id DESC LIMIT 1;" | lee || true)"

echo "== 3) GET /precios en 200 con modos, seguridad y goals"
revisa "GET /precios" 200 "$(http /precios)"
HTML="$(cuerpo /precios)"
if [ -z "$HTML" ]; then
  echo "NO MEDIDO cuerpo de /precios (lectura vacia)"; NOMEDIDO=$((NOMEDIDO + 1))
else
  for MARCA in "Modo global:" "bloque-seguridad-amazon_mx" "bloque-goals-amazon_mx"; do
    if printf '%s' "$HTML" | grep -qF -- "$MARCA"; then
      echo "OK    /precios trae $MARCA"
    else
      echo "FALLA /precios sin marca $MARCA"; FALLAS=$((FALLAS + 1))
    fi
  done
fi

echo "== 4) El crontab: sin la vieja, con las 2 de D.2"
if [ "$SIM" = 1 ]; then
  revisa "linea vieja (precio-corrida.lock)" 0 \
    "$(grep -c 'precio-corrida\.lock' "${TMPDIR:-/tmp}/orbit-d2-sim/crontab-gon" 2>/dev/null || true)"
  revisa "lineas D.2 (precio-spapi.lock)" 2 \
    "$(grep -c 'precio-spapi\.lock' "${TMPDIR:-/tmp}/orbit-d2-sim/crontab-gon" 2>/dev/null || true)"
else
  revisa "linea vieja (precio-corrida.lock)" 0 \
    "$(ssh goncloud 'crontab -l -u gon 2>/dev/null' | grep -c 'precio-corrida\.lock' || true)"
  revisa "lineas D.2 (precio-spapi.lock)" 2 \
    "$(ssh goncloud 'crontab -l -u gon 2>/dev/null' | grep -c 'precio-spapi\.lock' || true)"
fi

echo "== 5) TIPOS_PRECIO_SELLADOS trae resumen_diario (S.6)"
revisa "resumen_diario sellado" true "$(trae_resumen)"

echo "== RESULTADO: $FALLAS falla(s), $NOMEDIDO sin medir"
[ "$FALLAS" -eq 0 ] || exit 1
[ "$NOMEDIDO" = 0 ] || exit 4
