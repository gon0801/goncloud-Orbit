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
| 0.1 | **Ingesta de productos y costos** a `product` + `sku_cost`. Fuente: `sku_costs` de la SQLite de contabilidad. Mapeo explícito de nombres: `sku` → `product.odoo_sku`, `cost` → `cost_amount`, `currency` → `cost_currency`. La vigencia bitemporal se PRESERVA. **Semántica de re-corrida contra el esquema real**: `sku_cost` tiene un `EXCLUDE` (btree_gist) que impide dos vigencias solapadas del mismo producto, y el trigger `sku_cost_solo_cierra_vigencia` permite **únicamente** cerrar `valid_to` — no hay UPDATE de importe ni DELETE. Por lo tanto: vigencia nueva ⇒ cierra la anterior con `valid_to` e inserta; vigencia idéntica ya presente ⇒ **no-op**, jamás error ni fila duplicada. `cost` 0 o NULL ⇒ fila no escrita y contada (sellado 1). `includes_tax` se resuelve leyendo qué produce Odoo, no se asume. Subcomando `ingest costs`. `[tdd:required]` | Rojo antes del código. Tests: costo cero rechazado y contado; vigencia nueva cierra la anterior (una sola vigente por producto, el EXCLUDE lo prueba); **re-correr la ingesta completa dos veces deja la base idéntica** (no-op real, no "mismo ingest_run"); intento de modificar un importe existente → rechazado por el trigger. Corrida real con conteos y lista de SKU rechazados con motivo | - | cc:TODO |
| 0.2 | **Ingesta de listings**: el mapa SKU ↔ plataforma ↔ identificador externo, a `listing`. Sin él, el costo (por SKU de Odoo) no se une a lo que Amazon anuncia (por ASIN/seller SKU). Fuente a determinar EN LA TAREA con evidencia (contabilidad, API de Amazon, o ambas) y declarada. **El precio es OPCIONAL**: el CHECK `listing_precio_con_moneda` exige `(listing_price IS NULL) = (price_currency IS NULL)`, o sea ambos o ninguno. El producto de esta tarea es el MAPA; un listing sin precio se escribe igual. `[tdd:required]` | Rojo antes del código. Fuente elegida declarada con su SELECT/readback. Tests: un SKU en dos plataformas → dos filas; listing sin precio se escribe (ambos NULL) y no se descarta; precio presente sin moneda → rechazado por el CHECK. Corrida real: conteo por plataforma y % de SKU con costo que quedan mapeados | 0.1 | cc:TODO |
| 0.3 | **Habilitar la lectura de product ads** — es un cambio de SUPERFICIE DE SEGURIDAD, no una ingesta más: `/sp/productAds/list` **no está** en `LIST_REQUEST_TYPES`, que es un allowlist congelado (`MappingProxyType`) leído en vivo por el guard de POST; hoy `list_objects` rechaza ese path. Ampliarlo sigue el MISMO ritual que pagó `negativeKeywords`: evidencia regla 8 EN VIVO del vendor Content-Type exacto en AMBOS perfiles, con el log en `out/`, ANTES de tocar el allowlist. `[tdd:required]` | Log de la corrida real que prueba el vendor type correcto y el 200 en US y MX (o el fallo declarado). Allowlist ampliado con SOLO ese path. Tests del guard: el path nuevo pasa; un path fuera del allowlist sigue reventando; el conteo de `LIST_REQUEST_TYPES` en los tests se actualiza a propósito, no por accidente | - | cc:TODO |
| 0.4 | **Vínculo anuncio→producto**: poblar `ad_entity.listing_id` desde los product ads. **Dos decisiones de esquema que la tarea resuelve ANTES de escribir**: (a) `ad_entity_kind` NO tiene `product_ad` — se decide entre extender el enum por migración o no materializar el product ad como entidad y resolver el vínculo en el ad group; (b) **cardinalidad**: un ad group puede anunciar N ASIN y `ad_entity.listing_id` es UNO solo — hay que definir qué se escribe cuando N>1 (propuesta del lead: **NO escribir** y contar el ad group como "multi-ASIN, margen no atribuible", nunca elegir uno arbitrario). Ambas decisiones se documentan con su razón antes de implementar. `[tdd:required]` | Las dos decisiones escritas y justificadas. Rojo antes del código. Candado de arquitectura: el pipeline no importa `write.py`. Tests: ad group con 1 ASIN → `listing_id` resuelto; con 0 y con N → `listing_id` NULL y contado en su categoría. Corrida real read-only con el `SELECT` de cobertura: % de ad groups con vínculo resuelto, y los no resueltos clasificados por motivo (multi-ASIN / sin listing / sin costo), jamás silenciados | 0.2, 0.3 | cc:TODO |
| 0.5 | **Ingesta de tipos de cambio** a `fx_rate`. Obligatoria (sellado 2). `fx_resolve` **NO se toca** (sellado 3): esta tarea solo llena la tabla de la que esa función lee. Fuente y cadencia declaradas. `[tdd:required]` | Rojo antes del código. Tests: con la tabla poblada, `fx_resolve` devuelve `exact` el día que existe y `nearest_prior` dentro de los 7 días; **más de 7 días sin tasa → cero filas**, y el consumidor lo trata como dato faltante (sellado 1), no como 1.0 ni como constante. Corrida real: rango de fechas cubierto y lista de huecos > 3 días | - | cc:TODO |
| 0.6 | **Ingesta del ledger, y NO solo ventas** (sellado 4): además de `kind='sale'`, las clases de cargo que `v_margen_plataforma` resta (`fee`, `refund`, `withholding`). Sin ellas el margen sale sistemáticamente alto. **Semántica append-only contra el esquema real**: `ledger_event` tiene tres índices únicos de deduplicación (`ledger_dedupe_source` por `source_event_id`; `ledger_dedupe_sin_orden` y `ledger_dedupe_con_orden` por clave natural, `NULLS NOT DISTINCT`) — re-ingerir el mismo hecho es **no-op**, no una segunda observación. El ISR **no trae `order_id`** y llega en bultos quincenales: se prorratea explícitamente o se excluye POR ESCRITO, con la decisión documentada. `[tdd:required]` | Rojo antes del código. Tests: re-ingerir el mismo evento no inserta y no revienta (`ON CONFLICT DO NOTHING` verificado, no asumido); cada clase de cargo llega a su índice de dedupe correcto; evento sin `order_id` sigue el camino declarado y queda contado. Corrida real con conteo **por `kind`** y ventana; la evidencia declara explícitamente qué clases de cargo entraron y cuáles no | 0.1 | cc:TODO |
| 0.7 | **Candado de cobertura**: qué fracción del GASTO PUBLICITARIO real corresponde a anuncios con vínculo resuelto (0.4), costo conocido (0.1) y FX disponible (0.5). Ponderada por gasto, no por conteo de SKU. Umbral mínimo: **lo propone el lead con el número medido a la vista y lo aprueba el dueño** — no se inventa aquí (regla 3). `[tdd:required]` | Cobertura publicada por plataforma con su `SELECT` en la evidencia, desglosando el gasto NO cubierto por motivo (multi-ASIN, sin listing, sin costo, sin FX). Decisión del dueño con su texto literal. **Si no alcanza, la Fase 1 queda `blocked` con el motivo; no se arranca "con lo que hay"** | 0.4, 0.5, 0.6 | cc:TODO |

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
  solo si la decisión documentada lo justifica.

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

## Verificación del plan (contrato de calidad)

- `team_validation_mode`: **subagent** — cross-review externa simultánea con
  codex y grok sobre la v1 (1 ronda, el tope del kit), más la evaluación del
  lead de las cinco perspectivas. Producto: la fase entrega lectura antes que
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
