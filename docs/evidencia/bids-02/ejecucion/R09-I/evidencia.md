# R09-I: desarmar continua el ledger sin chocar UNIQUE(lote, orden)

## Prueba primero

`tests/test_fabrica_campanas.py::test_r09i_desarmar_con_pasos_1_a_n_continua_el_ledger`
(Postgres real, lote aplicado con pasos 1..2 como los deja `_mutar`).

Comando:

```bash
ORBIT_TEST_DSN=postgresql://orbit:orbit@127.0.0.1:5433/postgres \
  uv run --frozen python -m pytest -q -rs tests/test_fabrica_campanas.py
```

- Antes: el desarme pausaba las 2 campanas pero no escribia ninguna
  fila en `fabrica_lote_paso` (quedaban solo los ordenes 1 y 2).
- Despues: 1 passed. Los pasos de pausa quedan en 3 y 4, sellados
  `applied`. Vecinos: `tests/test_fabrica_campanas.py` 111 passed,
  0 saltadas.

## Cambios

- `tools/fabrica_campanas.py`: `_pausa_una` deja fila en el ledger
  antes del PUT y la sella con el readback, como `_cambia_estado_lote`
  ya hacia para pausar/reanudar. `_desarmar` fija `ctx.orden` en el
  maximo del lote antes de ciclar (el `_Ctx` fresco en 0 chocaria el
  `UNIQUE(lote, orden)` de un lote aplicado: era el gemelo del bug
  A-1, ya corregido en `pausar_lote`).
- `tests/test_fabrica_campanas.py`: el doble `_ConnFalsa` responde la
  consulta de maximo de orden, y el ensayo de staging suma las 5
  filas de pausa que el desarme ahora si deja (conteo declarado en la
  fila, no debilitado).

## Mutante

Dejar `ctx.orden = 0` en `_desarmar`: el INSERT de la primera pausa
choca `UNIQUE(lote, orden)` y la prueba nueva pasa a rojo
(1 failed). Revertido: 1 passed.
