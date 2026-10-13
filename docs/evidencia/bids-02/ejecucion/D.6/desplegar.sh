#!/usr/bin/env bash
# Despliegue D.6 de BIDS 02 (remate R14): migraciones 0071 (GRANT de
# v_impulso_estado) y 0072 (candado de tipo del grupo del impulso),
# mas toda _bids02_ no aplicada del SHA, y el codigo del SHA aprobado
# (impulso_io, cli_bids, productos.js, fabrica_campanas). Patron:
# docs/evidencia/bids-02/ejecucion/D.4/desplegar.sh. Guardas propias:
# el SHA trae las 0071/0072 y los cuatro archivos del remate; si una
# falla, aborta. A diferencia de D.4, D.6 NO instala linea de cron:
# imprime la huella (md5) y el numero de lineas del crontab de gon
# antes y despues, y aborta si cambian (R13-E). En simulacion
# (ORBIT_SIMULACION=1) todo lo de datos corre de verdad contra
# ORBIT_SIM_DSN, el crontab es el archivo
# ${TMPDIR:-/tmp}/orbit-d6-sim/crontab-gon y lo de servidor (docker,
# build) se marca SIMULACION y no se toca: no hay ssh ni mutacion
# fuera de la base sim. Las salidas de la corrida real se guardan con
# su linea exit= al final (R13-M).
# Uso: bash docs/evidencia/bids-02/ejecucion/D.6/desplegar.sh <sha de origin/master con CI verde>
set -euo pipefail

APROBADO=${1:?uso: desplegar.sh <sha de origin/master con CI verde>}
REPO=$(git rev-parse --show-toplevel)
SRV=/mnt/data/appdata/orbit
STAMP=$(date -u +%Y%m%d-%H%M)
DIR=docs/evidencia/bids-02/ejecucion/D.6
SIM=${ORBIT_SIMULACION:-0}
DSN_SIM=${ORBIT_SIM_DSN:-postgresql://orbit:orbit@127.0.0.1:5433/orbit_sim_d6}
TMPD=${TMPDIR:-/tmp}/orbit-d6-sim
cd "$REPO"
trap 'echo "FALLO en la linea $LINENO. Si ya se aplico alguna migracion o se toco el crontab: bash $DIR/rollback.sh $STAMP"' ERR

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
    0068) echo "SELECT count(*) = 1 FROM information_schema.columns WHERE table_name = 'fabrica_lote' AND column_name = 'pausado_por';" | q ;;
    0069) echo "SELECT to_regclass('public.anuncio_retiro_intencion') IS NOT NULL;" | q ;;
    0070) echo "SELECT has_table_privilege('app_admin', 'public.fabrica_lote', 'DELETE');" | q ;;
    0071) echo "SELECT COALESCE(obj_description('public.v_impulso_estado'::regclass, 'pg_class'), '') = 'GRANT SELECT declarado por la migracion 0071 (BIDS 02 R09-E)';" | q ;;
    0072) echo "SELECT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'impulso_grupo_tipo' AND NOT tgisinternal) AND EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'grupo_impulso_tipo_guardia' AND NOT tgisinternal);" | q ;;
    *) echo "ABORTA: migracion $1 sin consulta de aplicada en la guia" >&2; exit 1 ;;
  esac
}
# R13-E: D.6 no instala linea de cron. huella_cron imprime
# "<md5> <lineas>" del crontab de gon tal como esta (sim: el archivo;
# real: lectura por ssh), para comparar antes y despues sin tocarlo.
huella_cron() {
  if [ "$SIM" = 1 ]; then
    python3 - "$CRON_SIM" <<'PY'
import hashlib, sys
datos = open(sys.argv[1], "rb").read()
lineas = datos.decode("utf-8").splitlines()
print(f"{hashlib.md5(datos).hexdigest()} {len(lineas)}")
PY
  else
    ssh goncloud "crontab -l -u gon 2>/dev/null" | python3 -c "
import hashlib, sys
datos = sys.stdin.buffer.read()
lineas = datos.decode('utf-8').splitlines()
print(f'{hashlib.md5(datos).hexdigest()} {len(lineas)}')
"
  fi
}

