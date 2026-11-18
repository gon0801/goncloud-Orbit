# D-G.1 y D-G.2 — rojo, verde y poder de discriminación

Contrato: `docs/evidencia/arreglos-03/DISENO.md`, sección D-G, que refuerza «ST» de `DISENO-datos.md`.
SHA previo: `f19942f` (origin/master al abrir el PR: D-E, P-B y D-N integrados; `app/ads/reports.py` y las pruebas tocadas son idénticos a los de `1f15c85`, donde se midió primero). Arreglo: `7ae5673`; cabeza medida: `9052b99`.

## Lo que cambia en `app/ads/reports.py`

- `_envenenar_clave_producto(asin, sku, metric_date, ...)` pasa a `_envenenar_clave(clave, *, por_clave, plan, envenenadas, skips)`:
  la clave es cualquier tupla. Productos la llama con `(asin, sku, metric_date)` y su comportamiento no cambia (mismos motivos de
  skip; `_fusionar_fila_producto` se queda igual).
- `_planea_filas_terminos` lleva su propio `envenenadas`, que dura todo el reporte. La clave `(external_id, search_term, metric_date)`
  se resuelve antes de validar métricas. Invalidan la clave, además de contar su motivo:
  - una métrica ilegible (no numérica o fraccionaria): motivo de siempre;
  - una fila sin ninguna métrica: motivo de siempre;
  - una fila con clics o gasto y `purchases7d` ausente o nulo: motivo nuevo «fila con pedidos vacíos»;
  - una suma que queda vacía: ya no se borra la clave sin más; se invalida (la previa retirada cuenta como
    «fila de clave envenenada (subtotal parcial)»).
  Una fila válida de una clave invalidada se salta con «fila de clave envenenada (subtotal parcial)».
- Los negativos siguen abortando: el chequeo de «pedidos vacíos» y el salto por clave invalidada van después del chequeo de
  negativos (igual que en productos). Por eso una fila con un negativo aborta aunque su clave ya esté invalidada o le falten
  pedidos. El seudocódigo de «ST» pone el salto por clave invalidada primero; ponerlo ahí haría que un negativo se descartara en
  silencio, en contra de «los negativos siguen abortando» del plan (D-G.2). El mutante «chequeo de veneno antes de negativos»
  (abajo) lo muestra.
- No se convierte un dato faltante en pedidos cero: la clave sale del reporte ese día.

## Pruebas (dinero) en `tests/test_reports_pipeline.py`

Nuevas, de D-G.1:
- `test_arreglos03_dg_fila_ilegible_invalida_la_clave`: reproducción `test_terminos_fila_invalida_no_envenena_la_clave` con
  `ultimo_cerrado=`, en los dos órdenes, con otra clave del mismo reporte que sí se guarda; más la variante «fila sin ninguna
  métrica» junto a una hermana con 0 pedidos.
- `test_arreglos03_dg_clave_vaciada_no_se_recrea`: reproducción `test_terminos_par_envenenado_se_recrea_con_la_tercera_fila` con
  `ultimo_cerrado=`; más la variante solo-pedidos + solo-ventas + fila completa, en los dos órdenes, que llega a la suma vaciada
  sin pasar por la regla de pedidos vacíos.
- `test_arreglos03_dg_clics_sin_pedidos_invalida_la_clave`: `clicks=12, cost=9.5, purchases7d` vacío (nulo, y en el otro orden
  ausente) más `clicks=30, cost=21.0, purchases7d=0, sales7d=0.0` dan un plan vacío.

Pruebas existentes que codificaban el comportamiento que D-G invierte (se ajustan, no se quitan):
- `test_plan_terminos_clasifica_y_gates` y `test_pipeline_search_terms_en_vivo`: sus filas válidas con clics o gasto y sin
  `purchases7d` pasan a traer `purchases7d: 0` (siguen probando clasificación, tipos, entidad ausente y dedupe; con la regla nueva
  esas filas se descartarían).
- `test_plan_terminos_par_envenenado_no_llega_al_insert`: la tercera fila ya no recrea la clave; plan vacío.
- `test_plan_terminos_primera_ocurrencia_invalida_no_contamina_la_clave` → `..._envenena_la_clave`: la primera fila
  fraccionaria ahora invalida la clave; la clave de tres filas válidas (con pedidos) se sigue fusionando.
- `test_plan_terminos_metrica_negativa_aborta_fail_closed`: suma dos variantes (fila ilegible antes del negativo; negativo sin
  pedidos): las dos siguen abortando.

## Rojo en el SHA previo

`app/ads/reports.py` de `f19942f` con las pruebas finales de `9052b99` (2026-11-18 12:33 UTC; el mismo rojo salió antes contra `1f15c85`):

```
PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_reports_pipeline.py -k arreglos03_dg_
E           AssertionError: subtotal publicado: [('arras boda', 30, 0), ('arras plata', 3, 1)]
tests\test_reports_pipeline.py:949: AssertionError
E       AssertionError: clave envenenada recreada con un subtotal: [('arras boda', 30, 0)]
tests\test_reports_pipeline.py:973: AssertionError
E           AssertionError: subtotal sin pedidos publicado: [('arras boda', 42, None)]
tests\test_reports_pipeline.py:994: AssertionError
3 failed, 22 deselected
```

Es el fallo de comportamiento (un término con 30 clics y 0 pedidos guardado como completo; la suma con pedidos vacíos guardada
con 42 clics), no de firma.

## Verde

Con D-G: `-k arreglos03_dg_` → `3 passed`; Acepta de D-G.2 (`tests/test_reports_pipeline.py tests/test_ads_producto_ingesta.py`)
→ `52 passed`, sin skips (con `ORBIT_TEST_DSN` del PG desechable 127.0.0.1:55433). Ver `acepta.txt`.

## Poder de discriminación (mutantes)

Se quitó una parte del arreglo a la vez y se corrió `-k "arreglos03_dg_ or plan_terminos"`; cada mutante da rojo:

| Mutante | Resultado | Pruebas que fallan |
| --- | --- | --- |
| la fila ilegible no invalida | 2 failed | `..._primera_ocurrencia_invalida_envenena_la_clave`, `test_arreglos03_dg_fila_ilegible_invalida_la_clave` |
| la fila sin métricas no invalida | 1 failed | `test_arreglos03_dg_fila_ilegible_invalida_la_clave` |
| sin la regla de pedidos vacíos | 2 failed | `..._par_envenenado_no_llega_al_insert`, `test_arreglos03_dg_clics_sin_pedidos_invalida_la_clave` |
| sin el salto por clave invalidada | 5 failed | las tres `test_arreglos03_dg_*` y dos de `plan_terminos` |
| salto por clave invalidada antes de los negativos | 2 failed | `..._metrica_negativa_aborta_fail_closed`, `..._par_envenenado_no_llega_al_insert` |
| la suma vaciada solo borra, sin invalidar | 1 failed | `test_arreglos03_dg_clave_vaciada_no_se_recrea` |

El script de mutantes se corrió fuera del repo y restauró `reports.py` al terminar.
