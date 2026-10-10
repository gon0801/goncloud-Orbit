#!/usr/bin/env bash
# X.1 paso 5 (reversa lista sin correr): quita la clave de politica con
# POST /api/ads-optimizer/settings/<plataforma> y cuerpo
# {"base_config_version_id": <id>, "motor_bid": "apagado"}. Con la clave
# ausente el motor no mueve bids (el codigo anterior falla cerrado con
# niveles_v3). Un 422 con "edicion vacia" significa que la clave ya estaba
# ausente (exito idempotente). Verifica created:true y "-> ausente".
# ATENCION - LA INVOCACION PELADA ESCRIBE: rollback.sh de D.1 la llama como
# `bash apagar.sh`, SIN argumentos y SIN bandera, y despues exige las
# claves en f|f. Por esa compatibilidad obligatoria, SIN ARGUMENTOS este
# script APAGA LAS DOS PLATAFORMAS DE VERDAD (en real: prod; en sim: la
# base sim). No tiene simulacro pelado. Los humanos simulan con plataforma:
# `apagar.sh <plataforma>` simula esa (exit 0, nada escrito) y solo
# --acepto-mutacion-real escribe. Otro uso sale 2. El token jamas se
# imprime: en modo real vive dentro del ssh, en simulacion viaja solo en
# el header del curl.
# Modo simulacion (ORBIT_SIMULACION=1): el POST va a ORBIT_SIM_API (app
# local apuntada a ORBIT_SIM_DSN) con el token de ORBIT_SIM_TOKEN.
# Uso: bash docs/evidencia/bids-02/ejecucion/X.1/apagar.sh [--acepto-mutacion-real] [amazon_mx|amazon_us]
set -euo pipefail

