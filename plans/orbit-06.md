# ORBIT 06 — consolidación post-cutover: el margen deja de ser una promesa

> **Propósito**: la Fase 3 del diseño v2 (`docs/CONTEXTO.md` §Fases) pide
> "digest diario por Telegram, vista de lectura, **margin-aware targets**".
> El lead verificó el 2026-08-30 que **margin-aware hoy no tiene piso**: las
> tablas existen y están TODAS vacías, no hay ingesta que las llene, y el
> vínculo anuncio→producto está en 0 de 5,899 filas. Este plan construye ese
> piso ANTES de tocar una sola línea de lógica de decisión.
>
> Precedencia: `docs/CONTEXTO.md` (reglas 1-10 y trampas del dominio) >
> `docs/ADS_OPTIMIZER_V2_DESIGN.md` (umbrales) > este plan. Un PR por tarea.
> **Aquí NO se cambian umbrales del motor** ni se toca la escalera: ORBIT 06
> añade dato y lectura; la Fase 2 de este plan es la única que decide, y
> nace en shadow.
>
> **Reloj**: la Fase 0 **NO depende del flip** y puede empezar hoy. Las
> Fases 1 y 2 sí: necesitan decisiones live reales para significar algo.
> Si la Fase 0 espera al flip, retrasa ORBIT 06 completa.
>
> **Reparto** (evidencia de delegación, `CLAUDE.md` global): GLM implementa
> las ingestas y la vista de margen (lógica de dominio, dinero); DeepSeek la
> superficie de lectura del dashboard (server-rendered, sin ssh/push/tracker);
> el lead escribe este plan, revisa cada entrega contra la base viva y cierra.
> Cross-review: 1 ronda por tarea; 2ª SOLO si la 1ª halló severidad alta;
> jamás 3ª.

## Estado verificado que origina este plan (regla 8, 2026-08-30)

| Pieza | Estado medido |
|---|---|
| `sku_costs` en contabilidad (SQLite) | **2,708 filas, 0 nulos, 0 ceros**, 1,087 SKU vigentes, `source` = odoo_sync (2,701) + manual (7) |
| Moneda de esos costos | **100% MXN**, sin excepción |
| `product` / `listing` / `sku_cost` en Orbit | 0 filas (esquema listo, sin ingesta) |
| `ledger_event` / `fx_rate` en Orbit | 0 filas |
| `v_margen_plataforma` | 0 filas (depende de `ledger_event` ⋈ `sku_cost`) |
| `ad_entity.listing_id` | **0 de 5,899** — el vínculo anuncio→producto no existe |
| Ingestas del CLI | solo `structure` y `metrics` |
| Tipos de entidad ingestados | campaign, ad_group, keyword, product_target — **ningún `product_ad`** |

**Consecuencia**: la pregunta "¿esta keyword vende un producto de 40% o de 8%
de margen?" hoy no tiene forma de responderse. Ese es el trabajo de la Fase 0.

## Decisiones SELLADAS (el header manda sobre las tareas)

1. **Sin costo conocido no hay decisión margin-aware.** Regla 3 de CONTEXTO
   aplicada al margen: dato faltante = fila NO escrita, **jamás un cero**.
   La trampa está documentada y pagada: `sales_history.cogs` de MeLi tenía
   49% sin costo y *el cero se disfrazaba de dato*. Los 2,708 costos de Odoo
   hoy están limpios, pero la ingesta debe rechazar el cero como valor, no
   normalizarlo.
2. **Todo costo lleva su moneda, y aquí SIEMPRE hace falta convertir.** Los
   costos son 100% MXN; la cuenta US vende en USD. El FX no es un extra: sin
   `fx_rate` poblado, el margen de US es indefinido. Precedente pagado:
   `sales_history` reportaba MXN hasta para amazon_us → error de 18.66×
   **siempre a favor de "todo es rentabilísimo"**. Un margen US sin FX
   explícito es un número que miente en la dirección más peligrosa.
