# ENVIOS 01 — Acta de sonda real de solo lectura (2026-11-25)

Lectura de solo lectura desde `orbit-app-1` en producción (`goncloud`) contra Amazon SP-API Orders `2026-01-01` y Mercado Libre API (`ClienteMeli`), sin escrituras ni persistencia de PII.

## 1. Amazon SP-API (`GET /orders/2026-01-01/orders`)

Parámetros probados: `includedData=FULFILLMENT,PROCEEDS,PACKAGES`, `maxResultsPerPage=100`, ventana de 30 días (`lastUpdatedAfter`). Resultado: **HTTP 200** en ambos mercados sin rol RDT/PII.

### Cobertura y volumen (últimos 30 días al 2026-11-25)

| Plataforma | Páginas (100/pág) | FBM con paquete | `shipByWindow` presente | `deliverByWindow` presente |
|---|---:|---:|---:|---:|
| `amazon_mx` | 4 | 116 | 116 / 116 (100 %) | 116 / 116 (100 %) |
| `amazon_us` | 1 | 40 | 40 / 40 (100 %) | 40 / 40 (100 %) |

### Claves exactas de ventanas y artículos

- `fulfillment.shipByWindow`: `earliestDateTime`, `latestDateTime` (ISO-8601 UTC).
- `fulfillment.deliverByWindow`: `earliestDateTime`, `latestDateTime` (ISO-8601 UTC).
- `fulfillment.fulfilledBy`: `"MERCHANT"` (FBM), `"AMAZON"` (FBA).
- `fulfillment.fulfillmentStatus`: `"PENDING"`, `"UNSHIPPED"`, `"SHIPPED"`, `"CANCELLED"`.
- `orderItems[]`: `orderItemId`, `product.{asin, sellerSku, title}`, `quantityOrdered` (o `packageItems[].quantity`).
- `packages[]` (presente en FBM; **vacío `[]` en 14/14 pedidos FBA MX**):
  - `packageReferenceId`, `carrier`, `shippingService`, `trackingNumber`, `shipTime`, `createdTime`, `packageStatus.{status, detailedStatus}`.
  - **PII detectada:** en `amazon_us`, `packages[]` incluye `shipFromAddress` con nombre y domicilio del remitente. `app/spapi/client.py::sanear()` hoy no descarta `shipFromAddress` (sobrevive `name`): **se agrega `"shipfromaddress"` a `_PII_GLOBAL`**.

### Pares `(platform, carrier, packageStatus.status, packageStatus.detailedStatus)` observados en 30 días

| Plataforma | `carrier` | `status` | `detailedStatus` | Conteo |
|---|---|---|---|---:|
| `amazon_mx` | `AMZMX` | `DELIVERED` | `DELIVERED` | 79 |
| `amazon_mx` | `DHLC` | `DELIVERED` | `DELIVERED` | 18 |
| `amazon_mx` | `AMZMX` | `UNDELIVERABLE` | `RETURNING_TO_SELLER` | 6 |
| `amazon_mx` | `AMZMX` | `IN_TRANSIT` | `DROPPED_OFF` | 4 |
| `amazon_mx` | `AMZMX` | `IN_TRANSIT` | `PICKED_UP` | 4 |
| `amazon_mx` | `DHL` | `SHIPPED` | `null` | 4 |
| `amazon_mx` | `AMZMX` | `IN_TRANSIT` | `OUT_FOR_DELIVERY` | 1 |
| `amazon_us` | `DHL` | `SHIPPED` | `null` | 40 |

**Conclusiones vinculantes (Regla 8):**
1. `AMAZON_CONFIRMA_ENTREGA = frozenset({("amazon_mx", "AMZMX"), ("amazon_mx", "DHLC")})`.
2. `("amazon_mx", "DHL")` y `("amazon_us", "DHL")` solo reportan `SHIPPED / null` sin confirmación de entrega → clasifican como `enviado_sin_confirmacion` (sin `entrega_vencida`).
3. `IN_TRANSIT` es un estado real de `packageStatus.status` en Amazon MX (`DROPPED_OFF`, `PICKED_UP` → `en_camino`; `OUT_FOR_DELIVERY` → `en_reparto`).
4. Amazon FBA devuelve `packages = []` → clasifica por `fulfillmentStatus` (`SHIPPED` → `enviado_sin_confirmacion`, sin atención).

## 2. Mercado Libre (`MLM`, `GET /orders/search`, `GET /shipments/{id}`, `GET /shipment_statuses`)

- `GET /users/me`: sitio `MLM`, ~700 pedidos en historial.
- `GET /orders/search?seller={id}&sort=date_desc&limit=50`: devuelve `paging.{total, offset, limit}` y `results[]` con `id`, `pack_id`, `status` (`paid`, `cancelled`), `date_created`, `date_last_updated`, `shipping.id`, `tags` (`delivered`, `not_delivered`, `pack_order`, etc.) y `order_items[].{item.{id, seller_sku, title}, quantity}`.
- `GET /shipments/{id}` (muestra de 30 envíos recientes):
  - `logistic_type`: 30/30 `xd_drop_off` (`tracking_method = "Agencias Mercado Libre"`).
  - Pares `status/substatus` observados: `pending/buffered` (1), `ready_to_ship/ready_to_print` (1), `ready_to_ship/dropped_off` (5), `shipped/null` (1), `shipped/out_for_delivery` (2), `shipped/soon_deliver` (1), `shipped/at_the_door` (1), `shipped/waiting_for_withdrawal` (1), `delivered/null` (17), `cancelled/null` (1).
  - `shipping_option.estimated_handling_limit` vino `null` en `xd_drop_off`; `shipping_option.estimated_delivery_time.date` y `.offset.date` sí vienen poblados; `status_history.date_delivered` y `status_history.date_returned` entregan fechas reales de entrega/devolución.
  - **PII detectada (descartar estrictamente):** `receiver_address`, `sender_address`, `receiver_id`, `sender_id`, `destination`, `origin`, `comments`, `buyer`.
- `GET /shipment_statuses` (HTTP 200, catálogo oficial de 15 estados):
  - `ready_to_ship`: `dropped_off`, `picked_up` (ya entregado a agencia/colecta → `en_camino`); `delayed`, `stale` (despacho atrasado reportado directo por MeLi → `por_despachar` con `despacho_vencido`).
  - `shipped`: `out_for_delivery`, `soon_deliver`, `at_the_door`, `in_delivery_zone` → `en_reparto`; `waiting_for_withdrawal` → `esperando_recoleccion` (en agencia esperando al comprador, sin falsa demora de paquetería); `bad_address`, `refused_delivery`, `not_localized`, `contact_with_carrier_required` → `problema`.
  - `not_delivered`: `returning_to_sender`, `returned_to_hub` → `en_devolucion` (si `status_history.date_returned` no es nulo → `devuelto`); resto → `problema`.
