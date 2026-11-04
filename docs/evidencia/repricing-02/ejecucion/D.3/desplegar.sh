#!/usr/bin/env bash
# Despliegue D.3 de REPRICING 02 (seccion 4): la migracion 0055 + el codigo
# del SHA aprobado (carril F.1-F.4 y S.7), reemplazo de las DOS lineas de
# cron de D.2 por las DOS de precios MX+US mas las DOS del job de muestras
# (12:15 MX, 12:20 US). Patron: ../D.2/desplegar.sh (pasos + cron +
# guardas) + docs/evidencia/bids-02/ejecucion/D.4/desplegar.sh. El paso
# 4b inserta las politicas FBM (idempotente por label); tras el deploy
# solo queda checklist.sh: el dia del despliegue no hay muestras hasta
# las 12:15 UTC (checklist 3 hasta la primera corrida del job y de la
# estimacion, 0 despues). Corre lejos de :45 (la estimacion vieja cada
# 6 h choca con el CHECK 0055 si cae entre la migracion y el recreate).
# Sus guardas propias: el SHA trae la 0055 (+ reversa), los modulos de
# F.1-F.4 y S.7 y las CUATRO lineas de cron en docs/DEPLOY.md; si una
# falla, aborta. CALCULA el crontab en 1c e
# INSTALA en 7b (tras el health del contenedor nuevo y tras correr
# `precio --reporte` de MX y US en 7v: si alguno no sale 0, aborta sin
# instalar nada). La guarda solo_previsto permite EXACTAMENTE 2 bajas
# (las de D.2 con --platform amazon_mx solo) y 4 altas (2 de precios
# MX+US + 2 de muestras); otra cosa aborta sin tocar nada. Tras
# instalar, RELEE el crontab real, comprueba que el diff contra el
# respaldo trae SOLO lo previsto y, si trae otra cosa, RESTAURA el
# respaldo y aborta. 7b es UNA ruta sim+real. El preflight cuenta
# `app.cli` Y `app.envio_muestras` (D.3 en adelante).
# En simulacion (ORBIT_SIMULACION=1) todo lo de datos corre de verdad
# contra ORBIT_SIM_DSN, el crontab es el archivo
# ${TMPDIR:-/tmp}/orbit-d3-sim/crontab-gon (sembrado con las 2 lineas
# viejas reales + una linea accounting ajena, que debe quedar byte-igual)
# y lo de servidor (docker, build) se marca SIMULACION y no se toca: no
# hay ssh ni mutacion fuera de la base sim. ORBIT_D3_SIM_FALLA_CRON=1
# (solo simulacion, solo para la prueba del guard) siembra una linea
# ajena entre la instalacion y la verificacion de 7b: el guard debe
# restaurar el respaldo y abortar.
# Uso: bash docs/evidencia/repricing-02/ejecucion/D.3/desplegar.sh <sha de origin/master con CI verde>
set -euo pipefail

