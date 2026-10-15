#!/usr/bin/env bash
# X.3: enciende US FBM. Primero pone el universo en live con
# POST /api/ads-optimizer/settings/amazon_us (precio_modo_universo
# amazon_us/fbm -> live). El global SOLO se sube si aun no esta en
# live (X.1 ya pudo encenderlo: no se pisa sin motivo). Despues pasa los
# goals
# a live en bloque con pedido modo/live (G.2) y el go literal de la fila
# (--go, obligatorio: el literal lo da el dueno al correr; sin el no hay
# encendido). Sin goals shadow que pasar, FRENA (encender sin siembra
# seria un exito que miente: avisa sembrar primero).
# ANTES de todo (tambien en simulacro) verifica por solo-lectura
# ads_bid_politica_amazon_us: ausente o niveles_v3 pasa; otro valor PARA
# con FRENADO sin escribir nada.
# SIMULA POR OMISION: sin --acepto-mutacion-real solo imprime lo que
# haria (lecturas, cuerpos, seleccion) y sale 0 sin POSTear nada.
# Corre DENTRO de orbit-app-1 con la forma de la sonda de M.0 (el python
# viaja por stdin de `docker exec -i`; el token se lee ahi de
# $ORBIT_SECRETS_DIR/api_write_token y jamas se imprime ni sale del
# servidor). En simulacion (ORBIT_SIMULACION=1) los POST van a
# ORBIT_SIM_API con ORBIT_SIM_TOKEN.
# Uso: bash docs/evidencia/repricing-02/ejecucion/X.3/encender.sh [--acepto-mutacion-real] --go "<literal del dueno>"
set -euo pipefail

MUTAR=0
GO=""
while [ $# -gt 0 ]; do
  case "$1" in
    --acepto-mutacion-real) MUTAR=1; shift ;;
    --go) GO=${2:?uso: encender.sh [--acepto-mutacion-real] --go "<literal>"}; shift 2 ;;
    *) echo "uso: encender.sh [--acepto-mutacion-real] --go \"<literal>\""; exit 2 ;;
  esac
