# A.1 — Borrador pre-0.5 (NO aplicable, insumo)

Fecha: 2026-09-07. Autor: lead.

Este borrador se escribio ANTES de conocer el plan oficial v1.4
(PR #197) y **no implementa nada**: el plan exige acta 0.5 antes de
A.1. Se conserva como insumo de diseno, no como migracion.

Contenido:

- `0024_reputacion.borrador.sql`: `reputation_snapshot` + `review_event`
  append-only con `prohibir_mutacion`, `reputation_alert` con sellado
  por columna, FKs a `listing`, GRANTs por rol.
- `test_reputacion_migracion.borrador.py`: 5 tests (estatico + PG
  temporal), verdes al archivar (suite 1576 passed).

Divergencias conocidas vs plan oficial (resolver en 0.5):

- El plan preve tablas de preguntas MeLi y reputacion seller como
  entidades propias; el borrador mete preguntas/disputas en `extra`.
- Nombres de archivos A.2–A.4 (`app/reputacion.py` unico) y umbrales
  D3 los fija el acta, no este borrador.
- Numero 0024: confirmar contra HEAD al aplicar (A.1 DoD).

No mover a `migrations/` sin acta 0.5 aprobada.
