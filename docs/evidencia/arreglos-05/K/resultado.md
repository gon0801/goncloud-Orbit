# Resultado del bloque K: adjudicar la sospecha sobre impuesto de comisiones

- Base: `1191125c65841e8ec8d77448f7bbdcfeed29be1a` (master con BASE y A-J
  integrados).
- Rama: `arreglos-05/k`.
- Modelo: `opencode-go/deepseek-v4.1-flash` (OpenCode Go), por orden de David.
- Aviso del bloque: la estimacion explica los componentes que pudo conciliar y
  conserva desconocido lo que la fuente no permite demostrar.
- Evidencia: el commit que contiene este archivo.
- Lectura de produccion: 2026-11-25T13:21-13:23Z, solo lectura, por
  `docs/evidencia/arreglos-01/lee_prod.sh`.

## Decision K.2 (actualizada 2026-11-25): MX, MeLi y Amazon US adjudicados y cerrados

La fuente externa completa (incluyendo SP-API Finances v2024 en USD nativo para
`ATVPDKIKX0DER`, `K/finances_us_v2024.txt`) adjudica las **tres plataformas**:

- **Amazon MX: impuesto separado, incorporado una sola vez en S4 (`0093`).** La
  cotizacion cobra `ReferralFee` al 12.931% y el cargo efectivo al 15.000%: razon
  por producto 1.160005 (110/113 productos en 1.16). Politica S4 vigente con
  `referral_fee_iva_multiplicador = 1.16`.
- **MeLi: impuesto incluido en la comision (`0093`).** Cotizacion y cargo coinciden
  en 19.500% (razon 1.000000, 10/10 productos). Multiplicador `1` correcto.
- **Amazon US: impuesto separado, incorporado una sola vez en S4 (`0100`).**
  Product Fees cotiza `ReferralFee` al `15.00%` sin `TaxAmount`. SP-API Finances
  v2024 devuelve explicitamente `Commission = Base (15.00%) + Tax (16.00% sobre
  Base) = 17.40%` en **164/164 items** (2026-01, 2026-05 y 2026-10/11, 0 anomalias)
  y `531/531` pedidos historicos con FX exacto (`2025-12` a `2026-11`, mediana
  `0.173985`). El valor previo `0.8586` era un artefacto de dividir `Commission`
  entre `sale_gross = ItemPrice + Shipping + Tax`. `MarketplaceFacilitatorTax` es
  sales tax de EE. UU. con efecto neto `$0.00 USD` (`retencion_iva_tasa = 0`).
  `ShippingHB` ya entra con IVA incluido en `precio_envio_muestra`.
- **`impuesto_fee_pendiente` se conserva intacto** (`fee_tax_amount_requiere_politica = true`):
  si SP-API empieza a enviar `TaxAmount != 0`, el calculador abstiene para nunca
  cobrar IVA dos veces.

## Cambio de calculo y reversa (K.2 — Forma A + re-anclaje US)

- **Migracion `migrations/0100_arreglos05_k_us_s4.sql`** y su reversa
  **`migrations/0100_reversa_arreglos05_k_us_s4.sql`**: cierran `repricing02_fbm_us`
  (`S3`) y siembran `amazon_us_fbm_s4_caja_real_2026_11` (`S4`) con
  `referral_fee_iva_multiplicador = 1.16`, `retencion_iva_tasa = 0`,
  `isr_tasa = 0.0207`, `iva_divisor = 1`, `precio_incluye_iva = false` y
  `fee_tax_amount_requiere_politica = true`. Cero cambios de formula en `app/`:
  `estimacion_venta`, `estimacion_reader`, `precio/objetivo`, `precio/reglas` y
  `precio/compuerta_io` ya soportan `S4` y eximen la transicion `S3 -> S4` en
  `fee_variable`.
- **Re-anclaje de goals US (`tools/reancle_goals_s4.py`)**: se extiende
  `--platform amazon_us` y la bandera explicita opt-in `--acotar-banda` (default
  fail-closed) para preservar `P*` en los 99 goals activos de US (97 dentro de
  banda `[0.10, 0.60]` con `|P*_S4 - P*_S3| <= $0.01`, y 2 en `12.06%` acotados
  al piso `10.00%` cuyo `P*` sigue debajo de `p_actual = $48.00`), con reversa
  exacta via `--revertir`.

## Pruebas `N k` (rojo/verde)

Archivo: `tests/test_arreglos05_k_impuesto_comision.py` (9 pruebas):

- **Rojo contra `da380023` (master previo):** fallaron las pruebas de migracion
  `0100` (`FileNotFoundError` / `falta 0100_arreglos05_k_us_s4.sql`) y re-anclaje US.
