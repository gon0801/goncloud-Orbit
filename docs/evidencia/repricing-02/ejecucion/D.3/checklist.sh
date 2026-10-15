#!/usr/bin/env bash
# Checklist post-deploy de D.3 (REPRICING 02 seccion 4): SOLO LECTURA.
# Corre UNA vez despues de desplegar.sh + politicas.sh y deja su salida
# en un txt con su linea exit= al final. Patron: ../D.2/checklist.sh.
# 0 = todo comprobado; 1 = alguna FALLA medida fuera de la seccion 6;
# 3 = las unicas FALLAS medidas son las de la seccion 6 (dia del
# despliegue: sin muestras ni escenarios de hoy hasta la primera
# corrida del job de muestras y de la estimacion); 4 = no pudo medir
# (el servidor no contesta o una lectura salio vacia, sin ninguna
# FALLA medida). El 1 y el 3 mandan sobre NOMEDIDO (lo no medido va
# en el txt).
# En simulacion (ORBIT_SIMULACION=1) la base es ORBIT_SIM_DSN y el HTTP
# es ORBIT_SIM_API: no hay ssh.
# Comprueba: /health en 200; los 2 CHECK de la 0055; las 2 politicas FBM
# vigentes; modos en shadow (D.3 no enciende); /precios en 200 con los
# modos, el bloque de seguridad y el bloque de goals; el crontab SIN las
# 2 viejas de D.2 y CON las 4 de D.3 (2 de precios MX+US + 2 de
# muestras); resumen_diario sellado; muestras MX y US calculadas hoy;
# escenarios `disponible` FBM de MX y US observados hoy; cobertura de MX
# y de US que cuadra (exit 0 de tools/precio_cobertura.py).
# Uso: bash docs/evidencia/repricing-02/ejecucion/D.3/checklist.sh <STAMP> > checklist-<sello>.txt 2>&1; echo "exit=$?" >> checklist-<sello>.txt
set -uo pipefail

SELLO=${1:-sin-sello}
REPO=$(git rev-parse --show-toplevel)
SIM=${ORBIT_SIMULACION:-0}
DSN_SIM=${ORBIT_SIM_DSN:-postgresql://orbit:orbit@localhost:5432/orbit_sim_D3}
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
cobertura() {  # cobertura <platform> -> exit del tool (0 = cuadra)
  if [ "$SIM" = 1 ]; then
    PY="$REPO/.venv/bin/python"
    [ -x "$PY" ] || PY=python3
    PYTHONPATH="$REPO" ORBIT_DSN_READ="$DSN_SIM" "$PY" tools/precio_cobertura.py --platform "$1" >/dev/null 2>&1
    echo $?
  else
    ssh goncloud "docker exec orbit-app-1 python tools/precio_cobertura.py --platform $1" >/dev/null 2>&1
    echo $?
  fi
}

echo "== Checklist D.3 (solo lectura; sello $SELLO)"

echo "== 1) HTTP: /health"
revisa "GET /health" 200 "$(http /health)"

echo "== 2) Esquema: 2 CHECK de la 0055, 2 politicas FBM y modos en shadow"
revisa "2 CHECK de la 0055 en escenario" 2 \
  "$(echo "SELECT count(*) FROM pg_constraint WHERE conname IN ('estimacion_escenario_disponible_exige_origen', 'estimacion_escenario_disponible_exige_fees_partidos');" | lee || true)"
revisa "2 politicas FBM vigentes" 2 \
  "$(echo "SELECT count(*) FROM estimacion_politica_version WHERE label IN ('repricing02_fbm_mx', 'repricing02_fbm_us') AND valid_to IS NULL;" | lee || true)"
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

echo "== 4) El crontab: sin las 2 viejas de D.2, con las 4 de D.3"
if [ "$SIM" = 1 ]; then
  CRON_F="${TMPDIR:-/tmp}/orbit-d3-sim/crontab-gon"
  revisa "viejas D.2 (precio MX sin US)" 0 \
    "$(grep 'precio-spapi\.lock' "$CRON_F" 2>/dev/null | grep -vc 'amazon_us' || true)"
  revisa "lineas precios MX+US (precio-spapi.lock)" 2 \
    "$(grep -c 'precio-spapi\.lock.*amazon_us' "$CRON_F" 2>/dev/null || true)"
  revisa "lineas muestras (envio_muestras)" 2 \
    "$(grep -c 'envio_muestras' "$CRON_F" 2>/dev/null || true)"
else
  CRON_ACTUAL="$(ssh goncloud 'crontab -l -u gon 2>/dev/null' || true)"
  revisa "viejas D.2 (precio MX sin US)" 0 \
    "$(printf '%s' "$CRON_ACTUAL" | grep 'precio-spapi\.lock' | grep -vc 'amazon_us' || true)"
  revisa "lineas precios MX+US (precio-spapi.lock)" 2 \
    "$(printf '%s' "$CRON_ACTUAL" | grep -c 'precio-spapi\.lock.*amazon_us' || true)"
  revisa "lineas muestras (envio_muestras)" 2 \
    "$(printf '%s' "$CRON_ACTUAL" | grep -c 'envio_muestras' || true)"
fi

echo "== 5) TIPOS_PRECIO_SELLADOS trae resumen_diario (S.6)"
revisa "resumen_diario sellado" true "$(trae_resumen)"

echo "== 6) Muestras y escenarios de hoy (= 3 FALLAS hasta la primera corrida)"
FALLAS_PRE6=$FALLAS
revisa "muestras MX calculadas hoy" t \
  "$(echo "SELECT count(*) >= 1 FROM precio_envio_muestra WHERE platform = 'amazon_mx' AND calculada_at::date = CURRENT_DATE;" | lee || true)"
revisa "muestras US calculadas hoy" t \
  "$(echo "SELECT count(*) >= 1 FROM precio_envio_muestra WHERE platform = 'amazon_us' AND calculada_at::date = CURRENT_DATE;" | lee || true)"
revisa "plataformas FBM con escenario disponible de hoy" 2 \
  "$(echo "SELECT count(DISTINCT platform) FROM estimacion_escenario WHERE canal = 'fbm' AND estado = 'disponible' AND observed_at::date = CURRENT_DATE;" | lee || true)"
FALLAS_6=$((FALLAS - FALLAS_PRE6))

echo "== 7) Cobertura de MX y US cuadra"
revisa "cobertura amazon_mx (exit)" 0 "$(cobertura amazon_mx)"
revisa "cobertura amazon_us (exit)" 0 "$(cobertura amazon_us)"

echo "== RESULTADO: $FALLAS falla(s), $NOMEDIDO sin medir"
if [ "$FALLAS" -eq 0 ]; then
  [ "$NOMEDIDO" = 0 ] || exit 4
elif [ "$FALLAS" -eq "$FALLAS_6" ]; then
  exit 3
else
  exit 1
fi
