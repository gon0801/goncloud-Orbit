# G.2 — mutantes

Uno por cada prueba nueva (9; el plan no lista obligatorios para G.2).
Cada mutante se demostró rojo contra su prueba y se revirtió; el verde
final corre sobre el árbol limpio (verificado: `grep` de los 9
mutantes vacío + suite en verde + ruff limpio).

| # | Mutante (cambio de 1 línea) | Prueba que lo mata | Rojo |
|---|---|---|---|
| M1 | `liberar_compuerta`: firma sin `_token: ... Depends(exige_token)` | `test_g2_sin_token_401_sin_abrir_admin` | `503 != 401` en `/liberar` |
| M2 | `_fila_plan_dict`: `"confirmable": fila.confirmable` → `not ...` | `test_g2_plan_no_escribe` | `assert False is True` |
| M3 | `aplicar_goals`: `HuellaDistinta` → `status_code=400` | `test_g2_aplicar_huella_vieja_409_con_plan_nuevo` | `400 != 409` |
| M4 | `aplicar_goals`: `go_literal=cuerpo.go_literal or "mutante"` | `test_g2_live_sin_go_422` | `200 != 422` (aplica con go inventado) |
| M5 | `liberar_compuerta`: `corrida_id=corrida_id + 1` | `test_g2_liberar_dos_veces_misma_fila` | `409 != 200` (la vecina no retuvo) |
| M6 | `aplicar_goals`: `conn.autocommit = True` → `False` | `test_g2_aplicar_ok_escribe_el_lote` | `500 != 200` (`aplicar_plan` exige autocommit) |
| M7 | `_SQL_SIN_GOAL`: `v.goal_id IS NULL` → `IS NOT NULL` | `test_g2_plan_sin_goal_lista_solo_los_sin_goal` | lista al ocupado en vez del libre |
| M8 | `_seleccion`: `> TOPE_UNIDADES` → `> TOPE_UNIDADES + 1` | `test_g2_plan_mas_de_500_unidades_422` | `200 != 422` con 501 filas |
| M9 | `liberar_compuerta`: `CorridaSinRetencion` → `status_code=404` | `test_g2_liberar_sin_retencion_409` | `404 != 409` |
