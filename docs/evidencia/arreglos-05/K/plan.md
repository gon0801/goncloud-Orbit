# Bloque K: plan de adjudicacion de la sospecha sobre impuesto de comisiones

Estado: plan escrito antes de cualquier commit del bloque. Fecha: 2026-11-25.

- Base del bloque: `1191125c65841e8ec8d77448f7bbdcfeed29be1a` (master con BASE y
  A-J integrados).
- Rama: `arreglos-05/k`.
- Alcance: filas K.1 y K.2 de `plans/arreglos-05.md`. `lane: fast`, sin
  despliegue propio, sin encender precios ni pausa por producto.

## Que se va a adjudicar

La sospecha es que la estimacion por venta no cuesta el impuesto (IVA) que la
plataforma cobra sobre la comision. La duda no es si existe un impuesto, sino si
ese impuesto **ya esta incluido** en la cotizacion o en el precio, si **viaja
separado** como cargo adicional, o si la fuente no permite demostrarlo. No se
infiere una tasa ni una regla fiscal desde la sospecha.

Contrato vigente que se lee antes de proponer nada:

- `app/estimacion_venta.py`: la comision (`F`) es `TotalFeesEstimate`
  conciliada con la suma de `FinalFee`; `_tiene_tax_amount` (linea 303) recorre
  los detalles incluidos y provoca la abstencion `impuesto_fee_pendiente` si
  cualquier `TaxAmount` es distinto de cero.
- `app/estimacion_venta.py::multiplicador_referral_fee` y `fee_variable_efectivo`:
  la politica `S4` escala solo la parte variable (ReferralFee) con
  `referral_fee_iva_multiplicador`.
- `migrations/0093_estimacion_s4_caja_real_mx.sql`: S4 siembra
  `referral_fee_iva_multiplicador = 1.16` en Amazon MX y `1` en MeLi, y conserva
  `fee_tax_amount_requiere_politica = true`.
- `docs/evidencia/arreglos-03/P-G/aceptacion.md`: la cotizacion MeLi conserva
  fecha original y arbol completo de fees en `Fees.detalle`.
- `docs/evidencia/margen-estimado-01/0.3/acta.md`: `P` incluye IVA, `I = P/1.16`,
  y una cotizacion con `TaxAmount` no cero deja el principal en `null` con
  `impuesto_fee_pendiente`.

## Fuente externa y respaldo de solo lectura

- Cargo externo: los reportes de liquidacion de Amazon (SP-API Finances) y los
  cargos de MeLi ya ingestados en `ledger_event`; la vista `v_ledger_cargo`
  (migracion `0086`) quita la comision estimada cuando ya llego la real del
  mismo pedido y es la fuente de cargos del margen. Como la vista deja la
  estimada cuando no hay real, la conciliacion exige ademas
  `split_part(coalesce(source_event_id, ''), '|', 2) <> 'fee_estimate'` y cuenta
  las estimadas aparte: una estimacion no es un cargo.
- Cotizacion integra: `estimacion_fee_observation` con `estado = 'success'`
  (Product Fees para Amazon y `CotizadorMeli` para MeLi, ambos con
  `fee_details` completo, `quoted_price_amount`, moneda y `fees_estimated_at`).
- Escenario: `estimacion_escenario` (`formula_version`, `estado`, `motivos`,
  `componentes`), `estimacion_politica_version` vigente y el codigo de
  `app/estimacion_venta.py` en el SHA base.
- Consulta: solo lectura con `docs/evidencia/arreglos-01/lee_prod.sh` (rol
  `orbit_read`, transaccion `read_only`). Nunca el DSN, `.env` ni
  `ORBIT_SECRETS_DIR` de produccion.

## Consultas y comparaciones (solo lectura)

Todas por plataforma (`amazon_mx`, `amazon_us`, `meli`), moneda
(`MXN`/`USD`) y fecha (mes UTC de la fuente). Salida saneada en
`K/cotizacion.txt`, `K/cargo.txt`, `K/escenario.txt` y `K/politica.txt`.

1. **Cotizacion integra** (`K/cotizacion.txt`)
   - Conteo de `estimacion_fee_observation` por plataforma, moneda, mes y
     estado.
   - Cuantas cotizaciones `success` traen `tax_amount` no cero en cualquier
     nivel de `fee_details` (incluidos los anidados). Cero filas con impuesto
     significa que la cotizacion no expone un impuesto separado.
   - Tasa cotizada de la comision variable por plataforma: `final_fee` de
     `ReferralFee` (Amazon) o `SaleFee` + `SaleFeeFixed` (MeLi) dividido entre
     `quoted_price_amount`, con la mediana y el rango.

