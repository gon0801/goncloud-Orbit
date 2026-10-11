#!/usr/bin/env bash
# Checklist post-deploy de D.2 (BIDS 02 seccion 3): SOLO LECTURA. Corre UNA
# vez despues de desplegar.sh, del sync de las 06:45 UTC y del reporte por
# placement de las 07:25 UTC, y deja su salida en un txt.
# Patron de salidas del de S.3: 0 = todo comprobado; 1 = alguna FALLA
# medida (un sync o reporte posterior con ok=false es FALLA nombrada, no
# INCOMPLETO: la reversa solo se dispara con 1); 3 = sin fallas pero
# todavia sin sync ni reporte posteriores al arranque (o sin reporte
# todavia); 4 = no pudo medir (el servidor no
# contesta o una lectura salio vacia, sin ninguna FALLA medida). Una FALLA
# medida sale con 1 aunque otra lectura quede vacia. "Posterior" cuenta desde
# el arranque del contenedor nuevo (docker inspect StartedAt), no desde el
# sello: el sello que toma como argumento es solo informativo. En
# simulacion (ORBIT_SIMULACION=1) el arranque lo da ORBIT_SIM_DESDE
# (timestamp ISO con zona), la base es ORBIT_SIM_DSN y el HTTP es
# ORBIT_SIM_API: no hay ssh.
# Comprueba (guia D.2): /health en 200; ninguna campana ENABLED sin fila en
# v_campana_config_vigente tras el sync; ads_placement_observation con filas
# tras el reporte; /donde-poner-el-dinero en 200 en MX y US; por_ubicacion y
# por_campana con filas en MX y US tras el reporte.
# Uso: bash docs/evidencia/bids-02/ejecucion/D.2/checklist.sh <STAMP> > checklist-salida.txt; echo "exit=$?"
set -uo pipefail

SELLO=${1:-sin-sello}
REPO=$(git rev-parse --show-toplevel)
DIR=docs/evidencia/bids-02/ejecucion/D.2
SIM=${ORBIT_SIMULACION:-0}
DSN_SIM=${ORBIT_SIM_DSN:-postgresql://orbit:orbit@127.0.0.1:5433/orbit_sim_d2}
API_SIM=${ORBIT_SIM_API:-http://127.0.0.1:8012}
FALLAS=0
PENDIENTE=0
NOMEDIDO=0
cd "$REPO"

revisa() {  # revisa <nombre> <esperado> <obtenido>
  if [ -z "$3" ]; then echo "NO MEDIDO $1 (lectura vacia)"; NOMEDIDO=$((NOMEDIDO + 1));
  elif [ "$2" = "$3" ]; then echo "OK    $1 = $3";
  else echo "FALLA $1: esperado '$2', obtenido '$3'"; FALLAS=$((FALLAS + 1)); fi
}
positivo() {  # positivo <nombre> <obtenido>: OK si es un entero > 0
  case "$2" in
    ''|*[!0-9]*) echo "NO MEDIDO $1 (lectura vacia)"; NOMEDIDO=$((NOMEDIDO + 1)) ;;
    0) echo "FALLA $1: esperado > 0, obtenido 0"; FALLAS=$((FALLAS + 1)) ;;
    *) echo "OK    $1 = $2" ;;
  esac
}
lee() {
  if [ "$SIM" = 1 ]; then
    psql "$DSN_SIM" -X -q -tA -v ON_ERROR_STOP=1
  else
    ssh goncloud 'DSN=$(docker exec orbit-app-1 printenv ORBIT_DSN_READ); docker exec -i orbit-db-1 psql "$DSN" -X -q -tA -v ON_ERROR_STOP=1'
  fi
}
http() {  # http <ruta> -> codigo
  if [ "$SIM" = 1 ]; then
    curl -s -o /dev/null -w '%{http_code}' "$API_SIM$1" || true
  else
    ssh goncloud "curl -s -o /dev/null -w %{http_code} 'http://127.0.0.1:8010$1'" || true
  fi
}
cuerpo() {  # cuerpo <ruta> -> body
  if [ "$SIM" = 1 ]; then
    curl -s "$API_SIM$1" || true
  else
    ssh goncloud "curl -s 'http://127.0.0.1:8010$1'" || true
  fi
}
dinero_filas() {  # lee stdin (JSON de donde-poner-el-dinero): "n_ubi n_camp"
  # por_ubicacion trae SIEMPRE 4 renglones (uno por ubicacion, con gasto
  # null sin datos): contar len() seria tautologico. Se cuentan filas con
  # gasto conocido; por_campana solo trae campanas con metricas.
  python3 -c "import json,sys
try: d = json.load(sys.stdin)
except Exception: print('ILEGIBLE -1'); raise SystemExit
ubi = d.get('por_ubicacion')
camp = d.get('por_campana')
if not isinstance(ubi, list) or not isinstance(camp, list): print('ILEGIBLE -1'); raise SystemExit
print(sum(1 for f in ubi if isinstance(f, dict) and f.get('gasto') is not None), len(camp))"
}

