# REPUTACION 01 / A.4 — Seller + preguntas (implementado por el lead)

Fecha: 2026-09-08.

## Decisiones

- D-LEAD-A4-1: migracion 0026 — UNIQUE de `meli_question` incluye
  `observed_at` (re-observacion diaria; pendientes = DISTINCT ON
  mas reciente). Sin esto el estado quedaba congelado.
- D-LEAD-A4-2: disputa "abierta" = status != 'closed' (E/0.3 solo
  mostro open/closed; estados nuevos cuentan como abiertas =
  fail-noisy hacia la alerta, nunca silencioso).
- D-LEAD-A4-3: `--fuente meli` corre 3 syncs en serie (snapshots +
  seller + questions), cada uno su run/tx; resumen fusionado con
  `run_ids`. Cero envios (solo GET + refresh token).
- Paginado limit/offset con guardias: corta en pagina corta, total
  alcanzado, ids repetidos (API que ignora offset) o 20 paginas.
- D-LEAD-A4-4: `app/reputacion.py` llego a 1096 lineas (guardrail 900,
  test_architecture muerde) → partido por responsabilidad real, no
  por partir: `reputacion_clientes.py` (IO, 280), `reputacion_plan.py`
  (puro, 234), `reputacion.py` (syncs+CLI, 626) con re-exports para
  compat. Enmienda tecnica al plan §propiedad (un archivo): el
  guardrail del repo manda; documentado aqui.

## Evidencia

- `tests/test_reputacion.py`: 27 passed (5 nuevos A.4: planes
  seller/question, sync+idempotencia seller, re-observacion y
  pendientes questions; fin-a-fin meli ahora 7 filas, 3 runs).
- Suite completa + ruff + pre-commit: ver PR.
