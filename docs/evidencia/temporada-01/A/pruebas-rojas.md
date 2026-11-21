# Pruebas nuevas del bloque A, demostradas fallando contra el SHA previo (regla 9)
SHA previo aplicable: b92de90 (master con el bloque I y la limpieza #688; el bloque A no esta).
Metodo: copia de tests/test_temporada01_a.py sobre ese SHA con el import de app.optimizer.temporada diferido dentro de cada prueba (el modulo no existe ahi; sin el diferido, la coleccion aborta y no se ve el fallo de cada prueba).
Comando: PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_temporada01_a.py -v

```
FAILED tests/test_temporada01_a.py::test_temporada01_a_fechas_del_calendario
FAILED tests/test_temporada01_a.py::test_temporada01_a_post_rechaza_booleanos
FAILED tests/test_temporada01_a.py::test_temporada01_a_campana_topada_una_definicion
FAILED tests/test_temporada01_a.py::test_temporada01_a_estado_de_baja_con_mantener_pausada
======================== 4 failed, 1 warning in 1.17s =========================
```

Cada prueba falla por si sola: el modulo app/optimizer/temporada no existe en el SHA previo (ImportError dentro de la prueba, no en la coleccion). En el bloque queda la corrida verde del SHA entregado en acepta-a2.txt y acepta-a3.txt.
