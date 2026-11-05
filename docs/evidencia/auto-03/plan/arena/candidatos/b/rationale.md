# AUTO-03, candidato B (sombra completa primero): rationale

## Problem

Construir AUTO-03 (meta ACoS por producto en grupos exclusivos y pausa de product ads de productos sin ventas) sobre tres subsistemas que hoy no se hablan: ciclo y cascada (`app/cycle.py`, `goals.py`), cola de cortes (`apply_cola.py`) y retiro de product ads (`retiro_anuncios.py`), más datos por producto sin colapso ni `as_of` (`ads_product_metric_observation`, `v_margen_producto`). El diseño ya está sintetizado; este plan decide la secuencia y corrige la spec donde el código la contradice (28 restricciones del ground). Restricciones que el plan respeta: ciclo como `app_decide` con REVOKE sobre `impulso*` y `anuncio_retiro*`; `apply_queue` con transiciones selladas por trigger; candado de importadores de `app.ads.write`; `MAX_LINEAS_MODULO=900` con `cycle.py`, `apply_cola.py`, `goals.py` ya en allowlist; migración libre 0076 sobre `origin/master`; el motor corre `desplegar`/`checklist`/`rollback` sin argumentos con `MOTOR_RAIZ` y `MOTOR_SHA`.

## Usage (caller's view)

Lo lee el agente del motor que implementa un bloque viendo solo ese bloque:

- Bloque C: abre `app/optimizer/goals.py`, `app/evaluacion_catalogo.py`, `app/optimizer/windows.py`; crea `app/optimizer/productos.py`, `app/productos_io.py`, `app/cli_productos.py`, `tools/replay_productos.py`. Firmas: `resolver_producto(entrada) -> EvaluacionProducto`, `meta_para_grupo(evaluaciones, grupo_id) -> MetaReferencia | None`, `lee_entradas_productos(conn, plataforma, decidido_el, configuracion, goals)`, `guarda_evaluaciones(conn, cycle_id, entradas, evaluaciones, *, estricto)`, `resumen_cobertura(...)`. Lo acepta `pytest tests/test_productos.py` más `ruff` y `compileall`.
- Bloque D: abre `app/cycle.py` (`_recorre_plataforma`, `_corre_fases`), `app/config_write.py`, `app/api_dashboard.py`; el cambio de firma es `proxima_config(..., producto_modo=...)`. Lo acepta `pytest tests/test_cycle_productos_sombra.py`.
- Bloque F: abre `goals.py` (cuatro firmas con `target_margen_producto` keyword-only), los consumidores del peldaño y `cycle.py` (cuatro llamadas). Lo acepta `pytest tests/test_cycle_target_producto.py`.
- Bloque G: abre `retiro_anuncios.py`, `api_fabrica.py`, `impulso_io.py`; la firma que cambia es `ejecuta_retiro(conn, plan, *, autorizacion, ...)` y `repone(..., autorizacion, permite_recrear=True)`.
- Bloque H: abre `apply_cola.py`, `apply_harvest_reconciliacion.py`, `optimizer/replay.py`; crea `app/pausa_producto.py`. Lo acepta `pytest tests/test_pausa_producto.py`.

Del lado de David, el uso es: `productos-evalua --platform P --json` (lectura), `productos-modo --platform P --target|--pausa off|shadow|live` (único escritor de `config_version`), pantalla de productos y la reposición existente `POST /retiro/reponer`.

## Shape

**Escalera por interruptor, no por capacidad.** Dos claves por plataforma, `ads_producto_target_<plat>` y `ads_producto_pausa_<plat>` (ausente = off; shadow = evalúa y guarda sin efecto; live = efecto). Cada release enciende un escalón con su reversa construida antes: D evalúa en sombra (target y pausa) y persiste; E mide; F enciende target; G prepara el retiro sin cambiar comportamiento; H enciende pausa.

