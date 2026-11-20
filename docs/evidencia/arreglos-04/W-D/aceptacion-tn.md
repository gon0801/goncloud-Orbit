# W-D / TN16: comparacion a la misma edad y «faltan datos» en el Resumen

Contrato: `plans/arreglos-04.md` (`## Bloque W-D`), `docs/evidencia/arreglos-04/DISENO.md` (`## W-D` y `### TN16`) y `docs/evidencia/arreglos-05/ACEPTACION.md`. Owner unico de `TN16`: `W-D.2/.4`. No se toca `app/pantalla_ruido.py` ni la cobertura del tablero de ruido (eso es `W-E`).

- SHA base sin el arreglo: `a967874` (`origin/master` con `W-K` #674).
- Master de rebase: `54f0d98c` (integra `W-E` #675); sin archivos compartidos con este bloque.
- SHA de codigo entregado: `9fdd54d`.
- Sin migraciones.

## Que cambia

1. **F3 (`W-D.1`): `app/dashboard_resumen.py`.**
   - `_SQL_DIAS_A_LA_EDAD` repite columnas y colapso de `_SQL_DIAS` (`DISTINCT ON (ad_entity_id, metric_date)` + `ORDER BY observed_at DESC`) y agrega `o.observed_at <= %s`.
   - `bloque_canal(conn, canal, ventana, hoy, *, instante)`: la consulta principal arranca en `ventana.calentamiento_desde`; el periodo anterior de un canal con madurez publicada (Amazon) se lee con corte `instante - ventana.dias`. Mercado Libre (`dias_inmaduros is None`) compara la ultima lectura y publica `observado_hasta: null`.
   - `comparison` lleva `totals` de esa lectura y `observado_hasta` ISO UTC. `resumen()` pasa `instante`.
2. **F10 (`W-D.2`): `app/ui_metricas.py::_frase_tarjeta`.** Con meta y ACoS vacio: «Sin ventas atribuidas frente a la meta de {meta}%.» solo si `totals.sin_ventas`; si no, «Faltan datos de algunos días: no se puede comparar el ACoS con la meta de {meta}%.». El estado de la tarjeta no cambia.
3. **Textos y documentos (`W-D.3`).** `app/templates/resumen.html`: linea `p.ch-comparacion` bajo la frase de la tarjeta (solo si `comparison.observado_hasta`) y caption nuevo de «Periodo anterior por canal». `docs/DASHBOARD.md` §9.4: contrato de `comparison.observado_hasta`. Ficha `resumen.md`: `resumen-tarjetas` menciona la linea y «Abrir Resumen» busca «en las ventas que todavía llegan» y el caption nuevo. `drive-resumen` de `orbit-verify` suma `tarjeta_misma_edad`.
4. **Comprobacion en produccion (`W-D.4`).** `docs/evidencia/arreglos-04/W-D/prueba.py` reutiliza la conexion falsa de `docs/evidencia/arreglos-2026-11-12/pruebas/web/test_rev_numeros.py`.

## Rojo previo (codigo con el arreglo retirado, `a967874`)

- (a) F10: `PYTHONPATH=. .venv/bin/python docs/evidencia/arreglos-04/W-D/prueba.py` -> `AssertionError: Sin ventas atribuidas frente a la meta de 20 %` (sin «Faltan datos»).
- (b) F3: con el mismo guion y solo la asercion (a) silenciada -> `AssertionError: periodo anterior no es la lectura a la misma edad: 676.0000` (la foto final; el arreglo da `532.0000` = 7 x 76).
- TN16 en el arnes: `ORBIT_TEST_DSN=postgresql://orbit@127.0.0.1:55449/postgres ORBIT_CHROMIUM="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" bash docs/evidencia/arreglos-05/comprobar.sh T ui "test_16 or test_control_contrato"` -> `1 failed, 1 passed`: `test_16_resumen_no_afirma_ausencia_de_ventas_que_api_reporta` falla en `assert not observado["html_con_afirmacion_sin_ventas"]`; el control de contrato pasa.

## Verde (head `9fdd54d`)

- `W-D.1`: `PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_dashboard_resumen.py tests/test_api_dashboard.py tests/test_ui.py` -> `135 passed, 59 skipped` y los 4 fallos sin `@skipif` de `test_api_dashboard.py` que ya fallan igual en `origin/master` cuando no escucha PostgreSQL (`BASE/rebase.md`). Con el PG16 del laboratorio: `216 passed` sobre `test_dashboard_resumen.py + test_api_dashboard.py + test_ui.py + test_ui_metricas.py + test_verify_orbit.py`, sin skips.
- `W-D.2`: `PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_ui_metricas.py` -> `1 passed`.
- `W-D.3`: `grep -q "en las ventas que todavía llegan" app/templates/resumen.html && grep -q "tarjeta_misma_edad" .cursor/skills/verify-orbit/helpers/orbit-verify && ... pytest -q tests/test_verify_orbit.py tests/test_ui.py` -> `89 passed, 24 skipped`.
- `W-D.4`: `PYTHONPATH=. .venv/bin/python docs/evidencia/arreglos-04/W-D/prueba.py` -> `W-D OK`.
- `W-D.6` (TN16) sobre `9fdd54d`: `bash docs/evidencia/arreglos-05/comprobar.sh T ui "test_16 or test_control_contrato"` -> `2 passed, 4 deselected`: la API conserva `sin_ventas=False`, `cost=None` y `acos=None`, el HTML dice «Faltan datos» y no «Sin ventas», y el CSP sigue `default-src 'self'`.

## Pendiente

- `W-D.5` (comparacion despues del desplegar) no se corrio: requiere el despliegue, que espera la coordinacion de integracion. Al desplegar, correr los comandos del anexo A del diseno y guardar `docs/evidencia/arreglos-04/W-D/comparacion.txt`.
