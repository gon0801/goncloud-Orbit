#!/usr/bin/env bash
# Reversa del despliegue 2.3: primero el codigo (para que la app deje de leer
# las tablas Jev), despues las reversas 0050 y 0049. La reversa 0049 aborta
# sola si ya hay fichas, revisiones o eventos: despues del primer dato real
# la salida es corregir hacia adelante, no reversar.
# Uso: cd ~/dev/goncloud-Orbit && bash docs/evidencia/jev-ads-01/ejecucion/2.3/rollback.sh <STAMP de desplegar.sh>
set -euo pipefail

STAMP=${1:?uso: rollback.sh <STAMP impreso por desplegar.sh>}
REPO=$(git rev-parse --show-toplevel)
SRV=/mnt/data/appdata/orbit
cd "$REPO"

echo "== 1) Restaurar el codigo de predeploy-$STAMP y reconstruir"
ssh goncloud "set -e; cd $SRV; [ -d predeploy-$STAMP/app ] || { echo 'ABORTA: no existe predeploy-$STAMP'; exit 1; }; \
  rm -rf app tools; cp -a predeploy-$STAMP/app predeploy-$STAMP/tools .; \
  cp -a predeploy-$STAMP/Dockerfile predeploy-$STAMP/.dockerignore predeploy-$STAMP/pyproject.toml predeploy-$STAMP/uv.lock .; \
  echo DIGEST antes=\$(docker inspect -f '{{.Image}}' orbit-app-1); \
  docker compose up -d --no-deps --build app; \
  echo DIGEST despues=\$(docker inspect -f '{{.Image}}' orbit-app-1); \
  sleep 5; curl -sS http://127.0.0.1:8010/health; echo"

echo "== 2) Reversa 0050 y despues 0049"
git show "origin/master:migrations/0050_reversa_jev_revision_created_at.sql" \
  | ssh goncloud 'docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1 -1'
git show "origin/master:migrations/0049_reversa_jev_ads.sql" \
  | ssh goncloud 'docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1 -1'

echo "== 3) Verificacion (esperado: f|0)"
R=$(ssh goncloud 'docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1 -tA' <<'SQL'
SELECT to_regclass('public.jev_revision') IS NOT NULL,
       (SELECT count(*) FROM pg_roles WHERE rolname = 'app_jev');
SQL
)
echo "$R"
[ "$R" = "f|0" ] || { echo "FALLA: quedaron restos de Jev"; exit 1; }
echo "== REVERSA LISTA. Esquema previo de respaldo: $SRV/backups/pre0049_0050_schema_$STAMP.sql"
