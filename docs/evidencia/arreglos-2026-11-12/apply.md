# Revisión: la escritura a Amazon Ads y los cortes (apply)

Revisé el código de `master a4644aad`, que es el mismo que corre en producción. No corregí nada.

- **Pruebas de reproducción:** están en `docs/evidencia/arreglos-2026-11-12/pruebas/apply/` y **fallan hoy** por la aserción que describe cada falla. Se corren con:
  `PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q docs/evidencia/arreglos-2026-11-12/pruebas/apply/`
  Resultado: 13 fallan (las fallas) y 1 pasa (la reversa de la pausa por producto, que funciona).
- **Ayuda:** dos sub-revisores leyeron bloqueo/retiro/pausa por producto (pruebas `test_sub_a_*`) y archivar/propuestas/herramientas (pruebas `test_sub_b_*`). Volví a correr sus pruebas y las comprobé.

**Foto de producción (sonda de solo lectura, 12-nov):**
- Ledger `apply_attempt`: 557 filas, **0 sin sello** y 0 con verificación fallida. Los 501 bids aplicados tienen `verify_ok = true`.
- Cola: 31 filas en sombra pendientes (pausa de product ads) y ninguna fila `applying` vieja.
- Ninguna de las fallas de abajo dejó rastro visible todavía. Son agujeros que se abren con una situación concreta, y cada una dice cuál.

## Cómo funciona

1. **Ciclo optimizador.** Corre 4 veces al día por plataforma: 08:40 US y 08:41 MX, y luego a las 10:40, 13:40 y 16:40 UTC. Primero decide con la base y después, dentro del mismo candado, escribe a Amazon.
2. **Bids.** Se aplican en el mismo ciclo (`app/apply.py`, `aplica_bids`). Por cada decisión:
   - se revisa el modo: escalera + goal + entidad;
   - se cobra cuota;
   - nace la fila del ledger y se hace commit ANTES del HTTP;
   - sale el PUT;
   - se relee Amazon por LIST;
   - se sellan ledger, resumen (`decision_application`) y caché con lo leído.
3. **Cortes (pause, negative, harvest).** Van a la cola `apply_queue` con 48 h de veto (`app/apply_cola.py`). Al vencer:
   - se re-validan con datos frescos;
   - se cobra cuota;
   - se reclama la fila (`released → applying`);
   - nace el ledger;
   - sale el HTTP y se relee.
4. **Harvest.** Es un `harvest_job` por fases (`app/apply_harvest.py`): primero el negative en el origen, después la keyword en el destino, después la relectura y, en grupos, las «hermanas».
5. **Reconciliación al inicio de la fase de apply** (`apply_harvest_reconciliacion.py`, `apply.reconcilia_bids`). Cierra lo que quedó a medias si el proceso murió: jobs en vuelo, negatives y pausas en `applying`, y bids sin sello.
6. **Reversas.** Hay una por tipo (bid, pause, negative, harvest, ajuste de campaña, pausa de product ad). Van en el ledger como `reversa` y no gastan cuota.
7. **Cliente de escritura** (`app/ads/write.py`):
   - lista cerrada de pares (método, ruta);
   - un objeto por petición;
   - la cuenta (profile) queda fija en la instancia;
   - el bid viaja redondeado a 2 decimales;
   - la moneda se compara con la de la plataforma.

   El cliente de lectura (`app/ads/client.py`) rechaza PUT, PATCH y DELETE.
8. **Fuera del ciclo también se escribe a Amazon:**
   - el bloqueo de términos que aprueba el dueño (`bloqueo_dueno_efecto.py`);
   - la pausa y retiro de product ads (`apply_producto.py`, `retiro_anuncios.py`);
   - los ajustes de campaña del dueño (`apply.py`);
   - herramientas de terminal y la fábrica de campañas.

## Fallas (con reproducción)

### 1. Orbit pisa el bid que el dueño cambió a mano, y la «reversa» regresa a un bid que nunca existió
**Qué le pasa al dueño.** La estructura se sincroniza una vez al día, a las 06:45 UTC. Si después el dueño cambia una puja a mano en Amazon (por ejemplo de 1.00 a 1.50), el ciclo de las 08:40, 10:40, 13:40 o 16:40:
- decide con el 1.00 viejo;
- manda su PUT (0.85) sin mirar qué hay en Amazon;
- lo da por bueno porque la relectura coincide con lo que mandó.

