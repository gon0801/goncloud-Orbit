# K.1 Conciliacion: cotizacion integra, cargo externo y escenario

- Base: `1191125c65841e8ec8d77448f7bbdcfeed29be1a` (master con BASE y A-J).
- Rama: `arreglos-05/k`.
- Lectura: 2026-11-25T13:21-13:23Z, rol `orbit_read`, transaccion `read_only`,
  por `docs/evidencia/arreglos-01/lee_prod.sh`.
- Comando: `bash docs/evidencia/arreglos-05/K/lee_previa.sh`.
- Salidas saneadas: `cotizacion.txt`, `cargo.txt`, `escenario.txt`,
  `politica.txt`; las consultas son `cotizacion.sql`, `cargo.sql`,
  `escenario.sql` y `politica.sql`.
- Es una foto viva: los conteos crecen entre un statement y otro. Cada tabla
  cita el conteo de su propio bloque.

## Fuente externa

| Lado | Que es | Tabla y fuente |
|---|---|---|
| Cotizacion integra | Product Fees de Amazon y cotizador de MeLi, con el arbol de fees, precio, moneda y fecha de la fuente | `estimacion_fee_observation` (`estado = 'success'`) |
| Cargo externo | Liquidacion de Amazon (SP-API Finances) y cargos de MeLi, con la comision real del pedido | `v_ledger_cargo` (migracion `0086`), exigiendo que la fila no sea estimada |
| Escenario | Lo que la estimacion calculo y con que politica | `estimacion_escenario`, `estimacion_politica_version` |

Se usa `v_ledger_cargo` y no `ledger_event` porque el libro guarda tambien una
comision estimada por pedido. La vista retira la estimada cuando ya llego la
real del mismo pedido, pero deja la estimada cuando no hay real. Para evidencia
de "cargo externo" se exige ademas
`split_part(coalesce(source_event_id, ''), '|', 2) <> 'fee_estimate'`, y las
estimadas se cuentan aparte (`cargo.txt`, bloque A2):

| Plataforma | referral_fee en la vista | Reales | Estimadas |
|---|---|---|---|
| Amazon MX | 1,745 | 1,727 | 18 |
| Amazon US | 663 | 660 | 3 |
| MeLi | 1,147 | 1,147 | 0 |

En la ventana de comparacion, los pedidos cuyo unico `referral_fee` es una
estimacion son 18 de 534 en Amazon MX, 2 de 53 en Amazon US y 0 de 207 en MeLi
(`cargo.txt`, bloque D). No entran en ninguna tasa publicada.

## Metodo de comparacion

- **Ventana comun.** La comparacion por producto aplica a las dos poblaciones la
  misma ventana: Amazon MX y MeLi desde 2026-09-01; Amazon US desde 2026-10-15.
  Una liquidacion de enero no se mezcla con una cotizacion de noviembre: los
  pedidos sin cotizacion contemporanea quedan fuera y se cuentan.
- **Un producto mapeado.** Un pedido entra a la mediana por producto solo si
  todas sus lineas de venta estan mapeadas y apuntan al mismo producto. Se
  cuentan aparte los pedidos descartados por linea sin producto o por varios
  productos (`cargo.txt`, bloques E y F; `escenario.txt`, bloques E y F). En la
  ventana se descartaron 0 pedidos.
- **Solo cargos efectivos.** La comision estimada no es un cargo y no entra en
  ninguna tasa (`cargo.txt`, bloque A2).
- **Sin identificadores.** Los ejemplos y las tasas no publican `order_id`; solo
  importes y agregados.

## Cotizacion integra

- Volumen `success`: Amazon MX 86,130 (desde 2026-09-08), Amazon US 16,387
  (desde 2026-10-15), MeLi 2,103 (desde 2026-10-17).
- **Impuesto explicito: cero.** Ninguna cotizacion de ninguna plataforma trae
  `tax_amount` distinto de cero, ni en el primer nivel ni en los detalles
  anidados (`cotizacion.txt`, bloque B: `0 | 0 | 0`). El escritor solo guarda la
  clave cuando el importe es distinto de cero, asi que la ausencia equivale a
  "la fuente no expone un impuesto".
- Tasa cotizada de la comision variable sobre el precio cotizado:

