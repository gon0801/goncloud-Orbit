# S.2: mutantes (uno por prueba nueva + los 2 obligatorios del plan)

Cada mutante: rojo con mutante, verde restaurado, revertido al terminar.
Comando base: el de `evidencia.md`. En r2 los marcados `[probe]` se
demostraron con el trigger desactivado (bloqueador 0054); en r3 el lead
los re-demostró TODOS en el entorno real con la 0059 en ORDEN59 y quitó
las marcas. M1, M2 y M11-M18 conservan su demostración r2 (ese código no
cambió).

## M1 — `test_s2_modo_efectivo_es_el_menor_de_los_tres`

Mutante (`app/precio/compuerta.py`): `min(` → `max(`.

- Rojo: `assert modo_efectivo(global_, universo, goal) == esperado` (`10 failed`).
- Verde restaurado: `25 passed`.

## M2 — `test_s2_modo_efectivo_vocabulario_cerrado`

Mutante (`app/precio/compuerta.py`): `if modo not in _ORDEN_MODO:` → `if False:`
(sin validación, el inválido llega al `min` y sale `KeyError`, no `ValueError`).

- Rojo: `E KeyError: 'turbo'` (`15 failed`).
- Verde restaurado: `25 passed`.

## M3 — `test_s2_global_shadow_goal_live_decide_shadow_y_virtual`

Mutante (`app/precio/corrida.py`, `_decidir_lectura`):
`modo_fila = modo_efectivo(modo_global, universo, mode)` → `modo_fila = mode`
(ignora el apagador: la decisión entra `live`).

- Rojo (r3, real): `assert ('subir', 'live') == ('subir', 'shadow')` (`1 failed`).
- Verde restaurado: `1 passed`.

## M4 — `test_s2_global_off_limpia_y_no_decide`

Mutante (`app/precio/corrida.py`, `correr`): `if modo_global == "off":` → `if False:`
(decide bajo off; r3: el espía lo pesca porque decidir ya es gracia).

- Rojo (r3, real): `assert [1] == []` en `llamadas` (`1 failed`).
- Verde restaurado: `1 passed`.

## M5 — OBLIGATORIO: la corrida NO relee la config antes de escribir [probe]

Prueba: `test_s2_apagado_a_media_corrida_retiene_apagador`. Mutante
(`app/precio/corrida.py`, `_aplicar_pendientes`): la lectura de settings
sale del loop (se lee una vez antes y se reusa: config rancia).

- Rojo (r3, real): `assert (2, 2) == (2, 1)`
  (la segunda escritura sale con la config vieja; sin retención).
- Verde restaurado: `1 passed`.

## M6 — OBLIGATORIO: la corrida aplica en el repaso una `live` cuyo goal pasó a `shadow`

Prueba: `test_s2_repaso_no_aplica_live_con_goal_pasado_a_shadow`. Mutante
(`app/precio/corrida.py`, `_aplicar_pendientes`):
`if not vigente or (mode == "live" and goal[0] != "live"):` → `if False:`.

- Rojo (r3, real): `assert (0, 1) == (0, 0)`
  (el PATCH sale; `n_patch == 1`).
- Verde restaurado: `1 passed`.

## M7 — `test_s2_repaso_no_aplica_live_con_goal_cerrado`

Mutante (`app/precio/corrida.py`): `goal[2] > hoy` → `goal[2] >= hoy`
(cerrado hoy cuenta vigente y se aplica).

- Rojo (r3, real): `assert (0, 1) == (0, 0)`.
- Verde restaurado: `1 passed`.

## M8 — `test_s2_repaso_no_aplica_live_con_goal_ya_no_live`

Mutante (`app/precio/corrida.py`): `goal[0] != "live"` → `goal[0] == "live"`
(invierte la rama: el goal shadow pasa y se aplica).

- Rojo (r3, real): `assert (0, 1) == (0, 0)`.
- Verde restaurado: `1 passed`.

## M9 — `test_s2_repaso_no_aplica_live_sin_goal_id`