APROBADO=${1:?uso: desplegar.sh <sha de origin/master con CI verde>}
REPO=$(git rev-parse --show-toplevel)
SRV=/mnt/data/appdata/orbit
STAMP=$(date -u +%Y%m%d-%H%M)
DIR=docs/evidencia/repricing-02/ejecucion/D.3
SIM=${ORBIT_SIMULACION:-0}
DSN_SIM=${ORBIT_SIM_DSN:-postgresql://orbit:orbit@localhost:5432/orbit_sim_D3}
# C-W6: el DSN solo se muestra enmascarado (`orbit:***@`); psql/pg_dump
# siguen usando $DSN_SIM real (la conexion no cambia).
DSN_VER=$(printf '%s' "$DSN_SIM" | sed 's|://[^@]*@|://orbit:***@|')
TMPD=${TMPDIR:-/tmp}/orbit-d3-sim
cd "$REPO"
trap 'echo "FALLO en la linea $LINENO. Si ya se aplico alguna migracion o se toco el crontab: bash $DIR/rollback.sh $STAMP"' ERR
# Las 2 lineas viejas de precios (D.2), caracter por caracter (leidas del
# crontab de gon en produccion el dia del paquete; la guarda 1c exige
# verlas exactamente asi antes de reemplazarlas).
VIEJA1='10 13 * * * /usr/bin/flock -n /tmp/precio-spapi.lock docker exec orbit-app-1 python -m app.cli precio --platform amazon_mx >> /mnt/data/appdata/orbit/logs/precio-corrida.log 2>&1'
VIEJA2='10 15-23/2 * * * /usr/bin/flock -n /tmp/precio-spapi.lock docker exec orbit-app-1 python -m app.cli precio --platform amazon_mx >> /mnt/data/appdata/orbit/logs/precio-corrida.log 2>&1'

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
  local top cli muestras
  top=$(ssh goncloud "docker top orbit-app-1") || return 1
  cli=$(printf '%s\n' "$top" | grep -c 'app\.cli' || true)
  muestras=$(printf '%s\n' "$top" | grep -c 'app\.envio_muestras' || true)
  [ "$cli" = "0" ] && [ "$muestras" = "0" ]
}
# aplicada <numero>: t si la migracion ya esta aplicada.
aplicada() {
  case "$1" in
    0055) echo "SELECT count(*) = 2 FROM pg_constraint WHERE conname IN ('estimacion_escenario_disponible_exige_origen', 'estimacion_escenario_disponible_exige_fees_partidos');" | q ;;
    *) echo "ABORTA: migracion $1 sin consulta de aplicada en la guia" >&2; exit 1 ;;
  esac
}
# instalar_cron <actual> <nuevo> <vieja1> <vieja2> <l1> <l2> <l3> <l4>:
# reemplaza las 2 viejas por las 4 nuevas en el sitio de la primera.
# Idempotente: si las viejas ya no estan y las 4 nuevas si, no toca
# nada. Si falta alguna nueva sin las viejas, aborta (estado inesperado).
instalar_cron() {
  python3 - "$1" "$2" "$3" "$4" "$5" "$6" "$7" "$8" <<'PY'
import sys
actual, nuevo, v1, v2, l1, l2, l3, l4 = sys.argv[1:9]
viejas = {v1.strip(), v2.strip()}
nuevas = [l1, l2, l3, l4]
with open(actual) as f:
    lineas = f.read().splitlines()
norm = [ln.strip() for ln in lineas]
if v1.strip() in norm or v2.strip() in norm:
    fuera = []
    puestas = False
    for ln in lineas:
        if ln.strip() in viejas:
            if not puestas:
                fuera += nuevas
                puestas = True
        else:
            fuera.append(ln)
    if norm.count(v1.strip()) != 1 or norm.count(v2.strip()) != 1:
        sys.exit("ABORTA: alguna vieja aparece != 1 vez (esperaba 1 cada una)")
    print("cron: 2 viejas reemplazadas por las 4 nuevas")
    lineas = fuera
elif all(n.strip() in norm for n in nuevas):
    print("cron: las 4 nuevas ya estaban instaladas")
else:
    sys.exit("ABORTA: sin viejas ni las 4 nuevas (estado inesperado del crontab)")
with open(nuevo, "w") as f:
    f.write("\n".join(lineas) + "\n")
PY
}
solo_previsto() {  # solo_previsto <base> <nuevo>: el diff trae EXACTAMENTE 2 bajas + 4 altas
  # El diff va a un archivo: con pipefail, `diff | grep` miente (diff
  # sale 1 cuando hay diferencias y el if nunca entraria).
  diff "$1" "$2" > "$2.diff" || [ "$?" = 1 ]
  grep '^<' "$2.diff" | sed 's/^< //' > "$2.bajas" || true
  grep '^>' "$2.diff" | sed 's/^> //' > "$2.agregadas" || true
  [ "$(grep -c . "$2.bajas" || true)" = 2 ] || {
    echo "ABORTA: el diff trae $(grep -c . "$2.bajas" || true) baja(s), esperaba 2; no se procede"
    cat "$2.diff"
    exit 1
  }
  for v in "$VIEJA1" "$VIEJA2"; do
    grep -qFx -- "$v" "$2.bajas" || {
      echo "ABORTA: la baja no es una vieja prevista: $v; no se procede"
      cat "$2.diff"
      exit 1
    }
  done
  [ "$(grep -c . "$2.agregadas" || true)" = 4 ] || {
    echo "ABORTA: el diff trae $(grep -c . "$2.agregadas" || true) alta(s), esperaba 4; no se procede"
    cat "$2.diff"
    exit 1
  }
  while IFS= read -r ln; do
    { [ "$ln" = "$LINEA1" ] || [ "$ln" = "$LINEA2" ] || [ "$ln" = "$LINEA3" ] || [ "$ln" = "$LINEA4" ]; } || {
      echo "ABORTA: el diff trae una linea no prevista: $ln; no se procede"; exit 1
    }
  done < "$2.agregadas"
}
cron_leer() {  # vuelca el crontab ACTUAL a stdout
  if [ "$SIM" = 1 ]; then
    cat "$CRON_SIM"
  else
    ssh goncloud "crontab -l -u gon 2>/dev/null"
  fi
}
cron_instalar() {  # instala el crontab que llega por stdin
  if [ "$SIM" = 1 ]; then
    cat > "$CRON_SIM"
  else
    ssh goncloud "crontab -u gon -"
  fi
}

