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
| MX | Amazon-fulfilled, precio fresco de bridge y SKU FBA | éxito en 3/3 sondas | `FBAFees`, `ReferralFee` |

La primera sonda MX FBA falló con precio mutable de Orbit. Al repetirla con tres
ofertas `AMAZON_NA` y precio fresco de bridge, las tres respondieron éxito; el
artefacto sanitizado es `fba-mx-fresh-probe-2026-09-08.json`. FBA MX queda
soportado sólo cuando la oferta/precio/canal cumplen ese contrato; un fallo
individual queda `incompleta`. La API puede cotizar por SKU y precio, pero
Amazon advierte que los costos reales pueden variar; no sustituye una liquidación
ni autoriza llamarla «margen neto».

Referencias: [SKU](https://developer-docs.amazon.com/sp-api/reference/getmyfeesestimateforsku),
[batch](https://developer-docs.amazon.com/sp-api/reference/getmyfeesestimates),
[modelo Product Fees v0](https://github.com/amzn/selling-partner-api-models/blob/main/models/product-fees-api-model/productFeesV0.json).
La integración futura reutiliza el patrón LWA redactor de
`app/publicacion_fotos.py`, pero como ingesta separada, nunca desde un GET de
catálogo. La cuota/errores se medirán en implementación; estas sondas no fijan
una cadencia.

## Finances y cobros de envío reales

Sí es posible investigar Amazon directamente: una llamada GET sanitizada a
`/finances/v0/financialEvents` devolvió HTTP 200 con el grupo
`TaxWithholdingEventList` disponible.
El resultado de la ventana consultada y sólo sus conteos/tipos está en
`finances-probe-2026-09-08.json`; no guarda órdenes, clientes, SKU, importes ni
documentos fiscales. Esta sonda usa **Finances v0**, coherente con sus grupos
`*EventList`; la referencia oficial de [Finances
v0](https://developer-docs.amazon.com/sp-api/lang-es_ES/reference/finances-v0)
describe la recuperación de eventos financieros de la cuenta.

El histórico Orbit MX muestra `tax_withheld` en 1,201 filas con orden y
`isr_withheld` en siete ajustes sin orden. Sus porcentajes mensuales observados
no son constantes y septiembre está incompleto; sólo sirven para conciliación,
nunca como tasa prospectiva. Finances prueba el acceso técnico, pero no expone
en esta sonda el RFC de la cuenta ni sustituye el certificado/política fiscal
vigente que determina la retención siguiente.

### Política MX cerrada

El responsable declara persona física. El contraste de 1,000 órdenes que sí
tienen `item_price` muestra 953 con IVA retenido dentro de `0.001` puntos de
8%; el resto tiene ajustes y no define la regla. La noticia vigente de
[Amazon Seller Central](https://sellercentral.amazon.com.mx/seller-forums/discussions/t/cd2f946a-8cc8-4ad8-b406-c763c7066d45?mons_sel_locale=en_MX)
publica para persona física mexicana con RFC la retención de 8% IVA y 2.5% ISR
desde 2026-01-01. Los cargos mensuales de ISR de abril a agosto quedan entre
2.3496% y 2.7467% de la base `item_price` del mes previo; marzo es una excepción
documentada y no se convierte en tasa.

Se sella `amazon_mx_pf_rfc_valid_2026_01`: IVA 8% e ISR 2.5% sobre
`item_price` sin impuesto, con vigencia desde 2026-01-01. La política se activa
sólo mientras Seller Central mantenga RFC válido; cualquier cambio la deja sin
política hasta versionarla de nuevo. Detalle sanitizado en
`retencion-mx-2026-09-08.json`.

No se sella política US: sus 522 ventas y 886 retenciones históricas no tienen
`item_price` normalizado. No se aplican tasas MX a US ni se usa cero como
retención.

Que Amazon cobre el envío al comprador tampoco equivale a costo logístico cero.
En el ledger, 133 de 1,179 ventas MX tienen `shipping_price` positivo; las 522
US observadas lo tienen nulo. A la vez, Finances devolvió costos MFN
`MFNPostageFee` y `PostageBilling_*` (combustible, rastreo, firma, arancel y
otros) que dependen de envío concreto. Por ello el cobro al cliente pertenece a
ingreso/orden y la logística sigue sin una tarifa prospectiva por oferta. Product
Fees no devuelve esa cotización.

## Componentes observados y faltantes

| Componente | Evidencia | Uso prospectivo |
|---|---|---|
| Costo de producto | `sku_cost` vigente y neto de IVA para todo listing Orbit; no hay kits confirmados | Completo: el responsable del negocio confirma que incluye importación, transporte de entrada y embalaje |
| Comisión de referencia | Product Fees MX/US | Sólo para oferta/precio/canal que devuelva éxito |
| Fulfilment FBA | Product Fees devuelve `FBAFees` en US y MX con oferta/canal/precio fresco | Disponible sólo si la cotización individual responde éxito |
| Envío/fulfilment FBM | Finances/ledger muestran cobro al cliente y cargos MFN variables; Product Fees FBM no lo devuelve | Falta cotización/tarifa prospectiva; no usar histórico como tarifa |
| Retenciones | Finances es accesible; ledger tiene `tax_withheld` e `isr_withheld` | Falta política prospectiva de esta cuenta: base, tasa, vigencia y aplicabilidad |
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
| MX FBM | Bridge sí, cuando hay precio | sí | referencia sí | guía sólo al vender | política PF sellada | incompleto antes de venta |
| MX FBA | Bridge sí, cuando hay precio fresco | sí | fees+FBAFees sí | incluida si el desglose lo acredita | política PF sellada | soportado |
| US FBM | Bridge sí, salvo filas viejas/sin precio | sí, convertir MXN→USD | fees sí | guía sólo al vender | sin política | incompleto |
| US FBA | Bridge sí, salvo filas viejas/sin precio | sí, convertir MXN→USD | fees+FBAFees sí | incluida sólo si el desglose lo acredita | sin política | incompleto |

**Dictamen:** la cotización oficial es viable y el total no se suma de nuevo con
sus detalles. El costo directo se cubre con el COGS Odoo confirmado. El cálculo
completo se libera sólo para FBA MX bajo la política sellada; FBM y US conservan
componentes conocidos y el motivo de no calcular, nunca un subtotal llamado
contribución completa.

Consulta de respaldo: `select-ledger.sql`. No contiene importes ni IDs.
