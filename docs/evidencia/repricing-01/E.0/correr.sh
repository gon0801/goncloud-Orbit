#!/usr/bin/env bash
# ORBIT · fase 10 · repricing-01 · E.0a
#
# Corredor de las consultas de solo lectura de E.0a contra produccion.
# Copia del de E.1 (docs/evidencia/repricing-01/E.1/correr.sh): mismo
# pipeline, mismo candado de palabras de escritura, misma escritura atomica
# y el mismo manifiesto salidas/CORRIDA.txt. Solo cambia esta cabecera.
#
# Lo corre el lead (regla 9 del runbook de la Fase 10: solo SELECT, solo
# por el rol lector ORBIT_DSN_READ, dentro de BEGIN READ ONLY) o el dueno:
#
#   ! bash docs/evidencia/repricing-01/E.0/correr.sh
#
# Cada consulta corre dentro de BEGIN READ ONLY y termina en ROLLBACK.
# El test -n del DSN evita que un printenv vacio deje a libpq conectarse
# con sus valores por defecto contra otra base. REGLA DE LECTURA: si
# salidas/CORRIDA.txt no dice "estado: COMPLETA", salidas/ NO es una
# extraccion.

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONSULTAS_DIR="$DIR/consultas"
SALIDAS_DIR="$DIR/salidas"
mkdir -p "$SALIDAS_DIR"

CORRIDA="$SALIDAS_DIR/CORRIDA.txt"
INICIO="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
COMMIT="$(git -C "$DIR" rev-parse HEAD 2>/dev/null || printf 'desconocido')"
# El `|| true` va DENTRO del pipeline: fuera de un repo git, `git status`
# sale 128 y, con pipefail, tumbaría el script antes de abrir la corrida.
SUCIO="$( { git -C "$DIR" status --porcelain -- "$CONSULTAS_DIR" 2>/dev/null || true; } | wc -l | tr -d ' ')"
escribir_corrida() {  # $1 = estado; el resto del archivo se reescribe entero
    {
        printf 'estado: %s\n' "$1"
        printf 'inicio_utc: %s\n' "$INICIO"
        printf 'fin_utc: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        printf 'commit_del_repo: %s\n' "$COMMIT"
        printf 'consultas_con_cambios_sin_commitear: %s\n' "$SUCIO"
    } > "$CORRIDA"
}

# Candado: ninguna consulta puede contener una palabra de escritura.
# grep -iE con salida = aborta ANTES de tocar producción.
if grep -liE '\b(insert|update|delete|truncate|alter|drop|create|grant|copy)\b' "$CONSULTAS_DIR"/*.sql; then
    echo "ATORADO: una o más consultas en $CONSULTAS_DIR contienen una palabra de escritura prohibida (ver arriba)." >&2
    exit 1
fi

# Candado de metacomandos (CodeRabbit, PR 304): psql ejecuta un "\!" u otro
# metacomando en cualquier punto de la linea y BEGIN READ ONLY no lo
# controla. Ningun archivo de consultas puede traer una diagonal invertida;
# los \echo de inicio y fin los agrega este corredor, no los archivos.
if grep -lF '\' "$CONSULTAS_DIR"/*.sql; then
    echo "ATORADO: una o más consultas en $CONSULTAS_DIR contienen una diagonal invertida (metacomando de psql prohibido; ver arriba)." >&2
    exit 1
fi

# Candado estructural (revision de cierre de la Fase 10, grok sobre 7674ee7):
# cada sentencia de cada archivo de consultas, sin comentarios y respetando
# los strings, empieza con `select` o `with` (solo-select.py). Asi `commit;`,
# `end;`, `END WORK;`, `END/*x*/;`, `do $$...$$`, `set ...`, `revoke` o
# `call` no llegan a produccion, y un `case ... end` en su propia linea no es
# falso positivo. Sin python3 no se corre (falla cerrado).
command -v python3 >/dev/null 2>&1 || { echo "ATORADO: sin python3 no se valida la forma de las consultas" >&2; exit 1; }
if ! python3 "$DIR/solo-select.py" "$CONSULTAS_DIR"/*.sql; then
    echo "ATORADO: una o más consultas en $CONSULTAS_DIR traen una sentencia que no es select/with (ver arriba)." >&2
    exit 1
fi

# Segunda defensa por palabras: control de transaccion y escrituras
# indirectas que caben dentro de un select (`select ... into` crea una
# tabla; `set_config` cambia la sesion aun en READ ONLY).
if grep -liwE '(commit|rollback|abort|begin|savepoint|release|into|call|execute|prepare|lock|vacuum|listen|notify|refresh|reindex|cluster|discard|reset|merge|comment|security|import|load|do|set|set_config|start|transaction)' "$CONSULTAS_DIR"/*.sql; then
    echo "ATORADO: una o más consultas en $CONSULTAS_DIR contienen control de transaccion o una escritura indirecta prohibida (ver arriba)." >&2
    exit 1
fi

# A partir de aquí la corrida existe: lo que haya en salidas/ deja de ser
# "la extracción" hasta que esta termine COMPLETA.
escribir_corrida "EN CURSO"

for archivo in "$CONSULTAS_DIR"/*.sql; do
    nombre="$(basename "${archivo%.sql}")"
    parcial="$SALIDAS_DIR/$nombre.txt.parcial"
    final="$SALIDAS_DIR/$nombre.txt"
    err="$SALIDAS_DIR/$nombre.err"

    echo "== $nombre =="
    set +e
    {
        printf '%s\n' 'BEGIN READ ONLY;'
        printf '%s\n' "\\echo -- INICIO $nombre"
        cat "$archivo"
        printf '%s\n' "\\echo -- FIN $nombre"
        printf '%s\n' 'ROLLBACK;'
    } | ssh goncloud '
        set -eu
        DSN=$(docker exec orbit-app-1 printenv ORBIT_DSN_READ)
        test -n "$DSN" || { echo "ATORADO: ORBIT_DSN_READ vacio"; exit 1; }
        docker exec -i orbit-db-1 psql "$DSN" -X -P pager=off -tA -v ON_ERROR_STOP=1
    ' > "$parcial" 2> "$err"
    # PIPESTATUS[1] es el estado del ssh/psql (el bloque { ... } de la
    # izquierda casi siempre sale 0 porque solo hace echo/cat).
    estado="${PIPESTATUS[1]:-$?}"
    set -e

    if [ "$estado" -eq 0 ]; then
        mv "$parcial" "$final"
    else
        echo "ATORADO: $nombre salió con código $estado; ver $err" >&2
        rm -f "$parcial"
        escribir_corrida "FALLIDA en $nombre (codigo $estado); los .txt de consultas posteriores son de una corrida ANTERIOR"
        exit 1
    fi
done

# Corrida completa: el manifiesto lista cada salida con su sha256, para que
# se note si alguien mezcla archivos de dos corridas.
escribir_corrida "COMPLETA"
{
    printf 'salidas:\n'
    for f in "$SALIDAS_DIR"/*.txt; do
        [ "$f" = "$CORRIDA" ] && continue
        printf '  %s  %s\n' "$(shasum -a 256 "$f" | cut -d' ' -f1)" "$(basename "$f")"
    done
} >> "$CORRIDA"

echo "Listo. Salidas en $SALIDAS_DIR/ (ver CORRIDA.txt)"
