# Comun a desplegar.sh, checklist.sh y rollback.sh (AUTO-03 B.1/B.2). Se carga con `.`.
# Contrato y diario (`ultimo.env`): docs/DEPLOY.md, seccion "Despliegue por el motor".
# Modelo de referencia: docs/evidencia/auto-03/plan/modelo-despliegue/modelo.py.
#
# ORBIT_SIMULACION=1: lo de datos corre contra ORBIT_SIM_DSN, el servidor es el
# directorio ORBIT_SIM_SRV y `ssh`/`docker`/`curl` son los que haya en el PATH
# (la prueba pone dobles). ORBIT_SIM_CORTE=<paso> mata la corrida en ese paso.

REPO=$(git rev-parse --show-toplevel)
cd "$REPO"
SIM=${ORBIT_SIMULACION:-0}
if [ "$SIM" = 1 ]; then
  SRV=${ORBIT_SIM_SRV:?en simulacion falta ORBIT_SIM_SRV}
  DSN_SIM=${ORBIT_SIM_DSN:?en simulacion falta ORBIT_SIM_DSN}
else
  SRV=/mnt/data/appdata/orbit
fi
DESP=$SRV/despliegue
ULTIMO=$DESP/ultimo.env
ESPERA=${AUTO03_ESPERA_S:-600}
# ARREGLOS 01 M.2: la reversa no puede quedar bloqueada por un ciclo; espera y recrea de todos modos.
ESPERA_REVERSA=${AUTO03_ESPERA_REVERSA_S:-300}
INTERRUPTORES=tools/despliegue/interruptores.env
TERMINALES=" verificado sin_cambios abortado_sin_cambios revertido "
CAMBIO_RE='^(CODIGO|MIGRACION|CLAVE|CRON)='
CRON_VIGIA=tools/despliegue/cron_jev_vigia.sh
# CLARIDAD 01 F: el cron de root corre $SRV/refresh_estimacion.sh; desplegar lo instala desde tools/.
REFRESCO=refresh_estimacion.sh

srv() { ssh goncloud "$@"; }

q() {
  if [ "$SIM" = 1 ]; then
    psql "$DSN_SIM" -X -q -tA -v ON_ERROR_STOP=1
  else
    srv 'DSN=$(docker exec orbit-app-1 printenv ORBIT_DSN_READ); docker exec -i orbit-db-1 psql "$DSN" -X -q -tA -v ON_ERROR_STOP=1'
  fi
}

# w: una migracion por stdin en UNA transaccion. psql ignora -1 sin -f, de ahi `-f -`.
w() {
  sigue_candado
  if [ "$SIM" = 1 ]; then
    psql "$DSN_SIM" -X -q -v ON_ERROR_STOP=1 -1 -f -
  else
    srv 'docker exec -i orbit-db-1 psql -U orbit -d orbit -X -q -v ON_ERROR_STOP=1 -1 -f -'
  fi
}

# recrea_contenedor [--reversa]: UNICA forma de recrear orbit-app-1 (paso 5 y revierte).
# Construye la imagen aparte, sin tocar el contenedor vivo, y en UN solo comando remoto espera a
# que `docker top` no tenga procesos app.cli (plazo $ESPERA) y recrea sin build: la ultima mirada
# y el `up` corren en el mismo shell. Sale 0 recreado, 75 plazo agotado con procesos vivos (no
# recreo) y cualquier otra cosa es error. Con --reversa espera $ESPERA_REVERSA y recrea de todos
# modos con una linea AVISO: una reversa no puede quedar bloqueada por un ciclo.
# Idempotente: otra corrida reconstruye (cache) y recrea la misma imagen.
recrea_contenedor() {
  local modo=adelante espera=$ESPERA rc=0
  case "${1:-}" in
    "") ;;
    --reversa) modo=reversa; espera=$ESPERA_REVERSA ;;
    *) echo "recrea_contenedor: argumento invalido: $1" >&2; return 2 ;;
  esac
  sigue_candado
  srv "docker compose -p orbit --project-directory $SRV -f $SRV/docker-compose.yml build app" \
    || return 1
  echo "imagen construida (el contenedor vivo no se toco)"
  echo "espero a que orbit-app-1 no tenga procesos app.cli (plazo ${espera}s)"
  sigue_candado
  srv "MODO_R=$modo ESPERA_R=$espera SRV_R=$SRV bash -s" <<'REMOTO' || rc=$?
