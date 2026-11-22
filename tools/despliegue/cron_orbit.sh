#!/usr/bin/env bash
# Bloque de cron de Orbit (ARREGLOS 01 N.1/N.2). La unica fuente es
# tools/despliegue/crontab-orbit.txt; este script lo prepara, lo instala, lo revisa
# o restaura el bloque anterior.
# Uso en goncloud: bash cron_orbit.sh prepara|instala|revisa|restaura
#   (ORBIT_CRON_BLOQUE apunta al archivo; por omision, el de junto al script).
#   restaura usa ORBIT_CRON_RESPALDO, un crontab completo del respaldo que deja instala.
#   instala acepta ORBIT_CRON_RESPALDO_NUEVO: el respaldo que creo prepara.
# Desde otra maquina: subir crontab-orbit.txt y pasar su ruta en ORBIT_CRON_BLOQUE.
#
# prepara, instala y revisa exigen cron-avisa en el host (ORBIT_CRON_AVISA, por
# omision /usr/local/bin/cron-avisa) cuando el bloque lo usa, y rechazan un crontab
# con mas de un bloque "# === Orbit (".
# prepara: si el bloque cambiaria, respalda el crontab en ORBIT_CRON_ARCHIVO e
# imprime `respaldo=<ruta>` SIN escribirlo. Si ya es igual dice "sin cambios".
# instala: con ORBIT_CRON_RESPALDO_NUEVO usa ese respaldo (y comprueba que el
# crontab no cambio desde prepara); sin el, respalda e imprime `respaldo=<ruta>`.
# Reemplaza SOLO el bloque "# === Orbit (" por el canonico. Cuenta las lineas con
# cron-avisa antes y despues: si bajan, ABORTA sin escribir. Idempotente: si ya es
# igual dice "sin cambios". Las lineas fuera del bloque no se tocan.
# revisa: compara el bloque del host contra el archivo; sale 0 si es igual y 1 con
# el diff si no. Cualquier otra salida es error.
# restaura: toma el bloque "# === Orbit (" del respaldo y lo pone en el crontab
# actual; es la reversa de instala y no aplica la guarda de avisos.
set -euo pipefail

ARCHIVO=${ORBIT_CRON_ARCHIVO:-/mnt/data/appdata/orbit/archive}
USUARIO=${ORBIT_CRON_USUARIO:-gon}
AVISA=${ORBIT_CRON_AVISA:-/usr/local/bin/cron-avisa}

AQUI=$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)
BLOQUE=${ORBIT_CRON_BLOQUE:-$AQUI/crontab-orbit.txt}
RESPALDO=${ORBIT_CRON_RESPALDO:-}
RESPALDO_NUEVO=${ORBIT_CRON_RESPALDO_NUEVO:-}

ACCION=${1:-}
case "$ACCION" in
  prepara | instala | revisa | restaura) ;;
  *) echo "uso: bash cron_orbit.sh prepara|instala|revisa|restaura" >&2; exit 2 ;;
esac
if [ -n "${2:-}" ]; then
  if [ "$ACCION" = restaura ]; then RESPALDO=$2; else RESPALDO_NUEVO=$2; fi
fi

if [ "$ACCION" = restaura ]; then
  [ -f "$RESPALDO" ] || { echo "no encuentro el respaldo: $RESPALDO" >&2; exit 1; }
else
  [ -f "$BLOQUE" ] && [ -s "$BLOQUE" ] || {
    echo "no encuentro el bloque canonico: $BLOQUE" >&2
    exit 1
  }
  if grep -q 'cron-avisa' "$BLOQUE" && [ ! -x "$AVISA" ]; then
    echo "ABORTA: falta $AVISA en el host y el bloque canonico lo usa" >&2
    exit 1
  fi
fi

ACTUAL=$(mktemp)
NUEVO=$(mktemp)
HOST=$(mktemp)
trap 'rm -f "$ACTUAL" "$NUEVO" "$HOST"' EXIT
crontab -u "$USUARIO" -l > "$ACTUAL" || { echo "no pude leer el crontab de $USUARIO" >&2; exit 1; }
bloques=$(grep -c '^# === Orbit (' "$ACTUAL" || true)
if [ "$bloques" -gt 1 ]; then
  echo "ABORTA: el crontab de $USUARIO trae $bloques bloques '# === Orbit ('" >&2
  exit 1
fi

cuenta_avisos() { grep -c 'cron-avisa' "$1" || true; }

# Cuenta avisos de $1 contando como maximo una linea jev-vigia con cron-avisa
# (deduplicar copias repetidas de jev-vigia dentro o fuera del bloque no reduce
# los avisos reales; quitar cron-avisa de jev-vigia o de otra tarea sigue abortando).
cuenta_avisos_dedup_vigia() {
  awk '
    /cron-avisa/ {
      if ($0 ~ /app\.cli jev-vigia/) {
        if (!vio_vigia) { vio_vigia = 1; n++ }
      } else {
        n++
      }
    }
    END { print n + 0 }' "$1"
}

extrae_bloque() {
  awk '/^# === / { dentro = ($0 ~ /^# === Orbit \(/) } dentro { print }' "$1" > "$2"
}

