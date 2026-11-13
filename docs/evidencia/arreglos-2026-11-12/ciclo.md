# Revisión del ciclo del optimizador (orquestación)

Código revisado: worktree `wt-orbit-revision` en master `a4644aad` (igual al desplegado).
Pruebas de reproducción: `docs/evidencia/arreglos-2026-11-12/pruebas/ciclo/test_revision_ciclo.py` (5 pruebas, las 5 fallan
por la falla que describen). Comando:

```
PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q docs/evidencia/arreglos-2026-11-12/pruebas/ciclo/ -s
```

## Cómo funciona

1. El cron llama `python -m app.cli cycle --platform amazon_us` a las 8:40, 10:40, 13:40 y 16:40 UTC (MX un minuto después).
2. Antes de abrir nada, el comando revisa dos cosas: que hoy haya llegado la ingesta con datos de ayer (`app/metricas_listas.py`) y que hoy no haya ya un ciclo `done` o `degraded`, ni uno `running` de menos de 3 h (`app/cycle.py:247-269`). En la práctica corre **una vez al día** (8:40). Los otros tres horarios solo sirven de reintento.
3. Toma un candado por plataforma (TTL 30 min, renovado con latidos) y abre el registro del ciclo (`optimizer_cycle`, estado `running`): TX1.
4. TX2 lee todo en una foto fija (REPEATABLE READ): guardas de frescura, metas (target) por plataforma, familia y producto, y luego decide hoja por hoja (bid o pausa) y ad group por ad group (negative / harvest). No escribe nada.
5. TX3 guarda en una sola transacción las decisiones, el target congelado de cada hoja y cierra el ciclo como `done` (o `degraded` si saltó una guarda). **Aquí se marca la hora de fin**, antes de tocar Amazon.
6. Fase de apply: mete los cortes (pausas, negatives, harvest) en la cola con veto de 48 h, reconcilia lo que quedó dudoso ayer, aplica los bids de **este** ciclo y libera los cortes cuyo veto ya venció. Cualquier error aquí se anota (`apply_error`) y deja el ciclo en `degraded`, sin tirar las decisiones.
7. Manda por Telegram los avisos y el resumen, vuelve a sellar las notas y suelta el candado.
8. En producción: 185 ciclos, todos `done` (ninguno `failed` ni `degraded` desde agosto). Duran de 2 a 5 s, pero esa cifra no incluye el apply (ver sospecha S5).
9. Impulso (`app/impulso.py`, `app/impulso_io.py`) es un lanzador de dos campañas con tope de gasto de 14 días que el "vigía" (:20) pausa o gradúa. **En producción no hay ni un impulso** (tabla vacía).
10. `app/campana_ajustes.py` planea cambios de presupuesto, placement y gasto fuera de Amazon que el dueño confirma a mano. `app/avisos_campana.py` arma textos. Ninguno de los dos decide solo.

## Fallas (con reproducción)

### F1. Un error 500 de Amazon en un solo bid frena todos los demás bids del día y la liberación de pausas vencidas

- **Qué te pasa:** si Amazon contesta 500 al cambiar el bid de una keyword, el ciclo deja de aplicar los bids que faltaban **y** deja de liberar los cortes cuyas 48 h de veto ya vencieron (pausas de hojas que sangran dinero). Por F2, nadie lo reintenta ese día. Los bids de ese ciclo quedan huérfanos para siempre: el ciclo de mañana solo aplica sus propios bids (`apply.bids_del_ciclo` filtra por `cycle_id`).
- **Dónde:** `app/cycle.py:3513-3591`. `reconcilia_harvest`, `reconcilia_bids`, `aplica_bids` y `libera_vencidos` viven dentro de un solo `try`. El 500 sube desde `apply.Aplicador._ejecuta_mutacion` (`app/apply.py:1422-1423`, diseñado así: "5xx SUBE") y el `except Exception` de la línea 3587 aborta la fase entera.
- **Prueba:** `test_un_5xx_en_un_bid_frena_los_demas_bids_y_la_cola`. Salida:
  ```
  status: degraded | bids decididos: 2 | PUTs: 1
  notes.apply: {'cortes_encolados': {...}, 'apply_error': 'status=500 sin retry ...'}   <- sin 'cortes_liberados'
  AssertionError: un 5xx en un bid dejo 1 bid(s) del ciclo sin intentar (y la cola de cortes sin liberar)
  ```
- **Qué debería pasar:** la regla sellada es "no reintentar **esa** mutación". El 500 de una keyword no debería impedir las demás ni la liberación de la cola. Cada paso (reconciliar, bids, cola) o cada bid debería aislar su propio fallo.