MUTAR=0
if [ "${1:-}" = "--acepto-mutacion-real" ]; then MUTAR=1; shift; fi
PLATS=""
if [ $# -eq 0 ]; then
  # Camino de rollback.sh de D.1: ESCRIBE las dos aunque no venga la bandera.
  PLATS="amazon_mx amazon_us"
  MUTAR=1
  echo "== SIN ARGUMENTOS: apaga AMBAS plataformas DE VERDAD (camino de rollback.sh)"
elif [ $# -eq 1 ]; then
  [ "$1" = amazon_mx ] || [ "$1" = amazon_us ] || { echo "uso: apagar.sh [--acepto-mutacion-real] [amazon_mx|amazon_us]"; exit 2; }
  PLATS=$1
else
  echo "uso: apagar.sh [--acepto-mutacion-real] [amazon_mx|amazon_us]"; exit 2
fi
REPO=$(git rev-parse --show-toplevel)
SIM=${ORBIT_SIMULACION:-0}
API_SIM=${ORBIT_SIM_API:-http://127.0.0.1:8011}
DSN_SIM=${ORBIT_SIM_DSN:-postgresql://orbit:orbit@127.0.0.1:5433/orbit_sim_d1}
cd "$REPO"

CUERPO() { printf '{"base_config_version_id": %s, "motor_bid": "apagado"}' "$1"; }

lee_id() {
  if [ "$SIM" = 1 ]; then
    psql "$DSN_SIM" -X -q -tA -v ON_ERROR_STOP=1 -c "SELECT id FROM config_version ORDER BY id DESC LIMIT 1"
  else
    ssh goncloud 'DSN=$(docker exec orbit-app-1 printenv ORBIT_DSN_READ); docker exec -i orbit-db-1 psql "$DSN" -X -q -tA -v ON_ERROR_STOP=1 -c "SELECT id FROM config_version ORDER BY id DESC LIMIT 1"'
  fi
}

# post <plataforma> <id> imprime "<codigo-http>\n<cuerpo>" en stdout.
post() {
  if [ "$SIM" = 1 ]; then
    [ -n "${ORBIT_SIM_TOKEN:-}" ] || { echo "ABORTA: ORBIT_SIM_TOKEN vacio (la simulacion real necesita un token local)"; exit 1; }
    curl -sS -w '\n%{http_code}' -X POST "$API_SIM/api/ads-optimizer/settings/$1" \
      -H "Content-Type: application/json" -H "x-orbit-token: $ORBIT_SIM_TOKEN" -d "$(CUERPO "$2")"
  else
    ssh goncloud "curl -sS -w '\n%{http_code}' -X POST http://127.0.0.1:8010/api/ads-optimizer/settings/$1 \
      -H \"Content-Type: application/json\" \
      -H \"x-orbit-token: \$(cat /mnt/data/appdata/orbit/secrets/api_write_token)\" \
      -d '$(CUERPO "$2")'"
  fi
}

apaga_una() {  # apaga_una <plataforma>: escribe de verdad esa plataforma.
  PLAT=$1
  echo "== apagado real de $PLAT (motor_bid apagado)"
  ID=$(lee_id) || { echo "ABORTA: no se pudo leer base_config_version_id"; exit 1; }
  for intento in 1 2; do
    RESP=$(post "$PLAT" "$ID")
    CODIGO=$(printf '%s\n' "$RESP" | tail -1)
    CUERPO_RESP=$(printf '%s\n' "$RESP" | sed '$d')
    echo "intento $intento con id=$ID -> HTTP $CODIGO"
    case "$CODIGO" in
      200) break ;;
      409)
        [ "$intento" = 2 ] && { echo "ABORTA: 409 dos veces seguidas: $CUERPO_RESP"; exit 1; }
        echo "409: otro guardo antes; releo el id y repito una vez"
        ID=$(lee_id) || { echo "ABORTA: no se pudo releer base_config_version_id"; exit 1; }
        continue ;;
      422)
        case "$CUERPO_RESP" in
          *edicion\ vacia*) echo "OK: edicion vacia, $PLAT ya tenia la clave ausente (idempotente)"; return 0 ;;
        esac
        echo "ABORTA: 422 no idempotente: $CUERPO_RESP"; exit 1 ;;
      *) echo "ABORTA: HTTP $CODIGO: $CUERPO_RESP"; exit 1 ;;
    esac
  done
  case "$CUERPO_RESP" in
    *'"created":true'*motor\ bid*|*'"created": true'*motor\ bid*) : ;;
    *) echo "ABORTA: respuesta sin created:true: $CUERPO_RESP"; exit 1 ;;
  esac
  case "$CUERPO_RESP" in
    *'-> ausente'*) : ;;
    *) echo "ABORTA: el label no trae '-> ausente': $CUERPO_RESP"; exit 1 ;;
  esac
  echo "OK: $CUERPO_RESP"
}

if [ "$MUTAR" = 0 ]; then
  echo "== SIMULACRO: sin --acepto-mutacion-real no se escribe nada"
  for PLAT in $PLATS; do
    if [ "$SIM" = 1 ]; then
      echo "POST $API_SIM/api/ads-optimizer/settings/$PLAT"
      echo "header x-orbit-token: <token de ORBIT_SIM_TOKEN, no se imprime>"
    else
      echo "ssh goncloud 'curl -sS -X POST http://127.0.0.1:8010/api/ads-optimizer/settings/$PLAT \\"
      echo "-H \"Content-Type: application/json\" \\"
      echo "-H \"x-orbit-token: \$(cat /mnt/data/appdata/orbit/secrets/api_write_token)\" \\"
      echo "-d \"{\\\"base_config_version_id\\\": <id>, \\\"motor_bid\\\": \\\"apagado\\\"}\"'"
    fi
    ID=$(lee_id) || { echo "ABORTA: no se pudo leer base_config_version_id"; exit 1; }
    [ -n "$ID" ] || { echo "ABORTA: config_version sin filas"; exit 1; }
    echo "base_config_version_id seria: $ID"
    echo "cuerpo seria: $(CUERPO "$ID")"
  done
  echo "(el token no sale del servidor; en sim solo viaja en el header)"
  echo "SIMULACRO OK (nada escrito)"
  exit 0
fi

for PLAT in $PLATS; do apaga_una "$PLAT"; done
