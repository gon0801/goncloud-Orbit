# Resultado del bloque K: adjudicar la sospecha sobre impuesto de comisiones

- Base: `1191125c65841e8ec8d77448f7bbdcfeed29be1a` (master con BASE y A-J
  integrados).
- Rama: `arreglos-05/k`.
- Modelo: `opencode-go/deepseek-v4.1-flash` (OpenCode Go), por orden de David.
- Aviso del bloque: la estimacion explica los componentes que pudo conciliar y
  conserva desconocido lo que la fuente no permite demostrar.
- Evidencia: el commit que contiene este archivo.
- Lectura de produccion: 2026-11-25T12:46-12:51Z, solo lectura, por
  `docs/evidencia/arreglos-01/lee_prod.sh`.

## Decision K.2: el bloque cierra sin cambio de calculo

La fuente externa adjudica la sospecha y descarta que falte un costo:

- **Amazon MX: impuesto separado, ya incorporado una sola vez.** La cotizacion
  cobra `ReferralFee` al 12.931% y el cargo real al 15.000%: la razon por
  producto es 1.160000 y 161 de 169 productos caen en 1.16. La cotizacion no
  trae `tax_amount`. La politica `S4` vigente desde 2026-11-21
  (`amazon_mx_fba_s4_caja_real_2026_11` y `..._fbm_...`) suma
  `ReferralFee x 0.16` una sola vez en `fee_iva_referral` y `fee_total`
  (2,828 de 2,828 escenarios). La comision estimada con S4 contra la cobrada da
  razon mediana 1.000000 y 161 de 169 productos dentro del 0.1%.
- **MeLi: impuesto incluido en la comision.** Cotizacion y cargo coinciden en
  19.500% (razon 1.000000, 13 de 13 productos dentro del 0.1%). El multiplicador
  `1` de `meli_s4_caja_real_2026_11` es correcto y los 191 escenarios S4 no
  tienen `fee_iva_referral`.
- **Amazon US: sin impuesto sobre comisiones.** La cotizacion esta en USD y el
  cargo en MXN; ningun producto cae en 1.16 y el cargo queda por debajo de la
  cotizacion. La politica `amazon_us/fbm` usa `iva_divisor = 1` y
  `precio_incluye_iva = false`.
- **`impuesto_fee_pendiente` no se disparo nunca** (0 casos en 108 mil
  escenarios), porque ninguna cotizacion ha traido impuesto explicito. Se
  conserva la abstencion tal como esta.

Detalle, importes y diferencia: `K/conciliacion.md`. Datos saneados:
`K/cotizacion.txt`, `K/cargo.txt`, `K/escenario.txt` y `K/politica.txt`.

## Por que no hay `N k`

La aceptacion del plan pide `N k` solo cuando cambia el calculo. Aqui no cambia:
el impuesto de la comision ya esta incorporado una sola vez por la politica S4
de la migracion `0093`, y MeLi y Amazon US no lo requieren. Los cuatro casos que
`N k` cubriria ya tienen control vigente:

| Caso | Control vigente |
|---|---|
| `TaxAmount` anidado | `_tiene_tax_amount` recorre `included_fee_details` (`app/estimacion_venta.py:303`); `tests/test_estimacion_venta.py::test_tax_amount_anidado_impuesto_fee_pendiente` y `tests/test_precio_reglas.py` (1452, 2336) |
| Total inclusivo | `_normalizar_detalle` exige `FinalFee = FeeAmount - FeePromotion` y `_validar_fee_detalles` exige `suma FinalFee == total`; si no, `fee_incompatible` |
| Impuesto separado | `multiplicador_referral_fee` y `fee_variable_efectivo`; `_normalizar_fee` suma `ReferralFee x (mult - 1)` una vez; el motor de precios aplica `fee_variable_multiplicador` desde el mismo escenario (`app/precio/objetivo.py:214`, `app/precio/reglas.py:394`) |
| Dato ausente | `fee_ausente`; `tax_amount` ausente queda `None`, nunca cero; `impuesto_fee_pendiente` solo con impuesto presente y distinto de cero |

No se agrega migracion, version ni reversa porque no hay DDL ni cambio de
formula. La version vigente sigue siendo la S4 de `0093`.

## Archivos cambiados

Solo evidencia, sin codigo:

- `docs/evidencia/arreglos-05/K/plan.md`: plan de adjudicacion previo.
- `docs/evidencia/arreglos-05/K/conciliacion.md`: K.1.
- `docs/evidencia/arreglos-05/K/resultado.md`: este archivo.
- `docs/evidencia/arreglos-05/K/lee_previa.sh` y `cotizacion.sql`, `cargo.sql`,
  `escenario.sql`, `politica.sql`: lectura reproducible.
- `docs/evidencia/arreglos-05/K/cotizacion.txt`, `cargo.txt`, `escenario.txt`,
  `politica.txt`: salidas saneadas.

No se toco `app/`, `tests/`, `migrations/`, `plans/` ni `plans/manifest.json`.

## Verificacion

- `uv run --frozen --python 3.12 ruff check app tests tools`: `All checks
  passed!`
- `uv run --frozen --python 3.12 ruff format --check app tests tools`: `541
  files already formatted`.
- Vecinos de estimacion y precios que cubren el impuesto y el multiplicador
  (`tests/test_estimacion_venta.py`, `tests/test_estimacion_fees.py`,
  `tests/test_estimacion_s4_caja_real.py`, `tests/test_precio_reglas.py`):
  **306 passed, 1 failed**.
- El unico fallo es
  `tests/test_precio_reglas.py::test_r4_g8_barrido_float_entra_a_subpaquetes`.
  No es del bloque: `git diff --stat 1191125c -- app tests migrations` sale
  vacio y el mismo test falla sobre el arbol de la base. Es un defecto de la
  prueba en Windows: compara `"sub/flotante.py"` contra una ruta con `\`. En
  Linux la ruta usa `/` y la prueba pasa. Se anota para su dueno; no se toca
  aqui porque K no cambia codigo.
- No se corrio la suite completa: el bloque es `lane: fast` y no toca codigo.

## Reversa

No hay DDL, ni migracion, ni binario nuevo. Revertir el PR quita solo los
archivos de evidencia. La politica S4 y el multiplicador siguen como estaban.

## Fase 2

`lane: fast`: este bloque no lleva despliegue propio. No se ejecuto
`desplegar.sh` ni `checklist.sh` y no hay recibo de despliegue.

## Seguimiento (P2)

- La diferencia no fiscal de Amazon US (cotizacion 15.000% contra cargo
  12.8797% en la ventana) queda anotada, no adjudicada. Podria venir de la
  categoria o de la base de conversion; no es un impuesto y no es un costo
  omitido. Su dueno es la estimacion y la ingesta, no K.
- 8 de 169 productos de Amazon MX caen fuera del 0.1% de la razon 1.16
  (promociones, cambios de categoria o devoluciones). No cambian la mediana.
- `test_r4_g8_barrido_float_entra_a_subpaquetes` falla solo en Windows por el
  separador de ruta. Mejora de prueba para su dueno.
