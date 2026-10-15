#!/usr/bin/env bash
# Despliegue D.2 de REPRICING 02 (seccion 3): migraciones (0059 y 0058, en
# ese orden) + el codigo del SHA aprobado (S.1 a S.6 y el carril G) y el
# reemplazo de la linea de cron de precios por las DOS de la guia (diaria
# 13:10 + repasos cada 2 h, lock precio-spapi.lock). Patron:
# docs/evidencia/repricing-02/ejecucion/D.1/desplegar.sh (pasos) +
# docs/evidencia/bids-02/ejecucion/D.4/desplegar.sh (cron + guardas).
# DESVIACION contra la guia (documentada en evidencia.md): la guia dice
# que D.2 lleva solo la 0058, pero produccion no trae aplicada la 0059
# (R03k, PR #453; verificada ausente por lectura el dia del paquete) y el
# codigo de S.2+ exige el trigger reparado: D.2 aplica la 0059 ANTES de
# la 0058.
# Sus guardas propias: el SHA trae la 0058 (+ reversa), la 0059 (+
# reversa), los modulos de S.1-S.6 y G.1-G.3, la rama de modos de precio
# en config_write.py, resumen_diario sellado en notifica.py y las DOS
# lineas de cron en docs/DEPLOY.md; si una falla, aborta. CALCULA el
# crontab en 1c e INSTALA en 7b (tras el health del contenedor nuevo y
# tras correr `precio --reporte` una vez en 7v: si no sale 0, aborta sin
# instalar nada). La guarda solo_previsto permite EXACTAMENTE una baja
# (la linea vieja con precio-corrida.lock) y dos altas (las de
# precio-spapi.lock); otra cosa aborta sin tocar nada. La guarda NUEVA de
# 7b (el bloque JEV solo respaldaba y mostraba diff): tras instalar,
# RELEE el crontab real, comprueba que el diff contra el respaldo trae
# SOLO lo previsto y, si trae otra cosa, RESTAURA el respaldo y aborta.
# C-W4: 7b es UNA ruta sim+real (`cron_leer`/`cron_instalar` cambian; el
# resto es el mismo codigo, asi que la sim prueba lo que corre en real).
# En simulacion (ORBIT_SIMULACION=1) todo lo de datos corre de verdad
# contra ORBIT_SIM_DSN, el crontab es el archivo
# ${TMPDIR:-/tmp}/orbit-d2-sim/crontab-gon (sembrado con la linea vieja
# real + una linea accounting ajena, que debe quedar byte-igual) y lo de
# servidor (docker, build) se marca SIMULACION y no se toca: no hay ssh
# ni mutacion fuera de la base sim. ORBIT_D2_SIM_FALLA_CRON=1 (solo
# simulacion, solo para la prueba del guard) siembra una linea ajena
# entre la instalacion y la verificacion de 7b: el guard debe restaurar
# el respaldo y abortar.
# Uso: bash docs/evidencia/repricing-02/ejecucion/D.2/desplegar.sh <sha de origin/master con CI verde>
set -euo pipefail

