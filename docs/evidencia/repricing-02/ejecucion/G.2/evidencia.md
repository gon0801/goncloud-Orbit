# G.2 — rutas de goals y compuerta (REPRICING 02, Sección 3)

`app/api_precios_write.py` (347 líneas): `POST /api/precios/goals/plan`
(vista previa, no escribe), `POST /api/precios/goals/aplicar` (todo o
nada con huella) y `POST /api/precios/compuerta/{corrida_id}/liberar`
(idempotente). Las tres reutilizan `exige_token` y `ConexionEscritura`
de `app/api_write.py` (token antes que conexión); registradas en
`app/main.py` junto a los demás routers (2 líneas). Rama
`rp02/sec3-seguridad-goals` sobre G.1 (`fe269cb`).

## Comandos y salidas

TDD rojo primero (`rojo-tdd.txt`): 9 pruebas en rojo con 404 (rutas
inexistentes), 0 saltos sobre ORDEN54.

```bash
PATH=/opt/homebrew/bin:$PATH ORBIT_TEST_DSN=postgresql://orbit:orbit@localhost:5432/postgres \
PYTHONPATH=. .venv/bin/python -m pytest -q -rs -p no:cacheprovider tests/test_api_precios_write.py
# 9 passed in 1.50s (5 del DoD + 4 extras: aplicar-ok, sin_goal, tope 500, liberar 409)
```

```bash
.venv/bin/ruff check app/api_precios_write.py app/main.py tests/test_api_precios_write.py
# All checks passed! (1 I001 autofix de orden de imports durante la sesión)
.venv/bin/ruff format --check <los 3>   # 3 files already formatted
```

Candados vecinos (puntuales, no batería): `test_architecture.py ::
test_escritura_precio_goal_vive_solo_en_goals_write` y `test_api_write.py ::
test_toda_ruta_post_declara_exige_token_antes_de_la_conexion` → 2 passed
(el módulo nuevo no trae SQL de escritura de `precio_goal`: despacha a
`goals_write`/`liberaciones`).

Mutantes: `mutantes.md` (M1–M9, uno por prueba; cada uno rojo y revertido).

DoD fila G.2 (los 5, en `tests/test_api_precios_write.py`):
1. sin token 401 antes de abrir admin — espiado: `connect` jamás se llama.
2. plan no escribe — `precio_goal` y `precio_compuerta_liberacion` en 0.
3. huella vieja 409 con el plan nuevo — `detail.plan_nuevo.huella` fresca.
4. live sin go 422 — cero escrituras.
5. liberar dos veces la misma fila — cuerpos idénticos, 1 fila.

## Desviaciones con razón

1. ORDEN54 sin 0058/0059: las rutas tocan `listing`, `config_version`,
   estimación, `precio_goal`/`v_precio_goal_vigente` y compuerta (0054);
   la 0058/0059 cubren `precio_decision`, que G.2 no pisa.
2. 4 pruebas extra sobre las 5 del DoD: aplicar-ok (sin ella un aplicar
   que nunca escribe pasa el DoD), `sin_goal` (selección del bosquejo T
   que G.3 necesita), tope 500 (lo pina el bosquejo T) y liberar-409
   (`CorridaSinRetencion -> 409` del bosquejo T).
3. `aplicar` repite la especificación del plan (`platform/pedido/
   selección`) además de `{huella, confirmados, go_literal}`: sin ella
   no puede recalcular adentro como exige `aplicar_plan`.
4. `liberar` pide `{actor, nota}` (el bosquejo abrevia `{nota}`):
   `liberar` exige actor no vacío y la ruta no inventa actores.
5. La ruta de aplicar suelta el snapshot (`rollback`) y enciende
   autocommit antes de `aplicar_plan`; la de liberar opera en
   autocommit: psycopg no cambia autocommit con transacción abierta y
   el re-lee de carrera de `liberar` no corre en transacción abortada.
6. Actor en blanco en aplicar → 422 en la ruta: `creado_por` es rastro
   de auditoría y `sembrar_goal` no lo valida (paridad con `liberar`).