| Plataforma | Moneda | Tipo | Cotizaciones | Mediana | Minimo | Maximo |
|---|---|---|---|---|---|---|
| Amazon MX | MXN | ReferralFee | 86,130 | 0.129310 | 0.129306 | 0.129316 |
| Amazon US | USD | ReferralFee | 16,387 | 0.150000 | 0.149944 | 0.150073 |
| MeLi | MXN | SaleFee | 2,103 | 0.195003 | 0.195000 | 0.195007 |

- La cotizacion de un listing FBA de Amazon MX trae el arbol completo:
  `ReferralFee` mas `FBAFees` con su hijo `FBAPickAndPack`, sin `tax_amount` en
  ningun nivel; la de un listing FBM trae solo `ReferralFee`. La de MeLi trae
  `SaleFee`, `SaleFeeFixed` y `ListingFee`. La de Amazon US trae `ReferralFee`,
  `PerItemFee` y `VariableClosingFee` (`escenario.txt`, bloque G).

## Cargo externo

- Tipos de cargo por plataforma (`cargo.txt`, bloque B): `referral_fee`,
  `fba_fee`, `shipping_fee`, `ads`, `otros`, `storage_fee`; retenciones
  `isr_withheld` y `tax_withheld`. La comision de MeLi llega como
  `referral_fee`, igual que la de Amazon.
- Tasa cobrada de la comision variable por pedido, solo cargos efectivos, en la
  ventana en que conviven cotizacion y cargo:

| Plataforma | Moneda | Pedidos reales | p10 | p50 | p90 | En 15.00% | En 19.50% | Solo estimada |
|---|---|---|---|---|---|---|---|---|
| Amazon MX | MXN | 534 | 0.150000 | 0.150000 | 0.150007 | 510 | 0 | 18 |
| Amazon US | MXN | 53 | 0.111951 | 0.128276 | 0.135426 | 0 | 0 | 2 |
| MeLi | MXN | 207 | 0.195000 | 0.195004 | 0.195005 | 0 | 206 | 0 |

La columna "En 15.00%" y "En 19.50%" cuenta pedidos cuya razon cae en
`[0.1499, 0.1501]` o `[0.1949, 0.1951]`; en Amazon US esa banda no aplica porque
su razon no es 15%.

- La mediana mensual de Amazon MX es `0.150000` en todos los meses desde
  2025-12 hasta 2026-11. La de MeLi es `0.195000` en todos los meses desde
  2025-03. La de Amazon US cambia de regimen: ver abajo.
- La venta de Amazon US se registra en MXN aunque la cotizacion este en USD. Por
  eso no se atribuye la diferencia a un tipo de cambio sin medirlo.

## Comparacion y diferencia

Razon por producto: mediana de la tasa cobrada (solo cargos efectivos, ventana
comun, un producto mapeado) dividida entre la mediana de la tasa cotizada del
mismo producto.

| Plataforma | Moneda | Cotizado | Cobrado | Razon | Productos 1.16 | Productos 1.00 | Veredicto |
|---|---|---|---|---|---|---|---|
| Amazon MX | MXN | 0.129311 | 0.150000 | **1.160005** | 110 de 113 | 0 de 113 | separado |
| MeLi | MXN | 0.195004 | 0.195004 | **1.000000** | 0 de 10 | 10 de 10 | incluido |
| Amazon US | USD / MXN | 0.150000 | 0.128797 | 0.858649 | 0 de 24 | 0 de 24 | **desconocido** |

- Amazon MX: 113 productos y 534 pares en la ventana. El rango de la razon es
  1.105681 a 1.160057; la mediana y 110 de 113 productos caen en 1.16. La
  diferencia no es una tasa supuesta: sale de dividir el cargo efectivo entre la
  cotizacion del mismo producto y del mismo periodo.
- MeLi: 10 productos y 43 pares. La razon es 1.000000 con rango 1.000000 a
  1.000000; la cotizacion ya trae el mismo importe que se cobra.
- Amazon US: **desconocido.** La razon por producto es 0.858649 con rango
  0.634122 a 0.940567, ni 1.0000 ni 1.16 ni el factor de IVA de la moneda (USD
  no tiene IVA), asi que el criterio de `plan.md` la marca como desconocida. Lo
  unico que la fuente sostiene es que, en la ventana con cotizaciones (desde
  2026-10-15), el cargo efectivo no supera la cotizacion: 0.128797 contra
  0.150000.
