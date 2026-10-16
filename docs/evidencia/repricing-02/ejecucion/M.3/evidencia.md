# Evidencia M.3: margen meli/meli

## Alcance

- `app/meli/estimacion.py` (nuevo): `resolver_oferta_meli` (ultima
  observacion PG; MXN-only del universo; 6 h simetrico al trigger),
  `CotizadorMeli` (GET `/sites/MLM/listing_prices`; parse
  `listing_fee_amount` + `currency_id` fail-closed; rechaza >4dp),
  `partir_fees_meli`, canonicas + huellas meli.
- `app/estimacion_universo.py`: registro `("meli", "meli")` con
  `CotizadorMeli`.
- `app/estimacion_repository.py`: `_sembrar_meli` (una cuenta por
  miembro; fila del que manda por (C+L) exactos; otros como
  `miembro:<sku>`), `_canonica_miembros`, colapso de `error_code` a
  vocabulario cerrado en `_validar_fee_compatible`.
- `app/estimacion_ingest.py`: rama meli (resolver PG + cotizador +
  canonicas), `_Tally` compartido, fail-closed antes de abrir el run,
  sello `ok=false` si el ctor Amazon falla, `ProductFeesClient`
  perezoso.
- `migrations/0073_precio02_meli_miembro_costo.sql` (+ reversa):
  costo del miembro + miembro de la unidad + miembro exigido en
  disponible. Amazon intacto.
- `politica_meli.sql`: politica `meli/meli` (ISR 0.025 neto de M.2).
- Decisiones de diseno: `diseno-vs-trigger.md` (ancla vs manda).

## Pruebas

- `tests/test_precio02_meli_cotizador.py`: 11 tests.
- `tests/test_precio02_escenario_meli.py`: 30 tests.
- `tests/test_migracion_0073.py`: 6 tests.
- Total M.3: 47 passed, 0 skips.
- Vecinos verdes: estimacion_* (7 archivos), escenario_fbm,
  contratos, arq_precio_f: 298 passed en conjunto, 0 skips.

## Mutantes

39/39 muertos, detalle en `mutantes.md`.

## Adenda CodeRabbit (cierre): CR-1 + CR-2a (+3 tests netos,
mutantes 41/41). Ver `panel.md` y `mutantes.md`.

## Panel

4/4 revisores (Muse A BLOQUEADA->resuelta, Muse B, GLM 5.3,
GLM 5.2) + 2 rondas de fixes. Detalle en `panel.md`. Pendiente
para D.4: verificar `sale_fee_amount` incluye-fijo contra categoria
con fijo > 0 (M.0 solo trae fijo 0).

## Repro

`./comprobar.sh` (requiere Postgres loop en 5432 + ORBIT_TEST_DSN).
