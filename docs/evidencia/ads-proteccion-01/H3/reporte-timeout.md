# H3 — clasificacion `fix/ads-report-timeout`: ABANDONAR (2026-09-25 UTC)

Comando del runbook: `git diff origin/master origin/fix/ads-report-timeout
--stat` + diff de `app/ads/reports.py`. Rama con 2 commits fuera de master
(`7f78345` poll 25 min + test, `e2cc267` evidencia gaps); base vieja
(pre-#329).

Salidas verbatim (exit 0 en ambos):

```
$ git diff origin/master origin/fix/ads-report-timeout --stat
 app/cycle.py                                       |  19 +-
 app/optimizer/goals.py                             |  13 +-
 docs/CHAT-CONTEXT.md                               |  17 -
 docs/CONTEXTO.md                                   |  15 -
 docs/evidencia/ads/2026-09-24-exact-us.md          |  27 +-
 docs/runbooks/ads-proteccion-01.md                 | 367 ---------------------
 .../specs/2026-09-24-ads-proteccion-design.md      | 131 --------
 docs/traspaso/ADS_OPTIMIZER_V2_DESIGN.md           |   5 -
 plans/ads-proteccion-01.md                         | 155 ---------
 plans/campanas-01.md                               |  12 +-
 plans/manifest.json                                |   5 -
 tests/test_cycle_pause_cooldown.py                 | 156 ---------
 tests/test_optimizer_goals.py                      |  44 ---
 13 files changed, 16 insertions(+), 950 deletions(-)

$ git diff origin/master origin/fix/ads-report-timeout -- app/ads/reports.py
(salida vacia)
```

## Codigo y tests: el poll ya esta en master, y la rama revierte B.2

- `app/ads/reports.py` y `tests/test_reports_pipeline.py`: diff VACIO
  rama-vs-master (identicos).
- `INTENTOS_POLL = 300` entro a master por `43ddd93` ("tolerar reportes
  lentos"), mismo contenido que `7f78345` (ese commit no esta en master,
  su contenido si).
- Vacio es SOLO el diff de `app/ads/reports.py`: un PR de esta rama NO
  seria vacio en codigo. La rama trae `app/cycle.py` y
  `app/optimizer/goals.py` en estado pre-B.2 (sin `comprobar_cooldown`,
  sin `kind` en `en_cooldown`, sin prioridad PAUSE) y no tiene
  `tests/test_cycle_pause_cooldown.py` (-156): mergearla seria una
  REGRESION que revierte B.2. Nada que rescatar; no se abre PR.

## Docs: la rama esta detras

- `docs/evidencia/ads/2026-09-24-exact-us.md`: master trae todo lo de la
  rama (huecos de decision) MAS la seccion "Recuperacion tras el deploy"
  (#329) y el puntero vigente a `plans/ads-proteccion-01.md`. La rama dice
  `plans/campanas-01.md` (viejo).
- `plans/campanas-01.md`: la rama trae la tabla 2.1-2.4 TODO que master ya
  reemplazo por el puntero al plan de proteccion. Contenido superado.

## Veredicto

ABANDONAR la rama: todo lo valioso ya vive en master por otra via. No se
abre PR. No bloquea el deploy A.4: la ingesta que A.4 observa corre sobre
master, donde `INTENTOS_POLL = 300` ya esta (verificado aqui); que prod lo
trae viene del registro de deploy #329 en
`docs/evidencia/ads/2026-09-24-exact-us.md` (respaldo
`predeploy-pr329-20260924-070227`, checklist con `INTENTOS_POLL=300`), no de
medicion propia en este H3.