### F2. Si la fase de apply falla una vez, el ciclo no se reintenta en todo el día

- **Qué te pasa:** basta un tropiezo de red al pedir el perfil a Amazon, un 500 (F1) o un secreto ilegible a las 8:40 para que el ciclo quede `degraded`. La guarda del cron trata `degraded` igual que `done`: a las 10:40, 13:40 y 16:40 dice "ya hay un ciclo degraded hoy" y no corre. Resultado: un día sin cambios de bid y sin liberar pausas vencidas, aunque el problema haya durado un minuto. Los cuatro horarios existen justo para reintentar.
- **Dónde:** `app/cycle.py:265-266` (`motivo_de_salto`). Ahí `degraded` se usa para dos cosas distintas: "datos viejos" (no tiene caso reintentar) y "falló el apply" (sí lo tiene). Se marca `degraded` en `app/cycle.py:3864-3865` y en `_SQL_SELLA_APPLY` (`:420-425`).
- **Prueba:** `test_ciclo_degradado_por_apply_bloquea_los_otros_horarios_del_dia`. Salida:
  ```
  AssertionError: el siguiente horario del cron NO reintenta:
  'cycle amazon_us: ya hay un ciclo degraded hoy; espera al siguiente horario'
  ```
- **Qué debería pasar:** un ciclo cuyo apply falló debería permitir el reintento del siguiente horario, que aplicaría lo pendiente. Otra opción: el siguiente horario corre solo la fase de apply sobre las decisiones de hoy.

### F3. Si el proceso muere a mitad del ciclo, el siguiente horario también se pierde

- **Qué te pasa:** un deploy o un reinicio del contenedor a las 8:40 deja el ciclo en `running`. El candado vence a los 30 min (9:10), pero la guarda del cron respeta cualquier `running` de menos de **3 h**. A las 10:40 (2 h después) se salta, así que el primer reintento real es a las 13:40. Si muere a las 10:40 o a las 13:40, el siguiente horario cae justo en el borde de 3 h y se decide por segundos.
- **Dónde:** `app/cycle.py:247-268`. Hay dos fuentes para la misma pregunta, "¿sigue vivo?": el latido del candado (30 min) y la regla de 3 h, que contradice la regla 2 (un número, una fuente).
- **Prueba:** `test_running_huerfano_con_lock_vencido_bloquea_el_siguiente_horario`. Salida:
  ```
  lock vencido: True
  AssertionError: el horario siguiente se salta con el lock ya vencido:
  'cycle amazon_us: ciclo running iniciado hace menos de 3 h'
  ```
- **Qué debería pasar:** que decida el candado. Si el latido venció, el siguiente horario corre (el claim y el "rastro" ya cierran el ciclo muerto como `failed`, `app/cycle.py:385-393`).

### F4. El ciclo decide antes de reconciliar, al revés de lo que dice CONTEXTO

- **Qué te pasa:** CONTEXTO dice "Reconciliación al inicio del ciclo contra Amazon VIVO". El código decide primero (TX2) y reconcilia después, ya en la fase de apply. Ejemplo: Amazon aplicó un bid pero contestó 500. Al día siguiente la hoja se salta como "bid en vuelo" y, segundos después, ese mismo ciclo confirma que el bid sí quedó. La hoja pierde un día de decisión con un dato que ya estaba resuelto, y el caché de bid con el que se decidieron las demás también iba sin reconciliar.
- **Dónde:** se lee `hojas_bid_en_vuelo` en TX2 (`app/cycle.py:3288`) y se reconcilia en `_fase_apply` (`app/cycle.py:3525-3528`).
- **Prueba:** `test_reconciliacion_corre_despues_de_decidir`. Salida:
  ```
  AssertionError: el mismo ciclo salta la hoja por 'bid en vuelo' y luego confirma ese bid
  assert not (1 and 1)
  ```
- **Qué debería pasar:** reconciliar (bids, pausas y harvest dudosos) antes de TX2, con el candado ya tomado.

### F5. Vaivén de bids en producción: el "piso aprendido" se cruza por un redondeo (frontera con la política)

