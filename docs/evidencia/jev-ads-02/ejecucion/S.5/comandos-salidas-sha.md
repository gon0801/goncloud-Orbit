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

## J5b: pantallas (TDD rojo -> verde, mutantes, panel)

Rama: `jev02/s5b-pantallas`. Base = HEAD:
`80da8768cf198355281b7d4b8c039cae90c1e9a0`
(J5a mergeado, PR #407). Sin commit nuevo en J5b: el arbol dirty
heredado SE CONSERVA (dictamen: los 2 sucios anunciados sirven; ver
prior result 1).

DSN: ORBIT_TEST_DSN="postgresql://orbit:***@localhost:5432/postgres"
(variable vacia en el entorno; los tests usaron el default de
`tests/test_schema.py::_test_dsn`; Postgres local). Sin claves `jev.*`
sembradas ni cambiadas; transporte falso en todo; sin migracion.
`PYTHONPATH=.` en cada corrida pytest.

Tamano (ningun modulo nuevo sobre 900; `test_architecture` verde):
`app/api_dashboard.py` 2180 -> 2340 (ya en allowlist con razon),
`app/ui.py` 658 -> 681, `app/jev_salud.py` 278, `app/jev_vista.py` 213.

### Rojo TDD (implementacion apartada con stash + plantillas nuevas fuera)

```
$ PYTHONPATH=. uv run --frozen pytest tests/test_api_dashboard.py -k "senal or gasto_sin_venta" -q
7 failed, 75 deselected   (las 7 API nuevas rojas; ej: 404 en vez de 422)

$ PYTHONPATH=. uv run --frozen pytest tests/test_ui.py -k "gasto_sin_venta or senal_antes" tests/test_jev_pantallas.py -q
3 UI nuevas rojas (jinja TemplateNotFound / ruta ausente)

$ PYTHONPATH=. uv run --frozen pytest tests/test_jev_pantallas.py -q
7 failed, 28 passed   (las 7 nuevas rojas; la dict_row con KeyError: 0,
  que es el aviso (a): sin la guarda, dict_row revienta)
```

Tras el rojo se restauro con `git stash pop` + plantillas de vuelta;
`git status` identico al heredado.

### Verde (Comprueba S.5 + pantallas)

```
$ PYTHONPATH=. uv run --frozen pytest tests/test_api_dashboard.py tests/test_ui.py \
  tests/test_ui_tema.py tests/test_jev_ads.py tests/test_jev_lectura.py \
  tests/test_architecture.py tests/test_jev_pantallas.py -q
370 passed, 1 warning in 9.68s   (cero saltadas; 335 de Comprueba + 35 pantallas)

$ uv run --frozen ruff check app/ tests/
All checks passed!

$ uv run --frozen ruff format --check app/api_dashboard.py app/ui.py app/jev_salud.py \
  app/jev_vista.py tests/test_api_dashboard.py tests/test_ui.py tests/test_jev_pantallas.py
7 files already formatted
```

### Mutantes J5b

15/15 cazados (uno por prueba nueva, J1-J15 en `mutantes.md`): cada
uno 1 failed con el mutante puesto y verde tras revertir por copia.
`cmp` de los 7 archivos contra respaldo: identicos.

### Panel de revision (solo lectura)

3 revisores GLM por `opencode run -m zai-coding-plan/<modelo>` con el
mismo prompt de 7 puntos (bloque espejo, avisos a/b/c, prohibiciones,
apagado, 2 mutantes): glm-4.7, glm-5.2 y glm-5.3, los tres 7/7 OK sin
fallas (salidas en `panel-glm-*-j5b.txt`). Hijos Muse: no disponibles
en este contexto (sin herramientas de delegacion); deslop/no-comments
aplicados a mano sobre el diff: 0 supresiones (comentarios en estilo
del repo, cada uno fija un por-que no obvio; `noqa` y `type: ignore`
con precedente en el archivo). Detalle en `panel-j5b.md`.
