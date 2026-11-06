# lib.sh - plomeria compartida de la receta de despliegue AUTO-03 (B.1-B.2).
#
# Cada script define ANTES de sourcear este archivo:
#   SIM               0 (produccion) o 1 (ORBIT_SIMULACION=1)
#   SRV               destino: ${ORBIT_SRV:-/mnt/data/appdata/orbit}
#   DSN_SIM           base de simulacion (solo con SIM=1)
#   AUTO03_ESPERA_S   plazo unico del candado y de las guardas (600 por omision)
#
# Produccion: los datos y el journal (ultimo.env) van por ssh goncloud con
# docker exec; el candado vive en el server y lo sostiene UNA sesion ssh.
# ORBIT_SIMULACION=1: los datos van contra DSN_SIM con psql local; el journal
# y $SRV son locales; docker y ssh son los binarios del PATH (la prueba de
# B.5 pone falsos que registran sus argumentos). El DSN jamas se imprime.
# Este archivo NO fija las banderas del shell: cada script pone las suyas
# (desplegar y rollback corren con -e; el checklist sigue midiendo sin -e).

ABORTA_CODIGO=1
ABORTA_GUARDA=75

aborta() {
  echo "ABORTA: $*" >&2
  exit "${2:-$ABORTA_CODIGO}"
}

# ---------------------------------------------------------------------------
# SQL: q lee (formato tA), w escribe cada script en su propia transaccion (-1)
# ---------------------------------------------------------------------------
q() {
  if [ "$SIM" = 1 ]; then
    psql "$DSN_SIM" -X -q -tA -v ON_ERROR_STOP=1
  else
    ssh goncloud 'DSN=$(docker exec orbit-app-1 printenv ORBIT_DSN_READ); docker exec -i orbit-db-1 psql "$DSN" -X -q -tA -v ON_ERROR_STOP=1'
  fi
}
w() {
  # -1 en ambas ramas: cada script queda en UNA transaccion (B.1(4)); sin el,
  # un script que revienta a medias deja esquema parcial.
  if [ "$SIM" = 1 ]; then
    psql "$DSN_SIM" -X -q -tA -v ON_ERROR_STOP=1 -1
  else
    ssh goncloud 'docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1 -1'
  fi
}

# ---------------------------------------------------------------------------
# Archivos remotos (en produccion viven en el server; en sim, en $SRV local)
# ---------------------------------------------------------------------------
lee_remoto() { # lee_remoto <ruta> -> contenido, vacio si no existe o ssh falla
  if [ "$SIM" = 1 ]; then
    cat "$1" 2>/dev/null || true
  else
    ssh goncloud "cat '$1' 2>/dev/null" || true
  fi
}
escribe_remoto() { # escribe_remoto <ruta> (contenido por stdin)
  if [ "$SIM" = 1 ]; then
    cat > "$1"
  else
    ssh goncloud "cat > '$1'"
  fi
}
borra_remoto() {
  if [ "$SIM" = 1 ]; then
    rm -f "$1"
  else
    ssh goncloud "rm -f '$1'"
  fi
}
prepara_srv() {
  if [ "$SIM" = 1 ]; then
    mkdir -p "$SRV/despliegue"
  else
    ssh goncloud "mkdir -p '$SRV/despliegue'"
  fi
}

