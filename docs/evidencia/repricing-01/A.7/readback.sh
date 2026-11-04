#!/usr/bin/env bash
# ORBIT · fase 10 · repricing-01 · A.7 · readback en produccion (solo lectura)
#
# Corre las consultas de consultas/*.sql (las mismas que ejecuta
# app/precio/fuentes.py) contra produccion, por plataforma, y deja las
# salidas en salidas/<plataforma>/. Lo corre el lead (regla 9 del runbook
# de la Fase 10: solo SELECT, solo por el rol lector ORBIT_DSN_READ, dentro
# de BEGIN READ ONLY) o el dueno:
#
#   ! bash docs/evidencia/repricing-01/A.7/readback.sh
#
# Despues, recuadro_desde_salidas.py arma el recuadro en local con esas
# salidas y --max-dias-sin-reportar explicito (readback.md dice cual).
#
# Candados, antes de conectar (mismo criterio que E.0/correr.sh):
#   - ninguna palabra de escritura en los archivos;
#   - la unica diagonal invertida permitida es una linea `\set nombre valor`
#     (los parametros de la consulta); cualquier otro metacomando aborta;
#   - quitadas esas lineas, cada sentencia es select/with
#     (E.0/solo-select.py, que ademas rechaza todo $ fuera de strings).
# Cada consulta corre como `BEGIN READ ONLY; <consulta>; ROLLBACK;` con
# `psql -q` (sin las lineas BEGIN/ROLLBACK en la salida). Escritura atomica
# (.parcial -> final) y salidas/CORRIDA.txt con el commit y el sha256 de
# cada salida. REGLA DE LECTURA: si CORRIDA.txt no dice "estado: COMPLETA",
# salidas/ NO es una extraccion.

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONSULTAS_DIR="$DIR/consultas"
SALIDAS_DIR="$DIR/salidas"
SOLO_SELECT="$DIR/../E.0/solo-select.py"
HOY="$(date -u +%F)"
PLATAFORMAS="amazon_mx amazon_us"
# Consultas que no se corren, con su razon escrita (queda en CORRIDA.txt).
# Uso: OMITIR="03_goals 04_decisiones" RAZON_OMISION="<por que>" bash readback.sh
# Sin razon no se omite nada: omitir sin decir por que es esconder filas.
OMITIR="${OMITIR:-}"
RAZON_OMISION="${RAZON_OMISION:-}"
if [ -n "$OMITIR" ] && [ -z "$RAZON_OMISION" ]; then
    echo "ATORADO: OMITIR sin RAZON_OMISION" >&2
    exit 1
fi

if grep -liE '\b(insert|update|delete|truncate|alter|drop|create|grant|copy)\b' "$CONSULTAS_DIR"/*.sql; then
    echo "ATORADO: una consulta de $CONSULTAS_DIR contiene una palabra de escritura (ver arriba)." >&2
    exit 1
fi
if grep -nH -F '\' "$CONSULTAS_DIR"/*.sql | grep -vE ':[0-9]+:\\set [a-z_]+ [A-Za-z0-9_-]+$'; then
    echo "ATORADO: una consulta de $CONSULTAS_DIR trae un metacomando distinto de \\set nombre valor (ver arriba)." >&2
    exit 1
fi
command -v python3 >/dev/null 2>&1 || { echo "ATORADO: sin python3 no se valida la forma de las consultas" >&2; exit 1; }
TMP_FORMA="$(mktemp -d)"
for archivo in "$CONSULTAS_DIR"/*.sql; do
    grep -vE '^\\set ' "$archivo" > "$TMP_FORMA/$(basename "$archivo")"
done
if ! python3 "$SOLO_SELECT" "$TMP_FORMA"/*.sql; then
    echo "ATORADO: una consulta de $CONSULTAS_DIR trae una sentencia que no es select/with (ver arriba)." >&2
    exit 1
fi

mkdir -p "$SALIDAS_DIR"
CORRIDA="$SALIDAS_DIR/CORRIDA.txt"
INICIO="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
COMMIT="$(git -C "$DIR" rev-parse HEAD 2>/dev/null || printf 'desconocido')"
SUCIO="$( { git -C "$DIR" status --porcelain -- "$CONSULTAS_DIR" "$0" 2>/dev/null || true; } | wc -l | tr -d ' ')"
escribir_corrida() {
    {
        printf 'estado: %s\n' "$1"
        printf 'inicio_utc: %s\n' "$INICIO"
        printf 'fin_utc: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        printf 'hoy_utc: %s\n' "$HOY"
        printf 'commit_del_repo: %s\n' "$COMMIT"
        printf 'consultas_o_corredor_con_cambios_sin_commitear: %s\n' "$SUCIO"
        printf 'omitidas: %s\n' "${OMITIR:-ninguna}"
        printf 'razon_omision: %s\n' "${RAZON_OMISION:-n/a}"
    } > "$CORRIDA"
}
escribir_corrida "EN CURSO"

for plataforma in $PLATAFORMAS; do
    mkdir -p "$SALIDAS_DIR/$plataforma"
    for archivo in "$CONSULTAS_DIR"/*.sql; do
        nombre="$(basename "${archivo%.sql}")"
        case " $OMITIR " in
            *" $nombre "*) echo "== $plataforma $nombre == OMITIDA ($RAZON_OMISION)"; continue ;;
        esac
        parcial="$SALIDAS_DIR/$plataforma/$nombre.txt.parcial"
        final="$SALIDAS_DIR/$plataforma/$nombre.txt"
        err="$SALIDAS_DIR/$plataforma/$nombre.err"
        echo "== $plataforma $nombre =="
        set +e
        {
            printf '%s\n' 'BEGIN READ ONLY;'
            sed -e "s/^\\\\set platform .*/\\\\set platform $plataforma/" \
                -e "s/^\\\\set hoy .*/\\\\set hoy $HOY/" "$archivo"
            printf '%s\n' 'ROLLBACK;'
        } | ssh goncloud '
            set -eu
            DSN=$(docker exec orbit-app-1 printenv ORBIT_DSN_READ)
            test -n "$DSN" || { echo "ATORADO: ORBIT_DSN_READ vacio"; exit 1; }
            docker exec -i orbit-db-1 psql "$DSN" -X -q -P pager=off -tA -v ON_ERROR_STOP=1
        ' > "$parcial" 2> "$err"
        estado="${PIPESTATUS[1]:-$?}"
        set -e
        if [ "$estado" -eq 0 ]; then
            mv "$parcial" "$final"
        else
            echo "ATORADO: $plataforma $nombre salio con codigo $estado; ver $err" >&2
            rm -f "$parcial"
            escribir_corrida "FALLIDA en $plataforma $nombre (codigo $estado)"
            exit 1
        fi
    done
done

escribir_corrida "COMPLETA"
{
    printf 'salidas:\n'
    for f in "$SALIDAS_DIR"/*/*.txt; do
        printf '  %s  %s\n' "$(shasum -a 256 "$f" | cut -d' ' -f1)" "${f#"$SALIDAS_DIR"/}"
    done
} >> "$CORRIDA"
echo "Listo. Salidas en $SALIDAS_DIR/ (ver CORRIDA.txt)"
