#!/usr/bin/env bash
# Bloque de cron de Orbit (ARREGLOS 01 N.1/N.2). La unica fuente es
# tools/despliegue/crontab-orbit.txt; este script lo instala o lo revisa.
# Uso en goncloud: bash cron_orbit.sh instala|revisa
#   (ORBIT_CRON_BLOQUE apunta al archivo; por omision, el de junto al script).
# Desde otra maquina: subir crontab-orbit.txt y pasar su ruta en ORBIT_CRON_BLOQUE.
#
# instala: exige cron-avisa en el host (ORBIT_CRON_AVISA, por omision
# /usr/local/bin/cron-avisa), respalda el crontab en ORBIT_CRON_ARCHIVO y reemplaza
# SOLO el bloque "# === Orbit (" por el canonico. Cuenta las lineas con cron-avisa
# antes y despues: si bajan, ABORTA sin escribir. Idempotente: si ya es igual dice
# "sin cambios". Las lineas fuera del bloque no se tocan.
# revisa: compara el bloque del host contra el archivo; sale 0 si es igual y 1 con
# el diff si no. Cualquier otra salida es error.
set -euo pipefail

ARCHIVO=${ORBIT_CRON_ARCHIVO:-/mnt/data/appdata/orbit/archive}
USUARIO=${ORBIT_CRON_USUARIO:-gon}
AVISA=${ORBIT_CRON_AVISA:-/usr/local/bin/cron-avisa}

AQUI=$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)
BLOQUE=${ORBIT_CRON_BLOQUE:-$AQUI/crontab-orbit.txt}

ACCION=${1:-}
case "$ACCION" in
  instala | revisa) ;;
  *) echo "uso: bash cron_orbit.sh instala|revisa" >&2; exit 2 ;;
esac

[ -f "$BLOQUE" ] && [ -s "$BLOQUE" ] || {
  echo "no encuentro el bloque canonico: $BLOQUE" >&2
  exit 1
}

ACTUAL=$(mktemp)
NUEVO=$(mktemp)
HOST=$(mktemp)
trap 'rm -f "$ACTUAL" "$NUEVO" "$HOST"' EXIT
crontab -u "$USUARIO" -l > "$ACTUAL" || { echo "no pude leer el crontab de $USUARIO" >&2; exit 1; }

cuenta_avisos() { grep -c 'cron-avisa' "$1" || true; }

case "$ACCION" in
  revisa)
    awk '/^# === / { dentro = ($0 ~ /^# === Orbit \(/) } dentro { print }' "$ACTUAL" > "$HOST"
    if [ ! -s "$HOST" ]; then
      echo "el crontab de $USUARIO no trae el bloque '# === Orbit ('" >&2
      exit 1
    fi
    if diff -u "$HOST" "$BLOQUE" >&2; then
      echo "bloque Orbit igual al canonico"
      exit 0
    fi
    echo "el bloque Orbit del crontab de $USUARIO no es el canonico" >&2
    exit 1 ;;
  instala)
    if grep -q 'cron-avisa' "$BLOQUE" && [ ! -x "$AVISA" ]; then
      echo "ABORTA: falta $AVISA en el host y el bloque canonico lo usa" >&2
      exit 1
    fi
    awk -v canon="$BLOQUE" '
      BEGIN { while ((getline l < canon) > 0) bloque = bloque l "\n"; close(canon) }
      /^# === / {
        if (dentro) dentro = 0
        if (!puesto && $0 ~ /^# === Orbit \(/) {
          printf "%s", bloque
          puesto = 1
          dentro = 1
          next
        }
      }
      !dentro { print }
      END { if (!puesto) exit 3 }' "$ACTUAL" > "$NUEVO" || {
      echo "ABORTA: el crontab de $USUARIO no trae el bloque '# === Orbit ('" >&2
      exit 1
    }
    if cmp -s "$NUEVO" "$ACTUAL"; then
      echo "sin cambios"
      exit 0
    fi
    antes=$(cuenta_avisos "$ACTUAL")
    despues=$(cuenta_avisos "$NUEVO")
    if [ "$despues" -lt "$antes" ]; then
      echo "ABORTA: el bloque canonico dejaria menos avisos ($despues < $antes); no escribo" >&2
      exit 1
    fi
    mkdir -p "$ARCHIVO"
    cp "$ACTUAL" "$ARCHIVO/crontab-$USUARIO.$(date -u +%Y%m%d-%H%M%S).cron-orbit-$ACCION"
    crontab -u "$USUARIO" "$NUEVO"
    echo "bloque Orbit instalado" ;;
esac