if [ "$SIM" = 1 ]; then
  echo "== MODO SIMULACION: datos sinteticos contra $DSN_VER, servidor intacto"
  mkdir -p "$TMPD"
  APLICADAS="$TMPD/aplicadas-$STAMP.txt"
  CRON_SIM="$TMPD/crontab-gon"
  RESPALDO_CRON="$TMPD/crontab-gon-pre-d3-$STAMP.txt"
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
  git cat-file -e "$APROBADO:migrations/0055_precio02_escenario_exige.sql" || { echo "ABORTA: $APROBADO no trae la 0055"; exit 1; }
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
  MUESTRAS=$(printf '%s\n' "$TOP" | grep -c 'app\.envio_muestras' || true)
  { [ "$CLI" = "0" ] && [ "$MUESTRAS" = "0" ]; } || { echo "ABORTA: hay app.cli ($CLI) o app.envio_muestras ($MUESTRAS) corriendo"; exit 1; }
  echo "cli=0 muestras=0"
fi
RUNNING=$(echo "SELECT count(*) FROM optimizer_cycle WHERE status = 'running';" | q)
[ "$RUNNING" = "0" ] || { echo "ABORTA: hay $RUNNING ciclo(s) en running"; exit 1; }
echo "ciclos running=0"
H1=$(echo "SELECT count(*) FROM apply_queue WHERE kind = 'harvest' AND estado NOT IN ('applied','failed','vetoed','discarded');" | q)
H2=$(echo "SELECT count(*) FROM harvest_job WHERE fase IN ('pending','negative_created','exact_created','hermanas_negadas');" | q)
[ "$H1" = "0" ] && [ "$H2" = "0" ] || { echo "ABORTA: harvest en vuelo ($H1 en cola, $H2 jobs)"; exit 1; }
echo "harvest en vuelo=0"
PENDIENTES=""
for m in 0055_precio02_escenario_exige.sql; do
  n=$(basename "$m" .sql | cut -c1-4)
  a=$(aplicada "$n")
  [ -n "$a" ] || { echo "ABORTA: la consulta de aplicada de $n salio vacia"; exit 1; }
  if [ "$a" = "t" ]; then echo "ya aplicada: $m"; else echo "pendiente: $m"; PENDIENTES="$PENDIENTES $m"; fi
done

echo "== 1b) Guardas propias de D.3 (abortan si fallan)"
for N in 0055_precio02_escenario_exige 0055_reversa_precio02_escenario_exige; do
  git cat-file -e "$APROBADO:migrations/$N.sql" || { echo "ABORTA: $APROBADO no trae $N"; exit 1; }
done
for F in app/ledger_atribucion.py app/precio/envio.py app/envio_muestras.py app/estimacion_universo.py app/estimacion_amazon.py app/spapi/precio_mercado.py app/precio/ventas.py tools/precio_sonda.py; do
  git cat-file -e "$APROBADO:$F" || { echo "ABORTA: $APROBADO no trae $F (F.1-F.4 o S.7)"; exit 1; }
