# Módulos Avanzados — especificación master

> Extraído de `MCP2_Documento_Master_Modulos_Avanzados.docx` (v1.0, 21-ago-2026)
> y reformateado a Markdown. El .docx original está en este mismo directorio.
> Escrito para "MCP2"; aplica a **Orbit** como sistema nuevo. Donde contradice
> las reglas de la autopsia (Traspaso 2), manda la autopsia — ver notas en
> `docs/CONTEXTO.md` § "Módulos avanzados".

**Alcance:** 5 módulos — Repricing, Creación de Campañas por API, Reputación,
Promociones, Envíos. Plataformas: Amazon + Mercado Libre.

## Principios de diseño del documento

- **API-first**: toda acción ejecutable vía API de las plataformas.
- **Reglas configurables**: la lógica de negocio no se hardcodea, se expone
  como reglas.
- **Trazabilidad completa**: todo cambio de precio, campaña o promoción queda
  registrado.
- **Fail-safe**: con error de API o datos incompletos, no se ejecutan acciones
  destructivas.
- **Multi-plataforma nativa**: Amazon y MeLi como ciudadanos de primera clase.

## Capas

| Capa | Responsabilidad |
|---|---|
| Presentación | UI / Dashboard / Alertas |
| Orquestación | Reglas de negocio y flujos (motor de reglas, schedulers) |
| Servicios de dominio | Lógica de cada módulo (Repricer, Campaign Builder…) |
| Integraciones | Amazon SP-API, MeLi API, Ads API |
| Datos | Persistencia y cache |

## Módulo 1 — Motor de Repricing

Ajustar precios en Amazon y MeLi para maximizar margen de contribución,
proteger Buy Box/visibilidad y respetar margen mínimo.

- Reglas por producto, categoría o globales. Precio óptimo con: COGS, fees,
  margen mínimo, competencia, elasticidad estimada.
- Modos: agresivo, equilibrado, conservador, manual override. Floor/ceiling.
- Historial completo de cambios con motivo y resultado. Dry-run antes de
  cambios masivos. Alerta cuando la competencia rompe el margen mínimo.

**Modelos:** `RepriceRule(id, product_id, platform, strategy, min_margin_pct,
floor_price, ceiling_price, competitor_offset, active)` ·
`PriceHistory(id, product_id, platform, old_price, new_price, reason,
triggered_by, timestamp, result)` · `CompetitorPrice(id, product_id,
competitor_asin/item_id, price, timestamp, source)` ·
`PriceFloor(product_id, platform, absolute_floor, margin_based_floor)`

**Estrategias:** Match Competitor (offset configurable), Beat Competitor (X% o
$Y debajo), Margin Target, Buy Box Oriented (Amazon), Inventory Aware (stock
bajo → subir), Time-based (hora/día).

**APIs:** Amazon SP-API Listings Items, Product Pricing (GetPricing,
GetCompetitivePricing), Catalog Items. MeLi: Items API (PUT /items/{id}),
Search/competencia, fees calculator.

**Reglas de seguridad:** nunca bajar del floor; máx X cambios por producto en
24h (anti-thrashing); API falla → marcar pendiente, no reintentar agresivo;
margen < mínimo → alertar y no ejecutar; dry-run obligatorio en cambios de
>50 productos.

## Módulo 2 — Creación de Campañas por API

Creación automática e inteligente de campañas según estado del producto.

**Tipos:** Amazon SP (Auto, Manual Exact/Phrase/Broad, Product Targeting,
Category Targeting), SB, SD. MeLi: Product Ads, Brand Ads (si aplica).

**Lógica por perfil de producto:**

- Nuevo (0–20 ventas): Auto + Broad + Product Targeting + Target ACoS alto.
- Crecimiento: Exact de keywords ganadoras + Phrase + ACoS medio.
- Maduro: Exact + Brand defense + ACoS bajo/rentabilidad.
- Stock bajo: reducir budgets o pausar no críticas.
- Ranking orgánico fuerte: bajar agresividad en branded terms.

**Parámetros:** target ACoS/ROAS, daily/total budget, bidding strategy,
placement multipliers, negativos iniciales + reglas, dayparting, fechas.

**Modelos:** `CampaignTemplate(id, name, platform, structure_type,
default_target_acos, default_budget_rules)` · `Campaign(id, external_id,
product_ids, platform, type, status, target_acos, budget, created_by)` ·
`CampaignTarget(id, campaign_id, target_type, match_type, bid, status)` ·
`CampaignCreationLog(id, request_payload, response, status, error, timestamp)`

**Flujo:** solicitud → consulta ventas/margen/stock/ACoS/ranking → selecciona
template → genera targets iniciales → crea vía API → registra IDs externos →
inicia monitoreo.

## Módulo 3 — Trackeo de Reputación

Monitoreo continuo de salud reputacional por publicación, alertas tempranas y
acciones correctivas.

**Métricas:** Amazon — rating promedio, total reviews, reviews nuevas 24/48h,
Account Health, Buy Box % (cada pocas horas). MeLi — reputación vendedor
(color), calificación del ítem, reclamos/mediaciones, tiempo de respuesta.
Ambas — tendencia de rating. Frecuencia diaria salvo Buy Box.

**Funcionalidades:** dashboard por producto/cuenta, alertas configurables
(rating < 4.2, review de 1 estrella, aumento de reclamos), historial,
vinculación con acciones (pausar campañas, notificar, ticket de respuesta),
detección básica de reviews falsas/ataques, integración con envíos.

