#!/usr/bin/env bash
# Reversa AUTO-03 (B.2). Uso:
#   MOTOR_SHA=<sha> bash tools/despliegue/rollback.sh
# Deshace SOLO lo anotado en $SRV/despliegue/ultimo.env para este MOTOR_SHA
# (la primera linea CODIGO= y, por clave, la primera CLAVE=), en este orden y
# siguiendo aunque un paso falle: claves a su valor previo por el mismo CLI
# (productos-modo), archivos de predeploy-<stamp> con rebuild y /health,
# $SRV/despliegue/sha de vuelta a SHA_PREVIO (lo borra si venia vacio) y, al
# final, las NNNN_reversa_* de las migraciones anotadas en orden inverso.
# Una reversa que aborta por filas (regla 7) no impide lo anterior: las
# migraciones de AUTO-03 son aditivas y el codigo anterior corre con ellas.
# Termina con ESTADO=revertido o ESTADO=rollback_incompleto (nombrando lo que
# no pudo deshacer) y sale distinto de cero en los dos casos; correrlo otra
# vez continua una reversa cortada. Sale 0 sin tocar nada si ultimo.env
# falta, es de otro MOTOR_SHA, no trae lineas de cambio o su ultimo ESTADO es
# abortado_sin_cambios o revertido. Toma el mismo candado que desplegar.sh
# antes de leer ultimo.env.
# ORBIT_SIMULACION=1: contra ORBIT_SIM_DSN y $SRV local (ORBIT_SRV).
set -uo pipefail

: "${MOTOR_SHA:?uso: MOTOR_SHA=<sha> bash tools/despliegue/rollback.sh}"