APROBADO=${1:?uso: desplegar.sh <sha de origin/master con CI verde>}
REPO=$(git rev-parse --show-toplevel)
SRV=/mnt/data/appdata/orbit
STAMP=$(date -u +%Y%m%d-%H%M)
DIR=docs/evidencia/repricing-02/ejecucion/D.2
SIM=${ORBIT_SIMULACION:-0}
DSN_SIM=${ORBIT_SIM_DSN:-postgresql://orbit:orbit@localhost:5432/orbit_sim_D2}
# C-W6: el DSN solo se muestra enmascarado (`orbit:***@`); psql/pg_dump
# siguen usando $DSN_SIM real (la conexion no cambia).
DSN_VER=$(printf '%s' "$DSN_SIM" | sed 's|://[^@]*@|://orbit:***@|')
TMPD=${TMPDIR:-/tmp}/orbit-d2-sim
cd "$REPO"
trap 'echo "FALLO en la linea $LINENO. Si ya se aplico alguna migracion o se toco el crontab: bash $DIR/rollback.sh $STAMP"' ERR
# La linea vieja de precios, caracter por caracter (leida del crontab de
# gon en produccion el dia del paquete; la guarda 1c exige verla
# exactamente asi antes de reemplazarla).
VIEJA='10 13 * * * /usr/bin/flock -n /tmp/precio-corrida.lock docker exec orbit-app-1 python -m app.cli precio --platform amazon_mx >> /mnt/data/appdata/orbit/logs/precio-corrida.log 2>&1'

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
# aplicada <numero>: t si la migracion ya esta aplicada.
aplicada() {
  case "$1" in
    0058) echo "SELECT count(*) = 2 FROM pg_constraint WHERE conname IN ('precio_decision_l_exige_origen', 'precio_decision_mover_exige_verificacion');" | q ;;
    0059) echo "SELECT pg_get_functiondef('precio_retencion_coherente()'::regprocedure) LIKE '%v_corr_fecha%';" | q ;;
    *) echo "ABORTA: migracion $1 sin consulta de aplicada en la guia" >&2; exit 1 ;;
  esac
}
# instalar_cron <actual> <nuevo> <vieja> <linea1> <linea2>: reemplaza la
# linea vieja por las dos nuevas. Idempotente: si la vieja ya no esta y
# las dos nuevas si, no toca nada. Si la vieja no esta y falta alguna
# nueva, aborta (estado inesperado: no se inventa).
instalar_cron() {
  python3 - "$1" "$2" "$3" "$4" "$5" <<'PY'
import sys
actual, nuevo, vieja, l1, l2 = sys.argv[1:6]
with open(actual) as f:
    lineas = f.read().splitlines()
if vieja.strip() in [ln.strip() for ln in lineas]:
    fuera = []
    cambio = 0
    for ln in lineas:
        if ln.strip() == vieja.strip():
            if cambio == 0:
                fuera += [l1, l2]
            cambio += 1
        else:
            fuera.append(ln)
    if cambio != 1:
        sys.exit(f"ABORTA: la linea vieja aparece {cambio} veces, esperaba 1")
    print("cron: linea vieja reemplazada por las 2 nuevas")
    lineas = fuera
elif l1.strip() in [ln.strip() for ln in lineas] and l2.strip() in [ln.strip() for ln in lineas]:
    print("cron: las 2 nuevas ya estaban instaladas")
else:
    sys.exit("ABORTA: sin linea vieja ni las 2 nuevas (estado inesperado del crontab)")
with open(nuevo, "w") as f:
    f.write("\n".join(lineas) + "\n")
PY
}
solo_previsto() {  # solo_previsto <base> <nuevo>: el diff trae EXACTAMENTE 1 baja + 2 altas
  # El diff va a un archivo: con pipefail, `diff | grep` miente (diff
  # sale 1 cuando hay diferencias y el if nunca entraria).
  diff "$1" "$2" > "$2.diff" || [ "$?" = 1 ]
  grep '^<' "$2.diff" | sed 's/^< //' > "$2.bajas" || true
  grep '^>' "$2.diff" | sed 's/^> //' > "$2.agregadas" || true
  [ "$(grep -c . "$2.bajas" || true)" = 1 ] || {
    echo "ABORTA: el diff trae $(grep -c . "$2.bajas" || true) baja(s), esperaba 1; no se procede"
    cat "$2.diff"
    exit 1
  }
  [ "$(cat "$2.bajas")" = "$VIEJA" ] || {
    echo "ABORTA: la baja no es la linea vieja prevista; no se procede"
    cat "$2.diff"
    exit 1
  }
  [ "$(grep -c . "$2.agregadas" || true)" = 2 ] || {
    echo "ABORTA: el diff trae $(grep -c . "$2.agregadas" || true) alta(s), esperaba 2; no se procede"
    cat "$2.diff"
    exit 1
  }
  while IFS= read -r ln; do
    { [ "$ln" = "$LINEA1" ] || [ "$ln" = "$LINEA2" ]; } || {
      echo "ABORTA: el diff trae una linea no prevista: $ln; no se procede"; exit 1
    }
  done < "$2.agregadas"
}
# C-W4: 7b verifica y restaura por UNA ruta compartida sim+real; solo
# difieren estas dos primitivas (leer el crontab actual, instalar uno).
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
  RESPALDO_CRON="$TMPD/crontab-gon-pre-d2-$STAMP.txt"
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
  git cat-file -e "$APROBADO:migrations/0058_precio02_decision_exige.sql" || { echo "ABORTA: $APROBADO no trae la 0058"; exit 1; }
  git cat-file -e "$APROBADO:migrations/0059_precio02_repara_retencion_coherente.sql" || { echo "ABORTA: $APROBADO no trae la 0059"; exit 1; }
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
for m in 0059_precio02_repara_retencion_coherente.sql 0058_precio02_decision_exige.sql; do
  n=$(basename "$m" .sql | cut -c1-4)
  a=$(aplicada "$n")
  [ -n "$a" ] || { echo "ABORTA: la consulta de aplicada de $n salio vacia"; exit 1; }
  if [ "$a" = "t" ]; then echo "ya aplicada: $m"; else echo "pendiente: $m"; PENDIENTES="$PENDIENTES $m"; fi
