#!/usr/bin/env bash
# Despliegue D.3 de BIDS 02 (seccion 4): migraciones (0067, mas toda
# _bids02_ no aplicada del SHA, aunque sea de otra seccion) y el codigo del
# SHA aprobado. NO cambia el cron: no respalda, calcula ni instala lineas.
# Patron: docs/evidencia/bids-02/ejecucion/D.2/desplegar.sh + las cinco
# guardas de "Como se despliega una seccion" de la guia. Sus guardas
# propias: el SHA trae la 0067, app/campana_ajustes.py, app/static/js/dinero.js
# y MUTATION_REQUEST_TYPES con PUT /sp/campaigns. Si una falla, aborta.
# En simulacion (ORBIT_SIMULACION=1) todo lo de datos corre de verdad contra
# ORBIT_SIM_DSN y lo de servidor (docker, build) se marca SIMULACION y no se
# toca: no hay ssh ni mutacion fuera de la base sim.
# Uso: bash docs/evidencia/bids-02/ejecucion/D.3/desplegar.sh <sha de origin/master con CI verde>
set -euo pipefail

APROBADO=${1:?uso: desplegar.sh <sha de origin/master con CI verde>}
REPO=$(git rev-parse --show-toplevel)
SRV=/mnt/data/appdata/orbit
STAMP=$(date -u +%Y%m%d-%H%M)
DIR=docs/evidencia/bids-02/ejecucion/D.3
SIM=${ORBIT_SIMULACION:-0}
DSN_SIM=${ORBIT_SIM_DSN:-postgresql://orbit:orbit@127.0.0.1:5433/orbit_sim_d3}
TMPD=${TMPDIR:-/tmp}/orbit-d3-sim
cd "$REPO"
trap 'echo "FALLO en la linea $LINENO. Si ya se aplico alguna migracion: bash $DIR/rollback.sh $STAMP"' ERR

q() {
  if [ "$SIM" = 1 ]; then
    psql "$DSN_SIM" -X -q -tA -v ON_ERROR_STOP=1
  else
    ssh goncloud 'DSN=$(docker exec orbit-app-1 printenv ORBIT_DSN_READ); docker exec -i orbit-db-1 psql "$DSN" -X -q -tA -v ON_ERROR_STOP=1'
  fi
}
w() {
  if [ "$SIM" = 1 ]; then
    psql "$DSN_SIM" -q -v ON_ERROR_STOP=1 -1
  else
    ssh goncloud 'docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1 -1'
  fi
}
guardas_proceso() {
  local top cli
  top=$(ssh goncloud "docker top orbit-app-1") || return 1
  cli=$(printf '%s\n' "$top" | grep -c 'app\.cli' || true)
  [ "$cli" = "0" ]
}
# aplicada <numero>: t si la migracion ya esta aplicada (tabla de la guia).
aplicada() {
  case "$1" in
    0060) echo "SELECT to_regclass('public.v_hoja_activa') IS NOT NULL;" | q ;;
    0061) echo "SELECT to_regclass('public.ads_campana_config_observation') IS NOT NULL;" | q ;;
    0062) echo "SELECT to_regclass('public.ads_placement_observation') IS NOT NULL;" | q ;;
    0063) echo "SELECT pg_get_constraintdef(oid) NOT LIKE '%cache_estado%' FROM pg_constraint WHERE conname = 'target_acos_ciclo_procedencia_check';" | q ;;
    0064) echo "SELECT count(*) = 1 FROM information_schema.columns WHERE table_name = 'campana_grupo' AND column_name = 'tipo';" | q ;;
    0065) echo "SELECT to_regclass('public.impulso') IS NOT NULL;" | q ;;
    0066) echo "SELECT to_regclass('public.anuncio_retiro') IS NOT NULL;" | q ;;
    0067) echo "SELECT to_regclass('public.campana_ajuste') IS NOT NULL;" | q ;;
    *) echo "ABORTA: migracion $1 sin consulta de aplicada en la guia" >&2; exit 1 ;;
  esac
}
if [ "$SIM" = 1 ]; then
  echo "== MODO SIMULACION: datos reales contra $DSN_SIM, servidor intacto"
  mkdir -p "$TMPD"
  APLICADAS="$TMPD/aplicadas-$STAMP.txt"
