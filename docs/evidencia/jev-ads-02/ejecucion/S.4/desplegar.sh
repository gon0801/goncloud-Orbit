#!/usr/bin/env bash
# Despliegue S.4 de Jev Ads 02: SOLO CODIGO (sin migracion). Copia el SHA
# aprobado (job jev-senales, bloque jev de /salud; el cron lo instala el
# dueno a mano con el bloque del paso 8, ANTES del checklist, guia Despliega 2).
# Patron de S.3/desplegar.sh: preflight, respaldo (codigo + compose + SHA;
# el .env NO se copia a ningun lado), git archive, md5, guardas con
# reintento, build + recrear + health, y seco del job apagado.
# Uso: cd ~/dev/goncloud-Orbit && bash docs/evidencia/jev-ads-02/ejecucion/S.4/desplegar.sh <sha-de-origin/master>
set -euo pipefail
trap 'echo "FALLO en la linea $LINENO. Reversa: bash $DIR/rollback.sh $STAMP"' ERR

APROBADO=${1:?uso: desplegar.sh <sha de origin/master con CI verde>}
REPO=$(git rev-parse --show-toplevel)
SRV=/mnt/data/appdata/orbit
STAMP=$(date -u +%Y%m%d-%H%M)
DIR=docs/evidencia/jev-ads-02/ejecucion/S.4
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

echo "== 1) Preflight (esperado cli=0 y t|t|t: nadie corre, S.3 aplicado, login listo)"
TOP=$(ssh goncloud "docker top orbit-app-1") || { echo "ABORTA: docker top no contesta"; exit 1; }
CLI=$(printf '%s\n' "$TOP" | grep -c 'app\.cli' || true)
R=$(ssh goncloud 'docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1 -tA' <<'SQL'
SELECT to_regclass('public.jev_senal') IS NOT NULL,
       pg_has_role('orbit_jev', 'app_jev', 'MEMBER'),
       EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'jev_senal_moneda_de_plataforma');
SQL
)
echo "cli=$CLI $R"
[ "$CLI" = "0" ] && [ "$R" = "t|t|t" ] || { echo "ABORTA: preflight no es cli=0 t|t|t"; exit 1; }

echo "== 2) Respaldo del codigo actual, compose y SHA (el .env NO se copia a ningun lado)"
ssh goncloud "set -e; cd $SRV; mkdir -p predeploy-$STAMP; \
  cp -a app Dockerfile .dockerignore pyproject.toml uv.lock tools docker-compose.yml predeploy-$STAMP/; \
  echo $APROBADO > predeploy-$STAMP/SHA; ls predeploy-$STAMP"

echo "== 3) Copiar el codigo del SHA aprobado (git archive, LF)"
git archive --format=tar "$APROBADO" app Dockerfile .dockerignore pyproject.toml uv.lock docker-compose.yml \
    tools/fabrica_campanas.py tools/jev_ads.py tools/jev_fichas.py \
  | ssh goncloud "cd $SRV && tar -xf -"

echo "== 4) md5 server vs SHA aprobado"
md5_de() { if command -v md5 >/dev/null; then md5 -q; else md5sum | cut -d' ' -f1; fi; }
for f in docker-compose.yml Dockerfile app/jev_senales.py app/jev_libro.py app/jev_lectura.py \
         app/optimizer/windows.py app/cli.py app/api_dashboard.py app/ui.py \
         app/templates/salud.html tools/jev_ads.py tools/jev_fichas.py; do
  local_md5=$(git show "$APROBADO:$f" | md5_de)
  srv_md5=$(ssh goncloud "md5sum $SRV/$f" | cut -d' ' -f1)
  [ "$local_md5" = "$srv_md5" ] || { echo "ABORTA: md5 distinto en $f"; exit 1; }
  echo "md5 OK $f"
done

echo "== 5) Guardas antes de recrear (reintento 30s hasta 30min)"
LISTO=0
for i in $(seq 1 60); do
  if guardas_proceso; then LISTO=1; break; fi
  echo "intento $i/60: guardas no pasan (app.cli en curso o sin respuesta); reintento en 30s"
  sleep 30
done
if [ "$LISTO" != "1" ]; then
  echo "DETENIDO: las guardas no pasaron en 30 minutos. Me detengo SIN recrear el contenedor."
  echo "Para terminar: espera a que docker top orbit-app-1 no liste app.cli y corre: ssh goncloud 'cd $SRV && docker compose up -d --no-deps --build app'"
  exit 1
fi
echo "guardas OK"

echo "== 6) Build + recrear app (digest antes/despues) + health"
ssh goncloud "set -e; cd $SRV; \
  echo DIGEST antes=\$(docker inspect -f '{{.Image}}' orbit-app-1); \
  docker compose up -d --no-deps --build app; \
  echo DIGEST despues=\$(docker inspect -f '{{.Image}}' orbit-app-1); \
  sleep 5; curl -sS http://127.0.0.1:8010/health; echo; \
  docker ps --filter name=orbit-app-1 --format '{{.Names}} {{.Status}}'"

echo "== 7) Seco del job apagado (esperado exit=0 y 'apagado' en la salida; si no, no se instala nada)"
set +e  # medir el exit sin que el trap ERR se adelante
SECO=$(ssh goncloud 'docker exec orbit-app-1 python -m app.cli jev-senales' 2>&1)
RC=$?
set -e
echo "$SECO"
[ "$RC" = "0" ] || { echo "ABORTA: el seco salio con $RC (no instales el cron)"; exit 1; }
echo "$SECO" | grep -q apagado || { echo "ABORTA: el seco no dice apagado (no instales el cron)"; exit 1; }

echo "== LISTO. STAMP=$STAMP"
echo "Reversa: bash $DIR/rollback.sh $STAMP"
echo
echo "== 8) MANUAL DEL DUENO (despues del merge): instalar la linea de cron (DEPLOY.md, pinzada por tests/test_jev_senales_cron.py::LINEA_JEV_SENALES)"
echo "Pega este bloque tal cual: respalda el crontab, agrega SOLO la linea y comprueba que el diff es +1:"
echo "ssh goncloud 'crontab -u gon -l > /tmp/cron-gon-antes-$STAMP.txt && cat /tmp/cron-gon-antes-$STAMP.txt; echo ---; (crontab -u gon -l; echo \"30 9,21 * * * /usr/bin/flock -n /tmp/jev-senales.lock docker exec orbit-app-1 python -m app.cli jev-senales --aplicar >> /mnt/data/appdata/orbit/logs/jev-senales.log 2>&1\") | crontab -u gon - && crontab -u gon -l > /tmp/cron-gon-despues-$STAMP.txt && diff /tmp/cron-gon-antes-$STAMP.txt /tmp/cron-gon-despues-$STAMP.txt; crontab -u gon -l | grep -c jev-senales'"
echo "Esperado: el diff muestra exactamente una linea agregada (> 30 9,21 ...) y el conteo final es 1."
echo
echo "Siguiente: bash $DIR/checklist.sh $STAMP (despues de instalar el cron)"
echo "Cierre:    leer el log de la primera corrida por cron (debe decir apagado): ssh goncloud 'tail -5 /mnt/data/appdata/orbit/logs/jev-senales.log'"
