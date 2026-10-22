# SP-API 02 - el freno de Amazon ya no tumba una descarga
autopiloto: motor
plan: sp-api-02
repo: git@github.com:gon0801/goncloud-Orbit.git

<!-- Origen: el 2026-10-22 a las 05:02 UTC, la descarga de inventario FBA de Amazon MX (run 1005)
recibió dos 429 seguidos en la página 9 de 22 y se selló ok=false sin escribir nada. Llegó la
alerta "[Orbit] ALERTA fallo SP-API ... http_429". La de US corrió justo después y salió completa.
Es la primera falla de inventario en la semana. El sistema de contabilidad corre a la misma hora,
pero solo llama a Orders, que tiene su propio límite.

La causa en el código: `SpapiClient.get` y `SpapiClient.post_fees` reintentan un 429 una sola vez,
con la espera de `Retry-After` o 1 s (política sellada en SP-API 01 A.1). Las ocho descargas
diarias pasan por ahí. Orbit no guarda cuánto pidió esperar Amazon.

Regla de David (memoria "Ingesta: cero fallas"): una corrida ok=false es un error, no ruido.
Reintento, paciencia y aviso. David autorizó este plan el 2026-10-22 y con él cambia la política
de A.1.

Qué cambia para David: un freno pasajero de Amazon ya no deja un día sin datos. Si una descarga
falla de todos modos, la corrida nocturna la repite una vez al final. Si la repetición la salva,
llega un aviso de "se recuperó".

Límites que no se tocan (docs/DEPLOY.md, SP-API 01 A.5): la corrida nocturna arranca a las 05:00
UTC, corre en serie y debe terminar antes de la sincronización de estructura de las 06:45 UTC.
Los precios de MX ya usan unos 23 min de un espacio de 25. Ninguna escritura a Amazon.

Reglas para quien implementa:
1. Cada arreglo lleva en el mismo commit una prueba que falla sin el arreglo.
2. Las Acepta corren solo las pruebas de su fila. La batería completa corre en CI.
3. Precedencia: docs/CONTEXTO.md > docs/DEPLOY.md > este plan. -->

## Bloque A - el cliente aguanta el freno de Amazon y lo deja anotado
lane: gate
depende: -
aviso: un freno pasajero de Amazon ya no tumba una descarga, y el motivo queda anotado
prueba-real: uv run pytest -q tests/test_spapi_client.py tests/test_spapi_salud.py
| Fila | Contenido | Acepta |
| --- | --- | --- |
| A.1 | En `app/spapi/client.py`, un 429 se reintenta hasta 5 veces en `get` y en `post_fees`. La espera de cada reintento es la mayor entre `Retry-After` y 2, 4, 8, 16 y 32 s. La suma de esperas de una misma llamada no pasa de 120 s. Pasado eso, la llamada devuelve el 429, como hoy. Con `limitador`, cada reintento sigue consumiendo cuota. Reescribe la sección "Política de reintentos" del docstring del módulo con la regla nueva. En `tests/test_spapi_client.py`, cambia `test_429_un_reintento_y_persistente_no_loop` por tres pruebas. Tres 429 y luego 200 devuelven 200 tras esperar 2, 4 y 8 s. Un `Retry-After: 30` manda sobre la espera fija. Un 429 que nunca cesa devuelve 429 tras 6 intentos, sin pasar de 120 s de espera. | uv run pytest -q tests/test_spapi_client.py |
| A.2 | Cada 429 deja una línea en el log con `logger.warning`. La línea dice la ruta sin parámetros, el número de intento, la espera aplicada y el valor de `Retry-After`, o "sin Retry-After" si Amazon no lo mandó. Nunca lleva headers ni cuerpo. Así queda en `logs/spapi-diario.log` cuánto pidió esperar Amazon. Prueba en `tests/test_spapi_client.py` con `caplog`. | uv run pytest -q tests/test_spapi_client.py |
| A.3 | En `app/spapi/salud.py`, `evaluar_alertas` agrega el flanco "recuperada". Si la corrida recién sellada es `ok=true` y la anterior sellada de la misma fuente y plataforma fue `ok=false`, manda un aviso nuevo, `notifica_spapi_recuperada`, en `app/notifica.py`. El aviso es una línea llana con la fuente, la plataforma y que ya se recuperó. Sigue sin fallar nunca: un error del aviso no rompe la ingesta. Pruebas en `tests/test_spapi_salud.py`. Fallida y luego ok avisa una vez. Ok y ok no avisa. Fallida, fallida y ok avisa una vez. | uv run pytest -q tests/test_spapi_salud.py |

