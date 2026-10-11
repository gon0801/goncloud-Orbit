#!/usr/bin/env bash
# Despliegue D.4 de BIDS 02 (seccion 5): migraciones (0064, 0065 y 0066,
# mas toda _bids02_ no aplicada del SHA, aunque sea de otra seccion), el
# codigo del SHA aprobado y UNA linea de cron: la suelta de impulso-vigia
# (08:20 UTC, tras las ingestas y antes del ciclo). Patron:
# docs/evidencia/bids-02/ejecucion/D.2/desplegar.sh (cron) +
# docs/evidencia/bids-02/ejecucion/D.3/desplegar.sh (pasos) + las cinco
# guardas de "Como se despliega una seccion" de la guia. Sus guardas
# propias: el SHA trae las 0064/0065/0066, app/impulso_io.py,
# app/static/js/productos.js, MUTATION_REQUEST_TYPES con PUT /sp/productAds
# y la linea de impulso-vigia en docs/DEPLOY.md. Si una falla, aborta.
# CALCULA la linea en 1c e INSTALA en 7b (tras el health del contenedor
# nuevo y tras CORRER el vigia una vez en 7v: si no sale 0 o guarda una
# fila en impulso_lectura, aborta sin instalar la linea). El diff
# verificado trae solo la prevista; si trae otra cosa, aborta sin tocar
# nada. En simulacion (ORBIT_SIMULACION=1) todo lo de datos corre de
# verdad contra ORBIT_SIM_DSN, el crontab es el archivo
# ${TMPDIR:-/tmp}/orbit-d4-sim/crontab-gon (sembrado del crontab ejemplo
# + una linea accounting ajena, que debe quedar byte-igual) y lo de
# servidor (docker, build) se marca SIMULACION y no se toca: no hay ssh
# ni mutacion fuera de la base sim.
# Uso: bash docs/evidencia/bids-02/ejecucion/D.4/desplegar.sh <sha de origin/master con CI verde>
set -euo pipefail

APROBADO=${1:?uso: desplegar.sh <sha de origin/master con CI verde>}
REPO=$(git rev-parse --show-toplevel)
SRV=/mnt/data/appdata/orbit
STAMP=$(date -u +%Y%m%d-%H%M)
DIR=docs/evidencia/bids-02/ejecucion/D.4
SIM=${ORBIT_SIMULACION:-0}
DSN_SIM=${ORBIT_SIM_DSN:-postgresql://orbit:orbit@127.0.0.1:5433/orbit_sim_d4}
TMPD=${TMPDIR:-/tmp}/orbit-d4-sim
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
    *) echo "ABORTA: migracion $1 sin consulta de aplicada en la guia" >&2; exit 1 ;;
  esac
}
# instalar_cron <actual> <nuevo> <linea-vigia>: escribe el crontab nuevo
# con la linea suelta al final. Idempotente: si ya esta, no la duplica.
instalar_cron() {
  python3 - "$1" "$2" "$3" <<'PY'
import sys
actual, nuevo, vigia = sys.argv[1:4]
with open(actual) as f:
    lineas = f.read().splitlines()
if any("impulso-vigia" in ln and ln[:1].isdigit() for ln in lineas):
    print("vigia: ya instalada")
else:
    lineas.append(vigia)
    print("vigia: instalada suelta al final")
with open(nuevo, "w") as f:
    f.write("\n".join(lineas) + "\n")
PY
}
solo_previsto() {  # solo_previsto <base> <nuevo>: el diff trae solo la prevista
  # El diff va a un archivo: con pipefail, `diff | grep` miente (diff
  # sale 1 cuando hay diferencias y el if nunca entraria).
  diff "$1" "$2" > "$2.diff" || [ "$?" = 1 ]
  if grep -q '^<' "$2.diff"; then
    echo "ABORTA: el diff trae lineas eliminadas (no previsto); el crontab queda intacto"
    cat "$2.diff"
    exit 1
  fi
  grep '^>' "$2.diff" | sed 's/^> //' > "$2.agregadas" || true
  while IFS= read -r ln; do
    [ "$ln" = "$VIGIA" ] || {
      echo "ABORTA: el diff trae una linea no prevista: $ln; el crontab queda intacto"; exit 1
    }
  done < "$2.agregadas"
}

if [ "$SIM" = 1 ]; then
  echo "== MODO SIMULACION: datos reales contra $DSN_SIM, servidor intacto"
  mkdir -p "$TMPD"
  APLICADAS="$TMPD/aplicadas-$STAMP.txt"
  CRON_SIM="$TMPD/crontab-gon"
  RESPALDO_CRON="$TMPD/crontab-gon-pre-d4-$STAMP.txt"
else
  APLICADAS="$DIR/aplicadas-$STAMP.txt"
  RESPALDO_LOCAL="$TMPD-respaldo-crontab-$STAMP.txt"
  NUEVO_LOCAL="$TMPD-nuevo-crontab-$STAMP.txt"
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
  git cat-file -e "$APROBADO:migrations/0065_bids02_impulso.sql" || { echo "ABORTA: $APROBADO no trae la 0065"; exit 1; }
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

