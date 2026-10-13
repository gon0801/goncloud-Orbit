#!/usr/bin/env bash
# Reversa del despliegue D.6 (BIDS 02 remate R14): restaura el codigo
# de predeploy-<sello> y aplica las reversas de aplicadas-<sello>.txt
# en orden inverso (0072 y despues 0071). Patron:
# docs/evidencia/bids-02/ejecucion/D.4/rollback.sh. D.6 no instalo
# linea de cron: el crontab no se toca; se imprime su huella (md5 y
# lineas) antes y despues y se aborta si cambio (R13-E). Las reversas
# de la 0071 y la 0072 no borran tablas ni filas (REVOKE y DROP del
# trigger/funcion): son seguras con impulsos creados, y la reversa de
# OJO con la reversa de la 0071: la ACL no distingue si el GRANT vino
# del default o de la migracion; un REVOKE real quita tambien el
# privilegio que ya existia por default privileges (solo se otorga al
# crear el objeto). La verificacion de abajo lo detecta (la vista
# dejaria de ser legible) y la correccion es reaplicar la 0071. El DSN de lectura sale del .env del
# servidor, sin depender de que orbit-app-1 este arriba (R13-L): se
# resuelve dentro del servidor y nunca viaja en claro.
# En simulacion (ORBIT_SIMULACION=1) la base es ORBIT_SIM_DSN, el
# respaldo y aplicadas-<sello>.txt viven en ${TMPDIR:-/tmp}/orbit-d6-sim
# y el codigo no se restaura a ningun servidor (no hay). Las salidas de
# la corrida real se guardan con su linea exit= al final (R13-M).
# Uso: bash docs/evidencia/bids-02/ejecucion/D.6/rollback.sh <STAMP impreso por desplegar.sh>
set -euo pipefail

STAMP=${1:?uso: rollback.sh <STAMP impreso por desplegar.sh>}
REPO=$(git rev-parse --show-toplevel)
SRV=/mnt/data/appdata/orbit
DIR=docs/evidencia/bids-02/ejecucion/D.6
SIM=${ORBIT_SIMULACION:-0}
DSN_SIM=${ORBIT_SIM_DSN:-postgresql://orbit:orbit@127.0.0.1:5433/orbit_sim_d6}
TMPD=${TMPDIR:-/tmp}/orbit-d6-sim
cd "$REPO"

q() {
  if [ "$SIM" = 1 ]; then
    psql "$DSN_SIM" -X -q -tA -v ON_ERROR_STOP=1
  else
    ssh goncloud 'DSN=$(grep -E "^ORBIT_DSN_READ=" /mnt/data/appdata/orbit/.env | head -1 | cut -d= -f2-); docker exec -i orbit-db-1 psql "$DSN" -X -q -tA -v ON_ERROR_STOP=1'
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

echo "== 0c) Filas en impulso (conteo informativo: las reversas no borran filas)"
N_IMP=$(echo "SELECT count(*) FROM impulso;" | q)
echo "impulso trae $N_IMP fila(s): la 0072 y la 0071 se reversan igual (no hay DROP de tablas)"

huella_cron() {
  # R13-E: D.6 no instala ni quita linea; solo se mide la huella.
  if [ "$SIM" = 1 ]; then
    python3 - "$CRON_SIM" <<'PY2'
import hashlib, sys
try:
    datos = open(sys.argv[1], "rb").read()
except OSError:
    datos = b""
lineas = datos.decode("utf-8").splitlines()
print(f"{hashlib.md5(datos).hexdigest()} {len(lineas)}")
PY2
  else
    ssh goncloud "crontab -l -u gon 2>/dev/null" | python3 -c "
import hashlib, sys
datos = sys.stdin.buffer.read()
lineas = datos.decode('utf-8').splitlines()
print(f'{hashlib.md5(datos).hexdigest()} {len(lineas)}')
"
  fi
}

echo "== 1) Restaurar codigo de predeploy-$STAMP (el crontab no se toca)"
HUELLA_ANTES=$(huella_cron) || { echo "ABORTA: no se pudo leer el crontab de gon"; exit 1; }
echo "crontab antes: md5 y lineas = $HUELLA_ANTES (sin tocarlo)"
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

echo "== 3) Verificacion (esperado: f en lo que reverso este despliegue)"
TRIG72=$(echo "SELECT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'impulso_grupo_tipo' AND NOT tgisinternal);" | q)
VISTA_LEIBLE=$(echo "SELECT has_table_privilege('app_read', 'public.v_impulso_estado', 'SELECT');" | q)
echo "trigger_0072=$TRIG72 vista_legible_por_app_read=$VISTA_LEIBLE"
case " $REVS " in
  *"0072_reversa_"*) [ "$TRIG72" = "f" ] || { echo "FALLA: el candado de la 0072 sigue"; exit 1; } ;;
esac
# La reversa de la 0071 quita el SELECT de la ACL (tambien el que
# venia de default privileges): no se exige f; se exige que la vista
# siga legible, y si no, FALLA en voz alta y se reaplica la 0071.
[ "$VISTA_LEIBLE" = "t" ] || { echo "FALLA: v_impulso_estado no es legible por app_read tras la reversa"; exit 1; }
HUELLA_DESPUES=$(huella_cron) || { echo "ABORTA: no se pudo releer el crontab de gon"; exit 1; }
echo "crontab despues: md5 y lineas = $HUELLA_DESPUES"
[ "$HUELLA_DESPUES" = "$HUELLA_ANTES" ] || { echo "ABORTA: el crontab cambio durante la reversa y D.6 no toca lineas"; exit 1; }
echo "== REVERSA LISTA. Codigo de predeploy-$STAMP restaurado y reversas aplicadas; sin tocar el crontab."
