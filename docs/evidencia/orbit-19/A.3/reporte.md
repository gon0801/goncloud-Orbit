# ORBIT 19 A.3 - Registro, idempotencia y recuperacion v1/v2

Fecha: 2026-09-06 UTC.

## Cambio

- Los pasos de creacion se derivan de la identidad del plan: v1 conserva un
  anuncio por producto y v2 crea uno por publicacion, en orden estable de
  `listing_id`.
- El ledger guarda el JSON del plan que corresponde a su version. El lector de
  `--registrar` discrimina `schema_version=2`; v1 sigue usando exactamente su
  lector anterior.
- El registro persiste para ambos formatos las cinco campanas, sus goals y las
  publicaciones. Para v2 conserva margen `NULL` o negativo, dos listings del
  mismo producto, y `target_origen=manual_lanzamiento` con derivado/fraccion
  nulos.
- La huella v2 canonica, el lock por lote y la consulta temprana del lote
  conservan idempotencia incluso si el reenvio ordena distinto los listings.
  El segundo envio devuelve el lote durable antes de planificar o mutar.
- `fabrica.creacion=v1` bloquea altas v2 desde CLI y API antes de HTTP. No
  participa en `--registrar`, reconciliacion ni pausa: un lote v2 ya existente
  sigue recuperable despues de deshabilitar nuevas altas.

## Regresiones y resultados

La regresion inicial de pasos v2 fallo contra el codigo anterior con
`AttributeError: PlanGrupoV2 ... productos`: el motor aun asumía el formato
v1. Tras el cambio, el caso crea dos product ads por los dos `seller_sku`.
La regresion de registro v2 fallo con el mismo supuesto al leer
`plan.target`; ahora persiste objetivo manual y dos publicaciones.

Resultados locales:

```text
tests/test_fabrica_plan.py tests/test_fabrica_campanas.py  78 passed, 22 skipped
tests/test_api_fabrica.py                                  38 skipped (sin ORBIT_TEST_DSN)
ruff check                                                  passed
ruff format --check                                         passed
```

El caso API que se ejecuta en CI crea un lote v2 simulado, reordena los
listings y lo reenvia: verifica una sola llamada al motor y el mismo resultado
durable. Los casos de PostgreSQL real de plan/API se reservan para la unica
bateria completa del PR.

No se enviaron POST a Amazon ni se crearon campañas reales.
