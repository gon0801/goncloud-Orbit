#!/usr/bin/env bash
# Despliegue del cierre de JEV ADS 02 (#440): solo codigo. Sin migracion
# (jev_aviso y jev_aviso_entrega existen desde 0052), sin linea de cron y
# sin encender nada: `jev.avisos` debe seguir ausente, y el script aborta
# si lo encuentra en true. Patron:
# docs/evidencia/bids-02/ejecucion/D.5/desplegar.sh, sin su modo simulacion.
# Uso: bash docs/evidencia/jev-ads-02/ejecucion/cierre/desplegar.sh <sha de origin/master con CI verde>
set -euo pipefail

APROBADO=${1:?uso: desplegar.sh <sha de origin/master con CI verde>}
REPO=$(git rev-parse --show-toplevel)
SRV=/mnt/data/appdata/orbit
STAMP=$(date -u +%Y%m%d-%H%M)
DIR=docs/evidencia/jev-ads-02/ejecucion/cierre
# La reversa es la de D.5 de BIDS 02: restaura predeploy-<sello> tal cual,
# sin nada propio de aquella seccion.
REVERSA=docs/evidencia/bids-02/ejecucion/D.5/rollback.sh
cd "$REPO"
trap 'echo "FALLO en la linea $LINENO. Si ya se copio codigo o se recreo: bash $REVERSA $STAMP"' ERR

q() {
  ssh goncloud 'DSN=$(docker exec orbit-app-1 printenv ORBIT_DSN_READ); docker exec -i orbit-db-1 psql "$DSN" -X -q -tA -v ON_ERROR_STOP=1'
}
guardas_proceso() {
  local top cli
  top=$(ssh goncloud "docker top orbit-app-1") || return 1
  cli=$(printf '%s\n' "$top" | grep -c 'app\.cli' || true)
  [ "$cli" = "0" ]
}

echo "== 0) SHA aprobado con la bateria completa verde"
git fetch -q origin
[ "$(git rev-parse origin/master)" = "$APROBADO" ] || { echo "ABORTA: origin/master ya no es $APROBADO"; exit 1; }
CI=$(gh run list --commit "$APROBADO" --event push --workflow Quality --json conclusion --jq '.[0].conclusion')
[ "$CI" = "success" ] || { echo "ABORTA: el run push de Quality sobre $APROBADO es '$CI', no success"; exit 1; }
echo "APROBADO=$APROBADO CI=$CI"

echo "== 1) Preflight: nadie corre, sin harvest en vuelo"
guardas_proceso || { echo "ABORTA: hay app.cli corriendo o docker top no contesta"; exit 1; }
echo "cli=0"
RUNNING=$(echo "SELECT count(*) FROM optimizer_cycle WHERE status = 'running';" | q)
[ "$RUNNING" = "0" ] || { echo "ABORTA: hay $RUNNING ciclo(s) en running"; exit 1; }
echo "ciclos running=0"
H1=$(echo "SELECT count(*) FROM apply_queue WHERE kind = 'harvest' AND estado NOT IN ('applied','failed','vetoed','discarded');" | q)
H2=$(echo "SELECT count(*) FROM harvest_job WHERE fase IN ('pending','negative_created','exact_created','hermanas_negadas');" | q)
[ "$H1" = "0" ] && [ "$H2" = "0" ] || { echo "ABORTA: harvest en vuelo ($H1 en cola, $H2 jobs)"; exit 1; }
echo "harvest en vuelo=0"
ABIERTAS=$(echo "SELECT count(*) FROM jev_corrida i WHERE i.evento = 'inicio' AND NOT EXISTS (SELECT 1 FROM jev_corrida f WHERE f.lote_id = i.lote_id AND f.evento = 'fin');" | q)
[ "$ABIERTAS" = "0" ] || { echo "ABORTA: hay $ABIERTAS corrida(s) de jev-senales sin cerrar"; exit 1; }
echo "corridas jev abiertas=0"

