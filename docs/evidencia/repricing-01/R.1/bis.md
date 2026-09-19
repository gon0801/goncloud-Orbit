# R.1 bis — tres tests que cierran sobrevivientes de la re-mutación

Solo tests (cero cambios en `app/**`, `tools/**`). Cada mutante sembrado a
mano sobre el HEAD nuevo con base real
(`ORBIT_TEST_DSN=postgresql://orbit:orbit@localhost:5432/postgres`) y
`PYTHONPYCACHEPREFIX=$(mktemp -d)` fresco por mutante, revertido sin commit
(por reemplazo inverso; `git diff` final solo trae los tres archivos de
tests). Los tres tests pasan en limpio. Cero sobrevivientes.

| Id | Test | Rojo contra el mutante | Veredicto |
|---|---|---|---|
| R1-A7-S1 | `test_r1bis_s1_tool_dsn_que_no_responde_es_exit_2_sin_traceback` | `FAILED ...r1bis_s1...` / `1 failed, 32 deselected` (sin `OrbitDbError` en el `except`, el tool traza y sale 1) | MUERTO |
| R1-A2-S2 | `test_r1bis_s2_margen_a_precio_sin_iva_no_usa_divisor` | `FAILED ...r1bis_s2...` / `1 failed, 185 deselected` (con `divisor = iva_divisor`, `I = P/1.16` y `m != 0.57`) | MUERTO |
| R1-A3-S3 | `test_r1bis_s3_comentario_con_llamada_patch_no_es_fuga` | `FAILED ...r1bis_s3...` / `1 failed, 93 deselected` (con `codigo = linea`, `notas.py` sale como fuga) | MUERTO |

Nota S2: la línea mutada existe dos veces en `objetivo.py` (l.121 en
`precio_estrella`, l.149 en `margen_a_precio`); el ancla de siembra incluye
`ingreso = precio / divisor` para tocar solo `margen_a_precio`.