Mutante (`app/precio/corrida.py`): `if not vigente or ...` →
`if gid is not None and (not vigente or ...)` (sin goal salta el chequeo).

- Rojo (r3, real): `assert (0, 1) == (0, 0)`.
- Verde restaurado: `1 passed`.

## M10 — `test_s2_universo_sin_entrada_vale_off`

Mutante (`app/precio/corrida.py`, `_decidir_lectura`): default del `.get` de
universo `"off"` → `"live"` (sin entrada vale live: decide y escribe).

- Rojo (r3, real): `assert (1, 0) == (0, 0)`
  (decide 1 —el aplicar la retiene por su propio default off—; la compuerta
  de decidir sí importa).
- Verde restaurado: `1 passed`.

## M11 — `test_umbral_ausente_aborta_al_arrancar[precio_modo_universo]`

Mutante (`app/precio/corrida.py`, `correr`):
`modo_universo = _modo_universo_config(...)` → `modo_universo = {}`
(la clave ausente ya no se exige).

- Rojo: `E Failed: DID NOT RAISE ValueError`.
- Verde restaurado: `1 passed`.

## M12 — `test_s2_guardar_precio_modo_global_off_conserva_claves`

Mutante (`app/config_write.py`, `_aplica_precio_global`):
`nuevo["precio_modo_global"] = modo` → `nuevo["precio_modo_global"] = nuevo.get(...)`
(el rastro dice el cambio pero el valor no viaja).

- Rojo: `assert fila[1]["precio_modo_global"] == "off"` (sigue `"live"`).
- Verde restaurado: `1 passed`.

## M13 — `test_s2_proxima_precio_modo_universo_fusiona_por_universo`

Mutante (`app/config_write.py`, `_aplica_precio_universo`):
`fusionado = dict(vigente) ...` → `fusionado = {}` (reemplaza, no fusiona).

- Rojo: `assert nuevo["precio_modo_universo"] == {"amazon_mx/fba": "live", ...}`
  (pierde `amazon_mx/fba`).
- Verde restaurado: `1 passed`.

## M14 — `test_s2_proxima_precio_modos_rechaza_vocabulario_y_fuera_de_registro`

Mutante (`app/config_write.py`): valida `{}` en vez del entrante
(`_valida_precio_universo(entrante, ...)` → `...( {}, ...)`).

- Rojo: `DID NOT RAISE` en universo-fuera-de-registro y modo-turbo;
  `AttributeError: 'list' object has no attribute 'items'` en el caso lista
  (sin validación, la fusión revienta).
- Verde restaurado: `1 passed`.

## M15 — `test_s2_proxima_precio_global_corrupto_rechaza_edicion_que_no_lo_toca`

Mutante (`app/config_write.py`): la relectura de la resultante valida `None`
en vez del global vigente (la corrupción previa pasa).

- Rojo: `E Failed: DID NOT RAISE SettingsInvalido`.
- Verde restaurado: `1 passed`.

## M16 — `test_s2_endpoint_post_precio_modos_wire_200_422_401`

Mutante (`app/api_write.py`, ruta settings): se quita la línea
`precio_modo_global=cuerpo.precio_modo_global,` (el POST no cablea el global).

- Rojo: `assert ok.status_code == 200` → `422` (edición vacía).
- Verde restaurado: `1 passed`.

## M17 — `test_s2_literal_precio_modo_global_pineado_contra_vocabulario`

Mutante (`app/api_write.py`, `CuerpoSettings`): el `Literal` suma `"turbo"`.

- Rojo: `assert set(get_args(...)) == set(MODOS)` (sobra `"turbo"`).
- Verde restaurado: `1 passed`.

## M18 — `test_s2_get_settings_expone_precio_modos`

Mutante (`app/api_dashboard.py`, `settings`): `"precio_modo_global": ...` → `None`.

- Rojo: `assert datos["precio_modo_global"] == "shadow"` (llega `None`).
- Verde restaurado: `1 passed`.
