# D-E: rojo antes del cambio y verde después

## D-E.1 — `test_arreglos03_de_diario_no_pide_el_dia_en_curso_de_us_en_invierno`

Reproducción `test_diario_pide_el_dia_en_curso_de_us_en_invierno` de
`docs/evidencia/arreglos-2026-11-12/pruebas/ingesta/test_rev_ingesta.py`, con su
`_ConnMuda` y su `descarga` falsa, copiada a `tests/test_ingest_diario.py` sin cambios
de comportamiento (solo el nombre que pide el plan; `ruff format` reacomodó dos líneas).

Rojo, en el SHA previo al arreglo `73a033e918b383753026c1dd61dceecadf800aff`
(origin/master al empezar, con D-A integrado), con solo la prueba agregada:

```
PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_ingest_diario.py -k arreglos03_de_
E       AssertionError: pide hasta 2026-11-11, pero en US son las 23:10 del 2026-11-11: ese dia sigue en curso (ultimo cerrado: 2026-11-10)
tests\test_ingest_diario.py:132: AssertionError
FAILED tests/test_ingest_diario.py::test_arreglos03_de_diario_no_pide_el_dia_en_curso_de_us_en_invierno
1 failed, 16 deselected
```

Verde con el arreglo: ver `acepta.txt` (fila D-E.1).

Discrimina: el rojo de arriba es exactamente el código sin `app/dia_cerrado.py` ni el
rango nuevo de `app/ads/diario.py`; con el rango viejo (`hoy - 1`) la prueba vuelve a
fallar con el mismo mensaje.

## Pruebas existentes que dependían de la hora (D-E.4 y vecinos de D-E.3)

Con la regla nueva, «hoy UTC - 1» todavía no cierra en US entre las 00:00 y las 08:00 UTC
de invierno (07:00 en verano) ni en MX entre las 00:00 y las 06:00 UTC. Las pruebas que
sembraban ese día con el reloj real habrían fallado a esas horas, y a cualquier otra hora
no probaban la regla nueva:

| Prueba | Antes | Ahora |
| --- | --- | --- |
| `tests/test_metricas_listas.py` (`HOY`, `AYER`) | `HOY = now()` UTC, `AYER = HOY - 1`, `started_at=now()` | `AHORA` fijo (2026-11-12 07:30 UTC), fixture autouse fija `metricas_listas._ahora`, la corrida se siembra con `started_at=AHORA` y cada plataforma con `ultimo_dia_cerrado(p, AHORA)`; la prueba con Postgres real afirma además que una corrida de US con «hoy UTC - 1» (11-11) no cuenta a esa hora |
| `tests/test_jev_senales.py` (`_metricas_de_ayer`) | `(now() AT TIME ZONE 'UTC')::date - 1` y `jev_senales.main` sin reloj | `_AHORA_GUARDA` fijo, `metricas_listas._ahora` fijado con `monkeypatch`, siembra por plataforma |
| `tests/test_cli_bids.py` (`_hoy`) | sin cambio | todas sus llamadas usan `--forzar`: la guarda no lee lo que siembra (la fila dice «si lo que siembra lo lee la guarda») |
| `tests/test_reports_pipeline.py` (dos pruebas de integración), `tests/test_ads_producto_ingesta.py`, `tests/test_placements.py` (`test_sync_salta_fechas_que_no_pidio`) | «ayer» = `now()` UTC - 1 escrito por `escribir_grupo` con `now()` de la base | «ayer» = el último día ya cerrado (en MX y US, o en MX para placements) en el instante real; no dependen de la hora |

## Ajuste a una prueba que D-J dejó en rojo en master

`tests/test_metricas_listas.py::test_jev_con_metricas_corre` y
`::test_jev_forzar_corre_sin_metricas` fallaban igual en `origin/master` `a6ffa32`
(`AttributeError: 'NoneType' object has no attribute 'motivo'`): su stub de
`jev_senales.correr` devolvía `None` y desde D-J `main` lee `cierre.motivo`. El `Acepta`
de D-E.4 y D-E.5 corre ese archivo con `-rs`, así que el stub ahora devuelve un cierre
`completa`. No cambia ninguna aserción.

## Prueba real, antes del despliegue (solo lectura)

`bash docs/evidencia/arreglos-01/en_prod.sh docs/evidencia/arreglos-03/D-E/prueba.py` y
`ssh goncloud 'crontab -u gon -l'`, 2026-11-18 ~11:00 UTC:

```
ModuleNotFoundError: No module named 'app.dia_cerrado'
en_prod exit=1
10 7 * * * docker exec orbit-app-1 python -m app.cli ingest diario --s…
5 8,10,13,16 * * * /usr/local/bin/cron-avisa --nombre "Avisos de campa…
20 8,10,13,16 * * * /usr/local/bin/cron-avisa --nombre "Vigía de impu…
40 8,10,13,16 * * * /usr/local/bin/cron-avisa --nombre "Optimizador de…
41 8,10,13,16 * * * /usr/local/bin/cron-avisa --nombre "Optimizador de…
lineas '^10 8 * * * …ingest diario': 0
```

La `prueba-real` del bloque la corre el lead después de desplegar e instalar el horario
con `cron_orbit.sh`.