if [ "$SIM" = 1 ]; then
  echo "== MODO SIMULACION: datos reales contra $DSN_SIM, servidor intacto"
  mkdir -p "$TMPD"
  APLICADAS="$TMPD/aplicadas-$STAMP.txt"
  CRON_SIM="$TMPD/crontab-gon"
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
  git cat-file -e "$APROBADO:migrations/0071_bids02_grant_vista_impulso.sql" || { echo "ABORTA: $APROBADO no trae la 0071"; exit 1; }
  git cat-file -e "$APROBADO:migrations/0072_bids02_impulso_grupo_tipo.sql" || { echo "ABORTA: $APROBADO no trae la 0072"; exit 1; }
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

echo "== 1b) Guardas propias de D.6 (abortan si fallan)"
for N in 0071_bids02_grant_vista_impulso 0072_bids02_impulso_grupo_tipo; do
  git cat-file -e "$APROBADO:migrations/$N.sql" || { echo "ABORTA: $APROBADO no trae $N"; exit 1; }
done
git cat-file -e "$APROBADO:app/impulso_io.py" || { echo "ABORTA: $APROBADO no trae app/impulso_io.py (R09-A/B/F/G/H)"; exit 1; }
git cat-file -e "$APROBADO:app/cli_bids.py" || { echo "ABORTA: $APROBADO no trae app/cli_bids.py (R09-F)"; exit 1; }
git cat-file -e "$APROBADO:app/static/js/productos.js" || { echo "ABORTA: $APROBADO no trae app/static/js/productos.js (R09-D)"; exit 1; }
git cat-file -e "$APROBADO:tools/fabrica_campanas.py" || { echo "ABORTA: $APROBADO no trae tools/fabrica_campanas.py (R09-I)"; exit 1; }
git show "$APROBADO:app/cli_bids.py" | grep -q '"impulso-vigia"' || { echo "ABORTA: app/cli_bids.py del SHA no registra impulso-vigia"; exit 1; }
echo "0071/0072, impulso_io, cli_bids, productos.js y fabrica_campanas presentes en el SHA"
echo "guardas propias OK"

echo "== 1c) Cron: D.6 no instala linea; se imprime la huella antes (R13-E)"
if [ "$SIM" = 1 ] && [ ! -f "$CRON_SIM" ]; then
  echo "SIMULACION: siembra del crontab (lineas Orbit del ejemplo + la de impulso-vigia ya instalada por D.4 + linea accounting ajena)"
  git show "$APROBADO:docs/DEPLOY.md" | sed -n "/^ORBIT_BLOCK=\$(cat <<.CRON./,/^CRON$/p" | sed '1d;$d' | head -3 > "$CRON_SIM"
  git show "$APROBADO:docs/DEPLOY.md" | grep -E '^[0-9].*impulso-vigia' >> "$CRON_SIM"
  echo "0 3 * * * /opt/accounting/ledger.sh  # accounting (ajena, debe quedar byte-igual)" >> "$CRON_SIM"
fi
HUELLA_ANTES=$(huella_cron) || { echo "ABORTA: no se pudo leer el crontab de gon"; exit 1; }
echo "crontab antes: md5 y lineas = $HUELLA_ANTES (sin tocarlo)"

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
  pg_dump "$DSN_SIM" --schema-only > "$TMPD/pred6_schema_$STAMP.sql"
  grep -q 'CREATE TABLE public.decision ' "$TMPD/pred6_schema_$STAMP.sql" || { echo "ABORTA: dump invalido"; exit 1; }
  echo "SIMULACION: backup local $TMPD/pred6_schema_$STAMP.sql"
