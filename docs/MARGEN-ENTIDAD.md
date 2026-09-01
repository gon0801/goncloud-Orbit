# Vista de contribucion por entidad publicitaria (ORBIT 06 · 1.1)

> **Estado.** SELLADO por el lead el 2026-08-31 con UNA enmienda (D1.mx:
> precio neto realizado del ledger en MX — ver Sello del lead) y la
> aprobacion literal del dueno: "si aprobado, sella y dale la 1.2 a
> cursor". La 1.2 implementa ESTE documento.
> **Fuente de hechos.** Obstaculos de la 1.1 en `plans/orbit-06.md`
> (medidos en vivo 2026-08-31, solo lectura). No se re-abren.
> **Consumidor.** Lectura (digest 1.3, dashboard 1.4). **No es senal de
> decision** hasta que el lead levante el candado de D1.bis. Cero escritura
> a Amazon. La Fase 2 nace despues y en shadow.

## Por que no se reusa `v_margen_plataforma`

La vista de plataforma agrupa solo por `(platform, amount_currency)`. Sin
`listing_id`, sin dimension temporal, sin halo. Asi a proposito (COMMENT
en `0001_initial.sql`). La lectura por keyword/target necesita grano de
entidad, ventana, vintage y el par con-halo/sin-halo. Sellado 7: vista
**nueva**, misma resolucion de costo (`sku_cost` vigente) y FX
(`fx_resolve`). Cero segunda forma de resolver costo o tasa.

## Forma del dominio (data shape)

Una fila = una entidad de decision con contribucion **pre-cargos** medible
en una ventana. El nombre no dice "margen": fee/refund/withholding no
entran (multi-home).

```
ContribucionEntidad
  identity:     (platform, ad_entity_id)  # kind IN (keyword, product_target)
  ventana:      [metric_date_from, metric_date_to]  # solo dias maduros
  vintage:      corte D-15 UTC via v_metric_mature
  dinero:       currency_reporte           # MXN en mx; USD en us
  ingreso:      ad_revenue_sum, revenue_same_sku_sum
  gasto_ads:    cost_sum                   # metricas, mismo grano
  cogs:         cogs_sin_halo, cogs_con_halo
  contrib:      contrib_sin_halo, contrib_con_halo  # el par o la fila no sale
  rango_invertido: bool                    # true si ratio_G > 1
  fx:           fx_source                  # exact | nearest_prior | (ausente)
  cargos:       clases_declaradas          # que NO entro (sellado 4)
  precio_as_of: timestamptz                # instante del snapshot de listing_price
```

Salida **aparte** (no es la misma vista): cobertura por entidad ausente,
con motivo (`sin_padre`, `catalogo_parcial`, `sin_mezcla_ledger`,
`sin_fx`, `serie_incompleta`, `kind_fuera`). Asi el consumidor distingue
"sin actividad" de "excluida por datos" (hallazgo Codex media).

Organizacion. Tabla/CTE SQL. Grano de gasto = `v_tacos` 0005 / motor:
`kind IN ('keyword','product_target')`. Catalogo del grupo:
`ad_entity(keyword|product_target) → parent ad_group → hijos product_ad
→ listing → product → sku_cost`.

## Hechos ya sellados por medicion (no re-abrir)

1. Multi-home total. Ingreso por entidad = metricas de ads
   (`ad_revenue` / `revenue_same_sku`). Ledger = reconciliacion de
   plataforma, no atribucion.
2. Halo completo en maduros 90d (~65 % MX / ~64 % US). Rango construible.
3. `orders ≈ unidades` hoy (ledger 90d: MX 300=300, US 178=178). El camino
   de dinero de D1 **no usa** `orders`. El supuesto vive solo como
   **candado SQL aparte** (ver D8), no como columna de la vista.
5 a 7. Monedas, fail-loud de la ola, atribuibilidad ledger (84 % MX /
   88 % US). Ver obstaculos en el plan.

## Decision D1 · COGS por entidad (PROPUESTA AL LEAD)

### Candidatos del obstaculo 4, contra los numeros

| Candidato | Que hace | Contra los numeros | Sesgo |
|---|---|---|---|
| **A** promedio simple | `AVG(cost)` × orders | Multi-ASIN dominante (peor MX 1,259). Aplasta SKUs. | Direccion desconocida. |
| **B** ponderado por revenue ledger | `Σ w_i·cost_i` × orders | Usa mezcla real. No reparte COGS al halo (~65 %). | Ads vs organico. |
| **C** razon costo/precio × ingreso | `ratio × ingreso` | Escala con ambas puntas del halo. Exige precio. | IVA + precio no realizado. |