done
git show "$APROBADO:app/precio/ventas.py" | grep -q 'HistoriaUnidad' || { echo "ABORTA: ventas.py del SHA sin HistoriaUnidad (S.7)"; exit 1; }
git show "$APROBADO:app/precio/reglas.py" | grep -q 'lineal' || { echo "ABORTA: reglas.py del SHA sin lineal (S.7)"; exit 1; }
echo "0055 (+ reversa) y modulos F.1-F.4 y S.7 presentes en el SHA"
for T in tools/precio_sonda.py tools/precio_cobertura.py; do
  git show "$APROBADO:Dockerfile" | grep -q "COPY $T" || { echo "ABORTA: Dockerfile del SHA sin COPY $T (sonda/checklist lo corren dentro)"; exit 1; }
done
echo "COPY de sonda y cobertura en el Dockerfile OK"
LINEA1=$(git show "$APROBADO:docs/DEPLOY.md" | grep -E '^10 13 .*precio-spapi\.lock.*amazon_us')
LINEA2=$(git show "$APROBADO:docs/DEPLOY.md" | grep -E '^10 15-23/2 .*precio-spapi\.lock.*amazon_us')
LINEA3=$(git show "$APROBADO:docs/DEPLOY.md" | grep -E '^15 12 .*envio_muestras.*amazon_mx')
LINEA4=$(git show "$APROBADO:docs/DEPLOY.md" | grep -E '^20 12 .*envio_muestras.*amazon_us')
for i in 1 2 3 4; do
  eval "ln=\$LINEA$i"
  [ "$(printf '%s\n' "$ln" | grep -c .)" = 1 ] || { echo "ABORTA: DEPLOY.md del SHA no trae exactamente la linea $i de D.3"; exit 1; }
done
echo "las 4 lineas de cron en el SHA OK"
echo "guardas propias OK"

echo "== 1c) Cron (guarda 5): respaldo, CALCULAR el reemplazo, diff solo previsto (se instala en 7b)"
INSTALAR_CRON=0
if [ "$SIM" = 1 ]; then
  if [ ! -f "$CRON_SIM" ]; then
    echo "SIMULACION: siembra del crontab pre-D.3 (las 2 viejas reales + linea accounting ajena)"
    printf '%s\n' "$VIEJA1" "$VIEJA2" > "$CRON_SIM"
    echo "0 3 * * * /opt/accounting/ledger.sh  # accounting (ajena, debe quedar byte-igual)" >> "$CRON_SIM"
  fi
  cp "$CRON_SIM" "$RESPALDO_CRON"
  instalar_cron "$CRON_SIM" "$CRON_SIM.nuevo" "$VIEJA1" "$VIEJA2" "$LINEA1" "$LINEA2" "$LINEA3" "$LINEA4"
  NUEVO="$CRON_SIM.nuevo"
  RESPALDO="$RESPALDO_CRON"
else
  mkdir -p "$TMPD"
  ssh goncloud "crontab -l -u gon 2>/dev/null" > "$RESPALDO_LOCAL" || { echo "ABORTA: no se pudo leer el crontab de gon"; exit 1; }
  instalar_cron "$RESPALDO_LOCAL" "$NUEVO_LOCAL" "$VIEJA1" "$VIEJA2" "$LINEA1" "$LINEA2" "$LINEA3" "$LINEA4"
  NUEVO="$NUEVO_LOCAL"
  RESPALDO="$RESPALDO_LOCAL"
fi
if diff -q "$RESPALDO" "$NUEVO" >/dev/null 2>&1; then
  # C-W1 (idempotencia real): diff vacio CON las 4 nuevas presentes = el
  # cron ya quedo instalado (reintento): se avisa y se sigue con
  # INSTALAR_CRON=0. `solo_previsto` sigue estricto en lo demas.
  if grep -qFx -- "$LINEA1" "$NUEVO" && grep -qFx -- "$LINEA2" "$NUEVO" && grep -qFx -- "$LINEA3" "$NUEVO" && grep -qFx -- "$LINEA4" "$NUEVO"; then
    echo "cron ya instalado (las 4 nuevas presentes, diff vacio): se sigue sin instalar en 7b"
  else
    solo_previsto "$RESPALDO" "$NUEVO"
  fi
