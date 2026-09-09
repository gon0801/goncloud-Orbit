# SP-API 01 / 0.4 — Sonda Inventario FBA + comparacion bridge

Fecha: 2026-09-09 05:49 UTC. Solo lectura (22 GET + 1 refresh LWA para la
sonda; 22 GET + 1 refresh para el listado de conciliacion; snapshot bridge
en `mode=ro`).

Correccion (ronda 2): la version anterior reporto 50 summaries porque
`siguiente_token` no veia `pagination` como hermano de `payload`
(`fbaInventory.json`: `GetInventorySummariesResponse` = `payload` +
`pagination` + `errors`). El "21x" era un artefacto de la sonda, no una
diferencia real. Fix con test en el mismo PR.

## Comando exacto

```bash
ssh goncloud 'docker exec -i orbit-app-1 python3 - --fuente inventario --mercado amazon_mx --max-paginas 30' < tools/sonda_spapi.py
```

## Respuesta Amazon

- Endpoint: `GET /fba/inventory/v1/summaries` (`granularityType=Marketplace`,
  `granularityId=marketplaceIds=A1AM78C64UM0Y8`), version **v1**: **200** en
  las 22 paginas.
- **22 paginas (21 x 50 + 21), 1071 summaries, ultima sin token: recorrido
  completo**, sin `next_token_repetido` ni `pagina_vacia_con_token`.
- `claves_top`: `granularity`, `inventorySummaries`.
- `claves_item`: `asin`, `condition`, `fnSku`, `lastUpdatedTime`,
  `productName`, `sellerSku`, `stores`, `totalQuantity`.
- Rate limit: `x-amzn-ratelimit-limit: 2.0` en todas.
- Aviso deprecacion: ninguno. D2 citada (ver 0.3).

## Comparacion contra el bridge (regla 10)

Snapshot: `sqlite3.connect("file:.../bridge.db?mode=ro").backup()` (patron
orbit-19 0.3), solo `SELECT`. Bridge fresco 2026-09-09 00:36 UTC; SP-API
leida 05:49 UTC (~5 h despues). Universo SP-API completo por `seller_sku`
(1071 pares sku/qty, 1 GET por pagina) cruzado contra el snapshot:

| sentido | resultado |
|---|---|
| presencia SP-API -> bridge | 1071/1071 presentes, 0 solo-SP-API |
| presencia bridge -> SP-API | 1071/1071 presentes, 0 solo-bridge |
| suma cantidades | SP-API 32803 vs bridge 32795 (dif +8) |
| qty identica por sku | 1063/1071; 8 difieren por 1 unidad |
| `GE-YXVC-R5BR` | ausente en ambas (coherente; no es FBA) |

```sql
SELECT marketplace_name, marketplace_id, COUNT(*), SUM(quantity_available),
       COUNT(CASE WHEN quantity_available > 0 THEN 1 END), MAX(fetched_at)
  FROM amazon_fba_inventory
 GROUP BY marketplace_name, marketplace_id;
```

## Diferencias encontradas (reales)

1. Solo 8/1071 SKUs difieren en cantidad, siempre por 1 unidad (ej.
   `1X-9MPP-46OA` 199 vs 198). Con 5 h entre ambas lecturas, es movimiento
   intradia, no divergencia de fuente.
2. MX y US del bridge son identicos fila a fila en agregados (1071/32795 en
   ambos); espejo ya conocido: orbit-19 0.3 midio 2142 filas en
   `amazon_fba_inventory`, exactamente 1071 x 2.

## Veredicto

| fuente | estado | motivo |
|---|---|---|
| inventario MX | **verificada** | 200, recorrido completo de 22 paginas, universo conciliado |
