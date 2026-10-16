#!/usr/bin/env bash
# X.2: siembra "margen de hoy" en shadow a MX FBM con las rutas G.2
# (POST /api/precios/goals/plan + POST /api/precios/goals/aplicar, las
# mismas que usa /precios). Guarda el plan (plan-<sello>.json; un archivo
# por bloque de 500 si hay mas: plan-<sello>-b<N>.json) con las unidades
# que quedaron bloqueadas y su motivo. Si el universo no tiene entrada en
# precio_modo_universo, la pone antes en shadow (en X.2 NO hay entrada:
# sin ella el universo estaria apagado).
# Los bloqueos fuera_de_banda NO se confirman: se listan para el dueno
# (criterio 1).
# C-W2: la seleccion es MX FBM, no toda la plataforma: se resuelven
# los ids sin goal con el filtro de canal (escenario `disponible` mas
# reciente; sin escenario entra y el plan lo bloquea) y se postean como
# `listing_ids` en bloques de 500 (el `sin_goal` de la API no filtra
# canal y sembraria FBA). `--listing-ids` explicitos se respetan tal
# cual (el dueno los pidio; sin filtro).
# R05d: las unidades sin oferta viva NO se siembran ni entran al
# denominador: inventario 0 Y sin precio propio observado, con las
# fuentes del motor (ultima spapi_price_observation sin precio propio +
# ultimo spapi_inventario_observation en 0; sin fila de inventario no es
# 0, es sin dato y entra (en FBM puro no hay filas: ahi el filtro no
# muerde). Se listan como EXCLUIDA (solo ids). Solo a la seleccion
# automatica; los explicitos los rescata leer.sh del denominador.
# SIMULA POR OMISION: sin --acepto-mutacion-real solo imprime lo que
# haria (cuerpos, seleccion, id base) y sale 0 sin POSTear nada. Con la
# bandera escribe de verdad: plan, guardar, aplicar con la huella.
# Corre DENTRO de orbit-app-1 con la forma de la sonda de M.0 (el python
# viaja por stdin de `docker exec -i`; el token se lee ahi de
# $ORBIT_SECRETS_DIR/api_write_token y jamas se imprime ni sale del
# servidor). En simulacion (ORBIT_SIMULACION=1) los POST van a
# ORBIT_SIM_API con ORBIT_SIM_TOKEN y el plan se guarda en
# ${TMPDIR:-/tmp}/orbit-x2-sim/.
# Uso: bash docs/evidencia/repricing-02/ejecucion/X.2/sembrar.sh [--acepto-mutacion-real] [--listing-ids 1,2,3]
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
DIR=docs/evidencia/repricing-02/ejecucion/X.2
SIM=${ORBIT_SIMULACION:-0}
API_SIM=${ORBIT_SIM_API:-http://127.0.0.1:8013}
DSN_SIM=${ORBIT_SIM_DSN:-postgresql://orbit:orbit@localhost:5432/orbit_sim_X2}
STAMP=$(date -u +%Y%m%d-%H%M%S)
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
    X_BASE="$API_SIM" X_TOKEN="$ORBIT_SIM_TOKEN" X_IDS="$IDS_CHUNK" \
      X_BASE_ID="$BASE_ID" X_MODO_UNIV="$MODO_UNIV" \
      python3 "$PYPROG"
  else
    # shellcheck disable=SC2029
    ssh goncloud "export X_IDS='$IDS_CHUNK' X_BASE_ID='$BASE_ID' X_MODO_UNIV='$MODO_UNIV' X_BASE='http://127.0.0.1:8000'; docker exec -i -e X_IDS -e X_BASE_ID -e X_MODO_UNIV -e X_BASE orbit-app-1 python -" \
      < "$PYPROG"
  fi
}

echo "== X.2 sembrar margen-de-hoy en shadow (MX FBM)"
MODO_UNIV=$(lee_sql "SELECT settings -> 'precio_modo_universo' ->> 'amazon_mx/fbm' FROM config_version ORDER BY id DESC LIMIT 1;") || { echo "ABORTA: no se pudo leer precio_modo_universo"; exit 1; }
echo "modo amazon_mx/fbm: ${MODO_UNIV:-<sin entrada>}"
BASE_ID=$(lee_sql "SELECT id FROM config_version ORDER BY id DESC LIMIT 1;") || { echo "ABORTA: no se pudo leer base_config_version_id"; exit 1; }
[ -n "$BASE_ID" ] || { echo "ABORTA: config_version sin filas"; exit 1; }
if [ -n "$IDS" ]; then
  IDS_TODOS="$IDS"
else
  # C-W2: solo MX FBM (mismo filtro de canal que encender.sh/apagar.sh).
  IDS_TODOS=$(lee_sql "SELECT string_agg(l.id::text, ',' ORDER BY l.id) FROM listing l LEFT JOIN v_precio_goal_vigente v ON v.listing_id = l.id AND v.platform = l.platform LEFT JOIN LATERAL (SELECT canal FROM estimacion_escenario e WHERE e.listing_id = l.id AND e.platform = l.platform AND e.estado = 'disponible' ORDER BY e.observed_at DESC LIMIT 1) e ON true WHERE l.platform = 'amazon_mx' AND v.goal_id IS NULL AND (e.canal IS NULL OR e.canal = 'fbm');") || { echo "ABORTA: no se pudo listar sin_goal FBM"; exit 1; }
fi
N_SIN=0
[ -n "$IDS_TODOS" ] && N_SIN=$(printf '%s' "$IDS_TODOS" | tr ',' '\n' | grep -c .)
if [ -n "$IDS" ]; then
  echo "ids explicitos (sin filtro de canal): $N_SIN"
else
  echo "unidades MX FBM sin goal vigente: $N_SIN"
fi
echo "seleccion: listing_ids [$IDS_TODOS]"

# R05d: fuera del denominador y de los goals (solo seleccion automatica).
if [ -z "$IDS" ] && [ -n "$IDS_TODOS" ]; then
  EXCLUIDAS=$(lee_sql "SELECT COALESCE(string_agg(l.id::text, ',' ORDER BY l.id), '') FROM listing l LEFT JOIN LATERAL (SELECT o.own_listing_price FROM spapi_price_observation o WHERE o.asin = l.external_id AND o.platform = l.platform ORDER BY o.observed_at DESC LIMIT 1) p ON true LEFT JOIN LATERAL (SELECT i.total_quantity FROM spapi_inventario_observation i WHERE i.seller_sku = l.seller_sku AND i.platform = l.platform ORDER BY i.observed_at DESC LIMIT 1) s ON true WHERE l.platform = 'amazon_mx' AND l.id = ANY(string_to_array('$IDS_TODOS', ',')::bigint[]) AND p.own_listing_price IS NULL AND s.total_quantity = 0;") || { echo "ABORTA: no se pudo listar sin_oferta_viva"; exit 1; }
  N_EXCL=0
  if [ -n "$EXCLUIDAS" ]; then
    N_EXCL=$(printf '%s' "$EXCLUIDAS" | tr ',' '\n' | grep -c .)
    for x in $(printf '%s' "$EXCLUIDAS" | tr ',' ' '); do echo "EXCLUIDA listing=$x motivo=sin_oferta_viva"; done
    RESTO=""
    for x in $(printf '%s' "$IDS_TODOS" | tr ',' ' '); do
      case ",$EXCLUIDAS," in *,"$x",*) ;; *) RESTO="${RESTO:+$RESTO,}$x" ;; esac
    done
    IDS_TODOS="$RESTO"
  fi
  echo "excluidas sin oferta viva: $N_EXCL"
  echo "seleccion tras filtro: listing_ids [$IDS_TODOS]"
