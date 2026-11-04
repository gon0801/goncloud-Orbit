# REPUTACION 01 / 0.2 — Sonda Apify (CERRADA)

Fecha: 2026-09-07/08 UTC. Lead. Store + uso (gratis) y 5 corridas
minimas con go del dueno (costo medido $0.006 total; usage 2.5691
→ 2.575 USD).

Sello: rating/count Amazon **verificado** via
`junglee/Amazon-crawler` (stars + reviewsCount por producto).
Texto de reviews (web_wanderer) **no_verificado**: 0/7 ASINs con
el input historico exacto. A.3 sigue bloqueada para texto; A.2
gana snapshots Amazon (ver condiciones abajo).

## Cuenta (verificado)

- Usuario `gon0801`, plan FREE: tope $5 USD/mes, 625 compute units.
- Gasto ciclo 08-27→09-26: **$2.57** (todo `PAID_ACTORS_PER_EVENT`
  del scraper MeLi diario). Resto estimado: ~$2.43.
- Corre a diario `devcake/mercadolibre-scraper` (SUCCEEDED 11:30 UTC,
  ~56s). 11/11 runs del historial son ese actor: **cero antecedente
  Amazon** en la cuenta.
- Punto para 0.5: D6 topea $10/mes pero el plan actual corta en $5.
  Gastar entre $5 y $10 exige subir de plan Apify.

## Candidatos (Store, verificado 2026-09-08)

| actor | precio | veredicto |
|---|---|---|
| `junglee/amazon-reviews-scraper` | $40/mes flat | DESCARTADO: 4x el tope D6 |
| `axesso_data/amazon-review-details-scraper` | pay-per-event (detalle por review ID) | COMPLEMENTO: necesita IDs previos, no descubre |
| `web_wanderer/amazon-reviews-extractor` | $0.0006→$0.0003 por review + pay-per-event compute | CANDIDATO: 396k runs, 4909 users, vivo hoy, "20+ regiones" |

## Tabla recurrencia×cobertura (HIPOTESIS hasta corrida minima)

Cobertura v1: 518 ASINs (342 MX + 176 US). Supuesto sin verificar:
el candidato acepta lista de ASINs y cobra solo reviews traidas
(R por ASIN) + compute menor.

| recurrencia | R=5 ($0.78/corrida) | R=10 ($1.55/corrida) |
|---|---|---|
| diaria x30 | $23.4 FUERA | $46.5 FUERA |
| semanal x4 | $3.1 DENTRO | $6.2 DENTRO justo |
| quincenal x2 | $1.6 DENTRO | $3.1 DENTRO |

Sin verificar: input real (nombres de campos, maxReviews),
¿trae rating/count del producto o solo reviews?, id estable por
review (requisito de dedup A.3), compute pay-per-event por corrida,
cobertura .com.mx vs .com.

## Corridas minimas (con go 2026-09-08)

Actor `gFtgG31RZJYlphznm`, input `{"products": [urls /dp/ASIN]}`.
(descubierto por 400s gratuitos: `products` requerido, no
`startUrls`; sin inputSchema publicado.)

| run | ASINs MX | resultado |
|---|---|---|
| `in8aiL5P8bw4tKN2j` | B0849JWYD8 | SUCCEEDED 15s, 0.002 CU, dataset 0 items |
| `jrGmgM3l4P1aTHPih` | B0849KPBG9, B0849LPW2V, B0849MTQ1D | SUCCEEDED, 0.0026 CU, 0 items |
| `DNJ9ZZyL7Y5qd4xvv` | B0BXHS56ZH (12 reviews en backup jun-2026), B0B372XLPN | SUCCEEDED, 0.0033 CU, 0 items; B0B372XLPN da 404 (producto muerto) |
| `T0Vv7jFEyiWdRSoHD` | B0BXHS56ZH con input HISTORICO exacto (`products:[ASIN], limit:2, sort:recent, region:amazon.com.mx`) | SUCCEEDED, 0.001 CU, 0 items |

El actor detecta bien `(ASIN, MX, es)` pero en su version actual
solo busca image/video reviews (log: "Getting image reviews ...
No image review found") y el mismo avisa que Amazon restringio text
reviews (US; MX igualmente vacio en la practica). El sistema viejo
usaba ESTE actor con el input historico y traia texto (163 reviews
en `product_reviews`, ultimas MX 2026-07-26): el actor o Amazon
cambio despues. Control directo por curl imposible (robot check
"automated access"). Codigo viejo: `app/main.py`
`_sync_amazon_reviews_apify` (cron diario 16:00, flag hoy `off`).

Veredicto: sin rating/count/texto por ASIN, este actor NO cumple el
contrato A.3. Evaluar otros actores de texto queda como sonda
futura; el patron sugiere restriccion general de text reviews.

## Rating/count via junglee/Amazon-crawler (VERIFICADO 2026-09-08)

Mismo actor default del sistema viejo (`APIFY_ACTOR_AMAZON_DEFAULT`).
Input historico: `{"categoryOrProductUrls": [{"url": .../dp/ASIN}],
"maxItemsPerStartUrl": 1}` por `run-sync-get-dataset-items`.

2 corridas (1 ASIN MX c/u, B0BXHS56ZH): 201, item con `stars=4.2`,
`reviewsCount=59`, `hasReviews=true`, `loadedCountryCode=MX`, precio,
marca. Costo medido: ~$0.0025/producto.

Condiciones para el acta 0.5:

- Padre/hijo: pedido B0BXHS56ZH, devuelve `asin=B0BXHVT1MG` (padre)
  con `originalAsin`+`input` = lo pedido. El rating es del PADRE
  (lo que ve el comprador). Snapshot por ASIN pedido + columna
  parent_asin; declarar grano padre.
- Costo: 518 ASINs x $0.0025 = ~$1.30/corrida. Semanal x4 = ~$5.2/mes
  + MeLi diario $2.57 = ~$7.8: DENTRO de D6 ($10) pero EXCEDE el plan
  FREE ($5) → subir de plan Apify si se aprueba.
- Hallazgo parcial (no verificado): el item trae `productPageReviews`
  (username, ratingScore, titulo, texto, `date:null` en la muestra).
  Posible rescate parcial de texto en A.3 futuro; sin fecha no sirve
  para flanco temporal. Requiere sonda propia.

## Tabla recurrencia×cobertura definitiva (snapshots Amazon)

| recurrencia | costo/mes est. | cabe en D6 |
|---|---|---|
| semanal (518 ASINs) | ~$5.2 + $2.57 MeLi = ~$7.8 | SI (subiendo de plan) |
| quincenal | ~$2.6 + $2.57 = ~$5.2 | SI (al limite del FREE) |
| diaria | ~$39 | NO |

Decision del dueno 2026-09-08: **semanal, 518 ASINs**; si el FREE
no alcanza, sube de plan. La primera corrida real fija el costo
verdadero (estimado $0.0025/producto con 2 corridas chicas).
