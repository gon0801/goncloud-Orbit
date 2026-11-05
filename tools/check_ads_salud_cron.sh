#!/usr/bin/env bash
# Verifica el cron productivo de A.3 sin modificar el crontab.
set -euo pipefail

esperada='*/10 10-12 * * * docker exec orbit-app-1 python -m app.cli ads-salud >> /mnt/data/appdata/orbit/logs/ads-salud.log 2>&1'
# Dos formas autorizadas completas; nunca interpretar comandos del crontab.
envuelta="*/10 10-12 * * * /usr/local/bin/cron-avisa --nombre \"Salud de la publicidad\" --cada 24 --tras 3 -- /bin/bash -c 'docker exec orbit-app-1 python -m app.cli ads-salud' >> /mnt/data/appdata/orbit/logs/ads-salud.log 2>&1"
if crontab_actual=$(crontab -u gon -l 2>&1); then
    :
else
    estado=$?
    if [ "$estado" -eq 1 ] && [ "$crontab_actual" = 'no crontab for gon' ]; then
        crontab_actual=
    else
        printf '%s\n' "$crontab_actual" >&2
        exit "$estado"
    fi
fi
cuenta_total=$(printf '%s\n' "$crontab_actual" | awk '/app\.cli ads-salud/ {n++} END {print n+0}')
cuenta_exacta=$(printf '%s\n' "$crontab_actual" | awk -v esperada="$esperada" -v envuelta="$envuelta" '$0 == esperada || $0 == envuelta {n++} END {print n+0}')

if [ "$cuenta_total" -ne 1 ] || [ "$cuenta_exacta" -ne 1 ]; then
    printf 'ads-salud cron invalido: total=%s exacta=%s\n' "$cuenta_total" "$cuenta_exacta" >&2
    exit 1
fi

printf 'ads-salud cron ok: una linea exacta en crontab de gon\n'
