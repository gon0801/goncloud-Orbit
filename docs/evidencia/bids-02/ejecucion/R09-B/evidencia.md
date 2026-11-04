# R09-B: la vista previa valida el PlanGrupoV2 antes de mostrarlo lanzable

## Prueba primero

`tests/test_impulso_io.py::test_r09b_previa_tope_bajo_el_piso_no_lanzable_y_lanza_sin_502`

Comando:

```bash
ORBIT_TEST_DSN=postgresql://orbit:orbit@127.0.0.1:5433/postgres \
  uv run --frozen python -m pytest -q -rs \
  tests/test_impulso_io.py::test_r09b_previa_tope_bajo_el_piso_no_lanzable_y_lanza_sin_502
```

- Antes: la previa devolvia el plan como lanzable (`planes` con un
  elemento) con un tope de 10 MXN, cuyo presupuesto por campana
  (0.36 MXN) queda bajo el piso de bid de la moneda (1.00 MXN).
- Despues: 1 passed. El producto queda en `no_lanzables` con el motivo
  de rango y `lanza` responde 422 sin crear lotes. Vecinos:
  `tests/test_impulso_io.py` 99 passed; `tests/test_impulso.py`,
  `tests/test_api_fabrica.py`, `tests/test_pantalla_productos.py` y
  `tests/test_ui_productos.py` 150 passed, 0 saltadas.

## Cambio

`app/impulso_io.py`: `_plan_producto` valida el `PlanGrupoV2` derivado
con `fp._valida_plan_v2` antes de devolver el plan. `previsualiza` ya
traducia `fp.PlanInvalido` a `no_lanzables`.

## Mutante

Quitar la llamada a `fp._valida_plan_v2` deja el plan lanzable: la
prueba nueva pasa a rojo (1 failed). Revertido: 1 passed.