fi

if [ "$MUTAR" = 0 ]; then
  echo "== SIMULACRO: sin --acepto-mutacion-real no se postea nada"
  if [ -z "$MODO_UNIV" ]; then
    echo "settings que se postearia: {\"base_config_version_id\": $BASE_ID, \"precio_modo_universo\": {\"amazon_mx/fbm\": \"shadow\"}}"
  fi
  echo "plan que se postearia: {\"platform\": \"amazon_mx\", \"pedido\": {\"tipo\": \"margen_de_hoy\", \"mode\": \"shadow\"}, \"listing_ids\": [$IDS_TODOS]}"
  echo "aplicar que se postearia por bloque (500): mismo cuerpo + {\"huella\": \"<del plan>\", \"confirmados\": [], \"actor\": \"X.2 sembrar.sh\"}"
  echo "SIMULACRO OK (nada escrito)"
  exit 0
fi
[ -n "$IDS_TODOS" ] || { echo "sin unidades MX FBM sin goal: nada que sembrar"; exit 0; }

[ "$SIM" = 1 ] && [ -z "${ORBIT_SIM_TOKEN:-}" ] && { echo "ABORTA: ORBIT_SIM_TOKEN vacio (la simulacion real necesita un token local)"; exit 1; }
GUARDA_DIR=$([ "$SIM" = 1 ] && echo "${TMPDIR:-/tmp}/orbit-x2-sim" || echo "$DIR")
mkdir -p "$GUARDA_DIR"
PLAN_JSON="$GUARDA_DIR/plan-$STAMP.json"
SALIDA_TXT="$GUARDA_DIR/sembrar-$STAMP.txt"
PYSET=$(mktemp)
PYGOALS=$(mktemp)
trap 'rm -f $PYSET $PYGOALS' EXIT
cat > "$PYSET" <<'PYEOF'
import json, os, sys, urllib.request