- **Qué te pasa:** CONTEXTO dice que, tras un "regreso por desplome", ese piso **no se vuelve a cruzar hacia abajo**. En producción sí se cruza. Hoja 4934: 0.33 → 0.29 → regresa a 0.33 → 0.29 → regresa → 0.29 → regresa (10-17, 10-25, 10-30, 11-07, 11-12). Cada vuelta deja la hoja ~5 días en el bid donde ya había perdido el tráfico. Sonda: **4 de las 8 hojas con un regreso** recibieron después un recorte que, redondeado, cayó en o debajo de su piso (4924, 4934, 5336 y 5895), y en los 4 casos el clamp no lo frenó solo por decimales.
- **Por qué:** el recorte se calcula sin redondear (0.33 × 0.88 = 0.2904). Como 0.2904 > 0.29, pasa el clamp del piso (`app/optimizer/politica.py:636`). Luego `app/ads/write.py:187` (`_bid_payload`) lo redondea a 2 decimales y Amazon recibe **0.29**, el piso exacto.
- **Prueba:** `test_piso_aprendido_se_cruza_por_redondeo_de_amazon`. Salida:
  ```
  veredicto: Mover(bid_nuevo=Decimal('0.290400'), motivo='grupo_sangra_vendedora') | lo que recibe Amazon: 0.29
  AssertionError: el recorte manda 0.29 a Amazon: igual o debajo del piso aprendido 0.29
  ```
  Sonda de producción (resumen): `hojas=4, recortes_que_cruzan=4, pasaron_clamp_por_decimales=4`.
- **Qué debería pasar:** comparar contra el piso el bid **tal como lo recibirá Amazon** (cuantizado a 0.01), o cuantizar `bid_nuevo` en la política. El código vive en `app/optimizer/*`, que es el área de otro revisor, pero el daño se ve en el ciclo y en producción.

## Sospechas (no reproducidas, o de otra área)

- **S1. Con datos viejos el ciclo igual libera cortes.** Si salta la guarda de frescura (marca de datos de más de 7 días o estructura de más de 48 h), el ciclo queda `degraded` sin decidir, pero en live **sí** corre la fase de apply: reconcilia y libera pausas, negatives y harvest vencidos (`app/cycle.py:3787-3860`; la guarda solo corta TX2). La revalidación usa "evidencia fresca" que en ese momento no es fresca. No lo reproduje.
- **S2. Filas shadow que bloquean para siempre.** Una fila de cola en `shadow` + `pending_veto` nunca se libera ni vence, y `apply_cola._SQL_CLAVES_BLOQUEADAS` (`app/apply_cola.py:160-166`) no filtra por modo. Si un goal de campaña está en shadow cuando se decide una pausa, la hoja queda fuera del motor (`veto_pendiente`, también para bids) aunque el goal pase a live después, hasta que un admin descarte la fila. Hoy hay 31 filas así de product ads (pausa por producto en shadow); esas sí tienen un descarte al encender (I.7). Las de keyword no lo tienen. Área de apply.
- **S3. Venta reciente contada como "veto pendiente".** Una pausa frenada por venta reciente (protección M1) se cuenta como `veto_pendiente` en las notas (`app/cycle.py:2203-2208`). Con la política apagada se cuenta con su propio motivo (`:2096-2108`). Salud y el digest atribuyen al veto lo que es protección. Comprobado leyendo el código; no mueve dinero.
- **S4. Una errata en el modo global apaga el optimizador sin aviso.** Si `ads_optimizer_mode` tiene un valor mal escrito, el ciclo sale `skipped` (`app/optimizer/goals.py:473-481`), devuelve 0 al cron, no manda digest (`app/cycle.py:3778-3784`) y se repite en silencio en los 4 horarios. En cambio, una errata en cualquier interruptor por plataforma (`ads_bid_politica_*`, `ads_bid_economia_*`, `ads_producto_*`) tira el ciclo entero con `failed` y exit 1: también se pierden pausas, negatives, harvest y la liberación de la cola. Son dos políticas distintas para el mismo tipo de error. Hoy el modo global solo se edita a mano con SQL (la pantalla de settings no lo toca).
- **S5. La duración del ciclo no incluye Amazon.** `finished_at` se escribe en TX3, antes de la fase de apply (`app/cycle.py:404-408`). Los 2-5 s de producción no miden el apply, y un apply colgado durante horas se ve en Salud como un ciclo `done` sano.
- **S6. Impulso (sin uso en producción).** El veredicto `clics_sin_ventas` es **final** y se decide con pedidos de D-1, que todavía no maduran: la venta atribuida tarda 5-8 días, y la regla 6 pide 10 días para cortar (`app/impulso.py:187-190`). Un producto que vendió el día 12 y cuya venta llega el día 18 ya no se gradúa (`_SQL_VIVOS` excluye las lecturas finales). Además, sin filas de sus campañas el acumulado cuenta como **0** en vez de "sin dato" (`app/impulso_io.py:964-965`), así que unas campañas sin vincular terminan pausadas como `sin_impresiones` a los 14 días.
- **S7. Presupuesto de campaña sin tope.** `campana_ajustes.planea_ajuste` acepta cualquier presupuesto > 0 (`app/campana_ajustes.py:137-149`). Lo confirma el dueño, pero un cero de más pasa igual. Hay dos comparadores de lectura distintos para el mismo ajuste: `coincide_campo` compara un solo campo y `coincide_clase` compara estrategia y los tres porcentajes (`:214-248`).
- **S8. Mensaje de error engañoso.** El texto del error dice "POST no idempotente" para un **PUT** de bid (se ve en la salida de F1). Confunde a quien lea el log o la nota `apply_error`.