echo "== 0) El servidor contesta"
if [ "$SIM" = 1 ]; then
  echo "MODO SIMULACION: base $DSN_SIM, HTTP $API_SIM"
  DESDE=${ORBIT_SIM_DESDE:-}
  if [ -z "$DESDE" ]; then echo "NO MEDIDO ORBIT_SIM_DESDE vacia (el arranque simulado es obligatorio)"; exit 4; fi
  H=$(http /health)
  if [ -z "$H" ]; then echo "NO MEDIDO la app local no contesta"; exit 4; fi
  echo "arranque simulado: $DESDE"
else
  RUNNING=$(ssh goncloud "docker inspect -f '{{.State.Running}}' orbit-app-1" || true)
  if [ -z "$RUNNING" ]; then echo "NO MEDIDO el servidor no contesta (docker inspect vacio)"; exit 4; fi
  revisa "orbit-app-1 corriendo" true "$RUNNING"
  DESDE=$(ssh goncloud "docker inspect -f '{{.State.StartedAt}}' orbit-app-1" || true)
  if [ -z "$DESDE" ]; then echo "NO MEDIDO arranque del contenedor (StartedAt vacio)"; NOMEDIDO=$((NOMEDIDO + 1)); fi
fi

echo "== Checklist D.2 (solo lectura; sello $SELLO, contenedor desde ${DESDE:-sin-dato})"

echo "== 1) HTTP: /health"
revisa "GET /health" 200 "$(http /health)"

echo "== 2) Esquema de D.2 (esperado t|t|t)"
revisa "vista config vigente y tablas 0061+0062" "t|t|t" \
  "$(echo "SELECT to_regclass('public.v_campana_config_vigente') IS NOT NULL, to_regclass('public.ads_campana_config_observation') IS NOT NULL, to_regclass('public.ads_placement_observation') IS NOT NULL;" | lee || true)"

SYNC_OK=0
REPORTE_OK=0
if [ -z "${DESDE:-}" ]; then
  echo "NO MEDIDO gates temporales (sin arranque del contenedor)"
  NOMEDIDO=$((NOMEDIDO + 1))
else
  echo "== 3a) Sync de las 06:45 posterior al arranque (sale 3 si todavia no corre)"
  # El gate es la CORRIDA (ingest_run ok), no las filas: guarda_config solo
  # inserta cuando algo cambio, asi que un dia sin cambios dejaria este gate
  # en INCOMPLETO para siempre. El conteo de filas es informativo.
  SYNC_N=$(echo "SELECT count(*) FROM ingest_run WHERE source = 'amazon_ads_structure_v2' AND started_at > '$DESDE' AND ok;" | lee || true)
  if [ -z "$SYNC_N" ]; then
    echo "NO MEDIDO sync posterior (lectura vacia)"; NOMEDIDO=$((NOMEDIDO + 1))
  elif [ "$SYNC_N" = "0" ]; then
    SYNC_FAIL_N=$(echo "SELECT count(*) FROM ingest_run WHERE source = 'amazon_ads_structure_v2' AND started_at > '$DESDE' AND NOT ok;" | lee || true)
    if [ -z "$SYNC_FAIL_N" ]; then
      echo "NO MEDIDO syncs fallidos posteriores (lectura vacia)"; NOMEDIDO=$((NOMEDIDO + 1))
    elif [ "$SYNC_FAIL_N" != "0" ]; then
      SYNC_MOTIVO=$(echo "SELECT coalesce(skip_reason, '(sin motivo)') FROM ingest_run WHERE source = 'amazon_ads_structure_v2' AND started_at > '$DESDE' AND NOT ok ORDER BY started_at DESC LIMIT 1;" | lee || true)
      echo "FALLA sync de las 06:45 posterior a $DESDE: $SYNC_FAIL_N corrida(s) con ok=false (motivo: ${SYNC_MOTIVO:-sin-dato}); la reversa aplica"
      FALLAS=$((FALLAS + 1))
    else
      echo "INCOMPLETO: el sync de las 06:45 UTC todavia no corre despues de $DESDE; vuelve a correr el checklist despues"
      PENDIENTE=1
    fi
  else
    echo "OK    syncs ok posteriores al arranque = $SYNC_N"
    echo "      config posterior al arranque = $(echo "SELECT count(*) FROM ads_campana_config_observation WHERE observed_at > '$DESDE';" | lee || true) fila(s) (informativo)"
    SYNC_OK=1
  fi
fi

