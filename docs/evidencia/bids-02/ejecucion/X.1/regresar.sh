#!/usr/bin/env bash
# X.1 paso 6: regresa las keywords danadas en LAS DOS plataformas (tambien
# en una que no se encendio), con POST /api/ads-optimizer/bid/regresar-todas
# y cuerpo {"plataforma": <p>, "confirmacion": "REGRESAR <N> KEYWORDS",
# "actor": "plan bids-02 X.1"}. N es el vigente por plataforma: hojas con
# ya_regresada en falso segun lee_danadas. Un 409 significa que la lista
# cambio: relee N y repite UNA vez. Guarda la respuesta completa por
# plataforma en ejecucion/X.1/respuesta-regresar-<plataforma>-<sello>.txt
# (el sello es por corrida: una segunda corrida no pisa la evidencia de
# la primera). Una hoja que fallo queda anotada en su motivo y no detiene
# a las demas (criterio 6 del Comprueba: cada llamada trae un RegresoHecho
# o un motivo por hoja); pero la corrida sale 1 si alguna hoja quedo con
# ok:false, tras procesar las dos plataformas. OK y exit 0 solo si todas
# quedaron ok:true.
# Verifica su propio 200: la respuesta trae N resultados y cada ok:true
# trae regreso_confirmado_at; sin el sale PENDIENTE_ANTERIOR para releer
# (el script aborta; repetir con N fresco es idempotente).
# N sale de lee_danadas, no de la pantalla: en modo real corre por la
# entrada estandar del contenedor (que ya trae ORBIT_DSN_READ); en
# simulacion corre con python local y ORBIT_DSN_READ a ORBIT_SIM_DSN.
# SIMULA POR OMISION: sin --acepto-mutacion-real solo imprime N y el cuerpo
# que mandaria por plataforma, y sale 0 sin mutar nada (no guarda txts).
# El token jamas se imprime: en modo real vive dentro del ssh, en
# simulacion viaja solo en el header del curl.
# Uso: bash docs/evidencia/bids-02/ejecucion/X.1/regresar.sh [--acepto-mutacion-real]
set -euo pipefail