vigia_fuera() {
  awk '
    /^# === / { dentro = ($0 ~ /^# === Orbit \(/) }
    !dentro && ($0 ~ /app\.cli jev-vigia/ || $0 ~ /^# job_key=jev-vigia/) { print }
  ' "$1"
}

fuera_sin_vigia() {
  awk '
    /^# === / { dentro = ($0 ~ /^# === Orbit \(/) }
    !dentro && !($0 ~ /app\.cli jev-vigia/ || $0 ~ /^# job_key=jev-vigia/) { print }
  ' "$1"
}

reemplaza_bloque() {
  local quita_vigia=${2:-0}
  awk -v canon="$1" -v quita="$quita_vigia" '
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
    !dentro {
      if (quita == 1 && ($0 ~ /app\.cli jev-vigia/ || $0 ~ /^# job_key=jev-vigia/)) next
      print
    }
    END { if (!puesto) exit 3 }' "$ACTUAL" > "$NUEVO"
}

respalda() {
  mkdir -p "$ARCHIVO"
  respaldo="$ARCHIVO/crontab-$USUARIO.$(date -u +%Y%m%d-%H%M%S).cron-orbit-$1"
  cp "$ACTUAL" "$respaldo"
  printf 'respaldo=%s\n' "$respaldo"
}

# Prepara el reemplazo: NO escribe el crontab. Sale 0 con `sin cambios` o con un
# respaldo existente en `respaldo=<ruta>`.
prepara_reemplazo() {
  reemplaza_bloque "$BLOQUE" 1 || {
    echo "ABORTA: el crontab de $USUARIO no trae el bloque '# === Orbit ('" >&2
    return 1
  }
  if cmp -s "$NUEVO" "$ACTUAL"; then
    echo "sin cambios"
    return 0
  fi
  antes=$(cuenta_avisos_dedup_vigia "$ACTUAL")
  despues=$(cuenta_avisos "$NUEVO")
  if [ "$despues" -lt "$antes" ]; then
    echo "ABORTA: el bloque canonico dejaria menos avisos ($despues < $antes); no escribo" >&2
    return 1
  fi
  respalda prepara
}

case "$ACCION" in
  revisa)
    extrae_bloque "$ACTUAL" "$HOST"
    if [ ! -s "$HOST" ]; then
      echo "el crontab de $USUARIO no trae el bloque '# === Orbit ('" >&2
      exit 1
    fi
    if [ -n "$(vigia_fuera "$ACTUAL")" ]; then
      echo "hay lineas jev-vigia fuera del bloque Orbit en el crontab de $USUARIO" >&2
      exit 1
    fi
    if diff -u "$HOST" "$BLOQUE" >&2; then
      echo "bloque Orbit igual al canonico"
      exit 0
    fi
    echo "el bloque Orbit del crontab de $USUARIO no es el canonico" >&2
    exit 1 ;;
  prepara)
    prepara_reemplazo ;;
  instala)
    reemplaza_bloque "$BLOQUE" 1 || {
      echo "ABORTA: el crontab de $USUARIO no trae el bloque '# === Orbit ('" >&2
      exit 1
    }
    if cmp -s "$NUEVO" "$ACTUAL"; then
      echo "sin cambios"
      exit 0
    fi
    antes=$(cuenta_avisos_dedup_vigia "$ACTUAL")
    despues=$(cuenta_avisos "$NUEVO")
    if [ "$despues" -lt "$antes" ]; then
      echo "ABORTA: el bloque canonico dejaria menos avisos ($despues < $antes); no escribo" >&2
      exit 1
    fi
    if [ -n "$RESPALDO_NUEVO" ]; then
      [ -f "$RESPALDO_NUEVO" ] || { echo "no encuentro el respaldo de prepara: $RESPALDO_NUEVO" >&2; exit 1; }
      cmp -s "$ACTUAL" "$RESPALDO_NUEVO" || {
        echo "ABORTA: el crontab cambio entre prepara e instala; vuelve a intentarlo" >&2
        exit 1
      }
      respaldo=$RESPALDO_NUEVO
    else
      respalda instala
    fi
    crontab -u "$USUARIO" "$NUEVO"
    echo "bloque Orbit instalado" ;;
  restaura)
    extrae_bloque "$RESPALDO" "$HOST"
    if [ ! -s "$HOST" ]; then
      echo "el respaldo no trae el bloque '# === Orbit ('" >&2
      exit 1
    fi
    vigia_respaldo=$(vigia_fuera "$RESPALDO")
    if [ -n "$vigia_respaldo" ] && [ "$(fuera_sin_vigia "$ACTUAL")" = "$(fuera_sin_vigia "$RESPALDO")" ]; then
      cp "$RESPALDO" "$NUEVO"
    else
      reemplaza_bloque "$HOST" 0 || {
        echo "ABORTA: el crontab de $USUARIO no trae el bloque '# === Orbit ('" >&2
        exit 1
      }
      if [ -n "$vigia_respaldo" ] && [ -z "$(vigia_fuera "$NUEVO")" ]; then
        printf '%s\n' "$vigia_respaldo" >> "$NUEVO"
      fi
    fi
    if cmp -s "$NUEVO" "$ACTUAL"; then
      echo "sin cambios"
      exit 0
    fi
    respalda restaura
    crontab -u "$USUARIO" "$NUEVO"
    echo "bloque Orbit restaurado" ;;
esac
