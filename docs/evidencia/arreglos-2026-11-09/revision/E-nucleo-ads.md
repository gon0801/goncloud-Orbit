# E. Núcleo de decisiones del motor de Amazon Ads (previo a ORBIT 07): resultado

Copia revisada: `orbit07-wt` en `3c841f4`. Sondas en `scratchpad/orbit07-review/sondas/E_*.py` (salidas en `E_*.txt`).
Python: `/Users/dn/dev/goncloud-Orbit/.venv/bin/python -P` con `PYTHONPATH=<orbit07-wt>`.

Conteo: 0 CRÍTICO, 0 ALTO, 3 MEDIO.

## Hallazgos confirmados

### H1. La guarda «estructura sin sincronizar > 48 h» no dispara mientras el motor haya aplicado algo en las últimas 48 h
- Severidad: MEDIO
- Frente: motor (y dinero de forma indirecta)
- Dónde:
  - `app/optimizer/windows.py:358` (`_SQL_SYNC_PLATAFORMA` = `max(s.synced_at)` sobre TODAS las filas de `ad_entity_state` de la plataforma) y `app/optimizer/windows.py:589` (la guarda compara ese máximo contra 48 h).
  - El apply pisa `synced_at` con `now()` en cada escritura propia: `app/apply.py:647-649` (`_SQL_ACTUALIZA_CACHE`, llamado tras el readback de cada bid en `app/apply.py:1484-1486`, y en la reconciliación `:784`, `:794`, `:872`), `app/apply_cola.py:310` (pausas), `app/apply_harvest_reconciliacion.py:121`, `app/apply_producto.py:66`.
  - La pantalla de Salud usa la misma consulta: `app/api_dashboard.py:1163`, `app/api.py:214`.
  - `app/ads/salud.py:20` solo vigila la ingesta de métricas (`amazon_ads_reports_v3`), no la de estructura.
- Escenario: el sync diario de estructura (06:45 UTC) deja de funcionar. Ayer el motor aplicó un bid (o una pausa) en esa plataforma → esa fila queda con `synced_at` de ayer 08:40 → `max(synced_at)` sale «fresco» → la guarda no salta y el ciclo sigue en live con estados y bids del cache viejos. Debería: medir la frescura con la corrida del sync de estructura (como ya se hizo con `ledger_fresco_at`, que mide `ingest_run` precisamente por este mismo blanqueo, comentario de `migrations/0016_target_margen_correcciones.sql:106-112`) y saltar la plataforma.
- Consecuencia de dinero: con el cache viejo, un cambio de bid hecho fuera del motor (a mano en Amazon) no se ve; el motor calcula el bid nuevo sobre el número viejo y lo escribe sin leer el bid vivo antes del PUT (`app/apply.py` `aplica_bids` no compara `old_value` con Amazon). El tope de ±30/20 % queda medido contra un bid que ya no existe y el cambio del dueño se pisa. Las campañas o keywords pausadas, reactivadas o archivadas fuera de Orbit siguen con su estado viejo para el motor.
- Alcance: se dispara con el cron y los defaults actuales, en live, en cuanto falle el sync de estructura. La guarda queda ciega mientras el motor aplique algo al menos cada 48 h (en US hubo decisiones casi todos los días del 22-oct al 4-nov). La pantalla de Salud tampoco lo muestra.
- Evidencia:
  - Producción: `docs/evidencia/quick-wins-2026-11-04/c6-p1/salud.json` → `amazon_us.synced_at = 2026-11-04T08:40:05.355Z`, posterior al inicio del ciclo (`started_at 08:40:02.688Z`) y al sync de estructura (06:45); ese día US aplicó 1 bid (`bids_aplicados: 1`). En MX, sin bids aplicados, `synced_at = 06:45:57Z`. O sea: el «synced_at» de la plataforma ya es la hora del apply del motor, no la del sync.
  - Sonda ejecutada `E_guarda_synced_at.py` (guarda real con la consulta simulada):
    ```
    ultimo sync de estructura: 2026-11-04T08:40:00+00:00 (5 dias)
    max(synced_at) que ve la guarda: 2026-11-08T08:40:00+00:00
    guarda_plataforma -> None
    sin el apply del motor -> MotivoSkip(guarda='synced_at', detalle='... hace 5 days, 0:00:00 > 48h')
    ```
