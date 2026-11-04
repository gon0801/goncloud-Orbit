#!/usr/bin/env bash
# Checklist post-deploy de D.4 (REPRICING 02 seccion 5): SOLO LECTURA.
# Corre UNA vez despues de desplegar.sh y deja su salida
# en un txt con su linea exit= al final. Patron: ../D.3/checklist.sh.
# 0 = todo comprobado; 1 = alguna FALLA medida fuera de la seccion 6;
# 3 = las unicas FALLAS medidas son las de la seccion 6 (dia del
# despliegue: sin muestras ni escenarios meli de hoy hasta la primera
# corrida del job de muestras y de la estimacion); 4 = no pudo medir
# (el servidor no contesta o una lectura salio vacia, sin ninguna
# FALLA medida). El 1 y el 3 mandan sobre NOMEDIDO (lo no medido va
# en el txt).
# En simulacion (ORBIT_SIMULACION=1) la base es ORBIT_SIM_DSN y el HTTP
# es ORBIT_SIM_API: no hay ssh.
# Comprueba: /health en 200; la senal de la 0073; la politica meli
# vigente; `meli/meli` SIN entrada de modo (D.4 no enciende); /precios
# en 200 con los modos, el bloque de seguridad y el bloque de goals;
# el crontab CON las 4 de D.4 (catalogo 11:30, muestras 12:25, precios
# 13:10 + repasos con lock propio) y con las 4 de D.3 intactas;
# resumen_diario sellado; muestras meli y escenarios `disponible`
# meli de hoy; cobertura meli (la seccion 7 compara las activas del
# catalogo contra las activas vistas por la corrida meli_catalogo de
# hoy: DoD de D.4 medido por el checklist, sin pedir nada a nadie).
# Uso: bash docs/evidencia/repricing-02/ejecucion/D.4/checklist.sh <STAMP> > checklist-<sello>.txt 2>&1; echo "exit=$?" >> checklist-<sello>.txt
set -uo pipefail

