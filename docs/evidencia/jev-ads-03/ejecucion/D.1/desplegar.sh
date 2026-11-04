#!/usr/bin/env bash
# Despliegue D.1 de Jev Ads 03: migracion 0074 (bloqueo_veredicto y
# bloqueo_efecto) y el codigo del SHA aprobado, con jev.bloqueo APAGADO
# (D.2 lo enciende en otro encargo). Nada escribe a Amazon.
# Patron de docs/evidencia/jev-ads-02/ejecucion/S.3/desplegar.sh: preflight,
# respaldo (codigo + compose + SHA; el .env NO se copia), backup de esquema,
# psql -1 (DEPLOY.md "Aplicar migraciones"), permisos, git archive, md5,
# guardas con reintento, digest y health. Sin logins nuevos.
# Uso: cd <repo> && bash docs/evidencia/jev-ads-03/ejecucion/D.1/desplegar.sh <sha-de-origin/master>
set -euo pipefail
trap 'echo "FALLO en la linea $LINENO. Si ya se aplico la migracion: bash $DIR/rollback.sh $STAMP (o --solo-esquema si el codigo no se copio)"' ERR

APROBADO=${1:?uso: desplegar.sh <sha de origin/master con CI verde>}
H=${ORBIT_SSH:-gonserver}
REPO=$(git rev-parse --show-toplevel)
SRV=/mnt/data/appdata/orbit
STAMP=$(date -u +%Y%m%d-%H%M)
DIR=docs/evidencia/jev-ads-03/ejecucion/D.1
cd "$REPO"

guardas_proceso() {  # 0 = nadie corre app.cli dentro del contenedor
  local top cli
  top=$(ssh "$H" "docker top orbit-app-1") || return 1
  cli=$(printf '%s\n' "$top" | grep -c 'app\.cli' || true)
  [ "$cli" = "0" ]
}

echo "== 0) SHA aprobado = origin/master con la bateria verde"
git fetch -q origin
[ "$(git rev-parse origin/master)" = "$APROBADO" ] || { echo "ABORTA: origin/master ya no es $APROBADO"; exit 1; }
CI=$(gh run list --commit "$APROBADO" --event push --workflow Quality --json conclusion --jq '.[0].conclusion')
[ "$CI" = "success" ] || { echo "ABORTA: el run push de Quality sobre $APROBADO es '$CI', no success"; exit 1; }
echo "APROBADO=$APROBADO CI=$CI"

echo "== 1) Preflight (esperado cli=0 y ok|f|f: nadie corre, 0074 sin aplicar, interruptor apagado)"
TOP=$(ssh "$H" "docker top orbit-app-1") || { echo "ABORTA: docker top no contesta"; exit 1; }
CLI=$(printf '%s\n' "$TOP" | grep -c 'app\.cli' || true)
R=$(ssh "$H" 'docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1 -tA' <<'SQL'
SELECT 'ok',
       to_regclass('public.bloqueo_veredicto') IS NOT NULL,
       coalesce((SELECT settings -> 'jev.bloqueo' = 'true'::jsonb
                   FROM config_version ORDER BY id DESC LIMIT 1), false);
SQL
)
echo "cli=$CLI $R"
[ "$CLI" = "0" ] && [ "$R" = "ok|f|f" ] || { echo "ABORTA: preflight no es cli=0 ok|f|f"; exit 1; }

echo "== 2) Respaldo del codigo actual, compose y SHA (el .env NO se copia a ningun lado)"
ssh "$H" "set -e; cd $SRV; mkdir -p predeploy-$STAMP; \
  cp -a app Dockerfile .dockerignore pyproject.toml uv.lock tools docker-compose.yml predeploy-$STAMP/; \
  echo $APROBADO > predeploy-$STAMP/SHA; ls predeploy-$STAMP"

echo "== 3) Backup del esquema (staging + verificacion)"
ssh "$H" "set -e; D=$SRV/backups; TMP=\"\$D/.pre0074_schema_$STAMP.sql.tmp\"; \
  docker exec orbit-db-1 pg_dump -U orbit -d orbit --schema-only > \"\$TMP\"; \
  [ -s \"\$TMP\" ] && grep -q 'CREATE TABLE public.decision ' \"\$TMP\" \
    && tail -5 \"\$TMP\" | grep -q 'PostgreSQL database dump complete' \
    || { echo 'DUMP INVALIDO'; rm -f \"\$TMP\"; exit 1; }; \
  chmod 600 \"\$TMP\"; mv \"\$TMP\" \"\$D/pre0074_schema_$STAMP.sql\"; \
  ls -l \"\$D/pre0074_schema_$STAMP.sql\""

echo "== 4) Migracion 0074 (DEPLOY.md, Aplicar migraciones: psql -1 ON_ERROR_STOP)"
git show "$APROBADO:migrations/0074_jev_ads03_bloqueo.sql" \
  | ssh "$H" 'docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1 -1'

echo "== 5) Permisos (esperado: permisos OK)"
R=$(git show "$APROBADO:$DIR/permisos.sql" 2>/dev/null || cat "$DIR/permisos.sql")
R=$(printf '%s\n' "$R" | ssh "$H" 'docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1 -tA')
echo "$R"
[ "$R" = "permisos OK" ] || { echo "ABORTA antes del codigo: $R. Reversa: bash $DIR/rollback.sh $STAMP --solo-esquema"; exit 1; }

echo "== 6) Copiar el codigo y docker-compose.yml del SHA aprobado (git archive, LF)"
git archive --format=tar "$APROBADO" app Dockerfile .dockerignore pyproject.toml uv.lock docker-compose.yml \
  | ssh "$H" "cd $SRV && tar -xf -"

echo "== 7) md5 server vs SHA aprobado"
for f in $(cat "$DIR/archivos.txt"); do
  local_md5=$(git show "$APROBADO:$f" | md5 -q 2>/dev/null || git show "$APROBADO:$f" | md5sum | cut -d' ' -f1)
  srv_md5=$(ssh "$H" "md5sum $SRV/$f" | cut -d' ' -f1)
  [ "$local_md5" = "$srv_md5" ] || { echo "ABORTA: md5 distinto en $f"; exit 1; }
  echo "md5 OK $f"
done

echo "== 8) Guardas antes de recrear (reintento 30s hasta 30min)"
LISTO=0
for i in $(seq 1 60); do
  if guardas_proceso; then LISTO=1; break; fi
  echo "intento $i/60: app.cli en curso o sin respuesta; reintento en 30s"
  sleep 30
done
if [ "$LISTO" != "1" ]; then
  echo "DETENIDO sin recrear: el contenedor viejo sigue con su imagen. Termina con: ssh $H 'cd $SRV && docker compose up -d --no-deps --build app'"
  exit 1
fi
echo "guardas OK"

echo "== 9) Build + recrear app (digest antes/despues) + health"
ssh "$H" "set -e; cd $SRV; \
  echo DIGEST antes=\$(docker inspect -f '{{.Image}}' orbit-app-1); \
  docker compose up -d --no-deps --build app; \
  echo DIGEST despues=\$(docker inspect -f '{{.Image}}' orbit-app-1); \
  sleep 5; curl -sS http://127.0.0.1:8010/health; echo; \
  docker ps --filter name=orbit-app-1 --format '{{.Names}} {{.Status}}'"

echo "== LISTO. STAMP=$STAMP"
echo "Siguiente: bash $DIR/checklist.sh $STAMP"
echo "Reversa:   bash $DIR/rollback.sh $STAMP"