- Cómo reproducir: `PYTHONPATH=<orbit07-wt> python -P sondas/E_guarda_synced_at.py`. En producción (no ejecutado, solo lectura): `SELECT max(finished_at) FROM ingest_run WHERE source='amazon_ads_structure_v2' AND ok;` contra `SELECT max(s.synced_at) FROM ad_entity_state s JOIN ad_entity e ON e.id=s.ad_entity_id WHERE e.platform='amazon_us';`. Si el segundo es posterior al primero, la guarda está midiendo al motor.

### H2. Si el dueño revierte una SUBIDA del motor, el motor ya no puede recortar esa keyword durante 90 días aunque pierda dinero
- Severidad: MEDIO
- Frente: dinero
- Dónde:
  - `migrations/0067_bids02_campana_ajuste.sql:80-89`: toda reversa `ok` de cualquier decisión `bid` entra a `v_cambio_bid` como `regreso_del_dueno`, con `bid_antes` NULL y `bid_despues = old_value`.
  - `app/lecturas_caso.py:382-399` (`_cambios_por_hoja`): deriva `bid_antes` = `bid_despues` del cambio anterior, que en este caso es el bid SUBIDO.
  - `app/optimizer/caso.py:251-260` (`piso_aprendido` = máximo `bid_antes` de los regresos).
  - `app/optimizer/politica.py:402-406` (R17) y `:615-616` (`_paso`): todo recorte que deje el bid en o bajo el piso se queda en `Mantener(piso_aprendido)`.
  - `app/apply.py:1665-1698` (`reversa_manual(tipo="bid")`) y `app/api_write.py:383-394` (`POST /api/ads-optimizer/reversa/bid`): aceptan cualquier decisión de bid aplicada, sin mirar la dirección.
  - El diseño supone lo contrario: `plans/bids-02-construccion.md:732` «Un cambio de origen `regreso_del_dueno` tiene dirección +1». La reversa de una subida produce dirección −1 y un piso por ENCIMA del bid vigente.
- Escenario: el motor sube 1.00 → 1.15 USD; el dueño no está de acuerdo y revierte esa decisión por la reversa manual → bid vigente 1.00, `piso_aprendido` = 1.15. Desde ahí, aunque la keyword tenga ACoS de 500 %, cualquier recorte (1.00 → 0.88 o 0.75) queda bajo 1.15 y se bloquea. La subida sí puede repetirse. Debería: el piso aprendido solo debe salir de regresos que deshacen RECORTES (el bid donde se perdió el tráfico), no de una subida revertida.
- Alcance: no lo dispara el cron por sí solo. Requiere que alguien revierta una subida por la ruta manual de reversa (la pantalla solo ofrece «regresar» rachas de recortes, que se calculan bien). El bloqueo dura hasta que la reversa sale de la ventana de 90 días de `_SQL_CAMBIOS`. Solo una keyword con cero pedidos se salva, porque la pausa se evalúa antes que R17.
- Evidencia: sonda ejecutada `E_piso_aprendido_subida.py` (filas tal como las entrega `_SQL_CAMBIOS`, política real):
  ```
  2026-10-01 motor antes 1.00 despues 1.15 direccion 1
  2026-10-03 regreso_del_dueno antes 1.15 despues 1.00 direccion -1
  piso_aprendido = 1.15
  solo la subida del motor (sin la reversa): Mover(factor=-0.25, bid_nuevo=0.7500, motivo='pierde_dinero_fuerte')
  subida + reversa del dueno               : Mantener(motivo='piso_aprendido')
  ```
- Cómo reproducir: `PYTHONPATH=<orbit07-wt> python -P sondas/E_piso_aprendido_subida.py`.

### H3. Un bid que quedó «sin sello» por un error ambiguo de Amazon permite un segundo cambio de la misma keyword al día siguiente, sin la espera de 7 días
- Severidad: MEDIO
- Frente: dinero
- Dónde:
  - Orden del ciclo: decide en `app/cycle.py:3760` (`_fase_lecturas`, TX2 y TX3) y solo después reconcilia y aplica en `app/cycle.py:3501` (`reconcilia_bids`) → `:3502` (`aplica_bids`).
  - La trayectoria de la política solo ve cambios con `verify_ok IS TRUE`: `migrations/0067_bids02_campana_ajuste.sql:77` → `app/lecturas_caso.py` `_SQL_CAMBIOS`.
  - Un 5xx o una falla de red agotada en el PUT, o en la lectura de comprobación después del PUT, deja el intento sin sello y la excepción sube: `app/apply.py:1466-1471` (y `_SQL_INTENTOS_BID_SIN_SELLO`, `app/apply.py:705-714`).
  - Nada en la fase de decisión ni en `aplica_bids` (`app/apply.py:1345-1420`) mira intentos de bid sin sello o recién reconciliados de la misma hoja.
