# Vista de margen por entidad publicitaria (ORBIT 06 · 1.1)

> **Estado.** Propuesta de diseño. El lead sella antes de la 1.2.
> **Fuente de hechos.** Obstáculos de la 1.1 en `plans/orbit-06.md`
> (medidos en vivo 2026-08-31, solo lectura). No se re-abren.
> **Consumidor.** Lectura (digest 1.3, dashboard 1.4). Cero escritura a
> Amazon. La Fase 2 (margin-aware) nace después y en shadow.

## Por qué no se reusa `v_margen_plataforma`

La vista de plataforma agrupa solo por `(platform, amount_currency)`. Sin
`listing_id`, sin dimensión temporal, sin halo. Así a propósito (COMMENT
en `0001_initial.sql`). El margen por keyword/target necesita grano de
entidad, ventana, vintage y el par con-halo/sin-halo. Sellado 7: vista
**nueva**, misma resolución de costo (`sku_cost` vigente) y FX
(`fx_resolve`). Cero segunda forma de resolver costo o tasa.

## Forma del dominio (data shape)

Una fila = una entidad de decisión con margen medible en una ventana.

```
MargenEntidad
  identity:   (platform, ad_entity_id)   # kind IN (keyword, product_target)
  ventana:    [metric_date_from, metric_date_to]  # solo dias maduros
  vintage:    corte D-15 UTC via v_metric_mature
  dinero:     currency_reporte            # MXN en mx; USD en us
  ingreso:    ad_revenue_sum, revenue_same_sku_sum
  gasto_ads:  cost_sum                    # metricas, mismo grano
  cogs:       cogs_sin_halo, cogs_con_halo
  margen:     margen_sin_halo, margen_con_halo   # el par o la fila no sale
  fx:         fx_source                   # exact | nearest_prior | (ausente)
  cargos:     clases_declaradas           # que entro / que no (sellado 4)
  cobertura:  flags de hueco (sin catalogo, sin costo, sin precio, sin fx)
```

Organización. Tabla/CTE SQL (no máquina de estados). El grano de gasto es
el de `v_tacos` 0005 y del motor: `kind IN ('keyword','product_target')`.
El catálogo de productos del grupo llega por
`ad_entity(keyword) → parent ad_group → hijos product_ad → listing → product
→ sku_cost`.

## Hechos ya sellados por medición (no re-abrir)

1. Multi-home total. Todo producto vendido (90d) vive en >5 grupos (máx 18
   MX / 46 US). Atribuir venta del ledger al grupo la contaría N veces.
   **Ingreso por entidad = métricas de ads** (`ad_revenue` /
   `revenue_same_sku`). El ledger reconcilia a nivel plataforma.
2. Halo completo en datos maduros 90d (100 % de filas con ambos campos).
   MX ~65 % halo; US ~64 %. El rango es construible directo.
3. `orders ≈ unidades` hoy (ledger 90d: MX 300=300, US 178=178). Supuesto
   medido con **test de vigencia**: si aparece venta multi-unidad, el
   supuesto caduca ruidoso (no se parchea en silencio).
5 a 7. Monedas, fail-loud de la ola, atribuibilidad ledger (84 % MX / 88 %
   US del importe con `product_id`). Ver obstaculos en el plan.

## Decisión D1 · COGS por entidad (PROPUESTA AL LEAD)

### Candidatos del obstáculo 4, contra los números

| Candidato | Qué hace | Contra los números | Sesgo |
|---|---|---|---|
| **A** promedio simple de costos del grupo | `AVG(cost)` × orders | Grupos con decenas o cientos de ASIN (peor MX 1,259). El promedio aplasta SKUs que no se venden. | Direccion desconocida. Barato de calcular, caro de creer. |
| **B** promedio ponderado por revenue del ledger | pesos = venta ledger de cada producto del grupo en la ventana; `Σ w_i·cost_i` × orders | Usa señal real de qué se vende. No reparte COGS al **halo** (~65 % del ingreso). Con B puro, `margen_con_halo` trataría el halo como margen casi puro. | Si ads empuja SKUs más caros que el mix orgánico, COGS bajo → margen alto (sesgo "rentabilísimo"). |
| **C** razón costo/precio × ingreso | `ratio = Σ w_i·(cost_i/price_i)`; COGS = ingreso × ratio | Escala con `ad_revenue` y con `revenue_same_sku` (las dos puntas del rango). No depende de partir orders entre halo y same-SKU. Exige `listing_price` presente. | Mismo riesgo de mezcla que B. **Más** el residual IVA en MX (vitrina con IVA, costo neto `includes_tax=false`) → ratio bajo → COGS subestimado → margen alto. |

### Elección propuesta: **C con pesos de B**

Nombre corto. **Razón costo/precio ponderada por revenue del ledger del
catálogo del ad group, aplicada a cada punta del ingreso.**

