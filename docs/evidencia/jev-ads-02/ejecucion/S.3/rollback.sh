#!/usr/bin/env bash
# Reversa del despliegue S.3: primero el codigo + docker-compose.yml (para
# que la app deje de ver el quinto DSN), despues la reversa de B (0052).
# La reversa aborta sola si ya hay una fila lote: despues del primer dato
# real la salida es corregir hacia adelante, no reversar.
# El login orbit_jev y la linea ORBIT_DSN_JEV se DEJAN a proposito: sin
# las tablas no hacen nada.
# Guardas: las del preflight de desplegar.sh, pero solo con el contenedor
# arriba. Con el contenedor parado no hay nada que interrumpir y docker
# top no contesta: ahi la reversa corre sin guardas y lo dice en su salida.
# Uso: cd ~/dev/goncloud-Orbit && bash docs/evidencia/jev-ads-02/ejecucion/S.3/rollback.sh <STAMP de desplegar.sh> [--solo-esquema]
# --solo-esquema: desplegar.sh aborto antes de copiar el codigo (paso 6);
# solo hay que deshacer la migracion (el login se deja igual).
set -euo pipefail

STAMP=${1:?uso: rollback.sh <STAMP impreso por desplegar.sh> [--solo-esquema]}
MODO=${2:-}
case "$MODO" in ''|--solo-esquema) ;; *) echo "ABORTA: opcion desconocida '$MODO'"; exit 2 ;; esac
REPO=$(git rev-parse --show-toplevel)
SRV=/mnt/data/appdata/orbit
cd "$REPO"

echo "== 0) SHA desplegado (guardado por desplegar.sh) y su reversa, ANTES de tocar nada"
SHA=$(ssh goncloud "cat $SRV/predeploy-$STAMP/SHA") || { echo "ABORTA: no existe $SRV/predeploy-$STAMP/SHA"; exit 1; }
git fetch -q origin
git cat-file -e "$SHA:migrations/0052_reversa_jev_senales.sql" || { echo "ABORTA: la reversa de B no existe en $SHA"; exit 1; }
echo "SHA=$SHA"

echo "== 0b) Guardas (solo con el contenedor arriba)"
RUNNING=$(ssh goncloud "docker inspect -f '{{.State.Running}}' orbit-app-1") || { echo "ABORTA: docker inspect no contesta"; exit 1; }
if [ "$RUNNING" = "true" ]; then
  TOP=$(ssh goncloud "docker top orbit-app-1") || { echo "ABORTA: docker top no contesta"; exit 1; }
  CLI=$(printf '%s\n' "$TOP" | grep -c 'app\.cli' || true)
  [ "$CLI" = "0" ] || { echo "ABORTA: hay app.cli corriendo"; exit 1; }
  echo "guardas OK (contenedor arriba, sin app.cli)"
else
  echo "contenedor parado: la reversa corre sin guardas (no hay nada que interrumpir)"
fi

if [ "$MODO" = "--solo-esquema" ]; then
  echo "== 1) Codigo sin tocar (desplegar.sh aborto antes de copiarlo)"
else
  echo "== 1) Restaurar el codigo y docker-compose.yml de predeploy-$STAMP y reconstruir"
  ssh goncloud "set -e; cd $SRV; [ -d predeploy-$STAMP/app ] || { echo 'ABORTA: no existe predeploy-$STAMP'; exit 1; }; \
    [ -f predeploy-$STAMP/docker-compose.yml ] || { echo 'ABORTA: predeploy-$STAMP sin docker-compose.yml'; exit 1; }; \
    rm -rf app tools; cp -a predeploy-$STAMP/app predeploy-$STAMP/tools .; \
    cp -a predeploy-$STAMP/Dockerfile predeploy-$STAMP/.dockerignore predeploy-$STAMP/pyproject.toml predeploy-$STAMP/uv.lock predeploy-$STAMP/docker-compose.yml .; \
    echo DIGEST antes=\$(docker inspect -f '{{.Image}}' orbit-app-1); \
    docker compose up -d --no-deps --build app; \
    echo DIGEST despues=\$(docker inspect -f '{{.Image}}' orbit-app-1); \
    sleep 5; curl -sS http://127.0.0.1:8010/health; echo"
fi

echo "== 2) Reversa de B (0052)"
git show "$SHA:migrations/0052_reversa_jev_senales.sql" \
  | ssh goncloud 'docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1 -1'

echo "== 3) Verificacion (esperado: f|t|t|t|t y login intacto 1|1)"
R=$(ssh goncloud 'docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1 -tA' <<'SQL'
SELECT to_regclass('public.jev_senal') IS NOT NULL,
       has_table_privilege('app_decide', 'public.jev_ficha_version', 'SELECT'),
       has_table_privilege('app_decide', 'public.jev_ficha_revocacion', 'SELECT'),
       has_table_privilege('app_decide', 'public.jev_revision', 'SELECT'),
       has_table_privilege('app_decide', 'public.jev_par_evento', 'SELECT');
SQL
)
ROLES=$(ssh goncloud 'docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1 -tA' <<'SQL'
SELECT count(*) FROM pg_roles WHERE rolname = 'orbit_jev';
SQL
)
LINEA=$(ssh goncloud "grep -c '^ORBIT_DSN_JEV=' $SRV/.env") || LINEA=""
echo "$R"
echo "login=$ROLES env=$LINEA"
[ "$R" = "f|t|t|t|t" ] || { echo "FALLA: la reversa no devolvio el esquema (app_decide sin SELECT en 0049)"; exit 1; }
[ "$ROLES" = "1" ] && [ "$LINEA" = "1" ] || { echo "FALLA: orbit_jev o la linea ORBIT_DSN_JEV se perdieron (debieron quedarse)"; exit 1; }
echo "== REVERSA LISTA. Esquema previo de respaldo: $SRV/backups/pre0052_schema_$STAMP.sql"
echo "NOTA: orbit_jev y ORBIT_DSN_JEV se dejan a proposito (sin las tablas no hacen nada)."