- La serie mensual de Amazon US (`cargo.txt`, bloque H) muestra dos regimenes en
  la tasa de comision por pedido: `0.167554` (2025-12), `0.173272` (2026-01) y
  `0.178202` (2026-02), compatibles con `15% x 1.16 = 0.174000`; y desde 2026-05
  entre `0.128189` y `0.131153`. La tasa de retencion cambia en el mismo periodo
  (`0.081928`, `0.074690`, `0.076559` al inicio; `0.061524` a `0.071930`
  despues), cerca de `8% / 1.16 = 0.068966`. La fuente no permite determinar la
  causa: no hay cotizaciones de esos meses y la liquidacion no separa un cargo
  de impuesto sobre la comision. No se atribuye a categoria ni a conversion; es
  una tarea de evidencia abierta.

Diferencia en dinero, tres pedidos de Amazon MX con la comision exacta al 15%
(`cargo.txt`, bloque G; sin identificador):

| Venta MXN | Comision MXN | Tasa cobrada | Razon | Diferencia contra 12.931% |
|---|---|---|---|---|
| 2,689.0000 | 403.3600 | 0.150004 | 1.160032 | 55.6454 MXN |

`2,689.0000 x 0.129310 = 347.7146`; `403.3600 - 347.7146 = 55.6454`, que es el
16.0% de la comision cotizada. La cotizacion no trae ese impuesto y el cargo
externo si lo cobra, ya dentro del 15%.

## Escenario

- Escenarios `disponible`: Amazon MX S4 2,830 y S3 42,174; Amazon US S3 7,823;
  MeLi S4 262 y S3 1,551.
- **`impuesto_fee_pendiente` no aparece en ningun escenario** (`escenario.txt`,
  bloque B). La abstencion existe en el codigo y nunca se ha disparado, porque
  ninguna cotizacion ha traido impuesto explicito.
- S4 de Amazon MX incorpora el impuesto una sola vez: en 2,830 de 2,830
  escenarios `fee_iva_referral = ReferralFee x 0.16` y
  `fee_total = total_fees + fee_iva_referral` (`escenario.txt`, bloque C). No
  multiplica el total completo: solo la parte variable, como manda el contrato.
- MeLi S4: 262 de 262 escenarios sin `fee_iva_referral`, con multiplicador `1`
  (`escenario.txt`, bloque D y `politica.txt`).
- La comision estimada con S4 contra el cargo efectivo, por producto en Amazon
  MX: 113 productos, 534 pares, tasa estimada mediana 0.150001, razon mediana
  **1.000004**, 110 de 113 productos dentro del 0.1% (`escenario.txt`, bloque E).
- MeLi: razon mediana 1.000000 y 10 de 10 productos dentro del 0.1%
  (`escenario.txt`, bloque F).
- Politica vigente desde 2026-11-21: `referral_fee_iva_multiplicador = 1.16` en
  `amazon_mx/fba` y `amazon_mx/fbm`; `1` en `meli/meli`; `iva_divisor = 1` y
  `precio_incluye_iva = false` en `amazon_us/fbm` (`politica.txt`).

## Separacion pedida por K.1

- **Incluido en el precio**: el IVA de la venta. `P` incluye IVA y el escenario
  calcula `I = P / 1.16`; la retencion de IVA de la venta viaja aparte como
  `tax_withheld` y la politica S4 la descuenta como tasa. No se toca.
- **Separado**: el IVA de la comision de Amazon MX. La cotizacion lo excluye
  (12.931%) y el cargo externo lo cobra dentro del 15.000%. La politica S4 lo
  incorpora una sola vez con `referral_fee_iva_multiplicador = 1.16`.
- **Incluido en la comision**: la comision de MeLi. La cotizacion y el cargo
  coinciden en 19.500% con razon 1.000000; el multiplicador `1` es correcto.
- **Desconocido (cerrado en segunda lectura, ver seccion siguiente)**: en la primera
  lectura sobre `ledger_event`, Amazon US quedo abierto porque `sale_gross` mezcla
  `ItemPrice + Shipping + Tax` y oculta el desglose `Base/Tax` de `Commission`.

## Ampliacion de fuente externa: adjudicacion de Amazon US (2026-11-25)

Lectura directa de solo lectura contra **Amazon SP-API Finances v2024**
(`GET /finances/2024-06-19/transactions?marketplaceId=ATVPDKIKX0DER`, moneda nativa USD) y
conciliacion historica completa en `accounting.db` (`mode=ro`). Salida saneada sin
`order_id` ni SKU en `K/finances_us_v2024.txt`.

