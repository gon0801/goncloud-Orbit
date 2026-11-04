#!/usr/bin/env bash
# Reversa del despliegue D.1: primero el codigo + docker-compose.yml de
# predeploy-<STAMP>, despues la reversa de 0074. La reversa aborta sola si
# ya hay veredictos o efectos: despues del primer dato real la salida es
# Quitar, no DROP. Patron de jev-ads-02/ejecucion/S.3/rollback.sh.
# Uso: cd <repo> && bash docs/evidencia/jev-ads-03/ejecucion/D.1/rollback.sh <STAMP> [--solo-esquema]
set -euo pipefail

STAMP=${1:?uso: rollback.sh <STAMP impreso por desplegar.sh> [--solo-esquema]}
MODO=${2:-}
case "$MODO" in ''|--solo-esquema) ;; *) echo "ABORTA: opcion desconocida '$MODO'"; exit 2 ;; esac
H=${ORBIT_SSH:-gonserver}
REPO=$(git rev-parse --show-toplevel)
SRV=/mnt/data/appdata/orbit
cd "$REPO"

echo "== 0) SHA desplegado y su reversa, ANTES de tocar nada"
SHA=$(ssh "$H" "cat $SRV/predeploy-$STAMP/SHA") || { echo "ABORTA: no existe $SRV/predeploy-$STAMP/SHA"; exit 1; }
git fetch -q origin
git cat-file -e "$SHA:migrations/0074_reversa_jev_ads03_bloqueo.sql" || { echo "ABORTA: la reversa de 0074 no existe en $SHA"; exit 1; }
echo "SHA=$SHA"

echo "== 0b) Guardas (solo con el contenedor arriba)"
RUNNING=$(ssh "$H" "docker inspect -f '{{.State.Running}}' orbit-app-1") || { echo "ABORTA: docker inspect no contesta"; exit 1; }
if [ "$RUNNING" = "true" ]; then
  CLI=$(ssh "$H" "docker top orbit-app-1" | grep -c 'app\.cli' || true)
  [ "$CLI" = "0" ] || { echo "ABORTA: hay app.cli corriendo"; exit 1; }
  echo "guardas OK (contenedor arriba, sin app.cli)"
else
  echo "contenedor parado: la reversa corre sin guardas"
fi

echo "== 0c) Pre-chequeo: tablas vacias"
FILAS=$(ssh "$H" 'docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1 -tA' <<'SQL'
SELECT (SELECT count(*) FROM bloqueo_veredicto) + (SELECT count(*) FROM bloqueo_efecto);
SQL
)
[ "$FILAS" = "0" ] || { echo "ABORTA: hay $FILAS filas; la salida es Quitar, no DROP"; exit 1; }

if [ "$MODO" = "--solo-esquema" ]; then
  echo "== 1) Codigo sin tocar"
else
  echo "== 1) Restaurar el codigo y docker-compose.yml de predeploy-$STAMP y reconstruir"
  ssh "$H" "set -e; cd $SRV; [ -d predeploy-$STAMP/app ] && [ -f predeploy-$STAMP/docker-compose.yml ] || { echo 'ABORTA: predeploy-$STAMP incompleto'; exit 1; }; \
    rm -rf app tools; cp -a predeploy-$STAMP/app predeploy-$STAMP/tools .; \
    cp -a predeploy-$STAMP/Dockerfile predeploy-$STAMP/.dockerignore predeploy-$STAMP/pyproject.toml predeploy-$STAMP/uv.lock predeploy-$STAMP/docker-compose.yml .; \
    echo DIGEST antes=\$(docker inspect -f '{{.Image}}' orbit-app-1); \
    docker compose up -d --no-deps --build app; \
    echo DIGEST despues=\$(docker inspect -f '{{.Image}}' orbit-app-1); \
    sleep 5; curl -sS http://127.0.0.1:8010/health; echo"
fi

echo "== 2) Reversa de 0074"
git show "$SHA:migrations/0074_reversa_jev_ads03_bloqueo.sql" \
  | ssh "$H" 'docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1 -1'

echo "== 3) Verificacion (esperado f|f|f y /cortes 200)"
R=$(ssh "$H" 'docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1 -tA' <<'SQL'
SELECT to_regclass('public.bloqueo_veredicto') IS NOT NULL,
       to_regclass('public.bloqueo_efecto') IS NOT NULL,
       EXISTS (SELECT 1 FROM pg_proc WHERE proname = 'bloqueo_exige_aprobado');
SQL
)
C=$(ssh "$H" 'curl -s -o /dev/null -w %{http_code} http://127.0.0.1:8010/cortes')
echo "$R cortes=$C"
[ "$R" = "f|f|f" ] && [ "$C" = "200" ] || { echo "FALLA: la reversa no dejo el esquema previo o /cortes no responde"; exit 1; }
echo "== REVERSA LISTA. Esquema previo de respaldo: $SRV/backups/pre0074_schema_$STAMP.sql"
