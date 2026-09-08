# Conciliacion independiente I-C-F-L-R (FBA MX)

Caso vivo AC14, 2026-09-08. Conciliado contra fuentes externas del mismo
contexto (bridge, sku_cost Orbit, Product Fees SP-API, politica acta 0.3).
No se usa un total guardado en Orbit como fuente: `estimacion_escenario` no
existe en produccion (0028 no aplicada). La formula se demostro con Decimal y
con `app.estimacion_venta.calcular_contribucion` en proceso.

Universo: Amazon MX, canal FBA. FBM y US fuera. Sin relleno de ausencias.

## Identidad del caso

| Campo | Valor | Fuente |
|---|---|---|
| Fecha de conciliacion UTC | 2026-09-08 19:10 UTC | reloj de la corrida |
| listing_id Orbit | 1213 | `listing.id` |
| ASIN | B0C8RVWG4F | `listing.external_id` |
| seller_sku | SK-YBQX-XQWV | `listing.seller_sku` |
| Canal | fba | `disponibilidad_observation.fuente='fba'` (qty 200) + bridge `AMAZON_NA` |
| valoracion_date | 2026-09-08 | dia UTC de la conciliacion |
| snapshot_id escenario | n/a | tablas 0028 ausentes en prod |
| Ventas observadas | ninguna | sin fila en `v_margen_producto` para `(product_id=308, amazon_mx)` |

## Componentes (cada uno contra su origen)

Formula sellada: `I = P / 1.16`, `C` = COGS Odoo neto, `F` = TotalFeesEstimate
conciliado con `Σ FinalFee`, `L = 0` porque FBA ya va en `F`,
`R = 0.025 * I`. Contribucion = `I - C - F - L - R`.

| Clave | Importe fuente | Moneda | Fecha fuente | Como se obtuvo | ¿Cuadra? |
|---|---|---|---|---|---|
| P precio publicacion | 988.0000 | MXN | bridge `fetched_at` 2026-09-08 18:35:40 | `amazon_listing_prices` snapshot RO (`AMAZON_NA`). Orbit `listing.listing_price` = 988.0000 MXN | Si: P Orbit = P bridge |
| I ingreso normalizado | 851.7241 | MXN | misma que P | `Decimal('988.0000') / Decimal('1.16')` cuantizado a 4 dec. | Si (hoja) |
| C costo | 341.0000 | MXN | `sku_cost.valid_from` 2026-08-18 | `sku_cost` id 2068, `includes_tax=false`, MXN. Corrida diaria `accounting_sku_costs` id 124 ok (08:15 UTC, `rows_skipped=0`) | Si: costo vigente neto |
| F fees | 191.7600 | MXN | `TimeOfFeesEstimation` 2026-09-08T19:10:55Z | Product Fees Success. ReferralFee FinalFee 127.76 + FBAFees 64.00 = TotalFeesEstimate 191.76. TaxAmount null. Ver `fees-product-fees-1213.json` | Si: F quote = Σ FinalFee |
| L logistica FBA | 0 | MXN | n/a | FBA ya en F; politica `logistica=0` | Si |
| R ISR | 21.2931 | MXN | politica acta 0.3 | `0.025 * I` cuantizado. IVA retenido 0.08*I no se resta | Si |
| Contribucion | 297.6710 | MXN | calculo de esta hoja | `I - C - F - L - R` = 851.7241 − 341.0000 − 191.7600 − 0 − 21.2931 | Si vs `calcular_contribucion` |

## Politica

| Campo | Valor esperado FBA MX | Valor usado | ¿Misma version? |
|---|---|---|---|
| universo | FBA Amazon MX | `amazon_mx/fba` | Si (acta 0.3 / 0029) |
| iva_divisor | 1.16 | 1.16 | Si |
| isr_tasa | 0.025 | 0.025 | Si |
| formula_version | S3 | S3 | Si |
| politica_version_id | seed 0029 (no en prod) | label `amazon_mx_pf_rfc_valid_2026_01` en proceso | Semilla documentada; no hay fila en prod |

## Controles de pertenencia

- F se resta una vez. Referral+FBA suman F; L queda 0 (no se vuelve a restar FBA).
- Product Fees `TaxAmount` es null / cero → no aplica `impuesto_fee_pendiente`.
- Todos los obligatorios presentes → principal disponible.

## Resultado

| Pregunta | Si / No / Bloqueado | Nota |
|---|---|---|
| ¿P cuadra con bridge? | Si | 988.0000 MXN exacto |
| ¿C cuadra con sku_cost del dia? | Si | 341.0000 vigente; corrida 124 ok |
| ¿F cuadra con la quote del mismo snapshot? | Si | 191.76 = Σ FinalFee; echo precio/SKU/FBA |
| ¿R cuadra con la politica? | Si | 21.2931 = 0.025 × 851.7241 |
| ¿La contribucion de esta hoja cuadra con Orbit? | n/a (sin escenario en prod) | Cuadra con `calcular_contribucion`: estado `disponible`, 297.6710 MXN, 34.9492% |
| ¿Hay ventas comparables para contrastar estimado vs real? | No | Sin ventas observadas; no se inventa contraste |

Evidencia anexa: `fees-product-fees-1213.json`, `calculo-independiente-1213.json`.
Estado: **AC14 cerrado en B.3** sin aplicar 0028 ni esperar B.5.
