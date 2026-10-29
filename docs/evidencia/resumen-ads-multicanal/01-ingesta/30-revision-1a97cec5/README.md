# Corrección integral sobre `1a97cec5` (RESUMEN-ADS-01)

SHA revisado: `1a97cec50effe1dffbfd2f8c81fe9a7d4bed099a`.
SHA de código verificado (batería completa, perf y reinicio):
`9a52ef44600b9e8cec62efb1af8b550ba83c18d8`. El commit final agrega esta
evidencia; su árbol de código es idéntico (ver `sha-final.txt`).

## Qué se corrigió

1. **Modelo temporal por aceptaciones completas.** Cada corrida exitosa
   registra `as_of=<epoch>` (real) o `futuro=<epoch>` (cuarentena) y
   `snap=<sha256>` de todas las filas canónicas. El reintento exacto se
   reconoce contra esa aceptación anterior, no contra `v_metric_latest`. Un
   futuro aceptado no se reclasifica; un mismo as-of con otra huella se
   rechaza; un atrasado nunca aceptado se rechaza; una recopia real avanza su
   reloj; una marca futura no bloquea correcciones reales.
2. **Sellado seguro de motivos.** `ingest_run.skip_reason` escapa NUL y
   controles antes de persistir. Una moneda `MXN\x00USD` o un `campaign_id`
   con NUL y fecha inválida dejan `ok=false`, `finished_at`, todas las filas
   rechazadas, cero datos parciales y un motivo legible.
3. **Conciliación estricta.** Los tres `conciliar.py` son byte-idénticos y
   fallan ante campaña/día o día completo que falte o sobre, NULL convertido
   en cero, métrica distinta o moneda distinta de MXN. El control diario se
   acota a la ventana de campañas (la tabla diaria trae historia anterior sin
   campañas).
4. **Pruebas, perf y docs.** Offsets que desbordan `datetime`, `math` en el
   TestClient, umbral con p95 sin redondear, muestras crudas, hash SHA-256 de
   todas las respuestas base/HEAD, carga y conciliación antes de instalar el
   script de cron, y documentación de las dos marcas independientes.
5. **Guard de arquitectura.** El adaptador SQLite se separó a
   `app/meli/ads_source.py`; `app/meli/ads_ingest.py` queda bajo 900 líneas y
   se retiró la entrada que esta fase había agregado a
   `tests/test_architecture.py`.

## Repros antes/después

`antes.txt` es la salida real contra `1a97cec5` (worktree `/tmp/orbit-1a97`).
`despues.txt` es la salida real contra el árbol final. Los scripts están en
`scripts/revision/` y usan los helpers de la ronda anterior:

```bash
export PYTHONPATH="docs/evidencia/resumen-ads-multicanal/01-ingesta/30-revision-10266d91/scripts/revision:tests:."
uv run --frozen python docs/evidencia/resumen-ads-multicanal/01-ingesta/30-revision-1a97cec5/scripts/revision/orbit_review_1a97cec5_future_reclassified.py
uv run --frozen python docs/evidencia/resumen-ads-multicanal/01-ingesta/30-revision-1a97cec5/scripts/revision/orbit_review_1a97cec5_retry_future.py
uv run --frozen python docs/evidencia/resumen-ads-multicanal/01-ingesta/30-revision-1a97cec5/scripts/revision/orbit_review_1a97cec5_nul.py
uv run --frozen python docs/evidencia/resumen-ads-multicanal/01-ingesta/30-revision-1a97cec5/scripts/revision/reviewer_c_conciliar_missing.py
```

Los dos primeros salen 1 en `1a97cec5` y 0 en el árbol final. El de NUL sale
1 en `1a97cec5` (la corrida queda abierta) y 0 en el árbol final (corrida
sellada). El de conciliación sale 0 con `CONCILIACION OK` en `1a97cec5` y 1
con `CONCILIACION FALLA` en el árbol final, que es el comportamiento correcto:
el caso de Orbit vacío ya no pasa.

## Offsets que desbordaban `datetime`

`rojo-offsets-10266.txt` muestra `OverflowError: date value out of range` con
el código de `10266d91` para `0001-01-01T00:00:00+00:01` y
`9999-12-31T23:59:59-00:01`. `verde-offsets.txt` muestra `None` (rechazo) con
el árbol final. La prueba completa
`test_updated_at_con_fraccion_o_anio_desbordado_se_rechaza` incluye ambos
offsets y pasa en el árbol final.

## Pruebas y gates

- `rojo-pruebas-1a97.txt`: 5 de las nuevas pruebas fallan contra `1a97cec5`
  (Casos A/B, mismo as-of con otra huella y los dos NUL).
- `verde-pruebas.txt`: las mismas pruebas pasan en el árbol final.
- `pruebas.txt`: focales y módulo de conciliación.
- `suite.txt`: suite completa con `ORBIT_TEST_DSN` sobre el commit de código.
- `gates.txt`: Ruff, formato, `bash -n`, `git diff --check` y pre-commit.

## Perf y reinicio

- `perf-interleaved.txt`: 5 rondas de 30 pares base/head, p95 sin redondear,
  muestras crudas y SHA-256 idéntico de todas las respuestas.
- `perf-100k.txt`: tres ingestas frescas de 100,000 filas.
- `reinicio-conciliacion.txt`: dos procesos uvicorn, historia y latest
  estables, conciliación real 788/788 filas y 197/197 días con exit 0.

## Pendientes reales

- Los diez lanes live en VM con `control-ui` y capturas no se ejecutaron en
  este checkout: no hay VM de revisión.
- La regresión Amazon base/HEAD con credenciales AWS y fuente viva no se
  ejecutó: no están disponibles aquí. El perf HTTP base/HEAD sí se ejecutó con
  el fixture Amazon de 30 días por canal.
- No se hizo push, merge ni deploy. No se inició RESUMEN-ADS-02.
