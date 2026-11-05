# Correccion del chequeo de ads-salud

Fecha: 2026-11-04. Base: `8c61f95`.

Causa: el checker contaba el job productivo (`total=1`) pero solo aceptaba
la linea sin wrapper (`exacta=0`). El crontab vigente usa `cron-avisa`, con
la misma cadencia, contenedor, modulo, comando y salida. La evidencia anterior
esta en `docs/evidencia/estado-planes-2026-11-04/cron-vigente.txt` y
`cron-salud.txt`.

Cambio acotado: aceptar las dos lineas completas autorizadas: raw documentada
y wrapper productivo con nombre, `--cada 24 --tras 3` y `/bin/bash -c` exactos.
No se interpreta ni ejecuta contenido del crontab. Se conservan los conteos y
la propagacion de errores reales de `crontab`.

## Rojo y verde

Antes del cambio del script, con la regresion nueva ya escrita:

```console
$ uv run --frozen python -m pytest -q tests/test_ads_salud_cron.py -k wrapper
FAILED test_checker_acepta_wrapper_productivo_sin_relajar_el_contrato
AssertionError: ads-salud cron invalido: total=1 exacta=0
1 failed, 7 deselected
```

Despues del cambio:

```console
$ bash -n tools/check_ads_salud_cron.sh
$ uv run --frozen python -m pytest -q tests/test_ads_salud_cron.py
8 passed
$ uv run --frozen ruff check tests/test_ads_salud_cron.py
All checks passed!
$ uv run --frozen ruff format --check tests/test_ads_salud_cron.py
1 file already formatted
```

La regresion comprueba wrapper aceptado, horario/contenedor/opciones incorrectos
rechazados, texto adicional de shell no ejecutado y duplicado raw+wrapper
rechazado. Las pruebas previas siguen comprobando raw, ausencia, duplicado,
horario incorrecto y error operativo propagado. Se retiro la prueba del texto
interno del script: el contrato raw ya se comprueba ejecutandolo.

## Verificacion en servidor (solo lectura)

```console
$ ssh goncloud 'bash -s' < tools/check_ads_salud_cron.sh
ads-salud cron ok: una linea exacta en crontab de gon
```

Salida 0. No se instalo el script ni se modifico cron o datos productivos.

## Revision propia y limites

El cambio solo amplia la allowlist por igualdad completa; no usa `eval`,
`source` ni tokenizacion de comandos externos. Un duplicado de cualquiera de
las formas incrementa total/exacta y falla. Un fallo de permisos sigue saliendo
con su codigo real. Cualquier otra envoltura o cambio de espacios se rechaza;
una nueva forma operativa requerira actualizar explicitamente el contrato.
Este chequeo valida la configuracion de cron, no acredita ejecuciones del job
ni salud de la ingesta.
