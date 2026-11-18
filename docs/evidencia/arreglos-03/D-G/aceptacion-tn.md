# D-G.4 — TN05 con el arnés TN (owner único: D-G)

Contrato: `docs/evidencia/arreglos-03/DISENO.md`, sección D-G, y `docs/evidencia/arreglos-05/ACEPTACION.md`.
05 solo consume esta evidencia.

## Qué recorre el caso

`docs/evidencia/arreglos-05/revision/ingest.py`, `caso05` (adaptado por D-E.7 a `ultimo_cerrado=`; ver
`docs/evidencia/arreglos-03/D-E/aceptacion-tn.md`): diez fechas del término `collar` con una fila válida (130 de gasto,
100 clics, 0 pedidos) y otra con `cost` ilegible y 2 pedidos; pasa por `reports.escribir_grupo` → PostgreSQL →
`windows.terminos_cortes`. La variante vecina corre `_planea_filas_terminos` con filas parciales tipadas en los dos órdenes
(`[a, b, buena]` y `[buena, a, b]`). `test_control` es el control de productos en los dos órdenes.

D-G no tocó el arnés: ni `ingest.py` ni `test_accept_ingest.py` ni sus aserciones (`git diff f19942f -- docs/evidencia/arreglos-05/`
sale vacío).

## Entorno

PG16 desechable `127.0.0.1:55449` (contenedor `orbit-review-current-pg`, base `thermo_ingest_current` que el fixture crea y
borra), bajo el candado `C:/Users/ehven/.ao/data/locks/orbit-tn` (soltado al terminar). Variables de ENCARGO-COMUN:
`TN=postgresql://orbit@127.0.0.1:55449/postgres` en los cinco DSN, `ORBIT_REVIEW_REPO/ROOT`, `ORBIT_ACCEPT_EVIDENCE`,
`PYTHONPATH`, `PYTHONDONTWRITEBYTECODE=1` y `PYTHONUTF8=1`. Sin red externa (HTTP simulado).

Comando (el `Acepta` de D-G.4):

```
CI=1 uv run --frozen --python 3.12 python -B -m pytest -p no:cacheprovider -q -rs docs/evidencia/arreglos-05/revision/test_accept_ingest.py -k "test_05 or test_control"
```

## Rojo

1. Registrado por D-E (`docs/evidencia/arreglos-03/D-E/aceptacion-tn.md`), sobre `36473e9` y `5c60c97`, y antes en `a6ffa32`.
2. Repetido aquí con el `app/ads/reports.py` de master sin D-G y el resto del árbol de la cabeza medida:
   - 2026-11-18 12:28 UTC, reports.py de `1f15c85`;
   - 2026-11-18 12:34 UTC, reports.py de `f19942f` (origin/master al abrir el PR; idéntico al de `1f15c85`).

   `-k "test_05"` → `1 failed`, salida 1, por el subtotal (no por firma, conexión ni fixture; las 86 migraciones se aplicaron):

```
E       AssertionError: {'consumidor_windows': [('collar', '910.0000', 0, '0.0000')], 'entidad_completa': True, 'planes_por_orden': [1, 0], 'sello': (True, 10, 10)}
E       assert (not [('collar', '910.0000', 0, '0.0000')])
docs\evidencia\arreglos-05\revision\test_accept_ingest.py:23: AssertionError
```

   El término `collar` llega al consumidor con 910.0000 de gasto y 0 pedidos como entidad completa, y el orden `[a, b, buena]`
   todavía publica un plan.

## Verde

Cabeza `9052b99182de174e55fc8154d80244d876a76bfb` (arreglo en `7ae5673`, rebasado sobre `f19942f`), 2026-11-18 12:34 UTC:

| Selección | Resultado | Salida |
| --- | --- | --- |
| `-k "test_05 or test_control"` (Acepta) | `2 passed, 4 deselected` | 0 |
| `-k "test_09"` (control vecino de D-E, no es Acepta) | `1 passed, 5 deselected` | 0 |

Antes, sobre el mismo código sin rebasar (`1f15c85` + D-G), 2026-11-18 12:28 UTC: mismos resultados.

- `test_05_fuente_corrupta_no_publica_subtotal_ni_recrea_clave`: `consumidor_windows` vacío (`windows.terminos_cortes` no ve el
  subtotal) y `planes_por_orden == [0, 0]` (en ningún orden se recrea la clave invalidada).
- `test_control_productos_no_publica_subtotal_en_ningun_orden`: verde; productos conserva su regla tras generalizar
  `_envenenar_clave`.
- Cero skips, xfail o errores de fixture.

## Vecinos (cubiertos por las pruebas de `tests/test_reports_pipeline.py`, ver `rojo-verde.md`)

Órdenes distintos, clave vaciada y no recreada, clics/gasto con pedidos ausentes, fila sin ninguna métrica, negativos que siguen
abortando y el control de productos (`tests/test_ads_producto_ingesta.py`, en el `Acepta` de D-G.2).
