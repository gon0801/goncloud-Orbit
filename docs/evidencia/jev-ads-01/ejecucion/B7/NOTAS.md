# B7 — R1 y R4 (prerrequisitos de 2.3)

Autor: Claude (lead), por orden directa de David (2026-10-04). Revision
independiente con `cross-review.ps1`: codex (ronda 1) y kimi (ronda 2, solo el
delta). PR #397.

## R1. created_at de jev_revision es la insercion real

- `migrations/0050_jev_revision_created_at.sql`: `created_at DEFAULT
  clock_timestamp()`; el trigger `jev_revision_tiempos` fija `NEW.created_at :=
  clock_timestamp()` (un valor explicito se ignora) y exige
  `decided_at/captured_at <= created_at`; corrige el typo del COMMENT de
  `jev_ficha_version`. `0049` no se edita.
- `migrations/0050_reversa_jev_revision_created_at.sql`: devuelve el DEFAULT,
  el cuerpo del trigger y el COMMENT de 0049.
- Rojo antes del arreglo, con solo 0049:
  `uv run --frozen python -m pytest -q tests/test_jev_catalogo.py -k created_at_es_la_insercion`
  dio `1 failed` (`created_at` 56 ms antes de la captura hecha dentro de la
  misma transaccion).
- Verde con 0050: `tests/test_jev_catalogo.py` 23 passed; jev_*, api_fabrica y
  api_dashboard 218 passed; esquema y arquitectura 138 passed.
- Mutantes, todos mueren: sin fijar `created_at` (2 failed); sin la regla de
  `decided_at` (1); sin la de `captured_at` (3); reversa sin `now()` (1);
  reversa sin COMMENT (1).
- Ronda 1 (codex): BLOQUEANTE, un `created_at` explicito viejo se aceptaba y
  la cronologia se podia falsear; corregido con el fijado en el trigger.
  Ronda 2 (kimi, delta): sin bloqueantes. CodeRabbit: alcance del COMMENT y
  cota superior en la prueba, corregidos. ai-review: el cambio a `cc:DONE` se
  hace en el PR de cierre con PR y SHA, y la asercion estatica de 0050.
- Ensayo sobre el esquema REAL de prod: `docs/evidencia/jev-ads-01/ejecucion/2.3/`.

## R4. Reutilizacion entre revisiones

Decision del lead: se conserva la prohibicion (conservadora, coincide con
`0049` y el asesor, sin migracion). Escrita en
`docs/superpowers/specs/2026-10-03-jev-ads-design.md`, seccion "Contrato con
Jev y reutilizacion". Reutilizar entre revisiones exige un spec nuevo.
