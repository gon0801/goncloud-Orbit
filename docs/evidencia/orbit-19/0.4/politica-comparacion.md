# ORBIT 19 / 0.4 — Politica de comparacion

Estado: **cerrada**. Ads MX **verificada** en 0.3 (forma, permisos,
cost/clicks; `conciliacion-sumas.txt`). Residual `delta_impressions=-1`.
Ventas y compras sin conciliar; actualizacion de atribucion como hipotesis
no demostrada (falta la fecha de observacion del agregado de campana).
D1–D4 cerradas (0.2). No bloquea la fase A. B.1 implementa la ingesta y reconcilia la tabla persistida; no
es candado de esta politica. Por probar no se asigna hasta cobertura
demostrada (regla cerrada, no tarea abierta de 0.4).

Fuentes: `docs/evidencia/orbit-19/0.3/reporte.md` y spec
`docs/superpowers/specs/2026-09-06-catalogo-campanas-abierto-design.md`.

## 1. Campos del reporte Ads (forma MX; conciliacion 0.3 hecha)

`reportTypeId=spAdvertisedProduct`, `groupBy=["advertiser"]`,
`adProduct=SPONSORED_PRODUCTS`, `timeUnit=DAILY`, `format=GZIP_JSON`.

Contrato minimo de ingesta B.1 (30d):

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
`attributedSalesSameSku30d` esta en el allowlist; la sonda 22:08 la pidio
y vino en 767/767. Si falta en una fila, promovido = null, no cero.

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
| Cobertura | COMPLETED **no** demuestra cobertura. El gzip SP actual solo trae filas con actividad (sonda 0.3 + docs Amazon). ASIN ausente del gzip = **Sin datos**, no Por probar, hasta que un reporte o cruce demuestre universo exhaustivo (fila por ASIN anunciado, incluidos ceros). Eso lo implementa B.1; no es candado de 0.4. 7709 product_ad MX vs 767 filas el 2026-09-03 no prueba «sin actividad». |
| Grano de comparacion | (platform, advertisedAsin, advertisedSku) agregado por suma de filas compatibles. No repartir cost/sales de campana entre ASIN. |
| Madurez atribucion | columnas 30d siguen creciendo ~30d. Una fila de `metric_date` D es madura para columnas 30d **solo** si existe observacion con `observed_at` (UTC) **>= D + 30 dias**. El paso del calendario no basta: un gzip de D+1 consultado 30 dias despues sigue provisional. Los 10 dias del motor de cortes no cierran esta columna. |
| Muestra | count de fechas con fila y sumas. 1 compra visible junto al ACoS, no como mala nota. |
| Frescura | `observed_at` de la ingesta. Append-only. |

Comparar solo filas del mismo mercado, moneda, grano y ventana.

## 3. Objetivo de comparacion (D2 cerrada)

Un objetivo es el del **grupo que el dueno esta preparando**, identificado
como tal (`manual_lanzamiento` o `margen_medido`). Antes de capturarlo, se
muestra ACoS sin etiqueta dentro/fuera. No se promedia el target de
varias campanas. El objetivo no acredita rentabilidad.

## 4. Precedencia de etiquetas Ads

1. Reporte faltante, cobertura no demostrada, o ASIN/SKU ausente de un
   gzip que solo trae actividad → **Sin datos**. Subtotales parciales se
   marcan parciales. No etiqueta dentro/fuera. COMPLETED no basta.
2. **Por probar en esta ventana** solo si la cobertura del universo de
   ASINs anunciados en esa ventana esta **demostrada** (no solo COMPLETED)
   y no hay actividad Ads en esa ventana. Hoy esa demostracion no existe.
   No implica producto nuevo ni que nunca se haya anunciado. Hasta que
   exista esa demostracion, no se asigna Por probar.
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
La UI muestra muestra limitada (1–29 fechas, integridad OK) **aparte**,
nunca como input de target automatico ni del sort D1 (D4).

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

## 7. Ordenes (D1 cerrada)

Soportados: margen observado, ventas totales, Revenue Ads (`sales30d`),
gasto, ACoS, CPC, CVR, compras. Asc/desc visible.

Null al final en ambas direcciones. Desempate estable `listing_id`.
Filtros/orden no pierden la seleccion (AC10).

Orden inicial: `margen_neto_pct` maduro (porcentaje, ventana
`[2026-02-20, D-15)` UTC). `dias_con_venta` visible. NULL al final,
nunca como 0%. MX y US no se mezclan. Ausencia Ads = Sin datos hasta
cobertura demostrada. La muestra 1–29 no entra al sort (D4). Desempate
`listing_id`.

## 8. Fixtures del spec — resultado esperado

Cifras ilustrativas, nunca valores sembrados en produccion.

| Caso | Entrada | Resultado |
|---|---|---|
| Ratios desde sumas | Gasto 10 / ventas 100 y gasto 90 / ventas 300, misma moneda/ventana | ACoS 25% (100/400), no promedio 20%. Muestras y cobertura visibles. |
| Igualdad | ACoS 25%, objetivo explicito 25% | Dentro del objetivo |
| Distintos objetivos de campana | Una publicacion en dos campanas con targets distintos; sin objetivo de comparacion | ACoS sin etiqueta dentro/fuera; no target promedio. Sumar cost/sales de las filas del ASIN. |
| Cero y ausencia | (1) gasto 10, sales30d 0 observado (2) ASIN ausente del gzip COMPLETED de un reporte solo-actividad (3) reporte faltante | (1) Gasto sin ventas, ACoS null (2) **Sin datos** (3) Sin datos. COMPLETED no convierte (2) en Por probar |
| Margen vs objetivo | Preview con target manual y margen 0, negativo o 8% vs objetivo 25% | Seleccionable. Revision muestra el margen y que es inferior/no positivo. No se presenta como rentable |
| Muestra | Dos ASIN ACoS 25%, compras 1 y 100 | Mismo resultado frente al target; conteos distintos visibles |
| Mismo producto | Dos listings, venta financiera 100 y margen 20% del producto | Grano compartido; total financiero 100, no 200 |
| Historia fuera de ventana | Gzip solo-actividad COMPLETED, ASIN ausente ahora, actividad antigua conocida | **Sin datos** en esta ventana; no Por probar; no "producto nuevo" |
| Reporte faltante | Sin gzip / cobertura no demostrada, aunque el ASIN exista en estructura | Sin datos. Prohibido etiquetar Por probar |
| Madurez sin observacion posterior | metric_date D, unica observacion observed_at = D+1, consulta en D+40 | Provisional. No maduro: falta observacion con observed_at >= D+30 |
| Madurez con observacion posterior | metric_date D, observacion observed_at >= D+30 | Maduro para columnas 30d |
| Orden estable | Misma metrica o null | Desempate listing_id; null al final; seleccion preservada |
| Columna promovida ausente | sales30d presente, attributedSalesSameSku30d null | Total visible; promovido/halo 30d desconocidos; no restar |
| salesSameSku30d | cualquier request | No se pide. 400 documentado. |

## 9. Que puede continuar

- **Fase A** no espera 0.4 ni Ads.
- **0.4 cerrada.** B.1 puede arrancar (Depends 0.4). Reconcilia la tabla
  ingerida vs gzip; no reabre este documento ni 0.3.
- B.2 no espera Ads.
- B.4/B.5 no asignan Por probar hasta cobertura demostrada.
- B.3 stock: FBA/FBM; Featured Offer Sin verificar.
- Ads MX verificada; US no_verificada hasta la primera ingesta.

No se implementa codigo en esta tarea.