done

echo "== 1b) Guardas propias de D.2 (abortan si fallan)"
for N in 0058_precio02_decision_exige 0058_reversa_precio02_decision_exige 0059_precio02_repara_retencion_coherente 0059_reversa_precio02_repara_retencion_coherente; do
  git cat-file -e "$APROBADO:migrations/$N.sql" || { echo "ABORTA: $APROBADO no trae $N"; exit 1; }
done
for F in app/precio/reglas.py app/precio/compuerta.py app/precio/liberaciones.py app/templates/_precios_seguridad.html app/precio/siembra.py app/api_precios_write.py app/templates/_precios_goals.html app/static/js/precios.js tools/precio_reversa.py; do
  git cat-file -e "$APROBADO:$F" || { echo "ABORTA: $APROBADO no trae $F (S.1-S.6 o carril G)"; exit 1; }
done
git cat-file -e "$APROBADO:app/precio/cuota.py" 2>/dev/null && { echo "ABORTA: $APROBADO todavia trae app/precio/cuota.py (S.1 borra el cupo)"; exit 1; }
echo "cuota.py ausente (S.1 borro el cupo)"
git show "$APROBADO:app/config_write.py" | grep -q 'precio_modo_global' || { echo "ABORTA: config_write.py del SHA sin rama de modos de precio (S.2)"; exit 1; }
git show "$APROBADO:app/notifica.py" | grep -q '"resumen_diario"' || { echo "ABORTA: notifica.py del SHA sin resumen_diario sellado (S.6)"; exit 1; }
echo "0058/0059 (+ reversas), modulos S.1-S.6 y G.1-G.3, modos S.2 y resumen S.6 presentes en el SHA"
LINEA1=$(git show "$APROBADO:docs/DEPLOY.md" | grep -E '^10 13 .*precio-spapi\.lock')
LINEA2=$(git show "$APROBADO:docs/DEPLOY.md" | grep -E '^10 15-23/2 .*precio-spapi\.lock')
[ "$(printf '%s\n' "$LINEA1" | grep -c .)" = 1 ] || { echo "ABORTA: DEPLOY.md del SHA no trae exactamente la linea diaria de precio-spapi.lock"; exit 1; }
[ "$(printf '%s\n' "$LINEA2" | grep -c .)" = 1 ] || { echo "ABORTA: DEPLOY.md del SHA no trae exactamente la linea de repasos de precio-spapi.lock"; exit 1; }
echo "las 2 lineas de cron en el SHA OK"
echo "guardas propias OK"