echo "== 1b) Guardas propias de D.4 (abortan si fallan)"
for N in 0064_bids02_grupo_tipo 0065_bids02_impulso 0066_bids02_retiro_anuncios; do
  git cat-file -e "$APROBADO:migrations/$N.sql" || { echo "ABORTA: $APROBADO no trae $N"; exit 1; }
done
git cat-file -e "$APROBADO:app/impulso_io.py" || { echo "ABORTA: $APROBADO no trae app/impulso_io.py (I.3)"; exit 1; }
git cat-file -e "$APROBADO:app/retiro_anuncios.py" || { echo "ABORTA: $APROBADO no trae app/retiro_anuncios.py (I.4)"; exit 1; }
git cat-file -e "$APROBADO:app/static/js/productos.js" || { echo "ABORTA: $APROBADO no trae app/static/js/productos.js (P.4)"; exit 1; }
git show "$APROBADO:app/cli_bids.py" | grep -q '"impulso-vigia"' || { echo "ABORTA: app/cli_bids.py del SHA no registra impulso-vigia"; exit 1; }
git show "$APROBADO:app/ads/write.py" | grep -q '("PUT", "/sp/productAds")' || { echo "ABORTA: MUTATION_REQUEST_TYPES del SHA no trae PUT /sp/productAds"; exit 1; }
echo "0064/0065/0066, impulso, retiro, productos.js, comando y PUT /sp/productAds presentes en el SHA"
VIGIA=$(git show "$APROBADO:docs/DEPLOY.md" | grep -E '^[0-9].*impulso-vigia')
[ "$(printf '%s\n' "$VIGIA" | grep -c .)" = 1 ] || { echo "ABORTA: DEPLOY.md del SHA no trae exactamente la linea de impulso-vigia"; exit 1; }
echo "linea de impulso-vigia en el SHA OK"
echo "guardas propias OK"

echo "== 1c) Cron (guarda 5): respaldo, CALCULAR la linea, diff solo previsto (se instala en 7b)"
INSTALAR_CRON=0
if [ "$SIM" = 1 ]; then
  if [ ! -f "$CRON_SIM" ]; then
    echo "SIMULACION: siembra del crontab pre-D.4 (dos lineas Orbit + linea accounting ajena)"
    git show "$APROBADO:docs/DEPLOY.md" | sed -n "/^ORBIT_BLOCK=\$(cat <<.CRON./,/^CRON$/p" | sed '1d;$d' | head -3 > "$CRON_SIM"
    echo "0 3 * * * /opt/accounting/ledger.sh  # accounting (ajena, debe quedar byte-igual)" >> "$CRON_SIM"
  fi
  cp "$CRON_SIM" "$RESPALDO_CRON"
  instalar_cron "$CRON_SIM" "$CRON_SIM.nuevo" "$VIGIA"
  NUEVO="$CRON_SIM.nuevo"
  RESPALDO="$RESPALDO_CRON"
else
  mkdir -p "$TMPD"
  ssh goncloud "crontab -l -u gon 2>/dev/null" > "$RESPALDO_LOCAL" || { echo "ABORTA: no se pudo leer el crontab de gon"; exit 1; }
  instalar_cron "$RESPALDO_LOCAL" "$NUEVO_LOCAL" "$VIGIA"
  NUEVO="$NUEVO_LOCAL"
  RESPALDO="$RESPALDO_LOCAL"
fi
solo_previsto "$RESPALDO" "$NUEVO"
if [ -s "$NUEVO.agregadas" ]; then
  echo "diff solo con la linea prevista:"
  cat "$NUEVO.agregadas"
  # El crontab NO se toca aqui: instalar antes de migrar y recrear dejaria
  # la linea nueva invocando un comando que la imagen vieja no tiene si un
  # paso posterior aborta. Se instala en 7b, tras el health del contenedor
  # nuevo y tras correr el vigia en 7v; un abort previo deja el crontab
  # intacto.
  INSTALAR_CRON=1
  echo "crontab calculado en $NUEVO (se instala en 7b; respaldo en $RESPALDO)"
else
  echo "la linea ya estaba: nada que instalar en 7b"
fi

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
  pg_dump "$DSN_SIM" --schema-only > "$TMPD/pred4_schema_$STAMP.sql"
  grep -q 'CREATE TABLE public.decision ' "$TMPD/pred4_schema_$STAMP.sql" || { echo "ABORTA: dump invalido"; exit 1; }
  echo "SIMULACION: backup local $TMPD/pred4_schema_$STAMP.sql"
