#!/usr/bin/env bash
# Checklist del despliegue por el motor (AUTO-03 B.2). Uso: MOTOR_SHA=<sha> bash checklist.sh
# Solo lectura salvo una linea: si todo pasa y ultimo.env es de este MOTOR_SHA, anota
# ESTADO=verificado. Sale 0 solo si produccion ya corre un SHA que contiene MOTOR_SHA.
set -uo pipefail
. "$(dirname "$0")/comun.sh"

SHA=$(git rev-parse --verify "${MOTOR_SHA:?falta MOTOR_SHA}^{commit}") || exit 1
FALLAS=0
falla() { echo "FALLA $*"; FALLAS=$((FALLAS + 1)); }
ok() { echo "OK    $*"; }

L=$(lee_ultimo) || { echo "FALLA no pude leer $ULTIMO"; exit 1; }
DESPLEGADO=$(srv "cat $DESP/sha 2>/dev/null || true") || { echo "FALLA no pude leer $DESP/sha"; exit 1; }
if contiene "$DESPLEGADO" "$SHA"; then ok "el SHA desplegado ($DESPLEGADO) contiene $SHA"; else falla "el SHA desplegado ('$DESPLEGADO') no contiene $SHA"; fi

if MIGS=$(migraciones_de "$SHA"); then
  while IFS=$'\t' read -r m s <&3; do
    [ -n "$m" ] || continue
    st=$(sonda "$s") || st=error
    if [ "$st" = t ]; then ok "migracion $m aplicada"; else falla "migracion $m: sonda '$st'"; fi
  done 3<<< "$MIGS"
else
  falla "no pude listar las migraciones de $SHA"
fi

if git cat-file -e "$SHA:$CRON_VIGIA" 2>/dev/null; then
  if R=$(cron_vigia revisa 2>&1); then ok "$R"; else falla "cron del vigia de Jev: $R"; fi
fi

if git cat-file -e "$SHA:tools/$REFRESCO" 2>/dev/null; then
  if srv "cmp -s $SRV/tools/$REFRESCO $SRV/$REFRESCO"; then ok "$SRV/$REFRESCO es el de tools/"; else falla "$SRV/$REFRESCO distinto de tools/$REFRESCO o ausente"; fi
fi

H=$(salud)
if [ "$H" = 200 ]; then ok "/health 200"; else falla "/health dio '$H'"; fi

PREVIO=$(primero SHA_PREVIO "$L")
if contiene "$PREVIO" "$SHA"; then
  ok "monotonia: $PREVIO ya contenia $SHA; este despliegue no toca claves"
elif ! VIEJO=$(interruptores_de "$PREVIO") || ! NUEVO=$(interruptores_de "$SHA"); then
  falla "no pude leer $INTERRUPTORES"
else
  CLAVES=$(printf '%s\n' "$L" | sed -n 's/^CLAVE=\([^:]*\):.*/\1/p' | LC_ALL=C sort -u)
  for k in $(claves_a_converger "$PREVIO" "$VIEJO" "$NUEVO"); do
    if printf '%s\n' "$CLAVES" | grep -qx "$k"; then ok "clave $k anotada"; else falla "clave $k cambio en $INTERRUPTORES y no tiene su linea CLAVE="; fi
  done
  for k in $CLAVES; do
    vig=$(valor_vigente "$k") || { falla "no pude leer la config vigente de $k"; continue; }
    esperado=$(valor "$k" "$NUEVO")
    if [ "$vig" = "$esperado" ]; then ok "config $k = '${vig:-off}'"; else falla "config $k = '${vig:-off}', el archivo pide '${esperado:-off}'"; fi
  done
fi

if [ "$FALLAS" -ne 0 ]; then
  echo "== CHECKLIST ROJO: $FALLAS falla(s)"
  exit 1
fi
if [ "$(ultimo_de MOTOR_SHA "$L")" = "$SHA" ] && [ "$(ultimo_de ESTADO "$L")" != verificado ]; then
  rc=0
  toma_candado || rc=$?
  [ "$rc" = 0 ] || { echo "candado ocupado: verde sin anotar ESTADO=verificado"; exit 75; }
  L=$(lee_ultimo) || { echo "FALLA no pude releer $ULTIMO"; exit 1; }
  case "$(ultimo_de MOTOR_SHA "$L"):$(ultimo_de ESTADO "$L")" in
    "$SHA":verificado | "$SHA":revirtiendo | "$SHA":rollback_incompleto | "$SHA":revertido) ;;
    "$SHA":*) anota ESTADO=verificado ;;
  esac
fi
echo "== CHECKLIST VERDE $SHA"
