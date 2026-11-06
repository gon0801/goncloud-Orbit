#!/usr/bin/env bash
# Despliegue AUTO-03 por el motor (B.1). Uso:
#   MOTOR_SHA=<sha> bash tools/despliegue/desplegar.sh [--ensayo]
# Sin otros argumentos. Lee MOTOR_SHA (un commit con CI verde y con CI en el
# propio SHA) y converge el server hacia ese SHA: migraciones *_auto03_* con
# cabecera de sonda pendientes, codigo del SHA y claves de
# tools/despliegue/interruptores.env. $SRV es /mnt/data/appdata/orbit.
#
# Journal $SRV/despliegue/ultimo.env: cada paso que cambia algo anota su linea
# ANTES de hacerlo (MIGRACION=, CLAVE=<clave>:<valor_previo>); CODIGO=
# predeploy-<stamp> se anota solo cuando el respaldo (3) quedo completo.
# ESTADO: guardas -> desplegado -> verificado (solo checklist.sh). Terminales:
# verificado, sin_cambios, abortado_sin_cambios, revertido. Sale 75 en guardas
# o candado agotados; 1 si hay una reversa pendiente o al fallar un paso.
#
# --ensayo: no toma el candado ni escribe nada; corre contra produccion en
# solo lectura (guardas informativas, monotonia, migraciones pendientes con su
# sonda y claves que cambiaria). Sale distinto de cero si falla ssh o psql o
# si una sonda sale vacia.
# ORBIT_SIMULACION=1: lo de datos corre contra ORBIT_SIM_DSN y el server es
# $SRV local (ORBIT_SRV), con el docker y el ssh que haya en el PATH.
set -euo pipefail

ENSAYO=0
case "${1:-}" in
  "") ;;
  --ensayo) ENSAYO=1 ;;
  *)
    echo "uso: MOTOR_SHA=<sha> bash tools/despliegue/desplegar.sh [--ensayo]" >&2
    exit 2
    ;;
esac
: "${MOTOR_SHA:?uso: MOTOR_SHA=<sha> bash tools/despliegue/desplegar.sh [--ensayo]}"