El cambio del dueño se pierde sin aviso. Además, «regresar» esa decisión (botón de regreso o reversa manual) escribe 1.00, que no es lo que el dueño tenía.

**Dónde.**
- `app/apply.py:1300-1442`: `aplica_bids` y `_ejecuta_mutacion` no leen el bid vivo antes del PUT. El LIST solo ocurre después.
- `app/apply.py:1471-1527`: `reversa_bid` escribe `decision.old_value`, que sale del caché.
- El ciclo no refresca bids antes de decidir (`app/cycle.py:3382-3419`).

**Pruebas.**
- `test_rev_apply.py::test_bid_no_pisa_un_cambio_manual_hecho_despues_del_sync`
  `AssertionError: con el bid vivo (1.50) distinto del old_value decidido (1.00) no debe salir el PUT; salieron [{'keywordId': '7201', 'bid': 0.85}]`
- `test_rev_apply.py::test_reversa_de_bid_regresa_al_bid_que_habia_en_amazon`
  `AssertionError: la reversa deberia volver a 1.50; escribio 1.0`

**Producción.** Revisé 343 pares de bids aplicados seguidos en la misma hoja. En 13 casos el «bid de antes» no es el último que puso Orbit, pero los 13 se explican por redondeo o por un regreso del dueño hecho con Orbit. No hay evidencia de que ya haya pisado un cambio manual. El agujero existe en cuanto el dueño toque una puja en Amazon.

**Qué debería pasar.** Leer el bid vivo (el mismo LIST de la relectura) antes del PUT:
- si no es `old_value`, no se escribe y la hoja se re-decide con el bid real;
- la reversa debería guardar como «bid anterior» lo que Amazon tenía de verdad.

### 2. Hay escrituras a Amazon que esquivan el candado del cliente de escritura
**Qué le pasa al dueño.** La regla sellada (APPLY.md §8) dice que todo lo que se escribe en Amazon pasa por un solo cliente con lista cerrada. Hoy hay otros caminos que escriben con su propio HTTP y sin esas protecciones.

El candado de pruebas (`tests/test_architecture.py:372`) solo vigila quién importa `app.ads.write`, así que no ve nada de esto.

**Dónde (código y sonda):**
- **La fábrica de campañas, `tools/fabrica_campanas.py`.**
  - Hace POST de creación con su propio `httpx` (líneas 877 y 903) y PUT `/sp/campaigns` con `state` (línea 1601). Para ese PUT, write.py dice explícitamente «nunca `state`».
  - Esos caminos no pasan por la lista cerrada, ni por el scope fijo, ni por el chequeo de moneda.
  - La usan en producción `app/impulso_io.py:141-230` (el «Vigía de impulso», 4 veces al día), `app/api_fabrica.py` y `app/fabrica_web.py`.
  - Sonda: `fabrica_lote_paso` tiene 87 objetos creados por ese camino (5 campañas, 5 ad groups, 66 keywords, 5 product ads, 6 targets; 2026-09-09).
- **Herramientas de terminal.**
  - `tools/archiva_inertes.py:354` y `tools/reactiva_campanas.py:386` usan su propio `httpx`.
  - Cuatro sondas (`tools/sonda_*.py`, líneas 102-114) usan el cliente de LECTURA para hacer PUT por la puerta trasera `cliente._client.request("PUT", ...)`.
- **Constructor «lavado».** `app/ads/archivar.py:134` (`preparar_escritor`) construye el cliente de escritura con modo `live` fijo, sin la revisión de modo por decisión.
  - Lo llaman `app/impulso_io.py:1071`, `app/retiro_anuncios.py:567, 798, 1134` y `app/cli.py:260, 306`.
  - Ninguno está en la lista de §8.3.
  - `app/apply_producto.py:11` lo admite: «no lleva esa protección».

**Qué debería pasar.** Una sola puerta que construya el cliente de escritura, con un candado que mire también el HTTP crudo a `advertising-api`. Lo que la fábrica necesita (crear campañas o ad groups, PUT de `state`) entra a la lista cerrada como decisión explícita del dueño, no por fuera.

