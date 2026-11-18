#!/usr/bin/env bash
# Lee los crontab requeridos para retirar un camino de ingesta o reputacion.
# 0: todas las lecturas completas y ningun uso. 1: uso activo. 2: lectura fallida.
set -eu
case ${1:-} in
    D-A) usuarios=(gon root); patron='ingest[[:space:]]+metrics([[:space:][:punct:]]|$)' ;;
    D-B) usuarios=(gon); patron='--fuente([[:space:]]+|=)amazon([[:space:][:punct:]]|$)' ;;
    *) echo 'Uso: bash comprobar-cron.sh D-A|D-B' >&2; exit 2 ;;
esac

total=0
for usuario in "${usuarios[@]}"; do
    if ! crontab=$(ssh -o BatchMode=yes goncloud "crontab -u $usuario -l"); then
        echo "Lectura incompleta del crontab de $usuario" >&2
        exit 2
    fi
    usos=$(awk -v patron="$patron" '
        /^[[:space:]]*#/ { next }
        { gsub(/["\047]/, ""); if ($0 ~ patron) n++ }
        END { print n+0 }
    ' <<< "$crontab")
    printf '%s usos=%d\n' "$usuario" "$usos"
    total=$((total + usos))
done
test "$total" -eq 0