```
productos_G = product_ads vivos del ad_group padre con listing + sku_cost
              + listing_price NOT NULL
w_i         = venta_ledger(product_i, plataforma, ventana) / Σ ventas
              (si Σ=0 → peso uniforme entre productos_G con costo+precio)
ratio_G     = Σ w_i * (cost_i_en_moneda_reporte / price_i_en_moneda_reporte)
cogs_sin_halo = revenue_same_sku_sum * ratio_G
cogs_con_halo = ad_revenue_sum       * ratio_G
```

`cost_i` se resuelve como en `v_margen_plataforma`: vigencia `sku_cost` al
`metric_date` (o al día representativo de la ventana si se agrega antes;
la 1.2 elige uno y lo testa). Conversión a moneda del reporte solo con
`fx_resolve` (sellado 3). Sin tasa utilizable → fila ausente, no invento.

### Por qué no A ni B solo

- **A** miente en el caso normal (multi-ASIN dominante, medido en 0.4/0.7).
- **B solo** deja el 65 % halo sin COGS. El extremo "con halo" del rango
  se vuelve optimista justo donde el dueño pidió acotar, no celebrar.
- **C con pesos de B** reparte COGS a las dos puntas, usa el mix que el
  ledger sí observa para los productos del grupo, y reusa la resolución
  de costo/FX ya sellada.

### Sesgo declarado (obligatorio en COMMENT y en la fila)

1. La mezcla que genera el revenue del keyword ≈ mezcla ledger de los
   productos del grupo (no hay gasto por ASIN dentro del grupo).
2. El halo (otros SKUs) se costea con la **misma** razón del grupo. Es
   proxy de categoría, no atribución.
3. Residual IVA MX. Precio de listing suele incluir IVA; costo Odoo es
   neto. La razón puede subestimar COGS y **sobreestimar margen** en MX.
   Dirección = el sesgo que este plan persigue. Mitigación diferida: si el
   lead exige, la 1.2 puede exigir evidencia de precio neto o marcar
   `precio_incluye_iva` cuando exista fuente. Hoy no se inventa el 1.16.
4. Sin `listing_price` en el catálogo resoluble del grupo → **fila no
   escrita** (sellado 1), contada en cobertura. No se cae a A en silencio.

### Qué necesita el lead sellar

- Aceptar **C+B** (o mandar A / B solo / otra con sesgo escrito).
- Aceptar el residual IVA como declarado, o exigir candado adicional
  antes de 1.2.
- Confirmar que `orders ≈ unidades` queda solo como test de vigencia
  auxiliar (el camino de dinero de C no lo necesita para el COGS).

## Decisión D2 · Grano y padres

- Fila de margen: `keyword` y `product_target` (mismo grano 0005 / motor /
  cobertura 0.7).
- `campaign` y `ad_group` **no** publican margen propio aquí (el gasto de
  campaign duplica a las hijas; medido en 0005). Rollup a campaña = SUM de
  hijas en la capa de lectura (1.4), no una segunda vista con otra ley.
- `product_ad` aporta catálogo (`listing_id`), no métricas.

## Decisión D3 · Ingreso, gasto, cargos

| Columna | Fuente | Notas |
|---|---|---|
| `ad_revenue`, `revenue_same_sku`, `cost`, `orders` | `v_metric_mature` → entidad | Ingreso y gasto publicitario. Halo = diferencia de los dos revenues. |
| `cogs_*` | D1 sobre catálogo del ad group | Ver arriba. |
| `fee` / `refund` / `withholding` por entidad | **No se atribuyen** | Multi-home (obstáculo 1). Restarlos por listing reventaría conteos. |
| Cargos a nivel plataforma | `ledger_event` vía reconciliación | Sellado 4 se cumple **declarando** que la vista de entidad NO resta esas clases; la plataforma sí (`v_margen_plataforma`). |
| `fee_type='ads'` del ledger | **Excluido del gasto de entidad** | El gasto ya vive en `ads_metric_observation.cost`. Restarlo otra vez infla costo (riesgo anticipado en 0.6). |

Declaración fija por fila (texto o columnas booleanas):

- Entraron al margen de entidad: gasto ads (métricas), COGS proxy (D1).
- No entraron: fee, refund, withholding del ledger (van a reconciliación
  de plataforma).
- `fee_type=ads` del ledger: no entra en ningún lado del par entidad.

## Decisión D4 · Ventana y vintage

- **Vintage.** Solo `v_metric_mature` (corte
  `metric_date <= (now() AT TIME ZONE 'UTC')::date - 15`). Consulta sin
  ese corte = no hay vista usable (DoD 1.2: "consulta sin vintage falla").
- **Ventana.** 90 días maduros hacia atrás desde el corte D-15.
  Es decir, `metric_date` en
  `[D_corte - 89, D_corte]` con `D_corte = hoy_UTC - 15`.