### 3. Archivar product ads por terminal es irreversible y no deja rastro antes de escribir; «reponer» duplica anuncios
**Qué le pasa al dueño.**
- `orbit archivar-anuncios` archiva sin dejar antes ninguna intención durable. Archivar en Amazon no tiene marcha atrás.
- La línea para recrear el anuncio solo se imprime al final. Si la relectura posterior falla (un 5xx sostenido del list), los anuncios ya quedaron archivados y esa línea no queda en ningún lado.
- No pide número esperado, ni huella, ni tope.
- `orbit reponer-anuncios`, corrido dos veces (normal cuando sale «sin confirmar»), crea dos anuncios del mismo SKU.
- Sonda: `anuncio_retiro` y `anuncio_recreacion_intento` tienen 0 filas. Los 203 archivados del 30-ago salieron por este camino, sin registro en la base.

**Dónde.** `app/ads/archivar.py:316-508` y `app/cli.py:240-330`.

**Pruebas.**
- `test_sub_b_archivar_cli_sin_ledger.py::test_relectura_caida_tras_archivar_pierde_la_linea_de_reversa`
  `AssertionError: archivo en Amazon (irreversible) y la linea de reversa se perdio ... assert None is not None`
- `test_sub_b_archivar_cli_sin_ledger.py::test_reponer_dos_veces_crea_dos_anuncios`
  `AssertionError: ... 2 creates, vivos=['901', '902']`

**Qué debería pasar.** Que pase por `retiro_anuncios`, que ya guarda la intención antes del HTTP y revisa si ya hay un anuncio vivo antes de recrear.

### 4. Aprobar un bloqueo sobre un negative PAUSED lo da por aplicado sin escribir nada
**Qué le pasa al dueño.** Si en Amazon ya existe el negative del término pero está pausado, al aprobar el bloqueo Orbit sella «aplicado» y no manda nada. Un negative pausado no bloquea: el término sigue gastando y la pantalla dice «bloqueado».

**Dónde.**
- `app/bloqueo_dueno_efecto.py:219-223`.
- `apply_harvest._identidad` (`app/apply_harvest.py:601`) solo descarta los ARCHIVED.
- El mismo módulo exige ENABLED en otros dos lugares (líneas 238 y 312).

**Prueba.** `test_sub_a_bloqueo_pausado_aplicado.py`
`AssertionError: sello [('intencion', None, None), ('aplicado', None, 'kid-pausado')] sin POST con el negative en PAUSED: el termino sigue gastando`

**Producción.** 48 bloqueos aplicados. El ledger no dice cuáles se adoptaron sin POST.

**Qué debería pasar.** Solo un negative ENABLED cuenta como aplicado.

### 5. Una «práctica de veto» en sombra congela la hoja para siempre (también sus bids)
**Qué le pasa al dueño.** Si el modo global o el goal de una campaña pasa por sombra (aunque sea un día), cada pausa decidida en ese tiempo queda en la cola como fila en sombra `pending_veto`.

Esa fila:
- nunca se libera;
- nunca se descarta sola: el descarte de filas en sombra exige al administrador;
- cuenta como «clave en vuelo» para siempre.

Al volver a live, el ciclo salta esa hoja completa, sin moverle el bid ni pausarla, hasta que alguien la descarte a mano. Solo la pausa por producto tiene limpieza automática al encenderse (`productos_modo.py`). Para hojas, la única limpieza fue la ceremonia única del cambio a live de ORBIT 05.

**Dónde.**
- `app/apply_cola.py:160-166`: `_SQL_CLAVES_BLOQUEADAS` no mira `modo` ni `vence_el` de las filas no terminales.
- `app/cycle.py:1910-1928`: salta la hoja completa.

**Prueba.** `test_rev_apply.py::test_fila_shadow_vencida_no_bloquea_la_clave_para_siempre`
`AssertionError: una practica de veto vencida hace 30 dias sigue congelando la hoja`

**Producción.** Hoy todos los goals están en live y las 31 filas en sombra son de product ads, que sí tienen su limpieza. Se abre en cuanto se use sombra para una campaña o para todo el sistema.

**Qué debería pasar.** Que una fila en sombra vencida deje de bloquear, o que el paso a live descarte las prácticas pendientes de esa campaña o plataforma.

