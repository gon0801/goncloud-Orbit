# ORBIT 19 / 0.3 — Contratos de reporte Ads y disponibilidad comercial

Fecha de evidencia: 2026-09-06 21:03–21:08 UTC (forma) y 22:08–22:11 UTC
(conciliacion de sumas). Rama: `feat/orbit-19-catalogo-fase0`.
Investigacion. Cero codigo de ingesta. Cero POST comerciales.

Sello lead: Ads MX es **verificada**. Permisos, forma (POST 200, COMPLETED,
767 filas) y conciliacion de gasto/clics (`delta_cost=0`, `delta_clicks=0`).
Residual declarado: `delta_impressions=-1` (11717 gzip vs 11718 campana).
`sales30d` gzip (3427.58) no se iguala a `ad_revenue` campana (1972.41):
columnas 30d siguen creciendo; el gzip 22:11 es vintage posterior. Cobertura
de ausentes: contrato solo-actividad verificado (767 filas vs 7709
`product_ad`); COMPLETED no prueba «sin actividad»; ausente = Sin datos.
`attributedSalesSameSku30d` presente en la sonda 22:08 (767/767). US no se
descargo = no_verificada. Stock bridge observado; conciliacion SKU/SP-API
en B.3. Featured Offer no_verificada.

`REPORTES_CFG` en `app/ads/reports.py` es `(spCampaigns, spTargeting, spTargeting, spSearchTerm)`. **No hay `spAdvertisedProduct`**. El cliente (`app/ads/client.py`) permite GET siempre y POST solo a `/reporting/reports` + LIST v3; superficie `create_report` / `get_report` / `download` usada en la sonda.

## Veredicto por fuente

`no_verificada` no se trata como cero: es desconocido. Un 0 observado (fila con `cost=0` o `quantity=0`) es otro hecho.

| fuente | estado | motivo | grano | ventana | atribucion | permisos | conciliacion |
|---|---|---|---|---|---|---|---|
| (a) `spAdvertisedProduct` Ads API | **verificada** (amazon_mx) | Docs + POST 200 + COMPLETED + 767 filas + sumas. cost y clicks cuadran vs `v_metric_latest` campana 2026-09-03. Impressions -1 declarado. COMPLETED no demuestra cobertura de ausentes. US no sondada = no_verificada. | Fila = (date, advertisedAsin, advertisedSku, campaignId, adGroupId, adId). `groupBy=["advertiser"]`. | API: max 31d, retencion 95d. Sondas: 1 dia DAILY `2026-09-03` (21:03 forma; 22:08 sumas). | 1/7/14/30d. Total=`sales30d`. Promovido=`attributedSalesSameSku30d` (sonda 22:08: 767/767). Halo=`salesOtherSku7d` solo 7d. `salesSameSku30d` 400. Madurez: observacion con `observed_at >= metric_date+30d`, no el calendario. | POST 200 amazon_mx. 400 por columna, no por scope. | **Hecha** (`conciliacion-sumas.txt`). gzip vs campana MX 2026-09-03: cost 362.06=362.06, clicks 116=116, impressions 11717 vs 11718. sales30d no se iguala (vintage/atribucion). Prohibido repartir agregado a ASIN. |
| (b) metricas actuales campaign/keyword | **no_verificada** (para rendimiento por ASIN) | Orbit ya ingiere `spCampaigns` / `spTargeting` / `spSearchTerm`. `v_metric_latest` el 2026-09-03 MX no tiene `kind=product_ad` (0 filas). Hay 7709 `product_ad` MX en estructura, 0 metricas. Insuficiente para ASIN. | campana / keyword / product_target. No ASIN. | misma que el cron (D-31..D-1, columnas 30d). Watermark MX/US `2026-09-04`. | 30d (`purchases30d`, `sales30d`, `attributedSalesSameSku30d`). | Ya operativos para esos report types. No autorizan inferir ASIN. | keyword+target cost = campana cost ese dia (362.06). Eso no da un numero por ASIN. Ausencia de metrica ASIN = desconocido, no ACoS 0. |
| (c) stock / inventario bridge | **verificada** (FBA y FBM por separado) | Snapshot `mode=ro` + `.backup()` 2026-09-06. Hay `amazon_listing_prices.quantity` (FBM) y `amazon_fba_inventory.quantity_available` (FBA fresco ese dia). `amazon_inventory_cache.qty` inutilizable (806 filas, todas 0, `updated_at=2026-08-10`). Orbit no ingiere stock. | seller_sku + marketplace. FBA y FBM no se suman. | frescura por tabla: FBA `fetched_at` 2026-09-06 18:36Z; listing `fetched_at` 2026-04-13 .. 2026-09-06 (por fila). | N/A | Lectura snapshot bridge. SP-API Inventory Summary no sondado desde Orbit. Accounting no se toco. | Semantica observada, no vs SP-API: `quantity` SIEMPRE NULL en `AMAZON_NA` (359/359) y NUNCA NULL en `DEFAULT` (450: 242 cero, 208 >0). FBA 2142 filas, 1800 ceros observados, 342 >0. NULL/fila ausente ≠ 0. |
| (d) Featured Offer / eligibility | **no_verificada** | Cero columnas `featured` / `buybox` / `eligibility` en el snapshot. No inferir. Sin fuente = Sin verificar. | — | — | — | No hay permiso ni tabla. | No hay serie que conciliar. |

