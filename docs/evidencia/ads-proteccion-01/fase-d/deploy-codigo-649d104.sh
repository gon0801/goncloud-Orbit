#!/usr/bin/env bash
# Deploy SOLO de codigo: 2aa70cc -> 649d104 (lote de residuales de la Fase D, #365).
# Unico cambio de app/: api_dashboard.py (traduccion de inversion_sin_evidencia en /salud).
# Sin migraciones. Patron de docs/DEPLOY.md y de fase-d/deploy-fase-d.sh.
# Se corre desde el repo local: bash <este archivo>
set -euo pipefail

APROBADO=649d1044593885066c0121e374f463432a78761d
ANTERIOR=2aa70cc6fa583fe673fde512ae823141ca8efe18   # codigo en prod (deploy Fase D 28-sep 00:31)
REPO=/Users/dn/dev/goncloud-Orbit
SRV=/mnt/data/appdata/orbit
STAMP=$(date -u +%Y%m%d-%H%M)
cd "$REPO"

echo "== 1) SHA aprobado y CI verde sobre ese SHA"
git fetch -q origin
[ "$(git rev-parse origin/master)" = "$APROBADO" ] || { echo "ABORTA: origin/master ya no es $APROBADO"; exit 1; }
# Un run duplicado 'cancelled' (concurrencia de CI) no invalida el 'success'; 'failure' o en curso si.
CI=$(gh run list --commit "$APROBADO" --workflow quality.yml --json conclusion --jq '[.[].conclusion] | if length == 0 then "sin_run" elif any(. == "success") and all(. == "success" or . == "cancelled") then "success" else join(",") end')
[ "$CI" = "success" ] || { echo "ABORTA: CI de $APROBADO = $CI"; exit 1; }
echo "APROBADO=$APROBADO (CI success)"

echo "== 2) Preflight en prod (esperado: ok|0|t|0|0)"
R=$(ssh goncloud 'docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1 -tA' <<'SQL'
SELECT 'ok',
       (SELECT count(*) FROM optimizer_cycle WHERE status = 'running'),
       to_regclass('public.target_acos_ciclo') IS NOT NULL,
       -- DEPLOY.md D.1.0-2: no se despliega encima de un harvest a medias.
       (SELECT count(*) FROM apply_queue
         WHERE kind = 'harvest' AND estado NOT IN ('applied', 'failed', 'vetoed', 'discarded')),
       (SELECT count(*) FROM harvest_job
         WHERE fase IN ('pending', 'negative_created', 'exact_created', 'hermanas_negadas'));
SQL
)
echo "$R"
[ "$R" = "ok|0|t|0|0" ] || { echo "ABORTA: preflight no es ok|0|t|0|0 (ciclo corriendo, falta 0046 o harvest en vuelo)"; exit 1; }

echo "== 3) El codigo en prod es el esperado ($ANTERIOR)"
local_md5=$(git show "$ANTERIOR:app/api_dashboard.py" | md5 -q)
srv_md5=$(ssh goncloud "md5sum $SRV/app/api_dashboard.py" | cut -d' ' -f1)
[ "$local_md5" = "$srv_md5" ] || { echo "ABORTA: app/api_dashboard.py en prod no es el de $ANTERIOR"; exit 1; }
echo "prod = $ANTERIOR (md5 api_dashboard.py)"

echo "== 4) Respaldo del codigo actual"
ssh goncloud "set -e; cd $SRV; mkdir -p predeploy-$STAMP; \
  cp -a app Dockerfile .dockerignore pyproject.toml uv.lock tools predeploy-$STAMP/; ls -A predeploy-$STAMP"

echo "== 5) Copiar el codigo del SHA aprobado (git archive, LF)"
git archive --format=tar "$APROBADO" app Dockerfile .dockerignore pyproject.toml uv.lock tools/fabrica_campanas.py \
  | ssh goncloud "cd $SRV && tar -xf -"

echo "== 6) md5 server vs SHA aprobado"
for f in app/api_dashboard.py app/cycle.py app/apply.py app/apply_cola.py app/optimizer/goals.py; do
  local_md5=$(git show "$APROBADO:$f" | md5 -q)
  srv_md5=$(ssh goncloud "md5sum $SRV/$f" | cut -d' ' -f1)
  [ "$local_md5" = "$srv_md5" ] || { echo "ABORTA: md5 distinto en $f"; exit 1; }
  echo "md5 OK $f"
done

echo "== 7) Build + recrear app (digest antes/despues) + health + /salud"
ssh goncloud "set -e; cd $SRV; \
  echo DIGEST antes=\$(docker inspect -f '{{.Image}}' orbit-app-1); \
  docker compose up -d --no-deps --build app; \
  echo DIGEST despues=\$(docker inspect -f '{{.Image}}' orbit-app-1); \
  sleep 5; curl -sS http://127.0.0.1:8010/health; echo; \
  echo \"/salud HTTP \$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8010/salud)\"; \
  docker ps --filter name=orbit-app-1 --format '{{.Names}} {{.Status}}'"

echo "== LISTO. Reversa: codigo $SRV/predeploy-$STAMP/ + docker compose up -d --no-deps --build app"
