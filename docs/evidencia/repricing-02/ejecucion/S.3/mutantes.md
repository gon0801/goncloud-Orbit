# S.3: mutantes (3 obligatorios + uno por prueba nueva)

Cada mutante: rojo con mutante, verde restaurado, revertido al terminar.
Comando base: el de `evidencia.md`. Todos se demostraron contra su prueba
(una línea `-k`) y se revirtieron; el verde final (§3 de `evidencia.md`)
corre sobre el árbol limpio (verificado con `grep` de los marcadores).

## M1 — OBLIGATORIO: fusible con mayor-o-igual

Prueba: `test_s3_fusible_80_de_158_retienen_79_pasan`. Mutante
(`app/precio/compuerta.py`, `_dispara`): `con_salto > frac * base` →
`con_salto >= frac * base`.

- Rojo: 79 nuevas retienen (`1 failed`).
- Verde restaurado: `1 passed`.

## M2 — OBLIGATORIO: fusible contando `no_evaluado`

Prueba: `test_s3_no_evaluado_no_cuenta_en_las_medidas`. Mutante
(`app/precio/corrida.py`, `_SQL_MEDIR_HOY`): fuera
`AND d.m_actual IS NOT NULL`.

- Rojo: `assert 3 == 2` (el `no_evaluado` con canal diluye; `1 failed`).
- Verde restaurado: `1 passed`.

## M3 — OBLIGATORIO: segundo escalón cuenta como nuevo

Prueba: `test_s3_segunda_ola_al_mismo_goal_no_cuenta_como_nueva` (DB).
Mutante (`app/precio/corrida.py`, `_medir_compuerta`):
`es_nueva(anterior, goal_id=fila[5])` →
`es_nueva(anterior, goal_id=None)`.

- Rojo: `assert (2, 2) == (2, 1)` (`1 failed`).
- Verde restaurado: `1 passed`.

## M4 — `test_s3_fusible_exige_minimo_de_movimientos`

Mutante (`app/precio/compuerta.py`, `_dispara`):
`and con_salto >= minimo` → `and True`.

- Rojo: 3/3 retienen (`Retenida ... == Abierta`, `1 failed`).
- Verde restaurado: `1 passed`.

## M5 — `test_s3_insumo_sistemico_gana_a_movimiento_masivo`

Mutante (`app/precio/compuerta.py`, `evaluar`): `if candidatas:` →
`if False:` (sin rama de insumo, gana movimiento).

- Rojo: `assert 'movimiento_masivo' == 'insumo_sistemico'` (`1 failed`).
- Verde restaurado: `1 passed`.

## M6 — `test_s3_insumo_sistemico_umbrales`

Mutante (`app/precio/compuerta.py`, `evaluar`):
`frac=config.insumo_salto_frac` → `frac=config.fusible_frac`
(60 > 0.50·158 es falso).

- Rojo: `assert False` (esperaba `Retenida`, `1 failed`).
- Verde restaurado: `1 passed`.

## M7 — `test_s3_liberadas_no_cuentan_en_ninguno_de_los_dos`

Mutante a (`app/precio/compuerta.py`, `evaluar`):
`if i.nueva and not i.liberada` → `if i.nueva`.

- Rojo: `Retenida(nuevas=80) == Abierta(nuevas=79)` (`1 failed`).

Mutante b (`app/precio/compuerta.py`, `armar_deriva`): fuera el
`if liberada: continue`.

- Rojo: `assert (158, 60) == (148, 50)` (`1 failed`).
- Verde restaurado: `1 passed`.

## M8 — `test_s3_segunda_ola_al_mismo_goal_no_es_nueva` (pura)

Mutante (`app/precio/compuerta.py`, `es_nueva`): fuera la cláusula
`mantener(cooldown)` (`return not (...)` → `return True`).

- Rojo: `assert True is False` (caso cooldown, `1 failed, 9 passed`).
- Verde restaurado: `10 passed`.

## M9 — `test_s3_salto_estricto_y_no_comparable`

Mutante (`app/precio/compuerta.py`, `salto`): `> pct` → `>= pct`.

- Rojo: `assert True == False` (caso 105, `1 failed, 7 passed`).
- Verde restaurado: `8 passed`.

## M10 — `test_s3_cortacircuito_abre_tras_n_y_exito_lo_reinicia`

Mutante (`app/precio/compuerta.py`, `abierto`): `>=` → `>`.