BASE = os.environ["X_BASE"]
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
        "precio_modo_universo": {"amazon_mx/fbm": "shadow"}})
    if st == 409:
        sys.exit("ABORTA: 409 en settings (otro guardo primero); re-corre el script")
    if st != 200:
        sys.exit(f"ABORTA: settings respondio {st}: {json.dumps(r)[:500]}")
    print(f"universo puesto en shadow: config {r.get('config_version_id')} ({r.get('label')})")
else:
    print(f"universo ya en {MODO_UNIV}: sin settings previo")
PYEOF
cat > "$PYGOALS" <<'PYEOF'
import json, os, sys, urllib.request

BASE = os.environ["X_BASE"]
IDS = os.environ.get("X_IDS", "")
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

pedido = {"tipo": "margen_de_hoy", "mode": "shadow"}
cuerpo = {"platform": "amazon_mx", "pedido": pedido,
          "listing_ids": [int(x) for x in IDS.split(",")]}
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
    actor="X.2 sembrar.sh"))
if st == 409:
    nuevo = (r.get("detail") or {}).get("plan_nuevo") if isinstance(r.get("detail"), dict) else None
    if nuevo is None:
        sys.exit(f"ABORTA: 409 sin plan_nuevo: {json.dumps(r)[:500]}")
    print(f"409: la huella cambio; reintento una vez con {nuevo.get('huella')}")
    print(f"PLAN_JSON {json.dumps(nuevo, separators=(',', ':'))}")
    st, r = post("/api/precios/goals/aplicar", dict(
        cuerpo, huella=nuevo["huella"], confirmados=[],
        actor="X.2 sembrar.sh"))
if st != 200:
    sys.exit(f"ABORTA: aplicar respondio {st}: {json.dumps(r)[:500]}")
print(f"aplicado: sembrados={r.get('sembrados')} cerrados={r.get('cerrados')} lote={r.get('lote')}")
for o in r.get("omitidas") or []:
    print(f"OMITIDA listing={o.get('listing_id')} bloqueo={o.get('bloqueo')} motivo={o.get('motivo')}")
PYEOF

echo "== siembra real (guardas: plan + bloqueos en $PLAN_JSON)"
: > "$SALIDA_TXT"
PYPROG=$PYSET
IDS_CHUNK=""
corre_py 2>&1 | tee -a "$SALIDA_TXT" || { echo "ABORTA: settings previo fallo (ver $SALIDA_TXT)"; exit 1; }
PYPROG=$PYGOALS
BLOQUE=0
RESTO="$IDS_TODOS"
while [ -n "$RESTO" ]; do
  BLOQUE=$((BLOQUE + 1))
  IDS_CHUNK=$(printf '%s' "$RESTO" | tr ',' '\n' | head -n 500 | paste -sd ',' -)
  RESTO=$(printf '%s' "$RESTO" | tr ',' '\n' | tail -n +501 | paste -sd ',' -)
  echo "-- bloque $BLOQUE ($(printf '%s' "$IDS_CHUNK" | tr ',' '\n' | grep -c .) unidades)" | tee -a "$SALIDA_TXT"
  corre_py 2>&1 | tee -a "$SALIDA_TXT" || { echo "ABORTA: bloque $BLOQUE fallo (ver $SALIDA_TXT)"; exit 1; }
  PLAN_JSON_BLOQUE="$PLAN_JSON"
  [ "$BLOQUE" -gt 1 ] && PLAN_JSON_BLOQUE="${PLAN_JSON%.json}-b$BLOQUE.json"
  grep '^PLAN_JSON ' "$SALIDA_TXT" | tail -n 1 | sed 's/^PLAN_JSON //' > "$PLAN_JSON_BLOQUE" || { echo "ABORTA: sin PLAN_JSON en la salida"; exit 1; }
  echo "plan guardado en $PLAN_JSON_BLOQUE"
done
NBLOQ=$(grep -c '^BLOQUEO ' "$SALIDA_TXT" || true)
echo "unidades bloqueadas guardadas: $NBLOQ (con motivo, en $SALIDA_TXT)"
NOMIT=$(grep -c '^OMITIDA ' "$SALIDA_TXT" || true)
echo "unidades omitidas al aplicar: $NOMIT (con motivo, en $SALIDA_TXT)"
