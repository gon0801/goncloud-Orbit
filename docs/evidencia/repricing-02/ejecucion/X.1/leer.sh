#!/usr/bin/env bash
# X.1: lee la primera corrida del universo MX FBA (paso 2 de "Criterios
# de encendido", plans/repricing-02.md). SOLO LEE: no postea, no escribe,
# no necesita --acepto ni token. Mide, sobre el lote margen_de_hoy del
# universo (el mas reciente con unidades del canal, salvo --lote) y su
# primer dia con decisiones (salvo --fecha): (a) cobertura, (b) % en
# mantener(en_tolerancia) sobre las efectivas, (c) lista las fuera de
# tolerancia para su explicacion escrita (el punto (d), la cuenta a
# mano, la hace el lead).
# R05d: del denominador salen las unidades sin oferta viva (inventario 0
# Y sin precio propio observado el dia D, con las fuentes del motor: la
# puerta S2 es NOT EXISTS spapi_price_observation de metric_date = D con
# own_listing_price NOT NULL, como app/spapi/precio_mercado.py; el
# inventario es la ultima spapi_inventario_observation con metric_date <=
# D; sin fila no es 0, es sin dato y entra). Se listan como EXCLUIDA
# (solo ids internos: el repo es publico).
# Sale 0 al medir (una lectura "no cumple" tambien es una medicion
# lograda); el veredicto viaja en la linea LECTURA:. En simulacion
# (ORBIT_SIMULACION=1) lee de ORBIT_SIM_DSN.
# Uso: bash docs/evidencia/repricing-02/ejecucion/X.1/leer.sh [--fecha YYYY-MM-DD] [--lote <hex>]
set -euo pipefail

FECHA=""
LOTE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --fecha) FECHA=${2:?uso: leer.sh [--fecha YYYY-MM-DD] [--lote HEX]}; shift 2 ;;
    --lote) LOTE=${2:?uso: leer.sh [--fecha YYYY-MM-DD] [--lote HEX]}; shift 2 ;;
    *) echo "uso: leer.sh [--fecha YYYY-MM-DD] [--lote HEX]"; exit 2 ;;
  esac
