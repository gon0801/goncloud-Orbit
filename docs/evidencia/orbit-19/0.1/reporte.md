# ORBIT 19 / 0.1 — Inventario de catalogo (SKU / margen / lotes)

observed_at Orbit: **2026-09-06 21:00:14.892102 UTC** (`orbit_read`,
`SET TIME ZONE 'UTC'`).
observed_at bridge: **2026-09-06 21:05:26.368628 UTC** (snapshot sqlite
`mode=ro`).
Ventana margen: **[2026-02-20, 2026-08-22)**; `ledger_fresco_at` =
2026-09-06 08:15:04.628154+00.
Hoy UTC: 2026-09-06; corte madurez `hoy - 15` = 2026-08-22.

Fuente Orbit: `ssh goncloud 'docker exec -i orbit-db-1 psql -U orbit_read -d orbit -v ON_ERROR_STOP=1'`.
Fuente bridge: backup API sqlite a `/tmp/bridge-snapshot-orbit19.db` + SELECT;
snapshot borrado al terminar (`SNAPSHOT_DELETED_REMOTE=yes`).
Cero mutacion Amazon. Cero ingest listings. Cero `docker cp` al contenedor app.
Cero escritura en bridge/accounting.

SQL: `select.sql` (corte en `ad_entity.state`; el cache real es
`ad_entity_state.status`) + `select-restante.sql`.
Salida: `select.out.txt`. Bridge: `bridge-omisiones.txt`.
Ambiguos: `multilisting.csv` (115 productos).

## Conteos por mercado (selector actual)

JOIN identico al de `app/fabrica_web.py`. Causas mutuamente excluyentes
en el orden del SELECT: multilisting, luego sin ventas, luego &lt;30 fechas,
luego otras guardas, luego sin seller_sku. Los elegibles son 1 listing +
margen_neto_pct NOT NULL + seller_sku no vacio.

| Mercado | listings | productos | elegibles_selector | multilisting | sin_ventas | menos_30 | otras_guardas | sin_sku |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| amazon_mx | 342 | 249 | 3 | 71 | 84 | 91 | 0 | 0 |
| amazon_us | 176 | 119 | 2 | 44 | 48 | 25 | 0 | 0 |

Cuadre: MX 3+71+84+91+0+0 = 249. US 2+44+48+25+0+0 = 119.
Listings Orbit: 342+176 = 518. Cero sin seller_sku, cero sin ASIN, cero sin precio.

### Comparacion vs spec 2026-09-06

Spec (`docs/superpowers/specs/2026-09-06-catalogo-campanas-abierto-design.md`):
MX 249 productos / 3 elegibles; 84 sin ventas; 91 &lt;30 fechas; 71 multilisting.
US 119 / 2; 48 sin ventas; 25 &lt;30 fechas; 44 multilisting.
Ventana [2026-02-20, 2026-08-22).

**No diverge.** Misma fecha, mismos conteos.

### Elegibles actuales (5)

| product_id | odoo_sku | platform | listing_id | asin | margen_neto_pct | dias_con_venta |
|---|---|---|---:|---|---:|---:|
| 333 | NH-PERS-CAR-AZU-CEN-DOR | amazon_mx | 1190 | B0BXHS56ZH | 40.80 | 40 |
| 335 | NH-PERS-CAR-AZU-COR-DOR | amazon_mx | 1206 | B0BXHVT1MG | 43.30 | 32 |
| 1621 | PERS-CAR-AZU-SAN-DOR | amazon_mx | 1204 | B0BXHV2D76 | 42.11 | 52 |
| 359 | NH-PERS-NOG-SIN-CEN-DOR | amazon_us | 1461 | B0BVQFLTLQ | 37.32 | 32 |
| 369 | NH-PERS-NOG-SIN-VBU-DOR | amazon_us | 1463 | B0BVQKMXXP | 38.58 | 32 |

Abrir seleccion por publicacion desbloquea, por mercado, los cortados solo
por multilisting (MX 71, US 44), mas sin ventas de un listing (MX 84, US 48)
y muestra &lt;30 fechas de un listing (MX 91, US 25). No se suma MX+US.
Ninguno de esos bloqueos es identidad invalida: todos tienen ASIN y seller_sku.

