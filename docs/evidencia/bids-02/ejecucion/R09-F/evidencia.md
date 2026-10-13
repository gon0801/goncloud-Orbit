# R09-F: el vigia no calla un dia perdido

## Pruebas primero

En `tests/test_impulso_io.py`:

- `test_r09f_vigia_sin_ingesta_avisa_y_marca_no_atendido`
- `test_r09f_cli_vigia_sin_impulsos_sale_0`
- `test_r09f_cli_vigia_sin_ingesta_sale_1_y_avisa`
- `test_r09f_cli_vigia_con_todo_fallado_sale_1`

Comando:

```bash
ORBIT_TEST_DSN=postgresql://orbit:orbit@127.0.0.1:5433/postgres \
  uv run --frozen python -m pytest -q -rs tests/test_impulso_io.py -k r09f
```

- Antes: 3 failed (el comando salia 0 con todo fallado y sin
  ingesta; la accion no traia marca de atendido). El control "sin
  impulsos sale 0" ya pasaba.
- Despues: 4 passed. Vecinos (`tests/test_impulso_io.py`,
  `tests/test_impulso_vigia_cron.py`, `tests/test_cli.py`):
  167 passed, 0 saltadas. La linea de cron no cambia.

## Cambios

- `app/impulso_io.py`: `AccionVigia` gana `atendido`. Sin ingesta de
  la plataforma (corte None) la accion queda no atendida y `vigila`
  avisa por stderr con el id del impulso.
- `app/cli_bids.py`: `main_impulso_vigia` cuenta los impulsos vivos
  antes de correr; si habia vivos y ninguno quedo atendido, avisa y
  sale 1. Sin vivos sigue en 0 y un fallo aislado sigue en 0 (su aviso
  ya salio por stderr en `vigila`).

## Mutante

Quitar `atendido=False` del caso sin ingesta: las dos pruebas de sin
ingesta pasan a rojo (2 failed). Revertido: 4 passed.