3. **El halo se reporta como RANGO, nunca como un número.** Decisión literal
   del dueño (2026-08-28, preflight 1.6c): "acotar con ambos supuestos". El
   56-58.5% del ingreso atribuido es de OTROS SKUs, y la cuenta US da entre
   **+1,671 y −2,238 USD en 91 días** según se cuente o no — *ni el signo se
   conoce*. Toda salida de margen de esta fase declara los dos extremos.
   ORBIT 05 sigue optimizando ACoS con revenue completo: CONTEXTO manda.
4. **Vintage explícito o no se consulta.** Tres relojes desalineados: la
   venta atribuida madura a 5-8 días, el costo al día 15 (y madura *hacia
   abajo* por clawback), los fees a 15-30 días. Las tablas point-in-time sin
   filtro de vintage inflan 13-17× e **invierten el signo de las
   tendencias**. Todo margen sale con su edad declarada.
5. **La Fase 2 nace en shadow.** Ningún target margin-aware toca una decisión
   real sin haber demostrado en shadow que mejora la tasa de acción útil.
   Es la regla de la Fase 4 del diseño v2 aplicada por adelantado.
6. **MeLi queda fuera.** Es estructuralmente incomparable con Amazon (cero
   halo, lag de 1 día, ISR con order_id, escritura de ads bloqueada a nivel
   cuenta). Mezclarlo en la misma vista de margen produce comparaciones
   falsas. Cuando entre, entra con su propio plan.

## Fase 0 — el dato (NO depende del flip; empieza hoy)

`[lane:gate]` — cada tarea aquí es un candado de datos: si la cobertura no
alcanza, la Fase 1 no arranca.

