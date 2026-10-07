# S.4 — comandos, salidas y SHA (2026-10-06)

Base: `85e25a8a228fa569f88c8c531d49eea02edcd818` (rama `jev02/s4-job`,
worktree `~/dev/wt/jev02-s4`). DSN de pruebas: localhost:5432
(redactado `***`).

## TDD rojo (13 pruebas primero)

```
pytest tests/test_jev_senales.py tests/test_jev_senales_cron.py \
  tests/test_cli.py::test_cli_jev_senales_despacha_con_sus_args \
  tests/test_optimizer_windows.py::test_sql_del_modulo_parsea_como_postgres \
  tests/test_optimizer_windows.py::test_subquery_compartido_deja_terminos_cortes_igual \
  tests/test_optimizer_windows.py::test_subquery_compartido_da_las_mismas_filas \
  tests/test_architecture.py::test_jev_solo_importa_lo_declarado \
  tests/test_architecture.py::test_candado_imports_jev_caza_fuga_sembrada -q
13 failed, 4 passed
```

Salida completa en `rojo.txt`. Los 4 verdes pre-cambio: las 2 de
comportamiento del subquery (iguales por construcción antes del
refactor), la guarda (muerde al despachar el CLI: verificado rojo
`assert [...] == []` y verde tras allowlist con razón) y la fuga
sembrada. Causas del rojo: `ModuleNotFoundError: app.jev_senales`
(10), constante ausente en la lista de parseo (1), línea cron ausente
en DEPLOY.md (2).

## Commit 1: parser aparte (sin cambio de comportamiento)

```
python -m app.cli --help + goals set --help : diff vacío (HELP-IGUAL)
pytest tests/test_cli.py -q --deselect ...jev_senales... : 56 passed
9522b82 Jev Ads S.4: saca el parser de cli.main (sin cambio de comportamiento)
```

## Verde por cambio

- Cambio 1 (windows): `pytest tests/test_optimizer_windows.py` →
  21 passed; `windows.py` 873 líneas.
- Cambios 2–3 (libro + job): `pytest tests/test_jev_senales.py` →
  9 passed (un bache: `SET TRANSACTION` tras lectura previa en lector
  no-autocommit; ajustes entró al snapshot).
- Cambios 4–8 + 11: suite S.4 (5 archivos) → 185 passed.
- Comprueba + vecinos (13 archivos, guía §S.4): 590 passed.
- Probe poblado `/tmp/probe_s4.py`: job + `dash.salud` + plantilla
  `/salud` con tarjeta jev → PROBE OK.
- Batería completa: `pytest -q` → 3987 passed (224s).
- `ruff check` + `ruff format --check` limpios en los 9 archivos
  tocados; `bash -n` en los 3 scripts de despliegue.

## Mutantes

Tabla en `mutantes.md`: 13/13 rojos con el diff citado, revertidos,
`grep MUTANTE` limpio en archivos de S.4.

## Panel

`panel.md` + `panel-glm-{5.3,5.2,4.7}.txt`: 0 bloqueantes; 5 hallazgos
aplicados, 8 desestimados con razón, 2 filas para el plan.

## SHA final

Commit 2 (job + pruebas + evidencia):
`4a1f4a0b4624cffe33a55e3ca9a42bb458c86159`
(esta línea se rellenó sin commit; la lleva el PR).
