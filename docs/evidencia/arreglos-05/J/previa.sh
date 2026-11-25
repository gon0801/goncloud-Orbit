#!/usr/bin/env bash
# Foto de solo lectura de produccion para el bloque J. No escribe nada.
# Uso: bash docs/evidencia/arreglos-05/J/previa.sh > docs/evidencia/arreglos-05/J/previa.txt
set -euo pipefail
cd "$(dirname -- "$0")/../../../.."

LEE=docs/evidencia/arreglos-01/lee_prod.sh

consulta() {
  local titulo=$1
  local sql=$2
  echo "## ${titulo}"
  printf '%s\n' "$sql" | bash "$LEE"
  echo
}

consulta "config vigente: modos, flags y economia (ultimas 3 versiones)" "
SELECT id, created_at,
       settings->>'ads_optimizer_mode' AS modo,
       settings->>'ads_bid_politica_amazon_mx' AS pol_mx,
       settings->>'ads_bid_politica_amazon_us' AS pol_us,
       settings->>'ads_producto_meta_amazon_mx' AS meta_mx,
       settings->>'ads_producto_meta_amazon_us' AS meta_us,
       settings->>'ads_producto_pausa_amazon_mx' AS pausa_mx,
       settings->>'ads_producto_pausa_amazon_us' AS pausa_us,
       settings->>'ads_producto_economia_amazon_mx' AS eco_mx,
       settings->>'ads_producto_economia_amazon_us' AS eco_us,
       settings->>'ads_pause_sin_cooldown_bid' AS sin_cd,
       settings->>'ads_bid_economia_amazon_mx' AS eco_cambio_mx,
       settings->>'ads_bid_economia_amazon_us' AS eco_cambio_us
FROM config_version ORDER BY id DESC LIMIT 3"

consulta "temporada vigente" "
SELECT coalesce(settings->>'ads_temporada_amazon_mx','(ausente)') AS temp_mx,
       coalesce(settings->>'ads_temporada_amazon_us','(ausente)') AS temp_us,
       coalesce(settings ? 'ads_temporada', false)::text AS tiene_calendario
FROM config_version ORDER BY id DESC LIMIT 1"

consulta "decisiones historicas por era, motor y kind" "
SELECT (inputs ? 'politica') AS con_politica,
       coalesce(inputs->>'motor', '(sin motor)') AS motor,
       kind, count(*) AS n,
       min(decided_at)::date AS desde, max(decided_at)::date AS hasta
FROM decision GROUP BY 1, 2, 3 ORDER BY 1, 2, 3"

consulta "procedencia del target en decisiones de hoja" "
SELECT coalesce(inputs->>'target_procedencia','(sin clave)') AS procedencia,
       kind, count(*) AS n,
       min(decided_at)::date AS desde, max(decided_at)::date AS hasta
FROM decision WHERE kind IN ('bid','pause')
GROUP BY 1, 2 ORDER BY 1, 2"

consulta "config historica de los interruptores del bloque" "
WITH cv AS (SELECT settings FROM config_version),
     cy AS (SELECT coalesce(notes::jsonb,'{}'::jsonb) AS n FROM optimizer_cycle)
SELECT 'config_meta_mx' AS dato, coalesce(settings->>'ads_producto_meta_amazon_mx','(ausente)') AS valor, count(*) AS n FROM cv GROUP BY 2
UNION ALL SELECT 'config_meta_us', coalesce(settings->>'ads_producto_meta_amazon_us','(ausente)'), count(*) FROM cv GROUP BY 2
UNION ALL SELECT 'config_pol_mx', coalesce(settings->>'ads_bid_politica_amazon_mx','(ausente)'), count(*) FROM cv GROUP BY 2
UNION ALL SELECT 'config_pol_us', coalesce(settings->>'ads_bid_politica_amazon_us','(ausente)'), count(*) FROM cv GROUP BY 2
UNION ALL SELECT 'config_eco_mx', coalesce(settings->>'ads_producto_economia_amazon_mx','(ausente)'), count(*) FROM cv GROUP BY 2
UNION ALL SELECT 'config_eco_us', coalesce(settings->>'ads_producto_economia_amazon_us','(ausente)'), count(*) FROM cv GROUP BY 2
UNION ALL SELECT 'config_pausa_mx', coalesce(settings->>'ads_producto_pausa_amazon_mx','(ausente)'), count(*) FROM cv GROUP BY 2
UNION ALL SELECT 'config_pausa_us', coalesce(settings->>'ads_producto_pausa_amazon_us','(ausente)'), count(*) FROM cv GROUP BY 2
UNION ALL SELECT 'config_pause_sin_cooldown', coalesce(settings->>'ads_pause_sin_cooldown_bid','(ausente)'), count(*) FROM cv GROUP BY 2
UNION ALL SELECT 'cycle_modo_meta', coalesce(n->'productos'->>'modo_meta','(sin productos)'), count(*) FROM cy GROUP BY 2
UNION ALL SELECT 'cycle_modo_pausa', coalesce(n->'productos'->>'modo_pausa','(sin productos)'), count(*) FROM cy GROUP BY 2
ORDER BY 1, 2"

consulta "pause historica sin politica: metadatos (la muestra usada como fixture)" "
SELECT id, cycle_id, ad_entity_id, decided_at
FROM decision WHERE kind = 'pause' AND NOT (inputs ? 'politica')
ORDER BY id DESC LIMIT 3"

consulta "decisiones de pausa de producto por modo congelado" "
SELECT coalesce(inputs->>'modo','(sin modo)') AS modo, count(*) AS n,
       min(decided_at)::date AS desde, max(decided_at)::date AS hasta
FROM decision WHERE inputs->>'motor' = 'producto'
GROUP BY 1 ORDER BY 1"
