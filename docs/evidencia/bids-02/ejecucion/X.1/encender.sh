#!/usr/bin/env bash
# X.1 paso 5: enciende el motor en UNA plataforma con
# POST /api/ads-optimizer/settings/<plataforma> y cuerpo
# {"base_config_version_id": <id>, "motor_bid": "niveles_v3"} (el literal
# del paso). Corre una vez por plataforma. No toca goals ni
# ads_optimizer_mode: el cuerpo solo trae base_config_version_id y motor_bid.
# INTERLOCK: aborta ANTES de nada (tambien en simulacro) si
# ejecucion/X.1/rejuego-<plataforma>.txt no existe o su ultima linea no es
# "cumple: true". Una plataforma con cumple:false no se enciende: el
# operador escribe no-encendida-<plataforma>.md (paso 2) y sigue la otra.
# SIMULA POR OMISION: sin --acepto-mutacion-real solo imprime la llamada
# que haria y sale 0 sin mutar nada. Con la bandera, manda el POST: un 409
# relee el id y repite una vez; un 422 con "edicion vacia" significa que la
# clave ya era niveles_v3 (exito idempotente). Verifica created:true y el
# label "motor bid ... -> niveles_v3". El token jamas se imprime: en modo
# real vive dentro del ssh, en simulacion viaja solo en el header del curl.
# Modo simulacion (ORBIT_SIMULACION=1): el POST va a ORBIT_SIM_API (app
# local apuntada a ORBIT_SIM_DSN) con el token de ORBIT_SIM_TOKEN.
# Uso: bash docs/evidencia/bids-02/ejecucion/X.1/encender.sh [--acepto-mutacion-real] <amazon_mx|amazon_us>
set -euo pipefail

MUTAR=0
if [ "${1:-}" = "--acepto-mutacion-real" ]; then MUTAR=1; shift; fi
[ $# -eq 1 ] || { echo "uso: encender.sh [--acepto-mutacion-real] <amazon_mx|amazon_us>"; exit 2; }
PLAT=$1
[ "$PLAT" = amazon_mx ] || [ "$PLAT" = amazon_us ] || { echo "uso: encender.sh [--acepto-mutacion-real] <amazon_mx|amazon_us>"; exit 2; }
REPO=$(git rev-parse --show-toplevel)
DIR=docs/evidencia/bids-02/ejecucion/X.1
SIM=${ORBIT_SIMULACION:-0}
API_SIM=${ORBIT_SIM_API:-http://127.0.0.1:8011}
DSN_SIM=${ORBIT_SIM_DSN:-postgresql://orbit:orbit@127.0.0.1:5433/orbit_sim_d1}
TXT=$DIR/rejuego-$PLAT.txt
cd "$REPO"

[ -f "$TXT" ] || { echo "ABORTA: no existe $TXT (corre rejuego.sh para $PLAT primero)"; exit 1; }
[ "$(tail -n 1 "$TXT")" = "cumple: true" ] || { echo "ABORTA: $TXT no termina en 'cumple: true' (ultima: $(tail -n 1 "$TXT")); escribe no-encendida-$PLAT.md y no enciendas $PLAT"; exit 1; }
echo "interlock OK: $TXT termina en cumple: true"

CUERPO() { printf '{"base_config_version_id": %s, "motor_bid": "niveles_v3"}' "$1"; }

lee_id() {
  if [ "$SIM" = 1 ]; then
    psql "$DSN_SIM" -X -q -tA -v ON_ERROR_STOP=1 -c "SELECT id FROM config_version ORDER BY id DESC LIMIT 1"
  else
    ssh goncloud 'DSN=$(docker exec orbit-app-1 printenv ORBIT_DSN_READ); docker exec -i orbit-db-1 psql "$DSN" -X -q -tA -v ON_ERROR_STOP=1 -c "SELECT id FROM config_version ORDER BY id DESC LIMIT 1"'
  fi
}

# post <id> imprime "<codigo-http>\n<cuerpo>" en stdout.
post() {
  if [ "$SIM" = 1 ]; then
    [ -n "${ORBIT_SIM_TOKEN:-}" ] || { echo "ABORTA: ORBIT_SIM_TOKEN vacio (la simulacion real necesita un token local)"; exit 1; }
    curl -sS -w '\n%{http_code}' -X POST "$API_SIM/api/ads-optimizer/settings/$PLAT" \
      -H "Content-Type: application/json" -H "x-orbit-token: $ORBIT_SIM_TOKEN" -d "$(CUERPO "$1")"
  else
    ssh goncloud "curl -sS -w '\n%{http_code}' -X POST http://127.0.0.1:8010/api/ads-optimizer/settings/$PLAT \
      -H \"Content-Type: application/json\" \
      -H \"x-orbit-token: \$(cat /mnt/data/appdata/orbit/secrets/api_write_token)\" \
      -d '$(CUERPO "$1")'"
  fi
}

if [ "$MUTAR" = 0 ]; then
  echo "== SIMULACRO: sin --acepto-mutacion-real no se escribe nada"
  if [ "$SIM" = 1 ]; then
    echo "POST $API_SIM/api/ads-optimizer/settings/$PLAT"
    echo "header x-orbit-token: <token de ORBIT_SIM_TOKEN, no se imprime>"
  else
    echo "ssh goncloud 'curl -sS -X POST http://127.0.0.1:8010/api/ads-optimizer/settings/$PLAT \\"
    echo "-H \"Content-Type: application/json\" \\"
    echo "-H \"x-orbit-token: \$(cat /mnt/data/appdata/orbit/secrets/api_write_token)\" \\"
    echo "-d \"{\\\"base_config_version_id\\\": <id>, \\\"motor_bid\\\": \\\"niveles_v3\\\"}\"'"
    echo "(llamada literal del paso X.1, cambio 5; el token no sale del servidor)"
  fi
  ID=$(lee_id) || { echo "ABORTA: no se pudo leer base_config_version_id"; exit 1; }
  [ -n "$ID" ] || { echo "ABORTA: config_version sin filas"; exit 1; }
  echo "base_config_version_id seria: $ID"
  echo "cuerpo seria: $(CUERPO "$ID")"
  echo "SIMULACRO OK (nada escrito)"
  exit 0
fi

echo "== encendido real de $PLAT (motor_bid niveles_v3)"
ID=$(lee_id) || { echo "ABORTA: no se pudo leer base_config_version_id"; exit 1; }
for intento in 1 2; do
  RESP=$(post "$ID")
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
        *edicion\ vacia*) echo "OK: edicion vacia, $PLAT ya estaba en niveles_v3 (idempotente)"; exit 0 ;;
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
  *'-> niveles_v3'*) : ;;
  *) echo "ABORTA: el label no trae '-> niveles_v3': $CUERPO_RESP"; exit 1 ;;
esac
echo "OK: $CUERPO_RESP"
