# G.1 — mutantes

Obligatorio del plan + uno por cada prueba nueva (15) y reescrita (3).
Cada mutante se demostró rojo y se revirtió; el verde final corre sobre
el árbol limpio (verificado: `grep -rn MUTANTE` vacío + suite en verde).

| # | Mutante (cambio de 1 línea) | Prueba que lo mata | Rojo |
|---|---|---|---|
| M0 (obligatorio) | `goals_write.aplicar_plan`: `with conn.transaction():` → `if True:` (aplica la mitad) | `test_g1_aplicar_fallo_a_mitad_no_deja_ninguna` | `assert 2 == 0` (quedan 2 filas) |
| M-T1 | `siembra._huella`: `hexdigest()[:16]` → `[:15]` | `test_g1_contrato_siembra_reproduce_el_plan_del_tool` | huella ≠ algoritmo congelado |
| M-T2 | `siembra._cuantizar_goal`: devuelve el goal sin cuantizar | `test_g1_planear_margen_de_hoy_propone_margen` | `0.366379 ≠ 0.3664` |
| M-T3 | `siembra._borde`: el piso propone el techo | `test_g1_planear_fuera_de_banda_propone_borde_confirmable` | `0.60 ≠ 0.10` |
| M-T4 | `siembra._fila`: `sin_escenario` con `confirmable=True` | `test_g1_planear_sin_escenario_no_confirmable` | `True is not False` |
| M-T5 | `siembra.UMBRAL_SALTO`: `0.25` → `0.50` | `test_g1_planear_salto_mayor_25` | `bloqueo None` en vez de `salto_mayor_25` |
| M-T6 | `siembra._fila`: acción siempre `sembrar` | `test_g1_planear_vigente_reemplaza` | `sembrar ≠ reemplazar` |
| M-T7 | `siembra._fila`: `pedido.mode == vigente.mode` → `!=` | `test_g1_planear_modo_conserva_goal_y_sin_cambio_no_hace_nada` | modo igual no sale `nada` |
| M-T8 | `siembra._fila`: `fuera_de_banda` siempre confirmable | `test_g1_planear_valor_en_bloque_aplica_el_mismo_goal` | `True is not False` |
| M-T9 | `siembra._fila`: `sin_cambio` con acción `sembrar` | `test_g1_planear_mismo_goal_y_modo_sale_sin_cambio` | `sembrar ≠ nada` |
| M-T10 | `siembra._componente`: faltante vale `0` en vez de fallar | `test_g1_planear_motivos_sin_p_estrella` | motivo `None` en vez de `referencia_sin_derivar` |
| M-T11 | `aplicar_plan`: `nuevo.huella != huella` → `==` | `test_g1_aplicar_huella_distinta_no_escribe` | no levanta `HuellaDistinta` |
| M-T12 | `aplicar_plan`: `escribibles` → `escribibles[:2]` | `test_g1_aplicar_fallo_a_mitad_no_deja_ninguna` | no falla la 3ª fila (no hay 3ª) |
| M-T13 | `aplicar_plan`: `continue` → `pass` (no omite) | `test_g1_aplicar_omite_bloqueo_y_cuenta` | escribe 2 en vez de 1 |
| M-T14 | `aplicar_plan`: `if estricto:` → `if False:` | `test_g1_aplicar_estricto_aborta_con_bloqueo` | no levanta `BloqueoSinConfirmar` |
| M-T15 | `aplicar_plan`: `lote=huella` → `lote=None` | `test_g1_aplicar_live_sin_go_no_escribe` | rastro `lote` NULL |
| M-R1a | tool `[plan] reemplaza` → `[plan] siembra` | `test_r1_siembra_con_vigente_reemplaza` | sin `reemplaza` en stdout |
| M-R1b | `aplicar_plan`: reemplazar sin `cerrar_goal` | `test_r1_csv_con_vigente_reemplaza` | el go choca con el vigente |
| M-R2 | `cerrar_goal`: `valid_to = hoy` → `hoy + 1` | `test_r2_mismo_dia_reedita_deja_vigente_y_anulado` | reeditar choca con el índice parcial |

Equivalente documentado (sobrevive con razón, no se cuenta como kill):
apagar la guarda `live sin go` de `aplicar_plan` no cambia nada observable
porque `sembrar_goal` (paso interno) la vuelve a exigir por fila y la
transacción revierte igual. Se deja la guarda del plan (falla antes de
abrir la transacción) como defensa en profundidad.
