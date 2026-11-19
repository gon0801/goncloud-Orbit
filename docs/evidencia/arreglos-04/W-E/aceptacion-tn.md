# W-E / TN15: madurez de 10 dias en busquedas sin venta, corte D-9 y cobertura en ruido

Contrato: `plans/arreglos-04.md` (`## Bloque W-E`), `docs/evidencia/arreglos-04/DISENO.md` (`## W-E` y `### TN15`) y `docs/evidencia/arreglos-05/ACEPTACION.md`. Owner unico de `TN15`: bloque `W-E`. No se crea una capa generica de metricas ni se modifica `ui_metricas.sin_ventas` (`TN16` pertenece exclusivamente a `W-D`).

## Cambios de contrato e invariantes

1. **F5 (`W-E.1`, `W-E.2`): `app/pantalla_busquedas.py` y `app/templates/busquedas_sin_venta.html`.**
   - `lee_busquedas(conn, *, plataforma, hoy)` fija `hasta = hoy - timedelta(days=DIAS_MADUREZ_CORTES)` (`hoy - 10`, misma madurez de la regla 6 en `app.optimizer.windows`) y `desde = hasta - timedelta(days=DIAS_BUSQUEDAS - 1)` con `DIAS_BUSQUEDAS = 90` (`(hasta - desde).days + 1 == 90`).
   - `app/ui.py:798` ya propaga `hasta` dentro de `**datos`; `busquedas_sin_venta.html` muestra el texto exacto del diseno con `{{ hasta | fecha_corta }}`.

2. **F6 (`W-E.3`, `W-E.4`): `app/pantalla_ruido.py`, `app/api_dashboard.py` y `app/templates/ruido.html`.**
   - `_SQL_MERCADO` agrega `AND m.metric_date <= %s` y `_comparacion(conn, plataforma, dias, *, hoy)` calcula `ultimo_maduro = hoy - timedelta(days=DIAS_INMADUROS + 1)` (`D-9`, con `DIAS_INMADUROS = 8` de `app.dashboard_resumen`), aplica el mismo tope en SQL y en Python sobre `antes` y `despues`, y publica `"despues_hasta": ultimo_maduro.isoformat()`.
   - `lee_ruido(conn, *, plataforma, hoy, dias=30)` recibe `hoy` explicito y `app/api_dashboard.py::ruido` le pasa `hoy=_hoy_pantalla(mercado)`.
   - Si el encendido es mas reciente que `D-9`, `despues` queda sin filas (`None`, `"Sin datos."` en HTML). El `<h4>` de `despues` en `ruido.html` muestra `hasta el {{ antes_y_despues.despues_hasta | fecha_corta }}; los últimos 8 días se dejan fuera porque sus ventas todavía llegan`.

3. **TN15 (`W-E.5`, `W-E.6`): cobertura completa antes de dividir en `app/pantalla_ruido.py::_bloque` y `app/templates/ruido.html`.**
   - Se retira `_suma` permisiva y se introduce `_suma_cobertura(valores, cero)` como unica comprobacion local de cobertura por metrica dentro de `app/pantalla_ruido.py`.
   - Si cualquier fila de la poblacion/moneda/ventana tiene `NULL` en una metrica (`cost`, `orders` o `ad_revenue`), el total de esa metrica queda en `None` (jamas se publica un subtotal ignorando `NULL` como total completo) y su bandera en `cobertura` queda en `False`.
   - `acos_pct` solo se calcula cuando `cobertura["gasto"]` y `cobertura["venta"]` son `True` y `venta > 0`. Con falta de cobertura en gasto o venta, `acos_pct` es `None` y `causa_acos` es `"cobertura_incompleta"`; con cobertura completa y `venta <= 0`, `acos_pct` es `None` y `causa_acos` es `"sin_ventas"`; con conjunto vacio, `_bloque` devuelve `None`.
   - Tanto `antes` como `despues` en `app/templates/ruido.html` muestran `—` en la metrica sin cobertura y la frase exacta `Faltan datos para calcular el ACoS.` cuando `causa_acos == "cobertura_incompleta"`, conservando `ACoS —.` cuando hay cobertura completa sin ventas.

## Evidencia en rojo (antes del arreglo)

