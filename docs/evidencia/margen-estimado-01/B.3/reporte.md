# MARGEN ESTIMADO 01 · B.3 — Reporte AC10–AC14

Fecha: 2026-09-08 14:44 UTC. Rama `orbit/margen-estimado-01-bloque-b`.
Rol de consulta: `orbit_read`. Transaccion `REPEATABLE READ READ ONLY`.
No se crearon campañas Amazon. No se toco Reputacion, bridge ni accounting.

## Estado por criterio

| ID | Resultado | Evidencia |
|---|---|---|
| AC10 | Cubierto en tests. Sangrado de `estimacion` en el plan falla el pytest. | `tests/test_estimacion_aislamiento_fabrica.py`, `ac10.md` |
| AC11 | Ya cubierto en bloque A (as-of / idempotencia). B.3 no lo reabre. | `tests/test_estimacion_venta.py` y schema A.1 |
| AC12 | GET catalogo/evaluacion en B.1: solo DB, sin OAuth/HTTP de fees. | `test_get_estimacion_no_llama_http_oauth_ni_write` |
| AC13 | UI B.2. Fuera de este diff. | `tests/test_ui_fabrica.py` |
| AC14 | Plantilla lista. Caso vivo bloqueado. | `conciliacion.md`. 0028 no esta en produccion |

## SQL en vivo

`select.sql` corrio entero el 2026-09-08 14:44:37 UTC.

Seccion A (universo) relleno en `conteos.json`:

- 342 listings MX, 176 US.
- Universo FBA MX por `disponibilidad_observation`: 247 listings (158 con stock, 89 en cero).
- 115 de esos 247 no tienen fila en `v_margen_producto` (sin ventas observadas).
- `v_margen_producto` MX: 141 filas, 5 con `margen_neto_pct` no null.

Seccion B (escenarios): omitida por `\if`.
`to_regclass('public.estimacion_escenario')` es NULL. 0028/0029 no estan
aplicadas. Deploy es B.5. Tras ingerir, repetir el mismo `select.sql` y
pegar las filas B en `conteos.json`.

## Tests

```
PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_estimacion_aislamiento_fabrica.py
......
6 passed, 1 warning in 1.11s
```

Rojo discriminante (parche temporal en `previsualizar` que metia
`plan["estimacion"]`, revertido despues):

```
FAILED tests/test_estimacion_aislamiento_fabrica.py::test_mismo_plan_huella_y_payload_con_estimacion_distinta
AssertionError: assert not True
  + where True = _tiene_clave(..., 'estimacion')
```

No queda parche en `app/fabrica_web.py`.

## Pendiente para B.4 / B.5

- Aplicar 0028/0029, ingerir FBA MX, repetir seccion B.
- Completar `conciliacion.md` con un listing FBA MX `disponible` sin ventas,
  conciliando P/C/F/R contra bridge, Product Fees, sku_cost y politica.
- PR y CI completo son B.4. Este commit no hace push.