else
  APLICADAS="$DIR/aplicadas-$STAMP.txt"
fi
# Append-only: un reintento en el mismo minuto conserva lo que anoto la
# corrida anterior (PENDIENTES ya salta las aplicadas, no hay duplicados);
# truncar dejaria a rollback.sh sin nada que reversar.
if [ -f "$APLICADAS" ]; then
  echo "reintento con $APLICADAS existente: se conserva (append-only)"
else
  : > "$APLICADAS"
fi

echo "== 0) SHA aprobado con la bateria completa verde"
if [ "$SIM" = 1 ]; then
  git cat-file -e "$APROBADO:migrations/0067_bids02_campana_ajuste.sql" || { echo "ABORTA: $APROBADO no trae la 0067"; exit 1; }
  echo "SIMULACION: $APROBADO existe local; sin fetch ni gh (no hay red a prod)"
else
  git fetch -q origin
  [ "$(git rev-parse origin/master)" = "$APROBADO" ] || { echo "ABORTA: origin/master ya no es $APROBADO"; exit 1; }
  CI=$(gh run list --commit "$APROBADO" --event push --workflow Quality --json conclusion --jq '.[0].conclusion')
  [ "$CI" = "success" ] || { echo "ABORTA: el run push de Quality sobre $APROBADO es '$CI', no success"; exit 1; }
  echo "APROBADO=$APROBADO CI=$CI"
fi

echo "== 1) Preflight: nadie corre, sin harvest en vuelo, migraciones pendientes"
if [ "$SIM" = 1 ]; then
  echo "SIMULACION: sin docker top (no hay contenedor); la guarda de proceso es el conteo de running"
else
  TOP=$(ssh goncloud "docker top orbit-app-1") || { echo "ABORTA: docker top no contesta"; exit 1; }
  CLI=$(printf '%s\n' "$TOP" | grep -c 'app\.cli' || true)
  [ "$CLI" = "0" ] || { echo "ABORTA: hay app.cli corriendo"; exit 1; }
  echo "cli=0"
fi
RUNNING=$(echo "SELECT count(*) FROM optimizer_cycle WHERE status = 'running';" | q)
[ "$RUNNING" = "0" ] || { echo "ABORTA: hay $RUNNING ciclo(s) en running"; exit 1; }
echo "ciclos running=0"
H1=$(echo "SELECT count(*) FROM apply_queue WHERE kind = 'harvest' AND estado NOT IN ('applied','failed','vetoed','discarded');" | q)
H2=$(echo "SELECT count(*) FROM harvest_job WHERE fase IN ('pending','negative_created','exact_created','hermanas_negadas');" | q)
[ "$H1" = "0" ] && [ "$H2" = "0" ] || { echo "ABORTA: harvest en vuelo ($H1 en cola, $H2 jobs)"; exit 1; }
echo "harvest en vuelo=0"
PENDIENTES=""
for m in $(git ls-tree --name-only "$APROBADO" migrations/ | grep _bids02_ | grep -v _reversa_ | sort); do
  n=$(basename "$m" | cut -c1-4)
  a=$(aplicada "$n")
  [ -n "$a" ] || { echo "ABORTA: la consulta de aplicada de $n salio vacia"; exit 1; }
  if [ "$a" = "t" ]; then echo "ya aplicada: $m"; else echo "pendiente: $m"; PENDIENTES="$PENDIENTES $m"; fi
done

