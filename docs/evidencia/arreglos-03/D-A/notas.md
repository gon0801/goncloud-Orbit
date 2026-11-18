# D-A — notas de implementación

Dos puntos en los que la fila D-A.2 no se pudo seguir al pie de la letra. Ninguno cambia la forma
del diseño (B-ING): las pruebas entran por `descarga` + `escribir_grupo`, como pide la fila.

## 1. Dos `pytest.raises` se quedan: `descarga` relanza los fallos globales

La fila dice que donde hay `pytest.raises` alrededor de `sync_metrics`/`sync_placements` la prueba
afirma `res.ok is False`. Eso vale para los fallos de grupo (un 4xx al crear, el escritor que
revienta, el reloj de la base que falla): `descarga` los devuelve en `ResultadoGrupo`. Pero dos de los
casos listados son fallos globales, y ahí `descarga` sella las runs abiertas como `global_failed` y
relanza la excepción, así que no existe ningún `res` del que leer `ok`:

- `tests/test_reports_pipeline.py` (era `:1463`): «Error al listar perfiles», `AdsAuthError` de
  `evaluar_perfiles`. Sigue con `pytest.raises(AdsAuthError)` y conserva sus afirmaciones sobre
  la run `global_failed` sin plataforma.
- `tests/test_placements.py` `test_fallo_de_transporte_sella_run_propia_y_no_toca_principal`: la red
  cae al crear con un `AssertionError` (no es `AdsApiError` ni `AdsReportsError`). Sigue con
  `pytest.raises(AssertionError)` y conserva sus afirmaciones sobre la run propia sellada `ok=false`.

Lo muestra el código: `app/ads/descarga.py`, `_Corrida.run` (`except BaseException: self._fallar_todos(exc); raise`)
y el docstring de `_fallar_todos` («la excepcion original sube (el caller jamas ve resultados)»).
Comando: `rg -n -A6 'except BaseException as exc:' app/ads/descarga.py`.

Los otros seis `pytest.raises` de la fila (`test_reports_pipeline.py` `:1385`, `:1487`, `:1528`,
`:1572`; `test_ads_producto_ingesta.py` `:1043`; y en placements «cero perfiles» y «fallo en fase de
base») pasan a `res.ok is False` más el tipo de `res.exc` o el texto de `res.skip_reason`.

## 2. Dos pruebas de `reports.main` no listadas se reescriben, no se borran

`tests/test_reports_pipeline.py` tenía `test_main_sin_orbit_dsn_ingest_falla_cerrado` y
`test_main_rechaza_rango_mayor_a_31_dias`, que llamaban `reports.main` pero no están en la lista de
borrados de la fila. Las dos guardas que prueban (sin `ORBIT_DSN_INGEST` sale 2; un rango de 32 días
sale 2 y dice «32 dias» y «31») siguen vivas en `ingest diario` y `tests/test_ingest_diario.py` no
las cubre. Se conservan con el mismo nombre y las mismas afirmaciones, entrando por
`app.ads.diario.main`.
