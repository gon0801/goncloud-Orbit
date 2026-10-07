# S.5: comandos, salidas y SHA

Rama: `jev02/s5-pantallas`. Base: `96daae6b4dfc86e45c1e24b1dfbfda9180a3af09`
(merge J4).

DSN: ORBIT_TEST_DSN="postgresql://orbit:***@localhost:5432/postgres"
(variable vacia en el entorno; los tests usaron el default de
`tests/test_schema.py::_test_dsn`; Postgres local). Sin claves `jev.*`
sembradas ni cambiadas; transporte falso y base de prueba en todo; sin
migracion y sin plantillas en este bloque.

## Comprueba final (arbol con el bloque)

```
$ PYTHONPATH=. uv run --frozen pytest tests/test_jev_pantallas.py tests/test_jev_lectura.py \
  tests/test_architecture.py tests/test_jev_ads.py tests/test_jev_cli.py \
  tests/test_jev_senales.py tests/test_jev_senales_cron.py tests/test_jev_catalogo.py \
  tests/test_api_dashboard.py tests/test_cli.py -q
434 passed, 1 warning in 16.80s   (cero saltadas)

$ uv run --frozen ruff check app/ tests/
All checks passed!

$ uv run --frozen ruff format --check app/jev_lectura.py app/jev_vista.py \
  app/jev_salud.py tests/test_jev_pantallas.py
4 files already formatted
```

De las 434, 28 son nuevas (`tests/test_jev_pantallas.py`: 26 puras/falsas +
2 de punta a punta contra Postgres real con 0001 + 0002 + 0004 + 0049 +
0050 + 0051 + 0052).

## Rojo previo al verde (honesto, sin TDD estricto)

Las pruebas se escribieron contra el contrato y cayeron dos veces por
errores de la prueba (falsa sin ORDER BY; `vista.termino` en vez de
`vista.clave.termino`) y una por bug real del bloque: `IN %s` con tupla
de tuplas revienta en psycopg (`syntax error at or near "$1"`, cazado
por `test_vistas_de_claves_punta_a_punta`); se paso a plazas por clave
con valores parametrizados. Tras eso, 28 passed.
