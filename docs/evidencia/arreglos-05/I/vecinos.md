# Vecinos del bloque I (ARREGLOS 05)

SHA de la cabeza: `eb8817077ae7ecd25f3ca706b6aba1cdda8e0417`.
Base: `a9885448ee04a1fe38a3be25c69c13985e140c76`.

Se ejecutaron en serie sobre el PG16 desechable, con `CI=1` y el venv del
checkout. Los conteos son de la cabeza del bloque.

| Suite | Resultado | Nota |
|---|---|---|
| `tests/test_arreglos05_i_cuotas.py` (`N i`) | 19 passed | Regresiones nuevas del bloque. |
| `tests/test_config_write.py tests/test_settings_escritura.py` | 52 passed | Incluye `-k arreglos01_x` (1 passed) y los caminos de `proxima_config`/`guarda_config`. |
| `tests/test_api_write.py` | 52 passed | Endpoint de escritura completo. |
| `tests/test_architecture.py` | 99 passed, 2 skipped | La omision es la declarada de la regla 99:2 (archivos ilegibles en este OS). |
| `tests/test_api_dashboard.py` | 81 passed | Snapshot de Settings y procedencia. |
| `tests/test_ui.py -k "settings or config or cap"` | 22 passed | UI aplicable a la pantalla y al formulario. |
| `tests/test_ui.py` completo | 91 passed, 7 failed | Los 7 son `FileNotFoundError: [WinError 206] The filename or extension is too long` al lanzar Node; el mismo limite aparece en el padre en `test_ola_a2_frontend.py` (ver abajo). No tocan Settings. |
| `tests/test_ola_a2_frontend.py tests/test_verify_orbit.py` | 19 passed, 4 failed | 3 fallos son el mismo `WinError 206` de Node; el cuarto es `test_drives_conducen_el_dashboard_vigente[resumen]` con `css_390` del navegador. El bloque completo falla igual con el codigo del padre (comprobado antes de commitear el arreglo). |

## Fallos previos de `master` en esta PC (no de este bloque)

- `test_fabrica_f2_hermanas` x24, `test_fabrica_f2_biblioteca` x16,
  `test_harvest_destino` x3, `test_d3_adversary` x2, `test_notifica` x3,
  `test_orbit07_m1` x1 y
  `test_precio02_escenario_meli.py::test_ingesta_meli_sin_cliente_salta_meli_y_sigue`
  x1: son los rojos previos de esta PC que el brief declara ajenos al bloque.
  No se ejecutaron aqui por pertenecer a otros modulos, igual que en G.
- El limite de linea de comandos de Windows (`WinError 206`) afecta a las
  pruebas que lanzan Node con un `-e` grande. Se reprodujo con el codigo del
  padre antes del commit (`tests/test_ola_a2_frontend.py` y
  `tests/test_verify_orbit.py`: 4 failed, 19 passed en ambos arboles).
- `test_drives_conducen_el_dashboard_vigente[resumen]` falla por `css_390` (el
  ancho de 390 px medido con Edge en esta PC), tambien con el codigo del padre.

## Cobertura del contrato

| Camino | Prueba |
|---|---|
| Formulario con `1,000`: error visible, cero POST, sin NaN/null | `I/adverso.py` con Edge real (checks `error_visible`, `cero_post`, `sin_version_nueva`, `cap_conserva_texto`). |
| API con `null` explicito en una cuota | `test_arreglos05_i_api_null_explicito_en_cuota_rechaza_sin_version`. |
| API con booleano | `test_arreglos05_i_api_booleano_en_cuota_rechaza_sin_version` y `test_arreglos05_i_cuerpo_settings_conserva_la_cuota_cruda`. |
| API con fraccion/texto ilegible | `test_arreglos05_i_api_fraccion_o_texto_ilegible_rechaza_sin_version` y la prueba pura parametrizada. |
| Campo omitido conserva y guarda el resto | `test_arreglos05_i_api_cuota_omitida_conserva_y_apila_una_fila`. |
| `null` valido de otro campo | `test_arreglos05_i_api_null_valido_en_otro_campo_se_guarda`. |
| Entero valido con version actual / vieja | `test_arreglos05_i_api_entero_valido_guarda_una_version_y_snapshot` y `test_arreglos05_i_api_version_vieja_409_y_cero_cambios`. |
| Control 01X (banda manual) | `tests/test_config_write.py -k arreglos01_x`: 1 passed. |
