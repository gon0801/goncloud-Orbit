#!/usr/bin/env bash
# Despliegue R.1 de Jev Ads 03: SOLO CODIGO. Sin migracion y sin cron
# (el cron lo instala programar-reconcilia.sh). Partida:
# docs/evidencia/jev-ads-02/ejecucion/S.5/desplegar.sh, mas las guardas
# de ciclo y harvest de jev-ads-01/ejecucion/2.3.
# git archive de lo que copia el Dockerfile (y Dockerfile/compose, que
# el build del servidor necesita). md5 contra el SHA, que en real tiene
# que ser origin/master. Digest y health al recrear.
# Simulacion: ORBIT_SIMULACION=1. No hay ssh, ni docker, ni fetch.
# Uso real: cd <repo> && bash docs/evidencia/jev-ads-03/ejecucion/R.1/desplegar.sh <sha de origin/master>
# Uso sim:  ORBIT_SIMULACION=1 ORBIT_SIM_DIR=<dir> bash docs/evidencia/jev-ads-03/ejecucion/R.1/desplegar.sh <sha>
set -euo pipefail
trap 'echo "FALLO en la linea $LINENO. Reversa: bash $DIR/rollback.sh $STAMP"' ERR

APROBADO=${1:?uso: desplegar.sh <sha de origin/master con CI verde>}
H=${ORBIT_SSH:-goncloud}
REPO=$(git rev-parse --show-toplevel)
SRV=/mnt/data/appdata/orbit
STAMP=$(date -u +%Y%m%d-%H%M)
DIR=docs/evidencia/jev-ads-03/ejecucion/R.1
SIM=${ORBIT_SIMULACION:-0}
SIM_DIR=${ORBIT_SIM_DIR:-$DIR/.sim}
cd "$REPO"

# Lo que el Dockerfile copia, mas Dockerfile y compose (el build del
# servidor los necesita; el Dockerfile no se copia a si mismo).
ARCHIVO=(
  app
  Dockerfile
  .dockerignore
  pyproject.toml
  uv.lock
  docker-compose.yml
  tools/fabrica_campanas.py
  tools/jev_ads.py
  tools/jev_fichas.py
  tools/precio_sonda.py
  tools/precio_cobertura.py
)

md5_de() {
  if command -v md5 >/dev/null 2>&1; then md5 -q
  else md5sum | cut -d' ' -f1
  fi
}

guardas_ciclo_harvest() {
  cat <<'SQL'
SELECT 'ok',
       (SELECT count(*) FROM optimizer_cycle WHERE status = 'running'),
       (SELECT count(*) FROM apply_queue
         WHERE kind = 'harvest' AND estado NOT IN ('applied', 'failed', 'vetoed', 'discarded')),
       (SELECT count(*) FROM harvest_job
         WHERE fase IN ('pending', 'negative_created', 'exact_created', 'hermanas_negadas'));
SQL
}

cli_corriendo() {
  if [ "$SIM" = 1 ]; then
    echo 0
    return
  fi
  local top
  top=$(ssh "$H" "docker top orbit-app-1") || return 1
  printf '%s\n' "$top" | grep -c 'app\.cli' || true
}

echo "== 0) SHA aprobado = origin/master con la bateria verde"
if [ "$SIM" = 1 ]; then
  git cat-file -e "$APROBADO^{commit}" || { echo "ABORTA: $APROBADO no es un commit local"; exit 1; }
  echo "SIMULACION: $APROBADO existe local; sin fetch ni gh"
else
  git fetch -q origin
  [ "$(git rev-parse origin/master)" = "$APROBADO" ] || { echo "ABORTA: origin/master ya no es $APROBADO"; exit 1; }
  CI=$(gh run list --commit "$APROBADO" --event push --workflow Quality --json conclusion --jq '.[0].conclusion')
  [ "$CI" = "success" ] || { echo "ABORTA: el run push de Quality sobre $APROBADO es '$CI', no success"; exit 1; }
  echo "APROBADO=$APROBADO CI=$CI"
fi

echo "== 1) Preflight (cli=0 y ok|0|0|0: nadie corre, sin ciclo, sin harvest en vuelo)"
echo "-- guardas de ciclo y harvest:"
guardas_ciclo_harvest
if [ "$SIM" = 1 ]; then
  CLI=0
  R="ok|0|0|0"
  echo "SIMULACION cli=$CLI $R"
else
  CLI=$(cli_corriendo) || { echo "ABORTA: docker top no contesta"; exit 1; }
  R=$(ssh "$H" 'docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1 -tA' < <(guardas_ciclo_harvest))
  echo "cli=$CLI $R"
