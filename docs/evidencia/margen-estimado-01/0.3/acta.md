# MARGEN ESTIMADO 01 · 0.3 — Acta de cierre del bloque 0

Fecha: 2026-09-08 UTC. Revisión de las evidencias 0.1 y 0.2. Este acta no
autoriza implementación A/B, cambios a Ads, precios o campañas.

## Lo que ya está comprobado

1. Existe una identidad verificable de oferta en bridge, con marketplace, SKU,
   ASIN, canal, precio y `fetched_at`. Orbit todavía no conserva las tres últimas
   dimensiones; `listing_price` es mutable y puede estar desfasado.
2. Todos los listings Orbit de MX y US tienen costo vigente MXN, declarado neto
   de IVA. El responsable del negocio confirma que no hay kits: cada listing
   actual tiene `product_id` y vende una unidad del producto costeado. La tasa
   USD→MXN vigente en la consulta tenía cuatro días de edad; Orbit ya sabe
   resolverla con fecha y sin inventar una tasa.
3. La Product Fees API se autentica con el loader existente y devuelve total y
   detalle para FBM de MX/US y FBA MX/US cuando usa oferta, canal y precio fresco
   de bridge. El total y sus detalles son una misma representación del cargo y
   se deben reconciliar, nunca sumar dos veces.
4. La liquidación histórica contiene fees, reembolsos y retenciones. Para MX,
   el responsable declara persona física y la política Amazon vigente más las
   retenciones observadas acreditan IVA 8% e ISR 2.5% sobre `item_price` sin
   impuesto, con ISR liquidado mensualmente. `isr_withheld` sigue sin
   `order_id`, por lo que el escenario guarda la retención estimada y concilia
   contra el certificado mensual; no prorratea el bulto histórico.
5. Finances Amazon es accesible en lectura y separa cobro de envío de cargos MFN
   variables. Que Amazon cobre el envío al cliente no convierte esos cargos en
   cero ni provee una tarifa prospectiva por oferta.
6. El bridge sincroniza precios cuatro veces al día. Precio vigente es el que
   tenga `fetched_at` dentro de seis horas; una observación más vieja es
   `desactualizada` y no puede producir contribución.
7. El responsable del negocio confirma que el COGS Odoo incluye importación,
   transporte de entrada y embalaje; no hay kits. Esos componentes no se suman
   por segunda vez.

## Alcance y exclusiones selladas

| Mercado/canal | Política sellada | Resultado antes de una venta |
|---|---|---|
| MX FBA | `amazon_mx_pf_rfc_valid_2026_01`: COGS completo, fee oficial, IVA 8% e ISR 2.5%; precio bridge fresco ≤6h | Contribución estimada completa, en MXN |
| MX FBM | La guía depende de pedido/destino y Amazon la registra al vender | Principal `null`, motivo `logistica_fbm_pendiente`; continúa seleccionable y el margen observado usa el costo real después de vender |
| US FBA/FBM | Ingesta histórica sin `item_price` normalizado ni política fiscal prospectiva US | Principal `null`, motivo `politica_retencion_us_ausente`; continúa seleccionable |

La exclusión de FBM/US es de la **estimación completa previa a venta**, no de
catálogo ni campañas. Un componente ausente conserva el principal `null`; no se
rellena con promedio histórico ni con cero.

## Contrato que queda sellado para la continuación

- Nombre v1: **Contribución estimada por venta · antes de Ads**, no margen neto.
- Grano: oferta con contexto `(marketplace, seller_sku, ASIN, canal, precio,
  fecha fuente)`, no producto ni listing sin contexto.
- Cálculo sólo si todos los componentes obligatorios del alcance acordado están
  completos y son compatibles: `I - C - F - L - R`. Dinero se conserva en su
  moneda de origen; conversión usa `fx_resolve` y guarda tasa/fecha/procedencia.
- Política MX: aplica sólo con RFC de persona física válido; `R = 8% IVA +
  2.5% ISR` sobre `item_price` sin impuesto, `effective_from=2026-01-01` y
  conciliación mensual de ISR. Cambio del RFC o de su validez desactiva la
  política hasta que se publique otra versión.
- `margen_neto_pct` observado, muestra limitada, orden inicial, selección,
  objetivo manual, huella, bids, budgets, motor y recuperación permanecen
  intactos. El estimado no genera ACoS de equilibrio ni target.
- GET de catálogo sólo lee Orbit. Credenciales y Product Fees viven en una
  ingesta separada, con respuestas sanitizadas y sin mutar bridge/accounting.
- Un componente ausente no equivale a cero. Cero/negativo comprobado sí se
  conserva. Reembolsos, almacenamiento y overhead quedan enumerados como
  exclusiones, no ocultos.

## Decisión de liberación

**Bloque 0: cerrado.** A.1 queda liberada sólo para el universo FBA MX sellado.
La exclusión explícita de FBM/US satisface los faltantes sin crear cifras falsas;
ampliar ese universo exige una enmienda de fuente/política antes de tocar A.3.
