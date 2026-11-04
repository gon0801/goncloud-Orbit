# SP-API 01 / A.2b — Sonda FULFILLMENT + PROCEEDS (solo lectura)

Fecha: 2026-09-09 UTC. Tres GET MX (`lastUpdatedAfter`, `maxResultsPerPage=5`,
`includedData=FULFILLMENT,PROCEEDS`) + 3 refresh LWA, desde `orbit-app-1` por
stdin. Salida: solo claves ordenadas y conteos, jamás valores (cero PII; sin
BUYER ni RECIPIENT en ningún `includedData` pedido).

## Comando exacto

```bash
ssh goncloud 'docker exec -i orbit-app-1 python3 -' < /tmp/sonda_a2b.py
```
(Sondas 2 y 3: variantes que solo agregan presencia/valores enum por orden.)

## Resultado 1 (ventana 1 día): 200, 5 órdenes

- `status: 200`, `x-amzn-ratelimit-limit: 0.0056`, sin avisos de deprecación.
- `claves_top`: `lastUpdatedBefore`, `orders`, `pagination`
  (`pagination.nextToken` presente).
- `claves_orden`: `createdTime`, `fulfillment`, `lastUpdatedTime`, `orderId`,
  `orderItems`, `proceeds`, `programs`, `salesChannel` (las tres intermedias
  son nuevas vs E/0.1, que pedía sin `includedData`).
- `fulfillment`: `deliverByWindow`, `fulfilledBy`, `fulfillmentServiceLevel`,
  `fulfillmentStatus`, `shipByWindow`.
- `proceeds`: `breakdowns`, `grandTotal`; `grandTotal`: `amount`,
  `currencyCode`.
- Sin 403: FULFILLMENT y PROCEEDS **no exigen permiso ni rol especial** en
  esta cuenta; no hubo exigencia de Restricted Data Token.

## Resultados 2 y 3 (valores enum): ventanas vacías

Dos GET posteriores (1 día y 7 días) devolvieron `200` con `orders: []`, así
que no se capturó ningún valor de `fulfillmentStatus` / `fulfilledBy` /
`fulfillmentServiceLevel`. No se reintentó para no quemar llamadas contra el
límite 0.0056/s: el mapeo guarda el texto tal cual (`order_status`,
`fulfillment_channel` son TEXT sin validación), así que ningún valor futuro
puede romper la ingesta.

## Decisiones de mapeo (para la ingesta)
- `fulfillment.fulfillmentStatus` → `order_status`.
- `fulfillment.fulfilledBy` → `fulfillment_channel` (análogo directo del
  `FulfillmentChannel` AFN/MFN de v0: quién cumple; `fulfillmentServiceLevel`
  es velocidad, no canal).
- `proceeds.grandTotal` (`amount` + `currencyCode`) → total con moneda.
- `breakdowns`, `programs`, ventanas de entrega: se ignoran (fuera de A.2b).
- Ausente = NULL, como hoy; `sanear` sigue filtrando comprador/dirección
  aunque Amazon los mandara.

> Nota (revisión PR #240): el mapeo `fulfillment.fulfillmentStatus` →
> `order_status` quedó corregido: el estado de envío vive en su propia
> columna `fulfillment_status` (dominio de envío) y `order_status` guarda
> SOLO el ciclo del pedido (`orderStatus` plano). Detalle en
> `docs/evidencia/sp-api-01/A.2/reporte.md`, sección "Revisión del lead PR #240".