## Contrato documentado — `spAdvertisedProduct`

Fuentes:

- API: [Advertised product reports](https://advertising.amazon.com/API/docs/en-us/guides/reporting/v3/report-types/advertised-product)
- Help grano/promovido/halo: [GYNVHW8R4QPYUS9H](https://advertising.amazon.com/help/GYNVHW8R4QPYUS9H)
- Help atribucion: [G22MA5YPN9KKT7TM](https://advertising.amazon.com/help/G22MA5YPN9KKT7TM) / [GX7KDKHMWQYMJ385](https://advertising.amazon.com/help/GX7KDKHMWQYMJ385)
- Allowlist vivo: `columnas-api-400.txt`

Configuracion SP:

| campo | valor |
|---|---|
| `adProduct` | `SPONSORED_PRODUCTS` |
| `reportTypeId` | `spAdvertisedProduct` |
| `groupBy` | `["advertiser"]` (unico) |
| `timeUnit` | `SUMMARY` o `DAILY` (`DAILY` exige columna `date`) |
| `format` | `GZIP_JSON` |
| max rango | 31 dias |
| retencion API | 95 dias (help consola: 90) |

Columnas de identidad: `date`, `advertisedAsin`, `advertisedSku`, `campaignId`, `adGroupId`, `adId` (tambien nombres de campana/ad group, `campaignStatus`, presupuesto).

Ventas:

| concepto | columnas API | nota |
|---|---|---|
| Total (promovido + halo) | `sales1d/7d/14d/30d`, `purchases1d/7d/14d/30d` | Help: "Sales" del producto anunciado incluye halo |
| Promovido (mismo SKU) | `attributedSalesSameSku1d/7d/14d/30d`, `purchasesSameSku1d/7d/14d/30d` | Help: "Sales (promoted)" = mismo SKU anunciado |
| Halo (otro SKU) | `salesOtherSku7d`, `unitsSoldOtherSku7d` | Solo 7d en el allowlist. No hay `*OtherSku30d` |
| Invalida | `salesSameSku30d` | 400 vivo; igual que en `spCampaigns` |

No restar Revenue Ads a ventas contables para inventar organico. No mezclar ventana 7d de halo con totales 30d. Halo 30d, si se muestra, es diferencia `sales30d - attributedSalesSameSku30d` con ambas presentes; si falta una, el par es desconocido (regla 3), no cero.

Moneda: las filas no la traen; es la del perfil (MXN / USD). Igual que los reportes ya ingeridos.

## Sonda real de permisos

Dentro de `orbit-app-1` (tiene `ORBIT_SECRETS_DIR`). Cliente del contenedor. Un reporte, 1 dia, D-3 UTC=`2026-09-03`, perfil `amazon_mx` primero. Body analogo a `solicitar_reporte`. Cero ingest, cero escritura de anuncios.

1. POST con `salesSameSku30d` → **400**. Detail: columna invalida. Allowed values incluyen `attributedSalesSameSku30d`, `purchasesSameSku30d`, `sales30d`, `salesOtherSku7d`.
2. Retry sin `salesSameSku30d` → **200**, `reportId` 36 chars, poll PENDING→PROCESSING→**COMPLETED** en intento 23 (~110 s), `fileSize=20813`.
3. Download gzip JSON: **767 filas**, 251 ASIN unicos, 251 SKU unicos. Columnas presentes = las pedidas.

Muestra (5 filas): `muestra-ads.json`. ASIN/SKU se dejan. Cero tokens, `client_id` o URL firmada.

Las 5 filas tienen `impressions>0` y `cost=clicks=sales30d=purchases30d=purchasesSameSku30d=0`. Un cero en la fila es observado. No se conto cuantas de las 767 traen impressions 0.

Sonda de conciliacion (22:08–22:11 UTC), mismo dia/perfil/DAILY: POST 200,
COMPLETED, 767 filas / 251 ASIN. Esta vez se pidio `attributedSalesSameSku30d`
y vino en 767/767. Sumas del gzip vs `v_metric_latest` campana: ver
`conciliacion-sumas.txt`. B.1 reconcilia la tabla persistida; no reabre esta
investigacion.

## Fila ausente no es cero

- Estructura MX: 7709 `product_ad`. Reporte D-3: 767 filas / 251 ASIN. Los ~7458 ads sin fila ese dia no son gasto 0 demostrado para siempre; no estuvieron en el archivo de ese dia.
- Help: el reporte cubre ASINs anunciados; puede incluir campanas PAUSED con 0 impressions si estuvieron ACTIVE en otro periodo. No afirma que la ausencia de ASIN sea cero.
- Funnel/Amazon: Sponsored Products "only return values for Campaigns that contain records with performance activity".
- Orbit ya sello lo mismo en `spCampaigns` ("solo filas con actividad"; metrica ausente → `None`).
- Spec ORBIT19: "Una fila inexistente solo permite inferir cero si el contrato del reporte y su cobertura lo demuestran; de otro modo es desconocido." Aqui no se demuestra. COMPLETED + ASIN ausente = **Sin datos**, no Por probar. Sin datos ≠ ACoS 0.

Atribucion inmadura: las metricas de un `date` siguen creciendo hasta cerrar 1/7/14/30d (y restatements). Dato reciente es provisional. El motor ya re-pide la ventana 30d; B.1 debe hacer lo mismo. 10 dias de madurez de cortes **no** cierran una columna 30d.

## Métricas actuales (b)

`REPORTES_CFG` no tiene producto anunciado. `ads_metric_observation` se llena a grano campana/keyword/product_target. El 2026-09-03 MX: 16 campanas con metrica, 0 product_ad. Hay identidad de anuncio (`ad_entity.kind=product_ad`, 5049 con `listing_id`, 342 listings) pero **cero rendimiento por ASIN**.

Usar (b) para el bloque "Rendimiento Ads" del spec seria repartir agregados — prohibido. (b) no bloquea A ni B.2; no sustituye a (a).

## Disponibilidad comercial

Snapshot: `python3 sqlite3.connect(file:bridge.db?mode=ro).backup()`. `/tmp/bridge-snapshot-orbit19.db` vive en PrivateTmp de la sesion SSH; no reaparece en la siguiente. Detalle: `bridge-observado.txt`.

`amazon_listing_prices` (809): precio **y** `quantity`, `fulfillment_channel`, `status`, `fetched_at`. No es "solo precios". `quantity` es listing FBM (`DEFAULT`); en FBA (`AMAZON_NA`) viene NULL — leer NULL como 0 mentiria 359 filas. `status` Inactive/Incomplete no es stock.

`amazon_fba_inventory` (2142, fresco 2026-09-06): `quantity_available` por seller_sku+marketplace. 1800 ceros observados, 342 positivos. No se confirmo el mapeo al campo fulfillable de SP-API (accounting no se toca). No sumar con `listing.quantity`.

`amazon_inventory_cache`: stale y todo-cero. Prohibido como fuente.

Featured Offer / Buy Box / eligibility: sin columna. **Sin verificar.** No bloquea seleccion ni comparacion Ads/economia (spec: disponibilidad Recommended).

Cero observado (`quantity=0`, `quantity_available=0`) ≠ fila ausente ≠ Featured Offer desconocido. Tres estados distintos (AC8).

## Que puede continuar / que queda

Puede continuar:

- **Fase A** (catalogo abierto): no depende de Ads por ASIN ni de stock.
- **B.2** (economia observada): no depende de (a); margen/ventas contables.
- **B.1** (ingesta Ads por producto anunciado): fuente (a) **verificada** en esta investigacion. Implementa la ingesta y reconcilia la tabla persistida vs gzip (misma ventana/moneda). No reabre 0.3 ni 0.4. Contrato minimo 30d (pedir, no dar por observado): `date, advertisedAsin, advertisedSku, campaignId, adGroupId, adId, impressions, clicks, cost, purchases30d, sales30d, purchasesSameSku30d, attributedSalesSameSku30d`. No pedir `salesSameSku30d`. Append-only. No repartir campana. Por probar exige cobertura demostrada del universo anunciado (regla de 0.4); si no, Sin datos.
- **0.4**: politica cerrada. No espera a B.1. Por probar sigue prohibido hasta cobertura demostrada.
- **B.3 stock**: puede mapear FBA y FBM por separado con frescura y 0-vs-ausente-vs-desconocido. No usar cache. No declarar integracion Featured Offer.

No bloqueado por esta tarea:

- B.1 puede arrancar cuando 0.4 este cerrada (Depends 0.4). La conciliacion de investigacion ya esta en `conciliacion-sumas.txt`.
- B.3 stock no se bloquea por "no hay tabla": si hay. Featured Offer queda ampliacion / Sin verificar; no bloquea B.4/B.5.

Sigue prohibido: crear/pausar anuncios; tocar accounting; tratar `no_verificada` o NULL como cero; mezclar halo 7d con totales 30d.

## Anexos

- `muestra-ads.json` — 5 filas redactadas + conteos
- `columnas-api-400.txt` — allowlist vivo (cuerpo 400)
- `conciliacion-sumas.txt` — gzip vs campana 2026-09-03 MX (22:08 UTC)
- `bridge-observado.txt` — tablas, columnas, cruces FBA/FBM
- `orbit-select.sql` / `orbit-select.out.txt` — kinds y metricas 2026-09-03 MX
