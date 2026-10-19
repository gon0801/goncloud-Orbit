# R1-cod: el POST incierto no sella fallido

SHA del arreglo: `9230f008acb589db808b00478333c906a28d7b75`.
Padre de esta evidencia: `dd7d8e84dae8c305bca7a0a1bb963e2f2f0665d2`.
Este archivo es el ultimo commit del encargo. El SHA final es el de `git rev-parse HEAD` al leer el commit que lo introduce.

## Commits

- `f94a0e8730a29bd89921427f86be1ffc6c423f5e` JEV ADS 03 R.1: pruebas del post incierto
- `9230f008acb589db808b00478333c906a28d7b75` JEV ADS 03 R.1: un 5xx o un corte de red no sella fallido
- `dd7d8e84dae8c305bca7a0a1bb963e2f2f0665d2` JEV ADS 03 R.1: paquete de produccion solo codigo
- el commit de esta evidencia

Sin push. La rama sigue siendo `j03/r1-incierto`.

## Que cambia para el dueno

Si Amazon acepto el negative y la respuesta se perdio (5xx, corte de red, tiempo agotado, cuerpo ilegible), la fila de Tus bloqueos ya no dice fallido. Queda en `intencion` con motivo `por_confirmar`, o en `aplicado` con el id si el LIST lo ve ENABLED. Un 4xx sigue en `fallido` con `http_<status>`. La pantalla no cambio de forma: la fila ya muestra el paso y el motivo.

La reconciliacion que ya existia (`_reconcilia_sin_candado`) cierra esa intencion abierta. No se toco su logica. La siguiente accion sobre la misma clave, o `python -m app.cli bloqueo-reconcilia`, la adopta si el negative ya esta ENABLED, o la sella `fallido` / `sin_resultado` si no esta. Eso ultimo es la conducta vieja de la reconciliacion, no de este POST.

## Cambio de diseno pedido por R.1

`test_ledger_antes_del_http_y_reconciliacion` fijaba `fallido` / `sin_respuesta` cuando el POST levantaba `ConnectError`. Ahora exige `intencion` / `por_confirmar` y un solo paso `intencion`. No habia una prueba de conducta que fijara `fallido` con 5xx ni `fallido` / `readback` en este camino. El `http_500` de `test_pausa_no_cuenta_anulados_ni_fallidos_de_amazon` es un fixture de conteo y no se movio.

Con el codigo de partida (`331af501`), (a), (b), (d) y (e) quedan en rojo. (c) ya pasaba. Ultima linea de esa corrida: `7 failed, 1 passed, 17 deselected in 3.19s`.

```
test_ledger_antes_del_http_y_reconciliacion
  AssertionError: assert 'fallido' == 'intencion'
test_r1_a_transporte_y_list_enabled_queda_aplicado
  AssertionError: assert 'fallido' == 'aplicado'
test_r1_b_5xx_y_list_enabled_queda_aplicado
  AssertionError: assert 'fallido' == 'aplicado'
test_r1_d_5xx_sin_list_queda_intencion_y_reconcilia
  AssertionError: assert (200, 'fallido', 'sin_respuesta') == (200, 'intencion', 'por_confirmar')
test_r1_d_lista_no_ok_deja_la_intencion_abierta
  AssertionError: assert (200, 'fallido', 'sin_respuesta') == (200, 'intencion', 'por_confirmar')
test_r1_e_post_ok_sin_readback_queda_intencion
  AssertionError: assert (200, 'fallido', 'readback') == (200, 'intencion', 'por_confirmar')
test_r1_e_post_ok_lista_ambigua_queda_intencion
  AssertionError: assert (200, 'fallido', 'readback') == (200, 'intencion', 'por_confirmar')
```

## Como quedo el POST

Tres salidas, en `app/bloqueo_dueno_efecto.py`:

1. `AdsApiErrorMutacion` con status 4xx sella `fallido` y `http_<status>`, como antes. No hay readback.
2. Un 5xx, o cualquier otra excepcion del POST, no sella `fallido`. Se anota en el log y se hace el readback.
3. Un POST que salio bien tambien hace el readback.

Si el LIST trae el negative ENABLED con id, `aplicado`. Si no aparece, o la lista no sale `ok`, no hay paso nuevo. La respuesta es 200 con `paso="intencion"` y `motivo="por_confirmar"`.

El status y el cuerpo no caben en `bloqueo_efecto` sin migracion, y este arreglo no lleva migracion. Van al log con `scrub`. `_send_with_retries` tiraba el cuerpo al lanzar `AdsApiError` en un POST no idempotente (5xx o red). `AdsApiError` ahora guarda `status` y `cuerpo` para que ese log no nazca vacio. El mensaje de la excepcion sigue sin cuerpo. No se reintenta.

