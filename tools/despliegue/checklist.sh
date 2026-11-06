#!/usr/bin/env bash
# Checklist post-despliegue AUTO-03 (B.2). Uso:
#   MOTOR_SHA=<sha> bash tools/despliegue/checklist.sh
# Solo lectura, salvo su UNICA escritura: si pasa y ultimo.env es de este
# MOTOR_SHA, anota ESTADO=verificado. Comprueba:
#   - el SHA desplegado ($SRV/despliegue/sha) contiene MOTOR_SHA;
#   - toda migracion *_auto03_* con cabecera da t en su sonda;
#   - /health da 200;
#   - toda clave cuyo valor difiere entre el interruptores.env de SHA_PREVIO
#     y el de MOTOR_SHA tiene su linea CLAVE= (salvo que SHA_PREVIO ya
#     contenga MOTOR_SHA: monotonia, ese despliegue no toca claves), y las
#     claves que este despliegue convergio tienen en la ultima config_version
#     el valor del archivo; las demas no se comparan (apagado manual).
# Sale 0 en verde y 1 con alguna falla; el motor corre rollback.sh entonces.
# ORBIT_SIMULACION=1: contra ORBIT_SIM_DSN y $SRV local; /health informativo.
set -uo pipefail

: "${MOTOR_SHA:?uso: MOTOR_SHA=<sha> bash tools/despliegue/checklist.sh}"

SIM=${ORBIT_SIMULACION:-0}
SRV=${ORBIT_SRV:-/mnt/data/appdata/orbit}
DSN_SIM=${ORBIT_SIM_DSN:-postgresql://orbit:orbit@127.0.0.1:5433/orbit_sim_auto03}
REPO=$(git rev-parse --show-toplevel)
TMP=$(mktemp -d "${TMPDIR:-/tmp}/auto03-checklist.XXXXXX")
# shellcheck source=lib.sh
source "$REPO/tools/despliegue/lib.sh"
trap 'rm -rf "$TMP"' EXIT
cd "$REPO"

MOTOR_SHA=$(git rev-parse --verify "${MOTOR_SHA}^{commit}") || {
  echo "FALLA: MOTOR_SHA no es un commit"
  exit 1
}

FALLAS=0
falla() {
  echo "FALLA $*"
  FALLAS=$((FALLAS + 1))
}

SHA_DESPLIEGA=$(lee_sha_srv)
L=$(lee_ultimo)
PREVIO=$(printf '%s\n' "$L" | primero_de SHA_PREVIO)

echo "== 1) el SHA desplegado contiene MOTOR_SHA"
if [ -z "$SHA_DESPLIEGA" ]; then
  falla "$SRV/despliegue/sha esta vacio o no existe"
elif git merge-base --is-ancestor "$MOTOR_SHA" "$SHA_DESPLIEGA" 2>/dev/null; then
  echo "OK    desplegado=$SHA_DESPLIEGA contiene $MOTOR_SHA"
else
  falla "desplegado=$SHA_DESPLIEGA no contiene $MOTOR_SHA"
fi

echo "== 2) sondas de las migraciones *_auto03_* del SHA"
while IFS= read -r m; do
  [ -n "$m" ] || continue
  cab=$(git show "$MOTOR_SHA:$m" | sed -n 's/^-- sonda-aplicada: //p' | head -1)
  if [ -z "$cab" ]; then
    falla "la migracion $m no trae cabecera '-- sonda-aplicada'"
    continue
  fi
  estado=$(echo "$cab" | q) || {
    falla "la sonda de $m fallo"
    continue
  }
  if [ -z "$estado" ]; then
    falla "la sonda de $m salio vacia"
  elif [ "$estado" = t ]; then
    echo "OK    $m aplicada (sonda t)"
  else
    falla "$m pendiente (sonda $estado)"
  fi
done < <(git ls-tree --name-only "$MOTOR_SHA" migrations/ |
  grep '_auto03_' | grep -v '_reversa_' | sort || true)

echo "== 3) /health"
if [ "$SIM" = 1 ]; then
  echo "SIMULACION: /health no se mide (no hay contenedor que conteste)"
else
  codigo=$(ssh goncloud "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8010/health" || true)
  if [ "$codigo" = 200 ]; then
    echo "OK    /health = 200"
  else
    falla "/health dio '${codigo:-nada}', no 200"
  fi
fi

echo "== 4) claves de interruptores.env (SHA_PREVIO=${PREVIO:-<vacio>})"
MONOTONIA=0
if [ -n "$PREVIO" ] && git merge-base --is-ancestor "$MOTOR_SHA" "$PREVIO" 2>/dev/null; then
  MONOTONIA=1
  echo "OK    monotonia: $PREVIO ya contiene $MOTOR_SHA; no se exigen claves"
fi
if [ "$MONOTONIA" = 0 ]; then
  A=$(claves_de_sha "$PREVIO")
  B=$(claves_de_sha "$MOTOR_SHA")
  CONVERGIDAS=$(printf '%s\n' "$L" | sed -n 's/^CLAVE=//p' | cut -d: -f1 | sort -u)
  DIFIEREN=0
  # Union de claves: cambian las que difieren de valor Y las que desaparecen
  # del archivo nuevo (vaciarlo es el apagado documentado).
  while IFS= read -r clave <&3; do
    [ -n "$clave" ] || continue
    valor=$(valor_de_clave "$B" "$clave")
    if [ "$(valor_de_clave "$A" "$clave")" = "$valor" ]; then
      continue
    fi
    DIFIEREN=$((DIFIEREN + 1))
    if printf '%s\n' "$CONVERGIDAS" | grep -qx -- "$clave"; then
      vigente=$(valor_vigente "$clave") || {
        falla "no se pudo leer el valor vigente de $clave"
        continue
      }
      if [ "$vigente" = "$valor" ]; then
        if [ -n "$valor" ]; then
          echo "OK    clave $clave convergida ('$valor')"
        else
          echo "OK    clave $clave vaciada (off)"
        fi
      else
        falla "clave $clave convergida a '${vigente:-<vacio>}', el archivo trae '${valor:-<vaciar>}'"
      fi
    else
      falla "clave $clave difiere y no tiene linea CLAVE= en ultimo.env"
    fi
  done 3< <(printf '%s\n' "$B" "$A" | grep -v '^$' | sed 's/=.*$//' | sort -u)
  [ "$DIFIEREN" -gt 0 ] && echo "claves que difieren: $DIFIEREN" ||
    echo "OK    ninguna clave difiere"
fi

echo "== resultado: $FALLAS falla(s)"
if [ "$FALLAS" -eq 0 ] &&
  [ "$(printf '%s\n' "$L" | ultimo_de MOTOR_SHA)" = "$MOTOR_SHA" ] &&
  [ "$(printf '%s\n' "$L" | ultimo_de ESTADO)" != verificado ]; then
  anota_ultimo "ESTADO=verificado"
  echo "ESTADO=verificado anotado"
fi
[ "$FALLAS" -eq 0 ] || exit 1
exit 0
