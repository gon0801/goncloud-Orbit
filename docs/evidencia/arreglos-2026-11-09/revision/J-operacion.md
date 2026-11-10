# J. Operación (cron, avisos, despliegue, base): resultado

Revisé la copia de master `3c841f4`, solo lectura. No usé ssh: el crontab real de hoy no lo
vi. Uso tres fuentes: las líneas que fija `docs/DEPLOY.md` (y que sus pruebas exigen), el
crontab capturado el 2026-10-21 (`docs/evidencia/ingesta-01/ejecucion/D.1/`) y las líneas
con `cron-avisa` capturadas el 4 y el 9 de noviembre (`estado-planes-2026-11-04/cron-vigente.txt`,
`claridad-01/F/servidor.txt`). Las sondas y sus salidas están en
`scratchpad/orbit07-review/sondas/J/`.

## Línea de tiempo UTC de un día (crontab de `gon` y de root, con la evidencia)

| UTC | Qué corre | Depende de | Cómo se protege |
|---|---|---|---|
| 00:45, 06:45, 12:45, 18:45 | `refresh_estimacion.sh` (root, `cron-avisa`) | el bridge `amazon-prices-sync` de :35 | desde #561 espera hasta 20 min al bridge |
| 03:30 | backup de Postgres (root) | — | fuera del repo |
| 05:00 | `spapi-diario.sh` (8 ingestas en serie, `flock -n`) | — | repite lo fallido hasta las 06:15; vigilante 07:30 |
| 06:45 | `ingest structure` | — | ciclo `degraded` si el estado tiene más de 48 h |
| 07:10 · 10:40 · 13:15 | `ingest diario --solo-faltantes` (sin flock) | — | `ads-salud` desde 10:30; Telegram de productos y ubicaciones |
| 07:30 | `spapi_vigilante` | spapi 05:00 | — |
| 08:05 | `avisos-campana` | métricas y ubicaciones de D-1 | guarda: sale 0 sin datos |
| 08:15 | `refresh_costos.sh` (costos, FX, ledger, MeLi Ads; `cron-avisa`) | syncs de contabilidad 06:30 y 08:00 | sale ≠0 si un pipeline falla |
| 08:20 | `impulso-vigia` | métricas D-1 | guarda: sale 0 sin datos |
| **08:40 / 08:41** | **ciclo US / MX (único horario)** | métricas D-1, estructura, costos de 08:15 | guarda D-1; sale 0 si no hay datos |
| 09:30, 21:30 | `jev-senales --aplicar` | métricas D-1 | `jev-vigia` 11:00 |
| 10:00–12:50 /10 | `ads-salud` (`cron-avisa --tras 3`) | ingesta | avisa atraso desde 10:30 |
| 11:30 · 12:15–12:25 | catálogo MeLi · muestras MX/US/MeLi | — | — |
| **13:10** + repasos 15:10–23:10 | precios Amazon (`cron-avisa`, `flock -E 75`) y MeLi (`flock -n`) | estimación 12:45, muestras, spapi | decide una vez al día; los repasos no vuelven a decidir |
| :17 cada hora | `bloqueo-reconcilia` | — | `flock -n` |

## Hallazgos confirmados

### H1. Si el ciclo de pujas o la corrida de precios dejan de correr, nadie recibe aviso
- Severidad: ALTO
- Frente: motor
- Dónde: `app/cli.py:113-115` (sin D-1 sale 0), `app/cli.py:130-136` (falla: sale 1 solo al log);
  `app/cycle.py:3971-3974` y `app/cycle.py:1591-1621` (`_sello_fallido`: sella `failed` sin
  aviso); `app/cli_precio.py:164-167` y `app/precio/corrida.py:1449` (`avisar` solo corre si la
  corrida termina bien); `app/ads/salud.py` (único vigilante de Ads: no lee `optimizer_cycle`);
  líneas documentadas sin `cron-avisa` en `docs/DEPLOY.md:1197-1199` (ciclo) y `:901-902` (precios
  MeLi); instalador del bloque en `docs/DEPLOY.md:1206`.
- Escenario: el ciclo de las 08:40 truena (error de base, credencial, regresión desplegada), o
  se salta porque faltan datos, o `docker exec` falla porque el contenedor no está. Qué pasa:
  sale 1 (o 0 si se saltó) hacia `optimizer.log` y ahí termina. No sale ningún Telegram y ningún
  vigilante revisa `optimizer_cycle`. La única pista es que ese día no llega el resumen del
  ciclo. Con precios MeLi pasa igual: una excepción a media corrida sale 1 al log sin aviso. Qué
  debería pasar: la regla del dueño en `plans/claridad-01.md:100` dice «una corrida que no corre
  es un bug, con aviso». Ingesta, SP-API y Jev ya tienen vigilante; el ciclo, que es lo que mueve
  bids, pausas y harvest, no tiene.
