#!/usr/bin/env bash
# Linea de cron del vigia de Jev (CLARIDAD 01 B.5/B.7) en el crontab de gon.
# Uso en goncloud: bash cron_jev_vigia.sh instala|quita|revisa|presente
# Desde otra maquina: git show <sha>:tools/despliegue/cron_jev_vigia.sh | ssh goncloud 'bash -s -- instala'
#
# instala: deja la linea (con su job_key) una sola vez dentro del bloque "# === Orbit (", despues
# de la de jev-senales; sin esa, al final del bloque. Si ya estaba fuera del bloque o repetida (el
# instalador del bloque de docs/DEPLOY.md la deja fuera al reescribirlo), la mueve. quita: borra
# solo esas dos lineas. Las dos respaldan el crontab en ORBIT_CRON_ARCHIVO antes de escribirlo, no
# tocan ninguna otra linea y son idempotentes: si no hay nada que cambiar, no escriben. Imprimen
# "agregada", "movida", "quitada" o "sin cambios". revisa sale 0 si la linea efectiva esta una
# sola vez dentro del bloque; presente, si esta en cualquier parte del crontab.
set -euo pipefail

ARCHIVO=${ORBIT_CRON_ARCHIVO:-/mnt/data/appdata/orbit/archive}
USUARIO=${ORBIT_CRON_USUARIO:-gon}
JOB_KEY='# job_key=jev-vigia  CLARIDAD 01 B.5: avisa si Jev no corre hace mas de 13 h'
LINEA='0 11 * * * /usr/local/bin/cron-avisa --nombre "Vigía de JEV" --cada 24 -- docker exec orbit-app-1 python -m app.cli jev-vigia >> /mnt/data/appdata/orbit/logs/jev-vigia.log 2>&1'

ACCION=${1:-}
case "$ACCION" in
  instala | quita | revisa | presente) ;;
  *) echo "uso: bash cron_jev_vigia.sh instala|quita|revisa|presente" >&2; exit 2 ;;
esac

ACTUAL=$(mktemp)
NUEVO=$(mktemp)
trap 'rm -f "$ACTUAL" "$NUEVO"' EXIT
crontab -u "$USUARIO" -l > "$ACTUAL" || { echo "no pude leer el crontab de $USUARIO" >&2; exit 1; }

# Lineas efectivas de la linea dentro del bloque Orbit: desde su cabecera hasta la siguiente "# ===".
en_bloque() {
  awk -v linea="$LINEA" '
    /^# === / { dentro = ($0 ~ /^# === Orbit \(/) }
    dentro && $0 == linea { n++ }
    END { print n + 0 }' "$ACTUAL"
}

escribe() {
  mkdir -p "$ARCHIVO"
  cp "$ACTUAL" "$ARCHIVO/crontab-$USUARIO.$(date -u +%Y%m%d-%H%M%S).jev-vigia-$ACCION"
  crontab -u "$USUARIO" "$NUEVO"
}

case "$ACCION" in
  revisa)
    n=$(en_bloque)
    if [ "$n" = 1 ]; then echo "linea jev-vigia en el bloque Orbit"; exit 0; fi
    echo "linea jev-vigia en el bloque Orbit: $n veces (se espera 1)" >&2
    exit 1 ;;
  presente)
    grep -qxF -- "$LINEA" "$ACTUAL" ;;
  instala)
    ANTES=$(grep -cxF -- "$LINEA" "$ACTUAL" || true)
    if [ "$ANTES" = 1 ] && [ "$(en_bloque)" = 1 ]; then
      echo "sin cambios"
      exit 0
    fi
    grep -q '^# === Orbit (' "$ACTUAL" || { echo "no encuentro el bloque '# === Orbit (' en el crontab de $USUARIO" >&2; exit 1; }
    awk -v job="$JOB_KEY" -v linea="$LINEA" '
      function pon() { print job; print linea; puesta = 1 }
      $0 == job || $0 == linea { next }
      /^# === / {
        if (dentro && !puesta) pon()
        dentro = ($0 ~ /^# === Orbit \(/)
      }
      { print }
      dentro && !puesta && /app\.cli jev-senales/ && !/^#/ { pon() }
      END { if (dentro && !puesta) pon() }' "$ACTUAL" > "$NUEVO"
    escribe
    if [ "$ANTES" = 0 ]; then echo "agregada"; else echo "movida"; fi ;;
  quita)
    if ! grep -qxF -- "$LINEA" "$ACTUAL" && ! grep -qxF -- "$JOB_KEY" "$ACTUAL"; then
      echo "sin cambios"
      exit 0
    fi
    awk -v job="$JOB_KEY" -v linea="$LINEA" '$0 != job && $0 != linea' "$ACTUAL" > "$NUEVO"
    escribe
    echo "quitada" ;;
esac
