#!/usr/bin/env bash
# D.4: inserta la politica `meli/meli` (REPRICING 02, carril M).
# Fuente: la FBA vigente (los demas campos de `settings` la copian),
# con iva 1.16, precio con IVA, `isr_tasa` 0.025 (medido M.2 por orden
# sobre ingreso neto) y envio de la muestra (`logistica` 0 +
# semantica). Idempotente por label: no repite una vigente (misma
# regla que las FBM de D.3, que van dentro de su desplegar.sh paso
# 4b; esta va APARTE porque el plan lo pide: `desplegar.sh` de D.4
# corre este script en su paso 4b). Por su `w()` a orbit-db-1: en
# goncloud no hay psql en el host ni en orbit-app-1 (medido en D.3).
# En simulacion (ORBIT_SIMULACION=1) la base es ORBIT_SIM_DSN.
# Uso: bash docs/evidencia/repricing-02/ejecucion/D.4/politicas.sh
set -euo pipefail

REPO=$(git rev-parse --show-toplevel)
SIM=${ORBIT_SIMULACION:-0}
DSN_SIM=${ORBIT_SIM_DSN:-postgresql://orbit:orbit@localhost:5432/orbit_sim_D4}
# C-W6: el DSN solo se muestra enmascarado (`orbit:***@`); psql sigue
# usando $DSN_SIM real (la conexion no cambia).
DSN_VER=$(printf '%s' "$DSN_SIM" | sed 's|://[^@]*@|://orbit:***@|')
cd "$REPO"

q() {
  if [ "$SIM" = 1 ]; then
    psql "$DSN_SIM" -X -q -tA -v ON_ERROR_STOP=1
  else
    ssh goncloud 'DSN=$(docker exec orbit-app-1 printenv ORBIT_DSN_READ); docker exec -i orbit-db-1 psql "$DSN" -X -q -tA -v ON_ERROR_STOP=1'
  fi
}
w() {
  if [ "$SIM" = 1 ]; then
    psql "$DSN_SIM" -q -v ON_ERROR_STOP=1 -1
  else
    ssh goncloud 'docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1 -1'
  fi
}

if [ "$SIM" = 1 ]; then
  echo "== MODO SIMULACION: datos sinteticos contra $DSN_VER, servidor intacto"
fi

echo "== 1) Fuente: la FBA vigente (exactamente 1)"
echo "SELECT id, label, valid_from FROM estimacion_politica_version WHERE universo = 'amazon_mx/fba' AND valid_to IS NULL;" | q
FUENTE=$(echo "SELECT count(*) FROM estimacion_politica_version WHERE universo = 'amazon_mx/fba' AND valid_to IS NULL;" | q)
[ "$FUENTE" = 1 ] || { echo "ABORTA: ve $FUENTE FBA vigentes, esperaba 1 (fuente ambigua)"; exit 1; }

echo "== 2) Inserta repricing02_meli (idempotente por label)"
w <<'SQL'
INSERT INTO estimacion_politica_version
    (label, universo, formula_version, settings, valid_from)
SELECT 'repricing02_meli', 'meli/meli', 'S3',
       settings
       || '{"iva_divisor": "1.16"}'::jsonb
       || '{"precio_incluye_iva": true}'::jsonb
       || '{"isr_tasa": "0.025"}'::jsonb
       || '{"logistica": "0"}'::jsonb
       || '{"logistica_semantica": "L sale de precio_envio_muestra, no de esta constante"}'::jsonb,
       CURRENT_DATE
  FROM estimacion_politica_version
 WHERE universo = 'amazon_mx/fba' AND valid_to IS NULL
   AND NOT EXISTS (
           SELECT 1 FROM estimacion_politica_version WHERE label = 'repricing02_meli'
       );
SQL
echo "SELECT id, label, universo, valid_from, valid_to, settings ->> 'isr_tasa' AS isr FROM estimacion_politica_version WHERE label = 'repricing02_meli' ORDER BY id;" | q

echo "== 3) Verifica: exactamente 1 vigente"
CUENTA=$(echo "SELECT count(*) FROM estimacion_politica_version WHERE label = 'repricing02_meli' AND valid_to IS NULL;" | q)
[ "$CUENTA" = 1 ] || { echo "ABORTA: deja $CUENTA politicas meli vigentes, esperaba 1"; exit 1; }
echo "POLITICA OK: repricing02_meli vigente (1)"
