# MARGEN ESTIMADO 01 · B.3 — Reporte AC10–AC14

Fecha: 2026-09-08 19:11 UTC. Rama `orbit/margen-estimado-01-bloque-b`.
Rol de consulta: `orbit_read`. Transaccion `REPEATABLE READ READ ONLY`.
No se crearon campañas Amazon. No se toco Reputacion, bridge ni accounting.
0028/0029 **no** se aplicaron a produccion.

## Estado por criterio

| ID | Resultado | Evidencia |
|---|---|---|
| AC10 | Cubierto en tests. Sangrado de `estimacion` en el plan falla el pytest. | `tests/test_estimacion_aislamiento_fabrica.py`, `ac10.md` |
| AC11 | Ya cubierto en bloque A (as-of / idempotencia). B.3 no lo reabre. | `tests/test_estimacion_venta.py` y schema A.1 |
| AC12 | GET catalogo/evaluacion en B.1: solo DB, sin OAuth/HTTP de fees. | `test_get_estimacion_no_llama_http_oauth_ni_write` |
| AC13 | UI B.2 (+ smoke navegador en `../B.2/`). | `tests/test_ui_fabrica.py`, evidencia B.2 |
| AC14 | **Cerrado** con listing FBA MX 1213 sin ventas. | `conciliacion.md`, `calculo-independiente-1213.json`, `fees-product-fees-1213.json` |

## AC14 — resumen numerico

Listing Orbit **1213** · ASIN `B0C8RVWG4F` · SKU `SK-YBQX-XQWV` · FBA MX · sin fila en `v_margen_producto`.

| Componente | Importe MXN | Fuente |
|---|---|---|
| P | 988.0000 | bridge = Orbit listing_price |
| I = P/1.16 | 851.7241 | Decimal |
| C | 341.0000 | sku_cost 2068 neto; corrida diaria 124 ok |
| F | 191.7600 | Product Fees = Σ FinalFee (127.76+64.00) |
| L | 0 | politica FBA |
| R = 0.025×I | 21.2931 | acta 0.3 |
| Contribucion | 297.6710 (34.9492%) | hoja = `calcular_contribucion` |

Escenarios en prod siguen ausentes (seccion B de `select.sql`). AC14 no depende de eso: la conciliacion es independiente contra fuentes externas.

## SQL en vivo (universo)

`select.sql` / `conteos.json` (2026-09-08 14:42 UTC):

- 342 listings MX, 176 US.
- Universo FBA MX: 247 listings; 115 sin fila en `v_margen_producto`.

## Tests

```
PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_estimacion_aislamiento_fabrica.py
......
6 passed
```

## Pendiente fuera de B.3

- B.4: merge/PR lead.
- B.5: aplicar 0028/0029, ingerir FBA MX, rellenar seccion B de escenarios en prod.
