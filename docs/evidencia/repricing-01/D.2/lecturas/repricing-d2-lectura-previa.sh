#!/usr/bin/env bash
# REPRICING 01 D.2 — lectura previa al encendido (SOLO LECTURA, rol orbit_read).
set -euo pipefail
ssh goncloud 'bash -s' <<'REMOTO'
set -euo pipefail
DSN=$(docker exec orbit-app-1 printenv ORBIT_DSN_READ)
docker exec -i orbit-db-1 psql "$DSN" -X -P pager=off -v ON_ERROR_STOP=1 <<'SQL'
\echo '== 1. goals vigentes'
SELECT id, listing_id, platform, margen_goal_pct, mode, valid_from, go_literal IS NOT NULL AS tiene_go
FROM precio_goal WHERE valid_to IS NULL ORDER BY listing_id;

\echo '== 2. decisiones de los ultimos 8 dias (goals vigentes)'
SELECT d.decision_date, d.listing_id, d.mode, d.resultado, d.motivo,
       round(d.m_actual*100, 2) AS m_pct, round(d.goal*100, 2) AS goal_pct,
       d.p_actual, d.p_objetivo, d.u15, d.u60, d.buy_box_is_own, d.created_at
FROM precio_decision d
WHERE d.decision_date >= current_date - 8
  AND d.listing_id IN (SELECT listing_id FROM precio_goal WHERE valid_to IS NULL)
ORDER BY d.listing_id, d.decision_date;

\echo '== 3. precio_cambio (esperado: 6 filas de A.4, nada nuevo)'
SELECT id, listing_id, estado, es_reversa, enviado_at FROM precio_cambio ORDER BY id;

\echo '== 4. config precio_* vigente'
SELECT id, label, created_at FROM config_version ORDER BY id DESC LIMIT 1;
SELECT k, v FROM config_version c, jsonb_each(c.settings) AS e(k, v)
WHERE c.id = (SELECT max(id) FROM config_version) AND k LIKE 'precio%' ORDER BY k;
SQL
echo '== 5. cron de precio (debe salir 1 linea) y cola del log'
crontab -l | grep -c 'app.cli precio' || true
tail -n 12 /mnt/data/appdata/orbit/logs/precio-corrida.log
echo '== 6. contenedor'
docker inspect -f '{{.State.Status}} {{.Image}}' orbit-app-1
REMOTO
