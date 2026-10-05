# S.2: mutantes (uno por prueba nombrada por la guia)

Rama `jev02/s2-nucleo`, base `436b8771c987090724247dd237a167a2687f56c8`.
Cada fila: linea mutada, prueba que la caza, resultado con el mutante
puesto, y revertido despues. DSN de pruebas:
ORBIT_TEST_DSN="postgresql://orbit:***@localhost:5432/postgres"
(variable vacia en el entorno; los tests usaron el default de
`tests/test_schema.py::_test_dsn`).

| # | Mutante (una linea) | Prueba | Resultado |
|---|---|---|---|
| M1 | `leer`: `ordenes_conocidas > 0` -> `>= 0` | `test_leer_tabla` | 9 failed, 3 passed |
| M2 | `probar_roster`: `> max_edad` -> `>= max_edad` | `test_probar_roster_48h_exactas_valen` | 1 failed |
| M3 | `anunciados_hoy`: quitar el `not` | `test_anunciados_hoy_quita_solo_archivados_y_conserva_exhaustivo` | 2 failed |
| M4 | `ajustes_desde_settings`: `type(tope) is not int` -> `not isinstance(tope, int)` | `test_ajustes_desde_settings_tabla[tope-bool-no-es-entero]` | 1 failed |
| M5 | `leer_destino`: `"ajena"` -> `"destino_corresponde"` | `test_leer_destino_tabla` | 1 failed, 3 passed |
| M6 | `planear`: orden `(vence, rol)` -> `(rol, vence)` | `test_planear_propuestas_primero_por_vencimiento` | 1 failed (tras reforzar la prueba: el mutante sobrevivio a la primera version, que no separaba rol de vencimiento) |
| M7 | `planear`: quitar `clave in unidades` del lazo de candidatas | `test_planear_sin_claves_repetidas` | 1 failed (tras reforzar la prueba con `tramo == "propuesta"`: el dict impide repetidas por estructura; lo que pincha es que propuesta gana) |
| M8 | `app/jev_lectura.py`: agregar `import os` | `test_modulo_puro_sin_red_ni_db_en_top_level[jev_lectura.py]` | 1 failed |
| M9 | `resolver_fichas`: `ficha_version_id=ficha.id` -> `None` | `test_resolver_fichas_conserva_id_traido_y_acredita_archivado` | 1 failed |
| M10 | `_mismo_origen`: agregar `and _censo_a_json(congelado) == _censo_a_json(crudo)` | `test_mismo_origen_ignora_synced_at_y_exige_identidad_y_exhaustivo` + `test_reanudacion_ignora_synced_at_y_llama_solo_al_pendiente` | 2 failed |
| M11 | `Libro.pagar`: quitar el `commit()` previo al HTTP | `test_revision_e_intencion_confirmadas_antes_del_http` (existente, fija el traslado) | 1 failed |
| M12 | `_con_estado_ausente`: solo `bool(anuncio_ids) and not estados` (muere la rama `status is None`) | `test_probar_roster_cada_motivo_por_separado[estado-ausente-sin-fila]` (nuevo, r2 bloqueante B1) | 1 failed, 53 passed; sin mutante 54 passed |

Notas:

- `test_planear_determinista` no tiene mutante de una linea: la propiedad
  es estructural (toda salida pasa por `sorted()`), y ningun cambio de una
  linea la rompe sin romper tambien M6/M7. La prueba documenta la propiedad.
- M6 y M7 sobrevivieron a la primera version de sus pruebas y obligaron a
  reforzarlas (consta arriba). Ningun mutante quedo puesto: tras cada fila
  se revertio y al final Comprueba dio 309 passed, 0 skipped.