set -u
fin=$(( $(date +%s) + ESPERA_R ))
while :; do
  n=$(docker top orbit-app-1 2>/dev/null | grep -c 'app\.cli' || true)
  [ "$n" -eq 0 ] && break
  if [ "$(date +%s)" -ge "$fin" ]; then
    if [ "$MODO_R" = reversa ]; then
      echo "AVISO: procesos app.cli vivos ($n) al recrear; la reversa recrea de todos modos" >&2
      break
    fi
    echo "procesos app.cli vivos ($n) al agotar ${ESPERA_R}s" >&2
    exit 75
  fi
  echo "procesos app.cli vivos ($n); reviso en 5 s"
  sleep 5
done
docker compose -p orbit --project-directory "$SRV_R" -f "$SRV_R/docker-compose.yml" \
  up -d --no-deps --no-build app
REMOTO
  [ "$rc" = 0 ] || return "$rc"
}

corte() {
  if [ "$SIM" = 1 ] && [ "${ORBIT_SIM_CORTE:-}" = "$1" ]; then
    echo "SIMULACION: corte en $1" >&2
    kill -KILL $$
  fi
}

lee_ultimo() { srv "cat $ULTIMO 2>/dev/null || true"; }
primero() { printf '%s\n' "$2" | sed -n "s/^$1=//p" | head -n 1; }
ultimo_de() { printf '%s\n' "$2" | sed -n "s/^$1=//p" | tail -n 1; }
tiene_cambios() { printf '%s\n' "$1" | grep -Eq "$CAMBIO_RE"; }
es_terminal() { case "$TERMINALES" in *" $1 "*) return 0 ;; esac; return 1; }

anota() {
  sigue_candado
  [[ "$1" =~ ^[A-Z_]+=[A-Za-z0-9_.:/-]*$ ]] || { echo "ABORTA: linea de diario invalida: $1" >&2; exit 1; }
  srv "printf '%s\n' '$1' >> $ULTIMO"
}

contiene() { [ -n "$1" ] && git merge-base --is-ancestor "$2" "$1" 2>/dev/null; }

asegura_commit() {
  git cat-file -e "$1^{commit}" 2>/dev/null && return 0
  git fetch -q origin 2>/dev/null || true
  git cat-file -e "$1^{commit}" 2>/dev/null || { echo "ABORTA: $1 no es un commit conocido" >&2; return 1; }
}

# interruptores_de <sha>: lineas `clave=valor` ordenadas; `off` se normaliza a vacio.
interruptores_de() {
  local txt l k v
  [ -n "$1" ] || return 0
  txt=$(git show "$1:$INTERRUPTORES" 2>/dev/null | tr -d '\r') || txt=""
  while IFS= read -r l; do
    case "$l" in '' | '#'*) continue ;; esac
    k=${l%%=*}
    v=${l#*=}
    [[ "$k" =~ ^ads_producto_(meta|pausa)_amazon_(mx|us)$ ]] \
      || { echo "ABORTA: clave desconocida en $INTERRUPTORES de $1: $k" >&2; return 1; }
    case "$v" in
      shadow | live) ;;
      off) v= ;;
      *) echo "ABORTA: valor invalido en $INTERRUPTORES de $1: $l" >&2; return 1 ;;
    esac
    printf '%s=%s\n' "$k" "$v"
  done <<< "$txt" | LC_ALL=C sort
}