### 6. Ya no hay ningún freno global: el «fusible» quedó en 1,000,000
**Qué le pasa al dueño.** APPLY.md §5.5 sella que el tope diario de bids «es un FUSIBLE… cortar una avalancha anómala (bug o dato corrupto que quiera mover cientos)… NO se retira».

Hoy todos los topes valen 1,000,000:
- sonda: `config_version` 51, del 9-nov;
- el máximo real aplicado en un ciclo fue 45 bids en MX y 24 en US;
- MX tiene 3,590 hojas y US 1,889.

Revisé el código: fuera de la cuota no existe otro tope por ciclo. `aplica_bids` solo se detiene en `consume_quota_y_sello` (`app/apply.py:1356`).

Si un dato corrupto (por ejemplo un margen o una meta que se desploma) hiciera calificar a todas las hojas, un solo ciclo movería las 3,590 hojas de MX hasta −30 % cada una, cuatro veces al día. Los cortes tampoco tienen freno: todos los vencidos se liberan de golpe tras 48 h, salvo que el dueño vete fila por fila.

**Qué debería pasar.** Un fusible real, por ejemplo 3–5× el máximo histórico (unos 150 bids por ciclo), o la decisión explícita del dueño de retirarlo, escrita en APPLY.md.

### 7. Un negative confirmado por la reconciliación queda sin reversa ejecutable (regla 7)
**Qué le pasa al dueño.** Si el proceso muere entre el POST del negative y su sello, la reconciliación lo encuentra en Amazon y lo marca aplicado. Pero el botón de reversa busca el id del negative solo en un intento con resultado `ok` exacto, y este quedó `ok:reconciliado`. Responde 422 y ese negative vivo no se puede deshacer desde Orbit.

**Dónde.**
- `app/apply.py:1557-1561` (`_SQL_ACK_OK`) y `:1602-1617`.
- `app/apply_harvest_reconciliacion.py:398-404`: guarda el id en el resumen, no en el ledger.

**Prueba.** `test_rev_apply.py::test_negative_confirmado_por_reconciliacion_se_puede_revertir`
`AssertionError: el negative n-3 esta vivo y aplicado pero sin reversa: no se resolvio el negative_id de la decision 1 desde el ledger`

**Qué debería pasar.** Que la reversa encuentre el id también en el sello de la reconciliación, o que ese sello lo guarde en el ledger.

### 8. «Quitar» un bloqueo con un 5xx deja el bloqueo «vivo» para siempre
**Qué le pasa al dueño.** Si el delete del negative responde 5xx pero Amazon sí lo archivó:
- el ledger queda en `reversa_fallida`, que cuenta como bloqueo vivo;
- la reconciliación de cada hora no lo revisa;
- la pantalla dice «bloqueado» cuando ya no lo está.

**Dónde.** `app/bloqueo_dueno_efecto.py:261-275` y `:39`. La ida sí relee Amazon tras un 5xx; la vuelta no.

**Prueba.** `test_sub_a_quitar_5xx_sin_readback.py`
`AssertionError: quitar=reversa_fallida/http_503; ultimo paso ('reversa_fallida', 'http_503', None) con el negative ya ARCHIVED en Amazon`

### 9. El bloqueo no lee el rechazo por ítem del 207 de Amazon
**Qué le pasa al dueño.** Si Amazon rechaza el negative con un 207 que trae un error por ítem, el dueño ve «por confirmar» hasta una hora. Después se sella `fallido/sin_resultado` y el motivo de Amazon se pierde.

**Dónde.** `app/bloqueo_dueno_efecto.py:225` descarta la respuesta. `write._mutate` solo lanza error con status ≥ 400.

**Prueba.** `test_sub_a_bloqueo_207_error_ignorado.py`
`AssertionError: 207 con error por item quedo intencion/por_confirmar`

### 10. Una lectura incompleta de Amazon se toma como «no existe» y dispara POST o borrados
**Qué le pasa al dueño.**
- `apply_harvest._lista_todos` lee el LIST de toda la cuenta con un máximo de 20 páginas y corta sin avisar.
- Con esa lectura se decide «no existe → postear».

