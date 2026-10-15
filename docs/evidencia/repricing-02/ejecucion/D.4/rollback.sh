#!/usr/bin/env bash
# Reversa del despliegue D.4 (REPRICING 02 seccion 5): quita las CUATRO
# lineas de D.4 del crontab ACTUAL de gon (D.4 solo agrego: no hay
# viejas que restaurar), restaura el codigo de predeploy-<sello> y
# aplica las reversas de aplicadas-<sello>.txt (la 0073). Patron:
# ../D.3/rollback.sh. La reversa NO restaura el respaldo completo (E7:
# otro loop pudo cambiar el crontab desde entonces; el actual manda y
# el respaldo queda como evidencia). La reversa de la 0073 restaura la
# funcion 0028 verbatim (las filas meli con costo de miembro no-ancla
# que ya existan quedan: el trigger solo dispara en escritura nueva).
# La politica meli que metio politicas.sh NO se toca (datos, no
# esquema; el codigo viejo la ignora).
# En simulacion (ORBIT_SIMULACION=1) la base es ORBIT_SIM_DSN, el
# respaldo, aplicadas-<sello>.txt y el crontab viven en
# ${TMPDIR:-/tmp}/orbit-d4-sim y el codigo no se restaura a ningun
# servidor (no hay).
# Uso: bash docs/evidencia/repricing-02/ejecucion/D.4/rollback.sh <STAMP impreso por desplegar.sh>
set -euo pipefail