- Rojo: `abierto.abierto is True` falla tras el 5.º error (`1 failed`).
- Verde restaurado: `1 passed`.

## M11 — `test_s3_80_intenciones_nuevas_retienen_y_no_escriben_nada`

Mutante (`app/precio/corrida.py`, `_retener_por_compuerta`):
condición → `if True: return False` (nunca retiene por compuerta).

- Rojo: `assert resultado == (0, 0, 80, [])` (`1 failed`).
- Verde restaurado: `1 passed`.

## M12 — `test_s3_79_intenciones_pasan_y_aplican`

Mutante (`app/precio/corrida.py`, `_medir_compuerta`): en el INSERT de
medición, `veredicto.nuevas` → `veredicto.medidas`.

- Rojo: fila `(158, 158, ...)` vs `(158, 79, ...)` (`1 failed`).
- Verde restaurado: `1 passed`.

## M13 — `test_s3_costo_20_en_60_retiene_nombrando_costo`

Mutante (`app/precio/corrida.py`, `_INSUMO_DECISION`):
`"costo": (6, 7)` → `"costo": (10, 11)` (lee `r_valor`, sin salto).

- Rojo: `(causa, insumo)` no es `insumo_sistemico/costo` (`1 failed`).
- Verde restaurado: `1 passed`.

## M14 — `test_s3_salto_solo_fbm_retiene_fbm_y_deja_pasar_fba`

Mutante (`app/precio/corrida.py`, `_medir_compuerta`):
`por_canal.setdefault(fila[2], ...)` → `setdefault("fba", ...)`
(un solo universo).

- Rojo: `assert {'fba'} == {'fba', 'fbm'}` (`1 failed`).
- Verde restaurado: `1 passed`.

## M15 — `test_s3_liberar_aplica_lo_retenido_de_hoy_y_no_lo_de_ayer`

Mutante (`app/precio/corrida.py`, `_medir_compuerta`):
`liberadas = {...}` → `liberadas = set()` (la remedición ignora lo
soltado y vuelve a retener).

- Rojo: `abiertos["fba"]` es `Retenida`, no `Abierta` (`1 failed`).
- Verde restaurado: `1 passed`.

## M16 — `test_s3_liberar_dos_veces_devuelve_la_fila_y_sin_retencion_levanta`

Mutante (`app/precio/liberaciones.py`, `_SQL_LIBERACION_DE`):
`WHERE corrida_id = %s` → `WHERE corrida_id = %s + 1` (las dos
relecturas fallan: ni el retorno temprano ni el rescate de carrera ven
la fila).

- Rojo: `UniqueViolation` en el segundo `liberar` (`1 failed`).
- Verde restaurado: `1 passed`.

Nota: matar solo el retorno temprano (`if fila is not None` → `if False`)
SOBREVIVE: el rescate `UniqueViolation` → relectura también devuelve la
fila (idempotencia en dos capas; el pronto es el camino común sin
excepción).

## M17 — `test_s3_repaso_sin_nada_pendiente_no_llama_al_mercado`

Mutante (`app/precio/corrida.py`, `_decidir_pendientes`):
`if lote:` → `if True:` (pide insumos con lote vacío).

- Rojo: `el repaso llamo al Mercado: insumos` (`1 failed`).
- Verde restaurado: `1 passed`.

## M18 — `test_s3_cinco_errores_seguidos_cortan_con_corte_errores`

Mutante (`app/precio/corrida.py`, `_aplicar_pendientes`):
`if cortacircuito.abierto:` → `if False:`.

- Rojo: `assert (7, 0, 0, []) == (5, 0, 2, [])` (`1 failed`).
- Verde restaurado: `1 passed`.

## M19 — `test_s3_fee_variable_del_escenario_retiene_nombrando_insumo`

Mutante (`app/precio/corrida.py`, `_INSUMO_ESCENARIO`):
`"fee_variable": (4, (6,))` → `(1, (6,))` (lee `fx_rate` NULL).

- Rojo: `(causa, insumo)` no es `insumo_sistemico/fee_variable`
  (`1 failed`).
- Verde restaurado: `1 passed`.

## M20 — `test_s3_precios_trae_bloque_seguridad_con_soltar`

Mutante (`app/api_precios.py`, `_bloque_precios`): fuera
`bloque["seguridad"] = ...`.

- Rojo: `UndefinedError: 'dict object' has no attribute 'seguridad'`
  (`1 failed`).
- Verde restaurado: `1 passed`.
