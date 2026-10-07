# S.5: mutantes (uno por prueba)

Rama `jev02/s5-pantallas`, base `96daae6` (merge J4). Cada fila: linea
mutada, prueba que la caza, resultado con el mutante puesto, y revertido
despues (restauracion por copia, nunca `checkout`: un intento previo con
`git checkout --` revertia codigo sin commitear y daba falsos "cazados";
incidente declarado y reconstruido desde respaldos). DSN de pruebas:
ORBIT_TEST_DSN="postgresql://orbit:***@localhost:5432/postgres"
(variable vacia en el entorno; los tests usaron el default de
`tests/test_schema.py::_test_dsn`; Postgres local).

| # | Mutante (una linea) | Prueba | Resultado |
|---|---|---|---|
| M1 | `banda_de_proporcion`: `>= 0.95` -> `> 0.95` | `test_banda_de_proporcion_tabla[19-20-todos]` | 1 failed |
| M2 | `banda_de_proporcion`: `<= 0.15` -> `< 0.15` | `test_banda_de_proporcion_tabla[3-20-pocos]` | 1 failed |
| M3 | `vista_de_dict`: `satisfacen=len(productos_ok)` -> `+ 1` | `test_vista_de_dict_decodifica_la_fila` | 1 failed |
| M4 | `relevancia_de_texto`: `ajena` -> `HayCompatible` | `test_la_fila_guardada_se_relee_igual` | 1 failed (relee `relevante_sin_venta`, no `ajena`) |
| M5 | `de_propuestas`: primera gana -> ultima gana | `test_de_propuestas_una_decision_muestra_la_primera` | 1 failed |
| M6 | `gasto_sin_venta`: `ORDER BY gasto DESC` -> `ASC` | `test_gasto_sin_venta_punta_a_punta` (Postgres real) | 1 failed |
| M7 | `gasto_sin_venta`: `(cantidad + 1, acumulado + gasto)` -> sin sumar | `test_gasto_sin_venta_ordena_y_totaliza` | 1 failed |
| M8 | `SenalVista.como_dict`: `sorted(motivos)` -> `reverse=True` | `test_como_dict_de_senal` | 1 failed |
| M9 | `vista_de_dict`: `vigente=bool(...)` -> `not ...` | `test_vista_de_dict_decodifica_la_fila` | 1 failed |
| M10 | `_como_dinero`: quitar la rama `str` | `test_vista_de_dict_acepta_json_serializado` | 1 failed (ValueError) |
| M11 | `de_propuestas`: `destino_ilegible` -> `False` | `test_de_propuestas_resuelve_origen_y_destino` | 1 failed |
| M12 | `PantallaGasto.como_dict`: `str(gasto)` -> `gasto` | `test_como_dict_de_pantalla_gasto` | 1 failed |

Notas:

- M4 es el mutante que mas informa: si la reconstruccion confundiera
  `ajena` con `corresponde`, la pantalla mostraria "sin venta" donde el
  job sello "ajena". La prueba de relectura lo caza.
- `otros_sin_venta` no tiene mutante: la fila no lo guarda (consta en el
  docstring de `vista_de_dict`) y `test_otros_sin_venta_no_decide` fija que
  `leer` lo ignora; ningun cambio de una linea ahi cambia una decision.
- Hallazgo de la fase de pruebas (no mutante): `IN %s` con tupla de tuplas
  revienta en psycopg (`syntax error at or near "$1"`); la prueba de punta
  a punta lo cazo antes del verde y se paso a plazas por clave con valores
  parametrizados.