**Datos primero.** 0076 trae `evaluacion_producto_ciclo` (append-only, invariante de fecha en trigger UTC, único por `(cycle_id, platform, product_id)`) y la vista `v_ad_group_impulso_abierto` (un solo lugar para "impulso abierto", que `retiro_anuncios._SQL_ANUNCIOS` y la lectura de productos comparten, sin tocar el REVOKE de `impulso`). 0077 (CHECK de `target_acos_ciclo` con `margen_producto` y `decision.evaluacion_id`) va justo antes del código que la usa (F). 0078 (motivo y `decision_id` de la intención y grants acotados de `app_decide`) va en G. Cada migración lleva `_reversa_` que aborta con filas (regla 7) y su primera línea `-- aplicada:` que `lib.sh` ejecuta: la lista de migraciones se deriva de los archivos, no se mantiene a mano.

**Módulo profundo.** `productos.py` es la política pura (tipos de la spec, `resolver_producto`, `meta_para_grupo`); `productos_io.py` esconde SQL, colapso, madurez, cobertura por fecha, topología y huella; el ciclo hace una carga y una resolución antes de recorrer hojas. El ciclo solo ve `EvaluacionProducto`. El colapso y la madurez no se duplican: `evaluacion_catalogo` publica `colapsa_ultima_observacion` y `fecha_madura` y `evaluar_ads` los usa. Una sola fuente de margen (`v_margen_producto`). `resumen_cobertura` es la única función de conteo, para las notas del ciclo y para la CLI.

**Un dueño por estado en la pausa.** La decisión es kind `pause` con entidad `product_ad` (sin `ALTER TYPE`; misma familia `entity_cut`, misma cuota `ads_apply_cap_<p>_pause`, mismo trigger de madurez, que se cumple porque la ventana termina en D-30). La cola sigue siendo dueña de veto, claim, cuota y cierre; `retiro_anuncios` es dueño del PUT y de la intención; `pausa_producto.py` solo despacha y cierra la fila de cola desde la intención confirmada. Cada fila de cola es un anuncio, así que "4/5" es agregación por producto en la pantalla. Un solo encolador: `encola_cortes` lee la decisión por `_SQL_CORTES_CICLO`.

**Peldaño nuevo con todos sus consumidores en F:** `PELDANOS_CASCADA`, CHECK, `PISA_POR_PELDANO` y `_FUENTES_COMPARTIDAS` (derivados de la tupla), `ui.py`, cuatro llamadas del ciclo, `_target_pause_vigente` (usa el target congelado más la huella), el test estático de paridad. El replay de bids no cambia porque el target viaja en `inputs.caso`; `reproduce` gana la rama `motor = producto` para la pausa en H, con freeze en el mismo cambio.

**Qué no hace:** no segundo encolador, no segundo escritor de `config_version`, no kind nuevo, no refactor de `cycle.py`/`apply_cola.py` (solo despachos delgados), no stock, no reactivación automática.

## Correcciones a la spec que propone el plan (fila A.2)

1. El ciclo llama `cascada_target_acos` y `peldano_target_acos`; el peldaño es `target_margen_producto` keyword-only, sin `cache_acos_target` (muerto).
2. Kind `pause` con entidad `product_ad`, no `pause_product_ad`.
3. La ejecución corre en `libera_vencidos` como `app_decide` con grants acotados (0078) y vista de impulso; no como `app_admin`.
4. `decision.evaluacion_id` FK nullable (la spec lo pedía; el ground añade migración y grant).
5. Madurez de 30 días solo en la ventana de rendimiento; el halo reciente se declara no maduro.
6. `ad_ids` solo pertenencia; ligadura por ASIN con `sku = listing.seller_sku`.
7. Fecha sin fila es cero solo con run ok que la cubre (la regla literal de la spec nunca pausaría a nadie).
8. R3 compara contra el equilibrio de plataforma, declarado en el snapshot.
9. "Al graduarse entra por la misma evaluación" es falso: el goal del impulso conserva su target.
10. El piso de 10 no aplica: función propia con `MARGEN_BANDA_MAX`, `fraccion_desde_settings` y `motivo_dato_invalido` públicos.
11. `ejecuta_retiro` devuelve resultado por anuncio y `repone` no recrea para el motivo nuevo (`requiere_recreacion`).

## Restricciones [decisión del dueño] y su supuesto reversible

