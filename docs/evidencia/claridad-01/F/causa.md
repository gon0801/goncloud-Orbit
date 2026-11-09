# F.1 Por qué Estados Unidos sale `oferta_desactualizada`

Lectura de producción del 2026-11-09 (14:40 a 15:00 UTC), solo lectura: rol `orbit_read` en
transacción `read_only`, `journalctl` y `crontab -l` en `goncloud`. Salidas completas en
`consultas.txt` y `servidor.txt` de esta carpeta.

## Causa

La causa está en Orbit. Amazon y el bridge sí entregan la oferta, pero Orbit la copia antes de que
llegue.

`tools/refresh_estimacion.sh` (copia en el servidor `/mnt/data/appdata/orbit/refresh_estimacion.sh`,
cron de root `45 */6 * * *`) copia `bridge.db` a una hora fija, diez minutos después de que arranca
`amazon-prices-sync.timer` (`00,06,12,18:35 UTC`). No comprueba que el bridge haya terminado. El
bridge baja primero México y después Estados Unidos, un reporte de Amazon tras otro. En la corrida de
las 12:35 los reportes tardan más y Estados Unidos terminó después de las 12:45 todos los días del
2 al 9 de noviembre:

| Día | US guardado por el bridge | Copia de Orbit |
| --- | --- | --- |
| 1-nov | 12:44:38 | 12:45, entra |
| 2-nov | 12:47:18 | 12:45, no entra |
| 3-nov | 12:45:38 | 12:45, no entra |
| 4-nov | 12:46:43 | 12:45, no entra |
| 5-nov | 12:51:45 | 12:45, no entra |
| 6-nov | 12:46:17 | 12:45, no entra |
| 7-nov | 12:46:49 | 12:45, no entra |
| 8-nov | 12:46:43 | 12:45, no entra |
| 9-nov | 12:48:44 | 12:45, no entra |

Así, la copia de las 12:45 trae la oferta de Estados Unidos de las 06:36. La ingesta la descarta por
vieja (`app/estimacion_insumos.py:375-379`, límite `FRESHNESS_LIMIT` de 6 h en `:41`) y escribe un
escenario `desactualizada` con motivo `oferta_desactualizada` (`app/estimacion_repository.py:1336-1338`).
La corrida de precios de Amazon (cron de gon `10 13 * * *`) lee ese escenario
(`app/spapi/precio_mercado.py:395`, `app/estimacion_reader.py:127-131`) y las 99 publicaciones de US
salen `no_evaluado / oferta_desactualizada`. Las corridas de las 15:10 a las 23:10 no vuelven a
decidir porque la decisión es una al día.

México no se ve afectado porque termina antes de las 12:45: entre 12:40:43 y 12:44:40.

## Evidencia

- `precio_decision` de amazon_us: 99 de 99 `oferta_desactualizada` el 28, 29 y 30 de octubre y del 2
  al 9 de noviembre. El 31 de octubre y el 1 de noviembre sí evaluó (`consultas.txt`, q1 y q6). Es una
  carrera que depende del minuto en que Amazon entrega el reporte, no una ingesta caída.
- `estimacion_oferta_observation` (q3): Estados Unidos tiene ofertas de las 00:36, 06:36 y 18:36, y
  ninguna de las 12:xx. México tiene las cuatro.
- Escenario vigente a las 13:10 (q4): el 31-oct y el 1-nov la oferta tenía 25 min (fetched 12:44). Del
  2 al 9-nov el escenario de las 12:45 no tiene oferta (escenario negativo de transición).
- `journalctl -u amazon-prices-sync` (`servidor.txt`): la hora en que se guardó US en cada corrida de
  las 12:35 (tabla de arriba).
- `estimacion.log` del 9-nov, corrida de las 12:45: `ofertas nuevas=328` (387 en las otras),
  `168x oferta_desactualizada` (112 en las otras), y `rc=0`.

## Por qué nadie se enteró

La ingesta sale 0 aunque una plataforma entera quede sin oferta fresca, y cron-avisa solo avisa con
salida distinta de 0. El aviso `no_evaluado` del motor de precios avisa una vez por racha
(`precio_aviso_dias_sin_evaluar` = 3) y señala la oferta, no la copia del bridge.

## Arreglo (F.2)

1. `tools/refresh_estimacion.sh` espera a que `amazon-prices-sync.service` deje de estar
   `activating` (hasta 20 min) antes de copiar `bridge.db`. Con la peor hora vista (12:51:45) y la
   ingesta de unos 8 min (12:45 a 12:52 el 31-oct y el 1-nov), la estimación queda escrita hacia las
   13:00, antes de la corrida de las 13:10. Si a los 20 min sigue `activating`, o su estado no se
   puede leer, copia lo que hay y sale 1 con la causa en `estimacion.log`, y cron-avisa avisa.
2. `python -m app.cli ingest estimacion` sale 1 y nombra la plataforma Amazon cuya oferta más nueva
   en la copia ya pasó las 6 h. cron-avisa, que ya envuelve `refresh_estimacion.sh`, avisa por
   Telegram una vez y avisa otra cuando vuelve a correr bien.
3. `tools/despliegue/desplegar.sh` instala `tools/refresh_estimacion.sh` en
   `/mnt/data/appdata/orbit/refresh_estimacion.sh`. Hasta hoy esa copia se ponía a mano (última vez
   el 8 de septiembre). El respaldo la guarda, la reversa la restaura y `checklist.sh` exige que sea
   igual a la de `tools/`.

`FRESHNESS_LIMIT` no cambia: a las 13:10 una oferta de las 06:36 sí está vieja.

## Prueba real (`prueba.sh`)

Comprueba en producción, solo lectura, lo que el despliegue cambia ese mismo día:

1. El script que corre el cron espera a `amazon-prices-sync` y es el de `tools/`.
2. La unidad existe.
3. La ingesta desplegada distingue las dos copias. `salida_ingesta.py` corre por stdin dentro de
   `orbit-app-1` el camino real completo: `main`, `ejecutar_ingesta`, la lectura de una SQLite
   temporal con `amazon_listing_prices` y `plataformas_sin_oferta_fresca`. Con US de hace 6 h 33 min
   (lo que vio la corrida de las 13:10 del 9-nov) debe salir 1 y nombrar `amazon_us`. Con US de hace
   2 min debe salir 0 sin aviso. Solo se sustituyen las fronteras externas: la conexión a Postgres
   (entrega un listing de MX y uno de US y rechaza cualquier otra sentencia), Product Fees y MeLi.
   No lee ni escribe la base, el bridge ni Amazon; la SQLite vive en un directorio temporal que se
   borra al salir.
4. Esa salida llega al aviso: el cron de root envuelve el script en cron-avisa y el script sale con
   el código de la ingesta.

Salida del 2026-11-09 antes del despliegue:

```
FALLA /mnt/data/appdata/orbit/refresh_estimacion.sh no espera a amazon-prices-sync o no es el de tools/
OK    amazon-prices-sync.service existe (LoadState=loaded)
FALLA la ingesta desplegada no distingue la copia vieja de la fresca: ... skip_reason: 2x oferta_persistencia
FALLA la salida 1 de la ingesta no llega a cron-avisa (cron sin cron-avisa o script que no propaga el codigo)
== F falta en produccion: 3 falla(s)
```

La primera corrida de las 13:10 después del despliegue se puede confirmar con q1 de `consultas.txt`:
amazon_us debe tener decisiones distintas de `oferta_desactualizada`.
