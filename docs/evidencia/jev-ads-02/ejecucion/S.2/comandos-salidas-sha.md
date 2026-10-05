# S.2: comandos, salidas y SHA

Rama: `jev02/s2-nucleo`. Base y HEAD final (sin commits en este paso):
`436b8771c987090724247dd237a167a2687f56c8`.

DSN: ORBIT_TEST_DSN="postgresql://orbit:***@localhost:5432/postgres"
(variable vacia en el entorno; los tests usaron el default de
`tests/test_schema.py::_test_dsn`; Postgres 16.15 local). Sin claves
`jev.*` sembradas ni cambiadas; transporte falso y base de prueba en todo.

## Linea base (arbol limpio)

```
$ PYTHONPATH=. uv run --frozen pytest tests/test_jev_ads.py tests/test_jev_cli.py \
  tests/test_jev_catalogo.py tests/test_api_dashboard.py tests/test_api_fabrica.py -q
252 passed, 1 warning in 19.74s
```

## Rojo de "Pruebas primero" (TDD)

```
$ PYTHONPATH=. uv run --frozen pytest tests/test_jev_lectura.py -q
ModuleNotFoundError: No module named 'app.jev_lectura'

$ ... test_modulo_puro_sin_red_ni_db_en_top_level[jev_lectura.py]
FileNotFoundError: .../app/jev_lectura.py

$ ... test_mismo_origen_ignora_synced_at_y_exige_identidad_y_exhaustivo
assert False  (1 failed)

$ ... test_resolver_fichas_conserva_id_traido_y_acredita_archivado
ImportError: cannot import name 'resolver_fichas' from 'app.jev_catalogo'

$ ... test_reanudacion_ignora_synced_at_y_llama_solo_al_pendiente
ValueError: misma solicitud con otro payload  (1 failed)
```

## Sondeo previo al traslado (descartable, /tmp/s2-sondeo-enriquecer.py)

Comportamiento HOY de `AsesorAds._enriquecer`, que la caracterizacion pincha:

```
archivado recibe ficha: True
no-acredita conserva traido: True
traido fuera del dict: True
exhaustivo igual: True
```

## Comprueba final (tras implementar + revertir los 11 mutantes)

```
$ PYTHONPATH=. uv run --frozen pytest tests/test_jev_lectura.py tests/test_jev_ads.py \
  tests/test_jev_cli.py tests/test_jev_catalogo.py tests/test_api_dashboard.py \
  tests/test_api_fabrica.py -q
309 passed, 1 warning in 18.72s   (cero saltadas)

$ uv run --frozen ruff check app/ tests/
All checks passed!

$ uv run --frozen ruff format --check <9 archivos del paso>
9 files already formatted
```

`uv sync` corrio al inicio sin enlazar `.venv` al checkout principal
(cada worktree tiene el suyo).