| Restricción | Supuesto fijado | Reversa |
|---|---|---|
| 3 goal explícito | S1: gana todo goal con target no nulo, fábrica incluida; E mide cuántas hojas pisa | Interruptor target en off/shadow |
| 5 ancla y paso | S2: sin paso; al abstenerse cae al siguiente peldaño | Constante y versión `politica` |
| 8 alcance de pausa | S5: product ads ENABLED, `first_seen_at` anterior a la ventana y en `ad_ids` | Interruptor pausa |
| 9 SKU | `sku = seller_sku` o abstención (`sku_distinto_en_asin`) | Versión `politica` |
| 10 fecha ausente | S3: cero solo con run ok `written` que la cubre y observación madura; E reporta también la regla estricta | Versión `politica` |
| 13 pisos | S4: 100 clics fijos y `PAUSE_COST_MIN`, sin adaptativo | Versión `politica` |
| 15 perímetro de cola | S6: se amplía y queda sellado en `docs/APPLY.md` (H.8) | Interruptor pausa descarta lo pendiente |
| 16 permisos | Grants acotados a `app_decide` (0078) y vista de impulso; REVOKE de `impulso*` intacto | Reversa 0078 |
| 20 autorización | `AutorizacionHumana` o `AutorizacionDeDecision`; el literal del vigía vale como humana | Código |
| 22 autoría | S7: sin escritura propia = adoptado, no habilita reposición; solo para el motivo nuevo | Código |
| 11 madurez de hojas | S8: unión de ventanas; historia corta = no apto (hoy 88 días de Ads por producto frente a D-90, así que se mide cero hasta ~6-nov y se activa solo) | Versión `politica` |

Las claves `politica` quedan congeladas en cada evaluación, por lo que cambiar una regla es una versión nueva y el replay de las viejas sigue fiel.

## Chequeo de banderas rojas

- Módulo superficial: `productos.py` expone una función pura y los tipos; la lectura expone una función por operación (leer, guardar, resumir). Sin opciones que expongan etapas. `pausa_producto.py` es un cierre, no pass-through: añade revalidación y reconciliación.
- Fuga de información: el ciclo no conoce SQL de producto ni de Amazon; la pantalla lee evaluaciones persistidas y no llama al resolutor; `app.ads.write` sigue con sus importadores (el escritor viene de `archivar.preparar_escritor`).
- Descomposición temporal: se evitó separar "carga, valida, guarda" en tres módulos; `productos_io` los junta por conocimiento (identidad, ventana, cobertura). Riesgo residual: `productos_io.py` puede acercarse a 900 líneas; si pasa, se parte por mercado de datos (margen / Ads / topología), no por etapa.
- Pass-through: `ejecuta_retiro` sobre `retira_anuncios` añade la política de lanzar; no se mantiene un envoltorio sin función.
- Dueño partido: sin segundo encolador, sin segundo escritor de config, una sola reserva de cuota, el cierre de fila lo hace solo `pausa_producto`, el cierre de intención solo `retiro_anuncios`.
- Dos caminos: un peldaño, una cascada; la pausa por `pause` existente; la autoría y la recreación se arreglan en `retiro_anuncios` y no se duplican.
- Internals importables: `_motivo_dato_invalido`, `_fecha_madura` y `_lee_goals` pasan a públicos con su único cuerpo en vez de importarse con guion bajo.
- Lista sincronizada a mano: `PISA_POR_PELDANO` y `_FUENTES_COMPARTIDAS` se derivan de `PELDANOS_CASCADA`; `lib.sh` deriva las migraciones por glob y la lista de `COPY tools/` del Dockerfile.

## Synthesis decision

La llena el lead.

## Tradeoffs accepted