echo "== 1b) Guardas propias de D.3 (abortan si fallan)"
git cat-file -e "$APROBADO:migrations/0067_bids02_campana_ajuste.sql" || { echo "ABORTA: $APROBADO no trae la 0067"; exit 1; }
git cat-file -e "$APROBADO:app/campana_ajustes.py" || { echo "ABORTA: $APROBADO no trae app/campana_ajustes.py (V.3)"; exit 1; }
git cat-file -e "$APROBADO:app/static/js/dinero.js" || { echo "ABORTA: $APROBADO no trae app/static/js/dinero.js (P.2b)"; exit 1; }
git show "$APROBADO:app/ads/write.py" | grep -q '("PUT", "/sp/campaigns")' || { echo "ABORTA: MUTATION_REQUEST_TYPES del SHA no trae PUT /sp/campaigns"; exit 1; }
echo "0067, ajustes, dinero.js y PUT /sp/campaigns presentes en el SHA"
echo "guardas propias OK"
echo "D.3 no cambia el cron: nada que respaldar, calcular ni instalar"

echo "== 2) Respaldo del codigo actual y SHA (el .env NO se copia a ningun lado)"
# Si predeploy-<sello> ya existe (reintento en el mismo minuto), se
# conserva: copiar encima guardaria el codigo NUEVO que dejo la corrida
# anterior y la reversa restauraria codigo nuevo sin tablas.
if [ "$SIM" = 1 ]; then
  if [ -f "$TMPD/predeploy-$STAMP/SHA" ]; then
    echo "SIMULACION: reintento, $TMPD/predeploy-$STAMP se conserva (no se pisa)"
  else
    mkdir -p "$TMPD/predeploy-$STAMP"
    git archive --format=tar "$APROBADO" app Dockerfile .dockerignore pyproject.toml uv.lock tools docker-compose.yml \
      | tar -x -C "$TMPD/predeploy-$STAMP"
    echo "$APROBADO" > "$TMPD/predeploy-$STAMP/SHA"
    echo "SIMULACION: respaldo local en $TMPD/predeploy-$STAMP"
  fi
elif ssh goncloud "test -f $SRV/predeploy-$STAMP/SHA"; then
  echo "reintento: predeploy-$STAMP ya existe, se conserva (no se pisa)"
else
  ssh goncloud "set -e; cd $SRV; mkdir -p predeploy-$STAMP; \
    cp -a app Dockerfile .dockerignore pyproject.toml uv.lock tools docker-compose.yml predeploy-$STAMP/; \
    echo $APROBADO > predeploy-$STAMP/SHA; ls predeploy-$STAMP"
fi

echo "== 3) Backup del esquema (staging + verificacion)"
if [ "$SIM" = 1 ]; then
  pg_dump "$DSN_SIM" --schema-only > "$TMPD/pred3_schema_$STAMP.sql"
  grep -q 'CREATE TABLE public.decision ' "$TMPD/pred3_schema_$STAMP.sql" || { echo "ABORTA: dump invalido"; exit 1; }
  echo "SIMULACION: backup local $TMPD/pred3_schema_$STAMP.sql"
else
  ssh goncloud "set -e; D=$SRV/backups; TMP=\"\$D/.pred3_schema_$STAMP.sql.tmp\"; \
    docker exec orbit-db-1 pg_dump -U orbit -d orbit --schema-only > \"\$TMP\"; \
    [ -s \"\$TMP\" ] && grep -q 'CREATE TABLE public.decision ' \"\$TMP\" \
      && tail -5 \"\$TMP\" | grep -q 'PostgreSQL database dump complete' \
      || { echo 'DUMP INVALIDO'; rm -f \"\$TMP\"; exit 1; }; \
    chmod 600 \"\$TMP\"; mv \"\$TMP\" \"\$D/pred3_schema_$STAMP.sql\"; \
    ls -l \"\$D/pred3_schema_$STAMP.sql\""
fi

echo "== 4) Migraciones BIDS 02 pendientes, cada una en su propio psql -1"
if [ -z "$PENDIENTES" ]; then echo "ninguna pendiente"; else
  for m in $PENDIENTES; do
    git show "$APROBADO:$m" | w
    echo "$m" >> "$APLICADAS"
    echo "aplicada $m (anotada en $APLICADAS)"
  done
fi

