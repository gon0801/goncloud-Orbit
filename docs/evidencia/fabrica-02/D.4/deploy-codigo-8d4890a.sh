#!/usr/bin/env bash
# Deploy de codigo 2d29ef2 -> 8d4890a (app/ identico a edd154e) (FABRICA 02 D.4, #378: candado origen_es_destino en
# excepcion/terna + defensa en apply/revalida). Sin migraciones, sin cambios de config.
# Cambios de app/: apply_harvest.py, apply_harvest_reconciliacion.py, cycle.py,
# optimizer/harvest_destino.py, api_dashboard.py. tools/reversa_harvest.py entra por stdin
# (no va en la imagen). Lo corre el dueno: bash <este archivo>
set -euo pipefail

APROBADO=8d4890adc442d4405d82d8a36707c743cf8a9bcc   # = edd154e (#378) + #379 (solo plans/manifest.json)
ANTERIOR=2d29ef2ee1dad3158fcabd5186bed82e2ba55233   # app/ en prod = 2d29ef2 (md5 122/122, lectura del lead 2026-10-02)
REPO=/Users/dn/dev/goncloud-Orbit
SRV=/mnt/data/appdata/orbit
STAMP=$(date -u +%Y%m%d-%H%M)
CAMBIADOS="app/apply_harvest.py app/apply_harvest_reconciliacion.py app/cycle.py app/optimizer/harvest_destino.py app/api_dashboard.py"
cd "$REPO"

echo "== 1) SHA aprobado y CI verde sobre ese SHA"
git fetch -q origin
[ "$(git rev-parse origin/master)" = "$APROBADO" ] || { echo "ABORTA: origin/master ya no es $APROBADO"; exit 1; }
CI=$(gh run list --commit "$APROBADO" --workflow quality.yml --json conclusion --jq '[.[].conclusion] | if length == 0 then "sin_run" elif any(. == "success") and all(. == "success" or . == "cancelled") then "success" else join(",") end')
[ "$CI" = "success" ] || { echo "ABORTA: CI de $APROBADO = $CI (espera a que termine en verde)"; exit 1; }
echo "APROBADO=$APROBADO (CI success)"

echo "== 2) Preflight en prod, orbit_read (esperado: ok|0|0|0|0)"
# ciclo running | ingesta a medias (6 h) | harvest released/applying | harvest_job en vuelo
# (las filas pending_veto 17/18 NO bloquean: se aplican en el ciclo con el codigo nuevo, que revalida origen)
R=$(ssh goncloud 'DSN=$(docker exec orbit-app-1 printenv ORBIT_DSN_READ); docker exec -i orbit-db-1 psql "$DSN" -X -v ON_ERROR_STOP=1 -tA' <<'SQL'
SELECT 'ok',
       (SELECT count(*) FROM optimizer_cycle WHERE status = 'running'),
       (SELECT count(*) FROM ingest_run WHERE finished_at IS NULL AND started_at > now() - interval '6 hours'),
       (SELECT count(*) FROM apply_queue WHERE kind = 'harvest' AND estado IN ('released', 'applying')),
       (SELECT count(*) FROM harvest_job
         WHERE fase IN ('pending', 'negative_created', 'exact_created', 'hermanas_negadas'));
SQL
)
echo "$R"
[ "$R" = "ok|0|0|0|0" ] || { echo "ABORTA: preflight no es ok|0|0|0|0"; exit 1; }

echo "== 3) El codigo en prod es el esperado ($ANTERIOR)"
for f in $CAMBIADOS; do
  [ "$(git show "$ANTERIOR:$f" | md5 -q)" = "$(ssh goncloud "md5sum $SRV/$f" | cut -d' ' -f1)" ] \
    || { echo "ABORTA: $f en prod no es el de $ANTERIOR"; exit 1; }
done
echo "prod = $ANTERIOR en los 5 archivos que cambian"

echo "== 4) Respaldo del codigo actual"
ssh goncloud "set -e; cd $SRV; mkdir -p predeploy-$STAMP; \
  cp -a app Dockerfile .dockerignore pyproject.toml uv.lock tools predeploy-$STAMP/; ls -A predeploy-$STAMP"

echo "== 5) Copiar el codigo del SHA aprobado (git archive)"
git archive --format=tar "$APROBADO" app Dockerfile .dockerignore pyproject.toml uv.lock tools/fabrica_campanas.py \
  | ssh goncloud "cd $SRV && tar -xf -"

echo "== 6) md5 server vs SHA aprobado"
for f in $CAMBIADOS; do
  [ "$(git show "$APROBADO:$f" | md5 -q)" = "$(ssh goncloud "md5sum $SRV/$f" | cut -d' ' -f1)" ] \
    || { echo "ABORTA: md5 distinto en $f"; exit 1; }
  echo "md5 OK $f"
done

echo "== 7) Build + recrear app (digest antes/despues) + health + /salud + /cortes"
ssh goncloud "set -e; cd $SRV; \
  echo DIGEST antes=\$(docker inspect -f '{{.Image}}' orbit-app-1); \
  docker compose up -d --no-deps --build app; \
  echo DIGEST despues=\$(docker inspect -f '{{.Image}}' orbit-app-1); \
  sleep 5; curl -sS http://127.0.0.1:8010/health; echo; \
  echo \"/salud HTTP \$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8010/salud)\"; \
  echo \"/cortes HTTP \$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8010/cortes)\"; \
  docker ps --filter name=orbit-app-1 --format '{{.Names}} {{.Status}}'"

echo "== 8) Smoke dentro del contenedor: el codigo nuevo esta cargado (solo import, cero IO)"
ssh goncloud "docker exec orbit-app-1 python -c 'import inspect; from app.optimizer import harvest_destino as h; from app.apply_harvest import plan_reversa_origen_harvest; print(\"origen_ad_group_external\" in inspect.signature(h.resolver_destino).parameters, callable(plan_reversa_origen_harvest))'"
echo "(esperado: True True)"

echo "== LISTO. Reversa del codigo: $SRV/predeploy-$STAMP/ + docker compose up -d --no-deps --build app"
