#!/usr/bin/env bash
# Reversa del despliegue S.1: primero el codigo (para que la app deje de
# escribir el acta), despues la reversa 0051. La reversa borra las dos tablas
# aunque tengan filas: el acta se vuelve a generar en la siguiente corrida de
# `ingest structure`. Las guardas se evaluan solo con el contenedor arriba.
# Uso: cd ~/dev/goncloud-Orbit && bash docs/evidencia/jev-ads-02/ejecucion/S.1/rollback.sh <STAMP de desplegar.sh> [--solo-esquema]
# --solo-esquema: desplegar.sh aborto antes de copiar el codigo (paso 5); solo
# hay que deshacer la migracion.
set -euo pipefail

STAMP=${1:?uso: rollback.sh <STAMP impreso por desplegar.sh> [--solo-esquema]}
MODO=${2:-}
case "$MODO" in ''|--solo-esquema) ;; *) echo "ABORTA: opcion desconocida '$MODO'"; exit 2 ;; esac
REPO=$(git rev-parse --show-toplevel)
SRV=/mnt/data/appdata/orbit
cd "$REPO"

guardas() {  # las del preflight de desplegar.sh: sin ciclo corriendo y sin app.cli dentro
  local ciclos cli
  ciclos=$(ssh goncloud 'docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1 -tA -c "SELECT count(*) FROM optimizer_cycle WHERE status = '"'"'running'"'"'"')
  cli=$(ssh goncloud 'docker top orbit-app-1 2>/dev/null | grep -c "app.cli" || true')
  echo "ciclos corriendo=$ciclos, procesos app.cli=$cli"
  [ "$ciclos" = "0" ] && [ "$cli" = "0" ]
}

echo "== 0) SHA desplegado (guardado por desplegar.sh) y su reversa, ANTES de tocar nada"
SHA=$(ssh goncloud "cat $SRV/predeploy-$STAMP/SHA") || { echo "ABORTA: no existe $SRV/predeploy-$STAMP/SHA"; exit 1; }
git fetch -q origin
git cat-file -e "$SHA:migrations/0051_reversa_ads_acta_listado.sql" || { echo "ABORTA: la reversa 0051 no existe en $SHA"; exit 1; }
echo "SHA=$SHA"

echo "== 1) Guardas, solo con el contenedor arriba"
RUNNING=$(ssh goncloud "docker inspect -f '{{.State.Running}}' orbit-app-1" 2>/dev/null || true)
if [ -z "$RUNNING" ]; then
  echo "ABORTA: no se pudo leer el estado del contenedor"
  exit 1
elif [ "$RUNNING" = "true" ]; then
  echo "contenedor arriba: se evaluan las guardas"
  guardas || { echo "ABORTA: hay ciclo o app.cli corriendo; la reversa espera"; exit 1; }
else
  echo "contenedor parado: la reversa corre sin guardas (nada que interrumpir; docker top no contesta)"
fi

if [ "$MODO" = "--solo-esquema" ]; then
  echo "== 2) Codigo sin tocar (desplegar.sh aborto antes de copiarlo)"
else
  echo "== 2) Restaurar el codigo de predeploy-$STAMP y reconstruir"
  ssh goncloud "set -e; cd $SRV; [ -d predeploy-$STAMP/app ] || { echo 'ABORTA: no existe predeploy-$STAMP'; exit 1; }; \
    rm -rf app tools; cp -a predeploy-$STAMP/app predeploy-$STAMP/tools .; \
    cp -a predeploy-$STAMP/Dockerfile predeploy-$STAMP/.dockerignore predeploy-$STAMP/pyproject.toml predeploy-$STAMP/uv.lock .; \
    echo DIGEST antes=\$(docker inspect -f '{{.Image}}' orbit-app-1); \
    docker compose up -d --no-deps --build app; \
    echo DIGEST despues=\$(docker inspect -f '{{.Image}}' orbit-app-1); \
    sleep 5; curl -sS http://127.0.0.1:8010/health; echo"
fi

echo "== 3) Reversa 0051"
git show "$SHA:migrations/0051_reversa_ads_acta_listado.sql" \
  | ssh goncloud 'docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1 -1'

echo "== 4) Verificacion (esperado: f)"
R=$(ssh goncloud 'docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1 -tA' <<'SQL'
SELECT to_regclass('public.ads_listado_plataforma') IS NOT NULL;
SQL
)
echo "$R"
[ "$R" = "f" ] || { echo "FALLA: quedaron restos del acta"; exit 1; }
echo "== REVERSA LISTA. Esquema previo de respaldo: $SRV/backups/pre0051_schema_$STAMP.sql"
