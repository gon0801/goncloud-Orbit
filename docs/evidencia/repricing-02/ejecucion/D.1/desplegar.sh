#!/usr/bin/env bash
# Despliegue D.1 de REPRICING 02 (corte 0): migraciones 0053 (canal MeLi) y
# 0054 (base: goal_id, l_origen, verificacion, modos, registros), mas el
# codigo del SHA aprobado (corrida detras del Mercado, cambios, CLI precio
# con --platform repetido, cobertura por vitrina). Patron:
# docs/evidencia/bids-02/ejecucion/D.6/desplegar.sh. Guardas propias:
# el SHA trae la 0053, la 0054 y los modulos nuevos del corte; si una
# falla, aborta. D.1 NO cambia el cron: imprime la huella (md5) y el
# numero de lineas del crontab de gon antes y despues, y aborta si
# cambian. En simulacion (ORBIT_SIMULACION=1) todo lo de datos corre de
# verdad contra ORBIT_SIM_DSN, el crontab es el archivo
# ${TMPDIR:-/tmp}/orbit-d1-sim/crontab-gon y lo de servidor (docker,
# build) se marca SIMULACION y no se toca: no hay ssh ni mutacion
# fuera de la base sim. Las salidas de la corrida real se guardan con
# su linea exit= al final.
# Uso: bash docs/evidencia/repricing-02/ejecucion/D.1/desplegar.sh <sha de origin/master con CI verde>
set -euo pipefail

APROBADO=${1:?uso: desplegar.sh <sha de origin/master con CI verde>}
REPO=$(git rev-parse --show-toplevel)
SRV=/mnt/data/appdata/orbit
STAMP=$(date -u +%Y%m%d-%H%M)
DIR=docs/evidencia/repricing-02/ejecucion/D.1
SIM=${ORBIT_SIMULACION:-0}
DSN_SIM=${ORBIT_SIM_DSN:-postgresql://orbit:orbit@localhost:5432/orbit_sim_d1}
TMPD=${TMPDIR:-/tmp}/orbit-d1-sim
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
# aplicada <numero>: t si la migracion ya esta aplicada.
aplicada() {
  case "$1" in
    0053) echo "SELECT EXISTS (SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid WHERE t.typname = 'estimacion_canal' AND e.enumlabel = 'meli');" | q ;;
    0054) echo "SELECT count(*) = 3 FROM information_schema.columns WHERE table_name = 'precio_decision' AND column_name IN ('goal_id', 'l_origen', 'verificacion');" | q ;;
    *) echo "ABORTA: migracion $1 sin consulta de aplicada en la guia" >&2; exit 1 ;;
  esac
}
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
if [ -f "$APLICADAS" ]; then
  echo "reintento con $APLICADAS existente: se conserva (append-only)"
else
  : > "$APLICADAS"
fi

echo "== 0) SHA aprobado con la bateria completa verde"
if [ "$SIM" = 1 ]; then
  git cat-file -e "$APROBADO:migrations/0053_precio02_canal_meli.sql" || { echo "ABORTA: $APROBADO no trae la 0053"; exit 1; }
  git cat-file -e "$APROBADO:migrations/0054_precio02_base.sql" || { echo "ABORTA: $APROBADO no trae la 0054"; exit 1; }
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
for m in 0053_precio02_canal_meli.sql 0054_precio02_base.sql; do
  n=$(basename "$m" .sql | cut -c1-4)
  a=$(aplicada "$n")
  [ -n "$a" ] || { echo "ABORTA: la consulta de aplicada de $n salio vacia"; exit 1; }
  if [ "$a" = "t" ]; then echo "ya aplicada: $m"; else echo "pendiente: $m"; PENDIENTES="$PENDIENTES $m"; fi
done

echo "== 1b) Guardas propias de D.1 (abortan si fallan)"
for N in 0053_precio02_canal_meli 0054_precio02_base; do
  git cat-file -e "$APROBADO:migrations/$N.sql" || { echo "ABORTA: $APROBADO no trae $N"; exit 1; }
done
for F in app/precio/cambios.py app/spapi/precio_mercado.py app/cli_precio.py app/precio_mercados.py tests/test_precio02_mercado.py; do
  git cat-file -e "$APROBADO:$F" || { echo "ABORTA: $APROBADO no trae $F (corte 0.b)"; exit 1; }
done
echo "0053/0054 y modulos del corte presentes en el SHA"
echo "guardas propias OK"

echo "== 1c) Cron: D.1 no instala linea; se imprime la huella antes"
if [ "$SIM" = 1 ] && [ ! -f "$CRON_SIM" ]; then
  echo "SIMULACION: siembra del crontab (tres lineas Orbit del ejemplo + linea accounting ajena)"
  printf '%s\n' "10 13 * * * /usr/bin/flock -n /tmp/precio-corrida.lock docker exec orbit-app-1 python -m app.cli precio --platform amazon_mx >> /mnt/data/appdata/orbit/logs/precio-corrida.log 2>&1" > "$CRON_SIM"
  printf '%s\n' "0 3 * * * /opt/accounting/ledger.sh  # accounting (ajena, debe quedar byte-igual)" >> "$CRON_SIM"
