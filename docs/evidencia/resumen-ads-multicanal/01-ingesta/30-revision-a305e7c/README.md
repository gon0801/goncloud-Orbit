# Corrección del veredicto sobre `a305e7c` (RESUMEN-ADS-01)

SHA revisado: `a305e7c0918f86ff33235652d1881b3d7bbe41f7`.
SHA de código verificado: `ef036a4b2f38bfc2703190535250e07102a57b30`.
El commit final agrega solo esta evidencia.

## Bloqueantes corregidos

1. **Piso legado con futuro.** `_estado_temporal` conserva el piso real
   heredado. Una aceptación futura escribe `piso=<epoch>`; sin marca real ni
   piso, el piso se calcula con las observaciones cuyo as-of no pertenece a
   una aceptación futura. El as-of entrante no cuenta como piso anterior.
   `antes.txt` muestra el costo 5 aceptado; `despues.txt` muestra el rechazo y
   `latest=20.0000`.
2. **Huella completa.** `snap=` es JSON con campaña, fecha, moneda, todas las
   métricas y la versión/revisión. Una revisión no oculta cambios de costo y
   los separadores internos no colisionan. `REVISION_HASH` y
   `HASH_DELIMITER` pasan de no-op silencioso a rechazo por mismo as-of.
3. **Snapshot vacío.** Es un no-op exitoso con `ok=true`, sin aceptación
   temporal. `EMPTY_FIRST` pasa de error a `snapshot vacio (no-op)`.
4. **NUL en campos.** `campaign_id`, `campaign_name` y `revision` con NUL se
   rechazan en `ads_source.py`. Los tres casos de `NUL_CAMPO` pasan de
   `rows_skipped=0` y DataError a `ok=false`, `rows_skipped=1` y rollback.
   `_texto_persistible` escapa también controles C1.
5. **Conciliación.** El origen revisa `currency`, no redondea precisión de más
   de 4 decimales y falla ante claves duplicadas de campaña/día o de control
   diario. Los cuatro casos de `CASE=...` pasan de `EXIT=0` a `EXIT=1`.
6. **Runbook.** `runbook.txt` demuestra el patrón anterior (`pipeline_rc=0`,
   `bytes=0`) y el nuevo (`test_s_rc=1` con SHA inválido, `test_s_rc=0` con el
   SHA válido). `docs/DEPLOY.md` incluye el comando de conciliación con ruta y
   DSN.
7. **Formato.** Se formatearon los 30 Python del cambio, no solo 14. Ruff,
   pre-commit, `bash -n` y `git diff --check` verdes.

## Secundarios atendidos

- Una aceptación vieja sin `snap=` reconoce su primer reintento exacto solo si
  coincide con toda la observación vigente.
- Un reintento ya no agrega otra aceptación temporal.
- Las reproducciones versionadas importan `_dinero` y `leer_origen` desde
  `ads_source.py`.
- El registro de reinicio incluye PID, parada y arranque.
- `99-cierre.md` actualiza sus conteos y el resumen de activación.

## Evidencia

- `antes.txt` y `despues.txt`: repros del veredicto contra `a305e7c` y contra
  el árbol final.
- `pruebas-rojo.txt`: 10 pruebas nuevas fallan contra `a305e7c`.
- `pruebas-verde.txt`: las mismas 10 pasan en el árbol final.
- `runbook.txt`: patrón de transferencia vacío y patrón con `test -s`.
- `perf-100k.txt`, `perf-interleaved.txt`, `reinicio-conciliacion.txt`,
  `gates.txt`, `suite.txt`: verificación de la ronda.

## Pendientes reales

- Diez lanes live en VM con `control-ui` y capturas PNG: no hay VM de revisión
  en este entorno.
- Regresión Amazon base/HEAD con credenciales AWS y fuente viva: no están
  disponibles aquí.
- No se hizo push, merge ni deploy. No se inició RESUMEN-ADS-02.