done
[ -n "${GO// /}" ] || { echo "uso: encender.sh [--acepto-mutacion-real] --go \"<literal no vacio>\""; exit 2; }
REPO=$(git rev-parse --show-toplevel)
DIR=docs/evidencia/repricing-02/ejecucion/X.3
SIM=${ORBIT_SIMULACION:-0}
API_SIM=${ORBIT_SIM_API:-http://127.0.0.1:8014}
DSN_SIM=${ORBIT_SIM_DSN:-postgresql://orbit:orbit@localhost:5432/orbit_sim_X3}
cd "$REPO"

lee_sql() {  # lee_sql <sql>: solo lectura
  if [ "$SIM" = 1 ]; then
    psql "$DSN_SIM" -X -q -tA -v ON_ERROR_STOP=1 -c "$1"
  else
    ssh goncloud "DSN=\$(docker exec orbit-app-1 printenv ORBIT_DSN_READ); docker exec -i orbit-db-1 psql \"\$DSN\" -X -q -tA -v ON_ERROR_STOP=1 -c \"$1\""
  fi
}
corre_py() {  # corre_py: corre $PYPROG dentro (real) o local (sim)
  if [ "$SIM" = 1 ]; then
    X_BASE="$API_SIM" X_TOKEN="$ORBIT_SIM_TOKEN" X_GO_B64="$(printf '%s' "$GO" | base64 -w0)" \
      X_BASE_ID="$BASE_ID" X_GLOBAL="$GLOBAL" X_UNIV="$UNIV" X_IDS="$IDS_CHUNK" \
      python3 "$PYPROG"
  else
    # shellcheck disable=SC2029
    ssh goncloud "export X_GO_B64='$(printf '%s' "$GO" | base64 -w0)' X_BASE_ID='$BASE_ID' X_GLOBAL='$GLOBAL' X_UNIV='$UNIV' X_IDS='$IDS_CHUNK' X_BASE='http://127.0.0.1:8000'; docker exec -i -e X_GO_B64 -e X_BASE_ID -e X_GLOBAL -e X_UNIV -e X_IDS -e X_BASE orbit-app-1 python -" \
      < "$PYPROG"
  fi
}

echo "== X.3 encender US FBM (paso 0: interruptor de bids)"
BID_POL=$(lee_sql "SELECT settings ->> 'ads_bid_politica_amazon_us' FROM config_version ORDER BY id DESC LIMIT 1;") || { echo "ABORTA: no se pudo leer ads_bid_politica_amazon_us"; exit 1; }
if [ -z "$BID_POL" ]; then
  echo "ads_bid_politica_amazon_us: ausente (el ciclo no mueve bids; pasa)"
elif [ "$BID_POL" = "niveles_v3" ]; then
  echo "ads_bid_politica_amazon_us: niveles_v3 (pasa)"
else
  echo "FRENADO: ads_bid_politica_amazon_us trae '$BID_POL' (ni ausente ni niveles_v3); no se escribe nada"
  exit 1
fi

GLOBAL=$(lee_sql "SELECT settings ->> 'precio_modo_global' FROM config_version ORDER BY id DESC LIMIT 1;") || { echo "ABORTA: no se pudo leer precio_modo_global"; exit 1; }
UNIV=$(lee_sql "SELECT settings -> 'precio_modo_universo' ->> 'amazon_us/fbm' FROM config_version ORDER BY id DESC LIMIT 1;") || { echo "ABORTA: no se pudo leer precio_modo_universo"; exit 1; }
BASE_ID=$(lee_sql "SELECT id FROM config_version ORDER BY id DESC LIMIT 1;") || { echo "ABORTA: no se pudo leer base_config_version_id"; exit 1; }
[ -n "$BASE_ID" ] || { echo "ABORTA: config_version sin filas"; exit 1; }
echo "precio_modo_global: ${GLOBAL:-<ausente>} | amazon_us/fbm: ${UNIV:-<sin entrada>} | base: $BASE_ID"
[ -n "$UNIV" ] || { echo "FRENADO: amazon_us/fbm sin entrada de modo (corre sembrar.sh primero)"; exit 1; }
# C-W2: solo el universo US FBM. El canal de la unidad es el del escenario
# `disponible` mas reciente (la misma fuente del motor y de /precios G.3);
# sin escenario no se puede probar que NO es FBA y el plan lo bloquea de
# todos modos, asi que entra (visible como BLOQUEO, nunca volteado).
IDS_TODOS=$(lee_sql "SELECT string_agg(v.listing_id::text, ',' ORDER BY v.listing_id) FROM v_precio_goal_vigente v LEFT JOIN LATERAL (SELECT canal FROM estimacion_escenario e WHERE e.listing_id = v.listing_id AND e.platform = v.platform AND e.estado = 'disponible' ORDER BY e.observed_at DESC LIMIT 1) e ON true WHERE v.platform = 'amazon_us' AND v.mode = 'shadow' AND (e.canal IS NULL OR e.canal = 'fbm');") || { echo "ABORTA: no se pudo listar goals shadow"; exit 1; }
N_IDS=0
[ -n "$IDS_TODOS" ] && N_IDS=$(printf '%s' "$IDS_TODOS" | tr ',' '\n' | grep -c .)
echo "goals shadow en amazon_us FBM: $N_IDS"
[ "$N_IDS" -gt 0 ] || { echo "FRENADO: sin goals shadow que pasar a live (corre sembrar.sh primero)"; exit 1; }

if [ "$MUTAR" = 0 ]; then
  echo "== SIMULACRO: sin --acepto-mutacion-real no se postea nada"
  if [ "$GLOBAL" = "live" ]; then
  echo "settings que se postearia: {\"base_config_version_id\": $BASE_ID, \"precio_modo_universo\": {\"amazon_us/fbm\": \"live\"}} (el global ya esta en live: no se toca)"
else
  echo "settings que se postearia: {\"base_config_version_id\": $BASE_ID, \"precio_modo_global\": \"live\", \"precio_modo_universo\": {\"amazon_us/fbm\": \"live\"}}"
fi
  echo "goals que se pasarian a live en bloque ($N_IDS): $IDS_TODOS"
  echo "aplicar que se postearia por bloque: pedido {\"tipo\": \"modo\", \"mode\": \"live\"} + go literal (no se imprime) + actor \"X.3 encender.sh\""
  echo "SIMULACRO OK (nada escrito)"
  exit 0
fi

[ "$SIM" = 1 ] && [ -z "${ORBIT_SIM_TOKEN:-}" ] && { echo "ABORTA: ORBIT_SIM_TOKEN vacio (la simulacion real necesita un token local)"; exit 1; }
PYSET=$(mktemp)
PYGOALS=$(mktemp)
trap 'rm -f $PYSET $PYGOALS' EXIT
cat > "$PYSET" <<'PYEOF'
import json, os, sys, urllib.request

BASE = os.environ["X_BASE"]
BASE_ID = os.environ["X_BASE_ID"]
GLOBAL = os.environ.get("X_GLOBAL", "")
UNIV = os.environ.get("X_UNIV", "")
TOK = os.environ.get("X_TOKEN", "")
if not TOK:
    with open(os.path.join(os.environ["ORBIT_SECRETS_DIR"], "api_write_token")) as f:
        TOK = f.read().strip()
if not TOK:
    sys.exit("ABORTA: token de escritura vacio")

def post(ruta, cuerpo):
    req = urllib.request.Request(
        BASE + ruta, data=json.dumps(cuerpo).encode(),
        headers={"Content-Type": "application/json", "x-orbit-token": TOK},
        method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.load(exc)
        except ValueError:
            return exc.code, {"detail": exc.read().decode("utf-8", "replace")[:500]}

cuerpo = {"base_config_version_id": int(BASE_ID),
        "precio_modo_universo": {"amazon_us/fbm": "live"}}
if GLOBAL != "live":
    cuerpo["precio_modo_global"] = "live"
st, r = post("/api/ads-optimizer/settings/amazon_us", cuerpo)
if st == 409:
    sys.exit("REINTENTAR_SETTINGS")
if st == 422 and "edicion vacia" in json.dumps(r):
    if GLOBAL == "live" and UNIV == "live":
        print("settings: ya estaba todo en live (edicion vacia idempotente)")
    else:
        sys.exit(f"ABORTA: edicion vacia inesperada (global={GLOBAL} univ={UNIV}): {json.dumps(r)[:300]}")
elif st != 200:
    sys.exit(f"ABORTA: settings respondio {st}: {json.dumps(r)[:500]}")
else:
    if not r.get("created"):
        sys.exit(f"ABORTA: settings sin created:true: {json.dumps(r)[:300]}")
    print(f"settings: config {r.get('config_version_id')} ({r.get('label')})")
PYEOF
cat > "$PYGOALS" <<'PYEOF'
import base64, json, os, sys, urllib.request

BASE = os.environ["X_BASE"]
IDS = os.environ.get("X_IDS", "")
GO = base64.b64decode(os.environ.get("X_GO_B64", "")).decode("utf-8")
TOK = os.environ.get("X_TOKEN", "")
if not TOK:
    with open(os.path.join(os.environ["ORBIT_SECRETS_DIR"], "api_write_token")) as f:
        TOK = f.read().strip()
if not TOK:
    sys.exit("ABORTA: token de escritura vacio")

def post(ruta, cuerpo):
    req = urllib.request.Request(
        BASE + ruta, data=json.dumps(cuerpo).encode(),
        headers={"Content-Type": "application/json", "x-orbit-token": TOK},
        method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.load(exc)
        except ValueError:
            return exc.code, {"detail": exc.read().decode("utf-8", "replace")[:500]}

pedido = {"tipo": "modo", "mode": "live"}
cuerpo = {"platform": "amazon_us", "pedido": pedido,
          "listing_ids": [int(x) for x in IDS.split(",")]}
st, plan = post("/api/precios/goals/plan", cuerpo)
if st != 200:
    sys.exit(f"ABORTA: plan respondio {st}: {json.dumps(plan)[:500]}")
filas = plan.get("filas", [])
bloq = [(f["listing_id"], f.get("bloqueo"), f.get("motivo")) for f in filas if f.get("bloqueo")]
print(f"plan live: {len(filas)} filas, {len(bloq)} bloqueadas, huella {plan.get('huella')}")
for lid, bl, mot in bloq:
    print(f"BLOQUEO listing={lid} bloqueo={bl} motivo={mot}")
st, r = post("/api/precios/goals/aplicar", dict(
    cuerpo, huella=plan["huella"], confirmados=[], go_literal=GO,
    actor="X.3 encender.sh"))
if st == 409:
    nuevo = (r.get("detail") or {}).get("plan_nuevo") if isinstance(r.get("detail"), dict) else None
    if nuevo is None:
        sys.exit(f"ABORTA: 409 sin plan_nuevo: {json.dumps(r)[:500]}")
    print(f"409: la huella cambio; reintento una vez con {nuevo.get('huella')}")
    st, r = post("/api/precios/goals/aplicar", dict(
        cuerpo, huella=nuevo["huella"], confirmados=[], go_literal=GO,
        actor="X.3 encender.sh"))
if st != 200:
    sys.exit(f"ABORTA: aplicar respondio {st}: {json.dumps(r)[:500]}")
print(f"aplicado: sembrados={r.get('sembrados')} cerrados={r.get('cerrados')} lote={r.get('lote')}")
for o in r.get("omitidas") or []:
    print(f"OMITIDA listing={o.get('listing_id')} bloqueo={o.get('bloqueo')} motivo={o.get('motivo')}")
PYEOF

echo "== encendido real: settings (universo live + global solo si falta)"
PYPROG=$PYSET
IDS_CHUNK=""
for intento in 1 2; do
  if SALIDA_SET=$(corre_py 2>&1); then break; fi
  if [ "$intento" = 1 ] && grep -q REINTENTAR_SETTINGS <<<"$SALIDA_SET"; then
    echo "409 en settings: otro guardo primero; releo el id y repito una vez"
    BASE_ID=$(lee_sql "SELECT id FROM config_version ORDER BY id DESC LIMIT 1;") || { echo "ABORTA: no se pudo releer base_config_version_id"; exit 1; }
    GLOBAL=$(lee_sql "SELECT settings ->> 'precio_modo_global' FROM config_version ORDER BY id DESC LIMIT 1;")
    UNIV=$(lee_sql "SELECT settings -> 'precio_modo_universo' ->> 'amazon_us/fbm' FROM config_version ORDER BY id DESC LIMIT 1;")
    continue
  fi
  printf '%s\n' "$SALIDA_SET"
  echo "ABORTA: settings fallo"
  exit 1
done
printf '%s\n' "$SALIDA_SET"

echo "== encendido real: goals a live en bloque ($N_IDS unidades)"
PYPROG=$PYGOALS
BLOQUE=0
RESTO="$IDS_TODOS"
while [ -n "$RESTO" ]; do
  BLOQUE=$((BLOQUE + 1))
  IDS_CHUNK=$(printf '%s' "$RESTO" | tr ',' '\n' | head -n 500 | paste -sd ',' -)
  RESTO=$(printf '%s' "$RESTO" | tr ',' '\n' | tail -n +501 | paste -sd ',' -)
  echo "-- bloque $BLOQUE ($(printf '%s' "$IDS_CHUNK" | tr ',' '\n' | grep -c .) unidades)"
  corre_py || { echo "ABORTA: bloque $BLOQUE fallo"; exit 1; }
done
echo "ENCENDIDO OK: amazon_us/fbm en live"
