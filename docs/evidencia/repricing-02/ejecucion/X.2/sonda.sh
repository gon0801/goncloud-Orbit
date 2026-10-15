#!/usr/bin/env bash
# X.2: sonda de la CAMARA del go sobre un goal shadow de conejillo (el
# primero en amazon_mx FBM). Con el go que conoce el dueno lo voltea a
# live y de vuelta a shadow en la misma corrida (plan + aplicar G.2; la
# reversa va incluida: al final el conejillo vuelve a shadow). Prueba
# server-side que el go literal abre y que la reversa cierra, antes de
# encender FBM de adeveras.
# Sin --acepto-mutacion-real solo imprime lo que haria (cero POST).
# Corre DENTRO de orbit-app-1 con la forma de la sonda de M.0 (el python
# viaja por stdin de `docker exec -i`; el token se lee ahi de
# $ORBIT_SECRETS_DIR/api_write_token y jamas se imprime ni sale del
# servidor). En simulacion (ORBIT_SIMULACION=1) los POST van a
# ORBIT_SIM_API con ORBIT_SIM_TOKEN.
# Uso: bash docs/evidencia/repricing-02/ejecucion/X.2/sonda.sh [--acepto-mutacion-real] --go "<literal del dueno>"
set -euo pipefail

MUTAR=0
GO=""
while [ $# -gt 0 ]; do
  case "$1" in
    --acepto-mutacion-real) MUTAR=1; shift ;;
    --go) GO=${2:?uso: sonda.sh [--acepto-mutacion-real] --go "<literal>"}; shift 2 ;;
    *) echo "uso: sonda.sh [--acepto-mutacion-real] --go \"<literal>\""; exit 2 ;;
  esac