done
case "$FECHA" in ''|[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]) ;; *) echo "uso: --fecha trae YYYY-MM-DD"; exit 2 ;; esac
case "$LOTE" in ''|*[!0-9a-fA-F]*) [ -z "$LOTE" ] || { echo "uso: --lote trae hex"; exit 2; } ;; esac
REPO=$(git rev-parse --show-toplevel)
SIM=${ORBIT_SIMULACION:-0}
DSN_SIM=${ORBIT_SIM_DSN:-postgresql://orbit:orbit@localhost:5432/orbit_sim_X1}
cd "$REPO"

lee_sql() {  # lee_sql <sql>: solo lectura
  if [ "$SIM" = 1 ]; then
    psql "$DSN_SIM" -X -q -tA -v ON_ERROR_STOP=1 -c "$1"
  else
    ssh goncloud "DSN=\$(docker exec orbit-app-1 printenv ORBIT_DSN_READ); docker exec -i orbit-db-1 psql \"\$DSN\" -X -q -tA -v ON_ERROR_STOP=1 -c \"$1\""
  fi
}

echo "== X.1 leer primera corrida (MX FBA, solo lectura)"
COMO_LOTE="--lote"
if [ -z "$LOTE" ]; then
  COMO_LOTE="defecto: mas reciente"
  LOTE=$(lee_sql "SELECT g.lote FROM precio_goal g LEFT JOIN LATERAL (SELECT canal FROM estimacion_escenario e WHERE e.listing_id = g.listing_id AND e.platform = g.platform AND e.estado = 'disponible' ORDER BY e.observed_at DESC LIMIT 1) e ON true WHERE g.platform = 'amazon_mx' AND g.origen = 'margen_de_hoy' AND (e.canal IS NULL OR e.canal = 'fba') ORDER BY g.created_at DESC, g.id DESC LIMIT 1;") || { echo "ABORTA: no se pudo leer el lote"; exit 1; }
  [ -n "$LOTE" ] || { echo "FRENADO: sin lote margen_de_hoy en amazon_mx/fba (nada sembrado: corre sembrar.sh primero)"; exit 1; }
fi
COMO_FECHA="--fecha"
if [ -z "$FECHA" ]; then
  COMO_FECHA="defecto: primer dia con decisiones"
  FECHA=$(lee_sql "SELECT min(d.decision_date)::text FROM precio_decision d WHERE d.platform = 'amazon_mx' AND d.listing_id IN (SELECT g.listing_id FROM precio_goal g WHERE g.platform = 'amazon_mx' AND g.origen = 'margen_de_hoy' AND g.lote = '$LOTE' AND g.valid_to IS NULL);") || { echo "ABORTA: no se pudo leer el dia"; exit 1; }
  [ -n "$FECHA" ] || { echo "FRENADO: el lote $LOTE no tiene decisiones (la corrida aun no corre)"; exit 1; }
fi
echo "lote: $LOTE ($COMO_LOTE) | fecha: $FECHA ($COMO_FECHA)"

CANAL_SQL="(e.canal IS NULL OR e.canal = 'fba')"
ESC_LATERAL="LEFT JOIN LATERAL (SELECT canal FROM estimacion_escenario e WHERE e.listing_id = g.listing_id AND e.platform = g.platform AND e.estado = 'disponible' ORDER BY e.observed_at DESC LIMIT 1) e ON true"
SEMB=$(lee_sql "SELECT count(*), count(*) FILTER (WHERE $CANAL_SQL), COALESCE(string_agg(g.listing_id::text, ',' ORDER BY g.listing_id) FILTER (WHERE $CANAL_SQL), '') FROM precio_goal g $ESC_LATERAL WHERE g.platform = 'amazon_mx' AND g.origen = 'margen_de_hoy' AND g.lote = '$LOTE' AND g.valid_to IS NULL;") || { echo "ABORTA: no se pudo leer sembradas"; exit 1; }
IFS='|' read -r R_CRUDO S_UNIV IDS_S <<< "$SEMB"
echo "sembradas lote (crudo): $R_CRUDO"
echo "sembradas en universo (canal fba): $S_UNIV"

EXC_CSV=""
if [ "$S_UNIV" -gt 0 ]; then
  EXC_CSV=$(lee_sql "SELECT COALESCE(string_agg(g.listing_id::text, ',' ORDER BY g.listing_id), '') FROM precio_goal g JOIN listing l ON l.id = g.listing_id AND l.platform = g.platform $ESC_LATERAL WHERE g.platform = 'amazon_mx' AND g.origen = 'margen_de_hoy' AND g.lote = '$LOTE' AND g.valid_to IS NULL AND $CANAL_SQL AND NOT EXISTS (SELECT 1 FROM spapi_price_observation o WHERE o.asin = l.external_id AND o.platform = 'amazon_mx' AND o.metric_date = '$FECHA' AND o.own_listing_price IS NOT NULL) AND COALESCE((SELECT i.total_quantity FROM spapi_inventario_observation i WHERE i.seller_sku = l.seller_sku AND i.platform = 'amazon_mx' AND i.metric_date <= '$FECHA' ORDER BY i.observed_at DESC LIMIT 1) = 0, false);") || { echo "ABORTA: no se pudo leer excluidas"; exit 1; }
fi
N_EXCL=0
IDS_D="$IDS_S"
if [ -n "$EXC_CSV" ]; then
  N_EXCL=$(printf '%s' "$EXC_CSV" | tr ',' '\n' | grep -c .)
  for x in $(printf '%s' "$EXC_CSV" | tr ',' ' '); do echo "EXCLUIDA listing=$x motivo=sin_oferta_viva"; done
  RESTO=""
  for x in $(printf '%s' "$IDS_S" | tr ',' ' '); do
    case ",$EXC_CSV," in *,"$x",*) ;; *) RESTO="${RESTO:+$RESTO,}$x" ;; esac
  done
  IDS_D="$RESTO"
fi
echo "excluidas sin oferta viva: $N_EXCL"
DD=$((S_UNIV - N_EXCL))
echo "efectivas (denominador): $DD"
[ -n "$IDS_D" ] && echo "efectivas: listing_ids [$IDS_D]"