- Escenario: día N, el motor recorta una keyword 1.00 → 0.75. El PUT llega a Amazon pero la respuesta o la lectura de comprobación da 5xx → intento sin sello. Día N+1, el sync de estructura ya ve 0.75. La política no ve el cambio en su trayectoria (no está verificado), así que no aplica R2 (7 días), R15 (20 clics nuevos), R16 ni R17, y decide otro −25 % sobre 0.75. En la fase de apply, `reconcilia_bids` confirma el cambio del día N e inmediatamente `aplica_bids` escribe el del día N+1 → −44 % en ~24 h. Debería: reconciliar los bids sin sello antes de decidir, o saltar en la decisión las hojas con un intento de bid sin sello.
- Alcance: live, con el cron y los defaults actuales. Requiere un error ambiguo de Amazon durante un bid (el código lo trata como caso esperado: «la fila ES el rastro del crash»). La exposición diaria es baja: 0 a 4 decisiones de bid por plataforma al día en la evidencia del 22-oct al 4-nov.
- Evidencia: lectura línea por línea del camino citado (no hay sonda pura posible: necesita base y HTTP).
- Cómo reproducir (no ejecutado: requiere base): con un ciclo live sembrado, un intento `apply_attempt` tipo `normal` sin `finished_at` para la decisión de bid de ayer de la hoja X y `ad_entity_state.current_bid` ya en el valor nuevo, correr `python -m app.cli cycle --platform amazon_us --forzar` y comprobar que el ciclo emite una decisión `bid` nueva para X y que `notes.apply.bids_reconciliados.confirmados` = 1 en el mismo ciclo.

## Sospechas sin confirmar

1. **Negativos que apagan la keyword propia del ad group.** `_procesa_grupo`/`hygiene.decide_hygiene` (`app/cycle.py:2702`, `app/optimizer/hygiene.py:309-331`) niega el término exacto en el ad group de origen sin comparar contra las keywords positivas de ese mismo ad group. En un ad group de concordancia exacta, el término igual al texto de la keyword (20+ clics, costo ≥ ticket promedio, 0 pedidos) quedaría negado, lo que en la práctica pausa la keyword con umbrales más bajos que los de la pausa (100 clics / 40 USD) y sin la protección por venta reciente. Falta confirmar: `SELECT` de decisiones `negative` cuyo `search_term` coincide (lower/btrim) con `keyword_text` de una keyword `EXACT` del mismo `ad_entity_id`.
2. **Un ciclo que revienta no avisa por Telegram desde el código.** `corre_ciclo` sella `failed` y relanza (`app/cycle.py:3971-3974`), el CLI imprime y sale 1 (`app/cli.py:130-136`), y el resumen diario solo sale en el camino feliz. Cualquier excepción de una sola hoja o una config inválida tumba toda la plataforma ese día, incluida la liberación de cortes en cola y las reconciliaciones (`app/cycle.py:3374-3392` sin try por hoja). El aviso depende de que el cron de producción envuelva `cli cycle` en `cron-avisa`, y el bloque documentado en `docs/DEPLOY.md:1197-1199` no lo hace. Falta: `crontab -u gon -l | grep 'cli cycle'`.
3. **Un solo intento diario del ciclo.** Si la ingesta de las 07:10 falla y se recupera en los reintentos de 10:40 o 13:15 (`docs/DEPLOY.md:1186-1191`), la guarda `metricas_listas.guarda` hace que el ciclo de 08:40 salga 0 sin abrir ciclo, y no hay otro horario: ese día no hay decisiones ni se liberan los cortes vencidos, y solo se nota porque falta el resumen. Falta confirmar en el crontab vigente si existe reintento del ciclo.

## Zonas revisadas sin hallazgos

