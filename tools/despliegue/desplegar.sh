#!/usr/bin/env bash
# Despliegue de Orbit por el motor (AUTO-03 B.1). Uso: MOTOR_SHA=<sha> bash desplegar.sh [--ensayo]
# Pasos: (0) candado y diario, (1) guardas, (2) monotonia, (3) respaldo, (4) migraciones
# de la receta (*_auto03_* y *_orbit07_*) con sonda, (5) codigo y build, (5b) linea de cron del
# vigia de Jev, (6) interruptores. Salidas: 0 bien; 75 no es el
# codigo (guardas o candado ocupados; si el diario ya traia cambios, los revierte antes);
# otra, falla. Contrato completo: docs/DEPLOY.md.
set -euo pipefail

ENSAYO=0
case "$#:${1:-}" in
  0:) ;;
  1:--ensayo) ENSAYO=1 ;;
  *) echo "uso: MOTOR_SHA=<sha> bash tools/despliegue/desplegar.sh [--ensayo]" >&2; exit 2 ;;
esac
. "$(dirname "$0")/comun.sh"

SHA=$(git rev-parse --verify "${MOTOR_SHA:?falta MOTOR_SHA}^{commit}")
FIN=$((SECONDS + ESPERA))

MIGS=$(migraciones_de "$SHA") || exit 1
NUEVO=$(interruptores_de "$SHA") || exit 1
if printf '%s\n' "$NUEVO" | grep -Eq '^ads_producto_pausa_.*=live$'; then
  echo "ABORTA: $INTERRUPTORES no puede poner la pausa en live: solo David la enciende desde la pantalla" >&2
  exit 1
fi

ensayo() {
  local previo g m s k
  echo "== ENSAYO (solo lectura) de $SHA"
  previo=$(srv "cat $DESP/sha 2>/dev/null || true")
  echo "SHA_PREVIO=${previo:-vacio}"
  [ -z "$previo" ] || asegura_commit "$previo"
  g=$(estado_guardas) || { echo "ERROR: no pude leer las guardas" >&2; exit 1; }
  echo "guardas (alias|ciclos running|harvest en cola|harvest jobs|app.cli): $g"
  [ "$g" = "$GUARDAS_LIBRES" ] && echo "guardas: libres" || echo "guardas: ocupadas (desplegar esperaria hasta ${ESPERA}s)"
  if contiene "$previo" "$SHA"; then
    echo "monotonia: $previo ya contiene $SHA; desplegar no cambiaria nada"
    return 0
  fi
  echo "monotonia: hay que desplegar"
  while IFS=$'\t' read -r m s <&3; do
    [ -n "$m" ] || continue
    s=$(sonda "$s") || exit 1
    [ "$s" = t ] && echo "migracion aplicada: $m" || echo "migracion pendiente: $m"
  done 3<<< "$MIGS"
  [ -n "$MIGS" ] || echo "migraciones de la receta (*_auto03_*, *_orbit07_*): ninguna en $SHA"
  VIEJO=$(interruptores_de "$previo") || exit 1
  CAMBIADAS=$(claves_a_converger "$previo" "$VIEJO" "$NUEVO")
  for k in $CAMBIADAS; do
    vig=$(valor_vigente "$k") || exit 1
    echo "clave que cambiaria: $k: vigente '$vig' -> '$(valor "$k" "$NUEVO")'"
  done
  [ -n "$CAMBIADAS" ] || echo "claves: ninguna cambia"
  if git cat-file -e "$SHA:$CRON_VIGIA" 2>/dev/null; then
    cron_vigia revisa 2>&1 || echo "desplegar instalaria (o moveria al bloque) la linea jev-vigia"
  fi
}

if [ "$ENSAYO" = 1 ]; then
  ensayo
  echo "== ENSAYO OK"
  exit 0
fi

echo "== (0) candado y diario"
rc=0
toma_candado || rc=$?
if [ "$rc" = 75 ]; then echo "candado ocupado ${ESPERA}s: no cambie nada"; exit 75; fi
[ "$rc" = 0 ] || { echo "ERROR: no pude tomar el candado" >&2; exit 1; }
L=$(lee_ultimo)
EST=$(ultimo_de ESTADO "$L")
ULT_SHA=$(ultimo_de MOTOR_SHA "$L")
case "$EST" in
  revirtiendo | rollback_incompleto)
    echo "ABORTA: reversa pendiente de $ULT_SHA (ESTADO=$EST); la termina rollback.sh con MOTOR_SHA=$ULT_SHA" >&2
    exit 1 ;;
esac
if tiene_cambios "$L" && [ "$ULT_SHA" != "$SHA" ] && ! es_terminal "$EST"; then
  echo "ABORTA: el diario trae cambios de $ULT_SHA sin estado terminal (ESTADO=$EST)" >&2
  exit 1
