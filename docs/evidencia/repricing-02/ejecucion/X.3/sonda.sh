#!/usr/bin/env bash
# X.3: sonda de ESCRITURA de precio (criterio 3 del plan) sobre el
# conejillo (el primer goal shadow en amazon_us FBM con seller_sku)
# mas una publicacion de control (el segundo goal shadow). Corre
# tools/precio_sonda.py: mueve al conejillo un centavo sobre su
# precio vivo y lo revierte. Pasa si Amazon acepta ida y vuelta, la
# lectura posterior muestra el vivo y el control no cambia. El go
# literal lo escribe el script (`plan repricing-02 X.3`, plan:154);
# no se pide por bandera. Entre ida y vuelta el conejillo vale un
# centavo mas (segundos): corre fuera de los minutos del cron.
# Sin --acepto-mutacion-real: dry-run (plan + lectura del control,
# cero PATCH). Corre DENTRO de orbit-app-1 (`docker exec`;
# ORBIT_DSN_DECIDE ya vive ahi). En simulacion (ORBIT_SIMULACION=1)
# corre local con ORBIT_DSN_DECIDE=ORBIT_SIM_DSN y mercado de archivo
# ORBIT_SONDA_SIM (sembrado aqui si falta: 29.99/50.00 USD
# sinteticos, el P de la oferta + un control distinto).
# Uso: bash docs/evidencia/repricing-02/ejecucion/X.3/sonda.sh [--acepto-mutacion-real]
set -euo pipefail

MUTAR=0
case "${1:-}" in
  --acepto-mutacion-real) MUTAR=1 ;;
  "") ;;
  *) echo "uso: sonda.sh [--acepto-mutacion-real]"; exit 2 ;;
