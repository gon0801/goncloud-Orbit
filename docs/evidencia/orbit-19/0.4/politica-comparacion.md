# ORBIT 19 / 0.4 — Politica de comparacion

Estado: **contrato tecnico sellado sobre 0.3**. El orden inicial y el
objetivo de comparacion siguen la propuesta D1/D2; **no estan aprobados
por el dueno**. 0.4 no sustituye a 0.2.

Fuentes: `docs/evidencia/orbit-19/0.3/reporte.md` (Ads verificada
2026-09-06 21:03 UTC, amazon_mx D-3) y spec
`docs/superpowers/specs/2026-09-06-catalogo-campanas-abierto-design.md`.

## 1. Campos del reporte Ads verificado

`reportTypeId=spAdvertisedProduct`, `groupBy=["advertiser"]`,
`adProduct=SPONSORED_PRODUCTS`, `timeUnit=DAILY`, `format=GZIP_JSON`.

Contrato minimo de B.1 (30d):

| campo API | uso |
|---|---|
| date | metric_date |
| advertisedAsin | ASIN anunciado (identidad) |
| advertisedSku | seller SKU anunciado |
| campaignId, adGroupId, adId | trazabilidad; NO se usan para repartir |
| impressions, clicks, cost | actividad / gasto |
| purchases30d | compras totales (incluyen halo) |
| sales30d | Revenue Ads total (incluye halo) |
| purchasesSameSku30d | compras mismo SKU |
| attributedSalesSameSku30d | Revenue Ads promovido (mismo SKU) |

Prohibido pedir `salesSameSku30d` (400 vivo, `columnas-api-400.txt`).
`attributedSalesSameSku30d` esta en el allowlist; la sonda de 0.3 no la
descargo (el retry no la pidio). B.1 la pide y concilia. Si falta en una
fila, promovido = null, no cero.

Halo nombrado por API: `salesOtherSku7d` / `unitsSoldOtherSku7d` **solo 7d**.
No mezclar con totales 30d. Halo 30d solo si ambas `sales30d` y
`attributedSalesSameSku30d` estan presentes:
`halo30d = sales30d - attributedSalesSameSku30d`. Si falta una, el par
es desconocido.

Moneda: la del perfil (amazon_mx=MXN, amazon_us=USD). Las filas no la traen.

**Pendiente de muestra:** perfil `amazon_us` no se sondó. Se asume el mismo
`reportTypeId` (misma app LWA). B.1 confirma US en la primera ingesta;
si 400/403, US queda no_verificada y no se inventan filas.

## 2. Ventana, cobertura, madurez

| eje | regla |
|---|---|
| Ventana de request | igual al cron actual: max 31 dias, re-pedir D-31..D-1 |
| Retencion | 95d API (help consola 90d). Backfill no diario. |
| Cobertura | el gzip cubre ASINs con actividad en el rango. 7709 product_ad MX vs 767 filas el 2026-09-03: la ausencia de fila **no** es gasto 0. |
| Grano de comparacion | (platform, advertisedAsin, advertisedSku) agregado por suma de filas compatibles. No repartir cost/sales de campana entre ASIN. |
| Madurez atribucion | columnas 30d siguen creciendo ~30d. Fecha > hoy_utc-30 = provisional. Los 10 dias del motor de cortes no cierran esta columna. |
| Muestra | count de fechas con fila y sumas. 1 compra visible junto al ACoS, no como mala nota. |
| Frescura | `observed_at` de la ingesta. Append-only. |

Comparar solo filas del mismo mercado, moneda, grano y ventana.

## 3. Objetivo de comparacion (depende D2, no aprobado)

Un objetivo es el del **grupo que el dueno esta preparando**, identificado
como tal. Antes de capturarlo, se muestra ACoS sin etiqueta dentro/fuera.
No se promedia el target de varias campanas para fabricar uno por ASIN.

Si D2 se acepta: `manual_lanzamiento` o `margen_medido` del grupo en curso.
Si D2 se rechaza, se replanifica.

## 4. Precedencia de etiquetas Ads

1. Reporte faltante o cobertura no verificada → **Sin datos**. Subtotales
   parciales se marcan parciales. No etiqueta dentro/fuera. **No** se
   llama Por probar: esa etiqueta exige evidencia de cobertura.
2. Cobertura **verificada** (request COMPLETED del contrato 0.4) y sin
   actividad observada (cero filas para ese ASIN/SKU en toda la ventana
   pedida) → **Por probar en esta ventana**. Significa solo eso. No
   implica producto nuevo ni que nunca se haya anunciado.
3. Gasto observado > 0 y sales30d observado = 0 → **Gasto sin ventas**,
   ACoS = null. Cero en la fila es observado, no ausencia.
4. sales30d > 0 → ACoS = 100 * suma(cost) / suma(sales30d).
   Con objetivo explicito: ACoS <= objetivo → **Dentro del objetivo**;
   ACoS > objetivo → **Por encima**. Igualdad cuenta Dentro.
   Sin objetivo: solo el numero. Evidencia inmadura → provisional.