- Alcance: se dispara con el cron y los defaults de hoy cualquier día que algo falle, en live.
  Ya pasó dos veces con este mismo patrón: precios MeLi abortaron 6 veces al día del 22 al 25 de
  octubre y nadie se enteró hasta una revisión manual
  (`docs/evidencia/alertas-2026-10-25/revision.md`, «Corridas MeLi observadas»), y `jev-senales`
  falló 36 veces seguidas (unos 18 días) con salida 1 al log (`docs/evidencia/claridad-01/B/log-jev.txt`).
  Agravante: el procedimiento documentado para reinstalar el bloque Orbit quita las envolturas
  `cron-avisa` que ya existen en producción y deja las líneas sin aviso (sonda J5).
  Límite de lo que vi: si hoy el ciclo y precios MeLi ya van envueltos en `cron-avisa`, ese
  envoltorio cubre la salida 1. No cubre el salto con salida 0 (H2) ni el resumen que no llega.
- Evidencia (sondas ejecutadas):
  ```
  J2: cycle amazon_mx con fallo: exit=1 stderr="ciclo fallo (el envelope quedo sellado 'failed' cuando fue posible): server closed the connection unexpectedly (lectura de ventanas)" telegram_enviados=0
      _sello_fallido llama a notifica: False / corre_ciclo except llama a notifica: False
      app/ads/salud.py menciona optimizer_cycle: False
      linea documentada: 40 8 * * * docker exec orbit-app-1 python -m app.cli cycle --platform amazon_us >> .../optimizer.log 2>&1 | cron-avisa: False
  J3: precio meli con fallo a media corrida: exit=1 stderr='precio: precio_decision: product_id 375 no es miembro del listing 1556' avisar_precio_llamado=0 telegram_enviados=0
  J5: DESPUES de correr el instalador: */10 10-12 * * * docker exec orbit-app-1 python -m app.cli ads-salud >> .../ads-salud.log 2>&1 | cron-avisa: False
  ```
- Cómo reproducir: `cd sondas/J && PYTHONPATH=<copia> /Users/dn/dev/goncloud-Orbit/.venv/bin/python J2_ciclo_falla_sin_aviso.py <copia>`
  (igual con `J3_precio_falla_sin_aviso.py` y `J5_instalador_quita_cron_avisa.py`). En producción,
  solo lectura y no ejecutado: `ssh goncloud 'crontab -u gon -l | grep -E "app.cli (cycle|precio --platform meli|impulso-vigia)"'`.

### H2. El ciclo y el vigía del impulso tienen un solo horario: si la ingesta llega tarde, ese día no corren
- Severidad: MEDIO
- Frente: dinero y motor
- Dónde: `docs/DEPLOY.md:1192-1199` (ciclo solo a las 08:40/08:41, vigía 08:20, avisos 08:05);
  `app/cli.py:113-115`; `app/cli_bids.py:125-127`, que contradice su propio contrato en
  `app/cli_bids.py:102-103`; `app/metricas_listas.py:30-41`; `app/ads/diario.py:56-60` (la
  ingesta de las 07:10 puede seguir hasta las 09:55) y `:124-129` (el texto dice «mientras, el
  ciclo de pujas espera»); plan aprobado en `plans/ingesta-01.md:56-62` y criterio en `:114`.
- Escenario: Amazon entrega tarde el reporte de campañas de D-1, por ejemplo a las 08:50 (dentro
  del plazo de 2 h 30 min de la ingesta). Qué pasa: a las 08:40/08:41 la guarda no encuentra D-1,
  imprime «sin métricas de ayer: espera al siguiente horario» y sale 0. No hay siguiente horario
  para el ciclo: ese día no se ajusta ninguna bid y no sale ninguna pausa ni harvest. El vigía del
  impulso (08:20) tampoco corre, y un impulso que ya pasó su tope de gasto sigue gastando su
  presupuesto diario un día más. Su docstring promete «sin ingesta, avisa y sale 1», pero sale 0
  sin avisar. `ads-salud` no avisa: a las 10:30 ya hay éxito del día (`unidades_atrasadas` mira
  si la corrida terminó hoy, no si el ciclo corrió). El Telegram de productos y ubicaciones dice
  que el ciclo «espera», cuando en realidad ya se perdió. Qué debería pasar: el plan aprobado
  manda reintentos del ciclo, del vigía y de los avisos a las 10:10, 13:10 y 16:10, más `flock`
  en la ingesta, y fija «cero días con el ciclo saltado todo el día por la guarda». El bloque
  instalado no los trae.