done
[ -n "${GO// /}" ] || { echo "uso: sonda.sh [--acepto-mutacion-real] --go \"<literal no vacio>\""; exit 2; }
REPO=$(git rev-parse --show-toplevel)
SIM=${ORBIT_SIMULACION:-0}
API_SIM=${ORBIT_SIM_API:-http://127.0.0.1:8013}
DSN_SIM=${ORBIT_SIM_DSN:-postgresql://orbit:orbit@localhost:5432/orbit_sim_X2}
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
      X_IDS="$IDS_CHUNK" X_MODO="$MODO" X_ACTOR="X.2 sonda.sh" \
      python3 "$PYPROG"
  else
    # shellcheck disable=SC2029
    ssh goncloud "export X_GO_B64='$(printf '%s' "$GO" | base64 -w0)' X_IDS='$IDS_CHUNK' X_MODO='$MODO' X_ACTOR='X.2 sonda.sh' X_BASE='http://127.0.0.1:8000'; docker exec -i -e X_GO_B64 -e X_IDS -e X_MODO -e X_ACTOR -e X_BASE orbit-app-1 python -" \
      < "$PYPROG"
  fi
}

echo "== X.2 sonda de la camara del go (MX FBM, conejillo)"
CTL=$(lee_sql "SELECT v.listing_id FROM v_precio_goal_vigente v LEFT JOIN LATERAL (SELECT canal FROM estimacion_escenario e WHERE e.listing_id = v.listing_id AND e.platform = v.platform AND e.estado = 'disponible' ORDER BY e.observed_at DESC LIMIT 1) e ON true WHERE v.platform = 'amazon_mx' AND v.mode = 'shadow' AND (e.canal IS NULL OR e.canal = 'fbm') ORDER BY v.listing_id LIMIT 1;") || { echo "ABORTA: no se pudo leer el goal conejillo"; exit 1; }
[ -n "$CTL" ] || { echo "FRENADO: sin goal shadow en amazon_mx FBM (corre sembrar.sh primero)"; exit 1; }
echo "conejillo: listing=$CTL (shadow)"

if [ "$MUTAR" = 0 ]; then
  echo "== SIMULACRO: sin --acepto-mutacion-real no se postea nada"
  echo "ida que se postearia: plan + aplicar {\"platform\": \"amazon_mx\", \"pedido\": {\"tipo\": \"modo\", \"mode\": \"live\"}, \"listing_ids\": [$CTL]} + go literal (no se imprime)"
  echo "vuelta que se postearia: plan + aplicar {\"platform\": \"amazon_mx\", \"pedido\": {\"tipo\": \"modo\", \"mode\": \"shadow\"}, \"listing_ids\": [$CTL]} (sin go)"
  echo "SIMULACRO OK (nada escrito)"
  exit 0
fi

[ "$SIM" = 1 ] && [ -z "${ORBIT_SIM_TOKEN:-}" ] && { echo "ABORTA: ORBIT_SIM_TOKEN vacio (la simulacion real necesita un token local)"; exit 1; }
PYPROG=$(mktemp)
trap 'rm -f $PYPROG' EXIT
cat > "$PYPROG" <<'PYEOF'
import base64, json, os, sys, urllib.request

BASE = os.environ["X_BASE"]
IDS = os.environ.get("X_IDS", "")
MODO = os.environ.get("X_MODO", "")
ACTOR = os.environ.get("X_ACTOR", "")
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

pedido = {"tipo": "modo", "mode": MODO}
cuerpo = {"platform": "amazon_mx", "pedido": pedido,
          "listing_ids": [int(x) for x in IDS.split(",")]}
st, plan = post("/api/precios/goals/plan", cuerpo)
if st != 200:
    sys.exit(f"ABORTA: plan respondio {st}: {json.dumps(plan)[:500]}")
filas = plan.get("filas", [])
bloq = [(f["listing_id"], f.get("bloqueo"), f.get("motivo")) for f in filas if f.get("bloqueo")]
print(f"plan {MODO}: {len(filas)} filas, {len(bloq)} bloqueadas, huella {plan.get('huella')}")
for lid, bl, mot in bloq:
    print(f"BLOQUEO listing={lid} bloqueo={bl} motivo={mot}")
extra = {"go_literal": GO} if MODO == "live" else {}
st, r = post("/api/precios/goals/aplicar", dict(
    cuerpo, huella=plan["huella"], confirmados=[], actor=ACTOR, **extra))
if st == 409:
    nuevo = (r.get("detail") or {}).get("plan_nuevo") if isinstance(r.get("detail"), dict) else None
    if nuevo is None:
        sys.exit(f"ABORTA: 409 sin plan_nuevo: {json.dumps(r)[:500]}")
    print(f"409: la huella cambio; reintento una vez con {nuevo.get('huella')}")
    st, r = post("/api/precios/goals/aplicar", dict(
        cuerpo, huella=nuevo["huella"], confirmados=[], actor=ACTOR, **extra))
if st != 200:
    sys.exit(f"ABORTA: aplicar respondio {st}: {json.dumps(r)[:500]}")
print(f"aplicado: sembrados={r.get('sembrados')} cerrados={r.get('cerrados')} lote={r.get('lote')}")
for o in r.get("omitidas") or []:
    print(f"OMITIDA listing={o.get('listing_id')} bloqueo={o.get('bloqueo')} motivo={o.get('motivo')}")
PYEOF

IDS_CHUNK="$CTL"
echo "== ida: conejillo a live (con el go del dueno)"
MODO=live
corre_py || { echo "ABORTA: la ida fallo (el go no abrio)"; exit 1; }
MODO_AHORA=$(lee_sql "SELECT mode FROM v_precio_goal_vigente WHERE platform = 'amazon_mx' AND listing_id = $CTL;")
[ "$MODO_AHORA" = "live" ] || { echo "ABORTA: el conejillo quedo en $MODO_AHORA (se esperaba live)"; exit 1; }
echo "ida comprobada: conejillo en live"
echo "== vuelta: conejillo de regreso a shadow (reversa incluida)"
MODO=shadow
corre_py || { echo "ABORTA: la vuelta fallo (el conejillo quedo en live: regresalo con aplicar modo/shadow)"; exit 1; }
MODO_AHORA=$(lee_sql "SELECT mode FROM v_precio_goal_vigente WHERE platform = 'amazon_mx' AND listing_id = $CTL;")
[ "$MODO_AHORA" = "shadow" ] || { echo "ABORTA: el conejillo quedo en $MODO_AHORA (se esperaba shadow)"; exit 1; }
echo "== X.2 sonda OK (el go abre y la reversa cierra)"
