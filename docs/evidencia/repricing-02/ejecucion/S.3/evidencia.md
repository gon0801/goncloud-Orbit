# S.3: construye la compuerta — evidencia

Rama: `rp02/sec3-seguridad-goals`, sobre S.2 (`d828e90`). Guía:
`plans/repricing-02-construccion.md` S.3 (líneas 533-573) + D.2 (998-1002).
TDD: las pruebas nuevas se escribieron primero y se miraron fallar por la
razón correcta antes de implementar (una es guarda: verde antes y después,
ver §2).

Comando base (se omite por brevedad):

```console
$ PATH=/opt/homebrew/bin:$PATH ORBIT_TEST_DSN=postgresql://orbit:orbit@localhost:5432/postgres \
  PYTHONPATH=. .venv/bin/python -m pytest -q -rs -p no:cacheprovider
```

## 1. Archivos

Nuevos: `app/precio/liberaciones.py` (único escritor de
`precio_compuerta_liberacion`, rol `app_admin`; `liberar` idempotente,
`CorridaSinRetencion`). Tocados: `app/precio/compuerta.py` (fusible,
insumo, corte; puro), `app/precio/corrida.py` (`_medir_compuerta` real con
fila por universo, `_aplicar_pendientes` con compuerta + corte, `correr`
valida `ConfigMotor`), `app/precio/fuentes.py` (`EstadoSeguridad`,
`UltimaCorrida`, `leer_seguridad`), `app/api_precios.py`
(`_seguridad_precio` + clave `seguridad` solo en /precios, no en /salud),
`app/templates/_precios_seguridad.html` (modos, última corrida,
retenciones del día, botón Soltar con `data-liberar-corrida`; la ruta
`POST /api/precios/compuerta/{id}/liberar` es de G.2, aquí no se cablea),
`tests/test_precio_compuerta.py` (8 pruebas puras),
`tests/test_precio_corrida.py` (11 pruebas DB + `SETTINGS` con las claves
H de la 0054 + `LINEA_CRONTAB_PRECIO*` a las dos líneas D.2 +
`_escenario` con fees partidos),
`tests/test_precio_pantalla.py` (1 prueba del bloque),
`tests/test_architecture.py` (`liberaciones.py` a excepciones,
`precio_compuerta_medicion` a `_ESCRIBE_CORRIDA`,
`_ESCRIBE_LIBERACION` nuevo), `docs/DEPLOY.md` (dos líneas + fila UTC).
Contratos intactos: `app/precio/tipos.py`, `puerto.py`, `config.py`,
`app/precio_mercados.py`, `app/estimacion_universo.py`, `0053/0054/0059`.

## 2. Rojo TDD (antes de implementar)

```console
$ pytest tests/test_precio_compuerta.py
E   ImportError: cannot import name 'Abierta' from 'app.precio.compuerta'
1 error

$ pytest tests/test_precio_corrida.py -k "test_s3_ or test_linea_crontab"
11 failed, 1 passed, 99 deselected
# 10 fallan por TypeError (_medir_compuerta aún de 4 args) o
# ModuleNotFoundError (liberaciones) o assert (cron); el seed de fee
# fallaba además por CHECK (ver nota OJO 2 abajo).
# El que pasa es test_s3_repaso_sin_nada_pendiente_no_llama_al_mercado:
# guarda verde-antes-y-después (fija que el repaso ocioso no toca al
# Mercado; su mutante (`if lote:` → `if True:`) sí lo pone rojo).

$ pytest tests/test_precio_pantalla.py -k test_s3_
1 failed (KeyError 'seguridad' vía UndefinedError en plantilla)
```

Nota OJO 2: el primer seed de `estimacion_escenario` para el test de fees
(`incompleta` + moneda) violó `estimacion_escenario_no_disponible_sin_total`:
fuera de `disponible` la moneda va NULL. El test final siembra escenarios
`disponible` con `fee_variable`/`fee_fijo` (vía `_escenario` extendido).
Mapeo cerrado sin inventar columnas: costo/envío/retención =
`c_valor`/`l_valor`/`r_valor` de la decisión; fx/fees = `fx_rate`,
`fee_variable`, `fee_fijo` del escenario por `escenario_id`
(moneda/par deben coincidir; ausente = no compara).

## 3. Verde focalizado (después de implementar)

```console
$ pytest tests/test_precio_corrida.py tests/test_precio_compuerta.py \
  tests/test_architecture.py tests/test_precio_pantalla.py \
  tests/test_precio02_mercado.py
342 passed, 1 warning in 28.52s

$ pytest tests/test_precio02_contratos.py tests/test_precio02_retencion.py \
  tests/test_precio02_base.py
# incluidos en corrida de 130 passed con pantalla+mercado (misma sesión)

$ ruff check app/ <4 tests> && ruff format --check <tocado>
All checks passed! / 19 files already formatted
```

No se corrió la batería completa (orden del brief).

## 4. Desviaciones con razón

1. `evaluar` recibe `ConfigMotor`, no `ConfigPrecio` (bosquejo E): el
   corte 0.a partió la config y el fusible vive en el motor; `config.py`
   es contrato sellado. `correr` valida el motor completo (producción
   trae todas las claves: D.0 + 0054 §12).
2. `_medir_compuerta` recibe `corrida_id` (el bosquejo J no lo firma):
   la fila de medición lo exige; el espía viejo se actualizó.
3. `saltado` no mueve el `Cortacircuito` (neutro): ni prueba fallo ni
   recuperación. Fijado en prueba.
4. Corte revisa antes del split shadow/live (orden literal del bosquejo):
   con el corte abierto, la pendiente sombra también se retiene
   (`corte_errores`, se reintenta sola).
5. `EstadoSeguridad.modo_*` son `None` con config vieja (fail-open como
   `universos_encendidos`): el contrato B9 exige /precios 200 sin claves
   de modo; solo el motor fail-closea.
6. Desempate entre insumos que disparan: más saltos gana; empate, orden
   canónico (`costo` primero). Fijado en código.
7. `liberaciones.py` sin candado propio de imports (precedente:
   `cambios.py`): solo excepción por nombre + escritor único de tabla.
8. `test_s3_repaso_*` verde antes de implementar (guarda, ver §2).