**Modelos:** `ReputationSnapshot(id, product_id, platform, rating,
review_count, date, extra_metrics JSON)` · `ReviewEvent(id, product_id,
platform, rating, text_snippet, date, sentiment, action_taken)` ·
`ReputationAlert(id, product_id, type, severity, message, resolved, created_at)`

**Fuentes:** Amazon SP-API (Catalog, Orders, Reports) + scraping controlado si
hace falta; MeLi Items, Questions, Orders+Claims, reputación de usuario,
webhooks.

**Acciones automáticas:** rating < umbral → alerta + sugerir pausar campañas
agresivas; review 1–2★ → notificación inmediata + template de respuesta;
aumento de reclamos → revisar logística; caída sostenida → marcar listing para
revisión.

## Módulo 4 — Central de Promociones

Creación, programación, monitoreo y análisis de impacto de promociones,
siempre con el efecto sobre margen de contribución adelante.

**Tipos:** Amazon — Percentage Off, Money Off/Coupon, Lightning Deal, Best
Deal/Prime Exclusive. MeLi — descuento %/monto, cuotas, Full/envío gratis,
ofertas del día. Ambas — bundles (si se implementa).

**Funcionalidades:** calendario unificado, simulador de impacto en margen
antes de lanzar, creación vía API, protección de margen mínimo, activación
masiva, reporte de performance real vs proyectado, alertas de promociones que
comen margen.

**Modelos:** `Promotion(id, platform, type, product_ids, discount_value,
start_date, end_date, status, external_id)` · `PromotionSimulation(id,
promotion_id, projected_margin, projected_units, projected_revenue)` ·
`PromotionPerformance(id, promotion_id, units_sold, revenue,
ad_spend_related, real_margin, date)`

**Flujo seguro:** definir → calcular margen proyectado → si < mínimo, bloquea
o pide confirmación explícita → crear vía API → trackear → reporte real vs
proyectado al finalizar.

## Módulo 5 — Seguimiento de Envíos

Panel unificado de envíos Amazon (FBA/FBM) y MeLi (Full, Flex, Mercado
Envíos).

- Estados: pendiente, preparado, en tránsito, en reparto, entregado, demorado,
  problema, devuelto.
- Alertas de demoras/problemas, tiempo promedio por plataforma y tipo,
  vinculación con pedidos/productos/stock, patrones que afectan reputación.

**Modelos:** `Shipment(id, platform, order_id, tracking_number, status,
carrier, shipped_at, delivered_at, estimated_delivery)` ·
`ShipmentEvent(id, shipment_id, status, location, timestamp, description)` ·
`ShipmentAlert(id, shipment_id, type, severity, resolved)`

**APIs:** Amazon SP-API Orders + Order Items, Shipping/Fulfillment, reports de
fulfillment y returns. MeLi: Orders + Shipments + Tracking + Claims.

## Dependencias entre módulos

| Desde | Hacia | Qué comparte |
|---|---|---|
| Márgenes | Repricing | Floor price y margen mínimo |
| Márgenes | Promociones | Simulación de impacto |
| Repricing | Campañas | Cambio de precio afecta ACoS |
| Reputación | Campañas | Pausar/bajar agresividad si rating cae |
| Reputación | Promociones | No promocionar mala reputación |
| Envíos | Reputación | Retrasos → malas calificaciones |
| Envíos | Inventario | Stock disponible |
| Campañas | Promociones | Coordinar presupuesto ads + descuentos |

## Roadmap sugerido por el documento

| Fase | Entregable | Duración | Depende de |
|---|---|---|---|
| 0 | Modelos de datos + auth APIs | 1–2 sem | — |
| 1 | Repricing básico | 3–4 sem | Márgenes |
| 2 | Campañas por API (SP básicas) | 3–4 sem | Ads |
| 3 | Promociones (MeLi + Amazon básica) | 2–3 sem | Márgenes + Repricing |
| 4 | Reputación y alertas | 2–3 sem | APIs items/orders |
| 5 | Envíos unificado | 2–3 sem | Orders APIs |
| 6 | Integraciones cruzadas + dashboards | 2–3 sem | Todo |

**Criterios de terminado:** Repricing cambia precio en ambas plataformas con
historial · Campañas crea Auto + Exact + Product Targeting en Amazon y básica
en MeLi · Reputación muestra rating + historial + ≥3 tipos de alerta ·
Promociones crea descuento y simula margen · Envíos lista ambas plataformas
con estado actualizado.

## Transversales

- **Auth**: LWA + refresh tokens (Amazon), OAuth 2.0 (MeLi); tokens encrypted
  at rest, renovación automática.
- **Rate limits**: colas para llamadas a APIs, backoff exponencial + jitter,
  circuit breaker por plataforma, logging de request/response.
- **Observabilidad**: salud de integraciones, alertas de fallas sostenidas,
  métricas de latencia y volumen.
- **Seguridad**: mínimo privilegio, auditoría de quién/regla ejecutó cada
  cambio, confirmación humana para acciones de alto impacto (cambios masivos
  de precio, borrado de campañas).
- **Stack sugerido por el doc (orientativo)**: Node/TS o Python/FastAPI, cola
  Redis+BullMQ, PostgreSQL, Redis cache.
