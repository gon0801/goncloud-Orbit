# REPUTACION 01 / 0.1 — Inventario de alcance

Fecha: 2026-09-07/08 UTC. Lead. Solo lectura, cero mutaciones.

Sello: Amazon **inventariado** (518/518 listings con mapa Odoo, 1
divergencia reportada). MeLi **inventariado** (65/65 items con mapa,
8 items del mapa fuera de la API, 13 SKUs fuera de Odoo). D2
(listings propios vinculados) es viable en ambas plataformas.

## Conteos

| lado | n |
|---|---|
| Orbit `listing` amazon_mx | 342 (249 productos) |
| Orbit `listing` amazon_us | 176 (119 productos) |
| Orbit listings sin seller_sku / sin odoo_sku | 0 / 0 |
| Bridge `amazon_sku_mapping` | 455 |
| Orbit con mapa Odoo (por seller_sku) | 518/518 |
| MeLi API `items/search?scan` | 65 items |
| Bridge `sku_mapping` (meli/MLM) | 336 filas, 73 items, 313 SKUs |
| Items API sin mapa | 0 |
| Items del mapa fuera de la API | 8 (dados de baja o pausados; lista abajo) |
| SKUs del mapa en Odoo Orbit | 300/313 (13 fuera; lista abajo) |
| Bridge `meli_sku_mapping` | 0 filas (vacia, no usar) |
| Bridge `meli_listings_cache` | 137 filas `active` pero `updated_at` 2026-05-01: STALE, no fuente |

## Divergencias (reportadas, nunca rellenadas)

- ASIN distinto mapa-vs-Orbit (1): seller_sku `XM-20QN-2YJR`
  (odoo `NH-GAM-NEG-COR-PLA`, amazon_mx): Orbit `B0D9F4V1CC`,
  mapa `B0851SSBL6`. Acta 0.5 decide cual manda.
- Items del mapa fuera de la API (8): MLM2727257503, MLM2787902225,
  MLM2787930515, MLM3014267840, MLM4733803966, MLM4734057258,
  MLM4749745832, MLM5209074728.
- SKUs del mapa fuera de Odoo Orbit (13): NH-BLA-BRO-PEZ-DOR/PLA,
  NH-CAR-BLA-PEZ-DOR/PLA, NH-CAR-ROJ-PEZ-DOR/PLA,
  NH-GAM-AZU-PEZ-DOR/PLA, NH-GAM-NEG-PEZ-DOR/PLA, NH-ITA-PEZ-DOR/PLA,
  NH-PLA-CH-PEZ-PLA.

## Archivos

- `orbit-select.sql` / salidas en los .tsv (volcados completos).
- `orbit-listings.tsv`: 518 (platform|asin|seller_sku).
- `bridge-amz-map.tsv`: 455 (seller_sku, odoo, asin).
- `bridge-sku-map.tsv`: 336 (channel, sku, item, site).
- `orbit-odoo-skus.txt`: 1089.
- `meli-api-items.txt`: 65.