| Task | Contenido | DoD | Depends | Status |
|---|---|---|---|---|
| 0.1 | **Ingesta de productos y costos** desde contabilidad a `product` + `sku_cost`. Fuente: `sku_costs` de la SQLite de contabilidad (2,708 filas, `sku`/`cost`/`currency`/`valid_from`/`valid_to`/`source`). Mapeo: `sku` → `product.odoo_sku`; la vigencia bitemporal (`valid_from`/`valid_to`) se PRESERVA tal cual, no se colapsa. `cost = 0` o NULL → fila NO escrita + conteo declarado (sellado 1). `includes_tax` se resuelve leyendo qué produce Odoo, no se asume. Nueva subcomando `ingest costs` en el CLI, mismo patrón que `structure`/`metrics` (`ingest_run` con su `source`). `[tdd:required]` | Rojo antes del código. Tests: costo cero → fila rechazada y contada, jamás escrita; dos vigencias del mismo SKU → dos filas, la vieja con `valid_to`; re-correr la ingesta es idempotente (mismo `ingest_run` no duplica). Corrida real: `SELECT` con conteo de `product` y `sku_cost` y la lista de SKU rechazados con su motivo | - | cc:TODO |
| 0.2 | **Ingesta de listings**: el mapa SKU ↔ plataforma ↔ identificador externo, a `listing` (`product_id`, `platform`, `external_id`, `seller_sku`, `listing_price`, `price_currency`). Sin este mapa el costo (por SKU de Odoo) no se puede unir a lo que Amazon anuncia (por ASIN/seller SKU). Fuente a determinar EN LA TAREA con evidencia: contabilidad, la API de Amazon, o ambas — se declara cuál y por qué. `[tdd:required]` | Rojo antes del código. Fuente elegida declarada con su SELECT/readback. Tests: un SKU en dos plataformas → dos filas; `price_currency` obligatorio (regla 4: todo dinero con moneda). Corrida real con conteo por plataforma y % de SKU con costo que quedan mapeados | 0.1 | cc:TODO |
| 0.3 | **Ingesta de `product_ad` de Amazon** y poblado de `ad_entity.listing_id`. Es el vínculo que hoy está en 0/5,899. Por el cliente de LECTURA ya sellado (`AdsClient`, POST `/sp/productAds/list` con paginación completa, mismo patrón que `snapshot_listas.py`); **jamás** por `app/ads/write.py`. El ad group pasa a saber qué ASIN anuncia, y de ahí sale el `listing_id`. `[tdd:required]` | Rojo antes del código. Candado de arquitectura: el pipeline no importa `write.py`. Tests de las partes puras (agrupado ad_group→ASIN, resolución a `listing_id`, ad group con 0 y con N product ads). Corrida real read-only: conteo de product ads por plataforma y **`SELECT` del % de `ad_entity` de tipo ad_group con `listing_id` resuelto**; los no resueltos se listan con motivo, no se silencian | 0.2 | cc:TODO |
| 0.4 | **Ingesta de tipos de cambio** a `fx_rate` (`rate_date`, `base_currency`, `quote_currency`, `rate`). Obligatoria, no opcional: los costos son 100% MXN y US vende en USD (sellado 2). `fx_resolve` ya existe en la base y es la única fuente de conversión — no se escribe una segunda. Fuente y cadencia declaradas; el hueco de un día es hueco (NULL), jamás el rate del día anterior sin decirlo. `[tdd:required]` | Rojo antes del código. Tests: día sin rate → `fx_resolve` devuelve NULL y el consumidor lo trata como dato faltante, no como 1.0 ni como el último conocido; conversión ida y vuelta consistente. Corrida real: rango de fechas cubierto y lista de días faltantes | - | cc:TODO |
| 0.5 | **Ingesta del ledger de ventas** a `ledger_event` (`kind='sale'`), que es de lo que come `v_margen_plataforma`. Bitemporal: `event_date` (cuándo pasó) y `observed_at` (cuándo lo supimos) — la venta atribuida madura a 5-8 días y el costo *hacia abajo* al día 15 (sellado 4). Los costos de ISR **no traen `order_id`** y llegan en bultos quincenales: se prorratean explícitamente o se excluyen POR ESCRITO, nunca se reparten en silencio. `[tdd:required]` | Rojo antes del código. Tests: la misma venta observada dos veces produce dos observaciones, no una sobrescritura; una consulta sin filtro de vintage falla el test (regla point-in-time); evento sin `order_id` sigue el camino declarado (prorrateo o exclusión) y queda contado. Corrida real con conteo por plataforma y ventana | 0.1 | cc:TODO |
| 0.6 | **Candado de cobertura**: medir qué fracción del GASTO PUBLICITARIO real corresponde a anuncios cuyo producto tiene costo conocido y FX disponible. Es el número que decide si la Fase 1 puede arrancar. Se publica como métrica, no como opinión. Umbral mínimo: **lo propone el lead con el número medido a la vista y lo aprueba el dueño** — no se inventa aquí (regla 3). `[tdd:required]` | Cobertura calculada y publicada por plataforma, ponderada por gasto (no por conteo de SKU: 100 SKUs sin gasto no valen lo que 1 con el 30%). El `SELECT` en la evidencia. Decisión del dueño registrada con su texto literal. **Si la cobertura no alcanza el umbral, la Fase 1 queda `blocked` con el motivo, no se arranca "con lo que hay"** | 0.3, 0.4, 0.5 | cc:TODO |

## Fase 1 — margen medible y honesto (todavía NO decide nada)

`[lane:gate]` — produce lectura y alertas. Cero escrituras a Amazon.

