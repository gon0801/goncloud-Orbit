# SP-API 01 / 0.2 — Sonda Product Pricing (MX + US)

Fecha: 2026-09-09 05:42 UTC. Solo lectura (2 GET + 1 refresh LWA por mercado).

## Comandos exactos

```bash
ssh goncloud 'docker exec -i orbit-app-1 python3 - --fuente pricing --mercado amazon_mx --asin B0BXHV2D76' < tools/sonda_spapi.py
ssh goncloud 'docker exec -i orbit-app-1 python3 - --fuente pricing --mercado amazon_us --asin B085VJ8DVX' < tools/sonda_spapi.py
```

ASIN US (`B085VJ8DVX`): `listing.external_id` plataforma `amazon_us`
(`SELECT` con `ORBIT_DSN_READ`, rol read). Nota operativa: desde el
contenedor `127.0.0.1:5432` no rutea; se uso el hostname `orbit-db-1`.

## MX (`B0BXHV2D76`) — version v0, veredicto verificada

- `GET /products/pricing/v0/items/B0BXHV2D76/offers` (`MarketplaceId`
  `A1AM78C64UM0Y8`, `ItemCondition=New`): **200**, 1 oferta.
  `claves_top`: `ASIN`, `Identifier`, `ItemCondition`, `Offers`, `Summary`,
  `marketplaceId`, `status`.
  `claves_item` (Buy Box/competencia): `IsBuyBoxWinner`,
  `IsFeaturedMerchant`, `IsFulfilledByAmazon`, `ListingPrice`,
  `PrimeInformation`, `SellerFeedbackRating`, `SellerId`, `Shipping`,
  `ShippingTime`, `ShipsFrom`, `SubCondition`.
- `GET /products/pricing/v0/competitivePrice`: **200**.
  `claves_top`/`claves_item`: `ASIN`, `Product`, `status`.
- Rate limit: `0.5` en ambos. Deprecacion: ninguna.

## US (`B085VJ8DVX`) — version v0, veredicto verificada

- Item offers: **200** con **0 ofertas** (`claves_item` vacio). Ausencia de
  oferta, no error: el contrato responde y `competitivePrice` si trae dato.
- `competitivePrice`: **200** (`ASIN`, `Product`, `status`).
- Rate limit: `0.5` en ambos. Deprecacion: ninguna.

## Veredicto

| mercado | estado | motivo |
|---|---|---|
| amazon_mx | **verificada** | ofertas con Buy Box + competitivos, 200 |
| amazon_us | **verificada** | 200 en ambas; offers vacio = ausencia declarada |