STAMP=${1:?uso: rollback.sh <STAMP impreso por desplegar.sh>}
REPO=$(git rev-parse --show-toplevel)
SRV=/mnt/data/appdata/orbit
DIR=docs/evidencia/repricing-02/ejecucion/D.4
SIM=${ORBIT_SIMULACION:-0}
DSN_SIM=${ORBIT_SIM_DSN:-postgresql://orbit:orbit@localhost:5432/orbit_sim_D4}
# C-W6: el DSN solo se muestra enmascarado (`orbit:***@`); psql sigue
# usando $DSN_SIM real (la conexion no cambia).
DSN_VER=$(printf '%s' "$DSN_SIM" | sed 's|://[^@]*@|://orbit:***@|')
TMPD=${TMPDIR:-/tmp}/orbit-d4-sim
cd "$REPO"

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

if [ "$SIM" = 1 ]; then
  echo "== MODO SIMULACION: datos sinteticos contra $DSN_VER, servidor intacto"
  PRE="$TMPD/predeploy-$STAMP"
  APLICADAS="$TMPD/aplicadas-$STAMP.txt"
  CRON_SIM="$TMPD/crontab-gon"
  [ -d "$PRE" ] || { echo "ABORTA: no existe $PRE (corre desplegar.sh en simulacion primero)"; exit 1; }
  SHA=$(cat "$PRE/SHA") || { echo "ABORTA: $PRE sin SHA"; exit 1; }
else
  PRE="$SRV/predeploy-$STAMP"
  APLICADAS="$DIR/aplicadas-$STAMP.txt"
  SHA=$(ssh goncloud "cat $PRE/SHA") || { echo "ABORTA: no existe $PRE/SHA"; exit 1; }
  git fetch -q origin
fi
[ -f "$APLICADAS" ] || { echo "ABORTA: no existe $APLICADAS"; exit 1; }
echo "SHA=$SHA"

echo "== 0) Reversas de aplicadas-$STAMP.txt existen en el SHA, ANTES de tocar nada"
REVS=""
while IFS= read -r m; do
  [ -n "$m" ] || continue
  n=$(basename "$m" .sql | cut -c1-4)
  resto=$(basename "$m" .sql | cut -c6-)
  r="${n}_reversa_${resto}.sql"
  git cat-file -e "$SHA:migrations/$r" || { echo "ABORTA: la reversa $r no existe en $SHA"; exit 1; }
  REVS="${r}${REVS:+ $REVS}"
done < "$APLICADAS"
echo "reversas en orden: ${REVS:-ninguna}"

echo "== 0b) Guardas (ciclos en running o proceso en curso abortan)"
if [ "$SIM" = 1 ]; then
  RUNNING=$(echo "SELECT count(*) FROM optimizer_cycle WHERE status = 'running';" | q)
  [ "$RUNNING" = "0" ] || { echo "ABORTA: hay $RUNNING ciclo(s) en running"; exit 1; }
  echo "guardas OK (sin ciclos en running)"
else
  RUNNING=$(ssh goncloud "docker inspect -f '{{.State.Running}}' orbit-app-1") || { echo "ABORTA: docker inspect no contesta"; exit 1; }
  if [ "$RUNNING" = "true" ]; then
    TOP=$(ssh goncloud "docker top orbit-app-1") || { echo "ABORTA: docker top no contesta"; exit 1; }
    CLI=$(printf '%s\n' "$TOP" | grep -c 'app\.cli' || true)
    MUESTRAS=$(printf '%s\n' "$TOP" | grep -c 'app\.envio_muestras' || true)
    CATALOGO=$(printf '%s\n' "$TOP" | grep -c 'app\.meli\.catalogo' || true)
    { [ "$CLI" = "0" ] && [ "$MUESTRAS" = "0" ] && [ "$CATALOGO" = "0" ]; } || { echo "ABORTA: hay app.cli, app.envio_muestras o app.meli.catalogo corriendo"; exit 1; }
    echo "guardas OK (contenedor arriba, sin app.cli, app.envio_muestras ni app.meli.catalogo)"
  else
    echo "contenedor parado: la reversa corre sin guardas de proceso (no hay nada que interrumpir)"
  fi
  RC=$(echo "SELECT count(*) FROM optimizer_cycle WHERE status = 'running';" | q)
  [ "$RC" = "0" ] || { echo "ABORTA: hay $RC ciclo(s) en running"; exit 1; }
fi

quitar_cron() {  # quitar_cron <actual> <nuevo> <l1..l4>: quita las 4 de D.4
  # Comparacion por texto exacto (sin espacios extremos): un comentario
  # ajeno que nombre precio-meli.lock o app.meli.catalogo NO se toca.
  # Sin lineas de D.4 no hay nada que quitar (desplegar no llego a 7b):
  # no aborta. Preserva el terminador final del archivo.
  python3 - "$1" "$2" "$3" "$4" "$5" "$6" <<'PY'
import sys
actual, nuevo, l1, l2, l3, l4 = sys.argv[1:7]
with open(actual, "rb") as f:
    crudo = f.read().decode("utf-8")
termina_nl = crudo.endswith("\n")
lineas = crudo.splitlines()
blancos = {l1.strip(), l2.strip(), l3.strip(), l4.strip()}
quedan = [ln for ln in lineas if ln.strip() not in blancos]
print(f"quitadas: {len(lineas) - len(quedan)}")
with open(nuevo, "w") as f:
    f.write("\n".join(quedan) + ("\n" if termina_nl and quedan else ""))
PY
}

echo "== 1) Cron: quitar las 4 lineas de D.4 del crontab actual + codigo de predeploy-$STAMP"
# E7: la reversa puede correr un dia despues; el crontab actual manda y
# el respaldo queda como evidencia. D.4 solo agrego: no hay viejas que
# restaurar.
L1_R=$(git show "$SHA:docs/DEPLOY.md" | grep -E '^30 11 .*app\.meli\.catalogo')
L2_R=$(git show "$SHA:docs/DEPLOY.md" | grep -E '^25 12 .*envio_muestras.*--platform meli')
L3_R=$(git show "$SHA:docs/DEPLOY.md" | grep -E '^10 13 .*precio-meli\.lock.*--platform meli')
L4_R=$(git show "$SHA:docs/DEPLOY.md" | grep -E '^10 15-23/2 .*precio-meli\.lock.*--platform meli')
for i in 1 2 3 4; do
  eval "ln=\$L${i}_R"
  [ "$(printf '%s\n' "$ln" | grep -c .)" = 1 ] || { echo "ABORTA: DEPLOY.md del SHA no trae exactamente la linea $i de D.4"; exit 1; }
done
if [ "$SIM" = 1 ]; then
  if [ ! -f "$CRON_SIM" ]; then
    echo "SIMULACION: sin crontab simulado: nada que quitar"
  else
    quitar_cron "$CRON_SIM" "$CRON_SIM.nuevo" "$L1_R" "$L2_R" "$L3_R" "$L4_R"
    for ln in "$L1_R" "$L2_R" "$L3_R" "$L4_R"; do
      grep -qF -- "$ln" "$CRON_SIM.nuevo" && { echo "ABORTA: tras revertir, el crontab todavia trae una linea de D.4"; exit 1; }
    done
    mv "$CRON_SIM.nuevo" "$CRON_SIM"
    echo "SIMULACION: crontab sin lineas de D.4 OK"
  fi
else
  mkdir -p "$TMPD"
  ssh goncloud "crontab -l -u gon 2>/dev/null" > "$TMPD/cron-actual-$STAMP.txt" || { echo "ABORTA: no se pudo leer el crontab de gon"; exit 1; }
  quitar_cron "$TMPD/cron-actual-$STAMP.txt" "$TMPD/cron-nuevo-$STAMP.txt" "$L1_R" "$L2_R" "$L3_R" "$L4_R"
  cat "$TMPD/cron-nuevo-$STAMP.txt" | ssh goncloud "crontab -u gon -"
  LEIDO=$(ssh goncloud "crontab -l -u gon")
  for ln in "$L1_R" "$L2_R" "$L3_R" "$L4_R"; do
    printf '%s\n' "$LEIDO" | grep -qF -- "$ln" && { echo "ABORTA: una linea de D.4 sigue instalada: $ln"; exit 1; }
  done
  echo "cron sin lineas de D.4 OK"
fi
if [ "$SIM" = 1 ]; then
  echo "SIMULACION: respaldo $PRE presente; sin servidor, nada que reconstruir"
else
  ssh goncloud "set -e; cd $SRV; [ -d predeploy-$STAMP/app ] || { echo 'ABORTA: no existe predeploy-$STAMP'; exit 1; }; \
    [ -f predeploy-$STAMP/docker-compose.yml ] || { echo 'ABORTA: predeploy-$STAMP sin docker-compose.yml'; exit 1; }; \
    rm -rf app tools; cp -a predeploy-$STAMP/app predeploy-$STAMP/tools .; \
    cp -a predeploy-$STAMP/Dockerfile predeploy-$STAMP/.dockerignore predeploy-$STAMP/pyproject.toml predeploy-$STAMP/uv.lock predeploy-$STAMP/docker-compose.yml .; \
    echo DIGEST antes=\$(docker inspect -f '{{.Image}}' orbit-app-1); \
    docker compose up -d --no-deps --build app; \
    echo DIGEST despues=\$(docker inspect -f '{{.Image}}' orbit-app-1); \
    sleep 5; curl -sSf http://127.0.0.1:8010/health; echo"
fi

echo "== 2) Reversas en orden inverso"
if [ -z "$REVS" ]; then echo "ninguna migracion aplicada por este despliegue: nada que reversar"; else
  for r in $REVS; do
    git show "$SHA:migrations/$r" | w
    echo "reversa aplicada $r"
  done
fi

echo "== 3) Verificacion (esperado: sin rama meli si este sello aplico la 0073)"
SENAL=$(echo "SELECT count(*) FROM pg_proc WHERE proname = 'estimacion_escenario_referencias_coherentes' AND pg_get_functiondef(oid) LIKE '%disponible meli exige miembro%';" | q)
echo "senal_0073=$SENAL"
# Solo se exige el estado previo en lo que este despliegue aplico
# (aplicadas-STAMP.txt); lo que ya venia aplicado lo deja otro despliegue.
case " $REVS " in
  *"0073_reversa_"*) [ "$SENAL" = "0" ] || { echo "FALLA: la rama meli de la 0073 sigue"; exit 1; } ;;
esac
echo "== REVERSA LISTA. Lineas de D.4 fuera, codigo de predeploy-$STAMP restaurado y reversas aplicadas; las filas de negocio quedan intactas."