# ---------------------------------------------------------------------------
# Journal $SRV/despliegue/ultimo.env (contrato del rollback)
# ---------------------------------------------------------------------------
lee_ultimo() {
  lee_remoto "$SRV/despliegue/ultimo.env"
}
anota_ultimo() { # anota_ultimo <linea> (append; cada paso anota ANTES de cambiar)
  if [ "$SIM" = 1 ]; then
    printf '%s\n' "$1" >> "$SRV/despliegue/ultimo.env"
  else
    printf '%s\n' "$1" | ssh goncloud "cat >> '$SRV/despliegue/ultimo.env'"
  fi
}
escribe_ultimo() { # escribe_ultimo <MOTOR_SHA> <SHA_PREVIO> (diario nuevo)
  escribe_remoto "$SRV/despliegue/ultimo.env" <<EOF
MOTOR_SHA=$1
ESTADO=guardas
SHA_PREVIO=$2
EOF
}
primero_de() { # primero_de <clave> (lineas por stdin) -> primer valor o vacio
  awk -F= -v k="$1" '$1 == k { print substr($0, length(k) + 2); exit }'
}
ultimo_de() { # ultimo_de <clave> (lineas por stdin) -> ultimo valor o vacio
  awk -F= -v k="$1" '$1 == k { v = substr($0, length(k) + 2) } END { if (v != "") print v }'
}
es_terminal() { # estados terminales de B.1; rollback_incompleto NO lo es
  case "$1" in
    verificado | sin_cambios | abortado_sin_cambios | revertido) return 0 ;;
    *) return 1 ;;
  esac
}
tiene_cambios() { # lineas por stdin: 0 si no hay CODIGO/MIGRACION/CLAVE
  grep -q -E '^(CODIGO|MIGRACION|CLAVE)='
}
lee_sha_srv() {
  lee_remoto "$SRV/despliegue/sha" | tr -d '[:space:]'
}
escribe_sha_srv() {
  escribe_remoto "$SRV/despliegue/sha" <<EOF
$1
EOF
}
borra_sha_srv() {
  borra_remoto "$SRV/despliegue/sha"
}

# ---------------------------------------------------------------------------
# Candado $SRV/despliegue/.lock sostenido por la misma corrida (fd 9)
# ---------------------------------------------------------------------------
PID_CANDADO=""
CANDADO_FIFO=""
TOKEN_CANDADO=""
toma_candado() { # 0 = tomado; 75 = plazo agotado sin tomarlo
  local plazo=${AUTO03_ESPERA_S:-600} fin tomado
  prepara_srv
  if [ "$SIM" = 1 ]; then
    command -v flock >/dev/null 2>&1 || aborta "flock no esta en el PATH (simulacion)" "$ABORTA_GUARDA"
    exec 9>>"$SRV/despliegue/.lock"
    if flock -w "$plazo" 9; then
      return 0
    fi
    exec 9>&-
    return "$ABORTA_GUARDA"
  fi
  # El marcador lleva el TOKEN de ESTA corrida: un .lock.tomado escrito por
  # otra corrida (o el residuo de una muerta) no puede hacer pasar por
  # adquirido un candado que esta corrida no tiene.
  TOKEN_CANDADO="$$-$(date +%s)-$RANDOM"
  CANDADO_FIFO="${TMPDIR:-/tmp}/auto03-candado.$$"
  rm -f "$CANDADO_FIFO"
  mkfifo -m 600 "$CANDADO_FIFO" || return "$ABORTA_GUARDA"
  # La sesion ssh vive mientras el fd 9 este abierto: el `cat` remoto lee
  # hasta EOF y el flock se libera cuando esta corrida cierra o muere. El
  # rm del marcador va con &&: si el flock se agoto, el marcador del QUE
  # TIENE el candado no se toca (el token equivocado ya hace que esta
  # corrida no se asigne el candado).
  ssh goncloud "flock -w $plazo '$SRV/despliegue/.lock' bash -c 'echo $TOKEN_CANDADO > \"$SRV/despliegue/.lock.tomado\"; cat >/dev/null' && rm -f '$SRV/despliegue/.lock.tomado'" \
    <"$CANDADO_FIFO" >/dev/null 2>&1 &
  PID_CANDADO=$!
  exec 9<>"$CANDADO_FIFO"
  fin=$(( $(date +%s) + plazo ))
  while [ "$(date +%s)" -le "$fin" ]; do
    tomado=$(lee_remoto "$SRV/despliegue/.lock.tomado")
    [ "$tomado" = "$TOKEN_CANDADO" ] && return 0
    sleep 2
  done
  suelta_candado
  return "$ABORTA_GUARDA"
}
suelta_candado() {
  { exec 9>&-; } 2>/dev/null || true
  if [ -n "$PID_CANDADO" ]; then
    { kill "$PID_CANDADO"; } 2>/dev/null || true
    { wait "$PID_CANDADO"; } 2>/dev/null || true
    PID_CANDADO=""
  fi
  [ -n "$CANDADO_FIFO" ] && { rm -f "$CANDADO_FIFO"; } 2>/dev/null || true
  return 0
}