Dos efectos:
- la reconciliación de un negative huérfano vuelve a postear un negative que ya estaba;
- un harvest recién creado se da por fallido y su reversa automática borra la keyword y el negative que sí se crearon.

**Dónde.**
- `app/apply_harvest.py:418-437`, usado 7 veces: `_paso_negative` :1570, `_paso_keyword` :1650/1673, `_paso_readback` :1802, `_reversa_automatica`, `_reconcilia_negativas` :393.
- El código ya tiene la versión correcta, `_lista_completa` (:517), pero solo la usa la reversa manual.

**Pruebas.**
- `test_rev_apply.py::test_negative_huerfano_no_se_repostea_si_la_lista_no_se_leyo_completa`
  `AssertionError: el negative ya existia (pagina 21): una lectura incompleta no autoriza otro POST; salieron 1`
- `test_rev_apply.py::test_harvest_no_revierte_una_keyword_creada_si_el_list_es_incompleto`
  `AssertionError: la keyword SI se creo (ack con id); se revirtio igual: ['/sp/keywords/delete', '/sp/negativeKeywords/delete']; estado failed`

**Gravedad: baja hoy.** Con páginas de 1,000 hacen falta más de 20,000 keywords o negatives. MX tiene 2,719 keywords y no sé cuántos negatives hay.

### 11. `archiva_inertes --reponer` crea keywords sin intención guardada antes del HTTP
**Qué le pasa al dueño.** Si la relectura no cuadra, el keywordId creado no se anota y la siguiente corrida crea otra keyword. La primera queda huérfana, en PAUSED.

**Dónde.** `tools/archiva_inertes.py:846-916`.

**Prueba.** `test_sub_b_inertes_reponer_sin_intencion.py`
`AssertionError: sin intencion pre-HTTP: rastro en ledger tras corrida 1 = []; creates enviados en total = 2`

**Latente:** `keyword_archivo_manual` tiene 0 filas en producción.

### 12. Menores (dato, no dinero)
- **El bid decidido no es el bid aplicado (regla 2).**
  - `decision.new_value` se guarda con 4–6 decimales y a Amazon va redondeado a 2 (half-even); por ejemplo 27.025 → 27.02.
  - Sonda: 428 de 485 bids aplicados tienen un `new_value` que nunca existió en Amazon.
  - `v_cambio_bid.bid_despues` (migración 0067) muestra ese valor.
  - Diferencia de medio centavo como máximo, pero son dos números para el mismo hecho.
- **El smoke sella «ok» un 207 con rechazo por ítem** (`tools/smoke_apply.py:498-501`).
  - Prueba: `test_sub_b_smoke_207_rechazo_sella_ok.py`.
  - En producción no ha pasado.

## Sospechas (no reproducidas)
- **Consistencia eventual del LIST.** `_paso_readback` lee la keyword milisegundos después de crearla. Si Amazon aún no la lista, el harvest se revierte (borra keyword y negative). En producción, 11 de 11 harvests terminaron bien.
- **429 repetido.** Un 429 cuatro veces seguidas sale como `AdsApiError`, igual que un 5xx «ambiguo». Aborta toda la fase de apply del ciclo y las decisiones de bid de ese ciclo se pierden, aunque un 429 significa «no procesado». En la siguiente reconciliación aparece como divergencia.
- **El veto espera al ciclo.** Mientras el ciclo re-valida un corte vencido (con HTTP a Amazon), tiene la fila tomada dentro de su transacción. El veto del dueño se queda esperando y luego recibe «en vuelo». No hay pérdida, pero la pantalla se cuelga.
- **Reintento de un negative ya rechazado.** Si un `_ejecuta_negative` sellado «fallo http 400» queda con la fila `applying` por un rollback posterior en el mismo barrido, `_reconcilia_negativas` vuelve a postear un negative que Amazon ya rechazó (`app/apply_cola.py:1472-1478` + `apply_harvest_reconciliacion.py:441-462`).
- **`archivar.leer_anuncios` ignora `nextToken`** (`archivar.py:147-165`). Una página truncada da «no existe» falso.
- **Monedas mezcladas en propuestas.** `propuestas_campana._SQL_AGREGADO_ASOF` (:183) suma sin agrupar por moneda (`min(metric_currency)`).
- **Lectura ilegible tomada como descarte.** `apply_producto.revalida_pause_producto` (:126-130) toma un 200 ilegible como `ya_no_califica` final, cuando debería quedar en espera.
- **La reconciliación de bloqueos falla en silencio.** `bloqueo_dueno_efecto.main` (:171-176) oculta la causa, y si falla crear el cliente aborta todas las claves sin rastro.