- **Verde tras implementar `0100` y `reancle_goals_s4`:**
  - `TaxAmount` anidado y top-level -> `impuesto_fee_pendiente`.
  - Total inclusivo (`FinalFee` conciliado contra `fee_total`; desajuste -> `fee_incompatible`).
  - Impuesto separado (`1.16` en US S4 conciliado al centavo contra muestras reales
    de SP-API Finances v2024; `1.00` en MeLi; coherencia en `objetivo.paso` y `reglas.decidir`).
  - Dato ausente (`fee_ausente`; `tax_amount = None` nunca tratado como cero).
  - Migracion `0100` ida, sonda, reversa limpia, reversa con escenarios S4 y
    re-aplicacion el mismo dia UTC en PG16 real.
  - Re-anclaje US en PG16: preserva `P*`, fail-closed sin `--acotar-banda`, acota
    con `--acotar-banda`, idempotente y reversible al centavo.
  - Compuerta de precios en transicion `S3 -> S4` de `amazon_us/fbm`: pasa ante
    `+16%` en `fee_variable` y retiene ante salto real de costo.
  - Corrida conjunta en PG16: **417 passed** (`test_arreglos05_k_impuesto_comision.py`,
    `test_estimacion_s4_caja_real.py`, `test_estimacion_venta.py`,
    `test_estimacion_fees.py`, `test_precio_reglas.py`, `test_architecture.py`),
    `ruff check` y `ruff format --check` limpios.

Detalle y datos saneados: `K/conciliacion.md`, `K/finances_us_v2024.txt`,
`K/cotizacion.txt`, `K/cargo.txt`, `K/escenario.txt` y `K/politica.txt`.

## Archivos cambiados

Primera entrega (adjudicacion MX y MeLi, PR #773):

- `docs/evidencia/arreglos-05/K/plan.md`, `conciliacion.md`, `resultado.md`,
  `lee_previa.sh`, `{cotizacion,cargo,escenario,politica}.{sql,txt}`.

Cierre Amazon US (2026-11-25, Forma A + re-anclaje US):

- `migrations/0100_arreglos05_k_us_s4.sql`: cierra `repricing02_fbm_us` (`S3`) y
  siembra `amazon_us_fbm_s4_caja_real_2026_11` (`S4`, `1.16`, `retencion_iva_tasa = 0`).
- `migrations/0100_reversa_arreglos05_k_us_s4.sql`: reversa segura (borra limpio
  si 0 escenarios `S4`; si existen escenarios `S4`, cierra hoy UTC y abre continuacion `S3`).
- `tools/reancle_goals_s4.py`: soporte `--platform amazon_us` y bandera opt-in
  `--acotar-banda` (default fail-closed; reversible via `--revertir`).
- `tests/test_arreglos05_k_impuesto_comision.py`: 9 pruebas `N k` en PG16.
- `docs/evidencia/arreglos-05/K/finances_us_v2024.txt`, `conciliacion.md`,
  `resultado.md`, `Z/cobertura.tsv`, `Z/resultado.md` y `plans/arreglos-05.md`.

No se toco `app/`: el calculador `S4`, `estimacion_reader`, `precio/objetivo`,
`precio/reglas` y `precio/compuerta_io` ya implementan la formula y eximen la
transicion `S3 -> S4`.

## Verificacion

- `uv run --frozen ruff check app tests tools`: `All checks passed!`
- `uv run --frozen ruff format --check app tests tools`: `542 files already formatted`.
- `ORBIT_TEST_DSN=... uv run --frozen --python 3.12 python -m pytest -q tests/test_arreglos05_k_impuesto_comision.py tests/test_estimacion_s4_caja_real.py tests/test_estimacion_venta.py tests/test_estimacion_fees.py tests/test_precio_reglas.py tests/test_architecture.py`: **417 passed** (0 fallos, 0 skips).
- `pre-commit run --all-files`: gitleaks y guardas sin hallazgos.

## Reversa

- Esquema/politica: `migrations/0100_reversa_arreglos05_k_us_s4.sql` (regresa
  `amazon_us/fbm` a `S3` de inmediato sin borrar escenarios referenciados).
- Metas `precio_goal` de US: `tools/reancle_goals_s4.py --revertir --platform amazon_us --lote reancle_s4_us_2026_11`
  restaura las 99 metas exactas previas via `goals_write`.

## Fase 2

Despliegue en `goncloud` con `tools/despliegue/desplegar.sh` y
`tools/despliegue/checklist.sh`, seguido del re-anclaje reversible de las 99
metas activas de `amazon_us` con `tools/reancle_goals_s4.py --platform amazon_us --acotar-banda --lote reancle_s4_us_2026_11`
(precios conserva `precio_modo_global = shadow`).

## Seguimiento (P2)

- 3 de 113 productos de Amazon MX caen fuera del 0.1% de la razon 1.16
  (promociones, cambios de categoria o devoluciones). No cambian la mediana.
- El `FBAFees` no es comision y no muestra patron 1.16 (0 de 65 productos); la
  migracion `0093` ya asento que llega con IVA.
- `test_r4_g8_barrido_float_entra_a_subpaquetes` falla solo en Windows por el
  separador de ruta. Mejora de prueba para su dueno.