## IDs ambiguos (multilisting)

115 productos con 2–4 listings en el mismo mercado
(MX 71 productos / 164 listings; US 44 / 101).
Lista completa: `multilisting.csv` y seccion MULTILISTING de `select.out.txt`.

No se puede elegir el primero en silencio: cada listing tiene ASIN y
seller_sku distintos.

Muestra peor caso (4 listings):

| platform | product_id | odoo_sku | listing_ids | asins |
|---|---:|---|---|---|
| amazon_mx | 245 | NH-EUM-VIN-CEN-DOR | 1036,1052,1115,1231 | B086M31F8H, B0877XXNR5, B0BLMHQ7TG, B0C9PDL3PW |
| amazon_mx | 246 | NH-EUM-VIN-CEN-PLA | 1055,1112,1155,1268 | B08NT1YSBJ, B0BHCSP6CK, B0BXCN6K33, B0CJTDNM4X |
| amazon_mx | 749 | SET-EUM-VIN-SAN-PLA | 1057,1111,1160,1250 | B08NTBY186, B0BHCR13Q1, B0BXCPQGNP, B0CJT5D4VM |
| amazon_mx | 832 | SET-PLA-M-SAN-DOR | 1027,1033,1095,1219 | B0849JWYD8, B084B1CYCW, B0BG7V393W, B0C8S6FDKP |
| amazon_us | 138 | NH-CAR-AZU-CEN-PLA | 1371,1387,1472,1490 | B08NTD3716, B09LLKKMRW, B0CH956ZLG, B0CPW8ZN6G |
| amazon_us | 158 | NH-CAR-AZU-VBU-PLA | 1369,1388,1476,1489 | B08NTBVTBM, B09LLP8FR7, B0CH97FGSY, B0CPW7X2LK |
| amazon_us | 1740 | SET-CAR-AZU-SAN-PLA | 1367,1389,1474,1488 | B08NST8LNS, B09LM7HXZR, B0CH96ZVPF, B0CPW7KKMS |

`campana_grupo_producto` hoy tiene PK `(grupo_id, product_id)`: un segundo
listing del mismo producto no cabe. Reserva de migracion = 0.2, no se
implementa aqui.

## IDs omitidos

### 1. Bridge: listings sin mapa Odoo

Snapshot READ-ONLY `/mnt/data/appdata/bridge/data/bridge.db`.
Union por `amazon_sku_mapping.seller_sku` (PK). Unir por texto de SKU
sigue prohibido.

| marketplace_name | listings | seller_sku_distintos | con_fila_mapa | sin_fila_mapa |
|---|---:|---:|---:|---:|
| amazon_mx | 548 | 548 | 342 | 206 |
| amazon_us | 261 | 261 | 176 | 85 |
| TOTAL | 809 | 735 | 518 | 291 |

`amazon_sku_mapping`: 455 filas, las 455 con `odoo_default_code` no vacio.
Listings Orbit (518) = listings bridge con mapa (518). Exacto.

Omitidos: **291 listings / 280 seller_sku distintos** (11 SKU aparecen en
ambos mercados). Por status:

| marketplace | Active | Inactive | Incomplete |
|---|---:|---:|---:|
| amazon_mx | 20 | 111 | 75 |
| amazon_us | 3 | 24 | 58 |

23 Active sin mapa (20 MX + 3 US) son los relevantes para anunciar.
Lista completa Active y top 50 de 291: `bridge-omisiones.txt`.
Muestra Active MX: `1D-MM44-H6BY` / B0D9D4MVX8; `NT-PPM2-94N3` / B086M53P42
(qty 199); `OZ-KHZX-VIQF` / B0849KM5KW (qty 99);
`NH-CAR-AZU-CEN-DOR` / B0GR8SDFPZ (seller_sku = odoo_sku, qty 1).
Muestra Active US: `H7-DQQ6-NW00` / B0B6S5DHSG; `ST-MV02-LRFL` / B0B385R1HJ;
`VO-0U7E-EWSL` / B0B385HRZJ.