echo "== 1c) Cron (guarda 5): respaldo, CALCULAR el reemplazo, diff solo previsto (se instala en 7b)"
INSTALAR_CRON=0
if [ "$SIM" = 1 ]; then
  if [ ! -f "$CRON_SIM" ]; then
    echo "SIMULACION: siembra del crontab pre-D.2 (la linea vieja real + linea accounting ajena)"
    printf '%s\n' "$VIEJA" > "$CRON_SIM"
    echo "0 3 * * * /opt/accounting/ledger.sh  # accounting (ajena, debe quedar byte-igual)" >> "$CRON_SIM"
  fi
  cp "$CRON_SIM" "$RESPALDO_CRON"
  instalar_cron "$CRON_SIM" "$CRON_SIM.nuevo" "$VIEJA" "$LINEA1" "$LINEA2"
  NUEVO="$CRON_SIM.nuevo"
  RESPALDO="$RESPALDO_CRON"
else
  mkdir -p "$TMPD"
  ssh goncloud "crontab -l -u gon 2>/dev/null" > "$RESPALDO_LOCAL" || { echo "ABORTA: no se pudo leer el crontab de gon"; exit 1; }
  instalar_cron "$RESPALDO_LOCAL" "$NUEVO_LOCAL" "$VIEJA" "$LINEA1" "$LINEA2"
  NUEVO="$NUEVO_LOCAL"
  RESPALDO="$RESPALDO_LOCAL"
fi
if diff -q "$RESPALDO" "$NUEVO" >/dev/null 2>&1; then
  # C-W1 (idempotencia real): diff vacio CON las 2 nuevas presentes = el
  # cron ya quedo instalado (reintento): se avisa y se sigue con
  # INSTALAR_CRON=0. `solo_previsto` sigue estricto en lo demas.
  if grep -qFx -- "$LINEA1" "$NUEVO" && grep -qFx -- "$LINEA2" "$NUEVO"; then
    echo "cron ya instalado (las 2 nuevas presentes, diff vacio): se sigue sin instalar en 7b"
  else
    solo_previsto "$RESPALDO" "$NUEVO"
  fi
else
  solo_previsto "$RESPALDO" "$NUEVO"
  echo "diff solo con lo previsto (1 baja + 2 altas):"
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
  pg_dump "$DSN_SIM" --schema-only > "$TMPD/pred2_schema_$STAMP.sql"
  grep -q 'CREATE TABLE public.precio_decision ' "$TMPD/pred2_schema_$STAMP.sql" || { echo "ABORTA: dump invalido"; exit 1; }
  echo "SIMULACION: backup local $TMPD/pred2_schema_$STAMP.sql"
else
  ssh goncloud "set -e; D=$SRV/backups; TMP=\"\$D/.pred2_schema_$STAMP.sql.tmp\"; \
    docker exec orbit-db-1 pg_dump -U orbit -d orbit --schema-only > \"\$TMP\"; \
    [ -s \"\$TMP\" ] && grep -q 'CREATE TABLE public.precio_decision ' \"\$TMP\" \
      && tail -5 \"\$TMP\" | grep -q 'PostgreSQL database dump complete' \
      || { echo 'DUMP INVALIDO'; rm -f \"\$TMP\"; exit 1; }; \
    chmod 600 \"\$TMP\"; mv \"\$TMP\" \"\$D/pred2_schema_$STAMP.sql\"; \
    ls -l \"\$D/pred2_schema_$STAMP.sql\""
fi

echo "== 4) Migraciones 0059 y 0058, cada una en su propio psql -1"
if [ -z "$PENDIENTES" ]; then echo "ninguna pendiente"; else
  for m in $PENDIENTES; do
    git show "$APROBADO:migrations/$m" | w
    echo "$m" >> "$APLICADAS"
    echo "aplicada $m (anotada en $APLICADAS)"
  done
