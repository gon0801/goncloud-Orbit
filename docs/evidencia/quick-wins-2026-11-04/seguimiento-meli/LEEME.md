# P01: comprobacion unica posterior al cron MeLi

**Instalado y programado:** job 10, **5-nov-2026 09:00 UTC** (01:00 America/Vancouver).
Hashes y permisos en `instalacion-hashes.json`; fecha, comando y conservación del job 8
en `programacion.json`. Prueba del archivo instalado: `smoke-instalado.json`, PENDIENTE/exit 3.
El job 10 corrió el 5-nov-2026 09:00 UTC con `estado=OK`: **P01 cerrado** (detalle abajo).

## Resultado del job 10 (5-nov-2026 09:00 UTC) — P01 CERRADO

Leído por SSH, solo lectura, el 5-nov-2026 ~09:01 UTC. Copia íntegra en
[`resultado-job10-20261105T090000Z.json`](resultado-job10-20261105T090000Z.json)
(sin DSN ni secretos). `ejecucion.log`: `{"estado": "OK"}`; `atq` ya no lista el job 10 y conserva el job 8.

- `estado=OK`, sesión `orbit_read` con `transaction_read_only=on`.
- Corrida nueva posterior al cron: `ingest_run` **1374**, 08:15:08 UTC, exitosa, 6 escritas / 810 duplicadas
  (misma versión canónica), cero rechazos.
- Huella de la corrida = huella del snapshot actual: `5985ba79…adb105`, as-of 2026-11-05 07:25:59 UTC.
- Conciliación 2026-04-16..2026-11-04: 812/812 filas campaña/día, 0 diferencias, 0 faltantes y 0 sobrantes;
  203/203 días de control, 0 diferencias. T+0: 4/4 filas y 1/1 día de control, 0 diferencias.
  La diferencia de +13.47 MXN del 4-nov quedó absorbida por la corrida nueva.
- Logs: `2026-11-05 costs/fx/ledger/meli-ads rc=0`; `meli_ads.log` no vacío (`run_id=1374 ok=True`).

No se reejecutó ninguna ingesta ni se modificó producción para esta lectura.

Preparación inicial: La prueba manual real en goncloud por
stdin devolvio `PENDIENTE` (exit 3) el 2026-11-05 01:17:09 UTC, antes del cron
08:15 UTC. No creo archivos remotos ni modifico fuentes o tareas.

El host crea una copia SQLite consistente abriendo accounting con `mode=ro`.
Temporales propios 0600, directorios 0700; el contenedor recibe esa copia por
stdin, sin montar accounting. El DSN permanece en el entorno del contenedor;
la sesion PostgreSQL debe ser `orbit_read` y READ ONLY, con timeout de 15 s.
No corre ingesta ni API. No genera mensajes externos.

El worker reutiliza **sin cambios** el conciliador existente (se copia al
instalar). Comprueba campos nulos, moneda, controles diarios, T+0 y ventana
2026-04-16..2026-11-04 conforme a ese conciliador. Exige nueva `ingest_run`
posterior a las 08:15, finalizada y exitosa, y aceptacion con **la misma huella
canonica y as-of** del snapshot actual. Exige el rc=0 fechado en costos.log y
meli_ads.log no vacio. Si accounting cambia entre cron y comprobacion, devuelve
FALLA conservadora: no prueba retrospectivamente que el cron ingirio ese nuevo
snapshot. Ese caso requiere revisar la corrida y no habilita un pase parcial.

La evidencia local del servidor queda en un archivo nuevo
`/mnt/data/appdata/orbit/seguimientos/p01-20261105/resultado-<UTC>.json`.
No persiste DSN ni stderr del runtime; logs pasan por `scrub` de la app.

## Receta de instalacion (ejecutada por el host; no repetir)

El host instaló archivos nuevos con permisos 0600 en directorio 0700, cotejó sus
hashes y creó un único job con entorno mínimo PATH/TZ y `at -M`. El smoke completo
RO y la revisión GPT Sol precedieron a esa instalación. La receta equivalente es:

```bash
BASE=docs/evidencia/quick-wins-2026-11-04/seguimiento-meli
ssh goncloud 'umask 077; mkdir -p /mnt/data/appdata/orbit/seguimientos/p01-20261105; chmod 700 /mnt/data/appdata/orbit/seguimientos/p01-20261105'
scp "$BASE/ejecutar.py" "$BASE/worker.py" goncloud:/mnt/data/appdata/orbit/seguimientos/p01-20261105/
scp docs/evidencia/resumen-ads-multicanal/01-ingesta/30-revision-10266d91/scripts/conciliar.py goncloud:/mnt/data/appdata/orbit/seguimientos/p01-20261105/conciliar.py
ssh goncloud 'chmod 600 /mnt/data/appdata/orbit/seguimientos/p01-20261105/*.py'
ssh goncloud "printf '%s\n' 'umask 077; python3 /mnt/data/appdata/orbit/seguimientos/p01-20261105/ejecutar.py > /mnt/data/appdata/orbit/seguimientos/p01-20261105/ejecucion.log 2>&1' | TZ=UTC at -M -t 202611050900"
```

Conservar **el ID nuevo que imprime at**. Comprobar el ID y fecha con
`ssh goncloud 'TZ=UTC atq'` y su contenido con
`at -c ID_NUEVO`, capturándolo en memoria y mostrando solo el comando esperado;
no publicar el entorno que incluye la salida completa. No tocar el job 8 existente.
La reversa de la programacion es solamente
`ssh goncloud 'atrm 10'` (solo si job 10 sigue siendo este seguimiento). Conservar evidencia y archivos para revision.

Despues de la hora, leer `resultado-*.json` y `ejecucion.log` por SSH.
Un exit 0 significa que todas las puertas anteriores pasaron; exit 1/2 o
`estado=FALLA` requiere revision. No reejecutar la ingesta para fabricar pase.

## Smoke completo real antes del cron

Comando reproducible desde el worktree:

```bash
python3 docs/evidencia/quick-wins-2026-11-04/seguimiento-meli/smoke.py
```

`smoke.py` envia el worker y conciliador existentes por stdin a Python del host;
crea snapshot temporal consistente y lo elimina al salir. Usa el UID por defecto
10001 del contenedor (comprobado con `docker exec orbit-app-1 id`); no requiere
root. La ejecucion real ejercito snapshot, worker, conexion SQL, conciliacion y
lectura de logs. `smoke-resultado.json` conserva el resultado esperado FALLA:
dos campanas del 4 de noviembre y total diario difieren por +13.47 MXN, no hay
corrida posterior a las 08:15 ni aceptacion con huella/as-of correspondiente,
y falta log MeLi exitoso. La sesion fue `orbit_read`, `transaction_read_only=on`;
el snapshot nacio 0600. No se cambio la fecha ni se ejecuto ingesta.

La proteccion de errores de `ejecutar.py` tambien guarda FALLA generica ante
fallos Docker/timeout, sin persistir stderr crudo. Si el almacenamiento falla,
lo indica en stdout. El worker registra el DSN en el redactor antes de leer datos.
