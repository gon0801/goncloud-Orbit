# ORBIT 19 / B.1 — Contrato de ingesta `spAdvertisedProduct`

Estado: implementado (migracion `0020_ads_producto_metrica.sql`, ingesta en
`app/ads/reports.py` con `PRODUCTOS_CFG` / `ingest_productos`, corrida propia
`--productos`). Fuente: politica cerrada 0.4 (secciones 1-2) y evidencia viva
0.3 (Amazon MX verificada 2026-09-06).

## Campos pedidos (y prohibidos)

`reportTypeId=spAdvertisedProduct`, `groupBy=["advertiser"]`,
`adProduct=SPONSORED_PRODUCTS`, `timeUnit=DAILY`, `format=GZIP_JSON`.

| campo API | columna en `ads_product_metric_observation` |
|---|---|
| date | metric_date |
| advertisedAsin / advertisedSku | advertised_asin / advertised_sku (identidad) |
| campaignId / adGroupId / adId | campaign_ids / ad_ids (JSONB de trazabilidad; NUNCA insumo de reparto) |
| impressions / clicks | impressions / clicks (BIGINT) |
| cost | cost (money_amount = NUMERIC(14,4)) |
| purchases30d | purchases30d (NUMERIC(14,4)) |
| sales30d | sales30d (NUMERIC(14,4)) — total, incluye halo |
| purchasesSameSku30d | purchases_same_sku30d |
| attributedSalesSameSku30d | attributed_sales_same_sku_30d — promovido (mismo SKU) |

PROHIBIDO pedir `salesSameSku30d` (400 vivo, `docs/evidencia/orbit-19/0.3/columnas-api-400.txt`).
Valor ausente en la fila = NULL, jamas 0 (regla 3). Moneda = la del perfil
(amazon_mx=MXN, amazon_us=USD), sellada por el trigger compartido
`metric_moneda_de_plataforma` (0020 agrega la rama de la tabla nueva).

## Grano y agregacion

- Grano sellado: `(platform, advertised_asin, advertised_sku, metric_date)`
  con `observed_at` bitemporal. PK = grano + observed_at (regla 5).
- Filas del MISMO asin/sku en varias campanas (o varios ads) se SUMAN hacia
  la clave, con envenenamiento: si algun aporte trajo la metrica ausente, la
  fusionada queda NULL (regla 3). Metrica negativa en fila cruda aborta la
  corrida fail-closed.
- NUNCA se reparte un agregado de campana entre productos (politica 0.4 §2):
  esta ingesta no lee `ads_metric_observation` ni escribe en ella.
- Append-only: re-ingesta del MISMO report id = dedupe (indice parcial
  `apm_dedupe_reporte`); reporte NUEVO del mismo dia = fila nueva, jamas UPDATE.

## Ventana y corrida

- Misma ventana que el cron actual: request max 31 dias, tirada diaria
  D-31..D-1 re-pidiendo las columnas 30d (maduracion de atribucion).
- Entrada: `python -m app.ads.reports --productos [--fecha ... --fecha-fin ...]`
  (misma puerta CLI de siempre, `app/cli.py` ya despacha a `reports.main`;
  sin `--productos` corren los 4 reportes clasicos, sin cambios).
- Skip automatico de reportes: sin asin/sku, date invalida, fecha futura,
  fuera del rango solicitado, metrica no numerica/fraccionaria, fila sin
  ninguna metrica, same_sku > total y fila absorbida por fusion
  (vocabulario cerrado, contado en `ingest_run.skip_reason`).

## Madurez (politica 0.4 §2, para consumidores)

Una fila de `metric_date` D es madura para columnas 30d SOLO si existe
observacion con `observed_at` (UTC) >= D + 30 dias. El paso del calendario no
basta. Los 10 dias del motor de cortes NO cierran esta columna.

## Cobertura / US

- El gzip SP solo trae filas con actividad: ASIN ausente = Sin datos, no
  cero, no Por probar (hasta cobertura demostrada del universo anunciado).
- `amazon_us` NO sondado (0.3): si su reporte da 400/403 la corrida aborta
  fail-closed y NO se inventan filas; US queda no_verificada hasta la
  primera ingesta viva (pendiente para el lead).
