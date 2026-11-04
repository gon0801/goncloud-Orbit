# Brief para Muse: modo de los goals con ceremonia (`goals set --mode` + `tools/goals_modo_grupo.py`)

Base `origin/master` tras `git fetch` (HEAD vigente). Rama desde el remoto,
**jamás** desde tu master local; `git log origin/master..HEAD` solo con los
commits de esta tarea. **Una sola tarea: el camino sellado para cambiar el
`mode` de un goal.** Se toma **después** del vigilante (un solo checkout).
No toca F2, ni el apply, ni el ciclo, ni Amazon.

Contrato: `app/goals_write.py` (regla 1: **único camino de escritura** de
`ads_optimizer_goal`; `edita_goal` con `updated_at` explícito, validación
pura antes de leer, `GoalInvalido`/`GoalInexistente` con exit codes
sellados), `app/cli.py::_goals_set` (despacha, cero SQL),
`app/optimizer/goals.py` (modo efectivo = meet de `ads_optimizer_mode` y
`goal.mode` en el retículo `off < shadow < live`), la ceremonia sellada del
repo (`tools/harvest_excepcion.py`: dry-run por defecto,
`--acepto-mutacion-real --esperado N --huella H --go "<literal>"`, solo
`app_admin`), y la fila D.3 de `plans/fabrica-02.md`. Este brief es
ejecutable y subordinado a esos documentos.

## El hueco que se tapa

Hoy el `mode` de un goal solo se fija al crearlo (`crea_goal`,
`MODOS_CREACION`). Ni `edita_goal` ni `goals set` ni el endpoint
`POST /goals/{id}` lo tocan, y el UPDATE crudo está prohibido por el candado
de arquitectura. Los cinco goals del grupo 1 (`kit_arras | Personalizado`)
nacieron en `shadow`; D.3 necesita encenderlos a `live` con go del dueño y
readback, y necesita poder **apagarlos** de vuelta sin ceremonia larga
(kill switch). Sin esto D.3 no arranca.

## Resultado exacto

1. **`goals_write.edita_goal(..., mode: str | None = None)`**. Valores
   válidos: `off`, `shadow`, `live` (los del CHECK de 0001). `None` = no
   cambiar. Validación **pura, antes de leer la fila**: valor fuera del
   vocabulario → `GoalInvalido("mode debe ser uno de ('off','shadow','live'), llegó …")`.
   Se combina libremente con los demás campos (es una columna más en
   `_cambios_edicion`). Regla de dominio, validada **después de leer**:
   pasar a `live` un goal `scope='campaign'` cuya campaña está en
   `campana_grupo_rol` exige que el grupo resuelva destino (rol
   `category_exact` presente en el grupo) **o** que el goal tenga terna
   completa; si no, `GoalInvalido("live sin destino de harvest: …")` — un
   goal en vivo que no puede cosechar es un goal que gasta sin cosechar.
   Sin trigger nuevo ni migración: el CHECK de `mode` ya existe.
2. **`python -m app.cli goals set <id> --mode off|shadow|live`**. Subir a
   `live` exige ceremonia: `--acepto-mutacion-real --go "<literal del
   dueño>"` (sin `--esperado/--huella`: es UN goal, el id es la
   autorización por conjunto). Sin los dos flags, `--mode live` es
   **dry-run**: imprime `goal <id>: mode actual → live` y sale 0 sin
   escribir. Bajar (`shadow` u `off`) **no exige ceremonia**: es el kill
   switch y no puede depender de un hash. Readback: la fila completa, como
   hoy. `--mode` cuenta como campo editado para el candado de «edición
   vacía».