## Estructura: movimientos que borran complejidad

`app/cycle.py` tiene 4,011 líneas, 29 constantes SQL, 13 clases, 11 `except Exception` y 17 lecturas de interruptores de settings. Sus funciones más grandes: `_procesa_decisora` (401 líneas, 33 ramas, **25 parámetros**), `_recorre_plataforma` (193), `_corre_fases` (168), `_fase_notifica` (152 líneas, 30 ramas), `_fase_apply` (152) y `_procesa_grupo` (147). En un solo archivo conviven unos 15 conceptos: candado, latido, registro del ciclo, guardas, gates de elegibilidad, cascada de target (plataforma, familia, producto), juicio de hoja, higiene de términos, destino de harvest y avisos de grupo, evaluación por producto, sombra de la meta, protección de venta, serialización del congelado, apply, Telegram y propuestas de campaña.

1. **Escribir el registro del ciclo una sola vez, al final.**
   - Hoy: las notas se escriben 2 o 3 veces por ciclo (cierre en TX3, re-sello post-apply y re-sello por el ancla del digest). Para no perder lo que se escribió entre medias hacen falta `_mezcla_evidencias_persistidas` (42 líneas), `_SQL_SELLA_APPLY`, la lógica `ancla_avanzo` y tres guardas `status='running'` distintas.
   - Movimiento: TX3 guarda solo las decisiones, el ciclo sigue `running` durante el apply y se cierra una vez al terminar, con el estado real.
   - Desaparece: el merge desde la base, el segundo y tercer sello y un `finished_at` engañoso (S5). Además F2 se resuelve sola: un proceso que muere en el apply deja `running` → `failed` por rastro → el siguiente horario reintenta.
   - Riesgo: un ciclo muerto en el apply volvería a decidir en el reintento. Las decisiones ya en cola quedan bloqueadas por clave y los bids, por la espera de 7 días de la política.

2. **Una sola fuente de "¿ya corrió hoy / sigue vivo?".**
   - Hoy: `motivo_de_salto` (3 h) duplica al candado (TTL 30 min) y mezcla "degraded por datos" con "degraded por apply" (F2, F3).
   - Movimiento: el cron solo pregunta "¿hay un ciclo `done` hoy?". Lo demás lo decide el claim (`CicloOcupado` ya sale con exit 0).
   - Desaparece: la regla de 3 h, la consulta `_SQL_ULTIMO_CICLO_HOY` con su doble rama y la ambigüedad de `degraded`.
   - Riesgo: un `degraded` por datos viejos se reintentaría cada horario. Es barato (2-5 s) y además es lo que quieres si la ingesta llega tarde.

3. **Borrar los caminos apagados que producción ya no corre.**
   - Hoy, con los valores reales de settings:
     - Política de bids apagada (`politica is None`): ~80 líneas en `_procesa_decisora`, todo `_pendiente_pause_apagada` (62 líneas), una rama de `_juicio_hoja` y `MOTIVO_POLITICA_APAGADA`. Ambas plataformas están en `niveles_v3`.
     - Meta por producto en **shadow**: la rama shadow de `_anota_sombra` (100 líneas), `_juicio_hoja` (56) y tres funciones `decide_con` armadas **por cada hoja** en el camino caliente. Ambas plataformas están en live.
     - Economía `auto03`: ramas en `_margen_familia_de_hoja` y en `apply_cola`. Ambas plataformas están en `producto_directo_v1`.
     - `ads_pause_sin_cooldown_bid`: ya **no lo lee nadie**, aunque producción lo tiene en `true` y el docstring del módulo (`app/cycle.py:60-63`) todavía describe su efecto.
   - Movimiento: quitar los modos que no están vivos y dejar como interruptor de emergencia solo el modo global (off/shadow/live), que ya existe.
   - Desaparece: unas 350-450 líneas de `cycle.py`, 4 interruptores y sus validaciones, y la mitad de los parámetros de `_procesa_decisora`.
   - Riesgo: perder "apagar bids sin apagar pausas" y la posibilidad de probar en shadow la meta de una plataforma nueva. Es decisión del dueño: hoy nadie los usa.

