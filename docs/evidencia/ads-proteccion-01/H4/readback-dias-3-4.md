# H4 / A.4 — dias 3 y 4 de observacion (28-29 sep UTC)

Lectura de produccion el 2026-09-30 01:48 UTC, como `orbit_read` en
`BEGIN READ ONLY` ... `ROLLBACK`, y GET de `/api/dashboard/salud`.

| Dia | Ingesta principal | Filas | Reportes `written` |
| --- | --- | ---: | --- |
| 28 sep | run 498, `ok=true`, 07:10-08:08 UTC | 10041 | 4 MX + 4 US |
| 29 sep | run 516, `ok=true`, 07:10-08:09 UTC | 10095 | 4 MX + 4 US |

Los dias 1 y 2 constan en `readback-parcial.md` (runs 460 y 479). Hay 4/7
dias naturales con exito principal. `/salud` muestra los ocho reportes con
`ultimo_estado=written`, `ultima_corrida=516`, `metric_date=2026-09-28` y
`incidentes=[]` en MX y US. La tabla `ads_ingest_incident` conserva solo la
fila 1, episodio falso cerrado el 28 sep 03:52 UTC, sin recovery ni aviso
de recuperacion; no nacieron incidentes nuevos en las runs 498 y 516.

El perfil CA sigue como un `rejected` sin plataforma en ambas runs. Esto
confirma que el arreglo #369 evita abrir un nuevo fallo global falso.

## Defecto operativo que impide cerrar A.4

El crontab productivo de `gon`, actualizado por ultima vez el 20 sep, NO
contiene `app.cli ads-salud`. Tampoco existe `logs/ads-salud.log`. El comando
figura en `docs/DEPLOY.md` pero no se instalo cuando A.3 llego a produccion
el 25 sep por el deploy de C.5. La ingesta de las 07:10 llama a la salud al
terminar, pero eso no sustituye el chequeo de atraso de las 10:30 ni sus
reintentos si no hubo ingesta exitosa.

Prueba operativa nueva: `tools/check_ads_salud_cron.sh`, ejecutada por stdin
en el servidor antes de cualquier arreglo:

```text
$ ssh goncloud 'bash -s' < tools/check_ads_salud_cron.sh
ads-salud cron invalido: total=0 exacta=0
exit 1
```

La instalacion del cron requiere el go de deploy de A.4 del runbook. Despues
hay que repetir la prueba hasta exit 0, comprobar la primera ejecucion
programada en `logs/ads-salud.log` y continuar el readback diario hasta el
2 oct. A.4 sigue abierta; los cuatro exitos de ingesta no demuestran que
funcione el aviso de atraso sin el cron.

Operacion preparada para despues del go especifico, NO ejecutada: respaldar
`crontab -u gon -l` en `archive/crontab-gon-pre-ads-salud-<UTC>`, comprobar
que no haya ninguna linea con `app.cli ads-salud`, y anadir solo estas dos
lineas al crontab de `gon`:

```cron
# job_key=ads-salud  A.3: 10:30 UTC y reintentos hasta 12:50
*/10 10-12 * * * docker exec orbit-app-1 python -m app.cli ads-salud >> /mnt/data/appdata/orbit/logs/ads-salud.log 2>&1
```

Readback inmediato: repetir `ssh goncloud 'bash -s' <
tools/check_ads_salud_cron.sh` (debe salir 0), comparar el crontab anterior
y posterior para exigir que el unico delta sean esas dos lineas, y verificar
que `orbit-app-1` sigue sano. Reversa: `crontab -u gon
/mnt/data/appdata/orbit/archive/<respaldo>`; no toca otros servicios.
