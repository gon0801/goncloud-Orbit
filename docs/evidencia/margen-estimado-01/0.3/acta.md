# MARGEN ESTIMADO 01 · 0.3 — Acta de estado del bloque 0

Fecha: 2026-09-08 UTC. Estado: **cerrado para FBA MX; FBM y US excluidos**.
Este acta conserva las comprobaciones y el contrato económico de 0.1 y 0.2.
No autoriza implementación A/B, cambios a Ads, precios o campañas.

## Hechos comprobados

1. Bridge conserva una oferta verificable con marketplace, SKU, ASIN, canal,
   precio y `fetched_at`; Orbit no conserva canal ni historial de precio.
   El timer de bridge corre cada seis horas. Una oferta tiene que expirar al
   cumplir seis horas; repetir una fila vieja no la rejuvenece.
2. El productor bridge solicita `GET_MERCHANT_LISTINGS_ALL_DATA` y copia el
   literal TSV `price`. Ese reporte almacenado no lleva marca de base fiscal,
   IVA, promociones, ni envío. En 28 de 30 comparaciones recientes contra
   ventas MX, `bridge.price = item_price + item_tax`; las otras dos difieren.
   Es evidencia histórica, no una clasificación fiscal de cada oferta futura.
3. Todos los listings actuales tienen `sku_cost` MXN neto de IVA y vigente en
   la fecha consultada. El responsable confirmó que incluye importación,
   transporte de entrada y embalaje, y que no hay kits: una oferta vende una
   unidad del producto costeado.
4. `fx_resolve` devuelve USD→MXN exacto o anterior de hasta siete días, con
   fecha y procedencia. Ese máximo está documentado contra la cadencia real;
   fuera de él devuelve ausencia, nunca una constante.
5. Product Fees responde para MX/US y FBA/FBM cuando recibe la misma oferta,
   canal, moneda y precio. El total concilia con sus `FinalFee`; es un único
   cargo, nunca se suma de nuevo con su desglose. La API advierte que el cargo
   real puede variar.
6. Finances separa cobro de envío del cliente y cargos MFN variables. Por ello
   no existe tarifa prospectiva FBM por oferta. El responsable confirmó en
   Seller Central que el RFC de persona física sigue válido y que todas las
   publicaciones MX vigentes usan IVA general de 16%.
7. Una sonda FBA MX con oferta fresca devolvió `Success`, total y
   `TimeOfFeesEstimation`. Sus dos detalles (`FBAFees` y `ReferralFee`) no
   traen `TaxAmount`; cada `FinalFee = FeeAmount - FeePromotion` y la suma de
   `FinalFee` coincide con el total. En producción, precio e importes se
   comparan como `Decimal`, no como texto con distinto número de decimales.

## Contrato ya cerrado

| Insumo | Clave y frescura | Resultado si falta o vence |
|---|---|---|
| Oferta | `(marketplace, seller_sku, asin, canal, precio, fetched_at)` desde bridge; `now_utc - fetched_at <= 6h` | `oferta_desactualizada` o `oferta_ausente` |
| FX | `fx_resolve(fecha_escenario, moneda, moneda_destino)`; exacta o anterior hasta 7 días | `fx_ausente` |
| Costo | `sku_cost` positivo, moneda y unidad compatibles, vigente en la fecha de la oferta, de `ingest_run.ok`; corrida diaria Orbit posterior a 08:15 UTC | `costo_ausente`, `costo_no_vigente` o `costo_desactualizado` |
| Fee | Cotización nueva ligada a **ese** snapshot de oferta: misma clave completa, `Success`, `TimeOfFeesEstimation >= fetched_at`, `TotalFeesEstimate = Σ FinalFee` y detalle reconciliado; no se reutiliza al cambiar precio/canal/SKU/marketplace | `fee_ausente` o `fee_incompatible` |
| FBM | No hay tarifa prospectiva verificable | `logistica_fbm_pendiente` |

La futura A.3 debe solicitar Product Fees después de capturar cada oferta
fresca, guardar ambas referencias append-only y usar la cotización sólo durante
la vigencia de esa oferta. No hay TTL financiero independiente inventado.
Costos, FX y ledger se refrescan diariamente a las 08:15 UTC desde un mismo
snapshot de accounting, después de COGS horario y FX de las 08:00. Para una
nueva estimación, un costo sólo está vigente y fresco si procede de la corrida
`ok` de ese día UTC; antes de ella queda `costo_desactualizado`.

## Normalización MX FBA sellada

El dueño confirmó dos condiciones de cuenta: RFC persona física válido en Seller
Central y 16% de IVA para todas las publicaciones MX vigentes. Junto con la
conciliación histórica de bridge, se fija para este universo:

- `P` es `bridge.price`, precio de publicación **con IVA**.
- `I = P / 1.16`, ingreso de venta sin IVA.
- `C` es el COGS Odoo neto de IVA; no se vuelve a restar importación, entrada ni
  embalaje.
- `F` es `TotalFeesEstimate` de Product Fees para el snapshot exacto, sólo si
  concilia con `Σ FinalFee`. En la sonda FBA MX, cada detalle cumple
  `FinalFee = FeeAmount - FeePromotion` y no devuelve `TaxAmount`; por ello se
  resta ese total una vez. Si una cotización futura devuelve `TaxAmount` no
  cero, el principal queda `null` con `impuesto_fee_pendiente`: no se supone
  que sea acreditable ni se resta hasta sellar una política fiscal nueva.
- `L = 0` sólo porque FBA ya está dentro de `F`; no es una tarifa FBM cero.
- `R = 0.025 * I` por ISR, que el contrato de Orbit trata como costo. La
  retención de IVA `0.08 * I` queda registrada para conciliación fiscal y no se
  resta de contribución: no es ingreso ni costo adicional sobre el IVA ya
  excluido de `I`.

Ejemplo de control (MX FBA, valores de prueba): `P=116`, `I=100`, `C=40`,
`F=15`, `L=0`, `R=2.50` → contribución `42.50 MXN` (42.50%). La retención IVA
para conciliación es `8.00 MXN`, fuera de esa resta. El fixture ilustra la base;
una cotización real se acepta sólo con las referencias y controles de la tabla.

## Exclusiones selladas

FBM y US siguen sin los componentes prospectivos necesarios. No se les asigna
cero ni se les niega la selección para campañas.

## Alcance actual

La v1 seguirá llamándose **Contribución estimada por venta · antes de Ads** y
usará `I - C - F - L - R` sólo si cada componente obligatorio está presente,
normalizado a una base documentada y vigente. El resultado principal será
`null` con motivo si cualquiera falta. No modifica `margen_neto_pct`, muestras,
orden, selección, objetivo manual, huella, bids, budgets, motor ni recuperación.

**Decisión de liberación: FBA MX queda liberado; bloque 0 sigue abierto para FBM y US.**
A.1 puede implementar únicamente la política FBA MX sellada. Ampliar el universo
requiere fuente y política nuevas, sin convertir ausencias en cero.