| Task | Contenido | DoD | Depends | Status |
|---|---|---|---|---|
| 1.1 | **Margen por entidad publicitaria, con vintage y con RANGO de halo.** Vista que une gasto (`ads_metric_observation` colapsado por `v_metric_latest`) con margen (`v_margen_plataforma`) por el `listing_id` de 0.3. Cada fila sale con: su moneda, su edad de dato declarada, y **dos números de margen — con halo y sin halo** (sellado 3). Un solo número está PROHIBIDO. Dato faltante = fila no escrita (sellado 1). `[tdd:required]` | Rojo antes del código. Tests: fila sin costo → ausente, no cero; fila US sin FX del día → ausente, no convertida a 1.0; el par con-halo/sin-halo siempre presente o la fila no sale; consulta sin vintage falla. Corrida real con el `SELECT` y el rango citado para ambas plataformas | 0.6 | cc:TODO |
| 1.2 | **Digest diario por Telegram** (lo pide la Fase 3 del diseño v2). Reusa `app/notifica.py` (fail-silent con NOTA en `notes`, ya sellado en 3.3 y 1.4): qué decidió el motor, cuánto se aplicó contra qué tope, y el margen del día **como rango**. Sin canal, el ciclo JAMÁS se degrada. `[tdd:required]` | Rojo antes del código. Tests: canal caído no tumba el ciclo y deja NOTA; el digest declara el modo del ciclo (live/shadow); el margen aparece como rango o no aparece. Envío real verificado una vez | 1.1 | cc:TODO |
| 1.3 | **Vista de lectura del margen en el dashboard** (server-rendered, sin JS: la CSP es `default-src 'self'`). Margen por campaña con su rango, su moneda y su edad de dato. Cap/valor nulo se ve como `—` CON etiqueta, jamás como `0` — mismo criterio que la quota de 1.5. Sin endpoints de escritura nuevos. `[tdd:required]` | Rojo antes del código. Tests de render: el rango se ve como rango; ausencia de dato NO se renderiza como cero; una plataforma sin margen no rompe la pantalla. `test_architecture` verde, cero escritura | 1.1 | cc:TODO |

## Fase 2 — margin-aware targets (la única que decide; nace en shadow)

`[lane:release]` — toca la lógica de decisión. No arranca sin las Fases 0 y 1
cerradas Y sin al menos una semana de datos post-flip.

| Task | Contenido | DoD | Depends | Status |
|---|---|---|---|---|
| 2.1 | **Diseño del target margin-aware**, con el dueño: cómo se convierte un margen-en-rango en un target de ACoS por campaña, y qué hace el motor cuando los dos extremos del rango dan decisiones OPUESTAS (el caso que el ±1,671/−2,238 USD garantiza que va a ocurrir). Respuesta por defecto propuesta: **abstenerse** — máximo comportamiento de una señal que no discrimina (Fase 4 del diseño v2). `[tdd:skip:decision-dueno]` | La regla escrita en `docs/CONTEXTO.md` con el texto literal de la decisión del dueño, incluyendo qué pasa ante rangos que se contradicen | 1.1, ORBIT 05 en live ≥1 semana | cc:TODO |
| 2.2 | **Implementación en shadow y prueba de que sirve.** El target margin-aware decide en paralelo al actual sin aplicarse, y se mide si AUMENTA la tasa de acción útil. Si no lo demuestra, **no entra**: se declara y se retira. `[tdd:required]` | Rojo antes del código. Golden replay: las decisiones existentes NO cambian mientras la señal esté en shadow. Comparación medida entre el target actual y el margin-aware sobre las mismas entradas, con el `SELECT` en la evidencia. Decisión de entrar/retirar firmada por el dueño | 2.1 | cc:TODO |

## Reject (con razón)

- **Un solo número de margen por campaña.** Viola el sellado 3 y la decisión
  literal del dueño ("acotar con ambos supuestos"). Con el halo en 56-58.5%,
  un número único no es una simplificación: es elegir en secreto un supuesto
  cuyo signo nadie conoce.
- **Rellenar el costo faltante con cero, con el promedio o con el último
  conocido.** Es exactamente la trampa que ya se pagó en el sistema viejo
  (`sales_history.cogs`, 49% sin costo). Dato faltante = fila no escrita.
- **Usar el rate de FX del día anterior cuando falta el del día**, sin
  declararlo. Un margen US mal convertido miente 18.66× a favor de "todo es
  rentabilísimo" — el error más peligroso posible aquí.
- **Incluir MeLi en la misma vista de margen** (sellado 6).
- **Adelantar la Fase 2 antes del flip.** Un target margin-aware validado
  contra decisiones que nunca se aplicaron no demuestra nada.
