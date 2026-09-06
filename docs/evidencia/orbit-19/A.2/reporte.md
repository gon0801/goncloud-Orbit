# ORBIT 19 A.2 - Normalizacion comun CLI/API/motor

Fecha: 2026-09-06 UTC.

## Alcance realizado

- `POST /api/fabrica/plan` conserva v1 con `productos` y acepta v2 solo con
  `listing_ids` y un `objetivo` explicito. Una mezcla de formas se rechaza en
  la frontera HTTP antes de consultar o mutar.
- API y CLI convierten sus entradas en los mismos argumentos de
  `tools/fabrica_campanas._arma_plan`; el motor construye el plan v2 canonico.
  El CLI v2 exige `--listing-ids` y `--target-acos`; no transforma
  `--productos` en listings.
- El objetivo manual admite `NUMERIC(6,2)` positivo sin default. El objetivo
  medido sigue usando la fraccion vigente y rechaza un margen ausente, cero o
  negativo en vez de inventar rentabilidad.
- Las publicaciones v2 se validan por plataforma, identidad y `seller_sku`
  unico antes de cualquier POST. Margen `NULL` y varios listings del mismo
  producto llegan al preview manual y se conservan como `null` en su snapshot.
- El interruptor `fabrica.creacion` es fail-closed: ausente o invalido equivale
  a `v1` y bloquea solo altas v2. El preview y las rutas de recuperacion no
  dependen de ese interruptor.

## Regresion y pruebas focalizadas

La prueba CLI nueva se ejecuto inicialmente en rojo: el doble de conexion de
prueba no conocia el resultado SQL por publicacion y fallo con
`TypeError: unexpected keyword argument 'publicaciones'`. Tras extender el
doble para representar esa consulta, el mismo caso verifica una seleccion de
dos listings del mismo producto, uno sin margen y otro negativo, con objetivo
manual, sin red ni escrituras.

Resultados locales:

```text
tests/test_fabrica_campanas.py  45 passed, 22 skipped
tests/test_fabrica_plan.py      29 passed
tests/test_api_fabrica.py       37 skipped (ORBIT_TEST_DSN no disponible)
ruff check                      passed
ruff format --check             passed
```

Los tres casos de API agregados usan PostgreSQL real y cubren preview v2 con
NULL/multilisting, rechazo de mezcla y SKU duplicado sin escribir lote, y
bloqueo de alta v2 con el interruptor ausente. Se ejecutan en la bateria
completa unica del PR; no se duplican localmente.

## Limite deliberado

No se llamo a Amazon ni a `/crear`. A.3 implementa el registro y la
recuperacion v2; A.5 ensaya la reversa con lote parcial y solo hace smoke
productivo de GET/preview.