### Eleccion: **C con pesos de B**, con candados del cross-review

```
vivos_G     = product_ads ENABLED|PAUSED del ad_group padre
completos_G = vivos_G con listing + sku_cost (as-of) + listing_price NOT NULL
              # COBERTURA 100%: si |completos_G| < |vivos_G| → FILA AUSENTE
              # (misma ley que v_margen_plataforma y el gate 0.7 a nivel grupo)

w_i         = venta_ledger(product_i, platform, ventana, misma moneda)
              / Σ ventas
              # si Σ = 0 → FILA AUSENTE (NO peso uniforme = candidato A)
              # ventas en moneda distinta a currency_reporte: convertir con
              # fx_resolve o excluir del peso y contar (ledger EXENTO del
              # sello metric_moneda_de_plataforma)

# cost_i: vigencia sku_cost al metric_date de CADA dia (no "dia representativo")
# price_i: listing_price AS-OF query time (limitacion D1.bis)
ratio_G     = Σ w_i * (cost_i / price_i)   # ambos en currency_reporte
cogs_sin_halo = revenue_same_sku_sum * ratio_G
cogs_con_halo = ad_revenue_sum       * ratio_G
```

Conversion solo con `fx_resolve` (sellado 3). Sin tasa → fila ausente.

Si `ratio_G > 1` (costo sobre precio, liquidacion, o FX desfasado): el par
se publica igual con `rango_invertido = true`. No se silencian ni se
intercambian las puntas.

### Por que no A ni B solo

- **A** miente en el caso normal (multi-ASIN).
- **B solo** deja el halo sin COGS.
- **C+B** reparte COGS a las dos puntas con el mix ledger observado.

### Sesgo declarado (obligatorio en COMMENT y en la fila)

1. Mezcla keyword ≈ mezcla ledger de los productos del grupo.
2. Halo costeado con la misma razon del grupo (proxy de categoria).
3. **IVA MX.** Precio de vitrina suele incluir IVA; costo Odoo es neto
   (`includes_tax=false`). Ratio bajo → COGS subestimado → contribucion
   alta. No se inventa el factor 1.16.
4. **Precio realizado.** `listing_price` no es el precio cobrado
   (promociones, cupones, repricing). Misma direccion: COGS subestimado.
5. **Precio sin historia.** `listing.listing_price` es snapshot mutable
   sin `valid_from`/`observed_at`. Aplicarlo a 90d maduros es
   point-in-time de consulta, no el precio del dia de la metrica.
   La fila expone `precio_as_of`. Un `ingest listings` posterior cambia
   el numero de la misma ventana. **No es backtesteable.**

### D1.bis · Candado de uso (enmienda cross-review)

Esta senal es **solo lectura** (digest / dashboard). **Prohibido** que la
Fase 2 (margin-aware) ni ningun camino de decision la consuma hasta que el
lead selle UNA de:

- (a) fuente de precio neto + efectivo por dia, o
- (b) cambio de D1 a otra formula sin `listing_price` (p.ej. B + tasa
  cogs/venta de plataforma para el halo), o
- (c) aceptacion escrita del sesgo con el texto literal del dueno.

Sin ese sello, 1.3/1.4 muestran el rango con etiqueta
`contribucion_pre_cargos · no decisoria`.

### Que necesita el lead sellar

- Aceptar **C+B** con los candados de cobertura 100 %, Σ=0 → ausente, y
  costo por `metric_date`.
- Aceptar D1.bis (bloqueo decisional) o elegir (a)/(b)/(c).
- Nombre SQL: propuesta `v_contribucion_entidad` (no `v_margen_entidad`).

## Decision D2 · Grano y padres

- Fila: `keyword` y `product_target` (0005 / motor / 0.7).
- `campaign` / `ad_group` no publican fila propia. Rollup = SUM de hijas
  en la capa de lectura (1.4).
- `product_ad` aporta catalogo (`listing_id`), no metricas.

## Decision D3 · Ingreso, gasto, cargos

| Columna | Fuente | Notas |
|---|---|---|
| `ad_revenue`, `revenue_same_sku`, `cost` | `v_metric_mature` | Ingreso y gasto ads. Halo = diferencia de revenues. |
| `cogs_*` / `contrib_*` | D1 | Contribucion pre-cargos. |
| `fee` / `refund` / `withholding` por entidad | **No se atribuyen** | Multi-home. |
| Cargos de plataforma | `v_margen_plataforma` | Verdad de contribucion CON cargos. Superficie distinta. |
| `fee_type='ads'` del ledger | **No** en la vista de entidad | El gasto de entidad es `ads_metric_observation.cost` (una fuente). |