## Baterias

Postgres del loop en el puerto 5436 respondio. No se arranco ni se apago.

Comando: `ORBIT_TEST_DSN` del loop, `.venv/bin/python -m pytest -q tests/test_bloqueo_dueno_efecto.py`

Ultima linea: `25 passed in 3.37s`

Comando: el mismo interprete, `tests/test_bloqueo_dueno_efecto.py tests/test_ads_client.py tests/test_ads_write.py tests/test_jev_bloqueo_api.py`

Ultima linea: `133 passed, 1 warning in 4.84s`

Ruff sobre los tres archivos de codigo: `All checks passed!`

## Mutantes

Cada uno se aplico, la prueba fallo por el desacuerdo predicho, y se revirtio. Ninguno quedo en el arbol.

| Mutante | Prueba | Resultado |
| --- | --- | --- |
| el `except Exception` del POST hace `raise` en vez de anotar | `test_r1_a_transporte_y_list_enabled_queda_aplicado` | `assert 'fallido' == 'aplicado'` |
| si la excepcion trae `status`, sella `fallido` / `http_500` antes del readback | `test_r1_b_5xx_y_list_enabled_queda_aplicado` | pasos `fallido` + `aplicado`, no solo `aplicado`. (a) siguio en verde |
| el corte de 4xx pasa a `500 <= status < 500` | `test_r1_c_4xx_sella_fallido_http` | `paso: 'aplicado' != 'fallido'` |
| la reconciliacion exige `PAUSED` en vez de `ENABLED` | `test_r1_d_5xx_sin_list_queda_intencion_y_reconcilia` | `assert 'fallido' == 'aplicado'` |
| el readback que no ve el negative devuelve `fallido` / `readback` | `test_r1_e_post_ok_sin_readback_queda_intencion` y la (d) de lista vacia | `(200, 'fallido', 'readback')` != `(200, 'intencion', 'por_confirmar')` |
| una lista que no sale `ok` se trata como un negative ENABLED inventado | `test_r1_d_lista_no_ok_deja_la_intencion_abierta` y `test_r1_e_post_ok_lista_ambigua_queda_intencion` | `(200, 'aplicado', None)` != `(200, 'intencion', 'por_confirmar')` |

## Paquete de produccion

`docs/evidencia/jev-ads-03/ejecucion/R.1/`: `desplegar.sh`, `checklist.sh`, `rollback.sh`, `programar-reconcilia.sh`. Solo codigo. Sin migracion. Nada se desplego ni se programo.

`desplegar.sh` parte de S.5. Guardas de ciclo (`optimizer_cycle.status = 'running'`) y de harvest (cola y `harvest_job` en vuelo), mas `app.cli` en el contenedor. `git archive` de lo que copia el Dockerfile, mas Dockerfile y compose. md5 contra el SHA, que en real tiene que ser `origin/master` con CI verde. Digest y health al recrear. El cron no se instala ahi.

`checklist.sh` exige `GET /cortes` 200 y `bloqueo-reconcilia` con exit 0.

`programar-reconcilia.sh` simula por omision. `--acepto-mutacion-real` escribe. `--quitar` saca solo la linea de bloqueo-reconcilia. La linea, si no esta, es:

```
17 * * * * /usr/bin/flock -n /tmp/orbit-bloqueo-reconcilia.lock docker exec orbit-app-1 python -m app.cli bloqueo-reconcilia >> /mnt/data/appdata/orbit/logs/bloqueo-reconcilia.log 2>&1
```

### Simulacion de desplegar.sh

Exit 0. El SHA pasado fue el de partida, para probar el script, no el arreglo (el arreglo aun no estaba en ese commit cuando corrio el archive).

```
== 0) SHA aprobado = origin/master con la bateria verde
SIMULACION: 331af50194471188ae472ac6268bf409e48c6d48 existe local; sin fetch ni gh
== 1) Preflight (cli=0 y ok|0|0|0: nadie corre, sin ciclo, sin harvest en vuelo)
-- guardas de ciclo y harvest:
SELECT 'ok',
       (SELECT count(*) FROM optimizer_cycle WHERE status = 'running'),
       (SELECT count(*) FROM apply_queue
         WHERE kind = 'harvest' AND estado NOT IN ('applied', 'failed', 'vetoed', 'discarded')),
       (SELECT count(*) FROM harvest_job
         WHERE fase IN ('pending', 'negative_created', 'exact_created', 'hermanas_negadas'));
SIMULACION cli=0 ok|0|0|0
== 2) Respaldo del codigo actual, compose y SHA (el .env NO se copia)
SIMULACION: respaldo local predeploy-20261019-0209 (sin ssh, sin .env)
== 3) Copiar el codigo del SHA (git archive de lo que copia el Dockerfile)
SIMULACION: archive extraido en local, sin ssh
== 4) md5 contra el SHA (en real, origin/master)
md5 OK 194 archivos
== 5) Guardas antes de recrear (reintento 30s hasta 30min)
SIMULACION: guardas cli=0 ciclo=0 harvest=0, sin espera
== 6) Build + recrear app (digest antes/despues) + health
SIMULACION DIGEST antes=no-tocado
SIMULACION: no se recrea el contenedor
SIMULACION DIGEST despues=no-tocado
SIMULACION health=ok
== LISTO. STAMP=20261019-0209
```

