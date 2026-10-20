# INGESTA 01 — la descarga diaria de Amazon Ads no falla

Estado: plan del 2026-10-19. El dueño lo pidió ese día en el chat con el lead:
"no debería de fallar ni uno". Diagnóstico medido en
`docs/evidencia/ingesta-01/diagnostico/README.md`. Base de planificación:
`origin/master` en `98f97861`. `team_validation_mode: manual-pass`.

**Resultado:** cada mañana Orbit trae de Amazon MX y US las métricas de
campañas, de productos y de ubicaciones, aunque Amazon tarde horas. Pide todos
los reportes a la vez, guarda cada grupo en cuanto llega, vuelve a pedir el que
se atora y, si algo falla, lo intenta otra vez a las 10:15 y a las 13:15 UTC.
El ciclo, el vigía del impulso, los avisos de campaña y las señales de Jev no
actúan sin las métricas de ayer: esperan al siguiente horario. Por Telegram:
las métricas de campañas siguen con el aviso de `app/ads/salud.py` que ya
existe (falla, atraso a las 10:30, recuperada); productos y ubicaciones ganan
mensajes en lenguaje llano con qué faltó, cuándo se reintenta y si se salvó.

**Precedencia:** `docs/CONTEXTO.md` > `docs/DEPLOY.md` > este plan. La página
"Desempeño de Ads" (rediseño de Resumen) es otro plan y va después de este.

## Contratos

- Lo que se escribe no cambia: mismas tablas, mismos `source`
  (`amazon_ads_reports_v3`, `amazon_ads_products_v3`, `amazon_ads_placements_v3`),
  dedupe por `source_report_id`, una `ingest_run` por `source` con `ok=true`
  solo si todos sus reportes se escribieron. Ninguna transacción abierta
  mientras se sondea: cada grupo se escribe en su propia transacción corta en
  cuanto sus reportes están descargados, mientras los otros siguen en cola.
