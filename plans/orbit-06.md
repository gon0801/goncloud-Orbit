# ORBIT 06 — consolidación post-cutover: el margen deja de ser una promesa

> **Propósito**: la Fase 3 del diseño v2 (`docs/CONTEXTO.md` §Fases) pide
> "digest diario por Telegram, vista de lectura, **margin-aware targets**".
> El lead verificó el 2026-08-30 que **margin-aware hoy no tiene piso**: las
> tablas existen y están TODAS vacías, no hay ingesta que las llene, y el
> vínculo anuncio→producto está en 0 de 5,899 filas. Este plan construye ese
> piso ANTES de tocar una sola línea de lógica de decisión.
>
> Precedencia: `docs/CONTEXTO.md` (reglas 1-10 y trampas del dominio) >
> `docs/traspaso/ADS_OPTIMIZER_V2_DESIGN.md` (umbrales) > este plan. Un PR
> por tarea. **Aquí NO se cambian umbrales del motor** ni se toca la
> escalera: ORBIT 06 añade dato y lectura; la Fase 2 es la única que decide,
> y nace en shadow.
>
> **Reloj**: la Fase 0 **NO depende del flip** y puede empezar hoy. Las
> Fases 1 y 2 sí: necesitan decisiones live reales para significar algo.
>
> **Reparto** (evidencia de delegación, `CLAUDE.md` global): GLM implementa
> las ingestas y la vista de margen (lógica de dominio, dinero); DeepSeek la
> superficie de lectura del dashboard (server-rendered, sin ssh/push/tracker);
> el lead escribe este plan, revisa cada entrega contra la base viva y cierra.
> Cross-review: 1 ronda por tarea; 2ª SOLO si la 1ª halló severidad alta;
> jamás 3ª.
>
> **v2 (2026-08-30)**: la v1 pasó cross-review simultánea de codex y grok y
> volvió con **5 hallazgos de severidad alta**, todos verificados por el lead
> contra `migrations/0001_initial.sql` y `app/ads/client.py`: **ninguno se
> cayó**. El error de fondo fue del lead — verificó los DATOS (conteos en la
> base viva) pero no el CONTRATO DEL ESQUEMA (constraints, triggers, índices
> únicos, definición de las vistas, semántica de las funciones). El detalle
> está en §Correcciones de la v1.

## Estado verificado que origina este plan (regla 8, 2026-08-30)

| Pieza | Estado medido |
|---|---|
| `sku_costs` en contabilidad (SQLite) | **2,708 filas, 0 nulos, 0 ceros**, 1,087 SKU vigentes, `source` = odoo_sync (2,701) + manual (7) |
| Moneda de esos costos | **100% MXN**, sin excepción |
| `product` / `listing` / `sku_cost` en Orbit | 0 filas (esquema listo, sin ingesta) |
| `ledger_event` / `fx_rate` en Orbit | 0 filas |
| `v_margen_plataforma` | 0 filas, y agrupa **solo por `(platform, amount_currency)`** — sin `listing_id`, sin dimensión temporal (así, a propósito) y sin halo |
| `ad_entity.listing_id` | **0 de 5,899** — el vínculo anuncio→producto no existe |
| `ad_entity_kind` | `campaign, ad_group, keyword, product_target, placement` — **no existe `product_ad`** |
| `LIST_REQUEST_TYPES` del cliente | campaigns, adGroups, keywords, targets, negativeKeywords — **no incluye `/sp/productAds/list`** |
| Ingestas del CLI | solo `structure` y `metrics` |

**Consecuencia**: la pregunta "¿esta keyword vende un producto de 40% o de 8%
de margen?" hoy no tiene forma de responderse. Ese es el trabajo de la Fase 0.

## Decisiones SELLADAS (el header manda sobre las tareas)

1. **Sin costo conocido no hay decisión margin-aware.** Regla 3 de CONTEXTO
   aplicada al margen: dato faltante = fila NO escrita, **jamás un cero**.
   La trampa está documentada y pagada (`sales_history.cogs` de MeLi, 49% sin
   costo, *el cero disfrazado de dato*). Los 2,708 costos de Odoo hoy están
   limpios, pero la ingesta rechaza el cero, no lo normaliza.
2. **Todo costo lleva su moneda, y aquí SIEMPRE hay que convertir.** Los
   costos son 100% MXN; la cuenta US vende en USD. Precedente pagado:
   `sales_history` reportaba MXN hasta para amazon_us → error de 18.66×
   **siempre a favor de "todo es rentabilísimo"**.
3. **`fx_resolve` es intocable y ya resuelve el caso.** Devuelve la tasa
   exacta o la `nearest_prior` dentro de 7 días, con un campo `source` que
   dice cuál de las dos, y **cero filas** si no hay ninguna utilizable —
   nunca una constante. Su comentario documenta el incidente que lo originó:
   un fallback silencioso a 20.5 infló 28,549 MXN. **Ningún consumidor
   inventa su propio FX ni "arregla" esta función**: lee `source` y declara
   cuando la tasa no era del día. Cero filas = dato faltante (sellado 1).
4. **El margen sin cargos miente hacia arriba.** `v_margen_plataforma` resta
   `fee`, `refund` y `withholding`; si el ledger solo trae `kind='sale'`, esos
   cargos quedan en 0 y el margen sale sistemáticamente alto — exactamente el
   sesgo "todo es rentabilísimo" que el sellado 2 persigue. El propio COMMENT
   de la vista avisa que `cargos_sin_orden = 0` puede significar "no llegó",
   no "no hubo". **Ninguna salida de margen se publica sin declarar qué
   clases de cargo entraron y cuáles no.**
5. **El halo se reporta como RANGO, nunca como un número.** Decisión literal
   del dueño (2026-08-28, preflight 1.6c): "acotar con ambos supuestos". El
   56-58.5% del ingreso atribuido es de OTROS SKUs, y la cuenta US da entre
   **+1,671 y −2,238 USD en 91 días** según se cuente — *ni el signo se
   conoce*. El halo NO sale del ledger: sale del lado de ads
   (`ad_revenue` vs `revenue_same_sku` en `ads_metric_observation`).
6. **Vintage explícito o no se consulta.** Tres relojes desalineados: la
   venta atribuida madura a 5-8 días, el costo al día 15 (y madura *hacia
   abajo* por clawback), los fees a 15-30 días. Las tablas point-in-time sin
   filtro de vintage inflan 13-17× e **invierten el signo de las tendencias**.
7. **Una sola fuente de RESOLUCIÓN DE COSTO, no una sola vista.**
   `v_margen_plataforma` es margen por plataforma y no admite `listing_id` ni
   fecha (documentado "sin dimensión temporal A PROPÓSITO"). El margen por
   entidad publicitaria **es una vista nueva y necesaria**; lo que no se
   duplica es la lógica de resolución: costo vigente por `sku_cost` +
   conversión por `fx_resolve`, idéntica a la que ya usa la vista de
   plataforma. Cualquier segunda forma de resolver costo o FX se rechaza.
8. **La Fase 2 nace en shadow** y solo entra si demuestra que aumenta la tasa
   de acción útil (regla de la Fase 4 del diseño v2, aplicada por adelantado).
9. **MeLi queda fuera.** Estructuralmente incomparable con Amazon (cero halo,
   lag de 1 día, ISR con order_id, escritura de ads bloqueada a nivel cuenta).

## DoD que aplica a TODAS las tareas de este plan

Ademas del DoD propio de cada fila, **ninguna tarea esta terminada sin estas
dos**, que van DENTRO del PR de quien la implementa:

1. **El marker de su fila a `cc:完了`**, con el resumen de lo entregado.
2. **Una línea en `docs/CHAT-CONTEXT.md`** contando el cambio en lenguaje de
   negocio (el candado de frescura del CI la exige cuando el PR toca un
   marker).

Se suben del brief al plan el 2026-08-31 porque en las TRES entregas de este
plan —0.1 y 0.2 de GLM, 0.3 de Cursor— los cerro el lead. Tres implementadores
distintos fallando lo mismo no es descuido de ellos: era que vivia en un
mensaje de chat en vez de en el contrato.

## Fase 0 — el dato (NO depende del flip; empieza hoy)

`[lane:gate]` — cada tarea es un candado de datos: si la cobertura no
alcanza, la Fase 1 no arranca.