MUTAR=0
[ "${1:-}" = "--acepto-mutacion-real" ] && MUTAR=1
[ $# -le 1 ] && { [ $# -eq 0 ] || [ "$MUTAR" = 1 ]; } || { echo "uso: regresar.sh [--acepto-mutacion-real]"; exit 2; }
REPO=$(git rev-parse --show-toplevel)
DIR=docs/evidencia/bids-02/ejecucion/X.1
SIM=${ORBIT_SIMULACION:-0}
API_SIM=${ORBIT_SIM_API:-http://127.0.0.1:8011}
DSN_SIM=${ORBIT_SIM_DSN:-postgresql://orbit:orbit@127.0.0.1:5433/orbit_sim_d1}
PY=${ORBIT_SIM_PYTHON:-$REPO/.venv/bin/python}
ACTOR="plan bids-02 X.1"
STAMP=$(date -u +%Y%m%d-%H%M%S)
cd "$REPO"

# Una sola definicion: imprime N (hojas con ya_regresada en falso) para la
# plataforma de argv[1]. Viaja por stdin igual que tools/rejuega_niveles.py.
SNIPPET=$(cat <<'PY'
import os, sys
from app.db import connect
from app.pantalla_danadas import lee_danadas
dsn = os.environ.get("ORBIT_DSN_READ", "")
if not dsn:
    print("ORBIT_DSN_READ no configurado", file=sys.stderr)
    sys.exit(2)
with connect(dsn) as conn:
    pantalla = lee_danadas(conn, plataforma=sys.argv[1])
print(sum(1 for h in pantalla.hojas if not h.ya_regresada))
PY
)

# lee_n <plataforma> imprime N en stdout.
lee_n() {
  if [ "$SIM" = 1 ]; then
    printf '%s\n' "$SNIPPET" | ORBIT_DSN_READ="$DSN_SIM" PYTHONPATH="$REPO" "$PY" - "$1"
  else
    printf '%s\n' "$SNIPPET" | ssh goncloud "docker exec -i orbit-app-1 python - $1"
  fi
}

CUERPO() { printf '{"plataforma": "%s", "confirmacion": "REGRESAR %s KEYWORDS", "actor": "%s"}' "$1" "$2" "$ACTOR"; }

# post <plataforma> <N> imprime "<codigo-http>\n<cuerpo>" en stdout.
post() {
  if [ "$SIM" = 1 ]; then
    [ -n "${ORBIT_SIM_TOKEN:-}" ] || { echo "ABORTA: ORBIT_SIM_TOKEN vacio (la simulacion real necesita un token local)"; exit 1; }
    curl -sS -w '\n%{http_code}' -X POST "$API_SIM/api/ads-optimizer/bid/regresar-todas" \
      -H "Content-Type: application/json" -H "x-orbit-token: $ORBIT_SIM_TOKEN" -d "$(CUERPO "$1" "$2")"
  else
    ssh goncloud "curl -sS -w '\n%{http_code}' -X POST http://127.0.0.1:8010/api/ads-optimizer/bid/regresar-todas \
      -H \"Content-Type: application/json\" \
      -H \"x-orbit-token: \$(cat /mnt/data/appdata/orbit/secrets/api_write_token)\" \
      -d '$(CUERPO "$1" "$2")'"
  fi
}

n_valida() {  # n_valida <N> <plataforma>: aborta si N no es entero >= 0.
  case "$1" in
    ''|*[!0-9]*) echo "ABORTA: N ilegible para $2: '$1'"; exit 1 ;;
  esac
}

# verifica_200 <txt> <N> <plataforma>: la respuesta trae N resultados y
# cada ok:true trae regreso_confirmado_at. Sin el: PENDIENTE_ANTERIOR.
VERIFICA=$(cat <<'PY'
import json, sys
txt, n, plat = sys.argv[1], int(sys.argv[2]), sys.argv[3]
doc = json.load(open(txt))
res = doc.get("resultados", [])
if len(res) != n:
    print(f"ABORTA: {plat} trae {len(res)} hojas, se pidieron {n}")
    sys.exit(1)
pend = [
    r.get("hoja_id", "?")
    for r in res
    if r.get("ok") is True and not r.get("regreso_confirmado_at")
]
for h in pend:
    print(f"PENDIENTE_ANTERIOR {plat} hoja={h} (sin regreso_confirmado_at: releer)")
sys.exit(1 if pend else 0)
PY
)

if [ "$MUTAR" = 0 ]; then
  echo "== SIMULACRO: sin --acepto-mutacion-real no se escribe nada"
  for PLAT in amazon_mx amazon_us; do
    N=$(lee_n "$PLAT") || { echo "ABORTA: no se pudo leer N para $PLAT"; exit 1; }
    n_valida "$N" "$PLAT"
    echo "$PLAT: N=$N"
    if [ "$SIM" = 1 ]; then
      echo "POST $API_SIM/api/ads-optimizer/bid/regresar-todas"
      echo "header x-orbit-token: <token de ORBIT_SIM_TOKEN, no se imprime>"
    else
      echo "ssh goncloud 'curl -sS -X POST http://127.0.0.1:8010/api/ads-optimizer/bid/regresar-todas [...]'"
    fi
    echo "cuerpo seria: $(CUERPO "$PLAT" "$N")"
  done
  echo "(el token no sale del servidor; en sim solo viaja en el header)"
  echo "SIMULACRO OK (nada escrito)"
  exit 0
fi

echo "== regreso real de danadas (actor: $ACTOR)"
FALLAS=0
for PLAT in amazon_mx amazon_us; do
  N=$(lee_n "$PLAT") || { echo "ABORTA: no se pudo leer N para $PLAT"; exit 1; }
  n_valida "$N" "$PLAT"
  for intento in 1 2; do
    RESP=$(post "$PLAT" "$N")
    CODIGO=$(printf '%s\n' "$RESP" | tail -1)
    CUERPO_RESP=$(printf '%s\n' "$RESP" | sed '$d')
    echo "$PLAT: intento $intento con N=$N -> HTTP $CODIGO"
    case "$CODIGO" in
      200) break ;;
      409)
        [ "$intento" = 2 ] && { echo "ABORTA: $PLAT 409 dos veces seguidas: $CUERPO_RESP"; exit 1; }
        echo "$PLAT: 409, la lista cambio; releo N y repito una vez"
        N=$(lee_n "$PLAT") || { echo "ABORTA: no se pudo releer N para $PLAT"; exit 1; }
        n_valida "$N" "$PLAT"
        continue ;;
      *) echo "ABORTA: $PLAT HTTP $CODIGO: $CUERPO_RESP"; exit 1 ;;
    esac
  done
  TXT="$DIR/respuesta-regresar-$PLAT-$STAMP.txt"
  printf '%s\n' "$CUERPO_RESP" > "$TXT"
  printf '%s\n' "$VERIFICA" | "$PY" - "$TXT" "$N" "$PLAT"
  OKS=$(printf '%s\n' "$CUERPO_RESP" | { grep -o '"ok": *true' || true; } | wc -l | tr -d ' ')
  MOTIVOS=$(printf '%s\n' "$CUERPO_RESP" | { grep -o '"ok": *false' || true; } | wc -l | tr -d ' ')
  FALLAS=$((FALLAS + MOTIVOS))
  echo "$PLAT: HTTP 200, ok=$OKS motivos=$MOTIVOS -> $TXT"
done
if [ "$FALLAS" -gt 0 ]; then
  echo "FALLA: $FALLAS hoja(s) con ok:false (evidencia en $DIR/respuesta-regresar-*-$STAMP.txt); la corrida no fue limpia"
  exit 1
fi
echo "OK: respuestas guardadas en $DIR/respuesta-regresar-<plataforma>-$STAMP.txt"