- Un grupo que falla no tumba a los otros.
- Ninguna escritura a Amazon: crear un reporte no toca campañas. Por eso este
  plan se aparta de `docs/CONTEXTO.md` ("POSTs de creación no-idempotentes
  fail-closed") solo para `POST /reporting/reports`: un duplicado cuesta cuota,
  no dinero, y se repite tras 5xx o error de red.
- Un reporte reemplazado por un repetido **no** se registra `failed` en
  `ads_report_result` (abriría un incidente en `app/ads/salud.py` y taparía el
  `written`): va al log y al resumen de la corrida.
- Módulos: `app/ads/descarga.py` (nuevo, ≤900) tiene la fase de API de los tres
  grupos y no importa `reports` ni `placements`: recibe de ellos la configuración
  de cada reporte y su `al_completar`. `app/ads/reports.py` (2157 líneas, en la
  allowlist) y `app/ads/placements.py` importan `descarga`, conservan el parseo y
  la fase de DB, y solo pierden código. `app/metricas_listas.py` (nuevo) tiene la
  guarda. `app/cli.py` (812 de 900) solo enruta.
- Reloj: todo en UTC; el servidor goncloud está en `Etc/UTC`. El rango D-31..D-1
  sale de `dt.datetime.now(dt.UTC)` como hoy en `_rango_desde_args`. "Hoy" de
  las guardas y de `--solo-faltantes` sale de `now()` de la DB.

## Horarios (UTC, crontab de `gon` en goncloud)

| Hora | Qué |
| --- | --- |
| 07:10 | `ingest diario` (plazo de sondeo 2 h 30 min; termina a más tardar 09:55) |
| 08:05 · 08:20 · 08:40 · 08:41 | avisos-campana · impulso-vigia · cycle US · cycle MX (como hoy, con guarda) |
| 09:30 · 21:30 | jev-senales (como hoy, con guarda) |
| 10:05 · 10:08 · 10:10 · 10:11 | reintento de avisos · vigía · cycle US · cycle MX |
| 10:15 | `ingest diario --solo-faltantes` (termina a más tardar 13:00) |
| 13:05 · 13:08 · 13:10 · 13:11 | reintento de avisos · vigía · cycle US · cycle MX |
| 13:15 | `ingest diario --solo-faltantes` (termina a más tardar 16:00) |
| 16:05 · 16:08 · 16:10 · 16:11 | último reintento de avisos · vigía · cycle US · cycle MX, con la misma guarda |

Las tres corridas de ingesta usan `flock -n /tmp/orbit-ingesta-diaria.lock`.
`jev-senales` no lleva reintento: si la guarda la salta a las 09:30, su corrida
de las 21:30 es el respaldo (Jev propone, no cambia pujas). Un día en que las
tres descargas fallan, el vigía del impulso tampoco corre: los presupuestos
diarios de las campañas de impulso siguen topando el gasto en Amazon.
No cambian: `ingest structure` a las 06:45 y `ads-salud` `*/10 10-12`; el resto
del crontab de `gon` (contabilidad, precio, spapi, reputación,
bloqueo-reconcilia, bridge) queda igual.
La contabilidad (`/mnt/data/appdata/accounting/`, 00/06/12/18) también pide
reportes a la cuenta; no es de este repo.

## Tareas y dependencias

Evidencia en `docs/evidencia/ingesta-01/ejecucion/<id>/`. La sección A es una
rama y un PR con un commit por fila; D y C van en su PR. Cada PR vive en draft
durante la implementación y la revisión (solo corre `rapido`); el lead lo revisa
según la regla 4 de quality-kit y, tras su APROBADO, el PR pasa a ready y ahí
corre `completa` una vez sobre el SHA final (flujo de `CLAUDE.md`).

### Sección 0 — sonda (en paralelo con A; no bloquea)

| Task | Contenido | DoD | Depends | Status |
| --- | --- | --- | --- | --- |
| S.0 | `[stage:medicion] [lane:fast]` Script `ejecucion/S.0/sonda.py`, corrido con `docker exec -i orbit-app-1 python - < sonda.py` a partir de las 19:00 UTC (lejos de las descargas de Orbit y de las de contabilidad de 12:00 y 18:00), y nunca con `/tmp/orbit-ingesta-diaria.lock` tomado. Usa `client = AdsClient(AdsCredentials.from_secrets_dir())` de producción y manda los POST con `httpx` y `client._build_headers(client._ensure_token(), profile_id, path=..., method="POST")` para guardar código y cuerpo crudos (el cliente de hoy los descarta en un error). (a) Pide a la vez los 12 reportes del día (4 de métricas, productos y placements, por perfil, D-31..D-1) y sondea cada 20 s hasta 2 h 30 min. (b) Mientras siguen en cola, repite el de campañas MX idéntico y luego con `name` + `-r1`. (c) Pide 24 más en una ráfaga y cuenta los 429. (d) Al terminar, los mismos 4 de métricas MX uno tras otro. No escribe en la DB. | `salida.txt`: minutos hasta COMPLETED por reporte en (a) y (d), códigos y cuerpos de (b) y (c), todo sin tokens. Con eso el lead confirma o ajusta en la revisión de A.1 los plazos y el sufijo del repetido. | - | cc:TODO |

### Sección A — construcción

| Task | Contenido | DoD | Depends | Status |
| --- | --- | --- | --- | --- |
| A.1 | `[stage:implementacion] [lane:gate] [tdd:required]` `app/ads/client.py`: la rama genérica `>= 400` de `_request` llena los campos `status` y `cuerpo` que `AdsApiError` ya tiene (#476), con el cuerpo redactado por `scrub`. `app/ads/descarga.py`: `descarga(conn, client, grupos, rango, *, reloj, sleep, al_completar)` pide todos los reportes de todos los grupos y perfiles a la vez (un solo `evaluar_perfiles`) y los sondea en una vuelta: 5 s los primeros 2 min y 30 s después; plazo de sondeo 2 h 30 min. Cada reporte COMPLETED se descarga en ese momento; cuando un grupo tiene todos los suyos, `al_completar` lo escribe en su transacción. Repetir un reporte (nuevo POST con `name` + `-r1`, `-r2`): si sigue en `PENDING`/`PROCESSING` 40 min, si queda `FAILED`, o si el POST falla por 5xx, red o 429 agotado; máximo 2 repetidos; el original se sigue sondeando y gana el primero en COMPLETED. Un 425 usa el `reportId` de `cuerpo` (`"The Request is a duplicate of : <id>"`, forma documentada; se ajusta a S.0 si difiere). Un GET que falla deja el reporte pendiente para la siguiente vuelta. Si falla `evaluar_perfiles`, las tres `ingest_run` se sellan `ok=false` con `global_failed`. `sync_metrics`, la corrida de productos y `sync_placements` delegan su fase de API en `descarga`; `esperar_reporte` e `INTENTOS_POLL*` se borran. | Pruebas con cliente y reloj falsos. (1), (2), (3), (5), (6), (7) y (8) entran por `reports.sync_metrics`, que existe hoy, así que su rojo con el código de hoy es real; (4), (10) y (11) entran por `descarga.descarga`: (1) un reporte de 55 min se repite y la corrida sale ok; (2) `FAILED` se repite; (3) todos a 2 h 10 min salen ok; (4) un reporte que agota repetidos y plazo falla solo su grupo, los otros quedan `ok=true`; (5) cinco 429 seguidos al crear se repiten y sale ok; (6) 5xx al crear se repite; (7) el caso del 2026-10-19: US keywords a 27 min con 5 de 8 descargados sale ok; (8) 425 con `cuerpo` sondea el id de la respuesta; (9) ok con reemplazo no deja filas `failed` y `salud.incidentes_de_run` devuelve `[]`; (10) número exacto de GET para 12 reportes de 2 h, calculado en la prueba con el calendario 5 s/30 s; (11) metrics termina a los 20 min y se escribe antes de que placements termine. (1), (3), (5) y (7) en rojo con el código de hoy. Pruebas de `status` y `cuerpo` del 425 en `tests/test_ads_client.py`. Suben verdes `test_reports_pipeline.py`, `test_placements.py` y `test_ads_producto_ingesta.py` ajustados al borrado. | - | cc:TODO |
| A.2 | `[stage:implementacion] [lane:gate] [tdd:required]` `python -m app.cli ingest diario [--fecha F --fecha-fin F] [--solo-faltantes]` en `descarga.main`: rango por defecto D-31..D-1, una `descarga` para los tres grupos, tres `ingest_run`. `--solo-faltantes` salta un grupo solo si hoy ya tiene una `ingest_run` `ok=true` de su `source` que escribió filas con `metric_date` = D-1 para **cada** plataforma con perfil aceptado; si una plataforma falta, el grupo corre. Un grupo saltado no abre `ingest_run`. Tope de pared de la corrida: 2 h 45 min. Al terminar manda por `notifica` (si `canal_activo()`) un mensaje por grupo de productos o ubicaciones y plataforma que falló o se salvó (las métricas de campañas ya avisan por `salud`), con estos textos y `<qué>` = "las métricas por producto" o "las métricas por ubicación": falla con reintento pendiente: "Amazon no entregó a tiempo <qué> de <MX/US> (esperamos 2 h 30 min). Orbit lo vuelve a intentar a las <HH:MM> UTC; mientras, el ciclo de pujas espera."; con error: "Amazon respondió con error al pedir <qué> de <MX/US>. ..." (mismo cierre); se salvó: "Ya llegaron <qué> de <MX/US> en el intento de las <HH:MM> UTC."; agotado a las 13:15: "Hoy no llegaron <qué> de <MX/US> después de tres intentos. Mañana a las 07:10 se intenta otra vez." Los horarios salen de una constante `HORARIOS_INGESTA` que también fija la prueba del crontab (A.4). `app/ads/descarga.py` también corre como `python -m app.ads.descarga` (mismo `main`). `ingest metrics` con `--productos` y `--placements` sigue igual. | Pruebas del rango por defecto, de `--solo-faltantes` (salta el grupo listo, corre el fallido, corre el que trae D-1 de MX y no de US, no cuenta un backfill de septiembre iniciado hoy, el saltado no abre `ingest_run`), del tope de pared con reloj falso, de salida distinta de 0 con un grupo fallido y de cada texto literal. | A.1 | cc:TODO |
| A.3 | `[stage:implementacion] [lane:gate] [tdd:required]` `app/metricas_listas.py`: `metricas_de_ayer_listas(conn, platform) -> bool` es verdadero si una `ingest_run` `amazon_ads_reports_v3` `ok=true` iniciada hoy escribió en `ads_metric_observation` filas con `metric_date` = D-1 de entidades de esa plataforma. `cycle`, `impulso-vigia`, `avisos-campana` y `jev-senales` la consultan antes de abrir nada; sin ella imprimen `sin métricas de ayer: espera al siguiente horario` y salen 0. `cycle` tampoco corre si la plataforma ya tiene hoy un ciclo `done` o `degraded`, o uno `running` iniciado hace menos de 3 h; uno `failed`, `skipped` o `running` más viejo (huérfano de un proceso muerto, que el rastro de `cycle.py` cierra al ganar el claim) sí se reintenta. Los cuatro aceptan `--forzar` para correr a mano sin guarda. Antes de cerrar, seguir los imports de cada comando y buscar `ads_placement_observation` y `ads_product_metric_observation` (`avisos-campana` ya lee ubicaciones por `notifica.avisar_campanas` → `pantalla_dinero.lee_ubicaciones`): si un comando las lee, su guarda exige también la corrida ok de hoy de ese `source`. | Pruebas con Postgres real: sin corrida de hoy → cero envelopes, cero escrituras y cero mensajes en los cuatro; con corrida que no trae D-1 → igual; con corrida lista → corren; ciclo `done` o `degraded` hoy → no corre, `running` de hace 1 h → no corre, `running` de hace 4 h, `failed` o `skipped` → corre; `--forzar` hace lo que dice. La primera en rojo con el código de hoy. | - | cc:TODO |
| A.4 | `[stage:implementacion] [lane:gate] [tdd:required]` Crontab en el repo: reescribir en `docs/DEPLOY.md` la tabla de crons y el bloque instalador `ORBIT_BLOCK` (hoy, en `docs/DEPLOY.md:726`, filtra `Orbit (ORBIT 03`, `job_key=ingest:`, `job_key=ads-salud`, `job_key=ads_optimizer`, `app.cli ingest`, `app.cli ads-salud` y `app.cli cycle`; esos filtros se conservan) para que instale la tabla de Horarios más `ingest structure` (06:45) y `ads-salud`, con `job_key` por línea, y quitar las de 07:10/07:20/07:25. Las líneas de `avisos-campana`, `impulso-vigia` y `jev-senales`, que hoy viven fuera del bloque y sin `job_key`, pasan dentro: el filtro también borra `app.cli avisos-campana`, `app.cli impulso-vigia` y `app.cli jev-senales`, y las pruebas `test_instalador_no_borra_la_linea_de_*` de esos tres se invierten (el instalador las reemplaza, no las duplica). El filtro no toca ninguna otra línea. Ajustar las pruebas que fijan líneas (`rg -l -e 'job_key=' -e 'app.cli ingest' -e 'app.cli cycle' tests/`; hoy incluye `test_compose_deploy.py::test_cron_metricas_escapa_porcentaje_de_date`, `test_placements_cron.py`, `test_avisos_campana_cron.py`, `test_impulso_vigia_cron.py`, `test_jev_senales_cron.py` y `test_ads_salud_cron.py`). La línea de `ads-salud` no cambia. | Prueba nueva `tests/test_ingesta_cron.py`: sobre un crontab de ejemplo con el de hoy (líneas Orbit dentro y fuera del bloque, más contabilidad, precio y bridge), el instalador deja cada línea de la tabla una sola vez, ninguna vieja, intactas las ajenas, y los minutos coinciden con `HORARIOS_INGESTA`; las pruebas ajustadas en verde. | A.2, A.3 | cc:TODO |

### Sección D — despliegue

| Task | Contenido | DoD | Depends | Status |
| --- | --- | --- | --- | --- |
| D.1 | `[stage:cierre-pr] [lane:release]` `desplegar.sh`, `checklist.sh` y `rollback.sh` en `ejecucion/D.1/`, con el patrón de `docs/evidencia/jev-ads-03/ejecucion/D.1/`. Guarda técnica: no recrear `orbit-app-1` si `flock -n /tmp/orbit-ingesta-diaria.lock true` falla o hay un ciclo `running` iniciado hace menos de 3 h; en ese caso reintentar a los 15 min. Sin migración. Respaldo `crontab -l` en `ejecucion/D.1/crontab-antes.txt`, luego correr el bloque instalador de `docs/DEPLOY.md`. | Checklist con 0 fallas: md5 de archivos; en `crontab -l`, las líneas Orbit iguales a la tabla, cada una una sola vez, y las ajenas iguales a `crontab-antes.txt`; `python -m app.ads.descarga --help` (muestra `--solo-faltantes`), `cycle --help` (muestra `--forzar`) y un `python -` de solo lectura que imprime `metricas_de_ayer_listas` para MX y US. Nunca correr `cycle` en el checklist. Rollback ensayado: restaura la imagen y `crontab-antes.txt`. Telegram al dueño: quedó desplegado. | A.1-A.4 | cc:TODO |
| D.2 | `[stage:medicion] [lane:release]` `at` a 7 días con el patrón de `/mnt/data/appdata/orbit/recordatorios/`: por día y `source`, `ok`, duración, repetidos, en qué intento llegó, a qué hora corrió cada ciclo y cuántos horarios saltó la guarda en cada trabajo. | `.sql` probado sin enviar; `atq` lo muestra; no bloquea el cierre. | D.1 | cc:TODO |

### Cierre

| Task | Contenido | DoD | Depends | Status |
| --- | --- | --- | --- | --- |
| C.1 | `[stage:cierre-pr] [lane:fast]` Ledger al día, `docs/CHAT-CONTEXT.md`, `plans/manifest.json`, `plans/ROADMAP.md`. | Todas las filas marcadas como hechas, cada una con su evidencia. | D.2 | cc:TODO |

## Criterios medidos

| Paso | Criterio |
| --- | --- |
| Antes | 5 fallas en 15 días; corrida en serie de 22 a 138 min. |
| Después (D.2) | Cero días sin una `ingest_run` `ok=true` con D-1 de MX y US antes de las 16:10 UTC en cada `source` (`amazon_ads_reports_v3`, `amazon_ads_products_v3`, `amazon_ads_placements_v3`), y cero días con el ciclo saltado todo el día por la guarda. Un día salvado por un reintento no cuenta como falla, pero el reporte lo nombra con la hora en que corrió el ciclo. |
| Plazos | 2 h 30 min de sondeo, 40 min por reporte y 2 repetidos son del autor y caben en los horarios. Si S.0 muestra otra cosa, el lead los ajusta en la revisión de A.1 sin cambiar los horarios. |

## Orden y salida

Orden: S.0 en paralelo con A.1 y A.3 → A.2 → A.4 → D.1 → D.2 → C.1. El plan
cierra con D.1 desplegado, D.2 programado y el ledger al día. Este plan es la
autorización completa para desplegar y cambiar el crontab de producción.