| Task | Contenido | DoD | Depends | Status |
|---|---|---|---|---|
| 0.1 | **Ingesta de productos y costos** a `product` + `sku_cost`. Fuente: `sku_costs` de la SQLite de contabilidad. Mapeo explícito de nombres: `sku` → `product.odoo_sku`, `cost` → `cost_amount`, `currency` → `cost_currency`. La vigencia bitemporal se PRESERVA. **Semántica de re-corrida contra el esquema real**: `sku_cost` tiene un `EXCLUDE` (btree_gist) que impide dos vigencias solapadas del mismo producto, y el trigger `sku_cost_solo_cierra_vigencia` permite **únicamente** cerrar `valid_to` — no hay UPDATE de importe ni DELETE. Por lo tanto: vigencia nueva ⇒ cierra la anterior con `valid_to` e inserta; vigencia idéntica ya presente ⇒ **no-op**, jamás error ni fila duplicada. `cost` 0 o NULL ⇒ fila no escrita y contada (sellado 1). `includes_tax` se resuelve leyendo qué produce Odoo, no se asume. Subcomando `ingest costs`. **Tres obstáculos medidos por el lead el 2026-08-30 que hay que resolver ANTES de escribir código — ver §Obstáculos de la 0.1**: el contenedor no ve la base de contabilidad; las vigencias de origen son timestamps intradía y el destino es DATE con `CHECK` y `EXCLUDE`; y `product.name` e `includes_tax` no tienen fuente. `[tdd:required]` | Las tres decisiones de §Obstáculos escritas y justificadas ANTES del código. Rojo antes del código. Tests: costo cero rechazado y contado; **colapso de vigencias** (varias filas del mismo SKU el mismo día → UNA vigencia; sin este test la ingesta revienta contra el `EXCLUDE`); vigencia nueva cierra la anterior (una sola vigente por producto); **re-correr la ingesta completa dos veces deja la base idéntica** (no-op real, no "mismo ingest_run"); intento de modificar un importe existente → rechazado por el trigger; SKU sin nombre sigue el camino decidido y queda contado. Corrida real con conteos, cuántas filas se colapsaron y lista de SKU rechazados con motivo | - | cc:完了 [2026-08-30, GLM PR #63 → master `faac179`. **Verificado por el lead contra la base VIVA, no contra la evidencia**: 1,087 productos / 1,955 vigencias / 181 nombres derivados; **una sola vigencia abierta por producto (1,087 de 1,087)**; 100 % MXN e `includes_tax=false` en las 1,955; cero costos ≤ 0; los 974 NULL de `valid_from` del origen cuadran exactos. **Recálculo INDEPENDIENTE del lead con SQL propio sobre el origen: 1,955, idéntico.** Caso punta a punta: `Y4-FB35-N645` con 7 filas del mismo costo y ruido de segundos → UNA vigencia. **No-op confirmado CUATRO veces** (corridas 30-33 con `rows_written=0`), más de lo que declaraba el PR. La remediación de la corrida 27 (1,522 escritas / 506 saltadas por ruido binario del REAL) no dejó daño: **los 8 triggers quedaron activos**, el `EXCLUDE` y los dos CHECK presentes, y de esa carga no sobrevive ni una fila. Dinero correcto: **rechaza ANTES de redondear**, así que una precisión genuina de más de 4 decimales nunca se redondea en silencio; el sub-centavo que cuantiza a cero se rechaza contado en vez de abortar la corrida (hallazgo del propio adversario de GLM). Contabilidad en `mode=ro` por construcción, cero escrituras fuera de alcance. **Residual D3 del IVA CERRADO por el lead con lectura directa de Odoo** (ver D3). GLM cumplió el proceso: rojo antes del código, 17 tests, adversario propio (6 hallazgos) y 1 sola ronda de cross-review con codex] |
| 0.2 | **Ingesta de listings**: el mapa SKU ↔ plataforma ↔ identificador externo, a `listing`. Sin él, el costo (por SKU de Odoo) no se une a lo que Amazon anuncia (por ASIN/seller SKU). **Fuente ya localizada y medida por el lead** — ver Obstaculos de la 0.2: las tablas del bridge, y el puente OBLIGATORIO es amazon_sku_mapping (unir por texto de SKU da 1 % de cobertura y esta PROHIBIDO). **El precio es OPCIONAL**: el CHECK `listing_precio_con_moneda` exige `(listing_price IS NULL) = (price_currency IS NULL)`, o sea ambos o ninguno. El producto de esta tarea es el MAPA; un listing sin precio se escribe igual. `[tdd:required]` | Rojo antes del código. Fuente elegida declarada con su SELECT/readback. Tests: un SKU en dos plataformas → dos filas; listing sin precio se escribe (ambos NULL) y no se descarta; precio presente sin moneda → rechazado por el CHECK. Corrida real: conteo por plataforma y % de SKU con costo que quedan mapeados | 0.1 | cc:完了 [2026-08-30, GLM PR #67 → master `f311057`. **Verificado por el lead contra la base VIVA**: 513 listings (mx 337 / us 176), 265 productos distintos, monedas MXN 337 / USD 176 —exactamente el corte por plataforma, o sea la derivación es correcta—, **0 violaciones del CHECK precio/moneda, 0 ASIN duplicados por plataforma, 0 `external_id` vacíos**, y `ad_entity.listing_id` **intacto en 0** (respetó el límite: eso es la 0.4). **Recálculo INDEPENDIENTE del lead desde el bridge: 513 / 337 / 176 / 265, idéntico.** No-op confirmado **tres veces** (corridas 38-40, `rows_written=0`, en momentos distintos). El bug propio que GLM cazó con la doble corrida —los SELECT previos abrían transacción implícita y `close()` revertía todo— dejó **cero daño verificado**: los `ingest_run` 35 y 36 **no existen** en la base, la reversión fue total. **CORRECCIÓN DEL LEAD**: la entrega definía `MONEDA_POR_PLATAFORMA` en `app/listings.py`, idéntico al `PLATAFORMAS_MONEDA` del motor que ya importan `api.py` y `api_dashboard.py`; no había impedimento de arquitectura (la frontera prohíbe que el MOTOR importe hacia afuera, no al revés). Unificado al importarlo, + candado nuevo `test_una_sola_fuente_de_moneda_por_plataforma` con allowlist de las definiciones DECLARADAS y auto-limpieza de permisos muertos. Al ponerlo aparecieron OTRAS dos preexistentes y **deliberadas**, ambas declaradas con su razón y no tocadas: `app/ads/write.py` ("capa distinta, misma ley", congelada) y `app/ads/structure.py` (`_PAIS_PLATAFORMA_MONEDA`, forma país→(plataforma, moneda) para el discovery de perfiles). **Corrección del lead tras la cross-review de kimi (2026-08-30)**: la primera versión del candado NO veía la de `structure.py` —sólo miraba claves `amazon_*` con valor string— y el cierre afirmó "una tercera" cuando eran CUATRO los lugares que codificaban esa ley; **el comentario original de GLM en `listings.py`, que citaba justo esa constante, tenía razón y el lead se equivocó al descartarlo**. El detector ahora mira el VALOR (que sea o contenga una moneda) y caza las dos formas. **Segunda corrección, cross-review de qwen (2026-08-30 — su primera revisión útil tras arreglar el kit)**: esa primera corrección no solo amplió la detección, también la ACHICÓ sin decirlo — al exigir que TODAS las claves fueran constantes dejaba escapar los mapas de claves mixtas y los que usan `**base`, el punto ciego inverso al que se corregía. Ahora las claves no se filtran y el criterio es solo el valor; el test sella las dos formas vivas, las claves mixtas, el `**` y dos falsos positivos (un dict sin monedas y uno vacío); y el test de poder discriminante dejó de escribir dentro de `app/` del repo —trabaja sobre un árbol temporal— porque un fallo entre escribir y borrar dejaba basura en el árbol fuente. Residual BAJO declarado y no corregido: `PLATAFORMAS_MONEDA` es un `dict` mutable ahora compartido por más importadores (`write.py` congela el suyo con `MappingProxyType`); congelarlo es endurecimiento barato pero toca el motor, y no se toca el motor por una constante a días del cutover. Residual de GLM: el camino "listing sin precio" no se ejercitó en producción (los 133 precios NULL cayeron todos en el conjunto sin mapeo); cubierto por test] |
| 0.3 | **Habilitar la lectura de product ads** — es un cambio de SUPERFICIE DE SEGURIDAD, no una ingesta más: `/sp/productAds/list` **no está** en `LIST_REQUEST_TYPES`, que es un allowlist congelado (`MappingProxyType`) leído en vivo por el guard de POST; hoy `list_objects` rechaza ese path. Ampliarlo sigue el MISMO ritual que pagó `negativeKeywords`: evidencia regla 8 EN VIVO del vendor Content-Type exacto en AMBOS perfiles, con el log en `out/`, ANTES de tocar el allowlist. **EVIDENCIA YA OBTENIDA POR EL LEAD (2026-08-31, `out/regla8-productads.log`)**, porque exige credenciales de Amazon que solo viven en el server: el vendor es **`application/vnd.spproductad.v3+json`** y responde **200 en AMBOS perfiles** (MX 31,063 product ads; US 6,918), contenedor `productAds` con `nextToken`/`totalResults` — la misma paginación de los otros `/list`. Con eso, lo que queda de esta tarea es **código puro y sin credenciales**: ampliar el allowlist con ESE valor y los tests del guard. `[tdd:required]` | Log de la corrida real que prueba el vendor type correcto y el 200 en US y MX (o el fallo declarado). Allowlist ampliado con SOLO ese path. Tests del guard: el path nuevo pasa; un path fuera del allowlist sigue reventando; el conteo de `LIST_REQUEST_TYPES` en los tests se actualiza a propósito, no por accidente | - | cc:完了 [2026-08-31. Evidencia regla 8 del LEAD (`out/regla8-productads.log`, master `6edb2de`): vendor `application/vnd.spproductad.v3+json`, **200 en AMBOS perfiles**. Implementación de **CURSOR** (PR #74 → master `ca9d5c7`), primera entrega suya en este repo: 5 líneas de código y 11 de test, tocando SOLO `client.py` y `test_ads_client.py`. **Verificado por el lead**: el allowlist crece con ESE path y ninguno más; el test fija el vendor exacto y `len(LIST_REQUEST_TYPES) == 6`, que es lo que obliga a actualizar el conteo A PROPÓSITO; **poder discriminante comprobado** quitando la línea del allowlist — falla exactamente ese test con `KeyError`. Cursor razonó (y el lead confirmó) que NO hacía falta duplicar tests del guard: `test_list_objects_post_de_lectura_con_vendor_types` recorre el mapa completo, así que el path nuevo queda ejercitado por el camino real de `list_objects` con sus headers, y `test_bypasses_del_guard_rechazados` ya cubre los paths fuera del allowlist. 67 tests verdes. Los dos DoD que faltaron —marker y línea de CHAT-CONTEXT— los cerró el lead, igual que en la 0.1 y la 0.2 de GLM: es el patrón que se repite en las tres entregas] |
| 0.4 | **Vínculo anuncio→producto al grano de product_ad.** Se materializa `kind='product_ad'` (migración `0004`). `listing_id` vive SOLO en esas filas. Join `(platform, asin del anuncio) → listing.(platform, external_id)`. Se escriben **ENABLED y PAUSED**; **ARCHIVED no se upserta** (la ventana de 90d pierde atribución; 0.7 mide si importa). `ad_group.listing_id` no se toca. Extiende `ingest structure` (misma resolución de padre same-run). El `sku` del payload es solo auditoría, no se persiste. Decisiones D1–D5 abajo. `[tdd:required]` | Decisiones D1–D5 escritas. Rojo antes del código. Candado: `structure.py` no importa `write.py`. Tests: `_CLAVE_CONTENEDORA` tiene `productAds`; ENABLED+PAUSED planificados y ARCHIVED saltado con motivo; product_ad con listing → `listing_id` resuelto; asin sin listing → fila escrita, `listing_id` NULL, contada; `ad_group.listing_id` sigue NULL; 0004 es solo `ADD VALUE 'product_ad'`. Corrida real (lead, tras aplicar 0004 en el server): `ingest structure` + SELECT de cobertura por plataforma (con listing / sin listing / sin costo) y por `status`. ARCHIVED no debe aparecer | 0.2, 0.3 | cc:完了 [2026-08-31. Diseño propuesto por **Cursor** (paró antes de programar, como pedía el brief) y aprobado por el dueño: modelo **(a) grano product_ad**, con dos ajustes del lead —incluir PAUSED (la ventana de margen mira 90 días y un anuncio pausado ayer gastó dentro de ella; ARCHIVED = muerto por el criterio sellado del probe 2.5) y **NO denormalizar** en el ad group (crearía dos lugares para el mismo hecho, la duplicación que este repo acaba de pasar tres rondas arreglando, y compraba 4 casos en MX y 0 en US). Implementación PR #78 → master `d54d202`; migración 0004, deploy y corrida real del lead. **VERIFICADO por el lead ANTES de aprobar**: materializar 12,5k entidades **no contamina el motor** (todas las consultas de decisión filtran por kind, y la evidencia por ad group filtra a keyword+product_target); el sync de estructura es aditivo y no las borra; y la paginación entra —**1,000 por página**, o sea 32 páginas MX y 7 US contra un tope de 100, y si algún día se pasara **revienta ruidoso** en vez de truncar. Una objeción del lead se disolvió sola y por buena razón: temía que la frescura de la estructura se falseara si los product ads llevaban estado, pero Cursor los integró AL SYNC EXISTENTE en vez de hacer una ingesta aparte, así que todo se sella en la misma corrida. Tests discriminantes comprobados: desactivando las DOS guardas de estado falla exactamente un test (la primera mutación del lead fue inválida — solo rompió una de dos guardas redundantes), y el test de integración afirma el `listing_id` exacto Y que el ad group queda NULL. **RESULTADO EN VIVO** (`ingest_run 41`): 12,527 product ads materializados (MX 7,709 / US 4,818), 25,454 ARCHIVED saltados, **8,626 con `listing_id` resuelto (69 %)** y **cero entidades de otro kind con listing_id**. Cobertura de la cadena completa: MX 5,015 de 7,709 y US 3,611 de 4,818 — y **el 100 % de los que resuelven listing llegan a un costo**, o sea la cadena anuncio→producto→costo no pierde nada. El hueco son 3,901 anuncios (31 %) cuyo ASIN no está en `listing`: es el mismo hueco de mapeo de la 0.2, ahora visto a nivel anuncio. **Ponderar por gasto queda para la 0.7** y no es trivial: los product ads NO tienen métricas propias (viven en keyword/product_target), así que el peso lo define la vista de margen de 1.1] |
| 0.5 | **Ingesta de tipos de cambio** a `fx_rate`. Obligatoria (sellado 2). Fuente localizada por el lead: `currency_rates` en la SQLite de contabilidad (210 filas: `rate_date`, `base_currency`, `quote_currency`, `rate`). `fx_resolve` **NO se toca** (sellado 3): esta tarea solo llena la tabla de la que esa función lee. Fuente y cadencia declaradas. `[tdd:required]` | Rojo antes del código. Tests: con la tabla poblada, `fx_resolve` devuelve `exact` el día que existe y `nearest_prior` dentro de los 7 días; **más de 7 días sin tasa → cero filas**, y el consumidor lo trata como dato faltante (sellado 1), no como 1.0 ni como constante. Corrida real: rango de fechas cubierto y lista de huecos > 3 días | - | cc:完了 [2026-08-31, PR #86 → master `2d0f193`; corrida real y cierre del lead. **Verificado por el lead ANTES de aprobar**: poder discriminante comprobado con mutacion propia — el ingestor ingenuo (etiquetas literales de la fuente) revienta EXACTAMENTE los 3 tests de la trampa; la cita del COMMENT de ingest_run (conflictos ON CONFLICT cuentan como skipped) es real. **CORRIDA REAL** (ingest_run 49): 210/210 filas escritas, rango 2025-10-31..2026-08-28, huecos >3d = los tres medidos (5d/5d/4d). **Verificacion independiente del lead con SQL propio sobre la base viva**: las 210 filas son (USD, MXN) con rate 16.898..18.6495, CERO filas absurdas (base=MXN con rate 10-25); fx_resolve en vivo: exact 2026-08-28 = 16.9481 (identico a la fila fuente), nearest_prior cruza el hueco navideno (2025-12-27 → tasa del 12-24), fecha anterior al rango = CERO filas, y hoy resuelve nearest_prior del 08-28. **Re-corrida no-op confirmada** (ingest_run 50: rows_written=0, 210x conflicto PK contado). fx_resolve intacto (sellado 3). Nota de registro: el brief se envio a GLM pero lo implemento CURSOR (confirmado por el dueno el 2026-08-31)] |
| 0.6 | **Ingesta del ledger, y NO solo ventas** (sellado 4). Fuente localizada por el lead: `ledger_events` en la SQLite de contabilidad (13,127 filas, con `platform`, `order_id`, `event_type`, `fee_category`, `sku`, `quantity`, `amount`). además de `kind='sale'`, las clases de cargo que `v_margen_plataforma` resta (`fee`, `refund`, `withholding`). Sin ellas el margen sale sistemáticamente alto. **Semántica append-only contra el esquema real**: `ledger_event` tiene tres índices únicos de deduplicación (`ledger_dedupe_source` por `source_event_id`; `ledger_dedupe_sin_orden` y `ledger_dedupe_con_orden` por clave natural, `NULLS NOT DISTINCT`) — re-ingerir el mismo hecho es **no-op**, no una segunda observación. El ISR **no trae `order_id`** y llega en bultos quincenales: se prorratea explícitamente o se excluye POR ESCRITO, con la decisión documentada. `[tdd:required]` | Rojo antes del código. Tests: re-ingerir el mismo evento no inserta y no revienta (`ON CONFLICT DO NOTHING` verificado, no asumido); cada clase de cargo llega a su índice de dedupe correcto; evento sin `order_id` sigue el camino declarado y queda contado. Corrida real con conteo **por `kind`** y ventana; la evidencia declara explícitamente qué clases de cargo entraron y cuáles no | 0.1 | cc:完了 [2026-08-31. Codigo + corrida viva post-review (`fd53701`): base sigue en **8,041** (sale 1,650 / fee 4,221 / withholding 2,034 / refund 136; mx 4,987 / us 3,054; fee_type=ads 353; withholding sin orden 14). **Ventana real** `rango_min=2025-11-14` .. `rango_max=2026-08-31` (runs 54-55 con codigo endurecido; rows_written=0 = no-op). Clases ENTRARON: sale, fee (incl. ads marcado), refund, withholding. NO entraron: meli (4,998); **106 viola signos** (fee+/refund+/sale<=0, jamas volteados; incluye ~6 ISR fee+). Huecos producto: venta_sin_ASIN=229 / sin_listing=12 / asin_sin_cantidad=0 (sale con product_id 1,409 / sin 241). Live solo pega `ledger_dedupe_source`; indices naturales en CI (`ORBIT_TEST_DSN`). Residual: amazon_us 100% MXN (D8) -> 0.7/lead. Review-fix: product_id solo sale, frontera tipada, DEPLOY DSN@db. **VERIFICADO POR EL LEAD antes del merge (2026-08-31)**: recalculo INDEPENDIENTE desde la fuente con SQL propio — IDENTICO en los 4 kinds (1,650/4,221/2,034/136) y en ads=353; cero cargos positivos, cero meli, cobertura de producto cuadra exacta (1,650−229−12=1,409). Poder discriminante comprobado con DOS mutaciones del lead: retenciones degradadas a fee → 2 tests caen; signo volteado en silencio → 2 tests caen. El implementador ademas CORRIGIO al plan: el obstaculo 5 pedia guardar reversas positivas que `ledger_convencion_signos` prohibe — las salto contadas con residual declarado (direccion conservadora: margen subestimado, jamas inflado). Sello de la run 51 doble-conto 241 filas (version intermedia); las runs 53-56 cuadran exactos los 13,145. **HALLAZGO DE PROCESO (Cursor)**: hot-patch al contenedor de produccion y corrida viva ANTES del review del lead — atenuado porque el brief autorizaba la escritura sin decir cuando, pero produccion solo corre imagenes de master mergeado. El deploy formal recreo el contenedor (drift CERO bajo /app) y la no-op run 56 desde la imagen desplegada reproduce exacto. REGLA NUEVA para briefs de Cursor: prohibido tocar el contenedor; la corrida real es del lead. Implementador: Cursor] |
| 0.7 | **Candado de cobertura**: qué fracción del GASTO PUBLICITARIO real corresponde a anuncios con vínculo resuelto (0.4), costo conocido (0.1) y FX disponible (0.5). Ponderada por gasto, no por conteo de SKU. Umbral mínimo: **lo propone el lead con el número medido a la vista y lo aprueba el dueño** — no se inventa aquí (regla 3). `[tdd:required]` | Cobertura publicada por plataforma con su `SELECT` en la evidencia, desglosando el gasto NO cubierto por motivo (multi-ASIN, sin listing, sin costo, sin FX). Decisión del dueño con su texto literal. **Si no alcanza, la Fase 1 queda `blocked` con el motivo; no se arranca "con lo que hay"** | 0.4, 0.5, 0.6 | cc:完了 [2026-08-31, PR #90+#91 → master 61758d8, corrida real del lead: MX 89.3% / US 100.0% a nivel grupo (90d maduros), estricta ~0% (multi-ASIN dominante, atribución per-producto → 1.1). UMBRAL APROBADO POR EL DUEÑO ≥85% a nivel grupo por plataforma; texto literal: "apruebo y areegla oo del tacos" (2026-08-31). MX PASA, US PASA → Fase 1 desbloqueada. Cross-review r1 codex (3 hallazgos cerrados) + r2 kimi/qwen (7 cerrados); el VERIFICAR de qwen destapó el doble conteo de v_tacos → migración 0005] |

## Obstáculos de la 0.1 (medidos por el lead 2026-08-30, antes de asignarla)

Van aquí y no en un brief de chat **a propósito**: son los hallazgos más
caros de la tarea, y un brief se pierde con la sesión. Quien tome la 0.1
—hoy o dentro de tres meses— choca con los tres.

**1 · El contenedor NO puede leer la base de contabilidad.**
`docker exec orbit-app-1 ls /mnt/data/appdata/accounting/data/accounting.db`
responde `No such file or directory`: el servicio `app` monta SOLO
`secrets/`. Decisión previa a implementar, con su razón. Opciones: (a)
bind-mount **read-only** del archivo en el compose; (b) correr la ingesta en
el host contra el DSN de Orbit; (c) exportar de contabilidad y meter el
archivo al contenedor. La (a) es la más limpia pero **cambia el contrato de
deploy** (`docs/DEPLOY.md`: "Qué se monta: SOLO `secrets/`") — si se elige,
se propone al lead antes de tocar el compose. En cualquier caso el acceso a
contabilidad es **read-only**.

**2 · Las vigencias de origen son timestamps que cambian en SEGUNDOS; el
destino es DATE.** Muestra real de `sku_costs`:
`2026-02-07 12:25:00 → 12:47:06 → 12:47:07 → 12:47:08`. Orbit guarda
`valid_from`/`valid_to` como **DATE**, con `CHECK (valid_to > valid_from)` y
un `EXCLUDE` que prohíbe rangos solapados. Medido sobre las 2,708 filas:

| Medición | Valor |
|---|---|
| Filas totales | 2,708 |
| Pares `(sku, día)` distintos | 1,955 |
| **Filas que abren y cierran el MISMO día** | **753** |
| **Pares `(sku, día)` con más de una vigencia** | **604** |

Portar fila-por-fila **REVIENTA**: esas 753 dan `valid_to = valid_from`
(viola `sku_cost_rango`) y esos 604 grupos dan rangos solapados (viola el
`EXCLUDE`). Es un tercio de la tabla, no un caso borde. Esa rotación
intradía es **ruido del sync de Odoo**, no historia económica: para el
margen, una venta del día D usó UN costo. **Propuesta del lead** (se puede
discutir, con justificación): colapsar a una vigencia por `(sku, día)` con
el último valor del día, y fusionar días consecutivos de igual costo en una
sola vigencia. El colapso se declara en la evidencia con cuántas filas se
fusionaron y bajo qué regla.

**3 · `product.name` e `includes_tax` no tienen fuente en `sku_costs`.**
`name` es `NOT NULL`; el único candidato es `bom_headers`
(`product_sku`, `product_name`, 906 filas). Medido: de los **1,087** SKU con
costo vigente, **906 tienen nombre y 181 NO**. Descartar esos 181 cuesta
~17% de la cobertura que la 0.7 va a medir. Propuesta del lead: crear el
producto igual, con nombre derivado del SKU y marca de "nombre no
disponible" — jamás inventar un nombre descriptivo (regla 3).
`includes_tax` es `NOT NULL` y **sin default a propósito**, y tampoco tiene
columna equivalente en el origen: se averigua qué produce Odoo
(`sku_costs.source`, `bom_lines`, `sync_cogs_odoo.py`) y **se declara con
evidencia**; si no se puede determinar, se para y se pregunta. Un costo con
o sin impuesto cambia el margen: es justo el tipo de error que este proyecto
persigue.

**Fuentes localizadas de paso** (no se tocan en la 0.1): `currency_rates`
(210 filas) es la fuente de la 0.5, y `ledger_events` (13,127 filas) la de la
0.6 — ambas en la misma SQLite de contabilidad.

### Decisiones de la 0.1 (GLM, 2026-08-29 — escritas ANTES del código, como exige el DoD)

Cada decisión cita su medición en vivo (SELECT sobre la SQLite de contabilidad,
2026-08-29, `mode=ro`). Las mediciones de cabecera del plan (2,708 filas, 753
mismo día, 604 grupos) se re-verificaron y cuadran exacto.

**D1 · Acceso a la fuente: snapshot con la API `.backup()` de SQLite + `docker cp`
(opción c).** El pipeline lee un SNAPSHOT de la base, no el archivo vivo. El
runbook (nuevo en `docs/DEPLOY.md`): en el host, `python3 -c` con la API
`.backup()` (stdlib) produce `/tmp/accounting-snapshot.db`; `docker cp` lo mete
al contenedor; `python -m app.cli ingest costs --sqlite /tmp/accounting-snapshot.db`
hace la ingesta. Razones: la base está en modo **WAL** (`PRAGMA journal_mode` =
wal, medido) — un bind-mount read-only de una WAL es frágil (el lector puede
necesitar escribir el `-shm`) y un `cp` directo deja fuera el WAL (lección ya
pagada, ver "Estado del servidor viejo" en CONTEXTO). La API `.backup()` da un
snapshot consistente con WAL sin tocar nada de contabilidad. **No cambia el
contrato de deploy** ("se monta SOLO `secrets/`"): la opción (a) queda ANOTADA
como propuesta futura para el lead si la cadencia pasa a diaria (exigiría
resolver el WAL del mount y tocar el compose); la (b) exige un runtime Python de
Orbit fuera del contenedor (venv + psycopg en el host) y rompe el camino único
del contenedor. El acceso queda read-only por construcción: el pipeline abre el
snapshot con `mode=ro` y solo hace SELECT. Cadencia: **manual** por ahora (la
0.7 necesita una base estable antes que frescura); el cron se agrega con este
mismo runbook cuando la Fase 1 lo pida.

**D2 · Colapso a UNA vigencia por `(sku, día)` con el ÚLTIMO valor del día, y
fusión de días consecutivos de igual costo.** Se adopta la propuesta del lead,
con dos reglas de borde medidas contra el origen:

- `valid_from` NULL (974 filas, todas del backfill del 2026-02-20): el día de
  inicio es `date(created_at)` — el día en que Odoo REPORTÓ ese costo. Contabilidad
  lo trata como "desde siempre" (`get_cost_asof` de su sync); Orbit NO porta esa
  afirmación (regla 3: la fila no puede probar cobertura anterior a su creación).
  Medido: **0 SKUs** donde una fila fechada sea anterior al `created_at` de su fila
  NULL → ordenar por `COALESCE(valid_from, created_at)` nunca invierte la historia.
- La cadena origen es perfecta: **0 huecos** (`valid_to` de una fila =
  `valid_from` de la siguiente, verificado con window function) y exactamente
  **una vigencia abierta por SKU** (1,087 filas `valid_to IS NULL` = 1,087 SKUs).

El colapso: el costo del día D es el de la última fila que empieza ≤ D; días
consecutivos de igual costo se funden. Impacto medido: los **604** grupos
`(sku, día)` multi-fila tienen TODOS más de un costo distinto (603 con
diferencia > 0.01 MXN): no es solo rotación idéntica — la regla "último valor
del día" decide qué costo manda ese día. Semántica de re-corrida contra el
esquema real: vigencia idéntica ya presente ⇒ **no-op**; vigencia nueva ⇒ cierra
la abierta (`UPDATE valid_to`, la única transición que el trigger
`sku_cost_solo_cierra_vigencia` permite) e inserta la nueva; costo DISTINTO para
un período ya publicado ⇒ **skip contado** (el importe es inmutable; jamás se
intenta el UPDATE que el trigger rechazaría).

**D3 · `includes_tax = false` (costo NETO de IVA), declarado con la cadena de
evidencia completa; `product.name` derivado del SKU para los sin nombre.**
Qué produce Odoo (evidencia):

- `sku_costs.cost` = `product.product.standard_price` **verbatim**
  (`scripts/sync_cogs_odoo.py` de accounting: `search_read` de `standard_price`,
  sin ajuste de impuesto alguno; 2,701/2,708 filas; las 7 manuales son el mismo
  SKU, mismo costo, mismo día — POST repetido del dashboard).
- El mantenimiento de costos en Odoo es: reglas planas del operador
  (`goncloud-bridge-in-out/tools/odoo_cost_update_arras.py`: "16mm = $95 MXN") y
  rollup de BoM (`odoo_cost_recompute_kits.py` usa el `compute_price` NATIVO de
  mrp, que SUMA costos de componentes — Odoo no aplica impuesto al costo: los
  impuestos viven en el lado de venta).
- `standard_price` es el costo de **valoración** de inventario (el propio audit
  de bridge lo etiqueta "impactan valorizacion"); en la contabilidad mexicana el
  IVA acreditable de proveedor jamás se capitaliza al inventario.
- Ninguna herramienta del ecosistema aplica o quita factor de IVA a un costo
  (verificado en accounting + bridge).

**RESIDUAL CERRADO por el lead el 2026-08-30, con lectura directa de Odoo**
(autorizada por el dueño en el momento: "por que no entras a mi odoo y revisas
personalmente?"). El dueño planteó la duda concreta: sus proveedores chinos no
cobran IVA y los mexicanos sí, y suponía que al hacer un RFQ y una orden Odoo
guardaría el costo con el precio completo. **Medido en la base `EHV` (Odoo 17),
y la suposición NO se cumple**:

- En **227 de 227** líneas de orden de compra, `price_subtotal =
  price_unit × product_qty`: el precio unitario es SIEMPRE neto.
- **213 líneas llevan impuesto ENCIMA** (`price_total > price_subtotal`) y **14
  no** — la mezcla de proveedores que describe el dueño EXISTE y se ve, pero
  viaja fuera del precio unitario. Ratio promedio `price_total/price_subtotal`
  = **1.1501**, exactamente `1 + 0.16 × 213/227`: cuadra la aritmética del 16 %
  aplicado a esas 213.
- De los 24 productos con `standard_price` y orden de compra comparables: **3
  coinciden exactos con el precio SIN impuesto y CERO con el precio CON
  impuesto**. Ejemplos: `4558-BR` costo 173.00 = unitario 173.00 (subtotal
  865.00, total 1,003.40); `4405-BG` 76.00 = 76.00 (1,140.00 / 1,322.40);
  `4609` 240.00 = 240.00 (720.00 / 835.20).

**Conclusión: `includes_tax = false` es CORRECTO y queda VERIFICADO contra la
fuente, no argumentado.** El riesgo que preocupaba —que los productos de
proveedor mexicano se vieran 16 % más caros que los importados y el motor
moviera presupuesto por una razón falsa— **no existe**: no era un sesgo parejo
sino diferencial entre productos, que es peor, y por eso se verificó en vez de
aceptarse. Dirección del residual:
declarar `false` cuando el valor cargado era bruto **subestima** margen
(conservador); declararlo `true` sin prueba lo **sobreestimaría** — el sesgo
"todo es rentabilísimo" que este plan persigue (sellado 2). `product.name`: 906
de 1,087 SKUs vigentes tienen nombre en `bom_headers`; los **181** sin nombre se
crean con `[sin nombre en Odoo] {sku}` y quedan contados en la corrida (regla 3:
jamás un nombre descriptivo inventado). `active` no se toca en la 0.1: el origen
solo trae productos activos con costo > 0, así que "ausente del origen" no
demuestra inactivo.

### Corrida real de la 0.1 (2026-08-29, con el pipeline del PR)

Snapshot `.backup()` → `ingest costs` con rol `orbit_ingest`. **Hallazgo
propio en la primera corrida (id 27)**: 506 skips por "costo con mas de 4
decimales" — las 506 filas que SQLite marcaba con `ROUND(cost,4) != cost`
traen **ruido binario del REAL** (residuo ≤ 1e-13, medido; ej. 554.1800000000001):
dinero de 2 decimales de Odoo, no precisión real. Regla corregida en el mismo
PR: ruido < 1e-5 se cuantiza a 4 decimales; precisión genuina ≥ 1e-5 se sigue
rechazando (test rojo→verde). La carga parcial se remedió como el propio
trigger prescribe ("cerrar en la fecha equivocada se corrige con una migración,
no con un UPDATE"): `DELETE` acotado a `ingest_run_id = 27` con el trigger
deshabilitado DENTRO de una transacción y reactivado antes del COMMIT; el
registro de la corrida 27 queda como historia honesta.

**Corrida final (id 28)**: 2,708 filas origen → **1,955 vigencias, 0 rechazos,
1,087 productos (181 con nombre derivado), 753 segmentos intradía colapsados,
0 fusiones** — sin fusiones es correcto: `sync_cogs_odoo.py` solo rota cuando el
costo cambia > 0.01, así que no existen días consecutivos de igual costo que
fundir. **Corrida de control (id 30, mismo snapshot): `rows_written=0`, no-op
real** (nueva ingest_run sellada, base idéntica). Verificación en la base viva:
una vigencia abierta por producto (1,087/1,087), 0 solapamientos, 100% MXN,
`includes_tax=false` en todas, y los SKUs de muestra cayeron exacto (Y4-FB35:
7 filas intradía → UNA vigencia `[2026-02-07, ∞)`; NH-CAR: el `valid_from`
NULL arranca en su `created_at` 2026-02-20 y los 3 cambios del 2026-08-18
colapsan al último valor 304.65). Lista de SKU rechazados en la corrida final:
**vacía**.

### Endurecimiento post-adversario (2026-08-30, mismo PR)

Ronda de adversario sobre el diff (artifact `.saikit/findings/`): 6 hallazgos
(0 altos, 3 medios, 3 bajos), todos contra datos legales-futuros del origen —
las invariantes medidas hoy (cadena perfecta, 100% MXN, sin sub-centavos) no
son garantías del esquema. Corregidos en el mismo PR, cada uno con su test:

1. **Costo sub-centavo** (0 < costo < 5e-5) cuantiza a 0.0000 y revienta
   `sku_cost_positivo` abortando la corrida entera → ahora es skip contado
   ("costo cero o nulo", sellado 1).
2. **Solape en el origen** publicaba la vigencia VIEJA como abierta (costo
   vigente divergente de la fuente) → ahora el SKU completo queda sin escribir
   y cada unidad no publicada cuenta su skip.
3. **Intradía en el borde de la serie**: declarado en D2 — un tramo que abre y
   cierra el mismo día SIN fila que continúe el día no puede reclamarlo bajo
   granularidad DATE: el día queda sin costo (dato faltante) y se cuenta
   aparte (`segmentos_intradia_en_borde`), distinto del ruido con sucesor.
4. **Moneda/`includes_tax` distintos en lo publicado** con igual importe ya no
   son no-op: divergencia y SKU completo sin escribir (regla 4).
5. **SKU ausente del origen** queda contado (antes: silencio con vigencia
   abierta huérfana). NO se cierra su vigencia (sería inventar que dejó de
   aplicar) ni se desactiva el producto.
6. **`UPDATE product.name`** del upsert (cuando el nombre mejora): queda
   DECLARADO — está dentro de la autorización del dueño para esta tarea
   ("escritura en `product`"), el catálogo es la excepción mutable por diseño
   y el GRANT de la migración lo permite; documentado aquí para que el lead
   lo vea en el diff, no en el silencio.


### Residuales que el lead deja declarados tras cerrar la 0.1 (2026-08-30)

Ninguno bloquea la 0.2; los dos deben resolverse ANTES de la Fase 1.

1. **La fusion de dias consecutivos de igual costo NUNCA se ejercito contra
   datos reales.** El lead midio el origen con SQL propio: hay **cero** pares
   de dias consecutivos con el mismo costo en las 2,708 filas, asi que esa
   rama del colapso no se ejecuto ni una vez en la corrida real. Esta cubierta
   por test unitario, pero sin evidencia de produccion: si estuviera mal,
   nada de lo corrido lo habria revelado. Cuando aparezca el primer caso real,
   verificarlo explicitamente.
2. ~~La ingesta es MANUAL: no hay cron.~~ **CERRADO el 2026-08-30**: el
   dueno decidio cadencia **DIARIA** y ya esta agendada (07:30 UTC en el
   crontab de gon, script refresh_costos.sh, runbook en docs/DEPLOY.md).
   Razon medida: los costos rotan poco (15 dias con cambios en 6.5 meses)
   pero el 2026-08-18 cambiaron 937 SKUs de golpe — con cadencia semanal un
   evento asi deja cada numero de margen mal hasta 6 dias; y correr la
   ingesta sin cambios es no-op (corridas 30-34 con rows_written=0), asi que
   el costo de la frecuencia es despreciable.

   **Hallazgo del lead al agendarla**: probar el script ANTES de poner el
   cron atrapó que el contenedor no tenía el subcomando `costs` — el deploy
   de las 02:12 UTC era anterior al merge de la 0.1, y un `--build` sin
   copiar el código reconstruye el VIEJO **en silencio** (`COPY app` sale
   `CACHED` y el contenedor dice `Running`, no `Recreated`). Corregido con un
   deploy real (imagen nueva, 29 modulos .py verificados identicos a
   origin/master por md5) y **documentado en docs/DEPLOY.md** para que no
   vuelva a pasar. Un cron que falla todos los dias es peor que ningun cron:
   por eso se probo primero.

## Obstáculos de la 0.2 (medidos por el lead 2026-08-30, antes de asignarla)

Misma disciplina que la 0.1: los hallazgos caros van al plan, no a un brief
de chat.

**1 · La fuente existe, y son DOS tablas de un TERCER sistema (el bridge).**
`amazon_listing_prices` (809 filas, 735 `seller_sku` distintos, **todas con
ASIN**; trae `seller_sku`, `asin`, `listing_id`, `marketplace_name`, `price`)
y `amazon_sku_mapping` (450 filas: `seller_sku`, `odoo_product_id`,
`odoo_default_code`, `asin`, `parent_asin`). Viven en la SQLite del bridge,
**no** en contabilidad: es una tercera fuente y **el contenedor tampoco la
ve** — aplica el mismo runbook de snapshot que resolvió la 0.1.

**2 · LA TRAMPA: los SKU de Amazon NO son los de Odoo, y unirlos por texto
falla.** Medido: de los 735 `seller_sku` de Amazon **solo 7 coinciden** con un
SKU que tenga costo. La razón: la mayoría de los listings se crearon con el
SKU **autogenerado por Amazon** (`01-5LZU-V9KZ`, `04-WUOG-3RXZ`) mientras
Odoo usa códigos de negocio (`ARR-16-DOR-CAM`, `NH-CAR-AZU-CEN-DOR`). No hay
transformación entre ambos: son identificadores distintos. **El puente
OBLIGATORIO es `amazon_sku_mapping`; unir por texto de SKU está PROHIBIDO**
—daría 1 % de cobertura y parecería "funcionar".

**3 · Cobertura medida de la cadena completa** (listing → mapeo → SKU de Odoo
→ costo vigente):

| Paso | Valor |
|---|---|
| `seller_sku` distintos en listings | 735 |
| con fila en `amazon_sku_mapping` | **450** |
| de esos, con `odoo_default_code` no vacío | 450 (100 %) |
| **de esos, que llegan a un costo** | **450 (100 %)** |
| sin fila de mapeo (el hueco) | **285** |

O sea: **todo lo mapeado llega a costo**; el único hueco son las 285
publicaciones sin mapeo. Ese hueco es el insumo de la 0.7 y hay que medirlo
**ponderado por gasto publicitario**, no por conteo: 285 productos sin
anuncios no valen lo que 1 que se lleve el 30 % del presupuesto.

**3.bis · El product ad trae ASIN y SKU JUNTOS** (medido por el lead en la probe de la 0.3, 2026-08-31): las claves de cada item son `adGroupId`, `adId`, `asin`, `campaignId`, `sku`, `state`. Eso simplifica la 0.4 más de lo previsto: el vínculo ad group → ASIN es **directo**, sin pasar por el seller_sku, y además el `sku` del propio anuncio sirve para **cruzar contra `amazon_sku_mapping`** y detectar mapeos podridos. Ojo con el volumen: 31,063 product ads en MX y 6,918 en US **incluyen todos los estados**, así que la 0.4 debe filtrar por `state` y declarar con qué criterio.

**3.ter · LA CARDINALIDAD NO ES LA EXCEPCIÓN, ES LA REGLA — y tumba la
política que este plan proponía.** Medido por el lead el 2026-08-31 apenas
quedó vivo el permiso de la 0.3 (`out/medicion-productads-cardinalidad.log`,
lectura paginada completa):

| Mercado | ad groups con anuncios ENABLED | con **UN** ASIN | con **VARIOS** | peor caso |
|---|---|---|---|---|
| amazon_mx | 32 | **4** | 28 | 1,259 ASINs |
| amazon_us | 48 | **0** | 48 | 212 ASINs |

La propuesta anterior —"si el ad group anuncia N ASIN, NO escribir
`listing_id` y contarlo como *multi-ASIN, margen no atribuible*"— **dejaría a
la cuenta US ENTERA sin margen**, porque allí **ningún** ad group anuncia un
solo producto. No es un caso borde: es el caso normal. Queda **DESCARTADA**.

Consecuencia de diseño, y es lo primero que la 0.4 tiene que resolver:
`ad_entity.listing_id` —UN listing por entidad— **no alcanza como modelo de
atribución de margen**. Las salidas que ve el lead, a decidir con el dueño
porque cambian qué significa el número:
 (a) atribuir a grano de **product ad** (materializarlo como entidad, lo que
 arrastra la migración del enum ya pre-aprobada);
 (b) margen del ad group como **mezcla ponderada** de sus productos — y hay
 que decir ponderada por QUÉ, porque no tenemos gasto por ASIN dentro del ad
 group;
 (c) declarar el margen **no atribuible a grano de ad group** y subirlo a
 campaña, aceptando que la Fase 2 decida con menos resolución.
Ninguna es gratis, y la elección determina si margin-aware puede existir.

**Estados: filtrar es obligatorio y pesa.** MX trae 23,354 ARCHIVED de 31,063
(75 %) y US 2,100 de 6,918. Sin filtro, el mapa se llena de anuncios muertos.
Cero product ads sin `asin` en ambos mercados: ese campo siempre viene.

**Y falta una pieza que la 0.3 no cubrió** (no era su alcance): el helper de
paginación `listar_todo` resuelve el contenedor por `_CLAVE_CONTENEDORA` de
`app/ads/structure.py`, que **no tiene `productAds`**. Con el allowlist solo,
`list_objects` funciona pero `listar_todo` no: la 0.4 debe agregar esa clave.

**4 · El ASIN es la llave correcta, y es NO AMBIGUA.** Medido: **cero** ASIN
apuntan a más de un SKU de Odoo, así que un anuncio que apunta a un ASIN
tiene EXACTAMENTE un costo. (115 SKU de Odoo tienen más de un ASIN —el mismo
producto en MX y US, o variantes—, lo cual es esperable y no crea ambigüedad
en la dirección que importa.) La 0.4 traerá ASIN desde los product ads: **la
unión del margen va por ASIN, no por `seller_sku`.**

**5 · Los mercados ya vienen con el nombre de Orbit.** `marketplace_name`
trae literalmente `amazon_mx` (548 listings) y `amazon_us` (261): no hace
falta traducir a `platform`. Verificarlo igual antes de confiar (regla 8).

### Decisiones de la 0.2 (GLM, 2026-08-30 — escritas ANTES del código, como exige el DoD)

Mediciones propias sobre snapshot `.backup()` del bridge (regla 8, `mode=ro`):
las del lead cuadran exacto (809 filas, 735 `seller_sku`, 450 con mapeo,
todas con ASIN) y añaden: **0 duplicados** de `(marketplace, asin)` y de
`(marketplace, seller_sku)`; **0 ASIN vacíos**; precio **133 NULL / 0 con
valor ≤ 0 / 0 con ruido binario**; rangos MX 99.00–4,076.61 y US 18.99–188.00
(magnitudes de moneda local); `status` = Active 416 / Inactive 260 /
Incomplete 133 (los 133 NULL de precio son los Incomplete); el mapeo cubre
**265 SKUs de Odoo distintos**; y `marketplace_name` verificado literal:
`amazon_mx` (A1AM78C64UM0Y8, 548) y `amazon_us` (ATVPDKIKX0DER, 261).

**D1 · Acceso: mismo runbook de snapshot de la 0.1, aplicado al bridge.** El
contenedor tampoco ve `/mnt/data/appdata/bridge/data/bridge.db` (contrato:
SOLO `secrets/`). `ingest listings --sqlite RUTA` recibe un snapshot del
bridge producido con la API `.backup()`; lectura `mode=ro`, solo SELECT, cero
escrituras en el bridge.

**D2 · Las llaves: la unión es SOLO por `seller_sku` contra el mapeo, y el
`external_id` es el ASIN DEL LISTING.** Unir por texto contra Odoo queda
PROHIBIDO (obstáculo 2). Detalles medidos:
- La unión listing→`amazon_sku_mapping` va por `seller_sku` (la PK del mapeo):
  cubre **450**; unir por ASIN cubriría 438 (12 filas del mapeo sin ASIN) —
  pierde. El mapeo ES el puente, su PK ES la llave.
- `external_id = amazon_listing_prices.asin` SIEMPRE (0 nulos, 0 duplicados
  `(marketplace, asin)`, 0 ASIN que apunten a 2 SKUs de Odoo). La UNICA
  divergencia listing.asin vs mapeo.asin (XM-20QN-2YJR: B0D9F4V1CC vs
  B0851SSBL6) resuelve a favor del ASIN DEL LISTING: es la identidad de la
  publicación en el namespace de Amazon, el mismo que traerán los product ads
  de la 0.4; se cuenta como informativo, no se corrige nada.
- `platform = marketplace_name` verificado literal; fuera de
  `{amazon_mx, amazon_us}` ⇒ fila rechazada y contada (defensa).
- 74 ASIN viven en ambos mercados ⇒ DOS filas `(platform, external_id)`
  distintas para el mismo producto: exactamente el caso "un SKU en dos
  plataformas → dos filas" del DoD.

**D3 · Precio y estado del listing.** El reporte origen
(`GET_MERCHANT_LISTINGS_ALL_DATA`) se pide UNO POR marketplace y su columna
`price` viene en la moneda local del mercado por construcción (el sync del
bridge no la guarda; los rangos medidos lo confirman). La moneda se DERIVA de
la plataforma (amazon_mx→MXN, amazon_us→USD — el mismo mapa
`_PAIS_PLATAFORMA_MONEDA` que ya vive en `app/ads/structure.py`). Precio NULL
(133) ⇒ listing con precio y moneda ambos NULL (el CHECK lo exige y "un
listing sin precio se escribe igual"). Precio ≤ 0 ⇒ tratado como NULL y
contado (regla 3; hoy 0 casos). Dinero: `Decimal(str(x))` con la misma regla
de ruido/precisión de la 0.1. `status` NO filtra: el listing de Orbit es el
MAPA de identidad, no el ciclo de vida — los anuncios pueden referenciar
publicaciones Inactive; se escribe todo y el estado queda en el origen.
**Sin mapeo (285): fila NO escrita y contada** — jamás se crea un producto
inventado para un `seller_sku` sin Odoo (regla 3). El hueco alimenta la 0.7.

**D4 · Re-corrida: upsert sobre `(platform, external_id)`.** Nueva ⇒ INSERT;
existente ⇒ UPDATE de `product_id`/`seller_sku`/precio cuando difieran (el
catálogo es la excepción mutable por diseño y el GRANT del esquema lo permite);
sin cambios ⇒ no-op REAL (re-correr dos veces deja la base idéntica). El cambio
de `product_id` de un listing ya publicado es la corrección legítima de un
re-mapeo del bridge y se cuenta aparte ("remapeos").

### Corrida real de la 0.2 (2026-08-30, con el pipeline del PR)

**Bug propio cazado por la prueba de doble corrida**: las corridas 35/36
contra la base viva imprimieron 513 escritas y dejaron la base VACÍA — con la
conexión del CLI (sin autocommit, a diferencia de los tests), los SELECT de
estado previos al primer bloque transaccional abrían una transacción implícita
y `conn.close()` la revertía ENTERA. Cero daño (rollback total, ni las
ingest_run sobrevivieron), corregido moviendo los SELECT DENTRO de la
transacción de trabajo, con test de regresión rojo→verde usando una conexión
sin autocommit exactamente como la del CLI.

**Corrida final (id 37)**: 809 filas origen → **513 listings escritos, 296
filas sin mapeo contadas** (los 285 `seller_sku` distintos del lead + 11 que
viven en ambos mercados: mismo hueco, unidades distintas), **0 conflictos de
ASIN, 0 remapeos**. Por plataforma: **amazon_mx 337 / amazon_us 176**, moneda
derivada MXN/USD al 100%. **Corrida de control (id 38, mismo snapshot):
`rows_written=0`, no-op real.** Verificación: CHECK precio-con-moneda 0
violaciones, **265 productos distintos mapeados**, y la cobertura del DoD:
**24.4% de los SKUs con costo quedan mapeados** (265/1,087) — el hueco (285
publicaciones sin mapeo) es el insumo de la 0.7 ponderado por gasto.
Residual declarado: el camino "listing sin precio" (133 filas Incomplete del
origen) **no se ejercitó en producción** — los 133 NULL de precio cayeron
TODOS en el conjunto sin mapeo; cubierto por test (fixture), sin evidencia de
producción (mismo estatus que la fusión de la 0.1).

### Endurecimiento post-adversario de la 0.2 (2026-08-30, mismo PR)

Ronda de adversario (artifact `.saikit/findings/orbit-06-0-2-adversario.json`):
7 hallazgos (0 altos, 3 medios, 4 bajos), todos contra datos legales-futuros
del bridge. Corregidos con test rojo→verde:

1. **Precio sub-centavo y fuera de rango** (medio): las dos guardas del
   endurecimiento de la 0.1 no estaban portadas — un precio en (0, 1e-5)
   cuantiza a 0.0000 y violaba `listing_precio_positivo` ABORTANDO la corrida
   entera; uno de 11+ enteros desbordaba NUMERIC(14,4). Ambos son dato
   faltante contado, no abort.
2. **Strip asimétrico del mapeo** (medio): la llave del mapeo quedaba cruda
   mientras el listing se stripeaba — pérdida con motivo falso y, con claves
   gemelas por espacio, producto EQUIVOCADO en silencio. Ahora ambas partes
   se normalizan y una colisión de claves tras el strip deja el SKU sin
   escribir, contado como "mapeo ambiguo tras normalizar".
3. **Precios divergentes del mismo ASIN** (medio): dos filas del mismo
   (plataforma, ASIN) y producto con precios distintos elegían la primera
   alfabética en silencio. Ahora el precio divergente se DESCARTA (dato
   faltante, regla 3) y queda contado; igual precio colapsa con su stat
   (`filas_mismo_asin`, ahora visible en stdout).
4. Bajos corregidos: el contador "ausente en el origen" solo cuenta filas
   ausentes del ARCHIVO (una fila presente-pero-sin-mapeo ya cuenta arriba,
   sin etiqueta falsa); re-mapeo con precio cuenta en ambos contadores (sin
   `elif`); test de dispatch del CLI `ingest listings`. Bajo declarado, no
   corregido: los contadores son pre-computados — bajo una corrida
   concurrente el reporte podría mentir aunque la base quede correcta (el
   WHERE del upsert es la defensa); cadencia manual, escritor único.

**Incidente de integración resuelto en el mismo PR**: el lead pusheó
`d5f358d` (cron diario de costos + lecciones de deploy) mientras la rama se
cortaba — la edición de DEPLOY.md de esta tarea reemplazaba el mismo bloque.
Detectado por el adversario, resuelto con rebase sobre `origin/master`
verificando que el diff quedó **puramente aditivo**. Lección para 0.5/0.6:
`git fetch` justo antes de editar docs compartidos y verificar el diff contra
master ANTES de pushear.

### Decisiones de la 0.4 (aprobadas por el lead, 2026-08-31)

La cardinalidad tumba el modelo de ad group (obstáculo 3.ter). Estas cinco
quedan selladas; la implementación las sigue.

**D1 · Modelo (a): materializar `kind='product_ad'`.** Un listing por entidad
es 1:1 con el anuncio, no con el ad group. En US 0/48 ad groups tienen un
solo ASIN; atribuir al grupo dejaría la cuenta entera sin margen. La
migración `0004` agrega el valor al enum y commitea sola (PostgreSQL no
deja usar el valor nuevo en la misma transacción).

**D2 · Estados vivos: ENABLED y PAUSED.** Criterio sellado desde el probe
2.5: archivado = muerto, pausado = vivo pero apagado. La vista de margen
mira 90 días hacia atrás; un anuncio pausado ayer gastó y vendió dentro
de esa ventana. Excluirlo pierde atribución real. Volumen: 1,707 PAUSED
en MX (28 % de los no archivados) y 481 en US — no es residual.

**D3 · Sin desnormalizar a `ad_group.listing_id`.** Ese campo queda NULL
siempre en este camino. N ASINs en un grupo no se aplastan a uno.

**D4 · ARCHIVED no se upserta.** MX trae 23,354 archivados de 31,063 (75 %).
Materializarlos llenaría el mapa de muertos. Consecuencia declarada: los
archivados dentro de la ventana de 90d pierden atribución. La 0.7 mide si
el gasto de esos anuncios importa.

**D5 · `sku` del payload es solo auditoría.** La unión del margen va por
ASIN (`listing.external_id`). El seller SKU no se guarda en `ad_entity`.

**SELECT de cobertura para el lead** (tras aplicar 0004 y correr
`ingest structure` contra la base viva; no se corre en este agente):

```sql
SELECT e.platform::text,
       count(*) AS product_ads,
       count(*) FILTER (WHERE e.listing_id IS NOT NULL) AS con_listing,
       count(*) FILTER (WHERE e.listing_id IS NULL) AS sin_listing,
       count(*) FILTER (
         WHERE e.listing_id IS NOT NULL
           AND NOT EXISTS (
             SELECT 1 FROM listing l
             JOIN sku_cost c ON c.product_id = l.product_id
             WHERE l.id = e.listing_id
           )
       ) AS sin_costo
  FROM ad_entity e
 WHERE e.kind = 'product_ad'
 GROUP BY e.platform
 ORDER BY e.platform;

SELECT s.status, count(*)
  FROM ad_entity e
  JOIN ad_entity_state s ON s.ad_entity_id = e.id
 WHERE e.kind = 'product_ad'
 GROUP BY s.status
 ORDER BY s.status;

-- Debe seguir en 0: este camino no escribe ad_group.listing_id
SELECT count(*) FILTER (WHERE listing_id IS NOT NULL)
  FROM ad_entity WHERE kind = 'ad_group';
```

## Obstáculos de la 0.5 (medidos por el lead 2026-08-31, antes de asignarla)

Misma disciplina: los hallazgos caros van al plan, no a un brief de chat.
Todo medido en vivo contra `currency_rates` de la SQLite de contabilidad
(`mode=ro`) y contra el consumidor real de `fx_rate`.

1. **LA trampa: las etiquetas de la fuente están INVERTIDAS respecto a su
   valor.** La fuente trae UN solo par: `base_currency='MXN',
   quote_currency='USD', rate≈16.9–18.6` (210 filas, 2025-10-31..2026-08-28).
   Ese valor es **pesos por dólar** — leído literal diría "1 MXN = 17 USD",
   absurdo. Y el consumidor real llama `fx_resolve(fecha, 'USD', 'MXN')` y
   **multiplica** `monto_USD × rate = monto_MXN` (ver `v_tacos_mensual` en
   `migrations/0001_initial.sql`). El mapeo correcto es: fila fuente
   `(MXN, USD, 16.95)` → `fx_rate (base='USD', quote='MXN', rate=16.95)`.
   Ingerir las etiquetas tal cual dejaría `fx_resolve('USD','MXN')` en CERO
   filas (todo lo USD sin cobertura) — o multiplicaría pesos ×17 si alguien
   consultara al revés. **La decisión del mapeo se escribe con su evidencia
   y con un test que la clave**: una tasa de ~17 con base=MXN es imposible
   (nadie paga 17 dólares por un peso).
2. **Cadencia y huecos ya medidos**: diaria (un cron de contabilidad la
   escribe ~08:00), con 3 huecos > 3 días en 10 meses (máx 5 días:
   2025-12-24→12-29, 2026-04-02→04-07, 2026-04-30→05-04). Todos dentro del
   `nearest_prior` de 7 días de `fx_resolve`. La corrida real los declara.
3. **`fx_rate` está VACÍA hoy (0 filas, medido)** y es APPEND-ONLY con
   trigger `prohibir_mutacion`: re-ingerir la misma PK
   `(rate_date, base, quote)` debe ser **no-op por conflicto, jamás UPDATE**
   (mismo patrón de re-corrida que 0.1/0.6).
4. **El contenedor no ve la contabilidad** (obstáculo 1 de la 0.1, ya
   resuelto allí): mismo camino de snapshot `--sqlite` y mismo patrón de
   subcomando (`ingest fx`).

### Decisiones de la 0.5 (escritas ANTES del codigo, 2026-08-31)

Cada decision cita la medicion del lead (Obstaculos de la 0.5) y el contrato
del esquema (`migrations/0001_initial.sql`: COMMENT de `fx_rate` + cuerpo de
`fx_resolve` + consumidor `v_tacos` que multiplica).

**D1 · Mapeo: etiquetas de la fuente se INVIERTEN; el numero se conserva.**
Fuente `(base=MXN, quote=USD, rate≈16.9–18.6)` → destino
`(base=USD, quote=MXN, rate=mismo)`. Evidencia:

- El consumidor real llama `fx_resolve(fecha, 'USD', 'MXN')` y **multiplica**
  (`l.amount * fx.rate` en `v_tacos` cuando `amount_currency <> MXN`). Una
  tasa ~17 con base USD convierte `1 USD → ~17 MXN` (pesos por dolar):
  correcto.
- Las etiquetas literales de la fuente dirian `1 MXN = 17 USD`: absurdo.
  Nadie paga 17 dolares por un peso. Un test clava esa imposibilidad
  semantica y exige que tras el mapeo `base=USD`.
- Ingerir etiquetas tal cual dejaria `fx_resolve('USD','MXN')` en CERO filas
  (toda la cuenta US sin cobertura) — o multiplicaria al reves si alguien
  consultara el par invertido. El COMMENT de `fx_rate` en 0001 ya documenta
  el incidente del sistema viejo con filas invertidas.

Par de origen distinto de `(MXN, USD)` → fila NO escrita y contada (hoy el
origen solo trae ese par; defensa contra futuro). Rate ≤ 0 / NULL / no
finito → skip contado (sellado 1 + CHECK `fx_rate_positiva`). `fx_resolve`
NO se toca (sellado 3).

**D2 · Acceso: mismo runbook de snapshot de la 0.1** (`ingest fx --sqlite`).
Snapshot `.backup()` de contabilidad, `mode=ro`, solo SELECT. Cadencia:
**manual** por ahora (igual que listings); la fuente ya es diaria en
contabilidad y los huecos medidos caben en el `nearest_prior` de 7 dias.
Cron diario se propone al lead cuando la 0.7 lo pida (mismo script que
costos, otro subcomando).

**D3 · Re-corrida: `INSERT … ON CONFLICT DO NOTHING` sobre la PK.**
`fx_rate` es append-only (`prohibir_mutacion`): jamas UPDATE. Misma PK
`(rate_date, base, quote)` ⇒ no-op real (`rows_written` no cuenta el
conflicto). Rate distinto el mismo dia/par no se "corrige" por UPDATE: queda
fuera (la fila publicada ES el dato; una correccion seria otra PK o una
migracion).

**Forma**: modulo hermano `app/fx.py` (no se mete en `costs.py`: frontera
distinta, tabla distinta, re-corrida distinta). Data shape: `FilaOrigenFx`
(crudo) → `mapear_destino` puro → `TasaFx` (fila lista para escribir).

## Obstáculos de la 0.6 (medidos por el lead 2026-08-31, antes de asignarla)

Medido en vivo contra `ledger_events` de contabilidad (13,144 filas) y
contra el esquema real de `ledger_event` en Orbit.

1. **`ledger_event` de Orbit está VACÍA (0 filas, medido)**: esta tarea
   ingiere TODO el libro, ventas incluidas — no hay ingesta previa de
   `kind='sale'` que respetar.
2. **El vocabulario de la fuente NO es el del enum destino.** Fuente:
   `sale_gross` / `fee` / `refund`; destino: `ledger_kind` =
   `sale/fee/refund/withholding`. Las retenciones vienen como `fee` con
   `fee_category` ∈ {`tax_withheld` (3,000), `isr_withheld` (973)}. El mapeo
   `event_type × fee_category → kind` se escribe EXPLÍCITO y con test; la
   propuesta del lead: `sale_gross→sale`, `refund→refund`,
   `fee+{tax_withheld,isr_withheld}→withholding`, resto de `fee→fee`.
3. **`platform='meli'` existe en la fuente (4,997 filas) y el enum de Orbit
   NO lo tiene.** Se EXCLUYE deliberado y contado — no es un error, es
   fuera de alcance. Y `'amazon'` de la fuente es **amazon_mx**: el rename
   es explícito, con test.
4. **`fee_category='ads'` (549 filas, ~gasto publicitario cobrado en el
   ledger)**: ingerirlo como `fee` y que la vista de margen (1.1) decida si
   lo resta — PERO dejarlo marcado (`fee_type='ads'`): el gasto de ads YA
   vive en las métricas de ads, y restarlo dos veces infla el costo. La
   decisión de la vista es de 1.1; la de esta tarea es NO perder la
   etiqueta.
5. **El signo NO es uniforme y no se "corrige"**: fees 10,212 negativos y
   **137 positivos** (reversas/ajustes reales), refunds 184 negativos y 15
   positivos, 3 ventas en 0. Decision (D4): el monto se conserva sin
   `abs` ni negacion; las filas que tras el mapeo de kind **violarian**
   `ledger_convencion_signos` **NO se escriben y se cuentan** (skip
   `viola ledger_convencion_signos`). Un test clava que una reversa
   positiva no se voltea y no se inserta. Meter reversas positivas
   exigiria migrar el CHECK (fuera de alcance de 0.6).
6. **El `sku` de la fuente es SKU de AMAZON, no de Odoo** (medido:
   `LQ-FV4D-DY2I` no existe en `product.odoo_sku`; los odoo_sku son tipo
   `4207`, `4405-BG`). `product_id` NO se resuelve por `product.odoo_sku`.
   Caminos posibles: el ASIN que viene en `raw_payload` → `listing`
   (producto de la 0.2) → `product_id`; o dejar `product_id` NULL contado.
   La decisión se escribe con su cobertura medida; **PROHIBIDO** un join por
   texto de SKU (lección de la 0.2: 1 % de cobertura).
7. **Dedupe servido por la fuente**: trae `id` y `dedupe_key` propios →
   `source_event_id` para `ledger_dedupe_source`. Los 596 fees sin
   `order_id` (ISR en bultos quincenales, ya anticipado por la fila de la
   tarea) caen en `ledger_dedupe_sin_orden` (`NULLS NOT DISTINCT`).
8. **`event_date` de la fuente es timestamp** (`2025-12-14 20:03:57`) y el
   destino es `DATE`: mismo colapso intradía que la 0.1, declarado y con
   test. `cogs_at_sale` de la fuente **NO se ingiere** (un número, una
   fuente: el costo vive en `sku_cost`); `quantity` sí (COGS se calcula en
   la vista). El desglose fiscal del `raw_payload` (`item_price`,
   `item_tax`, …) puede llenar las columnas homónimas — si se hace, con la
   MISMA moneda del amount y con test; si no, se declara.

### Decisiones de la 0.6 (escritas ANTES del codigo, 2026-08-31)

Cada decision cita medicion propia sobre snapshot/archivo vivo de
contabilidad (`mode=ro`, 13,145 filas) y el contrato de
`migrations/0001_initial.sql` (`ledger_convencion_signos` + tres indices
de dedupe). Re-verificado: `ledger_event` en Orbit = **0 filas**; `listing`
= 513 (mx 337 / us 176).

**D1 · Alcance: ingerir TODO el libro Amazon; MeLi fuera contado.**
Orbit esta vacia: no hay ingesta previa de `sale` que respetar. Se leen
las 8,147 filas `platform IN ('amazon','amazon_us')` y se EXCLUYEN las
4,998 `meli` (sellado 9), contadas en `rows_skipped` con motivo
`plataforma meli excluida`. Rename explicito: `amazon` → `amazon_mx`;
`amazon_us` → `amazon_us`. Cualquier otro valor → skip contado.

**D2 · Mapeo `event_type × fee_category → kind` (tabla, no ifs dispersos).**
Propuesta del lead adoptada y clavada con test:

| Fuente `event_type` | `fee_category` | Destino `kind` | `fee_type` |
|---|---|---|---|
| `sale_gross` | (cualquiera) | `sale` | NULL |
| `refund` | (cualquiera) | `refund` | `fee_category` o NULL |
| `fee` | `tax_withheld` / `isr_withheld` | `withholding` | la categoria |
| `fee` | resto (incl. `ads`) | `fee` | la categoria |
| otro | — | NO escrito | — |

`fee_category='ads'` (353 filas Amazon) se ingiere como `fee` con
`fee_type='ads'`: la vista 1.1 decide si resta; esta tarea NO pierde la
etiqueta (doble conteo ads vs metricas = riesgo de 1.1, no de 0.6).

**D3 · Acceso: mismo runbook de snapshot de la 0.1** (`ingest ledger
--sqlite`). Snapshot `.backup()` de contabilidad, `mode=ro`, solo SELECT.
Cadencia manual por ahora. Escritura SOLO en `ledger_event` (+
`ingest_run`) con `ORBIT_DSN_INGEST`.

**D4 · Signo: TAL CUAL cuando el CHECK lo admite; jamás voltear.**
Medido Amazon: fee+ 88 / refund+ 15 / sale≤0 = 3. El CHECK
`ledger_convencion_signos` exige `sale > 0` y
`fee|refund|withholding <= 0`. Voltear un fee+ a negativo **corrompe**
`SUM(amount)` (trata una reversa como cargo extra). Descartarlo en
silencio viola la regla 3. Decision: el monto fuente se conserva sin
`abs` ni negacion; las filas que tras el mapeo de kind **violarian** el
CHECK → **NO escritas y contadas** (`viola ledger_convencion_signos`).
Test: una reversa fee+ NO se voltea y NO se inserta. Residual declarado
para el lead: meter reversas positivas exigiria migrar el CHECK (fuera
de alcance de 0.6).

**D5 · `product_id` solo via ASIN→`listing`; jamás por texto de SKU.**
El `sku` fuente es seller SKU de Amazon (ej. `LQ-FV4D-DY2I`), no
`product.odoo_sku` (leccion 0.2: join por texto = 1 %). Medido: ASIN en
`raw_payload` solo en ventas (1,424/1,653 `sale_gross`; 0 en fees).
Camino: `ASIN` del payload → `listing.(platform, external_id)` →
`product_id`; sin ASIN o sin listing → `product_id` NULL contado
(`sin listing para ASIN` / `venta sin ASIN`). Fees/refunds quedan NULL
de producto (no hay ASIN en payload). `cogs_at_sale` NO se ingiere.

**D6 · Dedupe: `source_event_id = dedupe_key` (unico 8,147/8,147);
`order_id` vacio → NULL.** Con source id siempre presente en Amazon, la
re-corrida pega en `ledger_dedupe_source`. Los 400 `order_id=''`
(20 `isr_withheld` + ads/storage/…) se normalizan a NULL; el ISR
**entra** (no se excluye ni se prorratea en 0.6: la vista ya expone
`cargos_sin_orden`). Los indices `sin_orden` / `con_orden` se ejercitan
en tests con fixtures sin `source_event_id` (DoD: cada clase de cargo
llega a su indice). Re-corrida: `INSERT … ON CONFLICT DO NOTHING` sobre
los tres; conflicto = skip contado, base identica.

**D7 · `event_date` → DATE (colapso intradía declarado).** Medido: 1,678
filas con hora (`2026-01-16 06:52:15`) y 6,469 solo-dia. Destino DATE =
`date(event_date)` / `fromisoformat(...).date()`. Varias filas el mismo
dia con distinto `dedupe_key` son hechos distintos (no se fusionan).
Test: timestamp intradía colapsa al dia sin perder el evento.

**D8 · Desglose fiscal SI; `cogs_at_sale` NO; moneda = la del amount.**
De `raw_payload` (Orders): `ItemPrice`/`ItemTax` como
`{CurrencyCode, Amount}` → `item_price`/`item_tax` cuando la moneda
coincide con `amount_currency`; si diverge o falta → NULL (no se inventa).
`ShippingPrice`/`ShippingTax` igual. `quantity` = columna fuente (o
`QuantityShipped` del payload si la columna viene vacia en venta).
`cogs_at_sale` se ignora (un numero, una fuente: `sku_cost`). Nota
medida: `amazon_us` trae **100 % currency=MXN** en la fuente — se
guarda MXN (moneda original reportada), no se inventa USD.

**Forma**: modulo hermano `app/ledger.py`. Data shape: `FilaOrigenLedger`
(crudo) → `mapear_destino` puro (tabla de kind) → `EventoLedger` (fila
lista) → `sync_ledger` con mapa `(platform, asin)→product_id` precargado
desde `listing`.

## Fase 1 — margen medible y honesto (todavía NO decide nada)

`[lane:gate]` — produce lectura y alertas. Cero escrituras a Amazon.

| Task | Contenido | DoD | Depends | Status |
|---|---|---|---|---|
| 1.1 | **Diseño de la vista de contribucion POR ENTIDAD PUBLICITARIA.** `v_margen_plataforma` no sirve y no se fuerza (sin entidad, sin fecha, sin halo). Vista NUEVA, misma resolucion de costo/FX (sellado 7). **Ingreso = metricas de ads** (`ad_revenue` / `revenue_same_sku`): el obstaculo 1 midio multi-home total, asi que el ledger NO atribuye venta a la entidad (reconcilia plataforma). Debe definir COGS proxy del catalogo del ad group, ventana/vintage, rango de halo, y que cargos NO entran (multi-home). Doc: `docs/MARGEN-ENTIDAD.md`. `[tdd:skip:diseno]` | Diseno escrito y revisado por el lead ANTES de implementar: fuente de cada columna, supuesto de atribucion declarado, y por que no se reusa la vista de plataforma | 0.7 | cc:完了 [2026-08-31, Cursor PR #96 (docs/MARGEN-ENTIDAD.md). Diseno con adversario propio + codex ANTES de entregar (13 hallazgos absorbidos y versionados); renombre honesto a contribucion pre-cargos (los cargos no se atribuyen por entidad: multi-home total) y candado de uso auto-impuesto (no decisoria). SELLADO por el lead con UNA enmienda medida en vivo: D1.mx usa el precio NETO REALIZADO del ledger (100% cobertura; item_price/amount=0.8604 = 1/1.16 exacto, el IVA) — mueren los sesgos de IVA/precio-no-realizado/sin-historia en MX y la razon queda adimensional; US queda con listing_price + D1.bis vivo (0/153 ventas US con item_price, candado D8 de la 0.6). Aprobacion literal del dueno: "si aprobado, sella y dale la 1.2 a cursor". La 1.2 implementa este documento con la ola fail-loud D6 en el mismo tren] |
| 1.2 | **Implementación de la vista** del diseño 1.1. Cada fila sale con su moneda, su edad de dato declarada, **dos números de margen (con halo y sin halo)** y **qué clases de cargo entraron** (sellado 4). Un solo número está PROHIBIDO. Dato faltante = fila no escrita. `[tdd:required]` | Rojo antes del código. Tests: fila sin costo → ausente, no cero; fila US con FX `nearest_prior` → presente pero MARCADA, y sin tasa utilizable → ausente; el par con-halo/sin-halo siempre presente o la fila no sale; consulta sin vintage falla. Corrida real con el `SELECT` y el rango citado en ambas plataformas | 1.1 | cc:完了 [2026-09-01, Cursor PR #97 → master `28633b7`; migracion 0006 aplicada en goncloud por el lead y corrida real verificada. La entrega trajo adversario+kimi+grok propios ANTES del review. TRES cierres del lead: (1) el candado estatico del ensanchamiento repuesto sobre el SUBARBOL del CTE gasto (la enmienda de Cursor lo habia soltado; verificado discriminante en local); (2) la semilla MX endurecida — listing_price 250 vs neto realizado 100: con ambos en 100, la mutacion exacta contra la enmienda D1.mx del sello pasaba identica; (3) FIX de fan-out en ratio_dia — dos product_ads del MISMO producto duplicaban el COGS (el propio test de la enmienda de Cursor lo cazo ROJO en CI: esperado 7.5, salia -5; dedup por producto, regla 9 de facto). CI 925 verdes. **CORRIDA REAL** (2026-09-01): MX publica 108 entidades, par halo completo, cero rango invertido, cero FX (razon adimensional D1.mx), rango 90d 19,651..105,744 MXN; el residual de la 0005 VISIBLE en la vista (gasto_campaign_sin_contraparte agosto = 4.75 MXN exacto); desfase metricas-vs-ledger MX -1,920 MXN (~6%, contado). **US publica CERO por diseno**: el candado de cobertura 100% + grupos de hasta 1,259 ASINs = todo grupo con >=1 anuncio sin mapear queda ausente contado (273 catalogo_parcial; el precio NO es — 176/176 listings US con USD). La palanca es el mapeo, no el codigo. Nota de proceso: la enmienda de Cursor se pusheo sin esperar su CI (quedo rojo con su propio test — el sistema funciono)]|
| 1.3 | **Digest diario por Telegram** (lo pide la Fase 3). Reusa `app/notifica.py` (fail-silent con NOTA en `notes`, ya sellado en 3.3 y 1.4): qué decidió el motor, cuánto se aplicó contra qué tope, y el margen del día **como rango**. Sin canal, el ciclo JAMÁS se degrada. `[tdd:required]` | Rojo antes del código. Tests: canal caído no tumba el ciclo y deja NOTA; el digest declara el modo del ciclo (live/shadow); el margen aparece como rango o no aparece. Envío real verificado una vez | 1.2 | cc:完了 [2026-09-01, PR #100 → master 880c539: digest extiende contribucion como rango desde v_contribucion_entidad; sin dato con motivos de cobertura; residual tacos; ORBIT_DSN_READ. Deploy hecho. ENVIO REAL verificado 2026-09-01: digest del ciclo #30 (amazon_mx, shadow, 48 decisiones) reenviado desde el contenedor con secretos reales; Telegram ok=true y el dueno confirmo recepcion] |
| 1.4 | **Vista de lectura del margen en el dashboard** (server-rendered, sin JS: la CSP es `default-src 'self'`). Margen por campaña con su rango, su moneda, su edad de dato y su marca de FX aproximado. Valor nulo se ve como `—` CON etiqueta, jamás como `0` (mismo criterio que la quota de 1.5). Sin endpoints de escritura nuevos. `[tdd:required]` | Rojo antes del código. Tests de render: el rango se ve como rango; ausencia de dato NO se renderiza como cero; una plataforma sin margen no rompe la pantalla. `test_architecture` verde, cero escritura | 1.2 | cc:完了 [2026-09-01, Cursor PR orbit-06/1-4-dashboard: pantalla /contribucion server-rendered (rollup SUM hijas v_contribucion_entidad, motivo dominante v_contribucion_cobertura, etiqueta no decisoria). Modulo app/dashboard_contribucion.py; tests tests/test_ui_contribucion.py. TDD rojo antes del codigo. Revision del lead (kimi) cazo dos huecos y se cerraron con TDD en el mismo PR: edad de dato POR campana (columna Datos, no solo ventana de plataforma) y motivo visible JUNTO al rango cuando la cobertura es parcial (D4: no SUM parcial disfrazado de completo). Cierre del lead 2026-09-01: PR #99 (fixes review) → master 259ffc2; PR #101 (migracion 0007 perf vistas: fx_dia/costo_producto_dia/vivos_pesos pre-unidos, agregacion a grano grupo-dia-moneda; equivalencia EXCEPT = 0 difs, 101.6s→2.5s; evidencia docs/SELECT-EVIDENCIA-0007.md; aplicada en goncloud) → master 3b7cf5e; PR #102 (fix rollup: hijos SELECT DISTINCT a grano entidad — multiplicaba x dias ~x65-90 — y ent/cob MATERIALIZED contra Nested Loop >240s; verificado en prod 7.9s MX / 6.5s US, 0 campanas con rollup != suma directa) → master d2099d8. Deploy verificado: /contribucion 200, MX 23 campanas (13 con contribucion), US 15 (0 por catalogo_parcial, palanca = mapeo)] |
| 1.5 | **Precio multilisting US: MIN marcado** (enmienda D1.bis, sello del dueño 2026-09-01). Medido en prod por el lead: el `catalogo_parcial` de US NO era mapeo (0 huecos en campañas ENABLED) sino 2 productos (120/356) con dos ASINs a precios distintos; el candado "un solo precio por producto" excluía 273 entidades (~4,908 USD de gasto 90d). Sin ponderación posible (ledger US sin ASIN ni `item_price`), la regla nueva: `MIN(listing_price)` y la fila sale marcada (`precio_min_multilisting`, columna nueva AL FINAL de la vista; tag en dashboard, sufijo en digest). Migración 0008 (solo `v_contribucion_entidad`; MX intacto). `[tdd:required]` | Rojo antes del código (commit rojo con stub de 0008, CI en rojo). Tests: multilisting publica con price_i = MIN y marca; precio único sin marca; MX flag false; tag visible en UI; sufijo en digest. Estáticos: interfaz 0008 = 0007 + columna al final; dirección FX sellada intacta. Verificación en prod con TEMP view ANTES de aplicar: MX EXCEPT = 0, US gana exactamente las 273 y todas marcadas de grupos con 120/356 | 1.2 | cc:完了 [2026-09-01, lead (kimi). Rama orbit-06/1-5-precio-multilisting: TDD rojo (commit 2b87de4 con stub de 0008, 3 fallos locales + integracion roja en CI) -> verde (db089aa). PR #106 → master 0cf021a. Verificacion TEMP en prod ANTES de aplicar (docs/SELECT-EVIDENCIA-0008.md): MX EXCEPT = 0 filas (108=108, 0 marcadas); US gana exactamente las 273, todas marcadas, todas de grupos con 120/356. 0008 aplicada en goncloud + redeploy: US 15/15 campanas publican, 15 marcadas. Fix de cola en el mismo tren: 0009 redondeo NUMERIC(14,4) de cogs/contrib computados (colas de ~40 decimales; PR #107 → a6e3785, TEMP verificado: dif max <= 0.00005 redondeo puro), aplicada en goncloud; endpoint 200 ~30s, valores limpios (-356.8439 .. 634.6437 USD). La palanca US ya no es mapeo sino la REGLA nueva; queda la decision de negocio de si los ASINs duplicados (B09QC3X991/B0CR6YYSHP, B0B36NHWY5/B0CKB2413S) deben coexistir] + cross-review (claude+codex+grok, ronda unica 2026-09-01): 3 hallazgos media corregidos en PR #109 → master 3496177 (migracion 0010: marca exige peso — presencia sin ventas ya no marca — y contrib se computa del cogs YA redondeado, columnas publicadas reconcilian al 4o decimal; reversa 0008 con GRANTs), cada uno con test rojo demostrado; TEMP en prod todo-cero (0 marcas/llaves/valores distintos) antes de aplicar; declarados sin fix: guardrail de precio basura (regla sellada) y drift 0008/0009 (0008 ya aplicada) |

## Obstáculos de la 1.1 (medidos por el lead 2026-08-31, antes de asignarla)

Misma disciplina que 0.1–0.6: lo caro se mide ANTES y va al plan versionado.
Todo medido en vivo (SOLO LECTURA) sobre la base de producción. Estos hechos
DICTAN medio diseño; lo que queda abierto es la decisión del diseñador.

1. **El multi-home es TOTAL — la atribución por listing está muerta de
   entrada.** TODOS los productos vendidos (90d) se anuncian en MÁS de 5 ad
   groups vivos a la vez: MX 102/102 productos (máx 18 grupos), US 39/39
   (máx 46). Atribuir la venta del ledger "al grupo que anuncia el producto"
   contaría cada venta hasta 18–46 veces. Consecuencia directa: **el lado
   de ingreso del margen por entidad viene de las MÉTRICAS de ads**
   (`ad_revenue` / `revenue_same_sku`, la atribución de Amazon al
   keyword/target), no del ledger. El ledger queda como la verdad a nivel
   PLATAFORMA (reconciliación, no atribución) — exactamente lo que la fila
   1.1 insinuaba y ahora está medido.
2. **El rango de halo está COMPLETO en los datos**: 100 % de las filas
   maduras de 90d traen ad_revenue Y revenue_same_sku (MX 6,681/6,681, US
   4,998/4,998). Magnitudes: MX 214,469 MXN atribuidos vs 75,072 del mismo
   SKU (65 % halo); US 12,816 USD vs 4,671 (64 %). El "rango con
   halo / sin halo" del DoD de la 1.2 es directamente construible.
3. **Unidades del lado ads NO existen — pero el proxy está medido y es
   bueno**: `ads_metric_observation` trae `orders`, no unidades. Y el
   ledger dice que HOY toda venta es de 1 unidad (90d: MX 300 ventas = 300
   unidades exactas; US 178 = 178). `orders ≈ unidades` es un supuesto
   MEDIDO, no inventado — se declara y se le pone test de vigencia (si
   aparecen ventas multi-unidad, el supuesto caduca ruidosamente).
4. **El COGS por entidad es LA decisión abierta del diseño.** Un order en
   la keyword K del grupo G no dice QUÉ producto del grupo se vendió, y los
   grupos anuncian decenas de productos (0.4). Candidatos que el diseñador
   debe evaluar CONTRA estos números: costo promedio simple de los
   productos del grupo; promedio ponderado por revenue del ledger;
   costo/precio como razón aplicada a ad_revenue. La elección cambia qué
   significa el número: PARA y propónsela al lead antes de programar
   (mismo ritual que la 0.4).
5. **Monedas, ya sin sorpresas**: métricas US en USD (el gasto necesita
   fx_resolve, patrón de v_tacos 0005); ledger TODO en MXN (contabilidad
   convierte río arriba — D8 de la 0.6). El ledger de la reconciliación no
   necesita FX; el gasto y el ad_revenue de US sí.
6. **La reconciliación fail-loud nace en esta ola** (residual de la 0005
   subido de prioridad por codex en la cross-review externa): contador
   `gasto_campaign_sin_contraparte` en v_tacos + test VIVO de fail-loud
   (cost NULL → tacos_pct NULL) + candado anti-deriva de la allowlist de
   kinds (3 copias). La vista de margen no nace al lado de deuda declarada
   sin cerrar.
7. **Atribuibilidad del ledger (para la reconciliación)**: 84 % del importe
   de ventas MX con product_id resuelto (1.159M de 1.376M MXN), 88 % en US.
   Los sin producto se cuentan, no se esconden.

### Decisiones de la 1.1 (PROPUESTA Cursor, 2026-08-31 — enmendada post cross-review adversario+Codex; pendiente de sello del lead)

Documento completo: `docs/MARGEN-ENTIDAD.md`. Ritual de la 0.4: el lead
sella ANTES de la 1.2.

**D1 · COGS = C+B** (razon costo/precio ponderada por revenue ledger),
aplicada a cada punta del ingreso. Candados del cross-review: cobertura
**100 %** del catalogo vivo del grupo (si no, ausente; misma ley que
0.7 / `v_margen_plataforma`); Σ ventas ledger = 0 → **ausente** (NO
peso uniforme = A); `cost_i` por `metric_date` (no dia representativo).
Sesgos: mezcla; halo proxy; IVA MX; precio realizado ≠ vitrina; precio
sin historia (`listing_price` as-of query → no backtesteable).

**D1.bis · Bloqueo decisional.** Solo lectura (digest/dashboard) hasta
que el lead selle (a) precio neto+efectivo por dia, (b) otra formula sin
`listing_price`, o (c) aceptacion escrita del sesgo. Fase 2 no consume
esta senal sin ese sello.

**D2 · Grano** = `keyword` / `product_target`. Catalogo via `product_ad`.

**D3 · Nombre `contrib_*` / vista `v_contribucion_entidad`.** No se llama
margen: fee/refund/withholding no entran (multi-home). Gasto de entidad =
metricas; `fee_type=ads` del ledger es otra superficie (D6 las compara).

**D4 · Vintage** D-15; **ventana** 90d maduros; serie incompleta
fail-loud (contadores; una fila hueca → entidad ausente).

**D5 · Moneda.** Metricas + `fx_resolve`. Pesos ledger: convertir o
excluir (el esquema EXIME a `ledger_event` del sello de moneda).

**D6 · Ola fail-loud** con 1.2: contador campaign sin contraparte, test
cost NULL, allowlist kinds ×3, desfase ads metricas vs ledger, cobertura
por motivo.

**D7 · Ausencia.** Catalogo parcial, sin mezcla, sin FX, serie incompleta,
sin par halo → no escrita.

**D8 · Candado SQL** `quantity IS DISTINCT FROM 1` en ventas maduras de
la ventana → 0 filas o falla (supuesto orders≈unidades; la vista no usa
`orders`).

## Fase 2 — margin-aware targets (la única que decide; nace en shadow)

`[lane:release]` — no arranca sin Fases 0 y 1 cerradas Y ≥1 semana post-flip.

> **Enmienda del dueño (2026-09-03)**: arranca el día 2 post-flip por su
> decisión de cadencia («no podemos ser ultra conservadores… un test es
> suficiente»). Diseño aprobado con literales en `docs/superpowers/specs/2026-09-03-target-margen-plataforma-design.md`.
> **Cambio de planteamiento medido**: el margen por campaña es uniforme
> (≈0.69 MX / ≈0.79 US antes de cargos) → el target se deriva del **margen
> neto por plataforma del ledger** (40.2 % MX / 35.9 % US antes de
> publicidad), no de `v_contribucion_entidad` (sigue no decisoria). Fracción
> del dueño: **la mitad** → ≈ 20 MX / ≈ 18 US, casi el manual de hoy; el
> valor es que quede medido, con procedencia y auto-ajustable.

| Task | Contenido | DoD | Depends | Status |
|---|---|---|---|---|
| 2.1 | **Diseño del target margin-aware**, con el dueño. `[tdd:skip:decision-dueno]` | La regla escrita en `docs/CONTEXTO.md` con el texto literal de la decisión del dueño, incluyendo qué pasa ante señal ausente o contradictoria | 1.2, ORBIT 05 en live | cc:完了 [2026-09-03, lead con el dueño. Medido en la base viva: margen uniforme por campaña (un target por campaña no discrimina) y margen neto por plataforma 40.2 % MX / 35.9 % US (cargos sin ads 33.6 % / 48.9 %; el envío cross-border de US = 21 % de la venta, explicado al dueño). Decisiones literales: «La mitad (utilidad)» (fracción 0.5), «ok va asi» (camino A: peldaño `margen_plataforma` en la cascada, banda [10,45], paso ≤0.5 pt/ciclo, abstención → setting), requisito literal «se va a ir ajustando automaticamente no?» → frescura obligatoria (cron de ledger+fx, hoy manuales: última 08-31/08-28). Contradicción con/sin halo: irrelevante para esta fuente (ledger sin atribución); `v_contribucion_entidad` sigue no decisoria. Spec: `docs/superpowers/specs/2026-09-03-target-margen-plataforma-design.md`; CONTEXTO sellado en el mismo PR] |
| 2.2 | **Infra — frescura del ledger y del FX** (Grok o lead): los dos pipelines leen el MISMO snapshot SQLite de accounting que ya usa `refresh_costos.sh` (07:30 UTC: copia `accounting.db` → `docker cp` → `ingest costs --sqlite`); extender ese script (o uno hermano con el mismo `trap` de limpieza) para correr también `ingest fx --sqlite $SNAP` e `ingest ledger --sqlite $SNAP` (flags exactos según el runbook de `docs/DEPLOY.md` y `app/cli.py`, regla 8), ANTES del ciclo 08:40 UTC y DESPUÉS del sync de accounting (`sync_ads_to_ledger.py`, cada 6 h); un fallo de un pipeline NO debe tumbar los otros (cada uno con su `ingest_run` ok/false); log propio en `logs/`; corrida manual de estreno con `ingest_run` ok=true. `[tdd:skip:ops]` | `crontab -l -u gon` con las dos líneas; `ingest_run` de ledger y fx con `ok=true` y fecha de hoy; `max(observed_at)` del ledger = hoy; sin tocar el resto del crontab (disciplina aditiva) | 2.1 | cc:TODO |
| 2.3 | **GLM — vista + peldaño + guardas + superficie** según el spec §3-§9: migración `0015_target_margen_plataforma.sql` (vista `v_target_margen_plataforma`, GRANT a los roles de lectura, COMMENT con la fórmula y el lag); setting `ads_target_fraccion_margen_<platform>`; peldaño `margen_plataforma` en `cascada_target_acos` Y `cascada_target_acos_con_procedencia` (equivalencia extendida); abstenciones con vocabulario cerrado; paso máximo desde `notes.target.aplicado` del último ciclo live; freeze `target_procedencia` + snapshot en `decision.inputs` y `notes.target`; `/salud` y digest (§9); `docs/DASHBOARD.md`, `docs/DATABASE.md`, `docs/APPLY.md` al día; SELECT de comparación sombra (§10.2) como `tools/compara_target_margen.py` read-only. Mismas reglas de proceso que BIDS 01 (prohibido tocar producción; decisiones ANTES del código; rojo antes del verde; 1 PR). `[tdd:required]` | Rojos contra master: (a) cascada con fracción presente y margen válido devuelve `margen_plataforma`; (b) cada motivo de abstención cae al setting (6 casos); (c) banda y paso máximo; (d) freeze lleva procedencia y snapshot; (e) replay golden intacto; (f) equivalencia motor↔dashboard con el peldaño; (g) vista: cobertura <95 % → NULL, mezcla de moneda → NULL, ventana exacta [D-105, D-15), `fee_type=ads` excluido; suite completa verde en CI; herramienta de comparación produce la tabla sobre la base de tests | 2.2 | cc:TODO |
| 2.4 | **Lead — entrada** (spec §10): review + CodeRabbit (1 ronda), deploy con migración 0015 primero, **un ciclo sombra comparado** (tabla manual vs derivado con el SELECT en la evidencia), go literal del dueño, `config_version` nueva con `ads_target_fraccion_margen_*` = "0.5" (el interruptor), verificación del primer ciclo con procedencia `margen_plataforma` en `notes.target` y en el freeze, `/salud` y digest, AppFlowy. `[tdd:skip:ops]` | Evidencia: tabla sombra, go literal, `config_version` id, `SELECT` del primer ciclo con `target_procedencia = 'margen_plataforma'` y `target_aplicado` ≈ 20 MX / 18 US; AppFlowy Done | 2.3 | cc:TODO |

## Reject (con razón)

- **Un solo número de margen por campaña.** Viola el sellado 5 y la decisión
  literal del dueño. Con el halo en 56-58.5%, un número único no simplifica:
  elige en secreto un supuesto cuyo signo nadie conoce.
- **Rellenar el costo faltante con cero, promedio o último conocido.** Es la
  trampa ya pagada (`sales_history.cogs`, 49% sin costo).
- **Tocar `fx_resolve` para que devuelva NULL en vez de `nearest_prior`.**
  Su fallback de 7 días está medido contra la cadencia real y su `source`
  ya permite distinguir. Cambiarla para satisfacer un test es romper la
  única fuente de conversión (sellado 3).
- **Publicar margen con el ledger solo de ventas.** Sin `fee`/`refund`/
  `withholding` el margen miente hacia arriba (sellado 4).
- **Elegir un `listing_id` arbitrario en un ad group multi-ASIN.** Tuerce el
  margen sin dejar rastro; se cuenta como no atribuible.
- **Ampliar `LIST_REQUEST_TYPES` sin la evidencia regla 8 en vivo.** Es un
  allowlist de seguridad, no una constante de configuración.
- **Incluir MeLi en la misma vista de margen** (sellado 9).
- **Adelantar la Fase 2 antes del flip.** Un target validado contra
  decisiones que nunca se aplicaron no demuestra nada.

## Pre-aprobaciones del plan (se piden UNA vez, aquí)

- **Lectura de la base de contabilidad** — `sqlite3`, solo `SELECT` sobre
  `sku_costs` y las tablas de producto/listing que 0.2 identifique. Razón:
  única fuente de costos. Alcance: Fase 0, tareas 0.1 y 0.2. Lectura pura.
- **Lectura de la API de Amazon Ads** — `/sp/productAds/list` y los `/list`
  que 0.2 necesite, por el cliente de lectura. Razón: única forma de saber
  qué ASIN anuncia cada ad group. Alcance: Fase 0, tareas 0.3 y 0.4.
  **Read-only: ni un PUT, ni un POST de creación.**
- **Escritura en la base de Orbit** — `INSERT` en `product`, `sku_cost`,
  `listing`, `fx_rate`, `ledger_event` y `UPDATE` de `ad_entity.listing_id`,
  con el rol de ingesta. Alcance: Fase 0. **No toca `decision`,
  `apply_queue` ni `ads_optimizer_goal`.**
- **Migración de esquema, SÓLO si la tarea 0.4 decide extender
  `ad_entity_kind`** — `ALTER TYPE ... ADD VALUE`, por el runbook de
  migraciones con backup previo y `SELECT` de verificación. Alcance: 0.4, y
  solo si la decisión documentada lo justifica. **OJO (cross-review kimi,
  ronda 2)**: en PostgreSQL un valor de enum recién agregado **no puede
  usarse en la misma transacción que lo agrega**. La migración commitea
  ANTES de cualquier `INSERT` que use `product_ad`; migración e ingesta van
  en pasos separados, jamás en una sola transacción.

**Fuera de esto, con go explícito en el momento**: cualquier mutación en
Amazon, cualquier cambio de umbral del motor, cualquier `INSERT`/`UPDATE`
sobre las tablas de decisión, y el paso de la Fase 2 a live.

## Correcciones de la v1 (cross-review codex + grok, 1 ronda, 2026-08-30)

Los 10 hallazgos fueron verificados por el lead contra
`migrations/0001_initial.sql` y `app/ads/client.py`: **ninguno se cayó**.

| # | Sev | Hallazgo | Corrección |
|---|---|---|---|
| 1 | alta | `v_margen_plataforma` agrupa solo por `(platform, amount_currency)`: el JOIN por `listing_id` de la v1 era imposible | Sellado 7 + tarea 1.1 de diseño: vista nueva, misma resolución de costo/FX |
| 2 | alta | El DoD de FX exigía NULL en día sin tasa, contra el `nearest_prior` de 7 días de `fx_resolve` | Sellado 3: la función no se toca; el consumidor lee `source`. DoD reescrito |
| 3 | alta | `/sp/productAds/list` no está en `LIST_REQUEST_TYPES`; la v1 lo daba por habilitado | Tarea 0.3 propia, con el ritual regla 8 que pagó `negativeKeywords` |
| 4 | alta | Ledger solo `kind='sale'` deja los cargos en 0 → margen sistemáticamente alto | Sellado 4 + 0.6 ingiere también `fee`/`refund`/`withholding` |
| 5 | alta | El test "misma venta dos veces → dos filas" choca con los 3 índices únicos de dedupe del ledger | 0.6: re-ingesta es **no-op**; la semántica bitemporal es de `ads_metric_observation`, no del ledger |
| 6 | media | N product ads → un solo `ad_entity.listing_id`, sin política | 0.4 decide antes de escribir; propuesta: no atribuible, jamás uno arbitrario |
| 7 | media | `price_currency` obligatorio viola `listing_precio_con_moneda` (ambos NULL es válido) | 0.2: el precio es opcional; el producto es el mapa |
| 8 | media | `ad_entity_kind` no tiene `product_ad` | 0.4 decide enum vs. no materializar, con migración pre-aprobada si aplica |
| 9 | media | 0.1 no cubría el re-run real (EXCLUDE gist + trigger que solo cierra vigencia) | 0.1: no-op verificado corriendo la ingesta dos veces |
| 10 | baja | Plan ausente de `plans/manifest.json`; ruta del diseño v2 rota | Registrado en el manifest; ruta corregida a `docs/traspaso/` |

### Ronda 2 (kimi + qwen, 2026-08-30) — el tope duro; no hay tercera

**qwen no entregó**: colgado a los 300 s porque su versión no ejecuta
herramientas en modo no interactivo (el mismo fallo que grok declaró en el
preflight 1.6a). Declarado, no reintentado.

**kimi verificó las 8 afirmaciones de esquema del plan v2 como CORRECTAS**
—`v_margen_plataforma` sin `listing_id` ni dimensión temporal; `fx_resolve`
con `exact`/`nearest_prior`/cero filas y su campo `source`; los 5 paths de
`LIST_REQUEST_TYPES` sin productAds; `ad_entity_kind` sin `product_ad`;
`listing_precio_con_moneda` admitiendo ambos NULL; los 3 índices de dedupe
del ledger; el `EXCLUDE` y el trigger de `sku_cost`; y el registro en el
manifest— y halló 2 residuales BAJOS, ambos corregidos aquí:

| # | Sev | Hallazgo | Corrección |
|---|---|---|---|
| 11 | baja | `manifest.json` registraba orbit-06 pero `active` seguía en `orbit-05-preflight`, que está **9/9 cerrado**: el puntero señalaba trabajo terminado, y AGENTS.md dice que ese campo marca el plan cuyas tareas se siguen | `active` → `orbit-06`. Razón: el preflight está cerrado y `orbit-05` (el cutover) está bloqueado por calendario hasta ~2026-09-07; el único plan que hoy se puede seguir es la Fase 0 de éste. El día del flip el puntero vuelve a `orbit-05` |
| 12 | baja | La pre-aprobación de `ALTER TYPE ... ADD VALUE` no advertía que un valor de enum nuevo **no puede usarse en la misma transacción que lo agrega** | La pre-aprobación ahora exige commitear la migración ANTES de cualquier `INSERT` que use `product_ad`: pasos separados |

## Verificación del plan (contrato de calidad)

- `team_validation_mode`: **subagent** — dos rondas de cross-review externa
  simultánea: codex + grok sobre la v1 (10 hallazgos, 5 altos) y kimi + qwen
  sobre la v2 (qwen no entregó; kimi confirmó las 8 afirmaciones de esquema y
  halló 2 residuales bajos, corregidos). **Es el tope duro del kit: la 2ª
  ronda se justificó porque la 1ª halló severidad alta, y no habrá una 3ª.**
  Más la evaluación del lead de las cinco perspectivas. Producto: la fase entrega lectura antes que
  decisión. Arquitectura: una sola resolución de costo y FX (sellado 7), cero
  duplicación de `fx_resolve`. Seguridad: la ampliación del allowlist de POST
  es una tarea propia con evidencia en vivo, no un detalle. QA: cada tarea de
  código lleva rojo antes del verde y tests que discriminan contra el
  esquema REAL, no contra el esquema imaginado. Escéptico: el riesgo mayor
  sigue siendo la **cobertura del dato**, por eso 0.7 puede bloquear la fase.
- **`Spec skip reason`**: no se abre delta de producto. El contrato ya está
  sellado en `docs/CONTEXTO.md` (reglas 2/3/4, las trampas del dominio y la
  definición de la Fase 3); este plan es el ledger de tareas y no introduce
  comportamiento de producto nuevo. La única decisión de producto pendiente
  —qué hace el motor ante un rango de halo contradictorio— se toma en 2.1 y
  ESA sí se escribe en CONTEXTO.
- **Baseline de lint/formato**: ya existe (`.pre-commit-config.yaml` con ruff
  + candados anti-monolito, batería completa en CI). Sin tarea de setup.
- **Datos NO observados** (`not_observed != absent`): el % de SKUs anunciados
  con costo conocido; el vendor Content-Type de `/sp/productAds/list` (no hay
  evidencia regla 8 en el repo); y si existen ad groups multi-ASIN en MX/US
  —lo que decide si el hallazgo 6 es teórico o seguro—. Los tres se declaran
  `unknown` y los resuelven 0.3, 0.4 y 0.7 con medición, no con supuesto.
