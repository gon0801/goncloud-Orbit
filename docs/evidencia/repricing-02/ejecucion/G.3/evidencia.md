# G.3 — pantalla /precios con edicion (REPRICING 02, Seccion 3)

Pantalla `/precios` con tabla de goals por unidad (casillas, barra en
bloque, campo de go literal) sobre las rutas G.2. Rama
`rp02/sec3-seguridad-goals` sobre G.2 (`7931776`).

## Cambios

- `app/api_precios.py` (+64): lista `unidades` por plataforma en
  `/api/dashboard/precios` (solo `/precios`, no `/salud`): por unidad
  `listing_id`, `sku`, `canal`, `tiene_goal`, `margen_hoy` (tanto por
  uno), `goal`, `goal_mode`, `precio_objetivo`, `envio_origen`, `tacos`.
  `None` donde no hay dato.
- `app/templates/_precios_goals.html` (+55/-1): seccion `bloque-goals-*`
  por plataforma con la tabla (casilla por fila, origen con marca
  `chip alerta` en `familia`/`marketplace`) y la barra (pedido, goal %,
  modo, go literal, actor, token, "todos los sin goal", Planear,
  Aplicar deshabilitado, vista previa). `_precios_seguridad.html`
  intacto (solo incluido, como estaba).
- `app/templates/precios.html` (+1): bloque `scripts` con
  `/static/js/precios.js` (patron de `familias.html`).
- `app/static/js/precios.js` (nuevo, 300 lineas): token del campo de
  password al header `x-orbit-token` sin guardarlo; Planear pinta la
  vista (huella, filas, bloqueos con motivo, casilla por confirmable) y
  solo entonces habilita Aplicar; Aplicar reenvia el spec planeado +
  `{huella, confirmados, go_literal solo en live, actor}`; 409 repinta
  el plan nuevo; el boton Soltar de S.3 abre un mini-formulario
  (actor, nota, token) junto al boton.
- `tests/test_precio_pantalla.py` (+225): bloque G.3 al final con 2
  pruebas (patron S.3: JSON + HTML; semillas de estimacion reusadas de
  `test_precio_goals` como G.2).

## Comandos y conteos

TDD rojo primero (`rojo-tdd.txt`): `KeyError: 'unidades'` y
`assert 'bloque-goals-amazon_mx' in ...`, 2 failed.

```bash
PATH=/opt/homebrew/bin:$PATH ORBIT_TEST_DSN=postgresql://orbit:orbit@localhost:5432/postgres \
PYTHONPATH=. .venv/bin/python -m pytest -q -rs -p no:cacheprovider tests/test_precio_pantalla.py -k "test_g3_"
# 2 passed, 85 deselected
```

```bash
# Focalizadas finales (pantalla + rutas G.2 + assets): pytest.txt
... pytest -q -rs -p no:cacheprovider tests/test_precio_pantalla.py \
tests/test_api_precios_write.py tests/test_ui_assets.py
# 99 passed in 11.87s
```

```bash
.venv/bin/ruff check app/api_precios.py tests/test_precio_pantalla.py  # All checks passed!
.venv/bin/ruff format --check app/api_precios.py tests/test_precio_pantalla.py  # 2 already formatted
```

Mutantes: `mutantes.md` (M1, M2: uno por prueba nueva; rojo y
revertido cada uno). Bateria completa: no corrida (encargo).

DoD fila G.3:

1. El servidor entrega por unidad margen de hoy, goal, precio
   objetivo, origen del envio y TACoS: `unidades` en
   `test_g3_precios_unidades_traen_margen_goal_objetivo_origen_y_tacos`.
2. La prueba manual siembra un goal, cambia otro y siembra "margen de
   hoy" a todos los sin goal: `manual.txt` + PNG (pasos 1-4 MANUAL OK
   con verificacion en base en cada paso).

## Prueba manual (metodo exacto)