SIM=${ORBIT_SIMULACION:-0}
SRV=${ORBIT_SRV:-/mnt/data/appdata/orbit}
DSN_SIM=${ORBIT_SIM_DSN:-postgresql://orbit:orbit@127.0.0.1:5433/orbit_sim_auto03}
AUTO03_ESPERA_S=${AUTO03_ESPERA_S:-600}
REPO=$(git rev-parse --show-toplevel)
TMP=$(mktemp -d "${TMPDIR:-/tmp}/auto03-rollback.XXXXXX")
# shellcheck source=lib.sh
source "$REPO/tools/despliegue/lib.sh"
trap 'suelta_candado; rm -rf "$TMP"' EXIT
cd "$REPO"

MOTOR_SHA=$(git rev-parse --verify "${MOTOR_SHA}^{commit}") || {
  echo "FALLA: MOTOR_SHA no es un commit"
  exit 1
}

NO_PUDO=""

no_pudo() {
  echo "NO PUDO: $*" >&2
  NO_PUDO="${NO_PUDO:+$NO_PUDO; }$*"
}

if ! toma_candado; then
  echo "candado $SRV/despliegue/.lock ocupado: rollback no lee ni revierte hasta obtenerlo" >&2
  exit "$ABORTA_GUARDA"
fi

L=$(lee_ultimo)
ULT_SHA=$(printf '%s\n' "$L" | ultimo_de MOTOR_SHA)
ULT_ESTADO=$(printf '%s\n' "$L" | ultimo_de ESTADO)
if [ -z "$L" ] ||
  [ "$ULT_SHA" != "$MOTOR_SHA" ] ||
  [ "$ULT_ESTADO" = abortado_sin_cambios ] ||
  [ "$ULT_ESTADO" = revertido ] ||
  ! printf '%s\n' "$L" | tiene_cambios; then
  echo "nada que revertir para $MOTOR_SHA (diario ausente, de otro SHA, terminal o sin cambios)"
  exit 0
fi
if [ "$ULT_ESTADO" != revirtiendo ]; then
  anota_ultimo "ESTADO=revirtiendo"
fi

echo "== 1) claves a su valor previo (primera CLAVE= por clave)"
VISTOS=" "
while IFS= read -r linea <&3; do
  [ -n "$linea" ] || continue
  resto=${linea#CLAVE=}
  clave=${resto%%:*}
  previa=${resto#*:}
  case "$VISTOS" in
    *" $clave "*) continue ;;
  esac
  VISTOS="$VISTOS$clave "
  actual=$(valor_vigente "$clave") || { no_pudo "leer el valor vigente de $clave"; continue; }
  [ "$actual" = "$previa" ] && { echo "OK    $clave ya esta en '$previa'"; continue; }
  cmd=$(clave_a_cli "$clave" "$previa") || {
    no_pudo "regresar $clave: la clave no tiene camino por productos-modo"
    continue
  }
  echo "$clave: '${actual:-<vacio>}' -> '${previa:-<vacio>}'"
  corre_en_contenedor $cmd || no_pudo "regresar $clave via productos-modo"
done 3< <(printf '%s\n' "$L" | grep '^CLAVE=' || true)

echo "== 2) archivos de predeploy con rebuild y /health"
STAMP_PRE=$(printf '%s\n' "$L" | primero_de CODIGO)
if [ -n "$STAMP_PRE" ]; then
  DIR_PRE="despliegue/${STAMP_PRE}"
  if [ "$SIM" = 1 ]; then
    if [ ! -d "$SRV/$DIR_PRE" ]; then
      no_pudo "restaurar codigo: falta $SRV/$DIR_PRE"
    else
      rm -rf "$SRV/app" "$SRV/tools"
      cp -a "$SRV/$DIR_PRE/app" "$SRV/$DIR_PRE/tools" "$SRV/"
      cp -a "$SRV/$DIR_PRE/Dockerfile" "$SRV/$DIR_PRE/.dockerignore" \
        "$SRV/$DIR_PRE/pyproject.toml" "$SRV/$DIR_PRE/uv.lock" "$SRV/"
      docker compose up -d --no-deps --build app || no_pudo "docker compose up del rollback"
      echo "SIMULACION: /health no se mide (no hay contenedor que conteste)"
    fi
  else
    ssh goncloud bash -s <<REMOTE || no_pudo "restaurar codigo desde $STAMP_PRE"
set -e
cd '$SRV'
[ -d '$DIR_PRE/app' ] || { echo "falta $DIR_PRE"; exit 1; }
rm -rf app tools
cp -a '$DIR_PRE/app' '$DIR_PRE/tools' .
cp -a '$DIR_PRE/Dockerfile' '$DIR_PRE/.dockerignore' '$DIR_PRE/pyproject.toml' '$DIR_PRE/uv.lock' .
docker compose up -d --no-deps --build app
sleep 5
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8010/health | grep -q 200 || { echo 'health fallo tras el rebuild'; exit 1; }
echo "codigo restaurado desde $STAMP_PRE"
REMOTE
  fi
else
  echo "sin CODIGO= en el diario: nada de codigo que restaurar"
fi

echo "== 3) sha de vuelta a SHA_PREVIO"
PREVIO=$(printf '%s\n' "$L" | primero_de SHA_PREVIO)
if [ -n "$PREVIO" ]; then
  escribe_sha_srv "$PREVIO" || no_pudo "escribir $SRV/despliegue/sha"
  echo "sha=$PREVIO"
else
  borra_sha_srv || no_pudo "borrar $SRV/despliegue/sha"
  echo "sha borrado (venia vacio)"
fi

echo "== 4) reversas de las migraciones anotadas, en orden inverso"
REVS=""
while IFS= read -r m; do
  [ -n "$m" ] || continue
  n=$(basename "$m" .sql | cut -c1-4)
  resto=$(basename "$m" .sql | cut -c6-)
  REVS="${n}_reversa_${resto}.sql $REVS"
done < <(printf '%s\n' "$L" | grep '^MIGRACION=' || true)
if [ -z "${REVS// }" ]; then
  echo "ninguna migracion anotada: nada que reversar"
else
  for r in $REVS; do
    git cat-file -e "$MOTOR_SHA:migrations/$r" 2>/dev/null || {
      no_pudo "la reversa $r no existe en $MOTOR_SHA"
      continue
    }
    if git show "$MOTOR_SHA:migrations/$r" | w; then
      echo "reversa aplicada $r"
    else
      no_pudo "la reversa $r fallo (si aborta por filas, regla 7, el codigo anterior corre con ella aplicada)"
    fi
  done
fi

if [ -z "$NO_PUDO" ]; then
  anota_ultimo "ESTADO=revertido"
  echo "== REVERSA COMPLETA (ESTADO=revertido)"
else
  anota_ultimo "ESTADO=rollback_incompleto"
  anota_ultimo "FALLARON=$NO_PUDO"
  echo "== REVERSA INCOMPLETA (ESTADO=rollback_incompleto); correrla otra vez la continua:" >&2
  printf '%s\n' "$NO_PUDO" >&2
fi
exit 1
