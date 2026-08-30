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

## Fase 0 — el dato (NO depende del flip; empieza hoy)

`[lane:gate]` — cada tarea es un candado de datos: si la cobertura no
alcanza, la Fase 1 no arranca.

| Task | Contenido | DoD | Depends | Status |
|---|---|---|---|---|
| 0.1 | **Ingesta de productos y costos** a `product` + `sku_cost`. Fuente: `sku_costs` de la SQLite de contabilidad. Mapeo explícito de nombres: `sku` → `product.odoo_sku`, `cost` → `cost_amount`, `currency` → `cost_currency`. La vigencia bitemporal se PRESERVA. **Semántica de re-corrida contra el esquema real**: `sku_cost` tiene un `EXCLUDE` (btree_gist) que impide dos vigencias solapadas del mismo producto, y el trigger `sku_cost_solo_cierra_vigencia` permite **únicamente** cerrar `valid_to` — no hay UPDATE de importe ni DELETE. Por lo tanto: vigencia nueva ⇒ cierra la anterior con `valid_to` e inserta; vigencia idéntica ya presente ⇒ **no-op**, jamás error ni fila duplicada. `cost` 0 o NULL ⇒ fila no escrita y contada (sellado 1). `includes_tax` se resuelve leyendo qué produce Odoo, no se asume. Subcomando `ingest costs`. **Tres obstáculos medidos por el lead el 2026-08-30 que hay que resolver ANTES de escribir código — ver §Obstáculos de la 0.1**: el contenedor no ve la base de contabilidad; las vigencias de origen son timestamps intradía y el destino es DATE con `CHECK` y `EXCLUDE`; y `product.name` e `includes_tax` no tienen fuente. `[tdd:required]` | Las tres decisiones de §Obstáculos escritas y justificadas ANTES del código. Rojo antes del código. Tests: costo cero rechazado y contado; **colapso de vigencias** (varias filas del mismo SKU el mismo día → UNA vigencia; sin este test la ingesta revienta contra el `EXCLUDE`); vigencia nueva cierra la anterior (una sola vigente por producto); **re-correr la ingesta completa dos veces deja la base idéntica** (no-op real, no "mismo ingest_run"); intento de modificar un importe existente → rechazado por el trigger; SKU sin nombre sigue el camino decidido y queda contado. Corrida real con conteos, cuántas filas se colapsaron y lista de SKU rechazados con motivo | - | cc:完了 [2026-08-30, GLM PR #63 → master `faac179`. **Verificado por el lead contra la base VIVA, no contra la evidencia**: 1,087 productos / 1,955 vigencias / 181 nombres derivados; **una sola vigencia abierta por producto (1,087 de 1,087)**; 100 % MXN e `includes_tax=false` en las 1,955; cero costos ≤ 0; los 974 NULL de `valid_from` del origen cuadran exactos. **Recálculo INDEPENDIENTE del lead con SQL propio sobre el origen: 1,955, idéntico.** Caso punta a punta: `Y4-FB35-N645` con 7 filas del mismo costo y ruido de segundos → UNA vigencia. **No-op confirmado CUATRO veces** (corridas 30-33 con `rows_written=0`), más de lo que declaraba el PR. La remediación de la corrida 27 (1,522 escritas / 506 saltadas por ruido binario del REAL) no dejó daño: **los 8 triggers quedaron activos**, el `EXCLUDE` y los dos CHECK presentes, y de esa carga no sobrevive ni una fila. Dinero correcto: **rechaza ANTES de redondear**, así que una precisión genuina de más de 4 decimales nunca se redondea en silencio; el sub-centavo que cuantiza a cero se rechaza contado en vez de abortar la corrida (hallazgo del propio adversario de GLM). Contabilidad en `mode=ro` por construcción, cero escrituras fuera de alcance. **Residual D3 del IVA CERRADO por el lead con lectura directa de Odoo** (ver D3). GLM cumplió el proceso: rojo antes del código, 17 tests, adversario propio (6 hallazgos) y 1 sola ronda de cross-review con codex] |
| 0.2 | **Ingesta de listings**: el mapa SKU ↔ plataforma ↔ identificador externo, a `listing`. Sin él, el costo (por SKU de Odoo) no se une a lo que Amazon anuncia (por ASIN/seller SKU). Fuente a determinar EN LA TAREA con evidencia (contabilidad, API de Amazon, o ambas) y declarada. **El precio es OPCIONAL**: el CHECK `listing_precio_con_moneda` exige `(listing_price IS NULL) = (price_currency IS NULL)`, o sea ambos o ninguno. El producto de esta tarea es el MAPA; un listing sin precio se escribe igual. `[tdd:required]` | Rojo antes del código. Fuente elegida declarada con su SELECT/readback. Tests: un SKU en dos plataformas → dos filas; listing sin precio se escribe (ambos NULL) y no se descarta; precio presente sin moneda → rechazado por el CHECK. Corrida real: conteo por plataforma y % de SKU con costo que quedan mapeados | 0.1 | cc:TODO |
| 0.3 | **Habilitar la lectura de product ads** — es un cambio de SUPERFICIE DE SEGURIDAD, no una ingesta más: `/sp/productAds/list` **no está** en `LIST_REQUEST_TYPES`, que es un allowlist congelado (`MappingProxyType`) leído en vivo por el guard de POST; hoy `list_objects` rechaza ese path. Ampliarlo sigue el MISMO ritual que pagó `negativeKeywords`: evidencia regla 8 EN VIVO del vendor Content-Type exacto en AMBOS perfiles, con el log en `out/`, ANTES de tocar el allowlist. `[tdd:required]` | Log de la corrida real que prueba el vendor type correcto y el 200 en US y MX (o el fallo declarado). Allowlist ampliado con SOLO ese path. Tests del guard: el path nuevo pasa; un path fuera del allowlist sigue reventando; el conteo de `LIST_REQUEST_TYPES` en los tests se actualiza a propósito, no por accidente | - | cc:TODO |
| 0.4 | **Vínculo anuncio→producto**: poblar `ad_entity.listing_id` desde los product ads. **Dos decisiones de esquema que la tarea resuelve ANTES de escribir**: (a) `ad_entity_kind` NO tiene `product_ad` — se decide entre extender el enum por migración o no materializar el product ad como entidad y resolver el vínculo en el ad group; (b) **cardinalidad**: un ad group puede anunciar N ASIN y `ad_entity.listing_id` es UNO solo — hay que definir qué se escribe cuando N>1 (propuesta del lead: **NO escribir** y contar el ad group como "multi-ASIN, margen no atribuible", nunca elegir uno arbitrario). Ambas decisiones se documentan con su razón antes de implementar. `[tdd:required]` | Las dos decisiones escritas y justificadas. Rojo antes del código. Candado de arquitectura: el pipeline no importa `write.py`. Tests: ad group con 1 ASIN → `listing_id` resuelto; con 0 y con N → `listing_id` NULL y contado en su categoría. Corrida real read-only con el `SELECT` de cobertura: % de ad groups con vínculo resuelto, y los no resueltos clasificados por motivo (multi-ASIN / sin listing / sin costo), jamás silenciados | 0.2, 0.3 | cc:TODO |
| 0.5 | **Ingesta de tipos de cambio** a `fx_rate`. Obligatoria (sellado 2). Fuente localizada por el lead: `currency_rates` en la SQLite de contabilidad (210 filas: `rate_date`, `base_currency`, `quote_currency`, `rate`). `fx_resolve` **NO se toca** (sellado 3): esta tarea solo llena la tabla de la que esa función lee. Fuente y cadencia declaradas. `[tdd:required]` | Rojo antes del código. Tests: con la tabla poblada, `fx_resolve` devuelve `exact` el día que existe y `nearest_prior` dentro de los 7 días; **más de 7 días sin tasa → cero filas**, y el consumidor lo trata como dato faltante (sellado 1), no como 1.0 ni como constante. Corrida real: rango de fechas cubierto y lista de huecos > 3 días | - | cc:TODO |
| 0.6 | **Ingesta del ledger, y NO solo ventas** (sellado 4). Fuente localizada por el lead: `ledger_events` en la SQLite de contabilidad (13,127 filas, con `platform`, `order_id`, `event_type`, `fee_category`, `sku`, `quantity`, `amount`). además de `kind='sale'`, las clases de cargo que `v_margen_plataforma` resta (`fee`, `refund`, `withholding`). Sin ellas el margen sale sistemáticamente alto. **Semántica append-only contra el esquema real**: `ledger_event` tiene tres índices únicos de deduplicación (`ledger_dedupe_source` por `source_event_id`; `ledger_dedupe_sin_orden` y `ledger_dedupe_con_orden` por clave natural, `NULLS NOT DISTINCT`) — re-ingerir el mismo hecho es **no-op**, no una segunda observación. El ISR **no trae `order_id`** y llega en bultos quincenales: se prorratea explícitamente o se excluye POR ESCRITO, con la decisión documentada. `[tdd:required]` | Rojo antes del código. Tests: re-ingerir el mismo evento no inserta y no revienta (`ON CONFLICT DO NOTHING` verificado, no asumido); cada clase de cargo llega a su índice de dedupe correcto; evento sin `order_id` sigue el camino declarado y queda contado. Corrida real con conteo **por `kind`** y ventana; la evidencia declara explícitamente qué clases de cargo entraron y cuáles no | 0.1 | cc:TODO |
| 0.7 | **Candado de cobertura**: qué fracción del GASTO PUBLICITARIO real corresponde a anuncios con vínculo resuelto (0.4), costo conocido (0.1) y FX disponible (0.5). Ponderada por gasto, no por conteo de SKU. Umbral mínimo: **lo propone el lead con el número medido a la vista y lo aprueba el dueño** — no se inventa aquí (regla 3). `[tdd:required]` | Cobertura publicada por plataforma con su `SELECT` en la evidencia, desglosando el gasto NO cubierto por motivo (multi-ASIN, sin listing, sin costo, sin FX). Decisión del dueño con su texto literal. **Si no alcanza, la Fase 1 queda `blocked` con el motivo; no se arranca "con lo que hay"** | 0.4, 0.5, 0.6 | cc:TODO |

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
2. **La ingesta es MANUAL: no hay cron.** Los costos se quedan viejos desde
   hoy. La cadencia se decide ANTES de la Fase 1 (la vista de margen leeria
   costos desactualizados sin avisar), por el mismo runbook del snapshot que
   dejo la 0.1. Es decision del dueno: cada cuanto, y si el snapshot se
   automatiza en el host.

## Fase 1 — margen medible y honesto (todavía NO decide nada)

`[lane:gate]` — produce lectura y alertas. Cero escrituras a Amazon.

| Task | Contenido | DoD | Depends | Status |
|---|---|---|---|---|
| 1.1 | **Diseño de la vista de margen POR ENTIDAD PUBLICITARIA.** `v_margen_plataforma` no sirve para esto y no se fuerza: agrupa solo por `(platform, amount_currency)`, sin `listing_id` y sin fecha (así a propósito). Es una vista NUEVA, con la MISMA resolución de costo y FX (sellado 7). Debe definir: cómo se atribuye una venta del ledger a la entidad publicitaria (por `listing_id`, con su supuesto declarado), qué ventana y qué vintage usa, y cómo entra el rango de halo — que viene del lado de ads (`ad_revenue` vs `revenue_same_sku`), no del ledger. `[tdd:skip:diseno]` | Diseño escrito y revisado por el lead ANTES de implementar: fuente de cada columna, supuesto de atribución declarado, y por qué no se puede reusar la vista de plataforma | 0.7 | cc:TODO |
| 1.2 | **Implementación de la vista** del diseño 1.1. Cada fila sale con su moneda, su edad de dato declarada, **dos números de margen (con halo y sin halo)** y **qué clases de cargo entraron** (sellado 4). Un solo número está PROHIBIDO. Dato faltante = fila no escrita. `[tdd:required]` | Rojo antes del código. Tests: fila sin costo → ausente, no cero; fila US con FX `nearest_prior` → presente pero MARCADA, y sin tasa utilizable → ausente; el par con-halo/sin-halo siempre presente o la fila no sale; consulta sin vintage falla. Corrida real con el `SELECT` y el rango citado en ambas plataformas | 1.1 | cc:TODO |
| 1.3 | **Digest diario por Telegram** (lo pide la Fase 3). Reusa `app/notifica.py` (fail-silent con NOTA en `notes`, ya sellado en 3.3 y 1.4): qué decidió el motor, cuánto se aplicó contra qué tope, y el margen del día **como rango**. Sin canal, el ciclo JAMÁS se degrada. `[tdd:required]` | Rojo antes del código. Tests: canal caído no tumba el ciclo y deja NOTA; el digest declara el modo del ciclo (live/shadow); el margen aparece como rango o no aparece. Envío real verificado una vez | 1.2 | cc:TODO |
| 1.4 | **Vista de lectura del margen en el dashboard** (server-rendered, sin JS: la CSP es `default-src 'self'`). Margen por campaña con su rango, su moneda, su edad de dato y su marca de FX aproximado. Valor nulo se ve como `—` CON etiqueta, jamás como `0` (mismo criterio que la quota de 1.5). Sin endpoints de escritura nuevos. `[tdd:required]` | Rojo antes del código. Tests de render: el rango se ve como rango; ausencia de dato NO se renderiza como cero; una plataforma sin margen no rompe la pantalla. `test_architecture` verde, cero escritura | 1.2 | cc:TODO |

## Fase 2 — margin-aware targets (la única que decide; nace en shadow)

`[lane:release]` — no arranca sin Fases 0 y 1 cerradas Y ≥1 semana post-flip.

| Task | Contenido | DoD | Depends | Status |
|---|---|---|---|---|
| 2.1 | **Diseño del target margin-aware**, con el dueño: cómo se convierte un margen-en-rango en un target de ACoS por campaña, y qué hace el motor cuando los dos extremos dan decisiones OPUESTAS (el ±1,671/−2,238 USD garantiza que va a ocurrir). Propuesta por defecto del lead: **abstenerse** — máximo comportamiento de una señal que no discrimina. `[tdd:skip:decision-dueno]` | La regla escrita en `docs/CONTEXTO.md` con el texto literal de la decisión del dueño, incluyendo qué pasa ante rangos que se contradicen | 1.2, ORBIT 05 en live ≥1 semana | cc:TODO |
| 2.2 | **Implementación en shadow y prueba de que sirve.** Decide en paralelo al target actual sin aplicarse, y se mide si AUMENTA la tasa de acción útil. Si no lo demuestra, **no entra**: se declara y se retira. `[tdd:required]` | Rojo antes del código. Golden replay: las decisiones existentes NO cambian mientras la señal esté en shadow. Comparación medida entre el target actual y el margin-aware sobre las mismas entradas, con el `SELECT` en la evidencia. Decisión de entrar/retirar firmada por el dueño | 2.1 | cc:TODO |

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
