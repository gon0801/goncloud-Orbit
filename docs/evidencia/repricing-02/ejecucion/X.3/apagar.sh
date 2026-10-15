#!/usr/bin/env bash
# X.3: reversa de encender.sh. Regresa amazon_us/fbm a shadow con
# POST /api/ads-optimizer/settings/amazon_us (solo la llave del universo;
# precio_modo_global NO se toca: otros universos pueden seguir vivos) y
# despues regresa los goals live a shadow en bloque con pedido
# modo/shadow (G.2, sin go: shadow es sin go literal). El universo
# primero (frena al motor) y luego los goals (restaura lo previo al
# encendido). Idempotente: lo que ya esta en shadow se reporta y no se
# reescribe (422 edicion vacia = exito si ya estaba).
# SIMULA POR OMISION: sin --acepto-mutacion-real solo imprime lo que
# haria (lecturas, cuerpos, seleccion) y sale 0 sin POSTear nada.
# Corre DENTRO de orbit-app-1 con la forma de la sonda de M.0 (el python
# viaja por stdin de `docker exec -i`; el token se lee ahi de
# $ORBIT_SECRETS_DIR/api_write_token y jamas se imprime ni sale del
# servidor). En simulacion (ORBIT_SIMULACION=1) los POST van a
# ORBIT_SIM_API con ORBIT_SIM_TOKEN.
# Uso: bash docs/evidencia/repricing-02/ejecucion/X.3/apagar.sh [--acepto-mutacion-real]
set -euo pipefail

MUTAR=0
if [ "${1:-}" = "--acepto-mutacion-real" ]; then MUTAR=1; shift; fi
[ $# -eq 0 ] || { echo "uso: apagar.sh [--acepto-mutacion-real]"; exit 2; }
REPO=$(git rev-parse --show-toplevel)
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
    X_BASE="$API_SIM" X_TOKEN="$ORBIT_SIM_TOKEN" \
      X_BASE_ID="$BASE_ID" X_UNIV="$UNIV" X_IDS="$IDS_CHUNK" \
      python3 "$PYPROG"
  else
    # shellcheck disable=SC2029
    ssh goncloud "export X_BASE_ID='$BASE_ID' X_UNIV='$UNIV' X_IDS='$IDS_CHUNK' X_BASE='http://127.0.0.1:8000'; docker exec -i -e X_BASE_ID -e X_UNIV -e X_IDS -e X_BASE orbit-app-1 python -" \
      < "$PYPROG"
  fi
}

echo "== X.3 apagar US FBM (reversa del encendido)"
GLOBAL=$(lee_sql "SELECT settings ->> 'precio_modo_global' FROM config_version ORDER BY id DESC LIMIT 1;") || { echo "ABORTA: no se pudo leer precio_modo_global"; exit 1; }
UNIV=$(lee_sql "SELECT settings -> 'precio_modo_universo' ->> 'amazon_us/fbm' FROM config_version ORDER BY id DESC LIMIT 1;") || { echo "ABORTA: no se pudo leer precio_modo_universo"; exit 1; }
BASE_ID=$(lee_sql "SELECT id FROM config_version ORDER BY id DESC LIMIT 1;") || { echo "ABORTA: no se pudo leer base_config_version_id"; exit 1; }
[ -n "$BASE_ID" ] || { echo "ABORTA: config_version sin filas"; exit 1; }
echo "precio_modo_global: ${GLOBAL:-<ausente>} (no se toca) | amazon_us/fbm: ${UNIV:-<sin entrada>} | base: $BASE_ID"
# C-W2: solo el universo US FBM (mismo filtro de canal que encender.sh:
# escenario `disponible` mas reciente; sin escenario entra y el plan lo
# bloquea, nunca se voltea a ciegas).
IDS_TODOS=$(lee_sql "SELECT string_agg(v.listing_id::text, ',' ORDER BY v.listing_id) FROM v_precio_goal_vigente v LEFT JOIN LATERAL (SELECT canal FROM estimacion_escenario e WHERE e.listing_id = v.listing_id AND e.platform = v.platform AND e.estado = 'disponible' ORDER BY e.observed_at DESC LIMIT 1) e ON true WHERE v.platform = 'amazon_us' AND v.mode = 'live' AND (e.canal IS NULL OR e.canal = 'fbm');") || { echo "ABORTA: no se pudo listar goals live"; exit 1; }
N_IDS=0
[ -n "$IDS_TODOS" ] && N_IDS=$(printf '%s' "$IDS_TODOS" | tr ',' '\n' | grep -c .)
echo "goals live en amazon_us FBM: $N_IDS"