- Alcance: se dispara con el cron de hoy el día que la ingesta no deje D-1 antes de las 08:40
  (US o MX por separado). Live. La medición de 7 días (D.2) no tuvo ningún salto, pero antes de
  ingesta-01 hubo 5 fallas en 15 días.
- Evidencia (sonda J1 ejecutada):
  ```
  cycle amazon_us sin D-1: exit=0 stdout='sin métricas de ayer: espera al siguiente horario' corre_ciclo_llamado=False
  impulso-vigia con 1 impulso vivo y sin D-1: exit=0 stdout='sin métricas de ayer: espera al siguiente horario'
  docstring R09-F: con impulsos vivos y ninguno atendido (todos fallaron o sin ingesta) avisa y sale 1, ...
  DEPLOY.md bloque instalador, app.cli cycle: ['40 8 * * *', '41 8 * * *']
  plan ingesta-01: | 10:05 · 10:08 · 10:10 · 10:11 | reintento de avisos · vigía · cycle US · cycle MX |
  crontab instalado 2026-10-21, lineas de cycle: ['40 8 * * *', '41 8 * * *']
  J2: atraso con la ingesta terminada hoy a las 08:50: []
  ```
- Cómo reproducir: `PYTHONPATH=<copia> /Users/dn/dev/goncloud-Orbit/.venv/bin/python sondas/J/J1_ciclo_un_solo_horario.py <copia>`.

### H3. La corrida de precios de las 13:10 no espera a la estimación: con la espera nueva al bridge, US puede perder el día sin aviso
- Severidad: MEDIO
- Frente: dinero
- Dónde: `tools/refresh_estimacion.sh:21-27` (espera hasta 80 × 15 s = 20 min, es decir hasta las
  13:05) y `:38-44` (sale 0 si el bridge terminó dentro de la espera);
  `app/estimacion_reader.py:124-134` (una oferta de más de 6 h invalida el escenario);
  `app/precio/corrida.py:798-804` (los repasos saltan cualquier publicación ya decidida hoy,
  también las `no_evaluado`); cron de precios `10 13 * * *` (`claridad-01/F/servidor.txt`).
- Escenario: el bridge de US termina a las 13:02:30. Hoy el peor caso visto es 12:51:45 y la hora
  viene subiendo: 12:44 → 12:51 en 9 días. El script copia a las 13:02:30. La ingesta tarda unos
  7 min 52 s (medido: 12:45:00 → 12:52:52), así que el escenario queda escrito a las 13:10:22. La
  corrida de las 13:10 lee el escenario anterior, con la oferta de las 06:36 (6 h 34 min), lo marca
  `oferta_desactualizada` y decide `no_evaluado` para esas publicaciones. Los repasos de 15:10 a
  23:10 no vuelven a decidir. `refresh_estimacion.sh` sale 0 porque su copia sí traía US fresco,
  y `cron-avisa` calla. El aviso del motor de precios solo llega tras 3 días seguidos sin
  evaluar. Es la misma falla que #561 corrigió, desplazada a una banda nueva (bridge entre ~13:02
  y 13:05), y ahora con salida 0. Qué debería pasar: la corrida de 13:10 debería esperar o
  comprobar que la estimación de las 12:45 terminó, o la espera debería acotarse a lo que cabe
  antes de las 13:10.
- Alcance: cron y defaults de hoy, live en `amazon_us` (México termina antes). Solo pasa el día
  que el bridge se retrase más de unos 10 min sobre el peor caso visto. No escribe un precio
  malo: deja el día sin decidir.
- Evidencia (sonda J4 ejecutada: aritmética con los tiempos de producción más el lector real):
  ```
  espera maxima al bridge: 80 x 15 s = 0:20:00
  bridge US termina 13:02:30 -> copia 13:02:30, estimacion escrita ~13:10:22 -> DESPUES de la corrida de 13:10; refresh_estimacion sale 0
  bridge US termina 13:04:45 -> copia 13:04:45, estimacion escrita ~13:12:37 -> DESPUES de la corrida de 13:10; refresh_estimacion sale 0
  lector a las 13:10 con oferta de 06:36: estado=desactualizada motivos=('oferta_desactualizada',)
  la corrida salta lo ya decidido hoy: True
  ```
