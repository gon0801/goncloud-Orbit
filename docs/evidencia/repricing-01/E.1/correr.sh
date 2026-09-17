#!/usr/bin/env bash
# ORBIT · fase 8 · repricing-01 · E.1
#
# Corredor de las consultas de solo lectura de E.1 contra producción.
# NO lo corre el implementador de esta tarea.
#
# El 2026-09-17 el lead intentó una sonda trivial de solo lectura contra
# producción y el clasificador de permisos de su sesión la negó (motivo:
# Production Reads). Desde entonces nadie la ha vuelto a intentar, ni el
# lead ni sus subagentes: la negativa se respetó y no se rodeó. Por eso
# todas las cifras de producción de esta carpeta están en unknown.
#
# Lo corre el dueño, con:
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

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONSULTAS_DIR="$DIR/consultas"
SALIDAS_DIR="$DIR/salidas"
mkdir -p "$SALIDAS_DIR"

# Candado: ninguna consulta puede contener una palabra de escritura.
# grep -iE con salida = aborta ANTES de tocar producción.
if grep -liE '\b(insert|update|delete|truncate|alter|drop|create|grant|copy)\b' "$CONSULTAS_DIR"/*.sql; then
    echo "ATORADO: una o más consultas en $CONSULTAS_DIR contienen una palabra de escritura prohibida (ver arriba)." >&2
    exit 1
fi

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
        exit 1
    fi
done

echo "Listo. Salidas en $SALIDAS_DIR/"