if [ "$MUTAR" = 0 ]; then
  echo "== SIMULACRO: sin --acepto-mutacion-real no se postea nada"
  echo "settings que se postearia: {\"base_config_version_id\": $BASE_ID, \"precio_modo_universo\": {\"amazon_us/fbm\": \"shadow\"}}"
  if [ "$N_IDS" -gt 0 ]; then
    echo "goals que se regresarian a shadow en bloque ($N_IDS): $IDS_TODOS"
    echo "aplicar que se postearia por bloque: pedido {\"tipo\": \"modo\", \"mode\": \"shadow\"} + actor \"X.3 apagar.sh\" (sin go)"
  else
    echo "sin goals live que regresar: solo el universo"
  fi
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

st, r = post("/api/ads-optimizer/settings/amazon_us", {
    "base_config_version_id": int(BASE_ID),
    "precio_modo_universo": {"amazon_us/fbm": "shadow"}})
if st == 409:
    sys.exit("REINTENTAR_SETTINGS")
if st == 422 and "edicion vacia" in json.dumps(r):
    if UNIV == "shadow":
        print("settings: el universo ya estaba en shadow (edicion vacia idempotente)")
    else:
        sys.exit(f"ABORTA: edicion vacia inesperada (univ={UNIV}): {json.dumps(r)[:300]}")
elif st != 200:
    sys.exit(f"ABORTA: settings respondio {st}: {json.dumps(r)[:500]}")
else:
    if not r.get("created"):
        sys.exit(f"ABORTA: settings sin created:true: {json.dumps(r)[:300]}")
    print(f"settings: config {r.get('config_version_id')} ({r.get('label')})")
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

pedido = {"tipo": "modo", "mode": "shadow"}
cuerpo = {"platform": "amazon_us", "pedido": pedido,
          "listing_ids": [int(x) for x in IDS.split(",")]}
st, plan = post("/api/precios/goals/plan", cuerpo)
if st != 200:
    sys.exit(f"ABORTA: plan respondio {st}: {json.dumps(plan)[:500]}")
filas = plan.get("filas", [])
bloq = [(f["listing_id"], f.get("bloqueo"), f.get("motivo")) for f in filas if f.get("bloqueo")]
print(f"plan shadow: {len(filas)} filas, {len(bloq)} bloqueadas, huella {plan.get('huella')}")
for lid, bl, mot in bloq:
    print(f"BLOQUEO listing={lid} bloqueo={bl} motivo={mot}")
st, r = post("/api/precios/goals/aplicar", dict(
    cuerpo, huella=plan["huella"], confirmados=[],
    actor="X.3 apagar.sh"))
if st == 409:
    nuevo = (r.get("detail") or {}).get("plan_nuevo") if isinstance(r.get("detail"), dict) else None
    if nuevo is None:
        sys.exit(f"ABORTA: 409 sin plan_nuevo: {json.dumps(r)[:500]}")
    print(f"409: la huella cambio; reintento una vez con {nuevo.get('huella')}")
    st, r = post("/api/precios/goals/aplicar", dict(
        cuerpo, huella=nuevo["huella"], confirmados=[],
        actor="X.3 apagar.sh"))
if st != 200:
    sys.exit(f"ABORTA: aplicar respondio {st}: {json.dumps(r)[:500]}")
print(f"aplicado: sembrados={r.get('sembrados')} cerrados={r.get('cerrados')} lote={r.get('lote')}")
for o in r.get("omitidas") or []:
    print(f"OMITIDA listing={o.get('listing_id')} bloqueo={o.get('bloqueo')} motivo={o.get('motivo')}")
PYEOF

echo "== apagado real: universo a shadow (el global no se toca)"
PYPROG=$PYSET
IDS_CHUNK=""
for intento in 1 2; do
  if SALIDA_SET=$(corre_py 2>&1); then break; fi
  if [ "$intento" = 1 ] && printf '%s' "$SALIDA_SET" | grep -q REINTENTAR_SETTINGS; then
    echo "409 en settings: otro guardo primero; releo el id y repito una vez"
    BASE_ID=$(lee_sql "SELECT id FROM config_version ORDER BY id DESC LIMIT 1;") || { echo "ABORTA: no se pudo releer base_config_version_id"; exit 1; }
    UNIV=$(lee_sql "SELECT settings -> 'precio_modo_universo' ->> 'amazon_us/fbm' FROM config_version ORDER BY id DESC LIMIT 1;")
    continue
  fi
  printf '%s\n' "$SALIDA_SET"
  echo "ABORTA: settings fallo"
  exit 1
done
printf '%s\n' "$SALIDA_SET"

if [ "$N_IDS" -gt 0 ]; then
  echo "== apagado real: goals live a shadow en bloque ($N_IDS unidades)"
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
else
  echo "== sin goals live que regresar: solo el universo"
fi
echo "APAGADO OK: amazon_us/fbm en shadow"