Declaracion fija por fila:

- Entraron: gasto ads (metricas), COGS proxy (D1).
- No entraron: fee, refund, withholding (incl. ISR). Por eso el nombre es
  `contrib_*`, no `margen_*`.
- `fee_type=ads` del ledger no entra aqui.

**Doble superficie del gasto ads (enmienda).** Entidad lee metricas.
`v_margen_plataforma` sigue restando `fee` con `fee_type=ads` dentro de
sus cargos. Son dos medidas del mismo hecho en dos vistas. La
reconciliacion de D6 las compara y cuenta el desfase; no se suman entre
si. Regla 2: un consumidor elige una superficie, no las mezcla.

## Decision D4 · Ventana y vintage

- **Vintage.** Solo `v_metric_mature` (D-15 UTC). Sin ese corte la vista
  no es consultable.
- **Ventana.** 90 dias maduros:
  `metric_date ∈ [D_corte - 89, D_corte]`, `D_corte = hoy_UTC - 15`.
- **Por que 90d.** Magnitudes del obstaculo; signo US inestable a ~91d.
- Ledger de pesos D1: mismo corte D-15 sobre `event_date`.
- **Serie incompleta.** Dias de la ventana con `cost` NULL, o sin el par
  `ad_revenue`/`revenue_same_sku`: se CUENTAN (`filas_sin_costo`,
  `filas_sin_par_halo`). Con una sola → fila de entidad ausente
  (patron 0005). No SUM parcial disfrazado de completo.

## Decision D5 · Moneda por columna

| Pieza | Moneda | FX |
|---|---|---|
| Metricas MX | MXN | ninguno |
| Metricas US | USD | a MXN canonico solo si el consumidor lo pide, con `fx_resolve` por fila; sin tasa → ausente; `source` expuesto |
| `sku_cost` | 100 % MXN | a moneda del reporte con `fx_resolve(metric_date, ...)` |
| `listing_price` | moneda de la plataforma | cruzar con `fx_resolve` si hace falta |
| Ledger (pesos D1) | medicion hoy = MXN; **esquema permite otra** | convertir o excluir; no afirmar "imposible por schema" |

La vista publica `metric_currency` y no mezcla monedas en un total.

## Decision D6 · Reconciliacion fail-loud (obstaculo 6, misma ola)

La 1.2 incluye en el mismo tren (o PR apilado inmediato):

1. Contador `gasto_campaign_sin_contraparte` en `v_tacos`.
2. Test vivo: `cost IS NULL` → `tacos_pct IS NULL`.
3. Candado anti-deriva de la allowlist de kinds (3 copias: `v_tacos`,
   cobertura/motor, `v_contribucion_entidad`).
4. Comparacion gasto entidad (metricas) vs `fee_type=ads` del ledger en
   la misma ventana (desfase contado, no silenciado).
5. Tabla/vista de cobertura por motivo (entidades ausentes).

`v_margen_plataforma` sigue siendo la verdad de contribucion **con
cargos** a nivel plataforma. Esta vista no la sustituye.

## Decision D7 · Cuando no hay fila

Ausente (no cero), contada en cobertura por motivo:

- Kind fuera de `{keyword, product_target}`.
- Sin padre ad_group.
- Catalogo parcial: algun product_ad vivo sin listing+costo+precio.
- Σ ventas ledger del catalogo = 0 (sin mezcla observada).
- Sin `fx_resolve` cuando hace falta.
- Serie incompleta en la ventana (D4).
- Sin el par halo en algun dia de la ventana.

## Decision D8 · Candado `orders ≈ unidades`

No es columna de la vista. Es un test/SQL de vigencia del supuesto del
obstaculo 3:

```sql
-- Debe devolver 0 filas. Si no, el supuesto caduco: fallar CI / alerta.
SELECT platform, count(*), sum(quantity)
  FROM ledger_event
 WHERE kind = 'sale'
   AND event_date >= (now() AT TIME ZONE 'UTC')::date - 15 - 89
   AND event_date <= (now() AT TIME ZONE 'UTC')::date - 15
   AND quantity IS DISTINCT FROM 1
 GROUP BY platform;
```

Si el candado revienta, D1 no se "arregla" con otro proxy: se para y se
revisa (el camino C no usa unidades hoy, pero B u otra formula futura si).

## Por que no atribucion por listing

El obstaculo 1 tumba atribuir venta del ledger al grupo/listing (multi-home
total). La fila 1.1 del plan queda enmendada en este mismo PR.

