# ORBIT 19 / B.2 — Reporte de pruebas: economia observada

Fecha: 2026-09-06. Rama `feat/orbit19-fase-b`. Postgres local
(127.0.0.1:5432) disponible; los tests de DB corren de verdad (sin skip).

## Entregable

- `migrations/0021_economia_observada.sql` — vista `v_economia_producto`,
  grano (platform, product_id). Expansiva, NO re-runnable, sin tocar
  `v_margen_producto`. COMMENT ON con ADRs B.2-1/2/3.
- `app/economia_observada.py` — consumidor: `por_producto` / `por_listing`,
  dataclass `EconomiaProducto` con Decimal y None cuando falta el dato.
- `tests/test_economia_observada.py` — 1 estatico (pglast) + 5 contra
  Postgres real.

## Decision (ADR B.2-1, compartir el motor)

La proyeccion madura es SELECT directo de `v_margen_producto` (un numero,
una fuente; 0018 INTACTA). La muestra limitada necesita el mismo motor SIN
el guard de 30 dias y 0018 no puede alterarse ni parameterizarse, asi que
el bloque `muestra` de 0021 replica los CTE de 0018 verbatim salvo ese
guard. El riesgo de deriva queda pineado por los tests exactos de la
muestra (60 / -50 / NULL por integridad) y por el snapshot antes/despues.

## Pruebas (salida)

```
$ PYTHONPATH=. .venv/bin/python -m pytest -v tests/test_economia_observada.py
tests/test_economia_observada.py::test_0021_crea_solo_la_vista_y_no_toca_v_margen_producto PASSED
tests/test_economia_observada.py::test_v_margen_producto_conserva_valores_previos_tras_0021 PASSED
tests/test_economia_observada.py::test_muestra_limitada_solo_con_integridad_valida PASSED
tests/test_economia_observada.py::test_dos_listings_del_mismo_producto_comparten_grano_sin_duplicar PASSED
tests/test_economia_observada.py::test_producto_sin_ventas_es_none_no_cero PASSED
tests/test_economia_observada.py::test_margen_negativo_se_conserva PASSED
6 passed in 0.84s
```

```
$ ruff check app/economia_observada.py tests/test_economia_observada.py
All checks passed!
```

## Cobertura de los DoD de B.2

| DoD | Prueba | Resultado |
|---|---|---|
| v_margen_producto conserva valores previos | snapshot antes (hasta 0019) vs despues de 0021, filas identicas (5 productos) | PASSED |
| Muestra limitada solo con integridad valida | 5 dias limpios -> muestra 500 / 60%; fee sin tipo -> integridad_ok false y muestra NULL; 70 dias -> muestra NULL y maduro 50% | PASSED |
| Dos ASIN comparten grano sin duplicar | dos listings del mismo producto: muestra_venta 100 cada uno; suma por PRODUCTO = 100 (no 200) | PASSED |
| Sin ventas -> None, no 0 | listing sin fila: venta_total/margen/dias/moneda None; sin fila en la vista | PASSED |
| Margen negativo se conserva | maduro -20%; muestra 5 dias -50% | PASSED |
| 0021 no muta estructura ajena | estatico pglast: solo CREATE VIEW + COMMENT + GRANT SELECT; lee v_margen_producto sin redefinirla | PASSED |

## Notas / pendientes para el lead

- `muestra_venta`/`muestra_margen_neto_pct` solo se publican con
  `muestra_limitada` (integridad OK y 1-29 dias), regla 3: NULL jamas 0.
- D4 respetado: nada de esto entra al sort D1 ni a targets; la vista es
  de solo lectura para la UI.
- El archivo 0020 lo escribe otro agente en paralelo; 0021 no depende de
  el (ORDEN propio termina en 0019 + 0021). Si 0020 queda ANTES en el
  orden de aplicacion, no hay conflicto (sin objetos compartidos).
