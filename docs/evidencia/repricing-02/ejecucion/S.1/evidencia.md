# S.1: borra el cupo y guarda al decidir — evidencia

Rama: `rp02/sec3-seguridad-goals`. Guía: `plans/repricing-02-construccion.md` S.1
(líneas 451-493). TDD: las 7 pruebas nuevas se escribieron primero y se
miraron fallar por la razón correcta antes de implementar.

## 1. Compuerta del cupo (guía textual)

Antes del paso: 37 líneas. Después: 0.

```console
$ { test -e app/precio/cuota.py && echo "FALTA: app/precio/cuota.py sigue ahi"; \
	git grep --untracked -nE \
	"repartir_cupo|precio_cap_|cuota_mod|cuota_precio|validar_cap|motor_cuota|_cuota_reescrita|_ingreso_60d|pr\.cuota|\"cuota\"" \
	-- app tools; } | wc -l
       0
```

Borrados: `app/precio/cuota.py`, `reglas.repartir_cupo`, `reglas._prioridad`,
`corrida._fase3_uno`, `corrida._cuota_reescrita`, `precio_mercado._ingreso_60d`,
campo `prioridad` de `Decision`, campo `ingreso_60d` de `EntradaDecision`,
motivo `cuota` de `MOTIVOS_MANTENER`, cupo de `app/api_precios.py` (clave
`cuota`, `_cuota_precio`, `_SQL_CUOTA_USED`) y de `app/templates/salud.html`,
wording `cuota` de `app/notifica.py`. Nuevos: `migrations/0058_*` (2 CHECK
`NOT VALID` + reversa que los quita) y `correr` en tres pasos
(`_decidir_pendientes`, `_medir_compuerta`, `_aplicar_pendientes`) con fila
`precio_corrida` por ejecución. `apply_quota_state`, sus triggers y
`apply_cap_de_config` intactos (son de Ads); `precio_cap_*` sigue en la
config (se ignora).

## 2. Rojo TDD (antes de implementar)

```console
$ pytest -q -p no:cacheprovider tests/test_precio_corrida.py -k "test_s1_"
FAILED test_s1_ocho_live_mueven_precio - assert (8, 5) == (8, 8)
FAILED test_s1_segunda_corrida_aplica_lo_pendiente - assert (0, 0) == (0, 1)
FAILED test_s1_corrida_abre_cierra_y_aborta_la_anterior - assert [] == [('amazon_mx'...)]
FAILED test_s1_config_sin_caps_no_detiene - ValueError: config sin precio_cap_amazon_mx
FAILED test_s1_corrida_no_toca_apply_quota_state
E  Left contains one more item: ('precio:amazon_mx', 5, 1)
5 failed, 102 deselected

$ pytest -q -p no:cacheprovider tests/test_precio_migracion.py -k "0058"
E  FileNotFoundError: .../migrations/0058_precio02_decision_exige.sql
2 failed, 172 deselected
```

## 3. Verde (comando focalizado de la guía)

```console
$ PATH=/opt/homebrew/bin:$PATH ORBIT_TEST_DSN=postgresql://orbit:orbit@localhost:5432/postgres \
  PYTHONPATH=. .venv/bin/python -m pytest -q -rs -p no:cacheprovider \
  tests/test_precio_corrida.py tests/test_precio_reglas.py tests/test_precio_d0.py \
  tests/test_architecture.py tests/test_precio_migracion.py tests/test_precio_pantalla.py
531 passed, 1 warning in 33.66s
```

El warning es preexistente (`starlette.testclient` deprecado por `httpx`,
ajeno a S.1). No se corrió la batería completa (orden del plan).

## 4. Ruff sobre lo tocado

```console
$ ruff check <13 archivos tocados> && ruff format --check <mismos 13>
All checks passed!
13 files already formatted
```

## 5. Archivos no listados que importan lo cambiado (solo colecta)

```console
$ pytest -q -p no:cacheprovider --collect-only tests/test_precio02_mercado.py \
  tests/test_precio02_base.py tests/test_precio02_contratos.py tests/test_precio_write.py \
  tests/test_precio_cobertura.py tests/test_precio_goals.py tests/test_api_dashboard.py \
  tests/test_notifica.py tests/test_arq_precio_s.py tests/test_arq_precio_f.py \
  tests/test_arq_precio_g.py tests/test_arq_precio_m.py
403 tests collected in 0.68s
```

Sin errores de importación tras borrar `cuota.py` y renombrar pasos.

## 6. Desviaciones de la guía (con razón)

1. `test_relanzar_con_otro_pedido_no_reusa_cotizacion` sabotea `_persistir_uno`
   en vez de la compuerta: esta prueba exige morir ANTES de persistir y morir
   en la compuerta dejaría fila (el UNIQUE impediría re-decidir con el goal
   nuevo). Los otros 3 sabotajes sí van a `_medir_compuerta`.
2. `tests/test_precio_d0.py` calza un `app.precio.cuota` falso (copia exacta
   del `validar_cap` de REPRICING 01) solo mientras carga
   `docs/evidencia/repricing-01/D.0/verificar_config.py`, y lo retira
   enseguida: el guion D.0 es evidencia congelada que lo importa. Solo se
   borró la prueba que simulaba `validar_cap`; el resto de D.0 sigue verde.
3. `correr` exige `precio_modo_global` en settings (clave que la 0054 ya
   siembra en producción) y lo guarda en la fila: la columna es `NOT NULL`
   y la guía no dice qué poner; inventar un default violaría "dato faltante
   = None, nunca constante". S.2 lo hará cumplir (hoy solo se registra).
4. `_cerrar_corrida` cuenta `errores = len(errores)` pre-`avisar` (el fallo
   del sender no es de la corrida) y `EscrituraNoDisponible` registra UN
   error (antes uno por goal): lo no aplicado queda pendiente y la siguiente
   corrida lo reintenta (antes quedaba decidido-sin-aplicar para siempre).
5. La prueba 0058 vive en `tests/test_precio_migracion.py` (ORDEN58 local =
   ORDEN02 + 0058) para que la corra el comando focalizado de la guía; la
   0058 también se sumó a los ORDEN59 de corrida y pantalla y al omit de
   `_db_nueva` sin 0039.
