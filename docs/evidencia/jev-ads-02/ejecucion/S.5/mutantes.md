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

## J5b: pantallas (senal en /cortes y /gasto-sin-venta)

Rama `jev02/s5b-pantallas`, base `80da8768` (merge J5a). Uno por prueba
nueva (15): muta una linea, corre su prueba (rojo), revierte por copia
desde respaldo (nunca `checkout`). DSN de pruebas:
ORBIT_TEST_DSN="postgresql://orbit:***@localhost:5432/postgres"
(variable vacia en el entorno; los tests usaron el default de
`tests/test_schema.py::_test_dsn`; Postgres local).

| # | Mutante (una linea) | Prueba | Resultado |
|---|---|---|---|
| J1 | item: `"senal": senal_por_decision.get(...)` -> `None` | `test_cortes_senal_en_items_con_transporte_roto_y_sin_escrituras` | 1 failed |
| J2 | `_senal_presentada`: `"destino": destino` -> `None` | `test_cortes_senal_harvest_dos_bloques_y_destino_ilegible` | 1 failed |
| J3 | except senal: `senal_disponible = False` -> `True` | `test_cortes_senal_ilegible_se_avisa_y_la_pantalla_sigue` | 1 failed |
| J4 | `_senal.html`: `{% if not v.vigente %}` -> `{% if v.vigente %}` | `test_cortes_senal_revocada_sale_desactualizada` | 1 failed |
| J5 | seccion: `"gasto": total["gasto"]` -> `"0"` | `test_gasto_sin_venta_api_forma_totales_y_get_puro` | 1 failed |
| J6 | `_banda_honesta`: guarda `roster_probado` -> `if False` | `test_gasto_sin_venta_nunca_ninguno_sin_roster_probado` | 1 failed |
| J7 | vocabulario: `422` -> `400` | `test_gasto_sin_venta_vocab_cerrado_y_mercado_por_omision` | 1 failed |
| J8 | `gasto_sin_venta.html`: `{{ fila.clave.termino }}` -> `\|safe` | `test_ui_gasto_sin_venta_xss_termino_escapado` | 1 failed |
| J9 | `cortes.html`: `class="senal-jev"` -> `class="senal"` | `test_ui_cortes_senal_antes_de_asesoria_y_sin_error` | 1 failed |
| J10 | `ui.py`: ruta `/gasto-sin-venta` -> `/gasto-sin-ventas` | `test_ui_gasto_sin_venta_200_con_secciones_y_escape` | 1 failed |
| J11 | `FRASE_POR_LECTURA[vendio_aqui]` -> `"X"` | `test_frase_de_lectura_directa` | 1 failed, 2 passed |
| J12 | frase ajena: `if datos_hasta:` -> `if False:` | `test_frase_ajena_nombra_productos_y_fecha` | 1 failed |
| J13 | frase sin_lectura: `", ".join` -> `"\|".join` | `test_frase_sin_lectura_muestra_el_motivo` | 1 failed |
| J14 | `TITULO_POR_LECTURA[lectura]` -> `.get(lectura, "X")` | `test_titulo_de_lectura_desconocida_falla_cerrado` | 1 failed |
| J15 | `calculado`: lectura por nombre -> `ultima[0]` | `test_gasto_sin_venta_acepta_conexion_con_dict_row` | 1 failed (KeyError) |

Notas:

- J6 es el mutante que mas informa: sin la guarda honesta, una fila 0
  de 3 sin roster probado caeria en banda `ninguno` y la pantalla
  afirmaria una proporcion que el diseno prohibe sin roster probado.
- J15 es el aviso (a) de VEREDICTO-J5-r1 hecho mutante: revertir a
  `fetchone()[0]` revienta con `dict_row` (KeyError: 0), que es justo
  lo que el rojo previo al verde mostro en `test_jev_pantallas.py`.
- J4 fija el aviso de vigencia por el camino del HTML: con la
  condicion invertida, la senal revocada no pinta "desactualizada".
- Tras los 15, `cmp` de los 7 archivos contra sus respaldos: identicos,
  y `grep -rn MUTANTE app/`: sin rastros.
