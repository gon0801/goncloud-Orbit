#!/usr/bin/env bash
# Inserta las politicas amazon_mx/fbm y amazon_us/fbm (REPRICING 02 F.3, se
# corre en D.3 DESPUES del deploy). Los settings copian la politica VIGENTE
# de amazon_mx/fba en el momento de correrlo, con los overrides de la tabla
# del plan (US: iva 1, incluye-iva no, isr 0.0207; ambas: semantica FBM).
# Idempotente por label: correrlo dos veces no duplica.
# Uso: ./politicas.sh <DSN_ADMIN>   (DSN con rol app_admin)
#   En goncloud: DSN=$(docker exec orbit-app-1 printenv ORBIT_DSN_ADMIN)
set -euo pipefail

DSN="${1:?Uso: politicas.sh <DSN_ADMIN>}"
PSQL=(psql "$DSN" -X -q -v ON_ERROR_STOP=1)

echo "== politica vigente de FBA (fuente de la copia)"
"${PSQL[@]}" -c "SELECT id, label, valid_from FROM estimacion_politica_version WHERE universo = 'amazon_mx/fba' AND valid_to IS NULL;"

echo "== inserta amazon_mx/fbm (si falta)"
"${PSQL[@]}" <<'SQL'
INSERT INTO estimacion_politica_version
    (label, universo, formula_version, settings, valid_from)
SELECT 'repricing02_fbm_mx', 'amazon_mx/fbm', 'S3',
       settings || '{"logistica_semantica": "L sale de precio_envio_muestra, no de esta constante"}'::jsonb,
       CURRENT_DATE
  FROM estimacion_politica_version
 WHERE universo = 'amazon_mx/fba' AND valid_to IS NULL
   AND NOT EXISTS (
           SELECT 1 FROM estimacion_politica_version WHERE label = 'repricing02_fbm_mx'
       );
SQL

echo "== inserta amazon_us/fbm (si falta)"
"${PSQL[@]}" <<'SQL'
INSERT INTO estimacion_politica_version
    (label, universo, formula_version, settings, valid_from)
SELECT 'repricing02_fbm_us', 'amazon_us/fbm', 'S3',
       settings || '{"iva_divisor": "1", "precio_incluye_iva": false, "isr_tasa": "0.0207",
                      "logistica_semantica": "L sale de precio_envio_muestra, no de esta constante"}'::jsonb,
       CURRENT_DATE
  FROM estimacion_politica_version
 WHERE universo = 'amazon_mx/fba' AND valid_to IS NULL
   AND NOT EXISTS (
           SELECT 1 FROM estimacion_politica_version WHERE label = 'repricing02_fbm_us'
       );
SQL

echo "== verifica"
"${PSQL[@]}" -c "SELECT id, label, universo, settings, valid_from, valid_to FROM estimacion_politica_version WHERE label LIKE 'repricing02_fbm%' ORDER BY id;"