N_TOL=0
N_SINDEC=0
FUERA=""
if [ "$DD" -gt 0 ]; then
  TOL=$(lee_sql "SELECT count(*) FILTER (WHERE d.resultado = 'mantener' AND d.motivo = 'en_tolerancia'), count(*) FILTER (WHERE d.listing_id IS NULL) FROM (SELECT unnest(string_to_array('$IDS_D', ',')::bigint[]) AS listing_id) s LEFT JOIN precio_decision d ON d.platform = 'amazon_mx' AND d.decision_date = '$FECHA' AND d.listing_id = s.listing_id;") || { echo "ABORTA: no se pudo leer tolerancia"; exit 1; }
  IFS='|' read -r N_TOL N_SINDEC <<< "$TOL"
  FUERA=$(lee_sql "SELECT s.listing_id::text || '|' || COALESCE(d.resultado, 'sin_decision') || '|' || COALESCE(d.motivo, '') FROM (SELECT unnest(string_to_array('$IDS_D', ',')::bigint[]) AS listing_id) s LEFT JOIN precio_decision d ON d.platform = 'amazon_mx' AND d.decision_date = '$FECHA' AND d.listing_id = s.listing_id WHERE d.resultado IS NULL OR NOT (d.resultado = 'mantener' AND d.motivo = 'en_tolerancia') ORDER BY s.listing_id;") || { echo "ABORTA: no se pudo leer fuera de tolerancia"; exit 1; }
fi
echo "decisiones mantener(en_tolerancia): $N_TOL"
echo "sin decision el $FECHA: $N_SINDEC"
if [ -n "$FUERA" ]; then
  while IFS='|' read -r lid res mot; do
    if [ "$res" = "sin_decision" ]; then echo "FUERA listing=$lid decision=sin_decision"; else echo "FUERA listing=$lid decision=$res|$mot"; fi
  done <<< "$FUERA"
fi
if [ "$DD" -gt 0 ]; then
  PCT=$(awk -v n="$N_TOL" -v d="$DD" 'BEGIN {printf "%.1f", 100*n/d}')
  echo "TOLERANCIA $N_TOL/$DD = $PCT % (criterio: >= 95 %)"
else
  echo "TOLERANCIA 0/0 (sin denominador)"
fi

COB=$(lee_sql "SELECT count(*), count(*) FILTER (WHERE EXISTS (SELECT 1 FROM precio_goal g WHERE g.platform = 'amazon_mx' AND g.origen = 'margen_de_hoy' AND g.lote = '$LOTE' AND g.valid_to IS NULL AND g.listing_id = l.id)) FROM listing l LEFT JOIN v_precio_goal_vigente v ON v.listing_id = l.id AND v.platform = l.platform LEFT JOIN LATERAL (SELECT canal FROM estimacion_escenario e WHERE e.listing_id = l.id AND e.platform = l.platform AND e.estado = 'disponible' ORDER BY e.observed_at DESC LIMIT 1) e ON true WHERE l.platform = 'amazon_mx' AND v.goal_id IS NULL AND $CANAL_SQL;") || { echo "ABORTA: no se pudo leer cobertura"; exit 1; }
IFS='|' read -r G_CAND T_TRAS <<< "$COB"
U_UNIV=$((S_UNIV + G_CAND))
echo "sin goal hoy: $G_CAND | universo: $U_UNIV | traslape lote-candidatas: $T_TRAS"
CUADRA=1
[ "$R_CRUDO" = "$S_UNIV" ] || CUADRA=0
[ "$T_TRAS" = "0" ] || CUADRA=0
if [ "$CUADRA" = 1 ]; then
  echo "COBERTURA: CUADRA ($S_UNIV sembradas + $G_CAND sin goal = $U_UNIV)"
else
  echo "COBERTURA: NO CUADRA (crudo $R_CRUDO vs universo $S_UNIV, traslape $T_TRAS)"
fi

if [ "$CUADRA" = 1 ] && [ "$DD" -gt 0 ] && awk -v n="$N_TOL" -v d="$DD" 'BEGIN {exit !(100.0*n/d >= 95)}'; then
  echo "LECTURA: cumple"
else
  MOT="cobertura no cuadra"
  [ "$CUADRA" = 1 ] && MOT="tolerancia bajo 95 %"
  [ "$DD" -gt 0 ] || MOT="sin denominador"
  echo "LECTURA: no cumple ($MOT)"
fi