3. **`tools/goals_modo_grupo.py`** (nuevo, patrón `tools/harvest_excepcion.py`):
   `--grupo N --mode shadow|live`, solo Postgres, solo `ORBIT_DSN_ADMIN`,
   cero Amazon, cero apply. Candidatas: goals `scope='campaign'` cuyas
   campañas están en `campana_grupo_rol` del grupo (JOIN por
   `ad_entity_id`, jamás por nombre); un goal ya en el modo pedido cuenta
   como «ya está» y no entra a `--esperado`. Dry-run por defecto: tabla
   `goal_id | rol | mode actual → pedido | terna/bid-solo`, el valor de la
   envolvente `ads_optimizer_mode` vigente (con la nota «modo efectivo =
   meet»), y la huella sobre `(grupo, mode pedido, goal_ids ordenados)`.
   Mutación real con la ceremonia completa (`--acepto-mutacion-real
   --esperado N --huella H --go "<literal>"`), **goal por goal vía
   `edita_goal`**, reanudable (un fallo a mitad aborta con el `goal_id`; lo
   cambiado queda cambiado; re-correr termina el resto). Readback: los
   goals del grupo con su `mode` nuevo y el modo efectivo por goal. Entra
   por stdin al contenedor como los demás tools (la imagen solo trae
   `app/`).
4. **El endpoint `POST /goals/{id}` NO expone `mode`** (decisión sellada
   en este brief): el modo cambia solo desde shell admin con ceremonia.
   Agrega el test negativo: un body con `mode` → 422 y la fila intacta.
5. **Sin tocar `docs/DEPLOY.md`**: el paso 1 de D.3 lo reescribe el lead en
   el PR de cierres con tus comandos reales; pega en el PR el dry-run y el
   go literal de ejemplo para copiarlos.

## Antes de escribir una línea

Lee y anota el SHA en el PR: `app/goals_write.py` completo (`edita_goal`,
`_cambios_edicion`, `_valida_pre_editar`, `crea_goal`, `MODOS_CREACION`),
`app/cli.py::_goals_set` y el parser `goals`, `app/api_write.py::editar_goal`
y `CuerpoGoal`, `app/optimizer/goals.py` (docstring del modo efectivo),
`tools/harvest_excepcion.py` (ceremonia, `Abortar`, huella, reanudación),
`tests/test_goals_write.py`, `tests/test_cli.py` (bloque `goals`),
`tests/test_harvest_excepcion.py` (fixtures de tool con DSN real) y
`tests/test_architecture.py` (candado de escritor único de goals en
`tools/`). Baseline con DSN real antes del primer rojo:

```bash
ORBIT_TEST_DSN=<dsn-test> uv run --frozen python -m pytest -q \
  tests/test_goals_write.py tests/test_cli.py tests/test_harvest_excepcion.py tests/test_architecture.py
```

## Archivos y fronteras

Puedes tocar solo: `app/goals_write.py` (aditivo: parámetro `mode`, su
validación pura y la regla post-lectura), `app/cli.py` (flags `--mode`,
`--acepto-mutacion-real`, `--go` en `goals set`, aditivo),
`tools/goals_modo_grupo.py` (nuevo), `tests/test_goals_write.py`,
`tests/test_cli.py`, `tests/test_goals_modo_grupo.py` (nuevo),
`tests/test_architecture.py` (allowlist del tool nuevo + candado).

Prohibido: `migrations/`, `app/api_write.py` (el test negativo vive en
tests), `app/optimizer/*`, `app/apply*`, `app/cycle.py`, `app/ads/*`,
`docs/`, `plans/`, el tracker. Cero SQL de escritura fuera de
`goals_write` (el candado de arquitectura ya escanea `tools/`; el tool
nuevo despacha a `edita_goal`). Cero producción, cero SSH, cero Amazon,
cero secretos. **Prohibido tocar el contenedor de producción**: encender el
grupo 1 es D.3, lo corre el dueño con el lead, después del 19-sep.

## Orden TDD obligatorio

1. `edita_goal(mode=…)` rojo-primero: vocabulario (los tres valen, otro
   valor → `GoalInvalido` antes de leer); `mode` solo → cambia la fila y
   `updated_at`; combinado con `--target` → ambos; regla post-lectura
   (goal de grupo sin exacta y sin terna → `GoalInvalido`; con exacta →
   ok; goal suelto con terna → ok); goal `scope='platform'` → `mode`
   permitido (no hay regla de destino ahí).