esac
[ $# -le 1 ] || { echo "uso: sonda.sh [--acepto-mutacion-real]"; exit 2; }
GO_FILA="plan repricing-02 X.3"
PLAT=amazon_us
REPO=$(git rev-parse --show-toplevel)
SIM=${ORBIT_SIMULACION:-0}
DSN_SIM=${ORBIT_SIM_DSN:-postgresql://orbit:orbit@localhost:5432/orbit_sim_X3}
SONDA_SIM=${ORBIT_SONDA_SIM:-${TMPDIR:-/tmp}/orbit-sonda-X3.json}
cd "$REPO"
PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY=python3

lee_sql() {  # lee_sql <sql>: solo lectura
  if [ "$SIM" = 1 ]; then
    psql "$DSN_SIM" -X -q -tA -v ON_ERROR_STOP=1 -c "$1"
  else
    ssh goncloud "DSN=\$(docker exec orbit-app-1 printenv ORBIT_DSN_READ); docker exec -i orbit-db-1 psql \"\$DSN\" -X -q -tA -v ON_ERROR_STOP=1 -c \"$1\""
  fi
}
lee_vivo() {  # lee_vivo <listing>: precio vivo por tools/precio_sonda.py
  if [ "$SIM" = 1 ]; then
    ORBIT_DSN_DECIDE="$DSN_SIM" ORBIT_SONDA_SIM="$SONDA_SIM" \
      "$PY" -m tools.precio_sonda --platform "$PLAT" --listing-id "$1" --lee-vivo --mercado-sim
  else
    # shellcheck disable=SC2029
    ssh goncloud "docker exec -i orbit-app-1 python -m tools.precio_sonda --platform $PLAT --listing-id $1 --lee-vivo"
  fi
}
corre_sonda() {  # corre_sonda <listing> <acepta:0|1>
  if [ "$SIM" = 1 ]; then
    if [ "$2" = 1 ]; then
      ORBIT_DSN_DECIDE="$DSN_SIM" ORBIT_SONDA_SIM="$SONDA_SIM" \
        "$PY" -m tools.precio_sonda --platform "$PLAT" --listing-id "$1" \
          --acepto-mutacion-real --go "$GO_FILA" --mercado-sim
    else
      ORBIT_DSN_DECIDE="$DSN_SIM" ORBIT_SONDA_SIM="$SONDA_SIM" \
        "$PY" -m tools.precio_sonda --platform "$PLAT" --listing-id "$1" --mercado-sim
    fi
  elif [ "$2" = 1 ]; then
    # shellcheck disable=SC2029
    ssh goncloud "docker exec -i orbit-app-1 python -m tools.precio_sonda --platform $PLAT --listing-id $1 --acepto-mutacion-real --go '$GO_FILA'"
  else
    # shellcheck disable=SC2029
    ssh goncloud "docker exec -i orbit-app-1 python -m tools.precio_sonda --platform $PLAT --listing-id $1"
  fi
}

echo "== X.3 sonda de escritura (US FBM, conejillo + control)"
IDS=$(lee_sql "SELECT v.listing_id FROM v_precio_goal_vigente v JOIN listing l ON l.id = v.listing_id AND l.platform = v.platform LEFT JOIN LATERAL (SELECT canal FROM estimacion_escenario e WHERE e.listing_id = v.listing_id AND e.platform = v.platform AND e.estado = 'disponible' ORDER BY e.observed_at DESC LIMIT 1) e ON true WHERE v.platform = 'amazon_us' AND v.mode = 'shadow' AND l.seller_sku IS NOT NULL AND (e.canal IS NULL OR e.canal = 'fbm') ORDER BY v.listing_id LIMIT 2;") || { echo "ABORTA: no se pudo leer los goals conejillo"; exit 1; }
CTL=$(printf '%s' "$IDS" | sed -n '1p')
TST=$(printf '%s' "$IDS" | sed -n '2p')
[ -n "$CTL" ] || { echo "FRENADO: sin goal shadow en amazon_us FBM (corre sembrar.sh primero)"; exit 1; }
[ -n "$TST" ] || { echo "FRENADO: sin publicacion de control (se necesitan 2 goals shadow en amazon_us FBM)"; exit 1; }
echo "conejillo: listing=$CTL (shadow); control: listing=$TST (shadow)"

if [ "$SIM" = 1 ]; then
  python3 - "$SONDA_SIM" "$CTL" 29.99 USD "$TST" 50.00 USD <<'PYEOF' || { echo "ABORTA: no se pudo sembrar $SONDA_SIM"; exit 1; }
import json, sys
ruta, faltan = sys.argv[1], []
try:
    with open(ruta, encoding="utf-8") as f:
        precios = json.load(f)
except (OSError, ValueError):
    precios = {}
for lid, valor, moneda in ((sys.argv[2], sys.argv[3], sys.argv[4]),
                            (sys.argv[5], sys.argv[6], sys.argv[7])):
    if lid not in precios:
        precios[lid] = {"valor": valor, "moneda": moneda}
        faltan.append(lid)
with open(ruta, "w", encoding="utf-8") as f:
    json.dump(precios, f, sort_keys=True)
print(f"SIMULACION: mercado sim en {ruta} (sembro {','.join(faltan) or 'nada nuevo'})")
PYEOF
fi

if [ "$MUTAR" = 0 ]; then
  echo "== SIMULACRO: dry-run del conejillo + lectura del control (cero PATCH)"
  corre_sonda "$CTL" 0 || { echo "ABORTA: el dry-run fallo"; exit 1; }
  lee_vivo "$TST" || { echo "ABORTA: no se pudo leer el control"; exit 1; }
  echo "SIMULACRO OK (nada escrito)"
  exit 0
fi

echo "== acepto: control antes, sonda del conejillo, lecturas despues"
V_CTL_ANTES=$(lee_vivo "$TST") || { echo "ABORTA: no se pudo leer el control antes"; exit 1; }
V_CON_ANTES=$(lee_vivo "$CTL") || { echo "ABORTA: no se pudo leer el conejillo antes"; exit 1; }
echo "antes: $V_CON_ANTES | $V_CTL_ANTES"
corre_sonda "$CTL" 1 || { echo "ABORTA: la sonda fallo (revisa si el precio quedo movido)"; exit 1; }
V_CON_DES=$(lee_vivo "$CTL") || { echo "ABORTA: no se pudo releer el conejillo"; exit 1; }
V_CTL_DES=$(lee_vivo "$TST") || { echo "ABORTA: no se pudo releer el control"; exit 1; }
echo "despues: $V_CON_DES | $V_CTL_DES"
[ "$V_CON_DES" = "$V_CON_ANTES" ] || { echo "ABORTA: el conejillo cambio ($V_CON_ANTES -> $V_CON_DES)"; exit 1; }
[ "$V_CTL_DES" = "$V_CTL_ANTES" ] || { echo "ABORTA: el control cambio ($V_CTL_ANTES -> $V_CTL_DES)"; exit 1; }
echo "== X.3 sonda OK (ida y vuelta aceptadas, vivo intacto, control intacto)"