fi

echo "== 5) md5 del codigo desplegado vs SHA aprobado"
md5_de() { python3 -c "import hashlib, sys; print(hashlib.md5(sys.stdin.buffer.read()).hexdigest())"; }
ARCHIVOS_D2="app/precio/reglas.py app/precio/compuerta.py app/precio/liberaciones.py app/templates/_precios_seguridad.html app/precio/siembra.py app/api_precios_write.py app/templates/_precios_goals.html app/static/js/precios.js app/config_write.py app/notifica.py tools/precio_reversa.py"
if [ "$SIM" = 1 ]; then
  echo "SIMULACION: sin servidor; se verifica el respaldo local contra el SHA"
  for f in $ARCHIVOS_D2; do
    a=$(git show "$APROBADO:$f" | md5_de)
    b=$(md5_de < "$TMPD/predeploy-$STAMP/$f")
    [ "$a" = "$b" ] || { echo "ABORTA: md5 distinto en $f"; exit 1; }
    echo "md5 OK $f"
  done
else
  git archive --format=tar "$APROBADO" app Dockerfile .dockerignore pyproject.toml uv.lock tools/precio_reversa.py tools/precio_cobertura.py \
    | ssh goncloud "cd $SRV && tar -xf -"
  for f in $ARCHIVOS_D2; do
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
else
  ssh goncloud "set -e; cd $SRV; \
    echo DIGEST antes=\$(docker inspect -f '{{.Image}}' orbit-app-1); \
    docker compose up -d --no-deps --build app; \
    echo DIGEST despues=\$(docker inspect -f '{{.Image}}' orbit-app-1); \
    sleep 5; curl -sSf http://127.0.0.1:8010/health; echo; \
    docker ps --filter name=orbit-app-1 --format '{{.Names}} {{.Status}}'"
fi

echo "== 7v) Corre precio --reporte una vez con el codigo nuevo (solo lectura; si no sale 0, el cron queda sin instalar)"
AYER=$(date -u -d '1 day ago' +%F 2>/dev/null || date -u -v-1d +%F)
HOY=$(date -u +%F)
if [ "$SIM" = 1 ]; then
  PY="$REPO/.venv/bin/python"
  [ -x "$PY" ] || PY=python3
  ORBIT_DSN_READ="$DSN_SIM" "$PY" -m app.cli precio --reporte --platform amazon_mx --desde "$AYER" --hasta "$HOY" || { echo "ABORTA: precio --reporte no salio 0 en sim; el cron queda sin instalar"; exit 1; }
else
  ssh goncloud "docker exec orbit-app-1 python -m app.cli precio --reporte --platform amazon_mx --desde $AYER --hasta $HOY" || { echo "ABORTA: precio --reporte no salio 0; el cron queda sin instalar"; exit 1; }
fi
echo "reporte OK: salio 0"

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
  instalar_cron "$ACTUAL_7B" "$NUEVO.7b" "$VIEJA" "$LINEA1" "$LINEA2"
  solo_previsto "$ACTUAL_7B" "$NUEVO.7b"
  cp "$ACTUAL_7B" "$RESPALDO_7B"
  if [ "$SIM" != 1 ]; then
    ssh goncloud "mkdir -p $SRV/backups && cat > $SRV/backups/crontab-gon-pre-d2-$STAMP.txt" < "$ACTUAL_7B"
  fi
  cron_instalar < "$NUEVO.7b"
  if [ "$SIM" = 1 ] && [ "${ORBIT_D2_SIM_FALLA_CRON:-0}" = 1 ]; then
    echo "SIMULACION: falla sembrada (ORBIT_D2_SIM_FALLA_CRON=1): un escritor ajeno mete una linea tras instalar" >&2
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
      echo "crontab instalado y verificado (respaldo en $SRV/backups/crontab-gon-pre-d2-$STAMP.txt)"
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