## Estructura: movimientos que borran complejidad

1. **Una sola «mutación sellada».**
   - *Problema:* el patrón ledger → commit → HTTP → `except AdsApiErrorMutacion` → sello → relectura está copiado 14 veces en 5 archivos (apply 2, apply_cola 4, apply_harvest 5, reconciliación 1, producto 1; más smoke). Las copias ya divergieron:
     - `_ejecuta_negative` no lee el error por ítem y las otras dos copias que crean negatives sí;
     - el bloqueo no lo lee;
     - el smoke sella ok cualquier 2xx.
   - *Movimiento:* `muta(conn, decision_id, tipo, payload, quota, enviar, verificar) -> Desenlace`.
   - *Qué desaparece:* unas 400 líneas y 3 de las fallas (9, 12b y la parte de 207 de la 2).
   - *Riesgo:* el harvest guarda el ack sin sellar (hermanas). Esa variante tiene que caber como parámetro, no como copia.

2. **Un solo lector de LIST que diga si leyó todo.**
   - *Problema:* hay 5 paginadores con reglas distintas: `_lista_todos` (7 usos, corta callado), `_fila_de_readback`, `_lista_completa`, `_lista_filtrada` y `archivar.leer_anuncios` (no pagina). Además `_bid_leido`, `_estado_leido` y `_fila_de_lista` solo los usan las pruebas.
   - *Movimiento:* un `lista(cliente, path, filtro) -> (items, completa)` donde «incompleta» nunca autoriza POST ni borrado.
   - *Qué desaparece:* unas 150 líneas y la falla 10.
   - *Riesgo:* bajo. Las firmas cambian en 10 lugares.

3. **Un solo lugar que resuelve goal y modo.**
   - *Problema:* el `Goal` se arma desde `_SQL_GOALS_ENTIDAD` 4 veces (`apply.py:1241`, `apply_cola.py:486` y `:929`, `apply_harvest.py:783`). La regla «escalera ∧ goal» está 2 veces (`Aplicador.modo_efectivo` y `apply_cola._meet_goals`). Para preguntar el modo se fabrica una `DecisionBid(id=0, None…)` falsa (`apply_cola.py:527`).
   - *Movimiento:* `goals.goal_resuelto(conn, platform, campaign_id)` + `modo(conn, entidad)`.
   - *Qué desaparece:* unas 80 líneas y la decisión falsa.
   - *Riesgo:* bajo.

4. **Una sola puerta al cliente de escritura, en vez de un candado de imports.**
   - *Problema:* como solo `apply.py` puede importar `write.py`, a `apply.py` (2,479 líneas) le cayeron cosas ajenas:
     - los ajustes de campaña (líneas 1925-2480, unas 555 líneas);
     - el regreso del dueño;
     - las excepciones de `api_write`;
     - reexportaciones (`ESTADO_PUT_*`) y envoltorios (`_cliente_mutacion`, `_cliente_reversa`, `_cliente_ajuste`, `preparar_escritor`).

     Aun así, el candado no ve la fábrica ni las herramientas (falla 2). Hay 10 copias del lector de errores 207 (write, archivar, apply_harvest, archiva_inertes, reactiva_campanas y cuatro sondas).
   - *Movimiento:* `app/ads/escritor.py` como único constructor, con el modo y el perfil resueltos. El candado de pruebas cambia a «nadie más construye el cliente ni manda HTTP a advertising-api». Ajustes y regreso salen a sus propios módulos. Fábrica y herramientas usan ese cliente.
   - *Qué desaparece:* los 3 constructores duplicados, las reexportaciones, unos 550 líneas fuera de `apply.py` y 9 de los 10 lectores de 207.
   - *Riesgo:* medio. Es el cambio más grande; conviene hacerlo por caller.