else
  ssh goncloud "set -e; D=$SRV/backups; TMP=\"\$D/.pred6_schema_$STAMP.sql.tmp\"; \
    docker exec orbit-db-1 pg_dump -U orbit -d orbit --schema-only > \"\$TMP\"; \
    [ -s \"\$TMP\" ] && grep -q 'CREATE TABLE public.decision ' \"\$TMP\" \
      && tail -5 \"\$TMP\" | grep -q 'PostgreSQL database dump complete' \
      || { echo 'DUMP INVALIDO'; rm -f \"\$TMP\"; exit 1; }; \
    chmod 600 \"\$TMP\"; mv \"\$TMP\" \"\$D/pred6_schema_$STAMP.sql\"; \
    ls -l \"\$D/pred6_schema_$STAMP.sql\""
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
ARCHIVOS_D6="app/impulso_io.py app/cli_bids.py app/static/js/productos.js tools/fabrica_campanas.py"
if [ "$SIM" = 1 ]; then
  echo "SIMULACION: sin servidor; se verifica el respaldo local contra el SHA"
  for f in $ARCHIVOS_D6; do
    a=$(git show "$APROBADO:$f" | md5_de)
    b=$(md5_de < "$TMPD/predeploy-$STAMP/$f")
    [ "$a" = "$b" ] || { echo "ABORTA: md5 distinto en $f"; exit 1; }
    echo "md5 OK $f"
  done
else
  git archive --format=tar "$APROBADO" app Dockerfile .dockerignore pyproject.toml uv.lock tools/fabrica_campanas.py \
      tools/jev_ads.py tools/jev_fichas.py \
    | ssh goncloud "cd $SRV && tar -xf -"
  for f in $ARCHIVOS_D6; do
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

echo "== 7v) Corre el vigia una vez con la imagen nueva (solo si no hay impulsos vivos)"
VIVOS=$(echo "SELECT count(*) FROM impulso i LEFT JOIN impulso_lectura l ON l.impulso_id = i.id AND l.es_final WHERE l.impulso_id IS NULL;" | q)
if [ "$VIVOS" != "0" ]; then
  # Con vivos, el vigia es trabajo del dueno (puede pausar o graduar):
  # el despliegue no lo corre ni exige lecturas en cero.
  echo "vigia no corrido en el despliegue: hay $VIVOS impulso(s) vivo(s); lo atiende el cron"
else
  LEIDAS_ANTES=$(echo "SELECT count(*) FROM impulso_lectura;" | q)
  if [ "$SIM" = 1 ]; then
    PY="$REPO/.venv/bin/python"
    [ -x "$PY" ] || PY=python3
    ORBIT_DSN_ADMIN="$DSN_SIM" "$PY" -m app.cli impulso-vigia || { echo "ABORTA: impulso-vigia no salio 0 en sim"; exit 1; }
  else
    ssh goncloud "docker exec orbit-app-1 python -m app.cli impulso-vigia" || { echo "ABORTA: impulso-vigia no salio 0"; exit 1; }
  fi
  LEIDAS=$(echo "SELECT count(*) FROM impulso_lectura;" | q)
  [ "$LEIDAS" = "$LEIDAS_ANTES" ] || { echo "ABORTA: impulso_lectura paso de $LEIDAS_ANTES a $LEIDAS fila(s) tras el vigia"; exit 1; }
  echo "vigia OK: salio 0 sin impulsos y sin guardar (lecturas antes=$LEIDAS_ANTES despues=$LEIDAS)"
fi

echo "== 8) Cron: la huella no cambio (D.6 no instala linea: R13-E)"
HUELLA_DESPUES=$(huella_cron) || { echo "ABORTA: no se pudo releer el crontab de gon"; exit 1; }
echo "crontab despues: md5 y lineas = $HUELLA_DESPUES"
[ "$HUELLA_DESPUES" = "$HUELLA_ANTES" ] || { echo "ABORTA: el crontab cambio durante el despliegue (antes=$HUELLA_ANTES despues=$HUELLA_DESPUES) y D.6 no instala linea: otro proceso lo toco"; exit 1; }
echo "crontab intacto"

echo "== LISTO. STAMP=$STAMP"
echo "Siguiente: bash $DIR/checklist.sh $STAMP"
echo "Reversa:   bash $DIR/rollback.sh $STAMP"