# claves_a_converger <sha_previo> <viejo> <nuevo>: con SHA_PREVIO vacio cuentan todas las del
# archivo nuevo, tambien las que dicen off.
claves_a_converger() {
  if [ -z "$1" ]; then
    printf '%s\n' "$3" | sed -n 's/=.*//p'
  else
    claves_cambiadas "$2" "$3"
  fi
}

valor() { printf '%s\n' "$2" | sed -n "s/^$1=//p" | head -n 1; }

claves_cambiadas() {
  local k
  for k in $( (printf '%s\n' "$1"; printf '%s\n' "$2") | sed -n 's/=.*//p' | LC_ALL=C sort -u); do
    [ "$(valor "$k" "$1")" = "$(valor "$k" "$2")" ] || echo "$k"
  done
}

valor_vigente() {
  local v
  v=$(echo "SELECT coalesce(settings ->> '$1', '') FROM config_version ORDER BY id DESC LIMIT 1;" | q) || return 1
  [ "$v" = off ] && v=
  printf '%s' "$v"
}

# aplica_clave <clave> <valor>: el unico escritor es config_write.guarda_config via el CLI.
aplica_clave() {
  sigue_candado
  local resto=${1#ads_producto_}
  local tipo=${resto%%_*}
  local plat=${resto#*_}
  srv "docker exec orbit-app-1 python -m app.cli productos-modo --platform $plat --$tipo ${2:-off}"
}

# migraciones_de <sha>: `archivo<TAB>sonda` de las migraciones de la receta
# (*_auto03_* de AUTO-03, *_orbit07_* de ORBIT 07, *_claridad01_* de CLARIDAD 01 y
# *_arreglos01_* de ARREGLOS 01) sin reversa,
# en orden.
migraciones_de() {
  local f s r
  for f in $(git ls-tree --name-only "$1" migrations/ | sed 's#^migrations/##' \
    | grep -E '_(auto03|orbit07|claridad01|arreglos01)_' | grep -v '_reversa_' | LC_ALL=C sort); do
    if git show "$1:migrations/$f" | tr -d '\r' \
      | grep -Eiq '^[[:space:]]*(BEGIN|START[[:space:]]+TRANSACTION|COMMIT|ROLLBACK)[[:space:]]*(TRANSACTION|WORK)?[[:space:]]*;'; then
      echo "ABORTA: migrations/$f trae control de transaccion propio; desplegar la envuelve en una sola" >&2
      return 1
    fi
    s=$(git show "$1:migrations/$f" | head -n 10 | tr -d '\r' | sed -n 's/^-- sonda-aplicada: *//p' | head -n 1)
    [ -n "$s" ] || { echo "ABORTA: migrations/$f no trae la cabecera '-- sonda-aplicada:'" >&2; return 1; }
    r=$(reversa_de "$f")
    git cat-file -e "$1:migrations/$r" 2>/dev/null || { echo "ABORTA: migrations/$f no tiene su reversa $r" >&2; return 1; }
    printf '%s\t%s\n' "$f" "${s%;}"
  done
}

reversa_de() { printf '%s_reversa_%s' "${1%%_*}" "${1#*_}"; }

# sonda <select>: imprime t o f; otra salida (vacia incluida) es error.
sonda() {
  local r
  r=$(echo "$1;" | q) || { echo "ERROR: la sonda fallo: $1" >&2; return 1; }
  case "$r" in
    t | f) printf '%s' "$r" ;;
    *) echo "ERROR: la sonda no dio t/f (salida '${r}'): $1" >&2; return 1 ;;
  esac
}

# cron_vigia <instala|quita|revisa|presente>: el instalador de la linea jev-vigia de $SHA, corrido en goncloud.
cron_vigia() { git show "$SHA:$CRON_VIGIA" | srv "ORBIT_CRON_ARCHIVO=$SRV/archive bash -s -- $1"; }

salud() { srv "curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:8010/health" 2>/dev/null || true; }

