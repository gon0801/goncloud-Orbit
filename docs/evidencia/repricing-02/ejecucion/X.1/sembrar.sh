#!/usr/bin/env bash
# X.1: siembra "margen de hoy" en shadow a MX FBA con las rutas G.2
# (POST /api/precios/goals/plan + POST /api/precios/goals/aplicar, las
# mismas que usa /precios). Guarda el plan (plan-<sello>.json) con las
# unidades que quedaron bloqueadas y su motivo. Si el universo no tiene
# entrada en precio_modo_universo, la pone antes en shadow con la llamada
# de settings de encender.sh (en X.1 la 0054 ya la sembro; el paso queda
# por robustez). Los bloqueos fuera_de_banda NO se confirman: se listan
# para el dueno (criterio 1).
# SIMULA POR OMISION: sin --acepto-mutacion-real solo imprime lo que
# haria (cuerpos, seleccion, id base) y sale 0 sin POSTear nada. Con la
# bandera escribe de verdad: plan, guardar, aplicar con la huella.
# Corre DENTRO de orbit-app-1 con la forma de la sonda de M.0 (el python
# viaja por stdin de `docker exec -i`; el token se lee ahi de
# $ORBIT_SECRETS_DIR/api_write_token y jamas se imprime ni sale del
# servidor). En simulacion (ORBIT_SIMULACION=1) los POST van a
# ORBIT_SIM_API con ORBIT_SIM_TOKEN y el plan se guarda en
# ${TMPDIR:-/tmp}/orbit-x1-sim/.
# Uso: bash docs/evidencia/repricing-02/ejecucion/X.1/sembrar.sh [--acepto-mutacion-real] [--listing-ids 1,2,3]
set -euo pipefail

MUTAR=0
IDS=""
while [ $# -gt 0 ]; do
  case "$1" in
    --acepto-mutacion-real) MUTAR=1; shift ;;
    --listing-ids) IDS=${2:?uso: sembrar.sh [--acepto-mutacion-real] [--listing-ids 1,2,3]}; shift 2 ;;
    *) echo "uso: sembrar.sh [--acepto-mutacion-real] [--listing-ids 1,2,3]"; exit 2 ;;
  esac