1. **Arnes TN (`test_15` en `819fe1ff` / `ff03ed61`):** registrado en `plans/arreglos-04.md:32`, `docs/evidencia/arreglos-04/BASE/rebase.md:16` y `docs/evidencia/arreglos-05/revision/hallazgos.json:502`, donde `test_15_ruido_no_publica_acos_con_gasto_desconocido` fallo con `AssertionError: '5.00' is None` al sumar `(cost=10, sales30d=100)` + `(cost=None, sales30d=100)`.
2. **F5 en `0251269` (rebasado sobre `origin/master` `a967874`, que integra `W-K` #674):** `PYTHONPATH=. .venv/bin/python -B -m pytest -p no:cacheprovider -q tests/test_pantalla_busquedas.py -k arreglos04_w_e_` fallo con `AssertionError: assert datetime.date(2026, 11, 9) <= (datetime.date(2026, 11, 12) - datetime.timedelta(days=10))` (91 dias `2026-08-11..2026-11-09`).
3. **F6 y TN15 en `b8c5fa5` (antes de `af22e6e`):**
   - `test_arreglos04_w_e_bloque_exige_cobertura_antes_de_dividir`: `Differing items: {'gasto': '10'} != {'gasto': None}, {'acos_pct': '5.00'} != {'acos_pct': None}`.
   - `test_arreglos04_w_e_comparacion_excluye_dias_inmaduros_y_expone_hasta` y `docs/evidencia/arreglos-04/W-E/prueba.py`: `TypeError: _comparacion() got an unexpected keyword argument 'hoy'` (y en `test_rev_numeros.py` sobre `origin/master`: `- 20.00 / + 22.12`).
   - `test_arreglos04_w_e_ruido_api_y_html_coexisten_madurez_y_cobertura`: `AssertionError: {'gasto': '1009.0000', 'pedidos': 11, 'venta': '1199.0000', 'acos_pct': '84.15'}` (`assert '84.15' is None`, mezclando el dia inmaduro `2026-11-10` con la fila `cost=None` del `2026-10-15`).

## Evidencia en verde (con el arreglo)

- `W-E.1`: `PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_pantalla_busquedas.py -k arreglos04_w_e_` -> `1 passed, 34 deselected` (`rc=0`).
- `W-E.2`: `PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_pantalla_busquedas.py tests/test_ui.py` -> `105 passed, 26 skipped` (`rc=0`).
- `W-E.3` y `W-E.5`: `PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_pantalla_ruido.py tests/test_arq_bids_p.py` -> `27 passed, 3 skipped` (`rc=0`), cubriendo madurez `D-9`, encendido posterior a `D-9`, gasto ausente, venta ausente, conjunto completo con ventas, conjunto completo con venta cero, conjunto vacio y coexistencia API+HTML desde `_planea_filas(KEYWORDS_CFG, ...)`.
- `W-E.4`: `PYTHONPATH=. uv run --frozen --python 3.12 python - < docs/evidencia/arreglos-04/W-E/prueba.py` -> `W-E OK` (`rc=0`).
- Candados arquitectonicos: `PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_architecture.py -k "not presupuesto_de_tamano_por_modulo"` -> `96 passed` (`rc=0`; `app/ledger.py` es fallo preexistente en `origin/master`, `BASE/rebase.md:65`).
- En esta Mac mini (`darwin` arm64) sigue ausente PostgreSQL local en `127.0.0.1:5432` y `127.0.0.1:55449` (`BASE/rebase.md:69`, igual que en `W-C` #668 y `B-C` #661):
  - Con `CI=1`, `tests/test_schema.py::_postgres_obligatorio_ausente` falla cerrado reclamando PostgreSQL, y en `tests/test_api_dashboard.py` solo fallan los mismos 4 tests sin `@skipif` de `origin/master` cuando no escucha `5432` (`134 passed, 62 skipped` en el conjunto `test_pantalla_ruido.py + test_api_dashboard.py + test_ui.py`).
  - `PYTHONPATH=.:tests:docs/evidencia/arreglos-05/revision uv run --frozen --python 3.12 python -B -m pytest -p no:cacheprovider --collect-only -q docs/evidencia/arreglos-05/revision/test_accept_ui.py -k "test_15 or test_control_contrato"` recoge exactamente los `2/6` casos (`test_15_ruido_no_publica_acos_con_gasto_desconocido` y `test_control_contrato_resumen_conserva_desconocido_y_csp`, `rc=0`), listos para la corrida con PG16 desechable en `127.0.0.1:55449` del laboratorio de Hermes.

## Feature Map y recorrido local real de pantallas

Se verifico `.cursor/skills/verify-orbit/features/` y `.cursor/skills/verify-orbit/SKILL.md`: ni `/busquedas-sin-venta` ni `/ruido` tienen ficha en `features/` ni comando `drive-*` en `orbit-verify`. Conforme a `DISENO.md:410` y `ACEPTACION.md:126-127`, no se inventa `drive-ruido`.

Se ejecuto recorrido local real con Google Chrome `157.0.8103.79` (`--headless --dump-dom`) contra un servidor HTTP local efimero (`127.0.0.1:<puerto_libre>`) sirviendo `app.main:app` en `2026-11-13T03:00:00Z` (`2026-11-12` 21:00 CDMX):
- `GET /busquedas-sin-venta?plataforma=amazon_mx` (`200`) y `GET /api/dashboard/busquedas-sin-venta?plataforma=amazon_mx` (`200`): `Content-Security-Policy: default-src 'self'`, `Cache-Control: no-store`, `desde="2026-08-05"`, `hasta="2026-11-02"` y DOM con `data-pantalla="busquedas-sin-venta"` y `Solo campañas encendidas, 90 días que terminan el 2 nov. Los últimos 10 días se dejan fuera porque sus ventas todavía llegan, igual que hace Orbit antes de cortar.`.
- `GET /ruido?plataforma=amazon_mx` (`200`) y `GET /api/dashboard/ruido?plataforma=amazon_mx` (`200`): `Content-Security-Policy: default-src 'self'`, `Cache-Control: no-store`, `despues_hasta="2026-11-03"`, exclusion del dia inmaduro `2026-11-10`, `despues={"gasto": null, "pedidos": 2, "venta": "200.0000", "acos_pct": null, "cobertura": {"gasto": false, "pedidos": true, "venta": true, "completo": false}, "causa_acos": "cobertura_incompleta"}` y DOM con `data-pantalla="ruido"`, `Después del encendido (1 día, hasta el 3 nov; los últimos 8 días se dejan fuera porque sus ventas todavía llegan)` y `Gasto — MXN, 2 pedidos, venta 200.00. Faltan datos para calcular el ACoS.`.
