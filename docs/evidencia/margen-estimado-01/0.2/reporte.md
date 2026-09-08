# MARGEN ESTIMADO 01 · 0.2 — Fees, costos directos y fiscalidad

Consulta: 2026-09-08 UTC. Alcance: lectura de ledger y accounting; sondas de
consulta Product Fees por contexto de fulfilment. No hubo escrituras comerciales,
cambios de precios, anuncios ni persistencia de cotizaciones. Los requests usaron
el loader existente dentro de `orbit-app-1`; ningún valor, SKU, ASIN, precio ni
importe se escribió en la salida.

## Product Fees API

La credencial existente contiene las tres claves LWA requeridas por el cliente de
fotos. El refresh LWA devolvió HTTP 200. `getMyFeesEstimateForSKU` devolvió HTTP
200 y `FeesEstimateResult.Status=Success` en MX y US para
`IsAmazonFulfilled=false`. La sonda sanitizada versionada en
`product-fees-probe-2026-09-08.json` comprueba por escenario que el identificador,
marketplace, fulfilment, precio y moneda devueltos reproducen el request; que el
total y cada `FinalFee` son dinero de la moneda solicitada; y que la suma Decimal
de los `FinalFee` coincide con el total. No conserva SKU, ASIN, precio ni importe.

| Mercado | Escenario | Resultado | Clases devueltas |
|---|---|---|---|
| MX | no Amazon-fulfilled | éxito | `ReferralFee` |
| US | no Amazon-fulfilled | éxito | `PerItemFee`, `ReferralFee`, `VariableClosingFee` |
| US | Amazon-fulfilled, SKU con observación FBA | éxito | `FBAFees`, `PerItemFee`, `ReferralFee`, `VariableClosingFee` |
| MX | Amazon-fulfilled, SKU con observación FBA | `ClientError: InvalidParameterValue` | ninguna |

La última fila sólo demuestra que esa oferta/contexto MX no puede cotizarse como
se envió. Antes de soportar FBA MX hay que identificar una oferta válida y su
contexto completo o registrar el canal como no soportado. La API puede cotizar
por SKU y precio, pero Amazon advierte que los costos reales pueden variar; no
sustituye una liquidación ni autoriza llamarla «margen neto».

Referencias: [SKU](https://developer-docs.amazon.com/sp-api/reference/getmyfeesestimateforsku),
[batch](https://developer-docs.amazon.com/sp-api/reference/getmyfeesestimates),
[modelo Product Fees v0](https://github.com/amzn/selling-partner-api-models/blob/main/models/product-fees-api-model/productFeesV0.json).
La integración futura reutiliza el patrón LWA redactor de
`app/publicacion_fotos.py`, pero como ingesta separada, nunca desde un GET de
catálogo. La cuota/errores se medirán en implementación; estas sondas no fijan
una cadencia.

## Componentes observados y faltantes

| Componente | Evidencia | Uso prospectivo |
|---|---|---|
| Costo de producto | `sku_cost` vigente y neto de IVA para todo listing Orbit | Disponible, siempre con fecha de vigencia y unidad por verificar |
| Comisión de referencia | Product Fees MX/US | Sólo para oferta/precio/canal que devuelva éxito |
| Fulfilment FBA | Product Fees US devuelve `FBAFees` | Disponible US en la sonda; MX pendiente por contexto válido |
| Envío/fulfilment FBM | Ledger tiene `shipping_fee`; Product Fees FBM no lo devolvió | Falta tarifa prospectiva; no usar histórico como tarifa |
| Retenciones | Ledger tiene `tax_withheld` e `isr_withheld` | Falta política prospectiva: base, tasa, vigencia y aplicabilidad |
| Almacenamiento | `storage_fee` MX observado | Excluido de unidad hasta definir prorrateo o clase aparte |
| Reembolsos | `refund` ligado a orden | Excluido del escenario de venta cumplida; no se convierte en cero |
| Ads | `fee_type=ads`, sin order_id | Excluido por definición «antes de Ads» |

El ledger tiene `tax_withheld` con order_id en ambos mercados y siete
`isr_withheld` sin order_id por mercado; además hay fees sin orden. Todos sus
importes, incluso los de `amazon_us`, están en MXN. Esto confirma que no se puede
copiar moneda de plataforma ni prorratear ISR sin una base explícita para un
producto que aún no vende.

La página SAT consultada durante la planificación confirma que el tratamiento de
IVA de plataformas depende del caso del contribuyente; no acredita la tasa,
régimen o tratamiento aplicable a esta cuenta en 2026. La fuente legal y el
contrato contable siguen sin identificar. No se aplicó una tasa por suposición.

## Matriz de contrato y dictamen 0.2

| Mercado/canal | Precio+fecha | Costo | Fee API | Logística | Retención | Estado |
|---|---|---|---|---|---|---|
| MX FBM | Bridge sí, cuando hay precio | sí | referencia sí | falta tarifa | falta política | incompleto |
| MX FBA | Bridge sí, cuando hay precio | sí | contexto FBA no validado | falta confirmar cotización | falta política | incompleto |
| US FBM | Bridge sí, salvo filas viejas/sin precio | sí, convertir MXN→USD | fees sí | falta tarifa | falta política | incompleto |
| US FBA | Bridge sí, salvo filas viejas/sin precio | sí, convertir MXN→USD | fees+FBAFees sí | incluida sólo si el desglose lo acredita | falta política | incompleto |

**Dictamen:** la cotización oficial es viable y el total no se suma de nuevo con
sus detalles. El cálculo completo no se libera: logística FBM y retenciones no
tienen fuente prospectiva verificada; FBA MX requiere una sonda con oferta/contexto
válido. Mientras tanto sólo se muestran componentes conocidos y el motivo de no
calcular, nunca un subtotal llamado contribución completa.

Consulta de respaldo: `select-ledger.sql`. No contiene importes ni IDs.
