# S.4: mutantes (1 obligatorio + uno por prueba nueva)

Cada mutante: rojo con mutante, verde restaurado, revertido al terminar.
Comando base: el de `evidencia.md`. Todos se demostraron contra su prueba
(una línea `-k`) y se revirtieron; el verde final (§3 de `evidencia.md`)
corre sobre el árbol limpio (verificado: `app/precio/corrida.py` sin
diff y `grep` de los marcadores sin hits salvo un comentario previo).

## M0 — OBLIGATORIO: el cooldown ignora un error con readback nuevo

Prueba: `test_s4_error_con_efecto_si_consume_cooldown`. Mutante
(`app/precio/reglas.py`, `decidir`): `or cambio.sin_efecto` →
`or cambio.estado == "error"`.

- Rojo: reintenta en vez de enfriar (`assert (1, 1) == (1, 0)`,
  `1 failed`).
- Verde restaurado: `1 passed`.

## M1 — `test_s4_cooldown_ignora_cambio_sin_efecto`

Mutante (`app/precio/reglas.py`, `decidir`):
`or cambio.sin_efecto` → `or False`.

- Rojo: `1 failed` (`mantener/cooldown`, no `PideCotizacion`).
- Verde restaurado: `1 passed`.

## M2 — `test_s4_error_sin_efecto_no_consume_cooldown`

Mutante (`app/spapi/precio_mercado.py`, `cambios_previos`):
`(readback, readback_moneda) == (antes, antes_moneda)` →
`!= (antes, antes_moneda)`.

- Rojo: no reintenta (`assert (1, 0) == (1, 1)`, `1 failed`).
- Verde restaurado: `1 passed`.

## M3 — `test_s4_error_con_efecto_si_consume_cooldown`

Mutante (`app/spapi/precio_mercado.py`, `cambios_previos`):
`== (antes, antes_moneda)` → `== (despues, antes_moneda)`
(readback contra el precio nuevo).

- Rojo: reintenta (`assert (1, 1) == (1, 0)`, `1 failed`).
- Verde restaurado: `1 passed`.

## M4 — `test_s4_huerfana_movida_en_corrida_confirma_y_cuenta`

Mutante (`app/precio/cambios.py`, `cerrar_huerfanas`):
`elif vivo is not None and ...` → `elif False:` (sin rama
recuperada).

- Rojo: `assert ('error', None, None) == ('confirmado', ...)`
  (`1 failed`).
- Verde restaurado: `1 passed`.

## M5 — `test_s4_huerfana_quieta_es_error_sin_efecto`

Mutante (`app/precio/cambios.py`, `cerrar_huerfanas`, rama quieta):
`error_code="huerfana_sin_efecto"` → `"huerfana_sin_patch"`.

- Rojo: `'huerfana_sin_patch' != 'huerfana_sin_efecto'`
  (`1 failed`).
- Verde restaurado: `1 passed`.

## M6 — `test_s4_huerfana_otro_precio_sella_sin_patch`

Mutante (`app/precio/cambios.py`, `cerrar_huerfanas`, rama `else`):
`error_code="huerfana_sin_patch"` → `"huerfana_sin_efecto"`.

- Rojo: `'huerfana_sin_efecto' != 'huerfana_sin_patch'`
  (`1 failed`).
- Verde restaurado: `1 passed`.

## M7 — `test_s4_huerfana_ilegible_sella_sin_patch`

Mutante (`app/precio/cambios.py`, `cerrar_huerfanas`):
`except Exception as exc:` → `except KeyError:` (lo ilegible ya
no se sella: propaga).

- Rojo: `PrecioVivoAusente: sin oferta propia` (`1 failed`).
- Verde restaurado: `1 passed`.

## M8 — `test_s4_corrida_mx_no_cierra_cambios_us`

Mutante (`app/precio/cambios.py`, `cerrar_por_observacion`): fuera
`c.platform = %s AND` (y su parámetro).

- Rojo: el mutante cierra el cambio US
  (`assert 'confirmado' == 'enviado'`, `1 failed`).
- Verde restaurado: `1 passed`.

## M9 — `test_s4_dos_dias_sin_efecto_reintentan_y_arman_racha`

Mutante (`app/precio/corrida.py`, `freno_por_error`):
`racha >= dias` → `racha > dias`.

- Rojo: `assert False is True` en el `freno_por_error` de mañana
  (el reintento de hoy sigue verde; `1 failed`).
- Verde restaurado: `1 passed`.

## M10 — `test_s4_racha_sin_efecto_frena_cuarta_corrida`

Mutante (`app/precio/corrida.py`, `freno_por_error`):
`AND estado = 'error'` → `AND estado = 'error' AND readback_precio
IS NULL` (el freno filtrado que el encargo prohíbe).

- Rojo: `assert ('frenado', 'no_converge') == ('frenado',
  'api_error')` (`1 failed`; ver §4.8 de `evidencia.md`).
- Verde restaurado: `1 passed`.