if [ "$SYNC_OK" = 1 ]; then
  echo "== 3b) Ninguna campana ENABLED sin fila en v_campana_config_vigente (esperado 0)"
  for MERCADO in amazon_mx amazon_us; do
    N_SIN=$(echo "SELECT count(*) FROM ad_entity c JOIN ad_entity_state s ON s.ad_entity_id = c.id AND s.status = 'ENABLED' WHERE c.kind = 'campaign' AND c.platform = '$MERCADO' AND NOT EXISTS (SELECT 1 FROM v_campana_config_vigente v WHERE v.ad_entity_id = c.id);" | lee || true)
    # Si hay huerfanas, se listan: la ingesta skipea payloads ilegibles por
    # diseno, asi que el lead distingue un deploy roto de skips conocidos.
    if [ -n "$N_SIN" ] && [ "$N_SIN" != "0" ]; then
      echo "--- $MERCADO sin config (external_id, max 10):"
      echo "SELECT c.external_id FROM ad_entity c JOIN ad_entity_state s ON s.ad_entity_id = c.id AND s.status = 'ENABLED' WHERE c.kind = 'campaign' AND c.platform = '$MERCADO' AND NOT EXISTS (SELECT 1 FROM v_campana_config_vigente v WHERE v.ad_entity_id = c.id) ORDER BY 1 LIMIT 10;" | lee || true
    fi
    revisa "ENABLED sin config $MERCADO" 0 "$N_SIN"
  done
fi

if [ -n "${DESDE:-}" ]; then
  echo "== 3c) Reporte por placement de las 07:25 posterior al arranque (sale 3 si todavia no corre)"
  REP_N=$(echo "SELECT count(*) FROM ads_placement_observation WHERE observed_at > '$DESDE';" | lee || true)
  if [ -z "$REP_N" ]; then
    echo "NO MEDIDO placements posteriores (lectura vacia)"; NOMEDIDO=$((NOMEDIDO + 1))
  elif [ "$REP_N" = "0" ]; then
    REP_FAIL_N=$(echo "SELECT count(*) FROM ingest_run WHERE source = 'amazon_ads_placements_v3' AND started_at > '$DESDE' AND NOT ok;" | lee || true)
    if [ -z "$REP_FAIL_N" ]; then
      echo "NO MEDIDO reportes fallidos posteriores (lectura vacia)"; NOMEDIDO=$((NOMEDIDO + 1))
    elif [ "$REP_FAIL_N" != "0" ]; then
      REP_MOTIVO=$(echo "SELECT coalesce(skip_reason, '(sin motivo)') FROM ingest_run WHERE source = 'amazon_ads_placements_v3' AND started_at > '$DESDE' AND NOT ok ORDER BY started_at DESC LIMIT 1;" | lee || true)
      echo "FALLA reporte de las 07:25 posterior a $DESDE: $REP_FAIL_N corrida(s) con ok=false (motivo: ${REP_MOTIVO:-sin-dato}); la reversa aplica"
      FALLAS=$((FALLAS + 1))
    else
      echo "INCOMPLETO: el reporte de las 07:25 UTC todavia no corre despues de $DESDE; vuelve a correr el checklist despues"
      PENDIENTE=1
    fi
  else
    echo "OK    placements posteriores al arranque = $REP_N fila(s)"
    REPORTE_OK=1
  fi
fi

if [ "$REPORTE_OK" = 1 ]; then
  echo "== 3d) ads_placement_observation tiene filas (esperado > 0)"
  positivo "placements totales" "$(echo "SELECT count(*) FROM ads_placement_observation;" | lee || true)"
fi

echo "== 4) La pantalla de dinero en los dos mercados"
for MERCADO in amazon_mx amazon_us; do
  revisa "HTML donde-poner-el-dinero $MERCADO" 200 "$(http "/donde-poner-el-dinero?plataforma=$MERCADO")"
  if [ "$REPORTE_OK" = 1 ]; then
    DINERO=$(cuerpo "/api/dashboard/donde-poner-el-dinero?plataforma=$MERCADO" || true)
    if [ -z "$DINERO" ]; then
      echo "NO MEDIDO donde-poner-el-dinero $MERCADO (respuesta vacia)"; NOMEDIDO=$((NOMEDIDO + 1))
    else
      FILAS=$(echo "$DINERO" | dinero_filas || true)
      N_UBI=$(printf '%s' "$FILAS" | cut -d' ' -f1)
      N_CAMP=$(printf '%s' "$FILAS" | cut -d' ' -f2)
      positivo "por_ubicacion con gasto $MERCADO" "$N_UBI"
      positivo "por_campana con filas $MERCADO" "$N_CAMP"
    fi
  else
    echo "INCOMPLETO: por_ubicacion y por_campana de $MERCADO se miden despues del reporte de las 07:25 UTC"
    PENDIENTE=1
  fi
done

echo "== RESULTADO: $FALLAS falla(s), $NOMEDIDO sin medir, sync/reporte posterior $([ "$PENDIENTE" = 1 ] && echo PENDIENTE || echo comprobado)"
[ "$FALLAS" -eq 0 ] || exit 1
[ "$NOMEDIDO" = 0 ] || exit 4
[ "$PENDIENTE" = 0 ] || exit 3
