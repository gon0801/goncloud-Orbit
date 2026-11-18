# D-H — las pruebas nuevas fallan sin el arreglo

SHA previo: `feab505127a6231ef34a01bbf67882d0964832a0` (origin/master al abrir el bloque, ya con BASE).
Método: `git archive feab505` a una carpeta temporal, se copian solo `tests/test_ledger_sync.py` y
`tests/test_ledger_atribucion.py` de la rama y se corre:

```
PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q -rs tests/test_ledger_sync.py tests/test_ledger_atribucion.py -k arreglos03_dh_
2 failed, 27 deselected in 1.78s
```

## test_arreglos03_dh_devolucion_sin_categoria_queda_como_refund (D-H.2)

Falla por comportamiento: la devolución de MeLi sin categoría sale con `fee_type` vacío.

```
E       AssertionError: assert (EventoLedger(platform='meli', kind='refund', ...) is not None and 'refund' == 'refund'
E           refund and None == 'refund')
E        +  where None = EventoLedger(platform='meli', kind='refund', ...).fee_type
```

## test_arreglos03_dh_migracion_devoluciones_y_su_reversa (D-H.4)

En el SHA previo la migración no existe:

```
E       FileNotFoundError: [Errno 2] No such file or directory: '...\migrations\0087_arreglos03_devoluciones_meli.sql'
```

Como ese rojo solo dice «falta el archivo», se comprobó además que la prueba discrimina una migración
mal escrita. Sobre la misma carpeta temporal se cambió la migración por una versión rota y se corrió
`pytest -q tests/test_ledger_atribucion.py -k arreglos03_dh_`:

| Migración usada | Resultado |
| --- | --- |
| ida sin `platform = 'meli'` en el WHERE (etiqueta también Amazon) | 1 failed |
| reversa que pone NULL a todas las devoluciones MeLi en vez de solo los IDs guardados | 1 failed |
| ida que no vuelve a encender `ledger_solo_reatribuye` | 1 failed |
| ida sin el REVOKE (los servicios leerían la tabla de IDs) | 1 failed |
| la migración entregada | 1 passed |
