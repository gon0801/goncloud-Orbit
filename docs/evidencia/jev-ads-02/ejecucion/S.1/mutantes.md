# S.1: mutantes (J1 r1)

Un mutante por cada una de las 7 pruebas de la guia. Cada mutante se aplico,
se corrio la prueba (rojo), y se revertio. Verificacion de revertido:
`grep -c MUTANTE app/ads/*.py tests/test_structure_sync.py` dio 0 en todos.

| # | Mutante (cambio de una linea) | Prueba que enrojece | Resultado |
|---|---|---|---|
| 1 | `structure_api.py` `listar_con_prueba`: `if declarado is not None and ...` -> `if False:` (solo se compara el total de la ultima pagina) | `test_paginacion_primera_pagina_declara_total_y_ultima_no_da_error` | 1 failed (DID NOT RAISE), 9 passed |
| 2 | `structure_api.py` `listar_con_prueba`: `total_declarado=declarado` -> `declarado or 0` (None se vuelve 0) | `test_listar_con_prueba_devuelve_total_declarado_o_none` | 1 failed, 9 passed |
| 3 | `structure_plan.py` `armar_acta`: `if ad_group_crudo is None:` -> `if False:` (sin grupo no suma nada) | `test_acta_pura_clasifica_anuncios_y_grupos_sin_tocar_la_base` | 1 failed, 9 passed |
| 4 | `structure_plan.py` `huella_anuncios`: `sorted(ad_ids)` -> `ad_ids` (sensible al orden) | `test_huella_anuncios_sensible_al_contenido_e_invariante_al_orden` | 1 failed, 9 passed |
| 5 | `structure.py` `_insertar_acta`: `for fila in grupos:` -> `for fila in []:` (no escribe filas de grupo) | `test_corrida_ok_deja_acta_de_listado` + `test_acta_no_suma_a_rows_written_ni_cambia_entidades` | 2 failed, 8 passed |
| 6 | `structure.py` `sync_structure`: llamada `_insertar_acta` movida fuera de la transaccion de trabajo (antes del `try`, con refs vacio) | `test_corrida_fallida_no_deja_acta` (+ 5 y 7 de colateral) | 3 failed, 2 passed; la 6 con `assert 1 == 0` (el acta sobrevive al rollback) |
| 7 | `structure.py` `sync_structure`: `rows_written=written` -> `written + 1` en el sello ok | `test_acta_no_suma_a_rows_written_ni_cambia_entidades` + `test_sync_y_resync_estructura_en_vivo` | 2 failed |

Notas:

- Mutante 6, primer intento rechazado: `conn.commit()` dentro de
  `_insertar_acta`. psycopg lo prohibe
  (`Explicit commit() forbidden within a Transaction context`) y rompia
  tambien el camino ok: razon equivocada. El mutante valido es el de la
  tabla: acta en autocommit antes del trabajo.
- Mutante 7 obligo a endurecer la prueba 7: al principio solo leia
  `res.rows_written` (en memoria) y el mutante del sello no la enrojecia.
  Ahora tambien lee la fila sellada en `ingest_run` (`(9, 2)`).
- La prueba 6 pasa en vacio sin el cableado (nada escribe acta): su
  discriminacion la demuestra el mutante 6.