> Ingreso = metricas de ads en la entidad.
> COGS = proxy del catalogo del ad group (D1).
> Ledger = reconciliacion de plataforma, no atribucion.

## Fuera de alcance (1.1 / 1.2)

- Cambiar umbrales del motor o la escalera.
- Margin-aware targets (Fase 2) — ademas bloqueada por D1.bis.
- MeLi (sellado 9).
- Mutaciones en Amazon.
- Tocar contenedor de produccion o correr ingestas/mutaciones vivas desde
  el implementador. Corrida real = lead tras merge + deploy formal.
- Historia bitemporal de `listing_price` (exigiria esquema nuevo; va por
  D1.bis (a) si el lead la pide).

## Sello del lead (2026-08-31)

**Aprobado con una enmienda, respaldada por medicion en vivo del mismo dia.**

**D1.mx — el precio de la razon en MX es el NETO REALIZADO del ledger, no
`listing_price`.** Medido: 100 % de las ventas MX de la ventana traen
`item_price` (263/263; los 102 productos vendidos), fechado por
`event_date`, y la sanidad es exacta: `item_price/amount = 0.8604 ≈ 1/1.16`
— el IVA, quirurgico. Consecuencias:

- `price_i` en MX = promedio ponderado de `item_price/quantity` del
  producto en la MISMA ventana y filas que dan `w_i` (una fuente, una
  ventana). Muere el sesgo 3 (IVA), el 4 (precio no realizado) y el 5
  (precio sin historia) — y la razon queda ADIMENSIONAL (MXN/MXN): esa
  pieza deja de necesitar FX.
- MX se vuelve backtesteable; `precio_as_of` en MX pasa a ser la ventana
  misma.
- **US queda como propuso Cursor** (`listing_price` + sesgos declarados):
  medido 0/153 ventas US con `item_price` (el candado D8 de la 0.6 lo
  tira: CurrencyCode USD vs amount MXN). D1.bis sigue VIVO para US; para
  MX, la via (a) queda satisfecha por esta enmienda, pero la etiqueta
  `no decisoria` se mantiene en AMBAS plataformas hasta la Fase 2 (el
  proxy de mezcla y el costeo del halo siguen siendo proxys).

**Nota de sello para la 1.2 (trampa de implementacion, no de diseno):**
`fx_rate` solo tiene el par (USD, MXN). `fx_resolve(fecha,'MXN','USD')`
devuelve CERO filas — convertir costo MXN a USD es DIVIDIR entre la tasa
de `fx_resolve(fecha,'USD','MXN')`, jamas llamar el par invertido. Con la
enmienda D1.mx la razon ya no lo necesita en MX; aplica al costo US y a
cualquier total canonico.

**Lo demas, aceptado tal cual**: nombre `v_contribucion_entidad` y columnas
`contrib_*`; D2 grano; D3 fuentes y la doble superficie declarada; D4
ventana/vintage con serie incompleta fail-loud; D5 monedas; D6 la ola
fail-loud completa en el tren de la 1.2; D7 ausencias contadas por motivo;
D8 candado de vigencia de `orders ≈ unidades`.

## Checklist para el sello del lead

- [x] D1 COGS = C+B con cobertura 100 %, Σ=0 → ausente, costo por metric_date
      — **ENMENDADO**: `price_i` MX = neto realizado del ledger (ver Sello).
- [x] D1.bis: via (a) satisfecha para MX por la enmienda; VIVO para US;
      etiqueta `no decisoria` en ambas hasta Fase 2.
- [x] Nombre `contrib_*` / vista `v_contribucion_entidad`.
- [x] D4 serie incompleta fail-loud.
- [x] D6 ola fail-loud + desfase ads metricas vs ledger.
- [x] D8 candado quantity≠1.

Tras el sello, la 1.2 implementa con TDD. Cambio de D1 a D8 = enmienda
versionada, no silencio.

## Historial de enmiendas

- **2026-08-31 (Cursor).** Borrador inicial C+B.
- **2026-08-31 (SELLO del lead).** Aprobacion del dueno con texto literal;
  enmienda D1.mx (precio neto realizado del ledger, medido 100 %/0.8604);
  D1.bis vivo solo para US; nota de la trampa fx_resolve invertido para
  la 1.2.
- **2026-08-31 (post adversario+Codex).** Cobertura 100 %; Σ=0 → ausente;
  costo por `metric_date`; rename a contribucion pre-cargos; D1.bis;
  sesgo precio realizado + precio sin historia; serie incompleta;
  moneda del ledger sin mentir del schema; rango_invertido; D8;
  cobertura por motivo; fila 1.1 del plan alineada al obstaculo 1.
