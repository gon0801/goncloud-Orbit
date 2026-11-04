# ORBIT 19 / B.5 — Reporte de pruebas del comparador UI

Fecha: 2026-09-06. Rama `feat/orbit19-fase-b` (sin commit; working tree).

## Alcance

- `app/templates/fabrica.html`: seccion `#fabrica-comparador` (encabezado
  etiquetado, selects de orden/direccion/filtro, boton de recarga, estado
  `role="status"`, contenedor de datos).
- `app/static/js/fabrica.js`: bloque comparador (GET `/api/fabrica/evaluacion`;
  orden y direccion resueltos por la API con NULL al final; filtro de
  presentacion; seleccion del catalogo intacta AC10; cero POST).
- `app/static/css/fabrica.css`: estilos de la tabla comparador (scroll
  horizontal, tabular-nums, wrap).
- `tests/test_ui_fabrica.py`: 2 tests nuevos + mock `/evaluacion` en el flujo
  JS existente.
- `tools/semilla_comparador.py`: sembrado local re-ejecutable.

El motor NO se duplica: la UI solo presenta lo que devuelve
`/api/fabrica/evaluacion` (B.4). No se toco `app/evaluacion_catalogo.py`,
`app/economia_observada.py`, `app/disponibilidad.py` ni migraciones.

## pytest focal

Comando:

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_ui_fabrica.py tests/test_evaluacion_catalogo.py
```

Salida:

```
39 passed, 1 warning in 0.60s
```

(11 de `test_ui_fabrica.py`, 28 de `test_evaluacion_catalogo.py`; los que
usan Postgres corrieron contra el Postgres local de tests.)

Tests nuevos:

- `test_comparador_renderiza_controles_de_orden_filtro_y_sin_por_probar`:
  seccion con `aria-labelledby`, las 8 metricas de orden (§7 D1), direccion,
  filtro, `role="status"` y la literal "Por probar" ausente en el JS.
- `test_flujo_js_comparador_etiquetas_orden_filtro_y_cero_escrituras`
  (JS real en Node): 503 honesto sin filas inventadas; vacio => "Sin datos."
  con ventana y grano visibles; datos mixtos con etiquetas por precedencia
  (Dentro / Gasto sin ventas / Sin datos / Por encima (provisional));
  muestra limitada aparte con "(no entra al orden)"; disponibilidad tres
  estados + "Featured Offer: Sin verificar"; objetivo visible; orden=>GET
  `orden=gasto&direccion=desc` sin perder la casilla ni la marca
  "Seleccionada" (AC10); filtro sin re-consulta; 0 POST en todas las llamadas.

## Ruff

```bash
.venv/bin/ruff check tools/semilla_comparador.py tests/test_ui_fabrica.py
All checks passed!
.venv/bin/ruff format --check tools/semilla_comparador.py tests/test_ui_fabrica.py
1 file would be left unchanged (ya formateado tras `ruff format`)
```

## App en vivo con la DB sembrada

```bash
PYTHONPATH=. .venv/bin/python tools/semilla_comparador.py
# DB lista: orbit_b5_comparador (+ resumen por listing)

ORBIT_DSN_READ=postgresql://orbit:orbit@localhost:5432/orbit_b5_comparador \
  PYTHONPATH=. .venv/bin/python -m uvicorn app.main:app --port 8765
```

Verificaciones con curl (salida resumida de
`GET /api/fabrica/evaluacion?plataforma=amazon_mx&orden=acos&direccion=desc`):

```
1 B0AAAAAAA01 | objetivo: None      | etiqueta: None                  | provisional: True  | acos: 50  | muestra: 1
6 B0DDDDDDD01 | objetivo: 25.00     | etiqueta: por_encima_del_objetivo | provisional: False | acos: 30 | limitada: True 7.38
3 B0BBBBBBB01 | objetivo: 25.00     | etiqueta: dentro_del_objetivo   | provisional: False | acos: 25  | muestra: 1
4 B0BBBBBBB02 | objetivo: 25.00     | etiqueta: dentro_del_objetivo   | provisional: True  | acos: 25  | muestra: 10 | disp: cero {fba: 0}
2 B0AAAAAAA02 | objetivo: None      | etiqueta: sin_datos             | acos: None         | muestra: 0 | disp: desconocido
5 B0CCCCCCC01 | objetivo: 25.00     | etiqueta: gasto_sin_ventas      | acos: None         | margen: -42.6 | motivos: ['Margen negativo.']
```

- `GET /campanas/nuevas` => 200 con 12 referencias a `fabrica-comparador`.
- `POST /api/fabrica/crear` sin token/DSN admin => 503: ninguna escritura
  comercial se dispara sola.
- Uvicorn apagado tras la verificacion.

Nota de honestidad del fixture: dentro de una ventana de 31 dias solo las
fechas mas viejas pueden estar maduras (observed_at >= D+30); por eso L4
(muestra 10) se muestra "Dentro del objetivo (provisional)" — es el
comportamiento correcto de la regla de madurez, no un defecto.

## Capturas

Pendientes del lead: ver `instrucciones-navegador.md` (URL exacta, casos y
tabla de verificacion).
