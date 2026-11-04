# H4 / A.4 — cron `ads-salud` instalado en produccion

Go especifico del dueno en la conversacion del 2026-09-29 Vancouver:
"ok hazlo" tras pedir autorizacion para instalar solo la linea `ads-salud`
en el crontab de `gon`, con respaldo y reversa. Ejecutado 2026-09-30
02:04:43 UTC como `root` en `goncloud`; no se recreo el contenedor ni se
tocaron otros servicios.

Preflight: `orbit-app-1` running, imagen
`sha256:f84b0a350b89a7e64c97a3fdc943b44d6f7c2dea372b502184d8f5b1d01ba122`,
`ads_salud_lines=0` en el crontab de `gon`.

Se guardo el crontab anterior en
`/mnt/data/appdata/orbit/archive/crontab-gon-pre-ads-salud-20260930T020443Z`
(`sha256:87711117d18bd4d02eba8a172d180d6ee23951796683895b2c9a3643a9b7b60d`).
El nuevo crontab tiene `sha256:86635eeb2b8d50d4582da06b740cacd22726f286a5eb5ef56495e86db2074506`.
El readback se comparo byte a byte con el archivo preparado: el unico delta
fueron el comentario `job_key=ads-salud` y la linea exacta documentada en
`docs/DEPLOY.md`. `cron_servicio=active`.

Comando de instalacion ejecutado (exit 0):

```bash
ssh -T -o BatchMode=yes goncloud 'bash -s' <<'SH'
set -euo pipefail
linea='*/10 10-12 * * * docker exec orbit-app-1 python -m app.cli ads-salud >> /mnt/data/appdata/orbit/logs/ads-salud.log 2>&1'
comentario='# job_key=ads-salud  A.3: 10:30 UTC y reintentos hasta 12:50'
if crontab -u gon -l | grep -F 'app.cli ads-salud' >/dev/null; then
    echo 'ABORTO: ya existe una linea ads-salud en el crontab de gon' >&2
    exit 1
fi
marca=$(date -u +%Y%m%dT%H%M%SZ)
respaldo="/mnt/data/appdata/orbit/archive/crontab-gon-pre-ads-salud-$marca"
test ! -e "$respaldo"
crontab -u gon -l > "$respaldo"
nuevo=$(mktemp)
actual=$(mktemp)
trap 'rm -f "$nuevo" "$actual"' EXIT
cat "$respaldo" > "$nuevo"
printf '\n%s\n%s\n' "$comentario" "$linea" >> "$nuevo"
crontab -u gon "$nuevo"
crontab -u gon -l > "$actual"
if ! cmp -s "$nuevo" "$actual"; then
    crontab -u gon "$respaldo"
    echo 'ABORTO: readback distinto; crontab restaurado' >&2
    exit 1
fi
printf 'backup=%s\n' "$respaldo"
printf 'antes_sha256=%s\n' "$(sha256sum "$respaldo" | cut -d' ' -f1)"
printf 'despues_sha256=%s\n' "$(sha256sum "$actual" | cut -d' ' -f1)"
printf 'ads_salud_lineas=%s\n' "$(grep -Fc 'app.cli ads-salud' "$actual")"
printf 'cron_servicio=%s\n' "$(systemctl is-active cron)"
SH
```

```text
backup=/mnt/data/appdata/orbit/archive/crontab-gon-pre-ads-salud-20260930T020443Z
antes_sha256=87711117d18bd4d02eba8a172d180d6ee23951796683895b2c9a3643a9b7b60d
despues_sha256=86635eeb2b8d50d4582da06b740cacd22726f286a5eb5ef56495e86db2074506
ads_salud_lineas=1
cron_servicio=active
```

Readback independiente:

```text
$ ssh goncloud 'bash -s' < tools/check_ads_salud_cron.sh
ads-salud cron ok: una linea exacta en crontab de gon
exit 0
$ curl -fsS http://127.0.0.1:8010/health
{"status":"ok"}
$ docker exec orbit-app-1 python -m app.cli ads-salud
ads-salud chequeo=omitido motivo=pre_1030 ahora=2026-09-30T02:05:02.447553+00:00
exit 0
```

Post-check en DB como `orbit_read` (`BEGIN READ ONLY`): cero incidentes
abiertos; ultimo `ingest_run` principal exitoso 516, terminado el 29-sep
08:09:42 UTC. La ejecucion manual antes de 10:30 debia abstenerse; no
demuestra todavia el disparo del cron. La primera corrida programada sera
a las 10:00 UTC (se abstiene), y el primer chequeo util a las 10:30 UTC.
Observar `logs/ads-salud.log` y `/salud` tras ese horario. Los siete dias
de observacion del aviso empiezan el 30-sep; A.4 sigue WIP.

Reversa preparada: `crontab -u gon
/mnt/data/appdata/orbit/archive/crontab-gon-pre-ads-salud-20260930T020443Z`.
