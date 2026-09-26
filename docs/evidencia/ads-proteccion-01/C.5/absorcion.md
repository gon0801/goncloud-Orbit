# C.5 — absorcion opcion b (el nuestro absorbe al otro)

Fecha: 2026-09-25/26 UTC. Decision del dueno: "que el nuestro absorba al otro".
Rama nuestra: `feat/ads-proteccion-c5` @ `a256cff`. Rama absorbida:
`origin/fix/superficie-descartar-c5` (commit unico `a1d7fd2`,
autor gon, 2026-09-25 12:40 PDT). PR de absorcion: #351.

## Hallazgo previo (verificado, no supuesto)

- PR #344 ya estaba MERGED (`ad79eeb`, mergedAt 2026-09-25T17:34:06Z);
  lo nuestro (`ff6ba43`) ya era ancestro de `origin/master`.
- Tras #344, la completa de master quedo roja en
  `test_superficie_ads_optimizer_sellada_...` (run 36167946686):
  el endpoint C.5 `POST .../descartar` entro sin sellar la lista.
- Dos sellados nacieron en paralelo con la MISMA tupla
  `("/api/ads-optimizer/propuestas-campana/{proposal_id}/descartar", "post")`:
  `a1d7fd2` (otra rama) y `3343ae3` (PR #347, ya en master).
  Solo difieren en el comentario.

## Que se absorbio

Unico contenido valioso del otro: `a1d7fd2` (6+/2- en `tests/test_api.py`).
Merge `a256cff` (padres `ee50332` = origin/master + `a1d7fd2`):
conflicto en un solo hunk (comentario), resuelto unificando ambas
redacciones — conserva `exige_token` + despachador
`app/propuestas_campana.descartar_propuesta` + "sin tocar Amazon ni
apply_queue". Diff efectivo `a256cff` vs `origin/master`: 1 archivo,
4+/3-, solo-comentario, cero cambio ejecutable (veredicto opus,
`review-c51-abs-opus.md`: arbol 878/878 identico).

## Bateria completa local (UNA sola corrida, summa-gate regla 1)

Comando verbatim:

```
PYTHONPATH=. .venv/bin/python -m pytest -q > /tmp/c5-bateria.log 2>&1
.venv/bin/python -m ruff check . >> /tmp/c5-bateria.log 2>&1
```

Salida verbatim (cola de `/tmp/c5-bateria.log`):

```
3421 passed, 13 skipped, 1 warning in 214.12s (0:03:34)
PYTEST_EXIT=0
All checks passed!
RUFF_EXIT=0
```

SHA validado: `a256cff`. (La tarea hablaba de 278 tests; la suite real
trae 3421: todo verde.)

## Revisiones

- CI PR #351: `gate` pass, `rapido` pass, `review` (AI del repo) pass,
  `CodeRabbit` pass (nota: rate limited, sin comentarios).
- Cross-review opus independiente: APROBADO sin bloqueantes
  (`.saikit/scratch/ads-proteccion-1/review-c51-abs-opus.md`):
  `pytest tests/test_api.py -q -k superficie` -> `1 passed`, exit 0.

## Sombra / prod intactos (solo lectura, ssh gonserver)

```
mode  | count  ->  shadow | 9            (0 live: vivo apagado)
applied_nuevos desde 2026-09-25T06:13:41Z -> 0
config_version max id -> 21 (flag B.2a on; ads_pause_economica ausente: 0 filas)
apply_queue no terminales -> 0 filas
ads_campaign_proposal open -> 0
```

La absorcion no toco codigo de prod (solo comentario en test) ni la DB:
deploy C.5 (0041+0042+0043, codigo `ad79eeb`) sin cambios.

## Cierre

- Merge PR #351: <PENDIENTE / SHA>.
- Rama absorbida `fix/superficie-descartar-c5`: borrar tras el merge
  (`git push origin --delete fix/superficie-descartar-c5`).