5. Resto: se muestran los datos, sin forzar etiqueta.

CPC = suma(cost)/suma(clicks) si clicks > 0 y cobertura compatible; si no, null.
CVR = 100 * suma(purchases30d)/suma(clicks) con la misma guarda.

Ratios desde **sumas**, nunca promedio de porcentajes.

## 5. Economia observada (no Ads)

Vista madura `v_margen_producto` intacta (>=30 fechas, cobertura, moneda).
Si D4 se ratifica, la UI puede mostrar muestra limitada (1–29 fechas,
integridad OK) **aparte**, nunca como input de target automatico.

Dos listings del mismo producto comparten venta/margen del producto.
No duplicar el total financiero.

## 6. Disponibilidad (Recommended)

FBA = `amazon_fba_inventory.quantity_available` (fresco 2026-09-06).
FBM = `amazon_listing_prices.quantity` solo si `fulfillment_channel=DEFAULT`.
`AMAZON_NA.quantity` es NULL, no cero. No sumar FBA+FBM.
Cache `amazon_inventory_cache` prohibido (stale, todo cero).
Featured Offer: **Sin verificar**. No bloquea seleccion ni comparacion.

Tres estados: 0 observado / ausente / desconocido (AC8).

B.3 todavia debe conciliar SKU contra `listing` de Orbit y no afirmar
que `quantity_available` = fulfillable SP-API.

## 7. Ordenes (depende D1, no aprobado)

Soportados: margen observado, ventas totales, Revenue Ads (`sales30d`),
gasto, ACoS, CPC, CVR, compras. Asc/desc visible.

Null al final en ambas direcciones. Desempate estable `listing_id`.
Filtros/orden no pierden la seleccion (AC10).

Propuesta D1 (no aprobada): orden por `margen_neto_pct` maduro
(porcentaje, ventana `[2026-02-20, D-15)` UTC). `dias_con_venta` visible.
NULL al final, nunca como 0%. MX y US no se mezclan. Por probar (Ads)
solo con cobertura verificada; si no, Sin datos. Fallback tecnico si
D1 no se cierra: `listing_id` ascendente. La muestra 1–29 no entra al
sort salvo D4 ratificada.

## 8. Fixtures del spec — resultado esperado

Cifras ilustrativas, nunca valores sembrados en produccion.

| Caso | Entrada | Resultado |
|---|---|---|
| Ratios desde sumas | Gasto 10 / ventas 100 y gasto 90 / ventas 300, misma moneda/ventana | ACoS 25% (100/400), no promedio 20%. Muestras y cobertura visibles. |
| Igualdad | ACoS 25%, objetivo explicito 25% | Dentro del objetivo |
| Distintos objetivos de campana | Una publicacion en dos campanas con targets distintos; sin objetivo de comparacion | ACoS sin etiqueta dentro/fuera; no target promedio. Sumar cost/sales de las filas del ASIN. |
| Cero y ausencia | (1) gasto 10, sales30d 0 observado (2) ASIN ausente del gzip con cobertura verificada (3) reporte faltante | (1) Gasto sin ventas, ACoS null (2) Por probar en esta ventana (3) Sin datos. (2) no prueba «nunca anunciado» |
| Margen vs objetivo | Preview con target manual y margen 0, negativo o 8% vs objetivo 25% | Seleccionable. Revision muestra el margen y que es inferior/no positivo. No se presenta como rentable |
| Muestra | Dos ASIN ACoS 25%, compras 1 y 100 | Mismo resultado frente al target; conteos distintos visibles |
| Mismo producto | Dos listings, venta financiera 100 y margen 20% del producto | Grano compartido; total financiero 100, no 200 |
| Historia fuera de ventana | Request cubierto, cero actividad actual, actividad antigua conocida | Por probar en esta ventana; no "producto nuevo"; no "nunca anunciado" |
| Reporte faltante | Sin gzip / cobertura no verificada, aunque el ASIN exista en estructura | Sin datos. Prohibido etiquetar Por probar |
| Orden estable | Misma metrica o null | Desempate listing_id; null al final; seleccion preservada |
| Columna promovida ausente | sales30d presente, attributedSalesSameSku30d null | Total visible; promovido/halo 30d desconocidos; no restar |
| salesSameSku30d | cualquier request | No se pide. 400 documentado. |

## 9. Que puede continuar

- B.1 puede disenarse con este contrato (MX verificado; US en primera ingesta).
- B.2 no espera Ads.
- B.4/B.5 esperan D1 (orden) y D2 (objetivo) para no construir un ranking
  que el dueno no pidio. Los fixtures de arriba no dependen de D1.
- B.3 stock puede mapear FBA/FBM; Featured Offer queda Sin verificar.

No se implementa codigo en esta tarea.