Sin tooling UI en el repo (`package.json` no existe; `tests/test_ui*.py`
son TestClient sin capturas) y sin red para instalar navegadores: Edge
155 headless del equipo + CDP con un cliente de stdlib
(`/tmp/g3_manual.py`: `Page.navigate`, `Runtime.evaluate` para llenar y
clicar de verdad, `Page.captureScreenshot`). Servidor:
`uvicorn app.main:app --port 8123` con `ORBIT_DSN_READ`/`ADMIN` a la
base `orbit_g3_manual` (ORDEN59 + config con banda 0.10-0.60) y token
`token-manual-g3`; siembra con `/tmp/g3_seed_manual.py` (U1 sin goal
m24 + envio familia, U2 goal 25, U3 m42.50, U4 m75, U5 sin escenario,
TACoS 10.00, corrida 6 retenida). Cada paso verifica el estado en base
con psycopg además de la captura.

- `paso0-inicial.png`: tabla con las 5 unidades, marca familia, Aplicar
  deshabilitado.
- `paso1-plan.png`, `paso1-aplicado.png`, `paso1-tabla.png`: U1
  planea valor 30 shadow, aplica, reaparece con goal 30.00 % (tabla
  corrida a la derecha: TACoS 10.00 % visible).
- `paso2-plan.png`, `paso2-aplicado.png`: U2 cambia 25 a 32.00 % (un
  vigente, anterior cerrado).
- `paso3-plan.png`: "margen de hoy" a sin goal: 3 filas, 2 bloqueadas
  (`fuera_de_banda` confirmable con casilla, `sin_escenario`), Aplicar
  habilitado solo despues.
- `paso4-aplicado.png`: 1 sembrado (U3 0.4250 `margen_de_hoy`), 0
  cerrados; U4/U5 sin goal; bloqueos listados con motivo.
- `paso5-soltar-form.png`, `paso5-soltar-ok.png`: mini-formulario de
  Soltar y corrida 6 liberada (paso extra: el control se cableo aqui).

## Desviaciones con razon

1. TACoS por unidad = `v_tacos` del mes del corte (mes de hoy - 15),
   por plataforma, repetida en cada fila. Es la unica TACoS del
   sistema (por plataforma y mes); no existe TACoS por unidad e
   inventar una metrica por producto (ventana, madurez, halo) queda
   fuera de G.3. El mes del corte (no el calendario) es determinista:
   el dato maduro solo existe hasta D-15; sin fila, `None` ("-").
2. Lista `unidades` nueva en vez de extender `con_goal`/`sin_goal`.
   Forma uniforme para editar con y sin goal; cero cambios a claves
   existentes (las pruebas vecinas ni se enteran).
3. `margen_hoy` sale del escenario disponible mas reciente, no de la
   decision. Es lo que "margen de hoy" copia al sembrar: lo que se ve
   es lo que se siembra. `precio_objetivo` sale de la decision de hoy
   o `None` (la pantalla no deriva P*: un numero, una fuente).
4. `envio_origen` sale del escenario (`logistica_origen`, 0054):
   unica fuente uniforme para con y sin goal (la decision solo existe
   con goal).
5. Una sola barra en bloque (sin inputs por fila) + casilla "todos los
   sin goal": un camino sobre los pedidos G.2 tal cual. Pedidos de
   pantalla: `margen_de_hoy`/`valor`/`modo` (pasar a live con go
   literal); `cerrar` por pantalla no esta pedido.
6. Soltar con mini-formulario inyectado por JS (actor, nota, token
   propios): `_precios_seguridad.html` es intocable y su comentario
   pide que el JS de G.3 cuelgue del boton por
   `data-liberar-corrida`.
7. Paso 5 extra (Soltar) en la prueba manual: el control se cableo en
   este cambio y se verifica en la misma superficie.
8. Literales del JSON con la escala del NUMERIC de origen (`0.2400`,
   `127.6000`): convencion de `_dec_str`, no normalizacion nueva.