else
  solo_previsto "$RESPALDO" "$NUEVO"
  echo "diff solo con lo previsto (2 bajas + 4 altas):"
  cat "$NUEVO.bajas" "$NUEVO.agregadas"
  # El crontab NO se toca aqui: instalar antes de migrar y recrear dejaria
  # las lineas nuevas invocando codigo que la imagen vieja no trae si un
  # paso posterior aborta. Se instala en 7b, tras el health del contenedor
  # nuevo y tras el reporte de 7v; un abort previo deja el crontab intacto.
  INSTALAR_CRON=1
  echo "crontab calculado en $NUEVO (se instala en 7b; respaldo en $RESPALDO)"
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
  pg_dump "$DSN_SIM" --schema-only > "$TMPD/pred3_schema_$STAMP.sql"
  grep -q 'CREATE TABLE public.precio_decision ' "$TMPD/pred3_schema_$STAMP.sql" || { echo "ABORTA: dump invalido"; exit 1; }
  echo "SIMULACION: backup local $TMPD/pred3_schema_$STAMP.sql"
else
  ssh goncloud "set -e; D=$SRV/backups; TMP=\"\$D/.pred3_schema_$STAMP.sql.tmp\"; \
    docker exec orbit-db-1 pg_dump -U orbit -d orbit --schema-only > \"\$TMP\"; \
    [ -s \"\$TMP\" ] && grep -q 'CREATE TABLE public.precio_decision ' \"\$TMP\" \
      && tail -5 \"\$TMP\" | grep -q 'PostgreSQL database dump complete' \
      || { echo 'DUMP INVALIDO'; rm -f \"\$TMP\"; exit 1; }; \
    chmod 600 \"\$TMP\"; mv \"\$TMP\" \"\$D/pred3_schema_$STAMP.sql\"; \
    ls -l \"\$D/pred3_schema_$STAMP.sql\""
fi

echo "== 4) Migracion 0055 en su propio psql -1"
if [ -z "$PENDIENTES" ]; then echo "ninguna pendiente"; else
  for m in $PENDIENTES; do
    git show "$APROBADO:migrations/$m" | w
    echo "$m" >> "$APLICADAS"
    echo "aplicada $m (anotada en $APLICADAS)"
  done
fi

echo "== 4b) Politicas FBM (idempotente por label; fuente: FBA vigente)"
echo "SELECT id, label, valid_from FROM estimacion_politica_version WHERE universo = 'amazon_mx/fba' AND valid_to IS NULL;" | q
FUENTE_FBM=$(echo "SELECT count(*) FROM estimacion_politica_version WHERE universo = 'amazon_mx/fba' AND valid_to IS NULL;" | q)
[ "$FUENTE_FBM" = 1 ] || { echo "ABORTA: 4b ve $FUENTE_FBM FBA vigentes, esperaba 1 (fuente ambigua)"; exit 1; }
w <<'SQL'
INSERT INTO estimacion_politica_version
    (label, universo, formula_version, settings, valid_from)
SELECT 'repricing02_fbm_mx', 'amazon_mx/fbm', 'S3',
       settings || '{"logistica_semantica": "L sale de precio_envio_muestra, no de esta constante"}'::jsonb,
       CURRENT_DATE
  FROM estimacion_politica_version
 WHERE universo = 'amazon_mx/fba' AND valid_to IS NULL
   AND NOT EXISTS (
           SELECT 1 FROM estimacion_politica_version WHERE label = 'repricing02_fbm_mx'
       );
INSERT INTO estimacion_politica_version
    (label, universo, formula_version, settings, valid_from)