1. **Desglose directo en USD de SP-API Finances v2024 (sin tipo de cambio):**
   - `2026-01-01` a `2026-02-01` (`n=52`): `Commission.Base / ProductCharges = 0.150000`,
     `Commission.Tax / Base = 0.159887` (16%), `Commission.Total / Base = 1.159887`,
     `Commission.Total / ProductCharges = 0.173983`, **0 anomalias**.
   - `2026-05-01` a `2026-06-01` (`n=50`): `Base/Price = 0.150000`, `Tax/Base = 0.159887`,
     `Total/Price = 0.173983`, **0 anomalias**.
   - `2026-10-15` a `2026-11-25` (ventana comun con Orbit, `n=62`): `Base/Price = 0.150000`,
     `Tax/Base = 0.159887`, `Total/Price = 0.173984`, **0 anomalias**.
   - **164 de 164 items (100%)** cobran exactamente `15.00% Base + 16.00% Tax = 17.40%`
     (`1.16x` de la cotizacion `ReferralFee` de Product Fees, que llega al `15.00%` sin
     `TaxAmount`).
2. **Conciliacion historica completa `2025-12` a `2026-11` (`n=531` con FX exacto):**
   - `Commission_USD / ItemPrice_USD`: mediana `0.173985` (`1.159900x` de 15%), rango
     `0.173865` a `0.174092`, **531/531 (100%)** en `[17.30%, 17.50%]`, 0 outliers.
   - `ShippingHB_USD / ShippingPrice_USD`: mediana `0.174002` (`1.160014x`), `527/531` en
     `[17.30%, 17.50%]`. `ShippingHB` ya entra con IVA incluido en `precio_envio_muestra`
     desde el ledger (`app/precio/envio.py`).
3. **Explicacion del artefacto `0.8586` y los aparentes "dos regimenes":**
   - Nunca hubo cambio de regimen fiscal. `sale_gross` en `ledger_event` suma
     `ItemPrice + ItemTax + ShippingPrice + ShippingTax`, mientras `referral_fee` solo guarda
     `Commission` (sobre `ItemPrice`). Al subir `ShippingPrice` de `$8.99` (`2025-12`–`2026-03`)
     a `$12.99` (`2026-04`) y `$30.00` (`2026-05`–`2026-11`), la fraccion `ItemPrice / Total`
     bajo de `0.85` a `0.736`, haciendo que `Commission / sale_gross` bajara de `0.17` a
     `0.128` (`0.174 x 0.736`).
   - `tax_withheld` en Amazon US es `MarketplaceFacilitatorTax` (sales tax estatal de EE. UU.,
     que se compensa contra `OurPriceTax + ShippingTax` a `$0.00 USD` netos para el vendedor).
     Amazon US no retiene IVA mexicano (`retencion_iva_tasa = 0`).
4. **Veredicto final para Amazon US:** **impuesto separado (1.16x), omitido en `S3` e
   incorporado una sola vez mediante migracion `0100_arreglos05_k_us_s4.sql`** (politica `S4`
   `amazon_us_fbm_s4_caja_real_2026_11` con `referral_fee_iva_multiplicador = 1.16`,
   `retencion_iva_tasa = 0`, `isr_tasa = 0.0207`, `iva_divisor = 1`,
   `precio_incluye_iva = false`, `fee_tax_amount_requiere_politica = true`).

## Limites de la fuente

- La fuente demuestra el flujo de caja, no la acreditabilidad fiscal del IVA de
  la comision. K no la necesita para MX y MeLi: la decision del dueno del
  2026-11-13 y el contrato S4 tratan el egreso como costo, y aqui solo se
  verifica que se cuente una vez.
- El `FBAFees` no es comision. La migracion `0093` ya asento que llega con IVA;
  se midio para descartar un patron 1.16 y no lo hay: la razon cobrado/cotizado
  por producto tiene mediana 1.046875 y 0 de 65 productos en 1.16
  (`cotizacion.txt`, bloque E). Su tratamiento queda fuera de K.
- Las cotizaciones de Amazon US estan en USD y sus cargos en MXN. La comparacion
  por razon es valida por ser adimensional, pero no se convierte en una tasa
  fiscal ni se usa para atribuir la diferencia.
- La foto es de un dia. El criterio no depende de un umbral elegido a mano: la
  razon 1.16 sale de los importes y se repite en 110 de 113 productos de Amazon
  MX.