else
  ssh goncloud "set -e; D=$SRV/backups; TMP=\"\$D/.pred4_schema_$STAMP.sql.tmp\"; \
    docker exec orbit-db-1 pg_dump -U orbit -d orbit --schema-only > \"\$TMP\"; \
    [ -s \"\$TMP\" ] && grep -q 'CREATE TABLE public.decision ' \"\$TMP\" \
      && tail -5 \"\$TMP\" | grep -q 'PostgreSQL database dump complete' \
      || { echo 'DUMP INVALIDO'; rm -f \"\$TMP\"; exit 1; }; \
    chmod 600 \"\$TMP\"; mv \"\$TMP\" \"\$D/pred4_schema_$STAMP.sql\"; \
    ls -l \"\$D/pred4_schema_$STAMP.sql\""
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
ARCHIVOS_D4="app/impulso.py app/impulso_io.py app/retiro_anuncios.py app/pantalla_productos.py app/api_fabrica.py app/api_dashboard.py app/ui.py app/cli_bids.py app/ads/write.py app/ads/archivar.py app/templates/productos_sin_clics.html app/static/js/productos.js app/static/js/fabrica.js app/templates/fabrica.html"
if [ "$SIM" = 1 ]; then
  echo "SIMULACION: sin servidor; se verifica el respaldo local contra el SHA"
  for f in $ARCHIVOS_D4; do
    a=$(git show "$APROBADO:$f" | md5_de)
    b=$(md5_de < "$TMPD/predeploy-$STAMP/$f")
    [ "$a" = "$b" ] || { echo "ABORTA: md5 distinto en $f"; exit 1; }
    echo "md5 OK $f"
  done
else
  git archive --format=tar "$APROBADO" app Dockerfile .dockerignore pyproject.toml uv.lock tools/fabrica_campanas.py \
      tools/jev_ads.py tools/jev_fichas.py \
    | ssh goncloud "cd $SRV && tar -xf -"
  for f in $ARCHIVOS_D4; do
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

echo "== 7v) Corre el vigia una vez con la imagen nueva (sin impulsos sale 0 sin escribir)"
if [ "$SIM" = 1 ]; then
  # La base sim es prod-like: puede traer impulsos? No: la D.4 aun no
  # desplego; impulso existe (migracion aplicada en 4) y esta vacia.
  PY="$REPO/.venv/bin/python"
  [ -x "$PY" ] || PY=python3
  ORBIT_DSN_ADMIN="$DSN_SIM" "$PY" -m app.cli impulso-vigia || { echo "ABORTA: impulso-vigia no salio 0 en sim"; exit 1; }
else
  ssh goncloud "docker exec orbit-app-1 python -m app.cli impulso-vigia" || { echo "ABORTA: impulso-vigia no salio 0; el cron queda sin instalar"; exit 1; }
fi
LEIDAS=$(echo "SELECT count(*) FROM impulso_lectura;" | q)
[ "$LEIDAS" = "0" ] || { echo "ABORTA: impulso_lectura trae $LEIDAS fila(s) tras el vigia; el cron queda sin instalar"; exit 1; }
echo "vigia OK: salio 0 sin impulsos y sin guardar"

echo "== 7b) Cron: RELEER el crontab actual e instalar (tras el vigia de 7v)"
if [ "$INSTALAR_CRON" = 1 ]; then
  # E6: entre 1c y 7b otro loop pudo agregar lineas; instalar lo
  # calculado en 1c las borraria. Se recalcula sobre el actual y se
  # revalida el diff antes de instalar.
  if [ "$SIM" = 1 ]; then
    ACTUAL_7B="$CRON_SIM"
  else
    ssh goncloud "crontab -l -u gon 2>/dev/null" > "$NUEVO_LOCAL.actual" || { echo "ABORTA: no se pudo releer el crontab de gon"; exit 1; }
    ACTUAL_7B="$NUEVO_LOCAL.actual"
  fi
  instalar_cron "$ACTUAL_7B" "$NUEVO.7b" "$VIGIA"
  solo_previsto "$ACTUAL_7B" "$NUEVO.7b"
  if [ "$SIM" = 1 ]; then
    cp "$ACTUAL_7B" "$RESPALDO_CRON"
    mv "$NUEVO.7b" "$CRON_SIM"
    rm -f "$NUEVO.diff" "$NUEVO.agregadas" "$NUEVO.7b.diff" "$NUEVO.7b.agregadas"
    echo "SIMULACION: crontab instalado en $CRON_SIM (respaldo en $RESPALDO_CRON)"
  else
    ssh goncloud "mkdir -p $SRV/backups && cat > $SRV/backups/crontab-gon-pre-d4-$STAMP.txt" < "$ACTUAL_7B"
    cat "$NUEVO.7b" | ssh goncloud "crontab -u gon -"
    ssh goncloud "crontab -l -u gon" | grep -qF impulso-vigia || { echo "ABORTA: impulso-vigia no quedo instalado"; exit 1; }
    echo "crontab instalado (respaldo en $SRV/backups/crontab-gon-pre-d4-$STAMP.txt)"
  fi
else
  echo "nada que instalar (1c no trajo lineas nuevas)"
fi

echo "== LISTO. STAMP=$STAMP"
echo "Siguiente: bash $DIR/checklist.sh $STAMP"
echo "Reversa:   bash $DIR/rollback.sh $STAMP"