fi
if [ "$ULT_SHA" = "$SHA" ] && ! es_terminal "$EST"; then
  echo "reanudo la corrida de $SHA (ESTADO=$EST)"
else
  PREVIO=$(srv "cat $DESP/sha 2>/dev/null || true")
  [[ "$PREVIO" =~ ^([0-9a-f]{40})?$ ]] || { echo "ABORTA: $DESP/sha invalido: $PREVIO" >&2; exit 1; }
  [ -z "$PREVIO" ] || asegura_commit "$PREVIO"
  srv "set -e; if [ -f $ULTIMO ]; then cat $ULTIMO >> $DESP/historial.env; fi; \
    printf 'MOTOR_SHA=%s\nESTADO=guardas\nSHA_PREVIO=%s\n' $SHA '$PREVIO' > $ULTIMO.tmp; mv $ULTIMO.tmp $ULTIMO"
fi
L=$(lee_ultimo)
PREVIO=$(primero SHA_PREVIO "$L")
[ -z "$PREVIO" ] || asegura_commit "$PREVIO"
echo "MOTOR_SHA=$SHA SHA_PREVIO=${PREVIO:-vacio}"

echo "== (1) guardas (plazo comun ${ESPERA}s)"
while :; do
  G=$(estado_guardas) || { echo "ERROR: no pude leer las guardas" >&2; exit 1; }
  [ "$G" = "$GUARDAS_LIBRES" ] && break
  if [ "$SECONDS" -ge "$FIN" ]; then
    echo "guardas ocupadas al agotar el plazo: $G"
    if tiene_cambios "$(lee_ultimo)"; then
      anota ESTADO=guardas_con_cambios
      echo "el diario ya trae cambios de esta corrida y el motor no corre rollback con 75: los deshago aqui"
      ( set +e; revierte ) || true
      if [ "$(ultimo_de ESTADO "$(lee_ultimo)")" != revertido ]; then
        echo "ABORTA: la reversa no termino; salgo 1 para que el motor corra rollback.sh" >&2
        exit 1
      fi
    else
      anota ESTADO=abortado_sin_cambios
      echo "no cambie nada"
    fi
    exit 75
  fi
  echo "guardas ocupadas ($G); reviso en 15 s"
  r=$((FIN - SECONDS))
  [ "$r" -le 15 ] || r=15
  [ "$r" -ge 1 ] || r=1
  sleep "$r"
done
echo "guardas libres"

echo "== (2) monotonia"
if contiene "$PREVIO" "$SHA"; then
  anota ESTADO=sin_cambios
  echo "$PREVIO ya contiene $SHA: no toco codigo, migraciones ni claves"
  exit 0
fi

echo "== (3) respaldo"
if [ -z "$(primero CODIGO "$L")" ]; then
  STAMP=$(date -u +%Y%m%d-%H%M%S)
  P=$DESP/predeploy-$STAMP
  ARCH_RESPALDO="Dockerfile .dockerignore pyproject.toml uv.lock"
  srv "set -e; cd $SRV; rm -rf $DESP/predeploy-*.parcial; mkdir -p $P.parcial; cp -a app $P.parcial/"
  corte 3a
  srv "set -e; cd $SRV; cp -a tools $ARCH_RESPALDO $P.parcial/"
  if [ "$SIM" = 1 ]; then
    pg_dump "$DSN_SIM" --schema-only > "$P.parcial/esquema.sql"
  else
    srv "docker exec orbit-db-1 pg_dump -U orbit -d orbit --schema-only > $P.parcial/esquema.sql"
  fi
  srv "set -e; cd $SRV; test -s $P.parcial/esquema.sql; \
    grep -q 'PostgreSQL database dump complete' $P.parcial/esquema.sql; \
    find app tools $ARCH_RESPALDO -type f -exec md5sum {} + | LC_ALL=C sort > $P.parcial/md5.txt; \
    (cd $P.parcial && md5sum -c --quiet md5.txt); mv $P.parcial $P"
  anota "CODIGO=predeploy-$STAMP"
  echo "respaldo $P completo"
else
  echo "respaldo ya hecho: $(primero CODIGO "$L")"
fi
corte 3

echo "== (4) migraciones de la receta (*_auto03_*, *_orbit07_*)"
sigue_candado
while IFS=$'\t' read -r m s <&3; do
  [ -n "$m" ] || continue
  st=$(sonda "$s") || exit 1
  if [ "$st" = t ]; then echo "ya aplicada: $m"; continue; fi
  anota "MIGRACION=$m"
  git show "$SHA:migrations/$m" | w
  st=$(sonda "$s") || exit 1
  [ "$st" = t ] || { echo "ABORTA: $m aplicada pero su sonda no da t" >&2; exit 1; }
  echo "aplicada: $m"