SELLO=${1:-sin-sello}
REPO=$(git rev-parse --show-toplevel)
SIM=${ORBIT_SIMULACION:-0}
DSN_SIM=${ORBIT_SIM_DSN:-postgresql://orbit:orbit@localhost:5432/orbit_sim_D4}
API_SIM=${ORBIT_SIM_API:-http://127.0.0.1:8015}
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
cobertura() {  # cobertura <platform> -> imprime la salida; rc 0 cuadra, 1 no cuadra, 2 sin medir
  if [ "$SIM" = 1 ]; then
    PY="$REPO/.venv/bin/python"
    [ -x "$PY" ] || PY=python3
    PYTHONPATH="$REPO" ORBIT_DSN_READ="$DSN_SIM" "$PY" tools/precio_cobertura.py --platform "$1" 2>&1
  else
    ssh goncloud "docker exec orbit-app-1 python tools/precio_cobertura.py --platform $1" 2>&1
  fi
}

echo "== Checklist D.4 (solo lectura; sello $SELLO)"

echo "== 1) HTTP: /health"
revisa "GET /health" 200 "$(http /health)"

echo "== 2) Esquema: senal de la 0073, politica meli y meli/meli sin entrada"
revisa "0073 (rama meli en la funcion)" 1 \
  "$(echo "SELECT count(*) FROM pg_proc WHERE proname = 'estimacion_escenario_referencias_coherentes' AND pg_get_functiondef(oid) LIKE '%disponible meli exige miembro%';" | lee || true)"
revisa "1 politica meli vigente" 1 \
  "$(echo "SELECT count(*) FROM estimacion_politica_version WHERE label = 'repricing02_meli' AND valid_to IS NULL;" | lee || true)"
MODO_MELI="$(echo "SELECT settings -> 'precio_modo_universo' ->> 'meli/meli' FROM config_version ORDER BY id DESC LIMIT 1;" | lee || true)"
if [ -z "$MODO_MELI" ]; then
  echo "OK    meli/meli sin entrada de modo (D.4 no enciende)"
else
  echo "FALLA meli/meli con entrada '$MODO_MELI' (D.4 no enciende)"; FALLAS=$((FALLAS + 1))
fi
echo "INFO precio_modo_global: $(echo "SELECT settings ->> 'precio_modo_global' FROM config_version ORDER BY id DESC LIMIT 1;" | lee || true)"

echo "== 3) GET /precios en 200 con modos, seguridad y goals"
revisa "GET /precios" 200 "$(http /precios)"
HTML="$(cuerpo /precios)"
if [ -z "$HTML" ]; then
  echo "NO MEDIDO cuerpo de /precios (lectura vacia)"; NOMEDIDO=$((NOMEDIDO + 1))
else
  for MARCA in "Modo global:" "bloque-seguridad-amazon_mx" "bloque-goals-amazon_mx"; do
    if grep -qF -- "$MARCA" <<<"$HTML"; then
      echo "OK    /precios trae $MARCA"
    else
      echo "FALLA /precios sin marca $MARCA"; FALLAS=$((FALLAS + 1))
    fi
  done
fi

echo "== 4) El crontab: con las 4 de D.4 y las 4 de D.3 intactas"
if [ "$SIM" = 1 ]; then
  CRON_F="${TMPDIR:-/tmp}/orbit-d4-sim/crontab-gon"
  revisa "lineas precios meli (precio-meli.lock)" 2 \
    "$(grep -c 'precio-meli\.lock.*--platform meli' "$CRON_F" 2>/dev/null || true)"
  revisa "linea muestras meli" 1 \
    "$(grep -c 'envio_muestras.*--platform meli' "$CRON_F" 2>/dev/null || true)"
  revisa "linea catalogo meli" 1 \
    "$(grep -c 'app\.meli\.catalogo' "$CRON_F" 2>/dev/null || true)"
  revisa "lineas precios D.3 intactas (precio-spapi.lock)" 2 \
    "$(grep -c 'precio-spapi\.lock' "$CRON_F" 2>/dev/null || true)"
  revisa "lineas muestras D.3 intactas (amazon)" 2 \
    "$(grep 'envio_muestras' "$CRON_F" 2>/dev/null | grep -c 'amazon_' || true)"
else
  CRON_ACTUAL="$(ssh goncloud 'crontab -l -u gon 2>/dev/null' || true)"
  revisa "lineas precios meli (precio-meli.lock)" 2 \
    "$(printf '%s' "$CRON_ACTUAL" | grep -c 'precio-meli\.lock.*--platform meli' || true)"
  revisa "linea muestras meli" 1 \
    "$(printf '%s' "$CRON_ACTUAL" | grep -c 'envio_muestras.*--platform meli' || true)"
  revisa "linea catalogo meli" 1 \
    "$(printf '%s' "$CRON_ACTUAL" | grep -c 'app\.meli\.catalogo' || true)"
  revisa "lineas precios D.3 intactas (precio-spapi.lock)" 2 \
    "$(printf '%s' "$CRON_ACTUAL" | grep -c 'precio-spapi\.lock' || true)"
  revisa "lineas muestras D.3 intactas (amazon)" 2 \
    "$(printf '%s' "$CRON_ACTUAL" | grep 'envio_muestras' | grep -c 'amazon_' || true)"
fi

echo "== 5) TIPOS_PRECIO_SELLADOS trae resumen_diario (S.6)"
revisa "resumen_diario sellado" true "$(trae_resumen)"

echo "== 6) Muestras y escenarios meli de hoy (= 2 FALLAS hasta la primera corrida)"
FALLAS_PRE6=$FALLAS
revisa "muestras meli calculadas hoy" t \
  "$(echo "SELECT count(*) >= 1 FROM precio_envio_muestra WHERE platform = 'meli' AND calculada_at::date = CURRENT_DATE;" | lee || true)"
revisa "escenarios disponible meli de hoy" t \
  "$(echo "SELECT count(*) >= 1 FROM estimacion_escenario WHERE platform = 'meli' AND estado = 'disponible' AND observed_at::date = CURRENT_DATE;" | lee || true)"
FALLAS_6=$((FALLAS - FALLAS_PRE6))

echo "== 7) Cobertura meli (API de hoy vs catalogo; DoD de D.4)"
COB_TXT="$(cobertura meli)"; COB_RC=$?
printf '%s\n' "$COB_TXT" | sed 's/^/  /'
case "$COB_RC" in
  0) echo "OK    cobertura meli cuadra (API de hoy vs catalogo)" ;;
  2) echo "NO MEDIDO cobertura meli (sin corrida meli_catalogo de hoy)"; NOMEDIDO=$((NOMEDIDO + 1)) ;;
  *) echo "FALLA cobertura meli (rc=$COB_RC)"; FALLAS=$((FALLAS + 1)) ;;
esac

echo "== RESULTADO: $FALLAS falla(s), $NOMEDIDO sin medir"
if [ "$FALLAS" -eq 0 ]; then
  [ "$NOMEDIDO" = 0 ] || exit 4
elif [ "$FALLAS" -eq "$FALLAS_6" ]; then
  exit 3
else
  exit 1
fi
