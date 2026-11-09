#!/usr/bin/env bash
# Linea de cron del vigia de Jev (CLARIDAD 01 B.5/B.7) en el crontab de gon.
# Uso en goncloud: bash cron_jev_vigia.sh instala|quita|revisa
# Desde otra maquina: git show <sha>:tools/despliegue/cron_jev_vigia.sh | ssh goncloud 'bash -s -- instala'
#
# instala: agrega la linea (con su job_key) dentro del bloque "# === Orbit (", despues de la de
# jev-senales; sin esa, al final del bloque. quita: borra solo esas dos lineas. Las dos respaldan
# el crontab en ORBIT_CRON_ARCHIVO antes de escribirlo, no tocan ninguna otra linea y son
# idempotentes: si no hay nada que cambiar, no escriben. Imprimen "agregada", "quitada" o
# "sin cambios". revisa sale 0 si la linea efectiva esta una sola vez dentro del bloque.
set -euo pipefail

ARCHIVO=${ORBIT_CRON_ARCHIVO:-/mnt/data/appdata/orbit/archive}
USUARIO=${ORBIT_CRON_USUARIO:-gon}
JOB_KEY='# job_key=jev-vigia  CLARIDAD 01 B.5: avisa si Jev no corre hace mas de 13 h'
LINEA='0 11 * * * /usr/local/bin/cron-avisa --nombre "Vigía de JEV" --cada 24 -- docker exec orbit-app-1 python -m app.cli jev-vigia >> /mnt/data/appdata/orbit/logs/jev-vigia.log 2>&1'

ACCION=${1:-}
case "$ACCION" in
  instala | quita | revisa) ;;
  *) echo "uso: bash cron_jev_vigia.sh instala|quita|revisa" >&2; exit 2 ;;
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
  instala)
    if grep -qxF -- "$LINEA" "$ACTUAL"; then
      [ "$(en_bloque)" = 1 ] || { echo "la linea jev-vigia esta fuera del bloque Orbit o repetida; no la muevo" >&2; exit 1; }
      echo "sin cambios"
      exit 0
    fi
    grep -q '^# === Orbit (' "$ACTUAL" || { echo "no encuentro el bloque '# === Orbit (' en el crontab de $USUARIO" >&2; exit 1; }
    awk -v job="$JOB_KEY" -v linea="$LINEA" '
      function pon() { print job; print linea; puesta = 1 }
      /^# === / {
        if (dentro && !puesta) pon()
        dentro = ($0 ~ /^# === Orbit \(/)
      }
      { print }
      dentro && !puesta && /app\.cli jev-senales/ && !/^#/ { pon() }
      END { if (dentro && !puesta) pon() }' "$ACTUAL" > "$NUEVO"
    escribe
    echo "agregada" ;;
  quita)
    if ! grep -qxF -- "$LINEA" "$ACTUAL" && ! grep -qxF -- "$JOB_KEY" "$ACTUAL"; then
      echo "sin cambios"
      exit 0
    fi
    awk -v job="$JOB_KEY" -v linea="$LINEA" '$0 != job && $0 != linea' "$ACTUAL" > "$NUEVO"
    escribe
    echo "quitada" ;;
esac