### Simulacion de checklist.sh

Exit 0.

```
== 0) El servidor contesta
SIMULACION: sin docker inspect
OK    orbit-app-1 corriendo = true
== Checklist R.1 (sello 20261019-0209, modo 1)
== 1) HTTP: health y /cortes
OK    GET /health = 200
OK    GET /cortes = 200
== 2) bloqueo-reconcilia sale 0
SIMULACION: python -m app.cli bloqueo-reconcilia no se ejecuta en prod
OK    bloqueo-reconcilia exit = 0
== RESULTADO: 0 falla(s), 0 sin medir
```

### Simulacion de rollback.sh

Exit 0.

```
== 0) Respaldo existe
SIMULACION SHA=331af50194471188ae472ac6268bf409e48c6d48
== 0b) Guardas (solo con el contenedor arriba, sin app.cli)
SIMULACION: guardas no se miden; no hay contenedor que interrumpir
== 1) Restaurar el codigo de predeploy-20261019-0209 y reconstruir
(R.1 no toca el cron aqui. Reversa de la linea: programar-reconcilia.sh --quitar)
SIMULACION DIGEST antes=no-tocado
SIMULACION: no se restaura el servidor ni se reconstruye
SIMULACION DIGEST despues=no-tocado
SIMULACION health=ok
== REVERSA LISTA.
Sin migracion que deshacer. Cron intacto en este script.
```

### Simulacion de programar-reconcilia.sh

Tres corridas, todas exit 0. La segunda no duplica. `--quitar` deja la linea del vigilante.

```
se agrega la linea de bloqueo-reconcilia
== crontab resultante
# job_key=spapi:vigilante  aviso si el cron spapi:diario no dejo sus 8 corridas
30 7 * * * /usr/bin/flock -n /tmp/spapi-vigilante.lock docker exec orbit-app-1 python -m app.cli spapi_vigilante >> /mnt/data/appdata/orbit/logs/spapi-vigilante.log 2>&1
17 * * * * /usr/bin/flock -n /tmp/orbit-bloqueo-reconcilia.lock docker exec orbit-app-1 python -m app.cli bloqueo-reconcilia >> /mnt/data/appdata/orbit/logs/bloqueo-reconcilia.log 2>&1
SIMULACION: sin --acepto-mutacion-real no se toco el crontab de gon

ya estaba: no se duplica
== crontab resultante
# job_key=spapi:vigilante  aviso si el cron spapi:diario no dejo sus 8 corridas
30 7 * * * /usr/bin/flock -n /tmp/spapi-vigilante.lock docker exec orbit-app-1 python -m app.cli spapi_vigilante >> /mnt/data/appdata/orbit/logs/spapi-vigilante.log 2>&1
17 * * * * /usr/bin/flock -n /tmp/orbit-bloqueo-reconcilia.lock docker exec orbit-app-1 python -m app.cli bloqueo-reconcilia >> /mnt/data/appdata/orbit/logs/bloqueo-reconcilia.log 2>&1
SIMULACION: sin --acepto-mutacion-real no se toco el crontab de gon

reversa: se quita la linea de bloqueo-reconcilia
== crontab resultante
# job_key=spapi:vigilante  aviso si el cron spapi:diario no dejo sus 8 corridas
30 7 * * * /usr/bin/flock -n /tmp/spapi-vigilante.lock docker exec orbit-app-1 python -m app.cli spapi_vigilante >> /mnt/data/appdata/orbit/logs/spapi-vigilante.log 2>&1
SIMULACION: sin --acepto-mutacion-real no se toco el crontab de gon
```

## Pendiente

- Sin push. El coordinador comenta y abre el PR.
- Nada se desplego ni se programo en goncloud. La simulacion de desplegar, checklist y rollback no midio el servidor: imprime el contrato y no hace ssh.
- La bateria completa del repo no corrio en esta sesion. Corrieron las pruebas del bloqueo y las del cliente de Ads que toca el cambio.
- La linea de cron, cuando se instale de verdad, usa flock en el host. Este encargo no la escribe.
