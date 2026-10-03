# E/0.1 — prueba focalizada del snippet de repricing en HEAD

Fecha UTC: 2026-10-03. Rama `docs/jev-ads-b1`, HEAD
`fa2039ee94ddc0742f1f3895f267fccbc59c8aa9` (origin/master vigente, squash del
PR #389). Fila 0.1 del plan `jev-ads-01`.

## Desviación declarada

0.1 ya está resuelta en master por la **PR #379** (`fix/manifest-repricing-01`,
merge `8d4890a`): la discrepancia entre el snippet de `plans/repricing-01.md` y
`plans/manifest.json` quedó reparada y la prueba focalizada pasa desde el
2026-10-02. Por encargo B1-r1, este bloque **no recodifica** nada: no toca
`plans/repricing-01.md`, ni `plans/manifest.json`, ni tests. Solo registra la
corrida que pasa sobre el HEAD vigente. El "falla antes del arreglo" del DoD
corresponde al cambio de #379 y ya se demostró en ese PR; aquí se documenta que
el fallo quedó resuelto allí.

## Corrida en el HEAD vigente

Comando y salida completos en `corrida.txt`. Resumen:

```
Comando: uv run --frozen python -m pytest -q tests/test_precio_d0.py::test_snippet_del_plan_coincide_con_el_manifest
SHA HEAD: fa2039ee94ddc0742f1f3895f267fccbc59c8aa9
Salida:
.                                                                        [100%]
1 passed in 0.17s
```

## Estado real conservado

La prueba compara el snippet de `plans/repricing-01.md` contra
`plans/manifest.json` y pasa sin cambios en este bloque. El estado real de
repricing que documenta el plan queda intacto: ningún archivo de plans ni de
tests fue editado en `docs/jev-ads-b1`.

Crudos adicionales en este directorio, producidos por un agente paralelo al
mismo encargo (ver §7 del NOTAS de 0.2 para la anomalía de dos actores):
`fallo-pre-379.txt` demuestra el "falla antes" reproduciendo el fallo en un
clon desechable en el estado previo a #379, y `pasa-en-head.txt` registra el
paso en HEAD con los datos de la PR #379. Ambos son coherentes con esta
corrida.
