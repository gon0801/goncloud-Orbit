#!/usr/bin/env bash
# Despliegue S.3 de Jev Ads 02: migracion B (0052: tablas de senales, vista
# y cierre del perimetro), login orbit_jev + ORBIT_DSN_JEV, y el codigo del
# SHA aprobado con el compose nuevo (quinto DSN en environment de app).
# El job jev-senales queda APAGADO (S.4 lo construye; sin interruptor).
# Patron de 2.3/desplegar.sh + reglas de plans/jev-ads-02-bloque-s.md:
# preflight, respaldo (codigo + compose + SHA; el .env NO se copia a
# ningun lado), backup de esquema, psql -1, login (bloque de DEPLOY.md
# con salida a /dev/null, solo codigo), permisos, git archive, md5,
# guardas repetidas con reintento, digest y health.
# Uso: cd ~/dev/goncloud-Orbit && bash docs/evidencia/jev-ads-02/ejecucion/S.3/desplegar.sh <sha-de-origin/master>
set -euo pipefail
trap 'echo "FALLO en la linea $LINENO. Si ya se aplico la migracion: bash $DIR/rollback.sh $STAMP (o --solo-esquema si el codigo no se copio)"' ERR

APROBADO=${1:?uso: desplegar.sh <sha de origin/master con CI verde>}
REPO=$(git rev-parse --show-toplevel)
SRV=/mnt/data/appdata/orbit
STAMP=$(date -u +%Y%m%d-%H%M)
DIR=docs/evidencia/jev-ads-02/ejecucion/S.3
cd "$REPO"

guardas_proceso() {  # 0 = nadie corre app.cli dentro del contenedor
  local top cli
  top=$(ssh goncloud "docker top orbit-app-1") || return 1
  cli=$(printf '%s\n' "$top" | grep -c 'app\.cli' || true)
  [ "$cli" = "0" ]
}

echo "== 0) SHA aprobado = origin/master con la bateria completa verde"
git fetch -q origin
[ "$(git rev-parse origin/master)" = "$APROBADO" ] || { echo "ABORTA: origin/master ya no es $APROBADO"; exit 1; }
CI=$(gh run list --commit "$APROBADO" --event push --workflow Quality --json conclusion --jq '.[0].conclusion')
[ "$CI" = "success" ] || { echo "ABORTA: el run push de Quality sobre $APROBADO es '$CI', no success"; exit 1; }
echo "APROBADO=$APROBADO CI=$CI"

echo "== 1) Preflight (esperado cli=0 y ok|f|t: nadie corre, B sin aplicar, decide aun lee Jev)"
TOP=$(ssh goncloud "docker top orbit-app-1") || { echo "ABORTA: docker top no contesta"; exit 1; }
CLI=$(printf '%s\n' "$TOP" | grep -c 'app\.cli' || true)
R=$(ssh goncloud 'docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1 -tA' <<'SQL'
SELECT 'ok',
       to_regclass('public.jev_senal') IS NOT NULL,
       has_table_privilege('app_decide', 'public.jev_revision', 'SELECT');
SQL
)
echo "cli=$CLI $R"
[ "$CLI" = "0" ] && [ "$R" = "ok|f|t" ] || { echo "ABORTA: preflight no es cli=0 ok|f|t"; exit 1; }

echo "== 2) Respaldo del codigo actual, compose y SHA (el .env NO se copia a ningun lado)"
ssh goncloud "set -e; cd $SRV; mkdir -p predeploy-$STAMP; \
  cp -a app Dockerfile .dockerignore pyproject.toml uv.lock tools docker-compose.yml predeploy-$STAMP/; \
  echo $APROBADO > predeploy-$STAMP/SHA; ls predeploy-$STAMP"

echo "== 3) Backup del esquema (staging + verificacion)"
ssh goncloud "set -e; D=$SRV/backups; TMP=\"\$D/.pre0052_schema_$STAMP.sql.tmp\"; \
  docker exec orbit-db-1 pg_dump -U orbit -d orbit --schema-only > \"\$TMP\"; \
  [ -s \"\$TMP\" ] && grep -q 'CREATE TABLE public.decision ' \"\$TMP\" \
    && tail -5 \"\$TMP\" | grep -q 'PostgreSQL database dump complete' \
    || { echo 'DUMP INVALIDO'; rm -f \"\$TMP\"; exit 1; }; \
  chmod 600 \"\$TMP\"; mv \"\$TMP\" \"\$D/pre0052_schema_$STAMP.sql\"; \
  ls -l \"\$D/pre0052_schema_$STAMP.sql\""

echo "== 4) Migracion 0052 (tablas de senales, vista, cierre del perimetro)"
git show "$APROBADO:migrations/0052_jev_senales.sql" \
  | ssh goncloud 'docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1 -1'