espera_salud() {
  local i
  for i in $(seq 1 30); do
    [ "$(salud)" = 200 ] && return 0
    sleep 2
  done
  return 1
}

# estado_guardas: `alias|ciclos_running|harvest_cola|harvest_jobs|app_cli`; libre = true|0|0|0|0.
estado_guardas() {
  local db top cli
  db=$(q <<'SQL'
SELECT (to_regclass('public.meli_sku_alias') IS NOT NULL)::text
  || '|' || (SELECT count(*) FROM optimizer_cycle WHERE status = 'running')
  || '|' || (SELECT count(*) FROM apply_queue WHERE kind = 'harvest'
             AND estado NOT IN ('applied', 'failed', 'vetoed', 'discarded'))
  || '|' || (SELECT count(*) FROM harvest_job WHERE fase NOT IN ('done', 'failed'));
SQL
) || return 1
  top=$(srv "docker top orbit-app-1") || return 1
  cli=$(printf '%s\n' "$top" | grep -c 'app\.cli' || true)
  printf '%s|%s' "$db" "$cli"
}
GUARDAS_LIBRES="true|0|0|0|0"

# toma_candado: flock en goncloud sostenido por una sesion ssh viva toda la corrida; al
# salir este proceso se cierra su stdin y el candado se suelta. 0 tomado, 75 plazo, 1 error.
toma_candado() {
  local out pid fin
  out=$(mktemp "${TMPDIR:-/tmp}/auto03-candado.XXXXXX")
  exec {CANDADO_FD}> >(exec ssh goncloud "mkdir -p $DESP && flock -w $ESPERA $DESP/.lock sh -c 'echo CANDADO; exec cat >/dev/null' || echo SIN_CANDADO" > "$out" 2>&1)
  pid=$!
  CANDADO_PID=$pid
  fin=$((SECONDS + ESPERA + 30))
  until grep -q '^CANDADO$' "$out"; do
    if grep -q '^SIN_CANDADO$' "$out" || [ "$SECONDS" -ge "$fin" ]; then
      rm -f "$out"
      return 75
    fi
    if ! kill -0 "$pid" 2>/dev/null && ! grep -q '^CANDADO$' "$out"; then
      grep -q '^SIN_CANDADO$' "$out" && { rm -f "$out"; return 75; }
      cat "$out" >&2
      rm -f "$out"
      return 1
    fi
    sleep 1
  done
  rm -f "$out"
}

# sigue_candado: la sesion que sostiene el candado sigue viva; sin ella ninguna corrida cambia nada.
sigue_candado() {
  [ -n "${CANDADO_PID:-}" ] && kill -0 "$CANDADO_PID" 2>/dev/null && return 0
  echo "ABORTA: se perdio la sesion ssh que sostiene el candado" >&2
  exit 1
}

