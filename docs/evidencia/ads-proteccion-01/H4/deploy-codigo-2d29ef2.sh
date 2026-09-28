#!/usr/bin/env bash
# Deploy de codigo 649d104 -> 2d29ef2 (A.3: un perfil de pais no soportado no abre
# fallo global, #369) + cierre manual del episodio 1 de ads_ingest_incident.
# Cambios de app/: app/ads/salud.py y app/ads/structure_api.py. Sin migraciones.
# El cierre va JUSTO despues del deploy: con el codigo nuevo, la siguiente ingesta
# (07:10 UTC) "recuperaria" el episodio 1 y mandaria un aviso de recuperacion falso.
# Se corre desde el repo local: bash <este archivo>
set -euo pipefail

APROBADO=2d29ef2ee1dad3158fcabd5186bed82e2ba55233
ANTERIOR=649d1044593885066c0121e374f463432a78761d   # codigo en prod (deploy 28-sep 02:39)
REPO=/Users/dn/dev/goncloud-Orbit
SRV=/mnt/data/appdata/orbit
STAMP=$(date -u +%Y%m%d-%H%M)
cd "$REPO"

echo "== 1) SHA aprobado y CI verde sobre ese SHA"
git fetch -q origin
[ "$(git rev-parse origin/master)" = "$APROBADO" ] || { echo "ABORTA: origin/master ya no es $APROBADO"; exit 1; }
# Un run duplicado 'cancelled' (concurrencia de CI) no invalida el 'success'; 'failure' o en curso si.
CI=$(gh run list --commit "$APROBADO" --workflow quality.yml --json conclusion --jq '[.[].conclusion] | if length == 0 then "sin_run" elif any(. == "success") and all(. == "success" or . == "cancelled") then "success" else join(",") end')
[ "$CI" = "success" ] || { echo "ABORTA: CI de $APROBADO = $CI (espera a que termine en verde)"; exit 1; }
echo "APROBADO=$APROBADO (CI success)"

echo "== 2) Preflight en prod (esperado: ok|0|0|0|0|1)"
# ciclo running | ingesta a medias (6 h) | harvest en cola | harvest_job en vuelo | episodio 1 abierto sin recuperar
R=$(ssh goncloud 'docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1 -tA' <<'SQL'
SELECT 'ok',
       (SELECT count(*) FROM optimizer_cycle WHERE status = 'running'),
       (SELECT count(*) FROM ingest_run WHERE finished_at IS NULL AND started_at > now() - interval '6 hours'),
       (SELECT count(*) FROM apply_queue
         WHERE kind = 'harvest' AND estado NOT IN ('applied', 'failed', 'vetoed', 'discarded')),
       (SELECT count(*) FROM harvest_job
         WHERE fase IN ('pending', 'negative_created', 'exact_created', 'hermanas_negadas')),
       (SELECT count(*) FROM ads_ingest_incident WHERE id = 1 AND closed_at IS NULL AND recovered_at IS NULL);
SQL
)
echo "$R"
[ "$R" = "ok|0|0|0|0|1" ] || { echo "ABORTA: preflight no es ok|0|0|0|0|1"; exit 1; }

echo "== 3) El codigo en prod es el esperado ($ANTERIOR)"
for f in app/ads/salud.py app/ads/structure_api.py; do
  [ "$(git show "$ANTERIOR:$f" | md5 -q)" = "$(ssh goncloud "md5sum $SRV/$f" | cut -d' ' -f1)" ] \
    || { echo "ABORTA: $f en prod no es el de $ANTERIOR"; exit 1; }
done
echo "prod = $ANTERIOR (md5 salud.py, structure_api.py)"

echo "== 4) Respaldo del codigo actual"
ssh goncloud "set -e; cd $SRV; mkdir -p predeploy-$STAMP; \
  cp -a app Dockerfile .dockerignore pyproject.toml uv.lock tools predeploy-$STAMP/; ls -A predeploy-$STAMP"

echo "== 5) Copiar el codigo del SHA aprobado (git archive, LF)"
git archive --format=tar "$APROBADO" app Dockerfile .dockerignore pyproject.toml uv.lock tools/fabrica_campanas.py \
  | ssh goncloud "cd $SRV && tar -xf -"

echo "== 6) md5 server vs SHA aprobado"
for f in app/ads/salud.py app/ads/structure_api.py app/api_dashboard.py app/cycle.py app/optimizer/goals.py; do
  [ "$(git show "$APROBADO:$f" | md5 -q)" = "$(ssh goncloud "md5sum $SRV/$f" | cut -d' ' -f1)" ] \
    || { echo "ABORTA: md5 distinto en $f"; exit 1; }
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

echo "== 8) Cierre manual del episodio 1 (sin recuperacion; cumple los CHECK de 0041)"
C=$(ssh goncloud 'docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1 -tA' <<'SQL'
UPDATE ads_ingest_incident SET closed_at = now()
 WHERE id = 1 AND closed_at IS NULL AND recovered_at IS NULL
RETURNING id;
SQL
)
C=$(echo "$C" | grep -x '[0-9]*' | head -1)
[ "$C" = "1" ] || { echo "ATENCION: el UPDATE no cerro el episodio 1 (salida: $C). Revisa antes de las 07:10 UTC."; exit 1; }
echo "episodio 1 cerrado"

echo "== 9) Readback: episodios abiertos (esperado: 0)"
ssh goncloud 'docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1 -tA' <<'SQL'
SELECT 'abiertos=' || count(*) FROM ads_ingest_incident WHERE closed_at IS NULL;
SELECT id, tipo, opened_run_id, alert_sent_at::timestamp(0), recovered_at, closed_at::timestamp(0)
  FROM ads_ingest_incident WHERE id = 1;
SQL

echo "== LISTO. Reversa del codigo: $SRV/predeploy-$STAMP/ + docker compose up -d --no-deps --build app"
