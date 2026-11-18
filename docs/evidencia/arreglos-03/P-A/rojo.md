# P-A.1: la prueba falla antes del arreglo

Prueba: `tests/test_precio_corrida.py::test_arreglos03_pa_reversa_por_corrida_solo_toma_su_corrida`.

## Rojo en el SHA previo

- SHA previo: `feab505` (`origin/master` con BASE y D-J integrados, base de este bloque).
- Cómo: `git archive feab505` a un directorio temporal, se le agrega solo la prueba nueva al final de
  `tests/test_precio_corrida.py` y se corre:
  `PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q -rs tests/test_precio_corrida.py -k arreglos03_pa_`
- Resultado: `1 failed` en `tests/test_precio_corrida.py:5352`, `AssertionError: assert None == 8`.
  La decisión que deja la corrida queda con `corrida_id` NULL (lo mismo que el plan dice de `a4644aad`).

## La prueba discrimina las dos mitades del arreglo

- Sin sello (código de `feab505`): falla en `:5352`, la decisión no trae `corrida_id`.
- Con el sello puesto (`corrida.py` y `cambios.py` de este bloque) pero `planear_reversa` con
  `SeleccionPorCorrida` todavía leyendo con `_SQL_PLAN_POR_FECHA`: falla en `:5386`,
  `assert [2, 3] == [2]`. El cambio real escrito a mano ese mismo día, sin `corrida_id`, se cuela en
  la reversa por corrida.
- Con el bloque completo: `1 passed` (ver `acepta.txt`).

## Relevo

El bloque lo empezó otro worker. Su prueba y su `persistir_decision` no llegaron a pasar en verde:
el INSERT de `persistir_decision` tenía 42 columnas y 41 `%s`, y la decisión escrita a mano en la
prueba no llenaba las columnas NOT NULL. Las dos cosas se corrigieron aquí (el INSERT lleva su `%s`
y la prueba usa el ayudante `_decision_subir` del mismo archivo).