No se inventa producto/SKU. Deben mostrarse como pendientes de vinculacion.

Tablas inventory/stock/quantity/fba en el bridge (solo inventario, no usadas
en 0.1): `amazon_fba_inventory` (2142, fetched_at 2026-09-06 18:36–18:37 UTC),
`amazon_inventory_cache` (806, max updated_at 2026-08-10 06:14:34 — viejo),
`amazon_inventory_cache_old`, `amazon_inventory_cache_staging`,
`inbound_stock_deltas`. Disponibilidad comercial queda para 0.3/B.3.

### 2. Product ads sin listing_id

`ad_entity.kind='product_ad'` LEFT JOIN `ad_entity_state`.

| platform | status | product_ads | sin_listing_id | con_listing_id |
|---|---|---:|---:|---:|
| amazon_mx | ARCHIVED | 1362 | 1063 | 299 |
| amazon_mx | ENABLED | 4768 | 1168 | 3600 |
| amazon_mx | PAUSED | 1579 | 429 | 1150 |
| amazon_us | ARCHIVED | 264 | 264 | 0 |
| amazon_us | ENABLED | 4090 | 920 | 3170 |
| amazon_us | PAUSED | 464 | 23 | 441 |

Totales: MX 7709 ads (2660 sin listing_id); US 4818 (1207 sin listing_id).
ENABLED sin listing: MX 1168, US 920. Son anuncios Amazon cuyo ASIN/SKU
no esta en `listing` de Orbit (incluye los 291 sin mapa y ads viejos).
No se reparte su gasto al catalogo. No se les asigna producto.

### 3. Sales ledger sin product_id

Ventana [2026-02-20, 2026-08-22): MX **7** sin product_id / 734 con;
US **0** / 357. No entran a `v_margen_producto` (la vista exige product_id
NOT NULL). Las 7 (todas amazon_mx):

| id | event_date | order_id (ultimos 4) | sku | amount MXN |
|---:|---|---|---|---:|
| 848 | 2026-03-15 | …7034 | NA-15WK-8U1A | 988 |
| 2042 | 2026-04-11 | …5003 | NA-15WK-8U1A | 988 |
| 3164 | 2026-05-14 | …0232 | NT-PPM2-94N3 | 1988 |
| 3358 | 2026-05-21 | …0600 | SET-CAR-AZU-PEZ-DOR | 989 |
| 3688 | 2026-06-08 | …0217 | 6M-0KK7-8OAK | 1988 |
| 6701 | 2026-07-14 | …8227 | OZ-KHZX-VIQF | 1988 |
| 6931 | 2026-07-22 | …3830 | L2-N1V2-3RZG | 1688 |

`NT-PPM2-94N3` y `OZ-KHZX-VIQF` estan Active sin mapa en el bridge.
`6M-0KK7-8OAK` esta Inactive sin mapa. Coherente: venta sin producto
porque el seller_sku no cruza `amazon_sku_mapping`.

## Lotes fabrica recuperables

`fabrica_lote`: **0 filas**. `fabrica_lote_paso`: **0 filas**.

No hay lote v1/v2 que preservar ni recuperar. Nada incierto, nada con
`external_id`. La reversa v2 sigue siendo requisito de A.1 (lector v1/v2
antes de crear), aunque hoy no haya filas que migrar.

## Que puede continuar sin dueno

Apertura de catalogo (fase A) **no depende de Ads**: los 518 listings Orbit
tienen identidad (ASIN + seller_sku); el bloqueo actual es de selector
(margen / multilisting), no de mapa faltante en lo ya ingerido.

Lo que SI espera dueno: D1–D4 en 0.2 (prioridad, target manual, nuevas vs
existentes, muestra limitada). Fase A no arranca sin 0.2.
0.3 (contratos Ads / disponibilidad) puede avanzar en paralelo a 0.1.

## Pendiente

Nada de implementacion. Cero codigo de producto, cero migracion, cero
selector v2. El Lead integra este inventario al plan; 0.2 cierra contrato.
