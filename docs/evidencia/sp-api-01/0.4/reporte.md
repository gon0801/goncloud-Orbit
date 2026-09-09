# SP-API 01 / 0.4 — Sonda Inventario FBA + comparacion bridge

Fecha: 2026-09-09 05:42 UTC. Solo lectura (1 GET + 1 refresh LWA para la
sonda; 1 GET extra de conciliacion por sku + snapshot bridge en `mode=ro`).

## Comando exacto

```bash
ssh goncloud 'docker exec -i orbit-app-1 python3 - --fuente inventario --mercado amazon_mx' < tools/sonda_spapi.py
```

## Respuesta Amazon

- Endpoint: `GET /fba/inventory/v1/summaries` (`granularityType=Marketplace`,
  `granularityId=marketplaceIds=A1AM78C64UM0Y8`), version **v1**: **200**.
- **50 summaries en pagina unica, sin `nextToken`: recorrido completo.**
- `claves_top`: `granularity`, `inventorySummaries`.
- `claves_item`: `asin`, `condition`, `fnSku`, `lastUpdatedTime`,
  `productName`, `sellerSku`, `stores`, `totalQuantity`.
- Rate limit: `x-amzn-ratelimit-limit: 2.0`.
- Aviso deprecacion: ninguno. D2 citada (ver 0.3).

## Comparacion contra el bridge (regla 10)

Snapshot: `sqlite3.connect("file:.../bridge.db?mode=ro").backup()` a
`/tmp/bridge-snapshot-spapi01.db` (patron orbit-19 0.3), solo `SELECT`.

```sql
SELECT marketplace_name, marketplace_id, COUNT(*), SUM(quantity_available),
       COUNT(CASE WHEN quantity_available > 0 THEN 1 END), MAX(fetched_at)
  FROM amazon_fba_inventory
 GROUP BY marketplace_name, marketplace_id;
```

Bridge 2026-09-09 00:36/00:37 UTC, fresco el mismo dia:

| marketplace | filas | suma qty | >0 | max fetched_at |
|---|---|---|---|---|
| amazon_mx | 1071 | 32795 | 171 | 2026-09-09 00:36:16 |
| amazon_us | 1071 | 32795 | 171 | 2026-09-09 00:37:19 |

Cruce por `seller_sku` (los 50 de SP-API contra el snapshot):

- Presencia: **50/50 presentes** en bridge MX.
- Cantidad: suma SP-API (`totalQuantity`) **1389** = suma bridge
  (`quantity_available`) **1389**; 7 positivos en ambas.
- `GE-YXVC-R5BR`: **ausente en ambas** fuentes FBA (coherente; no es FBA).

## Diferencias encontradas

1. Cobertura: bridge MX trae 1071 filas vs 50 summaries de SP-API (~21x).
   Causa no determinada en esta sonda; la ingesta debera conciliar el
   universo, no asumirlo.
2. MX y US del bridge son identicos (1071/32795/171 en ambos): espejo o
   duplicado del lado bridge, se declara.
3. Cantidades de los 50 comunes: **identicas** (1389 = 1389).

## Veredicto

| fuente | estado | motivo |
|---|---|---|
| inventario MX | **verificada** | 200, pagina unica completa, 50/50 conciliados en cantidad |
