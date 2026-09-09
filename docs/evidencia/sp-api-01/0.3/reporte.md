# SP-API 01 / 0.3 — Sonda Listings Items

Fecha: 2026-09-09 05:42 UTC. Solo lectura (1 GET + 1 refresh LWA).

## Comando exacto

```bash
ssh goncloud 'docker exec -i orbit-app-1 python3 - --fuente listings --mercado amazon_mx --sku GE-YXVC-R5BR --seller-id A29XRL07YRN0L' < tools/sonda_spapi.py
```

`--seller-id` = `accountInfo.id` del perfil Ads de Amazon MX (ver 0.5).
`GE-YXVC-R5BR` tiene mapeo en `amazon_sku_mapping` (brief).

## Respuesta Amazon

- Endpoint: `GET /listings/2021-08-01/items/A29XRL07YRN0L/GE-YXVC-R5BR`
  (`marketplaceIds=A1AM78C64UM0Y8`), version **2021-08-01**: **200**.
- `claves_top`: `sku`, `summaries`.
- `claves_item`: `asin`, `conditionType`, `createdDate`, `itemName`,
  `lastUpdatedDate`, `mainImage`, `marketplaceId`, `productType`, `status`.
- Rate limit: `x-amzn-ratelimit-limit: 5.0`.
- Aviso deprecacion: ninguno.
- D2 citada: lectura de estado del listing; precio/stock del bridge no se
  tocan (esta sonda no escribe nada en ningun lado).

## Veredicto

| fuente | estado | motivo |
|---|---|---|
| listings MX | **verificada** | 200 con `summaries` del item, 1 llamada |
