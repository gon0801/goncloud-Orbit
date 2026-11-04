# S.5: mutantes (uno por prueba nueva; el plan no lista obligatorios)

Cada mutante: rojo con mutante, verde restaurado, revertido al terminar.
Comando base: el de `evidencia.md`. Todos se demostraron contra su prueba
(línea `::test`) y se revirtieron; el verde final (§3 de `evidencia.md`)
corre sobre el árbol limpio (verificado: `grep` de los marcadores
`reversa:{platform}`, `fecha + 1`, `::date > %s`, `except KeyError`,
`pedido in vistos` sin hits; literales correctos presentes).

## M1 — `test_s5_plan_no_gasta_cuota`

Mutante (`app/precio/cambios.py`, `planear_reversa`):
`volver_a=Importe(f[3], f[4]), desde=Importe(f[5], f[6])` →
`volver_a=Importe(f[5], f[6]), desde=Importe(f[3], f[4])`
(precio destino y previo cruzados).

- Rojo: `1 failed` (`(100.00, MXN)` vs `(110.00, MXN)`).
- Verde restaurado: `1 passed` (en el verde final §3).

## M2 — `test_s5_precio_cambiado_salta_fila_y_lote_sigue`

Mutante (`app/precio/cambios.py`, `_revertir_fila`):
`(vivo.valor, vivo.moneda) != (despues, despues_moneda)` →
`== (despues, despues_moneda)` (lógica de salto invertida).

- Rojo: `1 failed` (revierte la movida, salta la quieta).
- Verde restaurado: `1 passed` (en el verde final §3).

## M3 — `test_s5_enviado_con_precio_aplicado_cierra_y_revierte_mismo_dia`

Mutante (`app/precio/cambios.py`, `_revertir_una`):
`confirmado_por = 'lectura_viva'` → `'observacion'`.

- Rojo: `1 failed` (`('confirmado', 'observacion')` vs lectura_viva).
- Verde restaurado: `1 passed` (en el verde final §3).

## M4 — `test_s5_universo_en_live_aborta`

Mutante (`app/precio/cambios.py`, `_universos_en_live`): slot de goal
`"live"` → `"shadow"` (el efectivo nunca sería live).

- Rojo: `1 failed` (`Failed:` no se levantó `UniversoEnLive`).
- Verde restaurado: `1 passed` (en el verde final §3).

## M5 — `test_s5_reversa_y_corrida_no_corren_a_la_vez`

Mutante (`app/precio/cambios.py`, candado del lote): `job` y advisory
`precio:<platform>` → `reversa:<platform>` (otro namespace: ambas
direcciones deben romper).

- Rojo: `1 failed` (el lote pasa con la corrida dentro).
- Verde restaurado: `1 passed` (en el verde final §3).

## M6 — `test_s5_tool_fecha_revierte_enviados_del_dia`

Mutante (`app/precio/cambios.py`, `_SQL_PLAN_POR_FECHA`):
`(...)::date = %s` → `> %s` (otro conjunto de días).

- Rojo: `1 failed` (1 `[revertir]` del día vecino, no 2).
- Verde restaurado: `1 passed` (en el verde final §3).

## M7 — `test_s5_tool_corrida_revierte_lote_de_corrida`

Mutante (`app/precio/cambios.py`, `_SQL_CORRIDA_DE`):
`SELECT platform, fecha` → `SELECT platform, fecha + 1`.

- Rojo: `1 failed` (plan vacío: resuelve el día siguiente).
- Verde restaurado: `1 passed` (en el verde final §3).

## M8 — `test_s5_tool_ids_validan_existencia_y_plataforma`

Mutante (`tools/precio_reversa.py`, `_platform_de_ids`):
`if pedido not in vistos` → `if pedido in vistos`.

- Rojo: `1 failed` (`IndexError` en vez de `Abortar` inexistente).
- Verde restaurado: `1 passed` (en el verde final §3).

## M9 — `test_s5_fallo_al_escribir_sella_error_y_lote_sigue`

Mutante (`app/precio/cambios.py`, `_revertir_una`):
`except Exception` → `except KeyError` (la explosión ya no se atrapa).

- Rojo: `1 failed` (`RuntimeError: bug-simulado` sube, lote muerto).
- Verde restaurado: `1 passed` (en el verde final §3).
