# ORBIT 19 / B.4 — Reporte de pruebas: evaluacion, ratios y orden estable

Fecha: 2026-09-06. Rama `feat/orbit19-fase-b`. Postgres local disponible; el
test de integracion corre de verdad (sin skip).

## Entregable

- `app/evaluacion_catalogo.py` — modulo PURO (sin psycopg/httpx en runtime;
  `EconomiaProducto` solo bajo `TYPE_CHECKING`): etiquetas Ads con la
  precedencia cerrada de 0.4 §4, ratios desde sumas, madurez D+30,
  `objetivo_comparacion` (D2: sin promedio), `motivos_margen` (D2/AC3),
  `evaluar_listing` y `ordenar` (§7 D1: NULL al final en ambas direcciones,
  desempate `listing_id`, MX/US fail-loud).
- `app/fabrica_web.py` — adaptador DB `evaluacion(conn, plataforma, orden,
  direccion)`: ventana Ads igual al cron (D-31..D-1 UTC), filas sumadas por
  (asin, sku) sin repartir campanas, objetivo desde grupos en preparacion
  (lote `planeado`, target de 0019), economia por `economia_observada`
  (B.2) y disponibilidad por `estado_disponibilidad` (B.3).
- `app/api_fabrica.py` — endpoint `GET /api/fabrica/evaluacion?plataforma=`
  con `orden` (8 metricas de §7) y `direccion` (asc/desc). Se extiende la
  API de fabrica de Fase A porque el catalogo ya vive ahi (`/catalogo`,
  A.2) y B.5 consumira el mismo router.
- `tests/test_evaluacion_catalogo.py` — 28 tests: todos los fixtures de
  0.4 §8 + integracion del endpoint + candado de pureza.

## Decisiones

1. **Por probar no existe**: el modulo ni define la constante (test
   `test_por_probar_no_existe_como_etiqueta`); ASIN ausente / reporte
   faltante / historia fuera de ventana => `sin_datos` siempre.
2. **Ausencia vs cero**: sumas ignoran NULL; un solo NULL no es 0. Con
   gasto>0 y sales30d ausente (no cero) NO se asigna Gasto sin ventas
   (regla 5); con cero observado si (regla 3).
3. **Madurez**: maduro(D) sii max(observed_at) de la clave >= D+30 dias
   (00:00 UTC, inclusive). Re-lectura append-only de la misma fecha no
   duplica `muestra`.
4. **Objetivo por listing**: union de targets de grupos `planeado`;
   valores distintos => None (nunca promedio). Sin grupo => ACoS sin
   etiqueta (D2).
5. **Costo de Ads ausente con ventas>0**: ACoS None, sin etiqueta (regla 5:
   la ausencia no es 0 y no inventa ACoS 0%).

## Pruebas (salida)

```
$ PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_evaluacion_catalogo.py
28 passed in 0.35s

$ .venv/bin/ruff check app/ tests/test_evaluacion_catalogo.py
All checks passed!

$ PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_evaluacion_catalogo.py \
    tests/test_api_fabrica.py tests/test_economia_observada.py tests/test_disponibilidad.py
92 passed
```

Fixtures de 0.4 §8 cubiertos (test arriba en `tests/test_evaluacion_catalogo.py`):
ratios desde sumas (25% no 20%), igualdad= Dentro, distintos objetivos sin
target promedio, cero/ausente/reporte faltante, margen 0/negativo/8% vs 25%
seleccionable con motivo, muestra 1 vs 100, dos listings mismo producto sin
duplicar financiero, historia fuera de ventana, madurez D+1 vs >=D+30,
orden estable nulls-ultimo-desempate, promovida ausente sin restar, halo
solo si ambas, disponibilidad desconocida sin bloquear, ninguna etiqueta
bloquea seleccion.