fi
HUELLA_ANTES=$(huella_cron) || { echo "ABORTA: no se pudo leer el crontab de gon"; exit 1; }
echo "crontab antes: md5 y lineas = $HUELLA_ANTES (sin tocarlo)"

echo "== 2) Respaldo del codigo actual y SHA (el .env NO se copia a ningun lado)"
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
  pg_dump "$DSN_SIM" --schema-only > "$TMPD/pred1_schema_$STAMP.sql"
  grep -q 'CREATE TABLE public.precio_decision ' "$TMPD/pred1_schema_$STAMP.sql" || { echo "ABORTA: dump invalido"; exit 1; }
  echo "SIMULACION: backup local $TMPD/pred1_schema_$STAMP.sql"
else
  ssh goncloud "set -e; D=$SRV/backups; TMP=\"\$D/.pred1_schema_$STAMP.sql.tmp\"; \
    docker exec orbit-db-1 pg_dump -U orbit -d orbit --schema-only > \"\$TMP\"; \
    [ -s \"\$TMP\" ] && grep -q 'CREATE TABLE public.precio_decision ' \"\$TMP\" \
      && tail -5 \"\$TMP\" | grep -q 'PostgreSQL database dump complete' \
      || { echo 'DUMP INVALIDO'; rm -f \"\$TMP\"; exit 1; }; \
    chmod 600 \"\$TMP\"; mv \"\$TMP\" \"\$D/pred1_schema_$STAMP.sql\"; \
    ls -l \"\$D/pred1_schema_$STAMP.sql\""
fi

echo "== 4) Migraciones 0053 y 0054, cada una en su propia corrida de psql"
if [ -z "$PENDIENTES" ]; then echo "ninguna pendiente"; else
  for m in $PENDIENTES; do
    git show "$APROBADO:migrations/$m" | w
    echo "$m" >> "$APLICADAS"
    echo "aplicada $m (anotada en $APLICADAS)"
  done
fi

echo "== 5) md5 del codigo desplegado vs SHA aprobado"
md5_de() { python3 -c "import hashlib, sys; print(hashlib.md5(sys.stdin.buffer.read()).hexdigest())"; }
ARCHIVOS_D1="app/precio/cambios.py app/spapi/precio_mercado.py app/cli_precio.py app/precio/corrida.py tools/precio_reversa.py tools/precio_cobertura.py"
if [ "$SIM" = 1 ]; then
  echo "SIMULACION: sin servidor; se verifica el respaldo local contra el SHA"
  for f in $ARCHIVOS_D1; do
    a=$(git show "$APROBADO:$f" | md5_de)
    b=$(md5_de < "$TMPD/predeploy-$STAMP/$f")
    [ "$a" = "$b" ] || { echo "ABORTA: md5 distinto en $f"; exit 1; }
    echo "md5 OK $f"
  done
else
  git archive --format=tar "$APROBADO" app Dockerfile .dockerignore pyproject.toml uv.lock tools/precio_reversa.py tools/precio_cobertura.py \
    | ssh goncloud "cd $SRV && tar -xf -"
  for f in $ARCHIVOS_D1; do
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
    TOP2=$(ssh goncloud "docker top orbit-app-1") || true
    CLI2=$(printf '%s\n' "$TOP2" | grep -c 'app\.cli' || true)
    if [ "$CLI2" = "0" ]; then LISTO=1; break; fi
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
else
  ssh goncloud "set -e; cd $SRV; \
    echo DIGEST antes=\$(docker inspect -f '{{.Image}}' orbit-app-1); \
    docker compose up -d --no-deps --build app; \
    echo DIGEST despues=\$(docker inspect -f '{{.Image}}' orbit-app-1); \
    sleep 5; curl -sSf http://127.0.0.1:8010/health; echo; \
    docker ps --filter name=orbit-app-1 --format '{{.Names}} {{.Status}}'"
fi

echo "== 8) Cron: la huella no cambio (D.1 no instala linea)"
HUELLA_DESPUES=$(huella_cron) || { echo "ABORTA: no se pudo releer el crontab de gon"; exit 1; }
echo "crontab despues: md5 y lineas = $HUELLA_DESPUES"
[ "$HUELLA_DESPUES" = "$HUELLA_ANTES" ] || { echo "ABORTA: el crontab cambio durante el despliegue (antes=$HUELLA_ANTES despues=$HUELLA_DESPUES) y D.1 no instala linea: otro proceso lo toco"; exit 1; }
echo "crontab intacto"

echo "== LISTO. STAMP=$STAMP"
echo "Siguiente: bash $DIR/checklist.sh $STAMP"
echo "Reversa:   bash $DIR/rollback.sh $STAMP"
