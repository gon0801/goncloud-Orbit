# MARGEN ESTIMADO 01 · 0.1 — Inventario de fuentes

Consulta: 2026-09-08 UTC. Alcance: transacciones `REPEATABLE READ READ ONLY`
en Orbit y aperturas SQLite `mode=ro` en bridge/accounting. No se escribieron
campañas, precios, datos de las fuentes ni secretos.

## Resultado

| Fuente | MX | US | Dictamen |
|---|---:|---:|---|
| `listing` Orbit | 342 listings, 342 con precio/SKU, 249 productos | 176 listings, 176 con precio/SKU, 119 productos | Identidad/costo reutilizables; precio no sirve como observación fechada |
| `sku_cost` vigente al 2026-09-08 | 342/342 listings con costo | 176/176 listings con costo | Reutilizable; una moneda de costo, todos `includes_tax=false` |
| `fx_rate` | — | USD→MXN, 215 filas, hasta 2026-09-04 | Reutilizable con su regla actual; edad 4 días en la consulta |
| `amazon_listing_prices` bridge | 548 filas, todas con `fetched_at` actual | 261 filas; 258 con fecha actual, 3 desde 2026-04-13 | Fuente de oferta/precio/canal, pero requiere guardar su observación en Orbit |
| `amazon_sku_mapping` bridge | \- | 455 SKU mapeados a 268 productos en total | Reusar sólo el puente existente; no unir por texto |

`listing` sólo tiene `id, product_id, platform, external_id, seller_sku,
listing_price, price_currency`. No tiene `fetched_at`, canal de fulfilment,
ni historial. La ingesta `app/listings.py` actualiza ese precio y conserva los
listings que ya no aparecen en el origen: una ejecución reciente no demuestra
que el precio guardado sea reciente.

El bridge sí expone `seller_sku, asin, marketplace_id, marketplace_name, price,
quantity, fulfillment_channel, item_name, status, fetched_at`. Por tanto la
identidad prospectiva mínima es oferta `(marketplace, seller_sku, ASIN, canal,
precio, fetched_at)`, no sólo `listing_id`. El bridge no contiene dos canales
ni dos precios para un mismo `(marketplace, ASIN, seller_sku)` en esta muestra;
eso no autoriza a suponer que la cardinalidad será siempre uno.

| Bridge | Canal | Filas | Con precio | Hallazgo |
|---|---|---:|---:|---|
| MX | `AMAZON_NA` | 276 | 275 | 1 sin precio |
| MX | `DEFAULT` | 272 | 198 | 74 sin precio |
| US | `AMAZON_NA` | 83 | 82 | 1 sin precio |
| US | `DEFAULT` | 178 | 121 | 57 sin precio |

La disponibilidad confirma que FBA/FBM siguen grano separado; no se puede usar
cantidad como tarifa. El contrato ya sellado de ORBIT 19 lee FBA desde
`amazon_fba_inventory` y FBM sólo desde `amazon_listing_prices` con
`fulfillment_channel='DEFAULT'`.

## Costo, moneda y vigencia

El accounting origen tiene 2,711 vigencias MXN de 1,089 SKU, todas positivas.
El costo de Odoo se publica como neto de IVA (`includes_tax=false`). No prueba
que incluya importación, embalaje o transporte. Tampoco prueba la unidad vendida:
los esquemas inspeccionados de `product`, `listing`, `sku_cost` y
`accounting.sku_costs` no tienen unidad, multiplicador, kit ni BOM. La fuente de
tasas tiene 215
filas `(MXN, USD)` que Orbit normaliza a `(USD, MXN)`; la conversión de costo
MXN a una estimación USD debe dividir entre esa tasa y conservar fecha/origen.

Por ello el costo vigente sólo es reutilizable después de acreditar que la oferta
vende una unidad equivalente al `product_id` costeado. La fuente de esa relación
debe ser Odoo u otro contrato operativo verificable; no se infiere de que
`seller_sku` y `odoo_sku` tengan texto parecido.

El ledger observado mantiene importes MXN aun para `amazon_us`. Es evidencia
histórica para contrastar, no moneda ni tarifa de una cotización futura.

## Calidad de las fuentes

La consulta de Orbit muestra última ingesta de listings el 2026-09-02, mientras
el bridge observó ofertas el 2026-09-08. Este desfase prueba que un estimador no
puede usar `listing.listing_price` como precio actual. Costos/FX se refrescaron
el 2026-09-07; disponibilidad se cargó el mismo día.

No se fija un TTL inventado. El bloque 0.3 debe usar la fecha real de cada
observación y definir qué ocurre cuando no hay actualización verificable. Ya
hay tres filas US antiguas: son `desactualizada`, no precio actual ni cero.

## Dictamen 0.1

**Viable con cambio de fuente de lectura y contrato de unidad/BOM.** Precio,
canal y fecha deben capturarse como observaciones nuevas por oferta. Costo y FX
pueden reutilizarse con sus reglas actuales sólo tras fijar esa equivalencia de
unidad. No se libera ningún cálculo ni la sustitución de margen observado con
estos hallazgos.

Consultas reproducibles: `select-orbit.sql`; los conteos de bridge/accounting
se obtuvieron con SQLite `mode=ro`, sin IDs, SKU, nombres, precios ni importes
en la evidencia.