echo "== 5) md5 del codigo desplegado vs SHA aprobado"
md5_de() { if command -v md5 >/dev/null; then md5 -q; else md5sum | cut -d' ' -f1; fi; }
ARCHIVOS_D3="app/campana_ajustes.py app/ads/campana_config.py app/ads/write.py app/apply.py app/api_write.py app/pantalla_dinero.py app/avisos_campana.py app/templates/donde_poner_el_dinero.html app/static/js/dinero.js"
if [ "$SIM" = 1 ]; then
  echo "SIMULACION: sin servidor; se verifica el respaldo local contra el SHA"
  for f in $ARCHIVOS_D3; do
    a=$(git show "$APROBADO:$f" | md5_de)
    b=$(md5_de < "$TMPD/predeploy-$STAMP/$f")
    [ "$a" = "$b" ] || { echo "ABORTA: md5 distinto en $f"; exit 1; }
    echo "md5 OK $f"
  done
else
  git archive --format=tar "$APROBADO" app Dockerfile .dockerignore pyproject.toml uv.lock tools/fabrica_campanas.py \
      tools/jev_ads.py tools/jev_fichas.py \
    | ssh goncloud "cd $SRV && tar -xf -"
  for f in $ARCHIVOS_D3; do
    a=$(git show "$APROBADO:$f" | md5_de)
    b=$(ssh goncloud "md5sum $SRV/$f" | cut -d' ' -f1)
    [ "$a" = "$b" ] || { echo "ABORTA: md5 distinto en $f"; exit 1; }
    echo "md5 OK $f"
  done
fi

echo "== 6) Guardas antes de recrear"
if [ "$SIM" = 1 ]; then
  RUNNING=$(echo "SELECT count(*) FROM optimizer_cycle WHERE status = 'running';" | q)
  [ "$RUNNING" = "0" ] || { echo "ABORTA: hay $RUNNING ciclo(s) en running"; exit 1; }
  echo "SIMULACION: guardas OK en una pasada (sin reintento de 30 min)"
else
  LISTO=0
  for i in $(seq 1 60); do
    if guardas_proceso; then LISTO=1; break; fi
    echo "intento $i/60: app.cli en curso o sin respuesta; reintento en 30s"
    sleep 30
  done
  if [ "$LISTO" != "1" ]; then
    echo "DETENIDO: las guardas no pasaron en 30 minutos. Me detengo SIN recrear el contenedor y SIN correr la reversa."
    echo "El contenedor viejo sigue corriendo su imagen anterior: la migracion aplicada y el codigo copiado no lo tocan."
    echo "Para terminar: espera a que docker top orbit-app-1 no liste app.cli y corre: ssh goncloud 'cd $SRV && docker compose up -d --no-deps --build app'"
    exit 1
  fi
  echo "guardas OK"
fi

echo "== 7) Build + recrear app (digest antes/despues) + health"
if [ "$SIM" = 1 ]; then
  echo "SIMULACION: sin rebuild (no hay servidor que recrear)"
  API_SIM=${ORBIT_SIM_API:-http://127.0.0.1:8012}
  if curl -s -o /dev/null -w '%{http_code}' "$API_SIM/health" 2>/dev/null | grep -q 200; then
    echo "SIMULACION: la app local contesta /health 200 (informativo)"
  else
    echo "SIMULACION: la app local no contesta (informativo; checklist.sh lo verifica)"
  fi
else
  ssh goncloud "set -e; cd $SRV; \
    echo DIGEST antes=\$(docker inspect -f '{{.Image}}' orbit-app-1); \
    docker compose up -d --no-deps --build app; \
    echo DIGEST despues=\$(docker inspect -f '{{.Image}}' orbit-app-1); \
    sleep 5; curl -sSf http://127.0.0.1:8010/health; echo; \
    docker ps --filter name=orbit-app-1 --format '{{.Names}} {{.Status}}'"
fi

echo "== LISTO. STAMP=$STAMP"
echo "Siguiente: bash $DIR/checklist.sh $STAMP"
echo "Reversa:   bash $DIR/rollback.sh $STAMP"
