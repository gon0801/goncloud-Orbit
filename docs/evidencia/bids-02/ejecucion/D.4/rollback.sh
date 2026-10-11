#!/usr/bin/env bash
# Reversa del despliegue D.4 (BIDS 02 seccion 5): quita la linea de
# impulso-vigia del crontab ACTUAL de gon, restaura el codigo de
# predeploy-<sello> y aplica las reversas de aplicadas-<sello>.txt en
# orden inverso. Patron: docs/evidencia/bids-02/ejecucion/D.2/rollback.sh.
# D.4 si instalo una linea de cron: el respaldo del crontab lo guardo
# desplegar.sh en $SRV/backups/crontab-gon-pre-d4-<sello>.txt pero la
# reversa NO lo restaura completo (E7: otro loop pudo cambiar el crontab
# desde entonces; el actual manda y el respaldo queda como evidencia).
# Con filas en impulso o anuncio_retiro la reversa ABORTA antes del DROP
# (regla 7: la de la 0065 y la de la 0066 abortan si hay filas). Despues
# del primer lanzamiento, un problema se corrige hacia adelante.
# En simulacion (ORBIT_SIMULACION=1) la base es ORBIT_SIM_DSN, el respaldo,
# aplicadas-<sello>.txt y el crontab viven en ${TMPDIR:-/tmp}/orbit-d4-sim
# y el codigo no se restaura a ningun servidor (no hay).
# Uso: bash docs/evidencia/bids-02/ejecucion/D.4/rollback.sh <STAMP impreso por desplegar.sh>
set -euo pipefail

