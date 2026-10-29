# Corrección de los cuatro bloqueantes sobre `43b5cd27`

Ronda de corrección del veredicto que rechazó
`43b5cd27d9a01499b1f9737c33651fc39ef5aa9a`. Sin migraciones, sin cambiar el
grano campaña/día y sin tocar pujas, pausas, precios, contabilidad ni bridge.

## Qué se corrigió

1. **La revisión constante ocultaba correcciones.** Las tres rutas (snapshot
   nuevo, compatibilidad legada sin `snap=` y atajo de duplicado futuro)
   comparan moneda y todas las métricas, no solo `source_version`.
2. **La frontera aceptaba BLOB y texto gigante.** `ads_source` rechaza BLOB en
   `campaign_id`, `campaign_name` y `revision`, y texto de más de 2048 bytes
   (el límite de PostgreSQL es de bytes). La fila rechazada se cuenta en
   `ingest_run.rows_skipped` con `ok=false`, `finished_at` y cero escrituras.
3. **La precisión monetaria se redondeaba.** La ingesta y el conciliador
   toleran solo el ruido binario del REAL de SQLite (hasta 4 ulps); `1.000001`
   se rechaza en vez de convertirse en `1.0000`.
4. **La conciliación omitía controles inválidos.** Una fecha vacía o ilegible
   falla; un control con NULL entra a la ventana y falla (NULL no es
   inactividad); un duplicado en `v_metric_latest` también falla.

## Evidencia

- `focales.txt`: 257 passed (ingesta, conciliación, CLI y arquitectura).
- `suite.txt`: 5580 passed, 1 warning (StarletteDeprecationWarning).
- `parent.txt`: las 14 regresiones nuevas fallan contra `43b5cd27`.
- `repros.txt`: los repros de los revisores A y C ya no reproducen el bug.
- `operativo.txt`: snapshot real de accounting, 788 filas, 4 entidades,
  reintento no-op y `CONCILIACION OK` (788/788 filas, 197/197 días).
- `perf-100k.txt`: tres ingestas de 100,000 filas en 6.301–6.457 s.
- `gates.txt`: ruff, formato, `pre-commit` y `git diff --check` verdes.

## Pendiente por infraestructura

Los diez lanes live en VM, las capturas PNG y la regresión Amazon base/HEAD no
se ejecutaron: no hay VM ni credenciales en este checkout.