done
case "$IDS" in ''|*[!0-9,]*) [ -z "$IDS" ] || { echo "uso: --listing-ids trae solo digitos y comas"; exit 2; } ;; esac
REPO=$(git rev-parse --show-toplevel)
DIR=docs/evidencia/repricing-02/ejecucion/X.1
SIM=${ORBIT_SIMULACION:-0}
API_SIM=${ORBIT_SIM_API:-http://127.0.0.1:8012}
DSN_SIM=${ORBIT_SIM_DSN:-postgresql://orbit:orbit@localhost:5432/orbit_sim_X1}
STAMP=$(date -u +%Y%m%d-%H%M%S)
cd "$REPO"

lee_sql() {  # lee_sql <sql>: solo lectura
  if [ "$SIM" = 1 ]; then
    psql "$DSN_SIM" -X -q -tA -v ON_ERROR_STOP=1 -c "$1"
  else
    ssh goncloud "DSN=\$(docker exec orbit-app-1 printenv ORBIT_DSN_READ); docker exec -i orbit-db-1 psql \"\$DSN\" -X -q -tA -v ON_ERROR_STOP=1 -c \"$1\""
  fi
}

echo "== X.1 sembrar margen-de-hoy en shadow (MX FBA)"
MODO_UNIV=$(lee_sql "SELECT settings -> 'precio_modo_universo' ->> 'amazon_mx/fba' FROM config_version ORDER BY id DESC LIMIT 1;") || { echo "ABORTA: no se pudo leer precio_modo_universo"; exit 1; }
echo "modo amazon_mx/fba: ${MODO_UNIV:-<sin entrada>}"
BASE_ID=$(lee_sql "SELECT id FROM config_version ORDER BY id DESC LIMIT 1;") || { echo "ABORTA: no se pudo leer base_config_version_id"; exit 1; }
[ -n "$BASE_ID" ] || { echo "ABORTA: config_version sin filas"; exit 1; }
if [ -n "$IDS" ]; then
  SEL="listing_ids [$IDS]"
else
  SEL="sin_goal (todo el universo)"
  N_SIN=$(lee_sql "SELECT count(*) FROM listing l LEFT JOIN v_precio_goal_vigente v ON v.listing_id = l.id AND v.platform = l.platform WHERE l.platform = 'amazon_mx' AND v.goal_id IS NULL;") || { echo "ABORTA: no se pudo contar sin_goal"; exit 1; }
  echo "unidades amazon_mx sin goal vigente: $N_SIN"
fi
echo "seleccion: $SEL"

if [ "$MUTAR" = 0 ]; then
  echo "== SIMULACRO: sin --acepto-mutacion-real no se postea nada"
  if [ -z "$MODO_UNIV" ]; then
    echo "settings que se postearia: {\"base_config_version_id\": $BASE_ID, \"precio_modo_universo\": {\"amazon_mx/fba\": \"shadow\"}}"
  fi
  if [ -n "$IDS" ]; then
    echo "plan que se postearia: {\"platform\": \"amazon_mx\", \"pedido\": {\"tipo\": \"margen_de_hoy\", \"mode\": \"shadow\"}, \"listing_ids\": [$IDS]}"
  else
    echo "plan que se postearia: {\"platform\": \"amazon_mx\", \"pedido\": {\"tipo\": \"margen_de_hoy\", \"mode\": \"shadow\"}, \"sin_goal\": true}"
  fi
  echo "aplicar que se postearia: mismo cuerpo + {\"huella\": \"<del plan>\", \"confirmados\": [], \"actor\": \"X.1 sembrar.sh\"}"
  echo "SIMULACRO OK (nada escrito)"
  exit 0
fi

[ "$SIM" = 1 ] && [ -z "${ORBIT_SIM_TOKEN:-}" ] && { echo "ABORTA: ORBIT_SIM_TOKEN vacio (la simulacion real necesita un token local)"; exit 1; }
GUARDA_DIR=$([ "$SIM" = 1 ] && echo "${TMPDIR:-/tmp}/orbit-x1-sim" || echo "$DIR")
mkdir -p "$GUARDA_DIR"
PLAN_JSON="$GUARDA_DIR/plan-$STAMP.json"
SALIDA_TXT="$GUARDA_DIR/sembrar-$STAMP.txt"
PYPROG=$(mktemp)
trap 'rm -f $PYPROG' EXIT
cat > "$PYPROG" <<'PYEOF'
import base64, json, os, sys, urllib.request

BASE = os.environ["X_BASE"]
IDS = os.environ.get("X_IDS", "")
BASE_ID = os.environ["X_BASE_ID"]
MODO_UNIV = os.environ.get("X_MODO_UNIV", "")
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

if not MODO_UNIV:
    st, r = post("/api/ads-optimizer/settings/amazon_mx", {
        "base_config_version_id": int(BASE_ID),
        "precio_modo_universo": {"amazon_mx/fba": "shadow"}})
    if st == 409:
        sys.exit("ABORTA: 409 en settings (otro guardo primero); re-corre el script")
    if st != 200:
        sys.exit(f"ABORTA: settings respondio {st}: {json.dumps(r)[:500]}")
    print(f"universo puesto en shadow: config {r.get('config_version_id')} ({r.get('label')})")
else:
    print(f"universo ya en {MODO_UNIV}: sin settings previo")

pedido = {"tipo": "margen_de_hoy", "mode": "shadow"}
cuerpo = {"platform": "amazon_mx", "pedido": pedido}
if IDS:
    cuerpo["listing_ids"] = [int(x) for x in IDS.split(",")]
else:
    cuerpo["sin_goal"] = True
st, plan = post("/api/precios/goals/plan", cuerpo)
if st != 200:
    sys.exit(f"ABORTA: plan respondio {st}: {json.dumps(plan)[:500]}")
print(f"PLAN_JSON {json.dumps(plan, separators=(',', ':'))}")
filas = plan.get("filas", [])
bloq = [(f["listing_id"], f.get("bloqueo"), f.get("motivo")) for f in filas if f.get("bloqueo")]
print(f"plan: {len(filas)} filas, {len(bloq)} bloqueadas, huella {plan.get('huella')}")
for lid, bl, mot in bloq:
    print(f"BLOQUEO listing={lid} bloqueo={bl} motivo={mot}")
st, r = post("/api/precios/goals/aplicar", dict(
    cuerpo, huella=plan["huella"], confirmados=[],
    actor="X.1 sembrar.sh"))
if st == 409:
    nuevo = (r.get("detail") or {}).get("plan_nuevo") if isinstance(r.get("detail"), dict) else None
    if nuevo is None:
        sys.exit(f"ABORTA: 409 sin plan_nuevo: {json.dumps(r)[:500]}")
    print(f"409: la huella cambio; reintento una vez con {nuevo.get('huella')}")
    print(f"PLAN_JSON {json.dumps(nuevo, separators=(',', ':'))}")
    st, r = post("/api/precios/goals/aplicar", dict(
        cuerpo, huella=nuevo["huella"], confirmados=[],
        actor="X.1 sembrar.sh"))
if st != 200:
    sys.exit(f"ABORTA: aplicar respondio {st}: {json.dumps(r)[:500]}")
print(f"aplicado: sembrados={r.get('sembrados')} cerrados={r.get('cerrados')} lote={r.get('lote')}")
PYEOF

echo "== siembra real (guardas: plan + bloqueos en $PLAN_JSON)"
if [ "$SIM" = 1 ]; then
  X_BASE="$API_SIM" X_TOKEN="$ORBIT_SIM_TOKEN" X_IDS="$IDS" X_BASE_ID="$BASE_ID" X_MODO_UNIV="$MODO_UNIV" \
    python3 "$PYPROG" 2>&1 | tee "$SALIDA_TXT"
  RC=${PIPESTATUS[0]}
else
  # shellcheck disable=SC2029
  ssh goncloud \
    "X_IDS='$IDS' X_BASE_ID='$BASE_ID' X_MODO_UNIV='$MODO_UNIV' X_BASE='http://127.0.0.1:8000' docker exec -i orbit-app-1 python -" \
    < "$PYPROG" 2>&1 | tee "$SALIDA_TXT"
  RC=${PIPESTATUS[0]}
fi
[ "$RC" = 0 ] || { echo "ABORTA: la siembra fallo (ver $SALIDA_TXT)"; exit 1; }
grep '^PLAN_JSON ' "$SALIDA_TXT" | tail -n 1 | sed 's/^PLAN_JSON //' > "$PLAN_JSON" || { echo "ABORTA: sin PLAN_JSON en la salida"; exit 1; }
echo "plan guardado en $PLAN_JSON"
NBLOQ=$(grep -c '^BLOQUEO ' "$SALIDA_TXT" || true)
echo "unidades bloqueadas guardadas: $NBLOQ (con motivo, en $SALIDA_TXT)"