echo "== 1b) Guardas propias del cierre (abortan si fallan)"
git cat-file -e "$APROBADO:app/jev_avisos.py" || { echo "ABORTA: $APROBADO no trae app/jev_avisos.py (S.8)"; exit 1; }
git show "$APROBADO:app/api_dashboard.py" | grep -q 'fila.get("listado_de")' || { echo "ABORTA: el SHA no trae el arreglo de J6-31"; exit 1; }
AVISOS=$(echo "SELECT COALESCE(settings->>'jev.avisos', 'ausente') FROM config_version ORDER BY id DESC LIMIT 1;" | q)
[ "$AVISOS" != "true" ] || { echo "ABORTA: jev.avisos ya esta en true; este despliegue entra con el aviso apagado"; exit 1; }
echo "jev_avisos.py y arreglo J6-31 en el SHA; jev.avisos=$AVISOS"

echo "== 2) Respaldo del codigo actual (el .env NO se copia a ningun lado)"
# Un predeploy-<sello> que ya existe se conserva: copiar encima guardaria
# el codigo NUEVO de la corrida anterior y la reversa lo restauraria.
if ssh goncloud "test -f $SRV/predeploy-$STAMP/SHA"; then
  echo "reintento: predeploy-$STAMP ya existe, se conserva (no se pisa)"
else
  ssh goncloud "set -e; cd $SRV; mkdir -p predeploy-$STAMP; \
    cp -a app Dockerfile .dockerignore pyproject.toml uv.lock tools docker-compose.yml predeploy-$STAMP/; \
    echo $APROBADO > predeploy-$STAMP/SHA; ls predeploy-$STAMP"
fi

echo "== 3) Copiar el codigo del SHA y comparar md5"
md5_de() { if command -v md5 >/dev/null; then md5 -q; else md5sum | cut -d' ' -f1; fi; }
ARCHIVOS="app/jev_avisos.py app/jev_senales.py app/jev_juicios.py app/jev_vista.py app/jev_lectura.py app/jev_salud.py app/api_dashboard.py app/notifica.py app/templates/salud.html"
git archive --format=tar "$APROBADO" app Dockerfile .dockerignore pyproject.toml uv.lock tools/fabrica_campanas.py \
    tools/jev_ads.py tools/jev_fichas.py \
  | ssh goncloud "cd $SRV && tar -xf -"
for f in $ARCHIVOS; do
  a=$(git show "$APROBADO:$f" | md5_de)
  b=$(ssh goncloud "md5sum $SRV/$f" | cut -d' ' -f1)
  [ "$a" = "$b" ] || { echo "ABORTA: md5 distinto en $f"; exit 1; }
  echo "md5 OK $f"
done

echo "== 4) Guardas antes de recrear"
LISTO=0
for i in $(seq 1 60); do
  if guardas_proceso; then LISTO=1; break; fi
  echo "intento $i/60: app.cli en curso o sin respuesta; reintento en 30s"
  sleep 30
done
if [ "$LISTO" != "1" ]; then
  echo "DETENIDO: las guardas no pasaron en 30 minutos. Me detengo SIN recrear el contenedor y SIN correr la reversa."
  echo "El contenedor viejo sigue corriendo su imagen anterior: el codigo copiado no lo toca."
  echo "Para terminar: espera a que docker top orbit-app-1 no liste app.cli y corre: ssh goncloud 'cd $SRV && docker compose up -d --no-deps --build app'"
  exit 1
fi
echo "guardas OK"

echo "== 5) Build + recrear app (digest antes/despues) + health"
ssh goncloud "set -e; cd $SRV; \
  echo DIGEST antes=\$(docker inspect -f '{{.Image}}' orbit-app-1); \
  docker compose up -d --no-deps --build app; \
  echo DIGEST despues=\$(docker inspect -f '{{.Image}}' orbit-app-1); \
  for i in 1 2 3 4 5 6 7 8 9 10; do curl -sSf http://127.0.0.1:8010/health && break; sleep 3; done; echo; \
  docker ps --filter name=orbit-app-1 --format '{{.Names}} {{.Status}}'"

echo "== LISTO. STAMP=$STAMP"
echo "Siguiente: bash $DIR/checklist.sh $APROBADO"
echo "Reversa:   bash $REVERSA $STAMP"
