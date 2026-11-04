# Corrección de los cinco bloqueantes sobre `4d1fa179`

Ronda de corrección del veredicto que rechazó
`4d1fa17918f2e25c92d760549b7eb92ec2af740e`. Sin migraciones, sin cambiar el
grano campaña/día y sin tocar pujas, pausas, precios, contabilidad ni bridge.

## Qué se corrigió

1. **Identificador combinado.** La frontera valida el `source_report_id`
   completo (campaña, fecha, revisión y epoch máximo) contra 2048 bytes, con
   margen bajo el máximo btree de 2704. Dos campos de 2048 bytes ya no llegan
   juntos a PostgreSQL.
2. **UTF-8 inválido.** La lectura captura `sqlite3.Error` al hacer
   `fetchone()`: la fila ilegible se cuenta en `rows_skipped`, el snapshot se
   aborta sin escrituras y la corrida queda `ok=false` con `finished_at`.
3. **NULL coincidente.** Un NULL en origen, control y Orbit ya no pasa: la
   comparación exige valores conocidos. Una identidad NULL en cualquiera de
   los dos lados también falla.
4. **Ventana explícita.** `--desde/--hasta` son obligatorios y no se derivan
   del snapshot. La actividad anterior se excluye y se reporta; campañas fuera
   de la ventana, o actividad/NULL del control después de `--hasta`, fallan.
5. **DSN fuera de argv.** `CONCILIAR_DSN` va por entorno; los errores de
   conexión se redactan con `app.redaction.redact_dsn`/`scrub`. El runbook
   dejó de pasar el DSN como argumento.

## Evidencia

- `focales.txt`: 264 passed.
- `suite.txt`: 5587 passed, 1 warning (StarletteDeprecationWarning).
- `parent.txt`: las 7 regresiones nuevas fallan contra `4d1fa179`.
- `repros.txt`: los repros A/B/C y el de DSN ya no reproducen los bloqueantes.
- `operativo.txt`: snapshot real de accounting, 788 filas, 4 entidades,
  reintento no-op y `CONCILIACION OK` (788/788 filas, 197/197 días, 59 días de
  historia anterior excluidos y reportados).
- `perf-100k.txt`: tres ingestas de 100,000 filas en 6.840–6.887 s.
- `gates.txt`: ruff, formato, `bash -n`, `pre-commit` y `git diff --check`
  verdes; tres copias del conciliador byte-idénticas.

## Pendiente por infraestructura

Los diez lanes live en VM, las capturas PNG, la regresión Amazon base/HEAD y
el perfil de `/` base/HEAD sobre este commit no se ejecutaron: no hay VM ni
credenciales en este checkout.