5. **Borrar espejos y envoltorios que solo existen por los ciclos de imports.**
   - *Problema:* `_SQL_DESCARTA`, `_SQL_PAUSE_PROPIO`, `_SQL_INSERT_REACTIVACION` y `_SQL_CACHE_ESTADO` están 2–3 veces (apply_cola, reconciliación, producto). `_termina` está 3 veces. El sello de «pausa aplicada» está 3 veces. Los envoltorios `revalida_harvest` y `reconcilia_harvest` en apply_harvest solo delegan. `consume_quota` e `intentos_sin_sello` solo los usan las pruebas. La reconciliación de pausas vive en el módulo de harvest.
   - *Movimiento:* un módulo de SQL de cola compartido y la reconciliación de cada tipo junto a su ejecución.
   - *Qué desaparece:* unas 120 líneas.
   - *Riesgo:* bajo.

6. **Retirar el CLI `archivar-anuncios` / `reponer-anuncios`.**
   - *Problema:* unas 240 líneas (`archivar.py:411-508`, `cli.py:240-330` y `679-730`) repiten sin ledger lo que `retiro_anuncios` ya hace con ledger.
   - *Movimiento:* borrarlas y usar `retiro_anuncios`.
   - *Qué desaparece:* las 240 líneas y la falla 3.
   - *Riesgo:* se pierde la ceremonia manual. `retiro_anuncios` tendría que aceptar una lista operada.

Hay otros dos candidatos más chicos:
- en el bloqueo, el «último paso» está escrito 7 veces (5 SQL LATERAL y 2 en Python); una vista borra unas 60 líneas;
- en `retiro_anuncios`, `reconcilia_intenciones` y `resuelve_intencion_de_decision` son la misma secuencia; unirlas borra unas 80 líneas.

## Qué comprobé que está bien
- **Ledger antes del HTTP en el ciclo.** Toda mutación del ciclo (bid, pause, negative, harvest, hermanas, reversas, pausa de product ad) nace con commit antes del HTTP. Cuota y ledger se commitean juntos. Sonda: 0 filas sin sello en 557.
- **Crash entre el HTTP y el sello.** La reconciliación nunca reenvía un bid ni una pausa: relee y confirma o falla. Un negative o un harvest solo se reenvían si la lectura demuestra ausencia (con la salvedad de la falla 10). Un señuelo en otro ad group no cuenta como aplicado.
- **207 en bids y pausas.** Los decide la relectura: una divergencia sella `fallo:divergencia_readback` y `verify_ok = false`. Negatives y harvest leen `error[]` y exigen el id del ack.
- **429 y 5xx.** Un 429 se reintenta dentro del mismo intento, sin cobrar otra vez. Un 5xx o un fallo de red en una mutación no se reintenta y deja el ledger abierto. La cuota es una por operación lógica (harvest = 1) y las reversas están exentas.
- **Veto.**
  - `released` sigue vetable y solo `applying` es «en vuelo»; el claim es atómico.
  - El descarte siempre ocurre antes del cobro.
  - La re-validación usa la ventana fresca del momento de liberar, con el umbral y el piso re-resueltos.
  - Las filas en sombra nunca se seleccionan para escribir; `_SQL_VENCIDAS` filtra `modo = 'live'`, y retiro exige `modo = 'live'`.
- **Moneda y redondeo.** El bid va redondeado a 2 decimales y la moneda se compara con la de la plataforma, que es más estricto que compararla con `goal.bid_currency`. La relectura compara contra el valor redondeado. Sonda: 0 divergencias.
- **Reglas 7 y 21.** `applied_cycle_id` y `applied_count` solo se sellan con `verify_ok`. La reversa no toca `decision_application`, así que no limpia el cooldown. Existe reversa probada para bid, pause, negative, harvest (keyword primero), ajuste de campaña y pausa de product ad (esta última solo la probó el sub-revisor; no hay prueba en la suite).
- **Candado del ciclo.** El `_TransportGuardado` revisa el dueño del lock antes de cada HTTP del cliente del ciclo, incluidos los LIST y el token.
- **Bloqueo del dueño.** Usa un candado de sesión por (ad group, término): dos clics, o un clic contra la reconciliación de :17, dan 409 o saltan la clave. No consume la cuota `negative`.
- **Propuestas de campaña.** No escriben nada a Amazon ni a MeLi.
