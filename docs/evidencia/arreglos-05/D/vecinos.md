# Vecinos que el bloque D corrio

Corridos en serie sobre `f946d329` con el PG desechable y HTTP simulado. Los
números son los que aparecen en [acepta.txt](acepta.txt); los grupos que no
entran al recibo (porque tienen rojos previos de esta PC) se anotan con su
conteo y el motivo.

| Suite | Resultado | Estado |
|---|---|---|
| `test_arreglos05_d_write.py` | 19 passed | verde (`N d`) |
| `test_arreglos05_a_estados.py` | 181 passed | verde (sin casillas nuevas) |
| `test_architecture.py` | 99 passed, 2 skipped | verde (mapa nuevo y 900 lineas) |
| `test_fabrica_plan.py` | 75 passed | verde |
| `test_fabrica_campanas.py` | 117 passed | verde |
| `test_api_fabrica.py` | 113 passed | verde |
| `test_impulso_io.py` | 114 passed, 1 deselected | verde; el deseleccionado exige password en el DSN y falla igual en el padre |
| `test_ads_write.py` + `test_ads_client.py` + `test_ads_archivar.py` | 136 passed | verde |
| `test_archiva_inertes.py` | 47 passed | verde |
| `test_apply.py` + `test_apply_harvest.py` + `test_reversa_harvest.py` | 168 passed | verde |
| `test_api_write.py` + `test_bloqueo_dueno_efecto.py` + `test_retiro_anuncios.py` | 163 passed | verde |
| `test_arreglos05_c_reposicion.py` | 20 passed | verde |
| `test_schema.py` + `test_apply_schema.py` | 117 passed | verde |
| `test_despliegue_motor.py -k arreglos05` | 3 passed | verde |
| `test_ui_fabrica.py` + `test_fabrica_0038.py` + `test_estimacion_aislamiento_fabrica.py` | 36 passed, 13 failed | **fuera del recibo**: los 13 son `test_flujo_js_*` con `WinError 206` al lanzar node; fallan igual en el padre `7ef47542` |
| `test_fabrica_f2.py` | 7 passed | verde |
| `test_fabrica_f2_hermanas.py` + `test_fabrica_f2_biblioteca.py` | 104 passed, 40 failed | **fuera del recibo**: 24 + 16 rojos previos documentados; el motor de la fabrica no los toca en D |
| `test_d3_adversary.py` | 174 passed, 2 failed | **fuera del recibo**: `test_revalidacion_harvest_respeta_target_del_goal` (2 parametrizaciones) es rojo previo |

Los rojos previos que el brief lista (`test_harvest_destino` x3, `test_notifica`
x3, `test_orbit07_m1` x1 y
`test_precio02_escenario_meli::test_ingesta_meli_sin_cliente_salta_meli_y_sigue`)
no tocan archivos de este bloque y no se volvieron a correr aquí.
