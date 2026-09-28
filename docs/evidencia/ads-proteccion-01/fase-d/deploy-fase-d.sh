#!/usr/bin/env bash
# Deploy Fase D (D.2 + D.1b + C.2a): primero 0045 y 0046, despues el codigo.
# Se corre desde el repo local: cd ~/dev/goncloud-Orbit && bash <este archivo>
# Patron de docs/DEPLOY.md y del deploy de D.1 (backup de esquema, psql -1,
# git archive del SHA aprobado, md5, digest antes/despues, health).
set -euo pipefail

APROBADO=2aa70cc6fa583fe673fde512ae823141ca8efe18   # origin/master, CI Quality success
REPO=/Users/dn/dev/goncloud-Orbit
SRV=/mnt/data/appdata/orbit
STAMP=$(date -u +%Y%m%d-%H%M)
cd "$REPO"

echo "== 0) SHA aprobado"
git fetch -q origin
[ "$(git rev-parse origin/master)" = "$APROBADO" ] || { echo "ABORTA: origin/master ya no es $APROBADO"; exit 1; }
echo "APROBADO=$APROBADO"

echo "== 1) Preflight en prod (ciclo corriendo, migraciones ya aplicadas)"
R=$(ssh goncloud 'docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1 -tA' <<'SQL'
SELECT 'ok',
       (SELECT count(*) FROM optimizer_cycle WHERE status = 'running'),
       -- 0045 aplicada = el CHECK ya acepta choque_clave. El NOMBRE no sirve:
       -- Postgres ya bautizo el CHECK anonimo de 0044 igual (<tabla>_<col>_check).
       EXISTS (SELECT 1 FROM pg_constraint
                WHERE conrelid = 'decision_sin_aplicar'::regclass AND contype = 'c'
                  AND pg_get_constraintdef(oid) LIKE '%choque_clave%'),
       to_regclass('public.target_acos_ciclo') IS NOT NULL;
SQL
)
echo "$R"
[ "$R" = "ok|0|f|f" ] || { echo "ABORTA: preflight no es ok|0|f|f (ciclo corriendo o migracion ya aplicada)"; exit 1; }

echo "== 2) Backup del esquema (staging + verificacion)"
ssh goncloud "set -e; D=$SRV/backups; TMP=\"\$D/.pre0045_0046_schema_$STAMP.sql.tmp\"; \
  docker exec orbit-db-1 pg_dump -U orbit -d orbit --schema-only > \"\$TMP\"; \
  [ -s \"\$TMP\" ] && grep -q 'CREATE TABLE public.decision_sin_aplicar' \"\$TMP\" \
    && tail -5 \"\$TMP\" | grep -q 'PostgreSQL database dump complete' \
    || { echo 'DUMP INVALIDO'; rm -f \"\$TMP\"; exit 1; }; \
  chmod 600 \"\$TMP\"; mv \"\$TMP\" \"\$D/pre0045_0046_schema_$STAMP.sql\"; \
  ls -l \"\$D/pre0045_0046_schema_$STAMP.sql\""

echo "== 3) Migracion 0045 (CHECK nombrado + choque_clave)"
git show "$APROBADO:migrations/0045_sin_aplicar_choque_clave.sql" \
  | ssh goncloud 'docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1 -1'

echo "== 4) Migracion 0046 (target_acos_ciclo)"
git show "$APROBADO:migrations/0046_target_acos_ciclo.sql" \
  | ssh goncloud 'docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1 -1'

echo "== 5) Verificacion de esquema (esperado: t|t|t|f|f|t)"
R=$(ssh goncloud 'docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1 -tA' <<'SQL'
SELECT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'decision_sin_aplicar_motivo_check'
               AND pg_get_constraintdef(oid) LIKE '%choque_clave%'),
       to_regclass('public.target_acos_ciclo') IS NOT NULL,
       has_table_privilege('app_decide', 'target_acos_ciclo', 'INSERT'),
       has_table_privilege('app_decide', 'target_acos_ciclo', 'UPDATE'),
       has_table_privilege('app_decide', 'target_acos_ciclo', 'DELETE'),
       has_table_privilege('app_read', 'target_acos_ciclo', 'SELECT');
SQL
)
echo "$R"
[ "$R" = "t|t|t|f|f|t" ] || { echo "ABORTA antes del codigo: esquema no quedo como se esperaba"; exit 1; }

echo "== 6) Respaldo del codigo actual"
ssh goncloud "set -e; cd $SRV; mkdir -p predeploy-$STAMP; \
  cp -a app Dockerfile pyproject.toml uv.lock tools predeploy-$STAMP/; ls predeploy-$STAMP"

echo "== 7) Copiar el codigo del SHA aprobado (git archive, LF)"
git archive --format=tar "$APROBADO" app Dockerfile .dockerignore pyproject.toml uv.lock tools/fabrica_campanas.py \
  | ssh goncloud "cd $SRV && tar -xf -"

echo "== 8) md5 server vs SHA aprobado"
for f in app/cycle.py app/apply.py app/apply_cola.py app/optimizer/goals.py; do
  local_md5=$(git show "$APROBADO:$f" | md5 -q)
  srv_md5=$(ssh goncloud "md5sum $SRV/$f" | cut -d' ' -f1)
  [ "$local_md5" = "$srv_md5" ] || { echo "ABORTA: md5 distinto en $f"; exit 1; }
  echo "md5 OK $f"
done

echo "== 9) Build + recrear app (digest antes/despues) + health"
ssh goncloud "set -e; cd $SRV; \
  echo DIGEST antes=\$(docker inspect -f '{{.Image}}' orbit-app-1); \
  docker compose up -d --no-deps --build app; \
  echo DIGEST despues=\$(docker inspect -f '{{.Image}}' orbit-app-1); \
  sleep 5; curl -sS http://127.0.0.1:8010/health; echo; \
  docker ps --filter name=orbit-app-1 --format '{{.Names}} {{.Status}}'"

echo "== LISTO. Reversa: codigo $SRV/predeploy-$STAMP/ + rebuild; esquema $SRV/backups/pre0045_0046_schema_$STAMP.sql"
