# REPUTACION 01 / 0.3 — Sonda MeLi

Fecha: 2026-09-07/08 UTC. Lead. Solo GET, cero mutaciones. Token de
`accounting/config/meli_tokens.json` (el cableado a ORBIT_SECRETS_DIR
es de A.2/A.4 + deploy).

Sello: MeLi **verificada** en los 7 endpoints (tabla). Correccion al
plan: opiniones es `/reviews/item/{id}` (singular); el candidato
`/reviews/items/` del plan NO se probo porque el singular respondio
primero — si A.4 quiere el plural, pide sonda.

## Veredicto por endpoint

Base `https://api.mercadolibre.com` + `Authorization: Bearer`.

| endpoint | estado | motivo |
|---|---|---|
| `GET /users/me` | **verificada** | 200, seller_id real `135734858` (ELECTRONICSHOUSE). El plan pedia no usar historico: confirmado en vivo. |
| `GET /users/{id}` (reputacion) | **verificada** | 200 `seller_reputation`: level `5_green`, silver, tx 662 (635 completed, 27 canceled), ratings 1/0/0. Grano: cuenta. |
| `GET /users/{id}/items/search?search_type=scan` | **verificada** | 200, 65 items en 1 pagina (limit=100); trae `scroll_id` para paginar. Grano: item. |
| `GET /items/{id}` | **verificada** | 200, `health` numerico (0.87 en la muestra), `status`, `sub_status`. Grano: item. |
| `GET /reviews/item/{id}` | **verificada** | 200 `rating_average`, `paging.total`, `rating_levels{one..five_star}`, `reviews[{id,rate,title,content,date_created,status}]`. Muestra con 4 reviews (avg 4.3) y muestra con 0. **total=0 trae avg=0: ese 0 es sin-dato, no rating** (regla acta 0.5). |
| `GET /questions/search?item=&seller_id=` | **verificada** | 200 ambas formas; `status` filtra (`UNANSWERED` total=1 hoy; 51 preguntas totales). Grano: pregunta. D5 (conteo pendientes) viable. |
| `GET /post-purchase/v1/claims/search?player=&stage=dispute` | **verificada** | 200, 22 disputes, `data[{id,resource:order,status,type,stage,reason_id,players}]`. Sin `stage` (o con `seller_id`) → 400 `atLeastOneFilterProvided`. Grano: claim. D3 (claims vendedor) viable sobre disputes. |
| `GET /v1/claims/...`, `/mediations/...` | **no_verificada** (muertas) | 404 ambas. No usar. |
| Otros `stage` de claims | **no_verificada** | Solo se probo `dispute`. Si A.5 los necesita, pide sonda. |

## Notas para 0.5

- D3 "claims sube >1pt vs semana previa": 0.3 entrega el endpoint y el
  total (22 disputes hoy); la ventana y el computo los fija el acta.
- D5 "pendiente = UNANSWERED + ventana": endpoint verificado; la
  ventana la fija el acta.
- Frescura: respuestas en vivo (sin `fetched_at` de origen; A.2/A.4
  sellan `observed_at` y declaran `fetched_at` = instante de llamada).

## Archivos

- `shapes.json`: shapes compilados de las sondas (scrubbed: sin
  tokens, textos truncados, sin order_id).