echo "== 5) Login orbit_jev + ORBIT_DSN_JEV (bloque de DEPLOY.md; nadie lo corre a mano)"
ssh goncloud 'bash -s' <<'SCRIPT' >/dev/null
set -euo pipefail
ENVF=/mnt/data/appdata/orbit/.env
gen() { head -c 48 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 32; }
sed -i '/^ORBIT_DSN_JEV=/d' "$ENVF"
P=$(gen)
docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1 -q <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'orbit_jev') THEN
    EXECUTE format('CREATE ROLE orbit_jev LOGIN PASSWORD %L', '$P');
  ELSE
    EXECUTE format('ALTER ROLE orbit_jev LOGIN PASSWORD %L', '$P');
  END IF;
END \$\$;
GRANT app_jev TO orbit_jev;
SQL
echo "ORBIT_DSN_JEV=postgresql://orbit_jev:${P}@127.0.0.1:5432/orbit" >> "$ENVF"
chmod 600 "$ENVF"
SCRIPT
echo "login orbit_jev aplicado (salida del bloque a /dev/null: sin contrasenas ni DSN)"

echo "== 5b) El login conecta y es miembro de app_jev (esperado conecta=1 miembro=t, sin valor)"
CONECTA=$(ssh goncloud 'DSN=$(grep ^ORBIT_DSN_JEV= /mnt/data/appdata/orbit/.env | cut -d= -f2-); docker exec -i orbit-db-1 psql "$DSN" -X -q -tA -v ON_ERROR_STOP=1 -c "SELECT 1"' || true)
MIEMBRO=$(ssh goncloud 'docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1 -tA' <<'SQL' || true
SELECT pg_has_role('orbit_jev', 'app_jev', 'MEMBER');
SQL
)
echo "conecta=$CONECTA miembro=$MIEMBRO"
[ "$CONECTA" = "1" ] && [ "$MIEMBRO" = "t" ] || { echo "ABORTA: orbit_jev no conecta o no es miembro de app_jev"; exit 1; }

echo "== 6) Permisos y esquema Jev (esperado: permisos OK)"
R=$(git show "$APROBADO:$DIR/permisos.sql" \
  | ssh goncloud 'docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1 -tA')
echo "$R"
[ "$R" = "permisos OK" ] || { echo "ABORTA antes del codigo: $R. Reversa: bash $DIR/rollback.sh $STAMP --solo-esquema"; exit 1; }

echo "== 7) Copiar el codigo y docker-compose.yml del SHA aprobado (git archive, LF)"
git archive --format=tar "$APROBADO" app Dockerfile .dockerignore pyproject.toml uv.lock docker-compose.yml \
    tools/fabrica_campanas.py tools/jev_ads.py tools/jev_fichas.py \
  | ssh goncloud "cd $SRV && tar -xf -"

echo "== 8) md5 server vs SHA aprobado"
md5_de() { if command -v md5 >/dev/null; then md5 -q; else md5sum | cut -d' ' -f1; fi; }
for f in docker-compose.yml Dockerfile app/jev_ads.py app/jev_asesor.py app/jev_vista.py app/jev_catalogo.py \
         app/jev_juicios.py app/api_dashboard.py \
         app/api_fabrica.py app/cycle.py tools/jev_ads.py tools/jev_fichas.py; do
  local_md5=$(git show "$APROBADO:$f" | md5_de)
  srv_md5=$(ssh goncloud "md5sum $SRV/$f" | cut -d' ' -f1)
  [ "$local_md5" = "$srv_md5" ] || { echo "ABORTA: md5 distinto en $f"; exit 1; }
  echo "md5 OK $f"
done

echo "== 9) Guardas antes de recrear (reintento 30s hasta 30min)"
LISTO=0
for i in $(seq 1 60); do
  if guardas_proceso; then LISTO=1; break; fi
  echo "intento $i/60: guardas no pasan (app.cli en curso o sin respuesta); reintento en 30s"
  sleep 30
done
if [ "$LISTO" != "1" ]; then
  echo "DETENIDO: las guardas no pasaron en 30 minutos. Me detengo SIN recrear el contenedor y SIN correr la reversa."
  echo "El contenedor viejo sigue corriendo su imagen anterior: la migracion aplicada y el codigo copiado no lo tocan."
  echo "Para terminar: espera a que docker top orbit-app-1 no liste app.cli y corre: ssh goncloud 'cd $SRV && docker compose up -d --no-deps --build app'"
  exit 1
fi
echo "guardas OK"

echo "== 10) Build + recrear app (digest antes/despues) + health + ORBIT_DSN_JEV visible (sin valor)"
ssh goncloud "set -e; cd $SRV; \
  echo DIGEST antes=\$(docker inspect -f '{{.Image}}' orbit-app-1); \
  docker compose up -d --no-deps --build app; \
  echo DIGEST despues=\$(docker inspect -f '{{.Image}}' orbit-app-1); \
  sleep 5; curl -sS http://127.0.0.1:8010/health; echo; \
  docker ps --filter name=orbit-app-1 --format '{{.Names}} {{.Status}}'; \
  docker exec orbit-app-1 printenv ORBIT_DSN_JEV | grep -q . && echo JEV-DSN-presente || { echo JEV-DSN-AUSENTE; exit 1; }"

echo "== LISTO. STAMP=$STAMP"
echo "Siguiente: bash $DIR/checklist.sh $STAMP"
echo "Reversa:   bash $DIR/rollback.sh $STAMP"