## Bloque B - la corrida nocturna repite una vez lo que falló
lane: release
depende: A
aviso: la corrida nocturna de Amazon ya repite al final lo que haya fallado, y el cliente nuevo está en producción
prueba-real: ssh gonserver 'sudo docker exec orbit-app-1 python -m app.cli ingest spapi_inventario --platform amazon_mx'
despliegue: desplegar=docs/evidencia/sp-api-02/ejecucion/B.3/desplegar.sh checklist=docs/evidencia/sp-api-02/ejecucion/B.3/checklist.sh rollback=docs/evidencia/sp-api-02/ejecucion/B.3/rollback.sh
| Fila | Contenido | Acepta |
| --- | --- | --- |
| B.1 | Versiona el guion de la corrida nocturna en `scripts/spapi-diario.sh`. Hoy vive solo en el servidor, en `/mnt/data/appdata/orbit/spapi-diario.sh`, y en `docs/DEPLOY.md`. Conserva el orden, la serie y el "una falla no cancela las siguientes". Agrega la repetición: cada fuente y plataforma cuya ingesta sale distinta de 0 se repite una vez, al final, en el mismo orden. Una repetición que arrancaría después de las 06:15 UTC no corre, y queda en el log "no repito <fuente> <plataforma>: ya son las HH:MM UTC". La hora sale de `date -u`, y en las pruebas de `SPAPI_DIARIO_AHORA` (HH:MM). Actualiza la sección del wrapper en `docs/DEPLOY.md` para que apunte al archivo versionado. | bash -n scripts/spapi-diario.sh |
| B.2 | Pruebas en `tests/test_spapi_diario.py`. Corren `scripts/spapi-diario.sh` con un `docker` falso al frente del PATH, que falla para las combinaciones que la prueba elija. Una falla de `spapi_inventario amazon_mx` se repite una vez al final y nada más. Si todo sale bien, no hay repeticiones. Con `SPAPI_DIARIO_AHORA=06:20`, no se repite nada y el log dice por qué. Una repetición que vuelve a fallar no se repite otra vez. | uv run pytest -q tests/test_spapi_diario.py |
| B.3 | Escribe la receta de despliegue en `docs/evidencia/sp-api-02/ejecucion/B.3/`, con el patrón de `docs/evidencia/ingesta-01/ejecucion/D.1/` y el contrato del motor. El motor corre cada script con `bash <script>`, sin argumentos, desde una copia del commit mergeado, con `MOTOR_SHA`. `desplegar.sh` toma el SHA de `MOTOR_SHA`. Espera a que Quality termine en ese commit (hasta 12 min) en lugar de abortar. Respeta las guardas de `ingesta-01` (lock de la ingesta diaria, ciclo `running`, `app.cli` corriendo) esperando hasta 10 min. Despliega el código, copia `scripts/spapi-diario.sh` a `/mnt/data/appdata/orbit/spapi-diario.sh` con `chmod +x` y guarda el respaldo `predeploy-<STAMP>`. `checklist.sh` sale 0 solo si el `app/spapi/client.py` del contenedor y el guion del servidor tienen el mismo md5 que en `MOTOR_SHA`, y la línea `job_key=spapi:diario` sigue en el crontab de gon. `rollback.sh` restaura el respaldo `predeploy` más reciente sin pedir argumentos. Los tres son solo lectura con Amazon. | bash -n docs/evidencia/sp-api-02/ejecucion/B.3/desplegar.sh && bash -n docs/evidencia/sp-api-02/ejecucion/B.3/checklist.sh && bash -n docs/evidencia/sp-api-02/ejecucion/B.3/rollback.sh |

## Bloque C - cierre: ledger y documentos al día
lane: fast
depende: B
aviso: SP-API 02 quedó cerrado y documentado
prueba-real: python3 -c "import json;json.load(open('plans/manifest.json'))"
| Fila | Contenido | Acepta |
| --- | --- | --- |
| C.1 | Agrega `sp-api-02` a `plans/manifest.json` y a `plans/ROADMAP.md` como cerrado. Anota en `docs/CHAT-CONTEXT.md` la regla nueva de reintento del 429, la repetición nocturna y el aviso de recuperación. Pega en `docs/evidencia/sp-api-02/ejecucion/C.1/NOTA.md` la salida de la prueba real del bloque B y las líneas de `logs/spapi-diario.log` de la primera corrida nocturna con el código nuevo, si ya ocurrió. Si no ocurrió, deja dicho que se revisa en la corrida del día siguiente. | python3 -c "import json;json.load(open('plans/manifest.json'))" && test -n "$(git diff --name-only origin/master -- docs/CHAT-CONTEXT.md plans/ROADMAP.md plans/manifest.json)" |