2. CLI `goals set --mode`: `live` sin ceremonia = dry-run sin escribir
   (spy en `edita_goal`) y exit 0; `live` con `--acepto-mutacion-real`
   pero `--go ""` → exit 2 sin escribir; `live` con ceremonia → llama a
   `edita_goal(mode="live")`; `shadow`/`off` sin ceremonia → escribe;
   `--mode` inválido → exit 2; sin DSN admin → exit 2.
3. Endpoint: body con `mode` → 422, fila intacta.
4. Tool con DSN real: dry-run no escribe y lista candidatas + envolvente;
   `--esperado` distinto de candidatas → aborta; huella distinta → aborta;
   go escribe solo los del grupo (uno de otro grupo y uno `scope=platform`
   intactos); «ya está» no cuenta ni se reescribe; fallo inyectado en el
   segundo goal → aborta con su id, el primero queda cambiado, re-correr
   termina el resto; readback muestra modo efectivo (meet) con la
   envolvente en `shadow` y en `live`.
5. Arquitectura: allowlist de imports del tool; el candado de escritor
   único **falla** con un `UPDATE ads_optimizer_goal SET mode` crudo
   sembrado en `tmp_path` bajo `tools/`.

## Mutantes que deben morir

- aceptar un `mode` fuera del vocabulario;
- `live` sin ceremonia que escriba (CLI) o `--go` vacío aceptado;
- bajar a `shadow` exigiendo ceremonia (el kill switch se traba);
- regla post-lectura invertida o ausente (live sin destino pasa);
- tool que edita goals de otro grupo, de `scope=platform`, o por nombre;
- huella ignorada o `--esperado` que cuenta los «ya está»;
- seguir después de un fallo a mitad sin abortar;
- UPDATE crudo en el tool (candado);
- endpoint que acepta `mode`;
- readback que imprime el `mode` pedido en vez del leído.

## DoD binario

1. `edita_goal` acepta `mode` con validación pura y regla post-lectura;
   `goals set --mode` con dry-run/ceremonia para `live` y sin ceremonia
   para bajar; `tools/goals_modo_grupo.py` con dry-run, ceremonia, goal por
   goal, reanudable y readback con modo efectivo.
2. Endpoint sin `mode` (test negativo 422).
3. Mutantes de arriba muertos, documentados en el PR con su rojo.
4. Candado de arquitectura verde y **rojo** con el UPDATE sembrado.
5. Ruff, `pre-commit run --all-files`, hook pre-push y la batería completa
   verdes **una sola vez en CI** sobre el SHA final.

## Verificación y entrega

```bash
ORBIT_TEST_DSN=<dsn-test> uv run --frozen python -m pytest -q \
  tests/test_goals_write.py tests/test_cli.py tests/test_goals_modo_grupo.py tests/test_architecture.py
uv run --frozen ruff check app/goals_write.py app/cli.py tools/goals_modo_grupo.py tests/
uv run --frozen ruff format --check app/goals_write.py app/cli.py tools/goals_modo_grupo.py tests/
pre-commit run --all-files
pre-commit run --hook-stage pre-push   # el push normal también lo ejecuta; jamás --no-verify
```

PR a `master` desde `origin/master`, carril **gate**, **en cola**: no se
mergea hasta el APPROVE del lead. La descripción del PR incluye: SHA base,
baseline literal, rojos por bloque, salida literal del dry-run del tool y
de `goals set --mode live` sin ceremonia, mutantes con su rojo, el diff de
`app/goals_write.py` mostrando que solo agrega, el go literal de ejemplo, y
el enlace al CI verde. Residuales a declarar: el tool no valida la
envolvente (`ads_optimizer_mode`), solo la muestra; encender un grupo con
envolvente en `shadow` es legal y no hace nada hasta que la envolvente suba.

Loop de cross-review, sin tope de rondas: **kimi** revisa sobre tu SHA;
lo que encuentre lo corriges en el mismo PR y kimi vuelve; cuando kimi sale
limpio entra el lead; si el lead encuentra algo, vuelve a ti y kimi lo
vuelve a ver. Solo el APPROVE del lead cierra el loop.