- Cómo reproducir: `PYTHONPATH=<copia> /Users/dn/dev/goncloud-Orbit/.venv/bin/python sondas/J/J4_estimacion_vs_precio_1310.py <copia>`.
  En producción, solo lectura y no ejecutado: q4 de `docs/evidencia/claridad-01/F/consultas.txt`
  (hora del escenario contra 13:10) para cada día.

### H4. El despliegue recrea el contenedor sin volver a mirar si hay un ciclo o una corrida en vuelo
- Severidad: MEDIO
- Frente: motor
- Dónde: `tools/despliegue/desplegar.sh:104-130`: las guardas se miran una sola vez. Después vienen
  respaldo, `pg_dump`, migraciones, copia y build (`:139-201`) y al final `compose_up` en `:201`.
  `tools/despliegue/comun.sh:48-51`: `compose_up` solo comprueba el candado ssh.
  `tools/despliegue/desplegar.sh:108-117`: la reversa interna corre justo cuando las guardas
  están ocupadas. `tools/despliegue/comun.sh:272-286` y `tools/despliegue/rollback.sh:11-15`:
  la reversa y `rollback.sh` reconstruyen y recrean sin mirar las guardas.
  `tools/despliegue/comun.sh:182-196`: la guarda solo ve procesos `app.cli`.
- Escenario: el motor despliega a las 08:38. Las guardas están libres, empieza respaldo y build y
  a las 08:40 arranca el ciclo US dentro del contenedor viejo. `compose up --build` recrea el
  contenedor a media fase de apply. Docker mata todo proceso de `docker exec` cuando se detiene el
  proceso principal del contenedor. Resultado: el ciclo muere sin cerrar, su fila queda `running`
  (la guarda de menos de 3 h y la falta de otro horario impiden reintentar hoy, H2) y la salida
  137 va solo al log (H1). Lo mismo pasa con una corrida de precios de las 13:10 o con la línea de
  cron que cae justo en los segundos de la recreación (`docker exec` falla). En la reanudación de
  un despliegue cortado, `desplegar.sh` revierte y recrea precisamente porque hay algo corriendo.
  Qué debería pasar: volver a revisar las guardas inmediatamente antes de recrear, también en la
  reversa, o tomar el mismo candado que usan los jobs.
- Alcance: cualquier despliegue del motor (24/7, sin ventana) cuya ventana entre guarda y
  recreación cruce una hora de cron que escribe: 08:20, 08:40/41, 13:10 y repasos, y :17 de cada
  hora. Live. Se recupera: el ciclo del día siguiente reconcilia los intentos sin sello, y precios
  cierra huérfanas leyendo el precio vivo (`app/precio/cambios.py:920-947`). Lo que se pierde es
  el día del ciclo, sin aviso.
- Evidencia: lectura línea por línea del camino completo. `grep -n "estado_guardas\|compose_up"
  tools/despliegue/desplegar.sh` da 34 (ensayo), 106 (único bucle de guardas) y 201
  (`compose_up`), sin otra revisión en medio. `revierte` llama a `compose_up && espera_salud`
  sin guardas (`comun.sh:281`). Lo de Docker es comportamiento documentado de `docker exec`: el
  comando solo vive mientras vive el proceso principal del contenedor.
- Cómo reproducir: lectura, más `grep -n "estado_guardas\|compose_up" tools/despliegue/desplegar.sh tools/despliegue/comun.sh tools/despliegue/rollback.sh`.
  La simulación existe (`ORBIT_SIMULACION=1`, `tests/test_despliegue_motor.py`) pero necesita
  Postgres: no la ejecuté.

## Sospechas sin confirmar

1. **Despliegues bloqueados días enteros.** `estado_guardas` (`comun.sh:182-196`) cuenta toda
   fila harvest en `pending_veto` (vive 48 h por diseño), todo `harvest_job` no terminal (en
   `hermanas_negadas` dura hasta 3 ciclos) y todo ciclo `running` sin límite de edad (un huérfano
   vive hasta el ciclo siguiente). Cada uno hace salir 75 a todo despliegue del motor, también al
   de un arreglo urgente. Para confirmarlo: contar en producción las horas al mes con esas filas.
2. **Qué cubre `cron-avisa` y qué envuelve hoy.** El script vive en el host
   (`/usr/local/bin/cron-avisa`) y no está en el repo. No sé si `--cada 24` avisa por un salto
   `flock -E 75` repetido, ni qué líneas lo traen hoy (ciclo, precios MeLi, vigía). Para
   confirmarlo: leer el script y `crontab -u gon -l`. Eso define cuánto de H1 ya está cubierto en
   producción.