- **Por qué 90d.** Magnitudes de halo y multi-home del obstáculo se
  midieron a 90d. La cuenta US cambia de signo del P&L atribuido según
  halo en ~91d (sellado 5). Una ventana corta inventaría estabilidad. Una
  mucho más larga mezcla demasiados regímenes de costo (los costos rotan
  poco, pero el 2026-08-18 movió 937 SKUs de golpe).
- **Por qué no “todo el histórico”.** `v_margen_plataforma` es historia
  completa a propósito y sin entidad. Aquí el número alimenta lectura
  operativa y, luego, targets. Necesita edad de dato acotada y declarada.
- Ledger en la razón D1: misma ventana de `event_date` con el mismo corte
  D-15 simétrico (patrón `v_tacos`), para no mezclar venta fresca con
  gasto maduro.

## Decisión D5 · Moneda por columna

| Pieza | Moneda | FX |
|---|---|---|
| Métricas MX (`cost`, revenues) | MXN | ninguno |
| Métricas US | USD | gasto y revenues se reportan en USD; si un consumidor canónico pide MXN, `fx_resolve(metric_date,'USD','MXN')` por fila (patrón 0005). Sin tasa → fila ausente. `source` se expone (`exact` / `nearest_prior`). |
| `sku_cost` | 100 % MXN | a USD (cuenta US) vía `fx_resolve` al día del costo/métrica |
| `listing_price` | MXN en mx, USD en us | misma moneda que el reporte de la plataforma; si hiciera falta cruce, otra vez `fx_resolve` |
| Ledger (reconciliación) | ya MXN (D8 de 0.6) | no necesita FX |

Un SUM que mezcle MXN con USD es imposible por schema (regla 4). La vista
expone `metric_currency` y nunca publica un total multi-moneda.

## Decisión D6 · Reconciliación fail-loud (obstáculo 6, misma ola)

La vista de margen **no nace** al lado de la deuda de la 0005. La 1.2
(implementación) incluye en el mismo tren, o en PR apilado inmediato
antes de marcar 1.2 Done:

1. Contador `gasto_campaign_sin_contraparte` en `v_tacos` (residuo
   campaign sin hija keyword/target; medido ~0.015 % MX, 0 US).
2. Test vivo fail-loud: `cost IS NULL` → `tacos_pct IS NULL` (ya esbozado
   en 0005; endurecer si el lead lo pide en CI con DSN).
3. Candado anti-deriva de la allowlist de kinds (las 3 copias:
   `v_tacos`, motor/cobertura, y la vista nueva de margen). Un test que
   falle si alguna lista diverge.

Reconciliación de **negocio** (no solo de grano):

- Suma de `cost` de entidades con margen publicado ≤ gasto maduro de
  plataforma (mismo filtro de kind). Hueco = entidades sin catálogo/costo
  /precio/FX, contado por motivo (alimenta la lectura de cobertura).
- Ledger: 84/88 % con `product_id` (obstáculo 7). Los sin producto se
  listan en la evidencia de corrida, no se esconden.
- `v_margen_plataforma` sigue siendo la verdad de contribución a nivel
  plataforma (venta − cargos − COGS con cobertura 100 %). La vista de
  entidad no la sustituye.

## Decisión D7 · Cuándo no hay fila

Ausente (no cero), y contado:

- Entidad sin padre ad_group resoluble, o grupo sin `product_ad` con
  listing+costo+precio.
- Sin `fx_resolve` utilizable cuando hace falta conversión.
- Sin el par completo `ad_revenue` / `revenue_same_sku` en la ventana
  (hoy 100 % a 90d maduros; defensa a futuro).
- Kind fuera de `{keyword, product_target}`.

## Por qué este diseño y no atribución por listing

La fila 1.1 del plan aún dice “por `listing_id`”. El obstáculo 1 lo
tumba con medición. El supuesto de atribución queda así:

> El ingreso atribuido a la entidad es el de Amazon Ads en esa entidad.
> El COGS es un proxy del catálogo del ad group (D1). El ledger no
> atribuye venta a keyword/target; solo reconcilia la plataforma.

## Fuera de alcance (1.1 / 1.2)

- Cambiar umbrales del motor o la escalera.
- Margin-aware targets (Fase 2).
- MeLi (sellado 9).
- Mutaciones en Amazon.
- Tocar el contenedor de producción o correr ingestas/mutaciones vivas
  desde el implementador (regla de proceso post-0.6). Corrida real = lead
  tras merge + deploy formal.

## Checklist para el sello del lead

- [ ] D1 COGS = C con pesos de B (o alternativa escrita).
- [ ] Residual IVA MX aceptado o mitigación exigida.
- [ ] D4 ventana 90d maduros + vintage D-15.
- [ ] D6 ola fail-loud viaja con 1.2.
- [ ] Nombre SQL de la vista (propuesta: `v_margen_entidad`).

Tras el sello, la 1.2 implementa con TDD. Este documento no se reescribe
en silencio: un cambio de D1 a D7 es enmienda versionada.