SIM=${ORBIT_SIMULACION:-0}
SRV=${ORBIT_SRV:-/mnt/data/appdata/orbit}
DSN_SIM=${ORBIT_SIM_DSN:-postgresql://orbit:orbit@127.0.0.1:5433/orbit_sim_auto03}
AUTO03_ESPERA_S=${AUTO03_ESPERA_S:-600}
REPO=$(git rev-parse --show-toplevel)
TMP=$(mktemp -d "${TMPDIR:-/tmp}/auto03-desplegar.XXXXXX")
# shellcheck source=lib.sh
source "$REPO/tools/despliegue/lib.sh"
trap 'suelta_candado; rm -rf "$TMP"' EXIT
cd "$REPO"

MOTOR_SHA=$(git rev-parse --verify "${MOTOR_SHA}^{commit}") ||
  aborta "MOTOR_SHA no es un commit de este repo"
git cat-file -e "$MOTOR_SHA:tools/despliegue/interruptores.env" ||
  aborta "$MOTOR_SHA no trae tools/despliegue/interruptores.env"
FECHA_LIMITE=$(( $(date +%s) + AUTO03_ESPERA_S ))

# ---------------------------------------------------------------------------
# Guardas (1): una sola pasada informativa con --ensayo; esperan y reintentan
# cada 15 s hasta AUTO03_ESPERA_S en un despliegue real.
# ---------------------------------------------------------------------------
guardas_ok() {
  local meli ciclos top cli h1 h2
  meli=$(echo "SELECT to_regclass('public.meli_sku_alias') IS NOT NULL;" | q) ||
    return 1
  [ "$meli" = t ] || { echo "guarda: falta meli_sku_alias (0075)"; return 1; }
  ciclos=$(echo "SELECT count(*) FROM optimizer_cycle WHERE status = 'running';" | q) ||
    return 1
  [ "$ciclos" = 0 ] || { echo "guarda: hay $ciclos ciclo(s) running"; return 1; }
  top=$(ssh goncloud "docker top orbit-app-1") || { echo "guarda: docker top no contesta"; return 1; }
  cli=$(printf '%s\n' "$top" | grep -c 'app\.cli' || true)
  [ "$cli" = 0 ] || { echo "guarda: hay app.cli en orbit-app-1"; return 1; }
  h1=$(echo "SELECT count(*) FROM apply_queue WHERE kind = 'harvest' AND estado NOT IN ('applied','failed','vetoed','discarded');" | q) ||
    return 1
  h2=$(echo "SELECT count(*) FROM harvest_job WHERE fase IN ('pending','negative_created','exact_created','hermanas_negadas');" | q) ||
    return 1
  [ "$h1" = 0 ] && [ "$h2" = 0 ] ||
    { echo "guarda: harvest en vuelo (cola=$h1 jobs=$h2)"; return 1; }
  return 0
}

# ---------------------------------------------------------------------------
# Migraciones *_auto03_* del SHA: validacion upfront de cabeceras y lista de
# pendientes (sonda f). (4) aplica cada una en psql -1.
# ---------------------------------------------------------------------------
migraciones_del_sha() {
  git ls-tree --name-only "$MOTOR_SHA" migrations/ |
    grep '_auto03_' | grep -v '_reversa_' | sort || true
}
cabecera_de() { # cabecera_de <ruta> -> el SELECT booleano de la cabecera
  git show "$MOTOR_SHA:$1" | sed -n 's/^-- sonda-aplicada: //p' | head -1
}
valida_migraciones() {
  local m cab
  while IFS= read -r m; do
    [ -n "$m" ] || continue
    cab=$(cabecera_de "$m")
    [ -n "$cab" ] || aborta "la migracion $m no trae cabecera '-- sonda-aplicada'"
  done < <(migraciones_del_sha)
}
pendientes() {
  local m cab estado
  while IFS= read -r m; do
    [ -n "$m" ] || continue
    cab=$(cabecera_de "$m")
    estado=$(echo "$cab" | q) || aborta "la sonda de $m fallo"
    if [ -z "$estado" ]; then
      aborta "la sonda de $m salio vacia"
    elif [ "$estado" = t ]; then
      echo "ya aplicada: $m" >&2
    else
      printf '%s\n' "$m"
    fi
  done < <(migraciones_del_sha)
}

# ---------------------------------------------------------------------------
# (3) Respaldo predeploy-<stamp>: dump del esquema + copia de app/, tools/,
# Dockerfile, .dockerignore, pyproject.toml y uv.lock. Se escribe en .parcial,
# se comprueba (dump no vacio y md5 identico al original) y se renombra;
# recien entonces anota CODIGO=. Si la corrida muere a media copia no queda
# CODIGO= y la reanudacion hace un respaldo completo nuevo.
# ---------------------------------------------------------------------------
respalda() {
  local stamp=$1 parcial
  parcial="despliegue/predeploy-$stamp.parcial"
  echo "== 3) respaldo predeploy-$stamp"
  if [ "$SIM" = 1 ]; then
    # Un respaldo a medias de una corrida anterior nunca se completa: fuera.
    rm -rf "$SRV/despliegue"/predeploy-*.parcial 2>/dev/null || true
    rm -rf "$SRV/$parcial"
    mkdir "$SRV/$parcial"
    pg_dump "$DSN_SIM" --schema-only > "$SRV/$parcial/esquema.sql"
  else
    ssh goncloud bash -s <<REMOTE
set -e
cd '$SRV'
rm -rf despliegue/predeploy-*.parcial
rm -rf '$parcial'
mkdir '$parcial'
docker exec orbit-db-1 pg_dump -U orbit -d orbit --schema-only > '$parcial/esquema.sql'
REMOTE
  fi
  if [ "$SIM" = 1 ]; then
    [ -s "$SRV/$parcial/esquema.sql" ] || aborta "el dump del esquema salio vacio"
    tail -5 "$SRV/$parcial/esquema.sql" | grep -q 'PostgreSQL database dump complete' ||
      aborta "el dump del esquema quedo incompleto"
    # Copia en dos pasos: una muerte entre ambos deja la copia a medias
    # (app/ dentro, archivos raiz fuera) que la prueba B.5 l exige ver.
    cp -a "$SRV/app" "$SRV/tools" "$SRV/$parcial/"
    cp -a "$SRV/Dockerfile" "$SRV/.dockerignore" \
      "$SRV/pyproject.toml" "$SRV/uv.lock" "$SRV/$parcial/"
    (cd "$SRV" && find app tools -type f | sort | xargs -r md5sum) > "$TMP/src.md5"
    (cd "$SRV/$parcial" && find app tools -type f | sort | xargs -r md5sum) > "$TMP/resp.md5"
    diff "$TMP/src.md5" "$TMP/resp.md5" >/dev/null ||
      aborta "el respaldo difiere del original (md5 de app/tools)"
    (cd "$SRV" && md5sum Dockerfile .dockerignore pyproject.toml uv.lock) > "$TMP/src2.md5"
    (cd "$SRV/$parcial" && md5sum Dockerfile .dockerignore pyproject.toml uv.lock) > "$TMP/resp2.md5"
    diff "$TMP/src2.md5" "$TMP/resp2.md5" >/dev/null ||
      aborta "el respaldo difiere del original (md5 de los archivos raiz)"
    mv "$SRV/$parcial" "$SRV/despliegue/predeploy-$stamp"
  else
    ssh goncloud bash -s <<REMOTE
set -e
cd '$SRV'
[ -s '$parcial/esquema.sql' ] || { echo 'dump vacio'; exit 1; }
tail -5 '$parcial/esquema.sql' | grep -q 'PostgreSQL database dump complete' || { echo 'dump incompleto'; exit 1; }
cp -a app tools '$parcial/'
cp -a Dockerfile .dockerignore pyproject.toml uv.lock '$parcial/'
( find app tools -type f | sort | xargs -r md5sum ) > /tmp/.auto03-src.$stamp.md5
( cd '$parcial' && find app tools -type f | sort | xargs -r md5sum ) > /tmp/.auto03-resp.$stamp.md5
diff /tmp/.auto03-src.$stamp.md5 /tmp/.auto03-resp.$stamp.md5 || { echo 'respaldo difiere (app/tools)'; exit 1; }
md5sum Dockerfile .dockerignore pyproject.toml uv.lock > /tmp/.auto03-src2.$stamp.md5
( cd '$parcial' && md5sum Dockerfile .dockerignore pyproject.toml uv.lock ) > /tmp/.auto03-resp2.$stamp.md5
diff /tmp/.auto03-src2.$stamp.md5 /tmp/.auto03-resp2.$stamp.md5 || { echo 'respaldo difiere (raiz)'; exit 1; }
rm -f /tmp/.auto03-*.md5
mv '$parcial' 'despliegue/predeploy-$stamp'
REMOTE
  fi
  anota_ultimo "CODIGO=predeploy-$stamp"
}

# ---------------------------------------------------------------------------
# (5) Codigo del SHA: git archive de app, raiz y los COPY tools/ del
# Dockerfile del propio SHA; md5 contra el SHA; compose up; /health; sha.
# ---------------------------------------------------------------------------
archivos_del_sha() {
  local lista="app Dockerfile .dockerignore pyproject.toml uv.lock" t
  while IFS= read -r t; do
    lista="$lista $t"
  done < <(git show "$MOTOR_SHA:Dockerfile" | awk '$1 == "COPY" && $2 ~ /^tools\// { print $2 }')
  printf '%s\n' "$lista"
}
despliega_codigo() {
  local lista h_ref h_srv
  lista=$(archivos_del_sha)
  # shellcheck disable=SC2086
  git archive --format=tar "$MOTOR_SHA" $lista | tar -x -C "$TMP/ref"
  # shellcheck disable=SC2086
  h_ref=$(huella_arbol "$TMP/ref" $lista)
  echo "== 5) codigo $MOTOR_SHA (md5 contra el SHA)"
  if [ "$SIM" = 1 ]; then
    rm -rf "$SRV/app"
    # shellcheck disable=SC2086
    git archive --format=tar "$MOTOR_SHA" $lista | tar -x -C "$SRV"
    # shellcheck disable=SC2086
    h_srv=$(huella_arbol "$SRV" $lista)
  else
    # shellcheck disable=SC2086
    git archive --format=tar "$MOTOR_SHA" $lista |
      ssh goncloud "cd '$SRV' && rm -rf app && tar -xf -"
    h_srv=$(huella_remota $lista)
  fi
  [ "$h_ref" = "$h_srv" ] || aborta "el codigo desplegado difiere del SHA (md5)"
  echo "md5 OK contra $MOTOR_SHA"
  corre_en_contenedor compose up -d --no-deps --build app ||
    aborta "docker compose up fallo"
  if [ "$SIM" = 1 ]; then
    echo "SIMULACION: /health no se mide (no hay contenedor que conteste)"
  else
    codigo=$(ssh goncloud "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8010/health") ||
      aborta "no se pudo medir /health tras el rebuild"
    [ "$codigo" = 200 ] || aborta "/health dio $codigo, no 200"
    echo "health OK"
  fi
  escribe_sha_srv "$MOTOR_SHA"
}

# ---------------------------------------------------------------------------
# (6) Convergencia de claves frente a SHA_PREVIO, nunca frente al archivo que
# (5) reescribe. Recorre la UNION de claves (las que cambian de valor Y las
# que desaparecen del archivo nuevo: vaciarlo es el apagado documentado). Con
# SHA_PREVIO vacio todas las claves cuentan como cambiadas. El diario guarda
# el VALOR VIGENTE en config_version (no el del archivo anterior): es el
# estado que el rollback debe restaurar.
# ---------------------------------------------------------------------------
converge_claves() {
  local previo=$1 l ya a b clave valor previo_vigente cmd plat
  l=$(lee_ultimo)
  ya=$(printf '%s\n' "$l" | sed -n 's/^CLAVE=//p' | cut -d: -f1 | sort -u || true)
  a=$(claves_de_sha "$previo")
  b=$(claves_de_sha "$MOTOR_SHA")
  echo "== 6) claves de interruptores.env frente a SHA_PREVIO=${previo:-<vacio>}"
  while IFS= read -r clave <&3; do
    [ -n "$clave" ] || continue
    valor=$(valor_de_clave "$b" "$clave")
    previo_vigente=$(valor_de_clave "$a" "$clave")
    [ "$previo_vigente" = "$valor" ] && continue
    if ! printf '%s\n' "$ya" | grep -qx -- "$clave"; then
      anota_ultimo "CLAVE=$clave:$(valor_vigente "$clave")"
    fi
    if [ "$valor" = live ]; then
      plat=$(plataforma_de_clave "$clave") ||
        aborta "la clave $clave no nombra una plataforma conocida"
      corre_en_contenedor exec orbit-app-1 python -m tools.auto03_verifica evalua \
        --platform "$plat" --json "/tmp/auto03-$plat.json" ||
        aborta "evalua de $plat fallo antes de subir $clave a live"
      corre_en_contenedor exec orbit-app-1 python -m tools.auto03_verifica replay \
        --json "/tmp/auto03-$plat.json" ||
        aborta "replay de $plat fallo antes de subir $clave a live"
    fi
    cmd=$(clave_a_cli "$clave" "$valor") ||
      aborta "la clave $clave no es del motor de productos y no tiene camino por productos-modo"
    echo "clave $clave: '$previo_vigente' -> '${valor:-<vaciar>}'"
    corre_en_contenedor $cmd
  done 3< <(printf '%s\n' "$b" "$a" | grep -v '^$' | sed 's/=.*$//' | sort -u)
}

# ---------------------------------------------------------------------------
# --ensayo (solo lectura contra produccion; nada se escribe)
# ---------------------------------------------------------------------------
ensayo() {
  local previo m cab estado clave valor
  echo "== ENSAYO de despliegue (solo lectura; MOTOR_SHA=$MOTOR_SHA)"
  # Sonda de infraestructura: si ssh o psql fallan, el ensayo sale distinto
  # de cero (B.1); las guardas de contenido siguen siendo informativas.
  echo "SELECT 1;" | q >/dev/null || aborta "psql/ssh no contestan"
  ssh goncloud true || aborta "ssh goncloud no contesta"
  previo=$(lee_sha_srv)
  echo "SHA_PREVIO=${previo:-<vacio>}"
  if [ -n "$previo" ] && git merge-base --is-ancestor "$MOTOR_SHA" "$previo" 2>/dev/null; then
    echo "monotonia: $previo ya contiene $MOTOR_SHA; un despliegue real anotaria sin_cambios"
  else
    echo "monotonia: $MOTOR_SHA no esta contenido; un despliegue real aplicaria:"
    valida_migraciones
    while IFS= read -r m; do
      [ -n "$m" ] || continue
      cab=$(cabecera_de "$m")
      estado=$(echo "$cab" | q) || aborta "la sonda de $m fallo"
      [ -n "$estado" ] || aborta "la sonda de $m salio vacia"
      if [ "$estado" = t ]; then
        echo "  ya aplicada: $m"
      else
        echo "  aplicaria: $m (sonda f)"
      fi
    done < <(migraciones_del_sha)
  fi
  if guardas_ok; then
    echo "guardas: OK (una pasada, sin esperar)"
  else
    echo "guardas: DETENIDAS (informativo; el despliegue real esperaria y saldria 75)"
  fi
  previo=$(lee_sha_srv)
  if [ -n "$previo" ] && git merge-base --is-ancestor "$MOTOR_SHA" "$previo" 2>/dev/null; then
    echo "claves: ninguna (monotonia)"
  else
    while IFS= read -r clave <&3; do
      [ -n "$clave" ] || continue
      valor=$(valor_de_clave "$(claves_de_sha "$MOTOR_SHA")" "$clave")
      if [ "$(valor_de_clave "$(claves_de_sha "$previo")" "$clave")" != "$valor" ]; then
        echo "clave $clave: cambiaria a '${valor:-<vaciar>}'"
      fi
    done 3< <(printf '%s\n' "$(claves_de_sha "$MOTOR_SHA")" "$(claves_de_sha "$previo")" |
      grep -v '^$' | sed 's/=.*$//' | sort -u)
  fi
  echo "== ENSAYO LISTO (no se escribio nada)"
}

# ---------------------------------------------------------------------------
# (0) Candado y diario
# ---------------------------------------------------------------------------
main() {
  local l ult_sha ult_estado cambios reanuda previo stamp pend m
  if [ "$ENSAYO" = 1 ]; then
    ensayo
    return 0
  fi
  if ! toma_candado; then
    echo "candado $SRV/despliegue/.lock ocupado: sale 75 sin escribir ultimo.env" >&2
    exit "$ABORTA_GUARDA"
  fi
  l=$(lee_ultimo)
  ult_sha=$(printf '%s\n' "$l" | ultimo_de MOTOR_SHA)
  ult_estado=$(printf '%s\n' "$l" | ultimo_de ESTADO)
  if printf '%s\n' "$l" | tiene_cambios; then cambios=1; else cambios=0; fi
  if [ "$ult_estado" = revirtiendo ] || [ "$ult_estado" = rollback_incompleto ] ||
    { [ "$cambios" = 1 ] && [ "$ult_sha" != "$MOTOR_SHA" ] && ! es_terminal "$ult_estado"; }; then
    echo "hay una reversa pendiente (ESTADO=${ult_estado:-<sin diario>}): desplegar se niega." >&2
    echo "Terminala con: MOTOR_SHA=<sha> bash tools/despliegue/rollback.sh" >&2
    exit "$ABORTA_CODIGO"
  fi
  reanuda=0
  if [ "$ult_sha" = "$MOTOR_SHA" ] && ! es_terminal "$ult_estado"; then
    reanuda=1
    echo "reanudacion: el diario ya es de $MOTOR_SHA (ESTADO=$ult_estado); conserva sus lineas"
  else
    previo=$(lee_sha_srv)
    escribe_ultimo "$MOTOR_SHA" "$previo"
    echo "diario nuevo: MOTOR_SHA=$MOTOR_SHA SHA_PREVIO=${previo:-<vacio>}"
  fi
  l=$(lee_ultimo)
  previo=$(printf '%s\n' "$l" | primero_de SHA_PREVIO)

  echo "== 1) guardas (plazo ${AUTO03_ESPERA_S}s, reintento cada 15s)"
  while ! guardas_ok; do
    if [ "$(date +%s)" -ge "$FECHA_LIMITE" ]; then
      if printf '%s\n' "$l" | tiene_cambios; then
        anota_ultimo "ESTADO=guardas_con_cambios"
        echo "guardas agotadas con cambios anotados: el rollback del motor deshace lo anotado" >&2
      else
        anota_ultimo "ESTADO=abortado_sin_cambios"
        echo "guardas agotadas sin cambios: no se toco nada" >&2
      fi
      exit "$ABORTA_GUARDA"
    fi
    echo "guardas pendientes; reintento en 15s"
    sleep 15
  done
  echo "guardas OK"

  if [ -n "$previo" ] && git merge-base --is-ancestor "$MOTOR_SHA" "$previo" 2>/dev/null; then
    echo "== 2) monotonia: $previo ya contiene $MOTOR_SHA; no toca codigo, migraciones ni claves"
    anota_ultimo "ESTADO=sin_cambios"
    return 0
  fi

  valida_migraciones
  if ! printf '%s\n' "$l" | grep -q '^CODIGO='; then
    stamp=$(date -u +%Y%m%d-%H%M%S)
    respalda "$stamp"
  else
    echo "== 3) respaldo ya anotado en el diario; no se repite"
  fi

  echo "== 4) migraciones *_auto03_* pendientes"
  pend=$(pendientes)
  if [ -z "$pend" ]; then
    echo "ninguna pendiente"
  else
    while IFS= read -r m; do
      [ -n "$m" ] || continue
      anota_ultimo "MIGRACION=$m"
      git show "$MOTOR_SHA:$m" | w
      echo "aplicada $m (anotada en ultimo.env)"
    done <<<"$pend"
  fi

  mkdir -p "$TMP/ref"
  despliega_codigo
  converge_claves "$previo"
  anota_ultimo "ESTADO=desplegado"
  echo "== LISTO. Sigue: MOTOR_SHA=$MOTOR_SHA bash tools/despliegue/checklist.sh"
}

main
