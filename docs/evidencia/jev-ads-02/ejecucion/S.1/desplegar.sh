#!/usr/bin/env bash
# Despliegue S.1 de Jev Ads 02: migracion 0051 (acta de listado de la ingesta
# de estructura) y el codigo del SHA aprobado. La ingesta diaria empieza a
# escribir el acta; nada la lee todavia (el consumidor llega en S.4).
# Patron de docs/evidencia/jev-ads-01/ejecucion/2.3/desplegar.sh con las reglas
# de plans/jev-ads-02-bloque-s.md: guarda de app.cli corriendo, reintento antes
# de recrear, posterior desde el arranque del contenedor. La migracion va antes
# que el codigo: sin las tablas, la ingesta diaria falla.
# Uso: cd ~/dev/goncloud-Orbit && bash docs/evidencia/jev-ads-02/ejecucion/S.1/desplegar.sh <sha-de-origin/master>
set -euo pipefail
trap 'echo "FALLO en la linea $LINENO. Si ya se aplico la migracion: bash $DIR/rollback.sh $STAMP (o --solo-esquema si el codigo no se copio)"' ERR

APROBADO=${1:?uso: desplegar.sh <sha de origin/master con CI verde>}
REPO=$(git rev-parse --show-toplevel)
SRV=/mnt/data/appdata/orbit
STAMP=$(date -u +%Y%m%d-%H%M)
DIR=docs/evidencia/jev-ads-02/ejecucion/S.1
cd "$REPO"

guardas() {  # 0 si se puede recrear el contenedor: sin ciclo corriendo y sin app.cli dentro
  local ciclos cli
  ciclos=$(ssh goncloud 'docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1 -tA -c "SELECT count(*) FROM optimizer_cycle WHERE status = '"'"'running'"'"'"')
  cli=$(ssh goncloud 'docker top orbit-app-1 2>/dev/null | grep -c "app.cli" || true')
  echo "ciclos corriendo=$ciclos, procesos app.cli=$cli"
  [ "$ciclos" = "0" ] && [ "$cli" = "0" ]
}

echo "== 0) SHA aprobado = origin/master con la bateria completa verde"
git fetch -q origin
[ "$(git rev-parse origin/master)" = "$APROBADO" ] || { echo "ABORTA: origin/master ya no es $APROBADO"; exit 1; }
CI=$(gh run list --commit "$APROBADO" --event push --workflow Quality --json conclusion --jq '.[0].conclusion')
[ "$CI" = "success" ] || { echo "ABORTA: el run push de Quality sobre $APROBADO es '$CI', no success"; exit 1; }
echo "APROBADO=$APROBADO CI=$CI"

echo "== 1) Preflight (esperado ok|0|f: sin ciclo corriendo, sin tabla del acta; mas 0 procesos app.cli)"
R=$(ssh goncloud 'docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1 -tA' <<'SQL'
SELECT 'ok',
       (SELECT count(*) FROM optimizer_cycle WHERE status = 'running'),
       to_regclass('public.ads_listado_plataforma') IS NOT NULL;
SQL
)
echo "$R"
[ "$R" = "ok|0|f" ] || { echo "ABORTA: preflight no es ok|0|f"; exit 1; }
guardas || { echo "ABORTA: hay ciclo o app.cli corriendo (no se interrumpe la ingesta de estructura de las 06:45 UTC)"; exit 1; }

echo "== 2) Respaldo del codigo actual y del SHA (antes de tocar la base: toda reversa lo encuentra)"
ssh goncloud "set -e; cd $SRV; mkdir -p predeploy-$STAMP; \
  cp -a app Dockerfile .dockerignore pyproject.toml uv.lock tools predeploy-$STAMP/; \
  echo $APROBADO > predeploy-$STAMP/SHA; ls predeploy-$STAMP"

echo "== 3) Backup del esquema (staging + verificacion)"
ssh goncloud "set -e; D=$SRV/backups; TMP=\"\$D/.pre0051_schema_$STAMP.sql.tmp\"; \
  docker exec orbit-db-1 pg_dump -U orbit -d orbit --schema-only > \"\$TMP\"; \
  [ -s \"\$TMP\" ] && grep -q 'CREATE TABLE public.decision ' \"\$TMP\" \
    && tail -5 \"\$TMP\" | grep -q 'PostgreSQL database dump complete' \
    || { echo 'DUMP INVALIDO'; rm -f \"\$TMP\"; exit 1; }; \
  chmod 600 \"\$TMP\"; mv \"\$TMP\" \"\$D/pre0051_schema_$STAMP.sql\"; \
  ls -l \"\$D/pre0051_schema_$STAMP.sql\""

echo "== 4) Migracion 0051 (acta de listado por plataforma y ad group)"
git show "$APROBADO:migrations/0051_ads_acta_listado.sql" \
  | ssh goncloud 'docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1 -1'

echo "== 5) Permisos y esquema del acta (esperado: permisos OK)"
R=$(git show "$APROBADO:$DIR/permisos.sql" \
  | ssh goncloud 'docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1 -tA')
echo "$R"
[ "$R" = "permisos OK" ] || { echo "ABORTA antes del codigo: $R. Reversa: bash $DIR/rollback.sh $STAMP --solo-esquema"; exit 1; }

echo "== 6) Copiar el codigo del SHA aprobado (git archive, LF)"
git archive --format=tar "$APROBADO" app Dockerfile .dockerignore pyproject.toml uv.lock \
    tools/fabrica_campanas.py tools/jev_ads.py tools/jev_fichas.py \
  | ssh goncloud "cd $SRV && tar -xf -"

echo "== 7) md5 server vs SHA aprobado"
md5_de() { if command -v md5 >/dev/null; then md5 -q; else md5sum | cut -d' ' -f1; fi; }
for f in app/ads/structure.py app/ads/structure_api.py app/ads/structure_plan.py; do
  local_md5=$(git show "$APROBADO:$f" | md5_de)
  srv_md5=$(ssh goncloud "md5sum $SRV/$f" | cut -d' ' -f1)
  [ "$local_md5" = "$srv_md5" ] || { echo "ABORTA: md5 distinto en $f"; exit 1; }
  echo "md5 OK $f"
done

echo "== 8) Guardas antes de recrear (reintento cada 30s hasta 30 min)"
INTENTO=0
until guardas; do
  INTENTO=$((INTENTO + 1))
  if [ "$INTENTO" -ge 60 ]; then
    echo "DETENIDO: las guardas no pasaron en 30 minutos. El contenedor viejo sigue corriendo su imagen anterior; la migracion 0051 aplicada y el codigo copiado no lo afectan. NO se corrio la reversa. Reintenta el despliegue mas tarde."
    exit 1
  fi
  echo "guardas no pasan (intento $INTENTO/60); reintento en 30s"
  sleep 30
done

echo "== 9) Build + recrear app (digest antes/despues) + health"
ssh goncloud "set -e; cd $SRV; \
  echo DIGEST antes=\$(docker inspect -f '{{.Image}}' orbit-app-1); \
  docker compose up -d --no-deps --build app; \
  echo DIGEST despues=\$(docker inspect -f '{{.Image}}' orbit-app-1); \
  sleep 5; curl -sS http://127.0.0.1:8010/health; echo; \
  docker ps --filter name=orbit-app-1 --format '{{.Names}} {{.Status}}'"

echo "== LISTO. STAMP=$STAMP"
echo "Siguiente: bash $DIR/checklist.sh $STAMP"
echo "Reversa:   bash $DIR/rollback.sh $STAMP"