4. **Una sola función de elegibilidad.**
   - Hoy, "¿esta entidad entra y en qué modo?" se calcula en al menos cuatro lugares: `cycle._gates_entidad`, `productos_io` (`goal_no_live`, `app/productos_io.py:434-437`), `apply_cola._modo_efectivo_corte`/`_meet_goals` y `Aplicador.modo_efectivo`. La lectura de goals está copiada tres veces (`cycle._SQL_GOALS` + `lee_goals`, `propuestas_campana.lee_evaluaciones` (`app/propuestas_campana.py:615-641`) y la de `productos_io`).
   - Movimiento: un `Elegibilidad` (goal resuelto, estado de los ancestros, modo efectivo) en `app/optimizer/goals.py`, con una sola lectura, que usen los cuatro.
   - Desaparece: tres copias del SQL de goals y tres variantes de la cascada de gates.
   - Riesgo: el aplicador vuelve a resolver el modo a propósito al aplicar (el goal puede cambiar en 48 h). Se conserva llamando a la misma función, no copiándola.

5. **`_fase_notifica` como una tabla, no cinco bloques.**
   - Hoy: 152 líneas con cinco bloques `try` casi iguales (enviar, latir, acumular la nota "jamás pisa") y la misma rama del `except` copiada.
   - Movimiento: una lista `(eventos, enviar, clave_nota, texto)` recorrida por un solo ciclo.
   - Desaparece: unas 90 líneas y cuatro copias de la regla de acumular.
   - Riesgo: bajo. El texto de cada nota pasa a ser dato.

6. **Aislar los pasos del apply (arregla F1 y borra el `try` gigante).**
   - Hoy: un solo `try` de 70 líneas mezcla cuatro operaciones independientes (reconciliar harvest, reconciliar bids, aplicar bids, liberar cola) y el `applied_count`.
   - Movimiento: cada paso con su propio resultado y su propio error en `notes.apply`, y la reconciliación movida antes de TX2 (F4).
   - Desaparece: el acoplamiento por el que un 500 de una keyword tira la cola de pausas, y la lectura en TX2 de `hojas_bid_en_vuelo`, que ya llegaría reconciliada.
   - Riesgo: hay que conservar el aborto global por candado perdido (`ApplyAbortado`), que sí debe cortar todo.

## Qué comprobé que está bien

- **Una decisión por hoja por día.** Sonda de 60 días: **ninguna** entidad tiene decisiones en dos ciclos del mismo día, aunque hubo días con un segundo ciclo manual (8-nov a las 17:58, 15-oct, 6-oct). Las claves en cola bloquean, y la espera de 7 días tras un bid de la política se cumple. Los cambios a 5 días de distancia que aparecen en producción son "regresos por desplome" (permitidos), salvo el vaivén de F5.
- **Ingesta a medias.** Una corrida de ingesta solo queda `ok=true` si terminaron **todos** los reportes de **todos** los perfiles (`app/ads/descarga.py:593-635`), y la guarda exige filas de D-1 **de esa plataforma**. Un perfil rechazado no deja pasar a la otra plataforma. El ciclo de las 8:40 no decide con datos de anteayer: sale con 75 y reintenta.
- **Zonas horarias.** A las 8:40 UTC la fecha del marketplace (Pacífico y Ciudad de México) ya es la misma que la UTC. Ningún horario del ciclo cae en el hueco en que difieren. Ventanas, candado y guardas usan reloj UTC fijo.
- **Lecturas de shadow/productos dentro de TX2.** Las lecturas de shadow y de productos que pueden fallar van en savepoint (`_evalua_productos`, la sombra con target None, el destino de harvest, la membresía de grupo) y no abortan la foto de TX2.
- **Transacciones con la conexión de producción (sin autocommit).** TX4 y los sellos entran desde IDLE y confirman de verdad. `envia_avisos_pendientes` deja la conexión limpia, así que no hay sellos perdidos al cerrar el CLI.
- **Candado: robo y liberación.** El claim es una sola sentencia, solo libera quien ganó, el zombie no pisa el cierre del sucesor y el apply revisa el dueño del candado antes de cada HTTP.
- **Pausa por producto en shadow.** Nace `shadow` en la cola aunque el ciclo sea live (`apply_cola._modo_pausa_producto`). Las 31 filas de producción son `modo=shadow` y no se liberan.
- **Moneda del digest.** El "residual tacos" dice MXN y la vista `v_tacos` sí convierte todo a MXN.
- **Ciclos fallidos.** En producción hay cero `failed` y cero `degraded`, así que las fallas F1-F4 están latentes, no ocurridas. F5 sí ocurrió (4 hojas).
