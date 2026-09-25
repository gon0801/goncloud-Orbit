# H3 — clasificacion `fix/ads-report-timeout`: ABANDONAR (2026-09-25 UTC)

Comando del runbook: `git diff origin/master origin/fix/ads-report-timeout
--stat` + diff de `app/ads/reports.py`. Rama con 2 commits fuera de master
(`7f78345` poll 25 min + test, `e2cc267` evidencia gaps); base vieja
(pre-#329).

## Codigo y tests: ya estan en master

- `app/ads/reports.py` y `tests/test_reports_pipeline.py`: diff VACIO
  rama-vs-master (identicos).
- `INTENTOS_POLL = 300` entro a master por `43ddd93` ("tolerar reportes
  lentos"), mismo contenido que `7f78345` (ese commit no esta en master,
  su contenido si).
- Conclusion: nada que rescatar por PR; un PR seria vacio en codigo.

## Docs: la rama esta detras

- `docs/evidencia/ads/2026-09-24-exact-us.md`: master trae todo lo de la
  rama (huecos de decision) MAS la seccion "Recuperacion tras el deploy"
  (#329) y el puntero vigente a `plans/ads-proteccion-01.md`. La rama dice
  `plans/campanas-01.md` (viejo).
- `plans/campanas-01.md`: la rama trae la tabla 2.1-2.4 TODO que master ya
  reemplazo por el puntero al plan de proteccion. Contenido superado.

## Veredicto

ABANDONAR la rama: todo lo valioso ya vive en master por otra via. No se
abre PR. No bloquea el deploy A.4 (la ingesta que A.4 observa ya trae el
poll de 25 min en prod y master).
