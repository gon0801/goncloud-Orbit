# S.1: mutantes (uno por prueba nueva)

Cada mutante: rojo con mutante, verde restaurado, revertido al terminar.
Comando base (se omite por brevedad):

```console
$ PATH=/opt/homebrew/bin:$PATH ORBIT_TEST_DSN=postgresql://orbit:orbit@localhost:5432/postgres \
  PYTHONPATH=. .venv/bin/python -m pytest -q -p no:cacheprovider <prueba>
```

## M1 — `test_s1_ocho_live_mueven_precio`

Mutante (`app/precio/corrida.py`, `_aplicar_pendientes`): `).fetchall()`
→ `).fetchall()[:7]` (aplica 7 de 8 pendientes).

- Rojo: `FAILED test_s1_ocho_live_mueven_precio - assert...` (`1 failed`).
- Verde restaurado: `1 passed`.

## M2 — `test_s1_segunda_corrida_aplica_lo_pendiente`

Mutante (`app/precio/corrida.py`, `_aplicar_pendientes`): `AND NOT EXISTS
(SELECT 1 FROM precio_cambio ...)` → `AND EXISTS (...)` (solo "aplica" lo
que ya tiene cambio, o sea nada).

- Rojo: `FAILED test_s1_segunda_corrida_aplica_lo_pendiente` (`1 failed`).
- Verde restaurado: `1 passed`.

## M3 — `test_s1_corrida_abre_cierra_y_aborta_la_anterior`

Mutante (`app/precio/corrida.py`, `_cerrar_corrida`): `retenidas = 0` →
`retenidas = 1` (S.1 no retiene; la fila debe decir 0).

- Rojo: `FAILED test_s1_corrida_abre_cierra_y_aborta_la_anterior` (`1 failed`).
- Verde restaurado: `1 passed`.

## M4 — `test_s1_config_sin_caps_no_detiene`

Mutante (`app/precio/corrida.py`, `correr`):
`_modo_config(settings, "precio_modo_global")` →
`_modo_config(settings, "precio_cap_amazon_mx")` (vuelve a exigir el cap).

- Rojo: `FAILED test_s1_config_sin_caps_no_detiene - app...` (`1 failed`).
- Verde restaurado: `1 passed`.

## M5 — `test_s1_corrida_no_toca_apply_quota_state`

Mutante (`app/precio/corrida.py`, `_abrir_corrida`): inserta una fila
`('precio:' || platform, hoy, 5, 1)` en `apply_quota_state` al abrir.

- Rojo: `FAILED test_s1_corrida_no_toca_apply_quota_state` (`1 failed`).
- Verde restaurado: `1 passed`.

## M6 — `test_0058_rechaza_l_valor_sin_origen_y_mover_sin_verificacion`

Mutante (`migrations/0058_precio02_decision_exige.sql`): `CHECK (l_valor IS
NULL OR l_origen IS NOT NULL)` → `CHECK (l_valor IS NULL OR l_valor IS NOT
NULL)` (tautología: el CHECK deja de exigir).

- Rojo: `FAILED test_0058_rechaza_l_valor_sin_origen_y_mover_sin_verificacion`
  (`1 failed`).
- Verde restaurado: `1 passed`.

## M7 — `test_0058_reversa_quita_los_dos_check`

Mutante (`migrations/0058_reversa_precio02_decision_exige.sql`): el segundo
`DROP CONSTRAINT` repite `precio_decision_l_exige_origen` (la reversa deja
un CHECK puesto).

- Rojo: `FAILED test_0058_reversa_quita_los_dos_check` (`1 failed`).
- Verde restaurado: `1 passed`.

Verificación final: ningún residuo de mutantes (`grep` de las 6 marcas en
`app/precio/corrida.py` y `migrations/0058*.sql` da vacío) y focalizadas en
verde (`531 passed`, ver `evidencia.md` §3).
