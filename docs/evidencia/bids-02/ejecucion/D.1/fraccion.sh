#!/usr/bin/env bash
# D.1 escribe la fraccion de margen de US en 0.8 ANTES de desplegar.sh.
# El codigo anterior camina hacia el target nuevo a 0.5 por ciclo, asi que
# escribirla antes no produce ningun salto. Corre antes de las 08:40 UTC.
#
# SIMULA POR OMISION: sin --acepto-mutacion-real solo imprime la llamada que
# haria y sale 0 sin mutar nada. Con la bandera, manda la llamada literal
# del paso D.1 (cambio 3) y verifica created:true con el label
# "margen encendido (fraccion <antes> -> 0.8)". El token de escritura no sale
# del servidor ni se imprime: en modo real vive dentro del ssh, en
# simulacion viaja solo en el header del curl, nunca en la salida.
#
# Modo real: el POST va por ssh a goncloud, con el token de
# /mnt/data/appdata/orbit/secrets/api_write_token. Modo simulacion
# (ORBIT_SIMULACION=1): el POST va a ORBIT_SIM_API (una app local apuntada
# a ORBIT_SIM_DSN) con el token de ORBIT_SIM_TOKEN; el id base se lee de la
# base local. Un 409 relee el id y repite una vez. Un 422 con
# "edicion vacia" significa que la fraccion ya era 0.8: exito.
# Uso: bash docs/evidencia/bids-02/ejecucion/D.1/fraccion.sh [--acepto-mutacion-real]
set -euo pipefail

MUTAR=0
[ "${1:-}" = "--acepto-mutacion-real" ] && MUTAR=1
[ $# -le 1 ] && { [ $# -eq 0 ] || [ "$MUTAR" = 1 ]; } || { echo "uso: fraccion.sh [--acepto-mutacion-real]"; exit 2; }
SIM=${ORBIT_SIMULACION:-0}
API_SIM=${ORBIT_SIM_API:-http://127.0.0.1:8011}
DSN_SIM=${ORBIT_SIM_DSN:-postgresql://orbit:orbit@127.0.0.1:5433/orbit_sim_d1}
CUERPO() { printf '{"base_config_version_id": %s, "margen": {"habilitado": true, "fraccion": 0.8}}' "$1"; }

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
    curl -sS -w '\n%{http_code}' -X POST "$API_SIM/api/ads-optimizer/settings/amazon_us" \
      -H "Content-Type: application/json" -H "x-orbit-token: $ORBIT_SIM_TOKEN" -d "$(CUERPO "$1")"
  else
    ssh goncloud "curl -sS -w '\n%{http_code}' -X POST http://127.0.0.1:8010/api/ads-optimizer/settings/amazon_us \
      -H \"Content-Type: application/json\" \
      -H \"x-orbit-token: \$(cat /mnt/data/appdata/orbit/secrets/api_write_token)\" \
      -d '$(CUERPO "$1")'"
  fi
}

if [ "$MUTAR" = 0 ]; then
  echo "== SIMULACRO: sin --acepto-mutacion-real no se escribe nada"
  if [ "$SIM" = 1 ]; then
    echo "POST $API_SIM/api/ads-optimizer/settings/amazon_us"
    echo "header x-orbit-token: <token de ORBIT_SIM_TOKEN, no se imprime>"
  else
    echo "ssh goncloud 'curl -sS -X POST http://127.0.0.1:8010/api/ads-optimizer/settings/amazon_us \\"
    echo "-H \"Content-Type: application/json\" \\"
    echo "-H \"x-orbit-token: \$(cat /mnt/data/appdata/orbit/secrets/api_write_token)\" \\"
    echo "-d \"{\\\"base_config_version_id\\\": <id>, \\\"margen\\\": {\\\"habilitado\\\": true, \\\"fraccion\\\": 0.8}}\""
    echo "(llamada literal del paso D.1, cambio 3; el token no sale del servidor)"
  fi
  ID=$(lee_id) || { echo "ABORTA: no se pudo leer base_config_version_id"; exit 1; }
  [ -n "$ID" ] || { echo "ABORTA: config_version sin filas"; exit 1; }
  echo "base_config_version_id seria: $ID"
  echo "cuerpo seria: $(CUERPO "$ID")"
  echo "SIMULACRO OK (nada escrito)"
  exit 0
fi

echo "== escritura real de la fraccion US 0.8"
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
        *edicion\ vacia*) echo "OK: edicion vacia, la fraccion ya era 0.8 (idempotente)"; exit 0 ;;
      esac
      echo "ABORTA: 422 no idempotente: $CUERPO_RESP"; exit 1 ;;
    *) echo "ABORTA: HTTP $CODIGO: $CUERPO_RESP"; exit 1 ;;
  esac
done
case "$CUERPO_RESP" in
  *'"created":true'*margen\ encendido*|*'"created": true'*margen\ encendido*) : ;;
  *) echo "ABORTA: respuesta sin created:true: $CUERPO_RESP"; exit 1 ;;
esac
case "$CUERPO_RESP" in
  *'-> 0.8)'*) : ;;
  *) echo "ABORTA: el label no trae '-> 0.8)': $CUERPO_RESP"; exit 1 ;;
esac
echo "OK: $CUERPO_RESP"
