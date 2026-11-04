# Reglas de revisión de Orbit

Migradas desde `.coderabbit.yaml` (`path_instructions`). Tono: directo y técnico, señala el riesgo concreto sin preámbulos.

Los documentos también se revisan. `docs/`, `plans/` y `docs/evidencia/` pueden traer contradicciones reales: números que no cuadran entre plan y spec, una cobertura invalidada que sigue declarada, o una dependencia que deja correr una fase antes de tiempo. Eso cuenta como hallazgo.

## `migrations/**/*.sql`

Son reglas innegociables del repo (`docs/CONTEXTO.md`). Cada violación es High como mínimo, nunca Low.

- Todo monto lleva columna `currency`. Un `SUM()` que pueda mezclar monedas es un bug, no estilo. Prohibido float para dinero.
- Las tablas de hechos son append-only, con clave `(entidad, metric_date, observed_at)` y un trigger que prohíbe UPDATE/DELETE. Un UPSERT in-place sobre métricas invalida los backtests sin dar síntoma.
- Dato faltante es fila ausente o NULL, jamás una constante de fallback. Un FX default de 20.5 infló el revenue en 28,549 MXN.
- Ninguna acción irreversible sin su reversa implementada antes.
- Un CHECK no puede depender de la TimeZone de la sesión. Los casts `timestamptz::date` son STABLE, no IMMUTABLE, y Postgres los acepta sin quejarse para luego evaluarlos distinto en cada sesión. Ese tipo de invariante va en un trigger con UTC fijado en la expresión.

## `tests/**/*.py`

Una prueba que pasa igual antes y después del arreglo no prueba nada. Marca:

- Tests que afirman sobre el AST un invariante que el SQL no impone de verdad.
- Guards de entorno que fallan abierto. Un skip silencioso se lee como cobertura y no lo es.