- Aceptamos 8 bloques y cuatro releases a cambio de que cada escalón sea comprobable con datos reales y reversible por interruptor antes del siguiente.
- Aceptamos que la sombra persista evaluaciones antes de que algo las consuma, a cambio de que replay, pantalla y medición de cobertura existan antes de cualquier efecto.
- Aceptamos que F y H enciendan `live` aunque la cobertura medida sea cero: con cero grupos aptos no hay efecto y se activa solo cuando los datos alcanzan (ventana de 90 días de Ads por producto). A cambio nadie tiene que acordarse de encender; el costo es un efecto sin aviso humano cuando llegue la cobertura. La pantalla y `notes.productos` lo muestran cada ciclo.
- Aceptamos no esperar un ciclo natural entre D y F; la ruta de persistencia se cubre con la prueba D.4 y los privilegios del checklist, y un fallo de TX3 con `estricto=True` falla cerrado (un día sin decisiones, no decisiones sin evidencia).
- Aceptamos ampliar grants de `app_decide` sobre `anuncio_retiro*` (revoke deliberado de 0066/0069) a cambio de una sola conexión, un solo protocolo de intento-intención-cierre y sin dos roles en un mismo efecto.
- Aceptamos que el peldaño casi no alcance campañas de la fábrica mientras S1 mande; está medido y es una decisión visible.
- Aceptamos el veto de 48 h y la cuota de 2 pausas diarias como observación en vivo previa al primer PUT, en lugar de una sombra larga de la cola.

## Alternatives considered

- **Semilla A, rebanadas por capacidad (todo el target hasta live y luego toda la pausa).** Perdió para mí porque duplica el cableado del ciclo dos veces (TX2, TX3, notas, pantalla) y deja la pausa, que es la parte con dinero y escritura en Amazon, sin ninguna evidencia real hasta el final; B comparte la lectura, la evaluación persistida, el replay y la medición con ambos escalones. A gana en un punto: entrega el target antes y con menos superficie en los primeros bloques. Se descartó porque el target casi no tiene cobertura (S1) y entregarlo primero entrega poco valor.
- **Kind nuevo `pause_product_ad` (opción A del ground).** Descartado: toca diez sitios entre enum, CHECKs, triggers, mapa de cuota y `KINDS_DECISION`, con el peligro de que `_revalida` lo trate como harvest.
- **Ejecutor `app_admin` aparte para las pausas (opción b del gotcha 16).** Descartado: parte un efecto entre dos roles y dos conexiones y obliga a un segundo protocolo de cierre; la cola necesita transiciones de `app_decide`.
- **Reutilizar `resuelve_target_margen`.** Descartado: recorta a [10,45] y baja con paso; rompe el caso 8% → 4%.

## Open questions and risks

- ¿Los goals de la fábrica y del impulso cuentan como "explícitos" (S1)? Si la medición E da cero hojas efectivas, el peldaño nunca actúa; quitar el target de esos goals o ponerlos debajo del producto es decisión de David, no de este plan.
- ¿Basta la regla S3 (cero por run ok que cubre la fecha) y los pisos de 100 clics y 500 MXN o 40 USD para una pausa automática por producto?
- ¿Se acepta ampliar el perímetro de la cola y los grants de `app_decide` (S6)?
- Riesgo: precondición de repo. Spec, ground y lectura están sin versionar en el checkout del lead; A.1 falla si no se suben antes.
- Riesgo: la ruta de persistencia solo se ejercita en producción en el primer ciclo natural después de D; si falla con `estricto=True` en F, ese ciclo no deja decisiones. Mitigación sugerida fuera del plan: un chequeo `at` posterior al ciclo de las 08:40 UTC con aviso por Telegram.
- Riesgo: `apply_cola.py` (1345), `cycle.py` (3032) y `goals.py` (1051) crecen unas líneas de despacho; no se parten en este plan.
- Riesgo: la conciliación H.6 supone que Amazon devuelve a la fecha cifras iguales (2%) a las colapsadas a D+30; ventas de atribución tardía pueden mover el número.
- Riesgo: el cuerpo de `ad_ids` (forma del JSONB) se confirma con `sondas/c.sql`; si no es lista de ids, S5 se vuelve "todos los ENABLED con identidad" y debe decirlo la medición.
- No se verificaron en producción las fracciones 0.5 y 0.8 (solo la lectura del 25-oct) ni el número 0076 libre.

## Next implementation step

Subir spec, ground y lectura a master y entregar el bloque A al motor; con A.2 y A.3 sellados y la medición previa en `previa.md`, escribir la 0076 con su prueba.

## Salida de `motor valida`

```
$ cd ~/dev/goncloud-motor && python3 -m motor valida docs/evidencia/auto-03/plan/arena/candidatos/b/plan.md --home ~/.motor
ok  decisiones-producto-01: 8 bloques A(fast), B(gate), C(gate), D(release), E(fast), F(release), G(release), H(release)
```