2. **Cargo externo** (`K/cargo.txt`)
   - Tipos de cargo (`fee_type`) y montos que existen hoy por plataforma y
     moneda, con signo (el esquema fija `fee <= 0`).
   - Tasa cobrada de la comision variable: para cada `order_id` con venta y con
     cargo de comision, `|fee| / sale`; se agrupa por producto de la venta y
     moneda.
   - La razon `cobrado / cotizado` por producto y moneda, con su mediana y su
     dispersion. Esta razon es la que adjudica: `1.0000` sugiere ya incluido;
     `1.16` (o el factor de IVA de esa moneda) sugiere impuesto separado ya
     cobrado por la plataforma.

3. **Escenario y politica** (`K/escenario.txt`, `K/politica.txt`)
   - Distribucion de `estimacion_escenario` por plataforma, estado y motivo;
     cuantas abstenciones son `impuesto_fee_pendiente`.
   - Politica vigente por universo: `formula_version`, y en S4 el valor de
     `referral_fee_iva_multiplicador`, `iva_divisor`, `retencion_iva_tasa` e
     `isr_tasa`.

## Criterio explicito de adjudicacion

Por plataforma y moneda, con al menos 10 pares venta/comision en el periodo:

- **Incluido**: la razon `cobrado / cotizado` de la comision variable es
  `1.0000` dentro de la tolerancia de redondeo de 4 decimales y/o la
  cotizacion trae el impuesto dentro del importe total de forma demostrable
  (`TaxAmount` no cero que ya forma parte del `TotalFeesEstimate`). En este caso
  no se suma nada: sumarlo dos veces seria el defecto.
- **Separado**: la razon es consistente con el factor de impuesto de la moneda
  (`1.16` en MXN) y la cotizacion no trae `TaxAmount`, mientras el cargo real
  si lo cobra. Entonces el contrato debe incorporarlo **una sola vez**; se
  verifica si la politica vigente ya lo incorpora (`referral_fee_iva_multiplicador`).
  Si ya lo incorpora, no hay codigo nuevo: el bloque documenta que esta
  incorporado una vez y con que version.
- **Desconocido**: no hay pares suficientes, la razon no es estable entre
  productos (mezcla de categorias o de promociones), o el cargo externo no
  permite aislar el impuesto. Entonces se conserva la abstencion
  `impuesto_fee_pendiente` y K queda **abierto**. No se escribe 16% ni cero.

La razon se mide; la tasa no se asume. Si la razon no es `1.0000` ni el factor
de IVA conocido de la moneda, es desconocido, no una tasa nueva.

## Que hara K.2 segun el resultado

- Descarta la sospecha (incluido o separado ya incorporado una sola vez): K
  cierra con evidencia, sin cambio de calculo y sin `N k`; se documenta por que
  el calculo no cambia y que control vigente se conserva.
- Confirma un costo omitido conforme al contrato: se incorpora una sola vez con
  version y reversa, con `N k` cubriendo TaxAmount anidado, total inclusivo,
  impuesto separado y dato ausente. Requiere TDD rojo contra el padre real.
- No adjudica: abstencion conservada, K abierto, tarea de evidencia registrada.

## Verificacion prevista

- Si no hay cambio de calculo: `ruff check` y `ruff format --check` sobre `app`,
  `tests` y `tools`; corrida de los vecinos de estimacion y de precios que
  tocan `impuesto_fee_pendiente` y el multiplicador
  (`tests/test_estimacion_venta.py`, `tests/test_estimacion_fees.py`,
  `tests/test_estimacion_s4_caja_real.py`, `tests/test_precio_reglas.py`), sin
  cerrar por un archivo de evidencia.
- Si hay cambio de calculo: rojo primero contra `1191125c`, PG desechable,
  migracion aditiva numerada con `_reversa_`, sonda en las primeras 10 lineas,
  grants minimos, pruebas de ida y reversa con los roles reales, y `N k` con
  los cuatro casos pedidos por el plan.

## Ajuste posterior a la revision (2026-11-25)

La revision de la ronda 1 pidio dos reglas mas estrictas, y se aplicaron antes
de publicar la cabeza final:

- **Ventana comun.** La comparacion por producto limita las dos poblaciones a la
  misma ventana (MX y MeLi desde 2026-09-01; US desde 2026-10-15). Antes, una
  liquidacion de enero podia promediarse con una cotizacion de noviembre. Los
  pedidos sin cotizacion contemporanea quedan fuera y se cuentan.
- **Un producto mapeado.** Un pedido entra a la mediana por producto solo si
  todas sus lineas estan mapeadas y apuntan al mismo producto; los descartados
  se cuentan aparte. Antes, `min(product_id)` atribuia la comision del pedido
  entero a un producto. En la ventana se descartaron 0.
- **Cargos efectivos.** Se excluye la comision estimada y se cuenta aparte.
- **Sin identificadores.** No se publican `order_id` en la evidencia.

Con esas reglas, Amazon US no cumple el criterio de adjudicacion (su razon no es
1.0000 ni el factor de IVA de la moneda), asi que se clasifica **desconocido** y
K queda abierto para US, con tarea de evidencia. Amazon MX y MeLi si adjudican.