fi
[ "$CLI" = "0" ] && [ "$R" = "ok|0|0|0" ] || { echo "ABORTA: preflight no es cli=0 ok|0|0|0"; exit 1; }

echo "== 2) Respaldo del codigo actual, compose y SHA (el .env NO se copia)"
if [ "$SIM" = 1 ]; then
  mkdir -p "$SIM_DIR/predeploy-$STAMP"
  echo "$APROBADO" > "$SIM_DIR/predeploy-$STAMP/SHA"
  echo "SIMULACION: respaldo local predeploy-$STAMP (sin ssh, sin .env)"
else
  ssh "$H" "set -e; cd $SRV; \
    if [ -e predeploy-$STAMP ]; then echo ABORTA: predeploy-$STAMP ya existe; exit 1; fi; \
    mkdir -p predeploy-$STAMP; \
    cp -a app Dockerfile .dockerignore pyproject.toml uv.lock tools docker-compose.yml predeploy-$STAMP/; \
    echo $APROBADO > predeploy-$STAMP/SHA; ls predeploy-$STAMP"
fi

echo "== 3) Copiar el codigo del SHA (git archive de lo que copia el Dockerfile)"
if [ "$SIM" = 1 ]; then
  rm -rf "$SIM_DIR/srv"
  mkdir -p "$SIM_DIR/srv"
  git archive --format=tar "$APROBADO" "${ARCHIVO[@]}" | tar -xf - -C "$SIM_DIR/srv"
  echo "SIMULACION: archive extraido en local, sin ssh"
else
  git archive --format=tar "$APROBADO" "${ARCHIVO[@]}" | ssh "$H" "cd $SRV && tar -xf -"
fi

echo "== 4) md5 contra el SHA (en real, origin/master)"
lista=$(git ls-tree -r --name-only "$APROBADO" "${ARCHIVO[@]}")
distintos=0
while IFS= read -r f; do
  [ -n "$f" ] || continue
  local_md5=$(git show "$APROBADO:$f" | md5_de)
  if [ "$SIM" = 1 ]; then
    srv_md5=$(md5_de < "$SIM_DIR/srv/$f")
  else
    srv_md5=$(ssh -n "$H" "md5sum $SRV/$f" | cut -d' ' -f1)
  fi
  if [ "$local_md5" != "$srv_md5" ]; then
    echo "ABORTA: md5 distinto en $f"
    distintos=1
  fi
done <<< "$lista"
[ "$distintos" = 0 ] || exit 1
echo "md5 OK $(printf '%s\n' "$lista" | grep -c .) archivos"

echo "== 5) Guardas antes de recrear (reintento 30s hasta 30min)"
if [ "$SIM" = 1 ]; then
  echo "SIMULACION: guardas cli=0 ciclo=0 harvest=0, sin espera"
else
  LISTO=0
  for i in $(seq 1 60); do
    CLI=$(cli_corriendo) || CLI=1
    R=$(ssh "$H" 'docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1 -tA' < <(guardas_ciclo_harvest)) || R=""
    if [ "$CLI" = "0" ] && [ "$R" = "ok|0|0|0" ]; then LISTO=1; break; fi
    echo "intento $i/60: guardas no pasan (cli=$CLI $R); reintento en 30s"
    sleep 30
  done
  if [ "$LISTO" != "1" ]; then
    echo "DETENIDO: las guardas no pasaron en 30 minutos. Me detengo SIN recrear el contenedor."
    exit 1
  fi
  echo "guardas OK"
fi

echo "== 6) Build + recrear app (digest antes/despues) + health"
if [ "$SIM" = 1 ]; then
  echo "SIMULACION DIGEST antes=no-tocado"
  echo "SIMULACION: no se recrea el contenedor"
  echo "SIMULACION DIGEST despues=no-tocado"
  echo "SIMULACION health=ok"
else
  ssh "$H" "set -e; cd $SRV; \
    echo DIGEST antes=\$(docker inspect -f '{{.Image}}' orbit-app-1); \
    docker compose up -d --no-deps --build app; \
    echo DIGEST despues=\$(docker inspect -f '{{.Image}}' orbit-app-1); \
    sleep 5; curl -fsS http://127.0.0.1:8010/health; echo; \
    docker ps --filter name=orbit-app-1 --format '{{.Names}} {{.Status}}'"
fi

echo "== LISTO. STAMP=$STAMP"
echo "R.1 no instala cron. Siguiente: bash $DIR/programar-reconcilia.sh"
echo "Checklist: bash $DIR/checklist.sh $STAMP"
echo "Reversa:   bash $DIR/rollback.sh $STAMP"
