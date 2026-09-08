# Conciliacion independiente I-C-F-L-R (FBA MX)

Modo: plantilla de trabajo. Un escenario FBA MX se reconcilia contra las
fuentes externas del mismo contexto, no contra el total guardado en Orbit.

Universo comprometido en el acta 0.3: Amazon MX, canal FBA. FBM y US quedan
fuera. No rellenar ausencias con cero.

## Identidad del caso

| Campo | Valor | Fuente |
|---|---|---|
| Fecha de conciliacion UTC | | reloj de la corrida |
| listing_id Orbit | | `listing.id` |
| ASIN | | `listing.external_id` (no copiar titulo ni PII) |
| seller_sku | | `listing.seller_sku` |
| Canal | `fba` | `estimacion_escenario.canal` o disponibilidad `fuente='fba'` |
| valoracion_date | | escenario |
| snapshot_id escenario | | `estimacion_escenario.id` |

## Componentes (cada uno contra su origen)

Formula sellada: `I = P / 1.16`, `C` = COGS Odoo neto, `F` = TotalFeesEstimate
conciliado con `Σ FinalFee`, `L = 0` porque FBA ya va en `F`,
`R = 0.025 * I`. Contribucion = `I - C - F - L - R`.

| Clave | Importe fuente | Moneda | Fecha fuente | Como se obtuvo | ¿Cuadra con Orbit? |
|---|---|---|---|---|---|
| P precio publicacion | | MXN | `fetched_at` bridge | `amazon_listing_prices.price` del mismo `(marketplace, seller_sku, ASIN, canal, fetched_at)` como Decimal | P Orbit = P bridge, no al reves |
| I ingreso normalizado | | MXN | misma que P | `P / 1.16` con Decimal | no usar un I guardado si P no cuadra |
| C costo | | MXN | `sku_cost.valid_from` + `ingest_run` del dia UTC | `sku_cost` vigente, `includes_tax=false`, corrida `ok` del dia | C Orbit = C accounting |
| F fees | | MXN | `TimeOfFeesEstimation` | Product Fees, misma oferta. `TotalFeesEstimate = Σ FinalFee`. No sumar el desglose otra vez | F Orbit = quote, no el total Orbit |
| L logistica FBA | 0 | MXN | n/a | FBA ya esta en F. No es tarifa FBM cero | L debe ser 0 o el principal es null |
| R ISR | | MXN | politica versionada | `0.025 * I`. IVA retenido 0.08*I no se resta | R Orbit = politica × I |
| Contribucion | | MXN | calculo de esta hoja | `I - C - F - L - R` hecho aqui | comparar DESPUES con `estimacion_escenario.contribucion` |

## Politica

| Campo | Valor esperado FBA MX | Valor del escenario | ¿Misma version? |
|---|---|---|---|
| universo | FBA Amazon MX | | |
| iva_divisor | 1.16 | | |
| isr_tasa | 0.025 | | |
| formula_version | S3 | | |
| politica_version_id | | | |

## Controles de pertenencia

- F se resta una vez. Si el desglose Referral+FBA suma F, no volver a restar FBA como L.
- Si Product Fees trae `TaxAmount` distinto de cero, el principal queda null con
  `impuesto_fee_pendiente`. No inventar acreditamiento.
- Si cualquier obligatorio falta, esta hoja declara incompleto. No se publica
  un subtotal como contribucion.

## Resultado

| Pregunta | Si / No / Bloqueado | Nota |
|---|---|---|
| ¿P cuadra con bridge? | | |
| ¿C cuadra con sku_cost del dia? | | |
| ¿F cuadra con la quote del mismo snapshot? | | |
| ¿R cuadra con la politica? | | |
| ¿La contribucion de esta hoja cuadra con Orbit? | | Se llena al final, nunca como fuente |
| ¿Hay ventas comparables para contrastar estimado vs real? | | Diferencia estimado/real no prueba error de formula |

Estado de esta entrega: plantilla lista. Caso vivo bloqueado porque
`estimacion_escenario` no existe en produccion (0028 no aplicada). Tras B.5
e ingesta, copiar un listing FBA MX `disponible` sin ventas (seccion B de
`select.sql`) y completar esta hoja con Decimal, no con texto de distinta
escala.