# revierte: la reversa de B.2 bajo el candado ya tomado; la usan rollback.sh y desplegar.sh
# (salida 75 con cambios, porque el motor no corre rollback con 75). Deshace solo lo anotado
# en ultimo.env por $SHA, en orden: claves, cron, codigo, sha y reversas de las migraciones
# (inverso), siguiendo aunque un paso falle. Antes de cada cambio comprueba que sigue el
# candado: sin el sale 1 dejando ESTADO=revirtiendo, y otra corrida continua la reversa.
# Sale 0 si no habia nada que deshacer, 1 con ESTADO=revertido y 2 con rollback_incompleto.
revierte() {
  local L EST PEND VISTAS linea k previo vig CODIGO P PREVIO m s st r
  L=$(lee_ultimo) || { echo "ERROR: no pude leer $ULTIMO" >&2; exit 1; }
  EST=$(ultimo_de ESTADO "$L")
  if [ -z "$L" ] || [ "$(ultimo_de MOTOR_SHA "$L")" != "$SHA" ] \
    || [ "$EST" = abortado_sin_cambios ] || [ "$EST" = revertido ] || ! tiene_cambios "$L"; then
    echo "nada que deshacer para $SHA (diario: MOTOR_SHA=$(ultimo_de MOTOR_SHA "$L") ESTADO=${EST:-sin diario})"
    exit 0
  fi
  [ "$EST" = revirtiendo ] || anota ESTADO=revirtiendo || exit 1
  PEND=""

  echo "== claves"
  VISTAS=""
  while IFS= read -r linea <&3; do
    [ -n "$linea" ] || continue
    k=${linea%%:*}
    previo=${linea#*:}
    case " $VISTAS " in *" $k "*) continue ;; esac
    VISTAS="$VISTAS $k"
    if ! vig=$(valor_vigente "$k"); then PEND="$PEND clave:$k"; continue; fi
    if [ "$vig" != "$previo" ]; then
      sigue_candado
      if aplica_clave "$k" "$previo"; then echo "clave $k: '${vig:-off}' -> '${previo:-off}'"; else PEND="$PEND clave:$k"; fi
    else
      echo "clave $k ya en '${previo:-off}'"
    fi
    corte rb
  done 3<<< "$(printf '%s\n' "$L" | sed -n 's/^CLAVE=//p')"

  echo "== cron"
  if printf '%s\n' "$L" | grep -qx 'CRON=jev-vigia'; then
    sigue_candado
    cron_vigia quita || PEND="$PEND cron:jev-vigia"
  fi

  echo "== codigo"
  CODIGO=$(primero CODIGO "$L")
  if [ -n "$CODIGO" ]; then
    P=$DESP/$CODIGO
    sigue_candado
    if srv "set -e; cd $SRV; test -d $P/app; test -d $P/tools; rm -rf app tools; cp -a $P/app $P/tools .; \
        cp -a $P/Dockerfile $P/.dockerignore $P/pyproject.toml $P/uv.lock .; \
        if [ -f $P/$REFRESCO ]; then install -m 755 $P/$REFRESCO $REFRESCO; \
        elif [ -f $P/$REFRESCO.ausente ]; then rm -f $REFRESCO; fi" \
      && recrea_contenedor --reversa && espera_salud; then
      echo "codigo restaurado de $CODIGO; /health 200"
    else
      PEND="$PEND codigo:$CODIGO"
    fi
  fi

  echo "== sha"
  PREVIO=$(primero SHA_PREVIO "$L")
  sigue_candado
  if [ -n "$PREVIO" ]; then
    srv "printf '%s\n' $PREVIO > $DESP/sha" && echo "$DESP/sha = $PREVIO" || PEND="$PEND sha"
  else
    srv "rm -f $DESP/sha" && echo "$DESP/sha borrado (no existia antes)" || PEND="$PEND sha"
  fi

  echo "== migraciones (orden inverso)"
  for m in $(printf '%s\n' "$L" | sed -n 's/^MIGRACION=//p' | awk '!v[$0]++' | tac); do
    s=$(git show "$SHA:migrations/$m" | head -n 10 | tr -d '\r' | sed -n 's/^-- sonda-aplicada: *//p' | head -n 1)
    if ! st=$(sonda "${s%;}"); then PEND="$PEND reversa:$m"; continue; fi
    if [ "$st" = f ]; then echo "$m no esta aplicada"; continue; fi
    r=$(reversa_de "$m")
    sigue_candado
    if git show "$SHA:migrations/$r" | w && st=$(sonda "${s%;}") && [ "$st" = f ]; then
      echo "reversa aplicada: $r"
    else
      PEND="$PEND reversa:$m"
    fi
  done

  if [ -z "$PEND" ]; then
    anota ESTADO=revertido
    echo "== REVERTIDO $SHA"
    exit 1
  fi
  anota ESTADO=rollback_incompleto
  anota "PENDIENTE=$(echo $PEND | tr ' ' '/')"
  echo "== ROLLBACK INCOMPLETO de $SHA; pendiente:$PEND (ver docs/DEPLOY.md)"
  exit 2
}