SELECT 'repricing02_fbm_us', 'amazon_us/fbm', 'S3',
       settings || '{"iva_divisor": "1", "precio_incluye_iva": false, "isr_tasa": "0.0207",
                      "logistica_semantica": "L sale de precio_envio_muestra, no de esta constante"}'::jsonb,
       CURRENT_DATE
  FROM estimacion_politica_version
 WHERE universo = 'amazon_mx/fba' AND valid_to IS NULL
   AND NOT EXISTS (
           SELECT 1 FROM estimacion_politica_version WHERE label = 'repricing02_fbm_us'
       );
SQL
echo "SELECT id, label, universo, valid_from, valid_to FROM estimacion_politica_version WHERE label LIKE 'repricing02_fbm%' ORDER BY id;" | q
CUENTA_FBM=$(echo "SELECT count(*) FROM estimacion_politica_version WHERE label LIKE 'repricing02_fbm%' AND valid_to IS NULL;" | q)
[ "$CUENTA_FBM" = 2 ] || { echo "ABORTA: 4b deja $CUENTA_FBM politicas FBM vigentes, esperaba 2"; exit 1; }

echo "== 5) md5 del codigo desplegado vs SHA aprobado"
md5_de() { python3 -c "import hashlib, sys; print(hashlib.md5(sys.stdin.buffer.read()).hexdigest())"; }
ARCHIVOS_D3="app/ledger_atribucion.py app/precio/envio.py app/envio_muestras.py app/estimacion_universo.py app/estimacion_amazon.py app/estimacion_insumos.py app/estimacion_fees.py app/estimacion_repository.py app/spapi/precio_mercado.py app/precio/ventas.py app/precio/reglas.py app/precio/tipos.py app/precio/corrida.py tools/precio_sonda.py"
if [ "$SIM" = 1 ]; then
  echo "SIMULACION: sin servidor; se verifica el respaldo local contra el SHA"
  for f in $ARCHIVOS_D3; do
    a=$(git show "$APROBADO:$f" | md5_de)
    b=$(md5_de < "$TMPD/predeploy-$STAMP/$f")
    [ "$a" = "$b" ] || { echo "ABORTA: md5 distinto en $f"; exit 1; }
    echo "md5 OK $f"
  done
else
  git archive --format=tar "$APROBADO" app Dockerfile .dockerignore pyproject.toml uv.lock tools/precio_sonda.py tools/precio_reversa.py tools/precio_cobertura.py \
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
    echo "intento $i/60: app.cli o app.envio_muestras en curso o sin respuesta; reintento en 30s"
    sleep 30
  done
  if [ "$LISTO" != "1" ]; then
    echo "DETENIDO: las guardas no pasaron en 30 minutos. Me detengo SIN recrear el contenedor y SIN correr la reversa."
    echo "El contenedor viejo sigue corriendo su imagen anterior: la migracion aplicada y el codigo copiado no lo tocan."
    echo "Para terminar: espera a que docker top orbit-app-1 no liste app.cli ni app.envio_muestras y corre: ssh goncloud 'cd $SRV && docker compose up -d --no-deps --build app'"
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

echo "== 7v) Corre precio --reporte de MX y US con el codigo nuevo (solo lectura; si alguno no sale 0, el cron queda sin instalar)"
AYER=$(date -u -d '1 day ago' +%F 2>/dev/null || date -u -v-1d +%F)
HOY=$(date -u +%F)
if [ "$SIM" = 1 ]; then
  PY="$REPO/.venv/bin/python"
  [ -x "$PY" ] || PY=python3
  for PLAT in amazon_mx amazon_us; do
    ORBIT_DSN_READ="$DSN_SIM" "$PY" -m app.cli precio --reporte --platform "$PLAT" --desde "$AYER" --hasta "$HOY" || { echo "ABORTA: precio --reporte $PLAT no salio 0 en sim; el cron queda sin instalar"; exit 1; }
  done
else
  for PLAT in amazon_mx amazon_us; do
    ssh goncloud "docker exec orbit-app-1 python -m app.cli precio --reporte --platform $PLAT --desde $AYER --hasta $HOY" || { echo "ABORTA: precio --reporte $PLAT no salio 0; el cron queda sin instalar"; exit 1; }
  done
fi
echo "reporte OK: MX y US salieron 0"