- Aritmética de `politica.decide` (niveles_v3): fuzz de 80 000 casos extremos (bids de 0.02 a 80, piso>bid, techo<bid, target 0.01–200, equilibrio ≤0, None en cada métrica). 0 violaciones de [piso, techo], del tope por decisión [−30 %, +20 %] y de la dirección, 0 excepciones (`E_fuzz_politica.txt`). «Regresar» puede pasar de +20 % (hasta el bid anterior): está en el diseño (CONTEXTO, «regresa al anterior de una vez»).
- División entre cero y tipos: todo en Decimal, comparaciones por multiplicación (`bid.py`, `hygiene.py:343`, `politica.py:492-520`). Las divisiones de `_fondo_juicio`, `_previa` y `_precio_de_hoy` están protegidas por `> 0`; la gamma rechaza `a` no entero. Redondeo a 2 decimales (HALF_EVEN) en `app/ads/write.py:198`, después del tope: con pisos ≥0.10 USD / 1.00 MXN no rompe el tope.
- Moneda: el piso y el techo salen por la moneda de la plataforma, un goal con otra moneda revienta a propósito (`goals.py` `resuelve_floor_ceiling`, deuda declarada) y el sync fija la moneda del bid por perfil.
- Pausa (`bid._decide_pause`): exige pedidos conocidos y 0, ventana madura ≥10 días (trigger `decision_madurez_corte`), umbral `max(100, adaptativo)`, piso 40/500. La económica (3× y 80/1000) está apagada en la config de producción conocida (config 42 sin `ads_pause_economica`). Con eso, una keyword con ≥1 pedido que quema dinero solo recibe recortes y R16 puede congelarla hasta 90 días: es consecuencia del diseño, no un defecto de código.
- Negativos y harvest (`hygiene.py`, `cortes.py`): elegibilidad 3/60/14 antes de dividir, umbral y piso que solo suben, términos tipo ASIN siempre saltados, dedupe normalizado. El destino por terna de plataforma está aceptado (`migracion_pendiente`, brief FABRICA 02); apply_harvest revisa en Amazon antes de crear, así que no duplica.
- Metas de ACoS (`goals.py`): orden goal → producto → familia → plataforma → setting. Un peldaño ≤0 o no finito revienta, nunca cae al siguiente. Los márgenes se recortan a [10, 45]. El margen de producto ≤0 se excluye (`productos.py:478`). Sin peldaño, la hoja se salta (`sin_target`). R3 con el equilibrio de plataforma está aceptado (S3, `plans/decisiones-producto-01.md:16`).
- Ventanas y madurez (`windows.py`, `lecturas_caso.py`): un día sin ingesta da None (falla cerrada), los pedidos inmaduros D-9..D-1 se exigen completos y el doble conteo se evita por `v_metric_latest`. En US (PST), D-1 se pide a las 07:10 UTC con el día local aún abierto: efecto chico (inmaduros y razón de tráfico) y se corrige con la reingesta diaria.
- Candado, sello y ciclo a medias (`cycle.py:363-446`, `3909-3984`): claim en una sola sentencia, liberación por dueño, rastro de ciclos muertos, TX3 atómica, guard `status='running'`. Que una hoja que revienta tumbe la plataforma entera es fail-closed declarado en el docstring del módulo.
- Umbrales del diseño v2 contra el código: PAUSE 100/40/500, NEGATIVE 20/40 y 8/130→45/600, HARVEST ≥2 y min(35, target), tope [−30, +20], frescura 7 días y 48 h, TTL 30 min. Todas las diferencias (niveles_v3 en lugar de bandas, cascada sin cache ni default 55, pisos por moneda, completitud de 7 fechas solo en cortes) están escritas en CONTEXTO.
- Cuota de cambios: sin caminos que la salten en el código. En producción los topes valen 1 000 000 (el dueño lo sabe: «que no limita», `docs/manual-usuario/paginas/40-catalogo.html:183`).
- Espera de 7 días en ad groups de términos (`_gates_entidad` con cooldown): un negativo o harvest aplicado congela todos los términos del grupo 7 días (92 términos en MX el 4-nov). Está declarado («en grupos, el gate sigue igual»).
- `replay.py` y `eras.py`: no están en ningún camino que escriba dinero (solo auditoría y pantalla).
- `familias.py` y `goals_write.py`: escritura de configuración validada antes de los CHECK de la base (piso ≤ techo, positivos, terna completa).