# ---------------------------------------------------------------------------
# Interruptores y su camino de regreso por el CLI productos-modo (C.6)
# ---------------------------------------------------------------------------
claves_de_sha() { # claves_de_sha <sha> -> lineas clave=valor; vacio si el sha lo es o no existe
  # Con SHA vacio NO se lee el indice git (git show ':ruta' lo haria): sin
  # SHA_PREVIO todas las claves cuentan como cambiadas (B.1).
  [ -n "$1" ] || return 0
  git show "$1:tools/despliegue/interruptores.env" 2>/dev/null || true
}
plataforma_de_clave() { # plataforma_de_clave <clave> -> amazon_mx/amazon_us/..., completas
  case "$1" in
    ads_producto_pausa_clics_*) printf '%s\n' "${1#ads_producto_pausa_clics_}" ;;
    ads_producto_pausa_gasto_*) printf '%s\n' "${1#ads_producto_pausa_gasto_}" ;;
    ads_producto_meta_*) printf '%s\n' "${1#ads_producto_meta_}" ;;
    ads_producto_pausa_*) printf '%s\n' "${1#ads_producto_pausa_}" ;;
    *) return 1 ;;
  esac
}
valor_de_clave() { # valor_de_clave <lineas clave=valor> <clave> -> valor o vacio
  printf '%s\n' "$1" | awk -F= -v k="$2" '$1 == k { print substr($0, length(k) + 2) }'
}
valor_vigente() { # valor_vigente <clave> -> valor vigente en config_version o vacio
  echo "SELECT COALESCE(settings ->> '$1', '') FROM config_version ORDER BY id DESC LIMIT 1;" | q
}
clave_a_cli() { # clave_a_cli <clave> <valor> -> args de productos-modo; 1 si la clave no es del motor
  local clave=$1 valor=$2 plat
  case "$clave" in
    ads_producto_meta_*)
      plat=${clave#ads_producto_meta_}
      echo "exec orbit-app-1 python -m app.cli productos-modo --platform $plat --meta ${valor:-off}"
      ;;
    ads_producto_pausa_clics_*)
      plat=${clave#ads_producto_pausa_clics_}
      [ -n "$valor" ] || return 1
      echo "exec orbit-app-1 python -m app.cli productos-modo --platform $plat --clics $valor"
      ;;
    ads_producto_pausa_gasto_*)
      plat=${clave#ads_producto_pausa_gasto_}
      [ -n "$valor" ] || return 1
      echo "exec orbit-app-1 python -m app.cli productos-modo --platform $plat --gasto $valor"
      ;;
    ads_producto_pausa_*)
      plat=${clave#ads_producto_pausa_}
      echo "exec orbit-app-1 python -m app.cli productos-modo --platform $plat --pausa ${valor:-off}"
      ;;
    *)
      return 1
      ;;
  esac
}
corre_en_contenedor() { # corre_en_contenedor <args de docker> (dentro de orbit-app-1)
  # Con la stdin cerrada: un ssh o un docker dentro de un bucle `while read`
  # drenaria la entrada del bucle y se comeria el resto de las lineas.
  # `compose` corre SIEMPRE dentro de $SRV (el proyecto Compose de Orbit):
  # una sesion ssh con otro directorio inicial no encontraria la config.
  if [ "$1" = compose ]; then
    if [ "$SIM" = 1 ]; then
      (cd "$SRV" && docker "$@") </dev/null
    else
      ssh -n goncloud "cd '$SRV' && docker $*"
    fi
  elif [ "$SIM" = 1 ]; then
    docker "$@" </dev/null
  else
    ssh -n goncloud "docker $*"
  fi
}

# ---------------------------------------------------------------------------
# Huella de un arbol: un solo md5 sobre el listado ordenado "hash  ruta"
# ---------------------------------------------------------------------------
huella_arbol() { # huella_arbol <dir> <ruta1> [ruta2...]
  local dir=$1
  shift
  (
    cd "$dir" || exit 1
    for x in "$@"; do
      if [ -d "$x" ]; then
        find "$x" -type f
      else
        printf '%s\n' "$x"
      fi
    done | sort | xargs -r md5sum | md5sum | cut -d' ' -f1
  )
}
huella_remota() { # huella_remota <ruta1> [ruta2...] en $SRV del server
  local lista=$*
  ssh goncloud "cd '$SRV' && { for x in $lista; do if [ -d \"\$x\" ]; then find \"\$x\" -type f; else printf '%s\n' \"\$x\"; fi; done | sort | xargs -r md5sum | md5sum | cut -d' ' -f1; }"
}