echo "== 7b) Cron: RELEER el crontab actual, instalar y VERIFICAR contra el respaldo (restaura+aborta si difiere)"
if [ "$INSTALAR_CRON" = 1 ]; then
  # E6: entre 1c y 7b otro loop pudo agregar lineas; instalar lo
  # calculado en 1c las borraria. Se recalcula sobre el actual y se
  # revalida el diff antes de instalar. Y tras instalar se RELEE el
  # crontab real y se verifica de nuevo: si el diff contra el respaldo
  # trae otra cosa (un escritor gano la carrera), se RESTAURA el
  # respaldo y se aborta. C-W4: este bloque es UNO solo para sim y real
  # (lo que la sim prueba es lo que corre en real); lo unico que cambia
  # de modo son `cron_leer`/`cron_instalar` y donde caen los temporales.
  if [ "$SIM" = 1 ]; then
    ACTUAL_7B="$TMPD/actual-7b-$STAMP.txt"
    RESPALDO_7B="$RESPALDO_CRON"
    INSTALADO_7B="$TMPD/instalado-7b-$STAMP.txt"
    FALLA_7B="$TMPD/falla-cron-$STAMP.txt"
  else
    ACTUAL_7B="$NUEVO_LOCAL.actual"
    RESPALDO_7B="$NUEVO_LOCAL.7b-respaldo"
    INSTALADO_7B="$NUEVO_LOCAL.instalado"
    FALLA_7B="$TMPD-falla-cron-$STAMP.txt"
  fi
  cron_leer > "$ACTUAL_7B" || { echo "ABORTA: no se pudo releer el crontab actual"; exit 1; }
  instalar_cron "$ACTUAL_7B" "$NUEVO.7b" "$VIEJA1" "$VIEJA2" "$LINEA1" "$LINEA2" "$LINEA3" "$LINEA4"
  solo_previsto "$ACTUAL_7B" "$NUEVO.7b"
  cp "$ACTUAL_7B" "$RESPALDO_7B"
  if [ "$SIM" != 1 ]; then
    ssh goncloud "mkdir -p $SRV/backups && cat > $SRV/backups/crontab-gon-pre-d3-$STAMP.txt" < "$ACTUAL_7B"
  fi
  cron_instalar < "$NUEVO.7b"
  if [ "$SIM" = 1 ] && [ "${ORBIT_D3_SIM_FALLA_CRON:-0}" = 1 ]; then
    echo "SIMULACION: falla sembrada (ORBIT_D3_SIM_FALLA_CRON=1): un escritor ajeno mete una linea tras instalar" >&2
    echo "0 4 * * * /opt/ajeno/carrera.sh  # escritor ajeno sembrado" >> "$CRON_SIM"
  fi
  cron_leer > "$INSTALADO_7B" || { echo "ABORTA: no se pudo releer el crontab tras instalar"; exit 1; }
  # Subshell: solo_previsto sale con exit y aqui el fallo debe restaurar,
  # no salir directo.
  if (solo_previsto "$RESPALDO_7B" "$INSTALADO_7B") 2>"$FALLA_7B"; then
    rm -f "$NUEVO.diff" "$NUEVO.agregadas" "$NUEVO.bajas" "$NUEVO.7b.diff" "$NUEVO.7b.agregadas" "$NUEVO.7b.bajas" "$FALLA_7B"
    if [ "$SIM" = 1 ]; then
      echo "SIMULACION: crontab instalado y verificado en $CRON_SIM (respaldo en $RESPALDO_7B)"
    else
      echo "crontab instalado y verificado (respaldo en $SRV/backups/crontab-gon-pre-d3-$STAMP.txt)"
    fi
  else
    cat "$FALLA_7B" >&2
    cron_instalar < "$RESPALDO_7B"
    echo "ABORTA: el crontab instalado trae lineas no previstas; RESPALDO RESTAURADO, nada cambio" >&2
    exit 1
  fi
else
  echo "nada que instalar en 7b (1c no dejo trabajo pendiente)"
fi

echo "== LISTO. STAMP=$STAMP"
echo "Siguiente: bash $DIR/checklist.sh $STAMP"
echo "Reversa:   bash $DIR/rollback.sh $STAMP"
