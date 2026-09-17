#!/usr/bin/env bash
# ORBIT · fase 8 · repricing-01 · E.1
#
# Corredor de las consultas de solo lectura de E.1 contra producción.
# NO lo corre el implementador de esta tarea.
#
# El 2026-09-17 el lead intentó una sonda trivial de solo lectura contra
# producción y el clasificador de permisos de su sesión la negó (motivo:
# Production Reads). La negativa se respetó y no se rodeó: ni el lead ni
# sus subagentes lo reintentaron. Ese mismo día el dueño autorizó la
# lectura con una regla de permiso (Bash(ssh goncloud:*) en
# settings.local.json del worktree del lead), y a partir de ahí el lead,
# y solo el lead, corrió correr.sh con el rol orbit_read en transacciones
# READ ONLY. Las cifras de este documento salen de salidas/*.txt.
#
# Lo corre el lead o el dueño, con:
#
#   ! bash docs/evidencia/repricing-01/E.1/correr.sh
#
# Cada consulta corre dentro de BEGIN READ ONLY (aborta si algo intenta
# escribir) y termina en ROLLBACK explícito (ninguna corrida dentro de
# esta base queda a medias ni deja un efecto colateral, aunque READ ONLY
# ya lo impida por su cuenta). La validación local (validacion-local.md)
# corre EXACTAMENTE este mismo formato de pipeline — lo validado es lo
# que se corre (hallazgo 9, ronda r1).
#
# Escritura atómica (hallazgo 10, ronda r1): cada resultado se escribe
# primero a "<nombre>.txt.parcial" y solo se renombra a "<nombre>.txt" si
# el pipeline completo salió 0 (ver PIPESTATUS abajo); así un resultado
# nunca queda a medio escribir con nombre final. stderr va aparte a
# "<nombre>.err" para no mezclarse con la salida de datos. Un resultado de
# cero filas nunca deja un archivo de 0 bytes: el archivo siempre trae al
# menos "BEGIN", el \echo de inicio/fin y "ROLLBACK".
#
# Identidad de la corrida (CodeRabbit, PR 297): "salidas/CORRIDA.txt" dice
# de qué corrida son los .txt. Nace "EN CURSO" antes de la primera consulta,
# pasa a "FALLIDA en <consulta>" si alguna aborta y solo al final a
# "COMPLETA", con el sha256 de cada salida. Una re-corrida que falla a la
# mitad deja .txt nuevos junto a .txt de la corrida anterior: sin este
# archivo el directorio parecería una extracción completa. REGLA DE LECTURA:
# si CORRIDA.txt no dice "estado: COMPLETA", salidas/ NO es una extracción.

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
