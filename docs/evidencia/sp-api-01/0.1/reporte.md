# SP-API 01 / 0.1 — Sonda Orders (v0 + 2026-01-01)

Fecha: 2026-09-09 05:43 UTC. Solo lectura (2 GET + 1 refresh LWA por version).
Salida sanitizada por `app/redaction.py`; cero PII (auditoria: ni `buyer*`
ni `@` en toda la salida).

## Comando exacto

```bash
ssh goncloud 'docker exec -i orbit-app-1 python3 - --fuente orders --mercado amazon_mx' < tools/sonda_spapi.py
```

Commit de la sonda: `c159b56` + fix de timestamp (`_ventana_orders` en Zulu,
ver "Incidente 400" abajo).

## v0 — `GET /orders/v0/orders` (version v0)

- Respuesta Amazon: **200**, 3 paginas x 10 ordenes (30), `NextToken` seguido
  sin anomalias hasta el tope `--max-paginas 3` (`limite_max_paginas`:
  recorrido parcial, hay mas paginas).
- `claves_top`: `CreatedBefore`, `NextToken`, `Orders`.
- `claves_item`: `AmazonOrderId`, `EarliestShipDate`, `FulfillmentChannel`,
  `HasRegulatedItems`, `IsAccessPointOrder`, `IsBusinessOrder`,
  `IsGlobalExpressEnabled`, `IsISPU`, `IsPremiumOrder`, `IsPrime`,
  `IsReplacementOrder`, `IsSoldByAB`, `LastUpdateDate`, `LatestShipDate`,
  `MarketplaceId`. Sin claves de comprador ni direccion.
- Aviso deprecacion: fijo `v0 deprecada; retiro 2027-03-27; sucesora
  2026-01-01` (Amazon no mando aviso en headers).
- Rate limit: `x-amzn-ratelimit-limit: 0.0167`.
- Bugs TRASPASO-1: **ausentes** en este recorrido (ningun `NextToken`
  repetido, ninguna pagina vacia con token en 3 paginas por version).
- Incidente 400 (sin maquillar): la primera corrida mando `CreatedAfter` con
  `+00:00` y v0 respondio `400 InvalidInput "timestamp must follow
  ISO8601"`. Se corrigio la sonda a Zulu (`...Z`) y v0 respondio 200. La
  2026-01-01 aceptaba ambos formatos.

## 2026-01-01 — `GET /orders/2026-01-01/orders` (searchOrders, version 2026-01-01)

- Respuesta Amazon: **200**, 3 paginas x 10 ordenes (30), `paginationToken`
  seguido sin anomalias hasta el tope (`limite_max_paginas`).
- `claves_top`: `createdBefore`, `orders`, `pagination`.
- `claves_item`: `createdTime`, `lastUpdatedTime`, `orderAliases`,
  `orderId`, `orderItems`, `salesChannel` (sin `buyer` ni `recipient`: no se
  pidio `includedData`).
- Aviso deprecacion: ninguno en headers ni cuerpo.
- Rate limit: `x-amzn-ratelimit-limit: 0.0056` (coincide con el modelo
  oficial: 0.0056 req/s, burst 20).
- Sin 403: no hubo exigencia de Restricted Data Token en este recorrido.

## Veredicto

| version | estado | motivo |
|---|---|---|
| v0 | **verificada** | 200 x 3 paginas, paginacion sana, cero PII |
| 2026-01-01 | **verificada** | 200 x 3 paginas, `paginationToken` sano, cero PII |
