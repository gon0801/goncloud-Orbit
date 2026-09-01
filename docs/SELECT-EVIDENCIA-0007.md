# Evidencia 0007 — equivalencia 0006↔0007 y timing en prod (2026-09-01)

Verificacion corrida en goncloud ANTES de aplicar la migracion: la definicion
0007 como TEMP VIEW (pg_temp gana el search_path, asi la cobertura TEMP
referencia la entidad TEMP — la cadena 0007 completa) contra la vista publica
0006, con `statement_timeout = 900000`, rol `orbit_read` (solo lectura).

## Diagnostico (por que 0006 tardaba ~100s por consulta)

EXPLAIN (ANALYZE, BUFFERS) de `SELECT count(*) FROM v_contribucion_entidad
WHERE platform='amazon_mx'` en prod: 190s totales.

1. `fx_resolve` en `LEFT JOIN LATERAL` por fila (entidad-dia x producto vivo):
   `loops=1149120` — 1.1M llamadas (~20s) para ~90 fechas distintas.
2. `ratio_dia`: el planner estima los CTE en `rows=1` y elige Nested Loop —
   el CTE `pesos` (2,678 filas) se escaneo **528,199 veces** (144s de 190s).
3. `sku_cost` as-of como sonda de rango por fila del mismo millon (~3.5s).

## Resultado de la verificacion

```
v_contribucion_entidad amazon_mx: 0007=108 (2.5s)   0006=108 (101.6s)
v_contribucion_entidad amazon_us: 0007=0   (1.1s)   0006=0   (44.1s)
v_contribucion_entidad:   dif simetrica = 0 filas (EXCEPT en ambos sentidos)
v_contribucion_cobertura amazon_mx: 0007=96  (2.4s) 0006=96  (102.0s)
v_contribucion_cobertura amazon_us: 0007=288 (2.5s) 0006=288 (102.5s)
v_contribucion_cobertura: dif simetrica = 0 filas (EXCEPT en ambos sentidos)
```

Dif simetrica CERO filas en ambas vistas (EXCEPT sobre la vista completa, en
ambos sentidos). Timing: 40-80x mas rapido; la lectura del digest de Telegram
(statement_timeout 10s en `app/notifica.py`) vuelve a caber con holgura.

## Que cambia y que NO cambia

0007 solo rearma la cadena de CTEs: `fx_dia` (fx por dia), `costo_producto_dia`
(sku_cost as-of por producto-dia), `vivos_pesos` (w_i pre-unido al vivo) y
agregacion a grano `(ad_group, dia, moneda)` con expansion a entidad al final
via `dias_entidad`. Interfaz (columnas, orden) identica — candado estatico
`test_0007_misma_interfaz_que_0006`. La suite de integracion
`tests/test_contribucion_entidad.py` (semantica sellada: cobertura 100%,
dedup por producto, par halo completo, serie incompleta ausente, FX US)
corre contra la definicion 0007 en CI.
