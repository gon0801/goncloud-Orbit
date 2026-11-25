# K.1 Conciliacion: cotizacion integra, cargo externo y escenario

- Base: `1191125c65841e8ec8d77448f7bbdcfeed29be1a` (master con BASE y A-J).
- Rama: `arreglos-05/k`.
- Lectura: 2026-11-25T13:11-13:16Z, rol `orbit_read`, transaccion `read_only`,
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
estimacion son 18 de 552 en Amazon MX, 2 de 55 en Amazon US y 0 de 207 en MeLi
(`cargo.txt`, bloque D). No entran en ninguna tasa publicada.

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

| Plataforma | Moneda | Pedidos reales | p10 | p50 | p90 | Pedidos exactos | Solo estimada |
|---|---|---|---|---|---|---|---|
| Amazon MX | MXN | 534 | 0.150000 | 0.150000 | 0.150007 | 510 al 15.00% | 18 |
| Amazon US | MXN | 53 | 0.111951 | 0.128276 | 0.135426 | 0 fuera de rango | 2 |
| MeLi | MXN | 207 | 0.195000 | 0.195004 | 0.195005 | 206 al 19.50% | 0 |

- La mediana mensual de Amazon MX es `0.150000` en todos los meses desde
  2025-12 hasta 2026-11. La de MeLi es `0.195000` en todos los meses desde
  2025-03. La de Amazon US baja de `0.168364` (2025-12) a `0.128273` (2026-11).
- La venta de Amazon US se registra en MXN aunque la cotizacion este en USD. La
  razon por pedido es adimensional y comparable, pero su base no es la misma
  divisa; se trata abajo.

## Comparacion y diferencia

Razon por producto: mediana de la tasa cobrada (solo cargos efectivos) dividida
entre la mediana de la tasa cotizada, con al menos un par y una cotizacion del
mismo producto.

| Plataforma | Moneda | Cotizado | Cobrado | Razon | Productos 1.16 | Productos 1.00 | Veredicto |
|---|---|---|---|---|---|---|---|
| Amazon MX | MXN | 0.129311 | 0.150000 | **1.160000** | 163 de 168 | 0 de 168 | separado |
| MeLi | MXN | 0.195004 | 0.195004 | **1.000000** | 0 de 13 | 13 de 13 | incluido |
| Amazon US | USD / MXN | 0.150000 | 0.128797 | 0.858649 | 0 de 24 | 0 de 24 | sin impuesto |

- Amazon MX: 168 productos y 1,509 pares. El rango de la razon es 1.134861 a
  1.276003; la mediana y 163 de 168 productos caen en 1.16. La diferencia no es
  una tasa supuesta: sale de dividir el cargo efectivo entre la cotizacion del
  mismo producto.
- MeLi: 13 productos y 72 pares. La razon es 1.000000 con rango 0.999978 a
  1.000000; la cotizacion ya trae el mismo importe que se cobra.
- Amazon US: la cotizacion esta en USD y el cargo en MXN. En la ventana hay 24
  productos y 53 pares; la tasa cotizada es 0.150000 y la cobrada 0.128797, con
  razon mediana 0.858649 y rango 0.634122 a 0.940567 (`cargo.txt`, bloque F).
  Ningun producto cae en 1.16 ni en 1.00. El cargo queda por debajo de la
  cotizacion: no hay un impuesto encima de la comision. La diferencia restante
  es de categoria y de base de conversion, no fiscal, y queda anotada como
  observacion.

Diferencia en dinero, un pedido de Amazon MX (el mayor de la ventana con la
comision exacta al 15%):

| Pedido | Venta MXN | Comision MXN | Tasa cobrada | Razon | Diferencia contra 12.931% |
|---|---|---|---|---|---|
| 701-6167430-7852254 | 2,689.0000 | 403.3600 | 0.150004 | 1.160032 | 55.6454 MXN |

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
  MX: 168 productos, 1,509 pares, tasa estimada mediana 0.150001, razon mediana
  **1.000000**, 163 de 168 productos dentro del 0.1% (`escenario.txt`, bloque E).
- MeLi: razon mediana 1.000000 y 13 de 13 productos dentro del 0.1%
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
- **Desconocido**: no queda ningun impuesto sobre comision sin adjudicar. Lo que
  queda sin explicar es la diferencia no fiscal de Amazon US (cargo por debajo
  de la cotizacion), que se anota como observacion y no como impuesto.

## Limites de la fuente

- La fuente demuestra el flujo de caja, no la acreditabilidad fiscal del IVA de
  la comision. K no la necesita: la decision del dueno del 2026-11-13 y el
  contrato S4 tratan el egreso como costo, y aqui solo se verifica que se
  cuente una vez.
- El `FBAFees` no es comision. Se midio para descartar un patron 1.16: la razon
  cobrado/cotizado por producto tiene mediana 1.046875 y 0 de 65 productos en
  1.16 (`cotizacion.txt`, bloque E). Su tratamiento queda fuera de K.
- Las cotizaciones de Amazon US estan en USD y sus cargos en MXN. La
  comparacion por razon es valida por ser adimensional, pero la diferencia no se
  convierte a una tasa fiscal.
- La foto es de un dia. El criterio no depende de un umbral elegido a mano: la
  razon 1.16 sale de los importes y se repite en 163 de 168 productos de Amazon
  MX.
