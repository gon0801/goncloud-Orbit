# R09-G: el tope limita por producto

Decision de diseno ya tomada por el dueno: un producto no puede tener
dos impulsos sin terminar a la vez en la misma plataforma, aunque sean
listings distintos. Queda escrita en la decision 4 de
`docs/superpowers/specs/2026-10-09-bids-02-design.md`.

## Prueba primero

`tests/test_impulso_io.py::test_r09g_tope_por_producto_previa_marca_y_lanza_rechaza_409`

Comando:

```bash
ORBIT_TEST_DSN=postgresql://orbit:orbit@127.0.0.1:5433/postgres \
  uv run --frozen python -m pytest -q -rs tests/test_impulso_io.py
```

- Antes: la vista previa mostraba lanzables los dos listings del
  mismo producto (`planes` con 2).
- Despues: 1 passed. La previa marca el segundo no lanzable (en la
  misma solicitud y contra un vivo de otro listing) y `lanza` lo
  rechaza con 409 sin crear impulso. Vecinos
  (`tests/test_impulso_io.py`, `tests/test_api_fabrica.py`):
  211 passed, 0 saltadas.

## Cambios

- `app/impulso_io.py`: `previsualiza` excluye el segundo listing de un
  producto ya presente en la vista y el de un producto con impulso
  vivo en otro listing. `lanza` suma el chequeo previo
  `_rechaza_si_producto_con_vivo` (409 sin crear nada) y el chequeo
  bajo el advisory lock pasa de listing a producto
  (`_SQL_VIVO_PRODUCTO`). `_SQL_VIVO` (solo listing) se elimino.
- `docs/superpowers/specs/2026-10-09-bids-02-design.md`: la decision
  escrita en la decision 4 del impulso.

## Mutante

Anular la consulta de vivo de otro listing en la vista previa
(`vivo_otro = None`): la prueba nueva pasa a rojo (1 failed). La
reversion se hizo con `git checkout` sobre el archivo sin commitear,
lo que obligo a reaplicar el cambio de la fila; reaplicado, la prueba
volvio a verde y la bateria de vecinos dio 211 passed.