done 3<<< "$MIGS"

echo "== (5) codigo de $SHA"
sigue_candado
TOOLS=$(git show "$SHA:Dockerfile" | tr -d '\r' | sed -n 's#^COPY \(tools/[^ ]*\) .*#\1#p')
ARCHIVOS="app Dockerfile .dockerignore pyproject.toml uv.lock $TOOLS"
TMPC=$(mktemp -d "${TMPDIR:-/tmp}/auto03-codigo.XXXXXX")
trap 'rm -rf "$TMPC"' EXIT
git archive --format=tar "$SHA" $ARCHIVOS | tar -xf - -C "$TMPC"
MD5=$(cd "$TMPC" && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 md5sum | sed 's/ \*/  /')
N=$DESP/codigo.nuevo
sigue_candado
git archive --format=tar "$SHA" $ARCHIVOS | srv "set -e; cd $SRV; rm -rf $N; mkdir -p $N; tar -xf - -C $N; \
  rm -rf app; mv $N/app app; cp -f $N/Dockerfile $N/.dockerignore $N/pyproject.toml $N/uv.lock .; \
  if [ -d $N/tools ]; then mkdir -p tools; cp -f $N/tools/* tools/; fi; rm -rf $N"
printf '%s\n' "$MD5" | srv "cd $SRV && md5sum -c --quiet -" \
  || { echo "ABORTA: el md5 del codigo en el servidor no coincide con $SHA" >&2; exit 1; }
echo "md5 OK: $(printf '%s\n' "$MD5" | wc -l) archivos"
compose_up
espera_salud || { echo "ABORTA: /health no da 200 tras el build" >&2; exit 1; }
sigue_candado
srv "printf '%s\n' $SHA > $DESP/sha"
echo "/health 200; $DESP/sha = $SHA"
corte 5

echo "== (5b) linea de cron del vigia de Jev"
if ! git cat-file -e "$SHA:$CRON_VIGIA" 2>/dev/null; then
  echo "$SHA no trae $CRON_VIGIA"
elif cron_vigia revisa; then
  echo "la linea ya estaba"
else
  # CRON= solo con la ausencia comprobada (presente sale 10): la reversa quita lo que agrego esta
  # corrida, no una linea previa que el instalador del bloque de docs/DEPLOY.md dejo fuera y aqui
  # se mueve. Si no se pudo leer el crontab, no se anota ni se instala.
  sigue_candado
  rc=0
  cron_vigia presente || rc=$?
  case "$rc" in
    0) ;;
    10) printf '%s\n' "$(lee_ultimo)" | grep -qx 'CRON=jev-vigia' || anota CRON=jev-vigia ;;
    *) echo "ABORTA: no pude leer si el crontab de gon ya trae la linea jev-vigia (salida $rc)" >&2; exit 1 ;;
  esac
  cron_vigia instala
fi

echo "== (6) interruptores frente a SHA_PREVIO"
sigue_candado
VIEJO=$(interruptores_de "$PREVIO") || exit 1
CAMBIADAS=$(claves_a_converger "$PREVIO" "$VIEJO" "$NUEVO")
for plat in $(for k in $CAMBIADAS; do
  [ "$(valor "$k" "$NUEVO")" = live ] && printf '%s\n' "${k#ads_producto_*_}"
done | LC_ALL=C sort -u); do
  echo "verificacion previa a live en $plat"
  srv "docker exec orbit-app-1 sh -c 'python -m tools.auto03_verifica evalua --platform $plat --json /tmp/auto03-$plat.json && python -m tools.auto03_verifica replay --json /tmp/auto03-$plat.json'" \
    || { echo "ABORTA: la verificacion previa a live de $plat fallo" >&2; exit 1; }
done
YA=$(printf '%s\n' "$(lee_ultimo)" | sed -n 's/^CLAVE=\([^:]*\):.*/\1/p')
PRIMERA=1
for k in $CAMBIADAS; do
  v=$(valor "$k" "$NUEVO")
  if ! printf '%s\n' "$YA" | grep -qx "$k"; then
    vig=$(valor_vigente "$k") || exit 1
    anota "CLAVE=$k:$vig"
  fi
  aplica_clave "$k" "$v"
  echo "clave $k = '${v:-off}'"
  if [ "$PRIMERA" = 1 ]; then PRIMERA=0; corte 6; fi
done
[ -n "$CAMBIADAS" ] || echo "ninguna clave cambia"

anota ESTADO=desplegado
echo "== DESPLEGADO $SHA (checklist.sh lo pasa a verificado)"
