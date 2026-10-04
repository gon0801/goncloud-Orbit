#!/usr/bin/env bash
# Despliegue 2.3 de Jev Ads 01: 0049 + 0050 y el codigo del SHA aprobado, con
# el asesor APAGADO (ninguna tarea automatica lo llama; sin clave TypeSafe).
# Patron de docs/DEPLOY.md y de ads-proteccion-01/fase-d/deploy-fase-d.sh:
# preflight, backup de esquema, psql -1, permisos, respaldo de codigo,
# git archive, md5, digest antes/despues y health.
# Uso: cd ~/dev/goncloud-Orbit && bash docs/evidencia/jev-ads-01/ejecucion/2.3/desplegar.sh <sha-de-origin/master>
set -euo pipefail

APROBADO=${1:?uso: desplegar.sh <sha de origin/master con CI verde>}
REPO=$(git rev-parse --show-toplevel)
SRV=/mnt/data/appdata/orbit
STAMP=$(date -u +%Y%m%d-%H%M)
DIR=docs/evidencia/jev-ads-01/ejecucion/2.3
cd "$REPO"

echo "== 0) SHA aprobado = origin/master con la bateria completa verde"
git fetch -q origin
[ "$(git rev-parse origin/master)" = "$APROBADO" ] || { echo "ABORTA: origin/master ya no es $APROBADO"; exit 1; }
CI=$(gh run list --commit "$APROBADO" --event push --workflow Quality --json conclusion --jq '.[0].conclusion')
[ "$CI" = "success" ] || { echo "ABORTA: el run push de Quality sobre $APROBADO es '$CI', no success"; exit 1; }
echo "APROBADO=$APROBADO CI=$CI"

echo "== 1) Preflight (esperado ok|0|f|0|0: sin ciclo corriendo, sin tablas Jev, sin harvest en vuelo)"
R=$(ssh goncloud 'docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1 -tA' <<'SQL'
SELECT 'ok',
       (SELECT count(*) FROM optimizer_cycle WHERE status = 'running'),
       to_regclass('public.jev_revision') IS NOT NULL,
       (SELECT count(*) FROM apply_queue
         WHERE kind = 'harvest' AND estado NOT IN ('applied', 'failed', 'vetoed', 'discarded')),
       (SELECT count(*) FROM harvest_job
         WHERE fase IN ('pending', 'negative_created', 'exact_created', 'hermanas_negadas'));
SQL
)
echo "$R"
[ "$R" = "ok|0|f|0|0" ] || { echo "ABORTA: preflight no es ok|0|f|0|0"; exit 1; }

echo "== 2) Backup del esquema (staging + verificacion)"
ssh goncloud "set -e; D=$SRV/backups; TMP=\"\$D/.pre0049_0050_schema_$STAMP.sql.tmp\"; \
  docker exec orbit-db-1 pg_dump -U orbit -d orbit --schema-only > \"\$TMP\"; \
  [ -s \"\$TMP\" ] && grep -q 'CREATE TABLE public.decision ' \"\$TMP\" \
    && tail -5 \"\$TMP\" | grep -q 'PostgreSQL database dump complete' \
    || { echo 'DUMP INVALIDO'; rm -f \"\$TMP\"; exit 1; }; \
  chmod 600 \"\$TMP\"; mv \"\$TMP\" \"\$D/pre0049_0050_schema_$STAMP.sql\"; \
  ls -l \"\$D/pre0049_0050_schema_$STAMP.sql\""

echo "== 3) Migracion 0049 (catalogo, revisiones, eventos, app_jev)"
git show "$APROBADO:migrations/0049_jev_ads.sql" \
  | ssh goncloud 'docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1 -1'

echo "== 4) Migracion 0050 (created_at = insercion real)"
git show "$APROBADO:migrations/0050_jev_revision_created_at.sql" \
  | ssh goncloud 'docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1 -1'

echo "== 5) Permisos y esquema Jev (esperado: permisos OK)"
R=$(git show "$APROBADO:$DIR/permisos.sql" \
  | ssh goncloud 'docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1 -tA')
echo "$R"
[ "$R" = "permisos OK" ] || { echo "ABORTA antes del codigo: $R. Reversa: bash $DIR/rollback.sh $STAMP --solo-esquema"; exit 1; }

echo "== 6) Respaldo del codigo actual"
ssh goncloud "set -e; cd $SRV; mkdir -p predeploy-$STAMP; \
  cp -a app Dockerfile .dockerignore pyproject.toml uv.lock tools predeploy-$STAMP/; ls predeploy-$STAMP"

echo "== 7) Copiar el codigo del SHA aprobado (git archive, LF)"
git archive --format=tar "$APROBADO" app Dockerfile .dockerignore pyproject.toml uv.lock \
    tools/fabrica_campanas.py tools/jev_ads.py tools/jev_fichas.py \
  | ssh goncloud "cd $SRV && tar -xf -"

echo "== 8) md5 server vs SHA aprobado"
for f in Dockerfile app/jev_ads.py app/jev_catalogo.py app/jev_juicios.py app/api_dashboard.py \
         app/api_fabrica.py app/cycle.py tools/jev_ads.py tools/jev_fichas.py; do
  local_md5=$(git show "$APROBADO:$f" | md5 -q)
  srv_md5=$(ssh goncloud "md5sum $SRV/$f" | cut -d' ' -f1)
  [ "$local_md5" = "$srv_md5" ] || { echo "ABORTA: md5 distinto en $f"; exit 1; }
  echo "md5 OK $f"
done

echo "== 9) Build + recrear app (digest antes/despues) + health"
ssh goncloud "set -e; cd $SRV; \
  echo DIGEST antes=\$(docker inspect -f '{{.Image}}' orbit-app-1); \
  docker compose up -d --no-deps --build app; \
  echo DIGEST despues=\$(docker inspect -f '{{.Image}}' orbit-app-1); \
  sleep 5; curl -sS http://127.0.0.1:8010/health; echo; \
  docker ps --filter name=orbit-app-1 --format '{{.Names}} {{.Status}}'"

echo "== LISTO. STAMP=$STAMP"
echo "Siguiente: bash $DIR/checklist.sh $STAMP"
echo "Reversa:   bash $DIR/rollback.sh $STAMP"