STAMP=${1:?uso: rollback.sh <STAMP impreso por desplegar.sh>}
REPO=$(git rev-parse --show-toplevel)
SRV=/mnt/data/appdata/orbit
DIR=docs/evidencia/bids-02/ejecucion/D.4
SIM=${ORBIT_SIMULACION:-0}
DSN_SIM=${ORBIT_SIM_DSN:-postgresql://orbit:orbit@127.0.0.1:5433/orbit_sim_d4}
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
  echo "== MODO SIMULACION: datos reales contra $DSN_SIM, servidor intacto"
  PRE="$TMPD/predeploy-$STAMP"
  APLICADAS="$TMPD/aplicadas-$STAMP.txt"
  RESPALDO_CRON="$TMPD/crontab-gon-pre-d4-$STAMP.txt"
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
  REVS="$r $REVS"
done < "$APLICADAS"
echo "reversas en orden: ${REVS:-ninguna}"

echo "== 0b) Guardas (ciclos en running abortan)"
if [ "$SIM" = 1 ]; then
  RUNNING=$(echo "SELECT count(*) FROM optimizer_cycle WHERE status = 'running';" | q)
  [ "$RUNNING" = "0" ] || { echo "ABORTA: hay $RUNNING ciclo(s) en running"; exit 1; }
  echo "guardas OK (sin ciclos en running)"
else
  RUNNING=$(ssh goncloud "docker inspect -f '{{.State.Running}}' orbit-app-1") || { echo "ABORTA: docker inspect no contesta"; exit 1; }
  if [ "$RUNNING" = "true" ]; then
    TOP=$(ssh goncloud "docker top orbit-app-1") || { echo "ABORTA: docker top no contesta"; exit 1; }
    CLI=$(printf '%s\n' "$TOP" | grep -c 'app\.cli' || true)
    [ "$CLI" = "0" ] || { echo "ABORTA: hay app.cli corriendo"; exit 1; }
    echo "guardas OK (contenedor arriba, sin app.cli)"
  else
    echo "contenedor parado: la reversa corre sin guardas de proceso (no hay nada que interrumpir)"
  fi
  RC=$(echo "SELECT count(*) FROM optimizer_cycle WHERE status = 'running';" | q)
  [ "$RC" = "0" ] || { echo "ABORTA: hay $RC ciclo(s) en running"; exit 1; }
fi

echo "== 0c) Lanzamientos y retiros: con filas, abortar antes del DROP (regla 7)"
for TABLA in impulso anuncio_retiro; do
  EXISTE=$(echo "SELECT to_regclass('public.$TABLA') IS NOT NULL;" | q)
  if [ "$EXISTE" = "t" ]; then
    N=$(echo "SELECT count(*) FROM $TABLA;" | q)
    [ "$N" = "0" ] || { echo "ABORTA: hay $N fila(s) en $TABLA; la reversa aborta con filas (regla 7). Despues del primer lanzamiento, corrige hacia adelante."; exit 1; }
    echo "$TABLA vacia: la reversa puede borrarla"
  else
    echo "$TABLA no existe: nada que cuidar"
  fi
done

echo "== 0d) Lotes de impulso huerfanos (sin fila en impulso): abortar"
EXISTE_FL=$(echo "SELECT to_regclass('public.fabrica_lote') IS NOT NULL;" | q)
EXISTE_IM=$(echo "SELECT to_regclass('public.impulso') IS NOT NULL;" | q)
if [ "$EXISTE_FL" = "t" ] && [ "$EXISTE_IM" = "t" ]; then
  NH=$(echo "SELECT count(*) FROM fabrica_lote f WHERE f.lote LIKE 'impulso-%' AND f.estado IN ('applied','failed','planeado') AND NOT EXISTS (SELECT 1 FROM impulso i WHERE i.lote = f.lote);" | q)
  [ "$NH" = "0" ] || { echo "ABORTA: hay $NH lote(s) de impulso huerfano(s) (applied/failed/planeado sin fila en impulso, quiza gastando sin vigia); reconcilia a mano antes de revertir."; exit 1; }
  echo "sin huerfanos: la reversa puede seguir"
else
  echo "sin fabrica_lote o sin impulso: nada que cuidar"
fi

quitar_cron() {  # quitar_cron <actual> <nuevo> <vigia>: quita solo esa linea
  # Comparacion por texto exacto (sin espacios extremos): un comentario
  # ajeno que nombre impulso-vigia NO se toca. Preserva el terminador
  # final del archivo.
  python3 - "$1" "$2" "$3" <<'PY'
import sys
actual, nuevo, vigia = sys.argv[1:4]
with open(actual, "rb") as f:
    crudo = f.read().decode("utf-8")
termina_nl = crudo.endswith("\n")
lineas = crudo.splitlines()
blanco = vigia.strip()
quedan = [ln for ln in lineas if ln.strip() != blanco]
print(f"quitadas: {len(lineas) - len(quedan)}")
with open(nuevo, "w") as f:
    f.write("\n".join(quedan) + ("\n" if termina_nl and quedan else ""))
PY
}

echo "== 1) Cron: quitar SOLO la linea de D.4 del crontab actual + restaurar codigo de predeploy-$STAMP"
# E7: la reversa puede correr un dia despues; el crontab actual manda y
# el respaldo queda como evidencia. Sin linea de D.4 no hay nada que
# quitar (desplegar no llego a 7b): no aborta.
VIGIA_R=$(git show "$SHA:docs/DEPLOY.md" | grep -E '^[0-9].*impulso-vigia')
[ "$(printf '%s\n' "$VIGIA_R" | grep -c .)" = 1 ] || { echo "ABORTA: DEPLOY.md del SHA no trae exactamente la linea de impulso-vigia"; exit 1; }
if [ "$SIM" = 1 ]; then
  if [ ! -f "$CRON_SIM" ]; then
    echo "SIMULACION: sin crontab simulado: nada que quitar"
  else
    AGENDA_ANTES=$(grep -c -E 'cycle --platform' "$CRON_SIM" || true)
    quitar_cron "$CRON_SIM" "$CRON_SIM.nuevo" "$VIGIA_R"
    if grep -qF -- "$VIGIA_R" "$CRON_SIM.nuevo"; then
      echo "ABORTA: tras quitar, el crontab todavia trae la linea de D.4"; exit 1
    fi
    AGENDA_DESPUES=$(grep -c -E 'cycle --platform' "$CRON_SIM.nuevo" || true)
    [ "$AGENDA_ANTES" = "$AGENDA_DESPUES" ] || { echo "ABORTA: la quita toco las lineas del ciclo"; exit 1; }
    mv "$CRON_SIM.nuevo" "$CRON_SIM"
    echo "SIMULACION: crontab sin linea de D.4 OK; respaldo $PRE presente; sin servidor, nada que reconstruir"
  fi
else
  mkdir -p "$TMPD"
  ssh goncloud "crontab -l -u gon 2>/dev/null" > "$TMPD/cron-actual-$STAMP.txt" || { echo "ABORTA: no se pudo leer el crontab de gon"; exit 1; }
  quitar_cron "$TMPD/cron-actual-$STAMP.txt" "$TMPD/cron-nuevo-$STAMP.txt" "$VIGIA_R"
  cat "$TMPD/cron-nuevo-$STAMP.txt" | ssh goncloud "crontab -u gon -"
  ssh goncloud "crontab -l -u gon" | grep -qF -- "$VIGIA_R" && { echo "ABORTA: la linea de D.4 sigue instalada"; exit 1; }
  echo "cron sin linea de D.4 OK"
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

echo "== 3) Verificacion (esperado: f en lo que aplico este despliegue)"
TABLA65=$(echo "SELECT to_regclass('public.impulso') IS NOT NULL;" | q)
TABLA66=$(echo "SELECT to_regclass('public.anuncio_retiro') IS NOT NULL;" | q)
echo "impulso=$TABLA65 anuncio_retiro=$TABLA66"
# Solo se exige f en lo que este despliegue aplico (aplicadas-STAMP.txt);
# una tabla que ya venia aplicada la deja otro despliegue.
case " $REVS " in
  *"0065_reversa_"*) [ "$TABLA65" = "f" ] || { echo "FALLA: impulso sigue"; exit 1; } ;;
esac
case " $REVS " in
  *"0066_reversa_"*) [ "$TABLA66" = "f" ] || { echo "FALLA: anuncio_retiro sigue"; exit 1; } ;;
esac
if [ "$TABLA65" = "t" ] || [ "$TABLA66" = "t" ]; then
  echo "== REVERSA LISTA (parcial: alguna tabla sigue porque este sello no la aplico; la deja otro despliegue)."
else
  echo "== REVERSA LISTA. impulso y anuncio_retiro se fueron vacias (con filas, el paso 0c aborta antes del DROP); el codigo anterior no lee las tablas."
fi