- **Un dashboard "de márgenes" bonito antes de la Fase 0.** Pintar un número
  que no tiene dato detrás es peor que no pintarlo.

## Pre-aprobaciones del plan (se piden UNA vez, aquí)

Cada una se ejecuta solo dentro del alcance declarado; nada fuera de esta
lista procede sin un go nuevo en el momento.

- **Lectura de la base de contabilidad** — `sqlite3` sobre el archivo de
  contabilidad en el server, solo `SELECT` sobre `sku_costs` y las tablas de
  producto/listing que 0.2 identifique. Razón: es la única fuente de costos
  (2,708 filas) y sin ella la Fase 0 no existe. Alcance: Fase 0, tareas
  0.1 y 0.2. **Lectura pura, cero escrituras a ese sistema.**
- **Lectura de la API de Amazon Ads** — POST `/sp/productAds/list` y los
  `/list` que 0.2 necesite, por el cliente de lectura sellado. Razón: es la
  única forma de saber qué ASIN anuncia cada ad group (hoy 0/5,899).
  Alcance: Fase 0, tarea 0.3. **Read-only: ni un PUT, ni un POST de
  creación.**
- **Escritura en la base de Orbit** — `INSERT` en `product`, `sku_cost`,
  `listing`, `fx_rate`, `ledger_event` y `UPDATE` de `ad_entity.listing_id`,
  con el rol de ingesta. Razón: es el producto de la Fase 0. Alcance: Fase 0
  completa. **No toca `decision`, `apply_queue` ni `ads_optimizer_goal`.**

**Fuera de estas pre-aprobaciones y por lo tanto con go explícito en el
momento**: cualquier mutación en Amazon, cualquier cambio de umbral del
motor, cualquier `INSERT`/`UPDATE` sobre las tablas de decisión, y el paso de
la Fase 2 a live.

## Verificación del plan (contrato de calidad)

- `team_validation_mode`: **manual-pass** — el lead evaluó las cinco
  perspectivas por separado sin spawnear subagentes (el dueño pidió no
  usarlos salvo que los pida). Producto: la fase entrega lectura antes que
  decisión, que es lo que el dueño necesita para confiar. Arquitectura: cero
  fuentes nuevas de margen (reusa `v_margen_plataforma` y `fx_resolve`), cero
  duplicación de la lógica de conversión. Seguridad: solo lectura sobre
  contabilidad y sobre Amazon; ninguna tarea pide leer secretos, y la
  escritura se limita a tablas de ingesta. QA: cada tarea de código lleva
  rojo antes del verde y un test que discrimina el fallo real, no el verde
  fácil. Escéptico: el riesgo mayor NO es el código sino la **cobertura del
  dato** — por eso 0.6 es un candado con poder de bloquear la fase, y por eso
  el umbral lo fija el dueño con el número a la vista.
- **`Spec skip reason`**: no se abre delta de producto. El contrato ya está
  sellado en `docs/CONTEXTO.md` — reglas 2, 3 y 4 (una sola fuente de margen,
  dato faltante = fila no escrita, todo dinero con moneda), las trampas del
  dominio (halo, tres relojes, point-in-time, monedas, ISR sin `order_id`) y
  la definición de la Fase 3. Este plan es el ledger de tareas para llegar
  ahí y **no introduce comportamiento de producto nuevo**. La única decisión
  de producto pendiente —qué hace el motor ante un rango de halo que se
  contradice— se toma en 2.1 y ESA sí se escribe en CONTEXTO.
- **Baseline de lint/formato**: ya existe (`.pre-commit-config.yaml` con ruff
  check + ruff format + candados anti-monolito, y la batería completa en CI).
  No hace falta tarea de setup previa.
- **Datos no observados**: el % de SKUs anunciados que tienen costo NO se
  conoce todavía — no se puede medir hasta que 0.3 exista. Se declara como
  `unknown`, no como "alto" ni como "suficiente". Es justamente lo que 0.6
  convierte en número.
