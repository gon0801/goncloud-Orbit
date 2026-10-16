# M.3: diseno vs triggers 0028 (decision registrada)

El COMMENT de `datos.sql` ("MeLi en oferta y escenario: ... `seller_sku`
el SKU del miembro que manda") choca con los triggers 0028 impuestos:

1. `estimacion_oferta_listing_coherente`: `oferta.seller_sku` debe casar
   con `listing.seller_sku` (el ancla).
2. `estimacion_escenario_referencias_coherentes`: `escenario.seller_sku`
   debe casar con el listing; `sku_cost.product_id` debia ser el producto
   del listing (el ancla); disponible exige oferta fresca (< 6 h).
3. `estimacion_escenario_disponible_exige_total` (CHECK): disponible exige
   `sku_cost_id` (y validacion) no nulos.

## Lo que se hizo

- `seller_sku` = ancla en oferta, fee y escenario (cumple 1 y 2 sin
  migracion). Desvia de la frase de `datos.sql`; el SKU del que manda se
  recupera por `miembro_product_id -> product.odoo_sku`.
- Migracion 0073 (+ reversa): en `meli`, `sku_cost` debe ser del producto
  de `miembro_product_id`, y el miembro debe estar en
  `v_precio_unidad_miembro` (la misma vista que usa la siembra). Sin ella,
  2 y 3 juntos hacian imposible una fila disponible honesta cuando el que
  manda no es el ancla (refs del ancla con numeros de otro = fila
  mentirosa). Amazon intacto (su rama no cambia).
- Frescura 6 h para todos (sin cambio): el cron de D.4 ordena catalogo
  (M.1) y luego estimacion el mismo dia. Si el catalogo falla o se
  atrasa, la fila sale `oferta_desactualizada` (motivo ruidoso), no
  excepcion.

## Lo que queda debiendo (dueno decide)

- `seller_sku` = SKU del que manda pediria relajar 1 y 2 para `meli` +
  resolver al que manda ANTES de persistir la oferta (hoy se conoce al
  sembrar). Utilidad marginal (el dato ya esta via join); no se hizo.