3. **Migraciones fuera de la receta.** `migraciones_de` (`comun.sh:138-153`) solo aplica los
   nombres `_(auto03|orbit07|claridad01)_`. Una migración de otro plan no se aplica, el código sí
   se despliega y el checklist sale verde (no hay registro de migraciones aplicadas). Hoy no hay
   ninguna pendiente (la última es 0082). Para confirmarlo: un plan con una migración sin esa
   etiqueta.
4. **Ingesta sin `flock`.** El plan lo pedía (`plans/ingesta-01.md:62`). La corrida de las 10:40
   puede seguir hasta pasadas las 13:10 y encimarse con la de 13:15: dos descargas del mismo grupo.
   Probablemente se absorbe por el dedupe y el 425 de Amazon. Para confirmarlo: medir duraciones
   reales de 10:40 en `ingest_run`.
5. **`bloqueo-reconcilia` falla sin decir por qué.** Su `except` imprime solo «bloqueo-reconcilia
   fallo», sin el error (`app/bloqueo_dueno_efecto.py:174-176`), y su línea horaria no está en la
   tabla de `docs/DEPLOY.md`. Para confirmarlo: ver si mueve algo en Amazon y si tiene
   `cron-avisa`.

## Zonas revisadas sin hallazgos (una línea cada una, con qué comprobaste)

- Frescura antes de decidir: el ciclo, el vigía, los avisos y Jev exigen D-1 escrito por una
  corrida ok de hoy (`metricas_listas.py:30-136`). Las bids usan ventana hasta `max(metric_date)-3d`
  (`CONTEXTO.md:102`), así que el D-1 parcial de US a las 07:10 UTC no entra a decisiones.
- Candado del ciclo y de precios: claim atómico con TTL y heartbeat, más rastro de huérfanos
  (`cycle.py:363-371`, `precio/corrida.py:129-162`). Precios usa `pg_try_advisory_lock`, que nunca
  bloquea.
- Corrida de precios muerta a media escritura: `cerrar_huerfanas` relee el precio vivo y sella
  `huerfana_recuperada` o `huerfana_sin_efecto` (`precio/cambios.py:920-947`). No duplica.
- `notifica.py`: todo envío es fail-silent con timeout de 10 s y ninguno tumba al job que lo llama
  (`notifica.py:207-234`, `precio/corrida.py:1449-1455`, `_fase_notifica` del ciclo). Límite que
  noté: `ads-salud` deja de reintentar tras 6 intentos (unos 60 min a cadencia de 10 min) y la
  recuperación cierra el episodio sin avisar si el aviso nunca salió (`ads/salud.py:21,75-82,88`).
  Para que importe, Telegram tendría que caerse justo cuando falla la ingesta.
- Reversas de la receta (0076–0082): todas abortan si la tabla o la columna ya tiene datos, así
  que no borran nada. El código anterior corre con el esquema nuevo porque los cambios son
  aditivos. `rollback.sh` deja `rollback_incompleto` y bloquea despliegues hasta resolverlo, tal
  como está documentado.
- Fechas: el rango D-31..D-1 se calcula en Python en UTC con tope de 31 días
  (`ads/diario.py:318-329`). El servidor y cron están en UTC, sin cambio de horario. El `date -d
  "31 days ago"` del crontab viejo ya no existe. El vigilante SP-API usa una ventana fija de
  04:30 a 07:30.
- Recursos: cada job abre y cierra su conexión (`with`/`finally`) y la API abre una por request
  (`api.py:115-140`). Los logs de cron sin rotación son un residual ya aceptado
  (`docs/evidencia/sp-api-01/vigilante/revision-grok.md:25`) y de volumen bajo. Los respaldos
  `predeploy-*` pesan unos 9 MB cada uno y no se podan: crecimiento lento, sin riesgo cercano.
- Cuelgues con `flock` tomado: todos los clientes externos tienen timeout (Ads 15 s, SP-API,
  MeLi, Telegram). Postgres no tiene `statement_timeout` ni `lock_timeout`, pero no encontré un
  camino concreto de espera indefinida en las corridas que usan `flock`.
- Secretos: el repo solo trae marcadores (`DEPLOY.md:1227-1232`, `.env.example`). Los errores y
  avisos pasan por `scrub` y el token de Telegram está registrado (`notifica.py:187`).
- `refresh_costos.sh`: cada pipeline está aislado, sale distinto de 0 si alguno falla y en
  producción va envuelto en `cron-avisa`. `spapi-diario.sh` corre en serie, con repetición
  acotada a las 06:15 y cubierto por el vigilante de las 07:30.
