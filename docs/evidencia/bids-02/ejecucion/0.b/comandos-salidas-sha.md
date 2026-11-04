# 0.b: comandos, salidas y SHA

SHA base: `282357d11dd0b63b55b2e69dfe57184663b3cff4` (rama
`bids-02/s1-preparacion`, arbol limpio antes de empezar). `$HOME` aparece
como `~` y la clave del DSN local como `***` (reglas 4, 10 y 16). Postgres
local en `127.0.0.1:5433`; `PATH` con `/opt/homebrew/bin` para `psql`.
Produccion solo se leyo por ssh (`pg_dump` + `printenv ORBIT_DSN_READ`);
jamas se escribio ahi.

## 1. Prueba primero: rojo por la razon correcta

Tres pruebas nuevas, tres rojos antes de escribir codigo:

```
$ ORBIT_TEST_DSN=postgresql://orbit:***@127.0.0.1:5433/postgres .venv/bin/python -m pytest -q tests/test_arq_bids_ci.py tests/test_optimizer_goals.py::test_gasto_para_concluir_defaults_por_plataforma tests/test_optimizer_goals.py::test_gasto_para_concluir_invalido_falla_cerrado
E           AttributeError: module 'app.optimizer.goals' has no attribute 'gasto_para_concluir_desde_settings'
FAILED tests/test_arq_bids_ci.py::test_paso_guardas_nombra_test_arq_bids - As...
FAILED tests/test_optimizer_goals.py::test_gasto_para_concluir_defaults_por_plataforma
FAILED tests/test_optimizer_goals.py::test_gasto_para_concluir_invalido_falla_cerrado
3 failed in 2.12s
```

Linea roja del arq:

```
E       AssertionError: el paso de guardas debe correr tests/test_arq_bids_*.py (comando actual: 'pip install fastapi "psycopg[binary]" httpx pytest pglast jinja2\nPYTHONPATH=. pytest -q tests/test_architecture.py tests/test_precommit_hooks.py tests/test_chat_context_guard.py\n')
```

Vistas (sin la 0060 las vistas no existen):

```
$ ORBIT_TEST_DSN=postgresql://orbit:***@127.0.0.1:5433/postgres .venv/bin/python -m pytest -q tests/test_bids02_vistas.py
E           psycopg.errors.UndefinedTable: relation "v_hoja_activa" does not exist
FAILED tests/test_bids02_vistas.py::test_v_hoja_activa_exige_triple_enabled
FAILED tests/test_bids02_vistas.py::test_v_hoja_activa_tipo_campana_cinco_casos
FAILED tests/test_bids02_vistas.py::test_v_cambio_bid_motor_live_y_sus_exclusiones
FAILED tests/test_bids02_vistas.py::test_v_cambio_bid_regreso_del_dueno - psy...
FAILED tests/test_bids02_vistas.py::test_vistas_legibles_para_decide_read_admin
5 failed in 1.22s
```

## 2. Verde por archivo

```
$ ORBIT_TEST_DSN=postgresql://orbit:***@127.0.0.1:5433/postgres .venv/bin/python -m pytest -q tests/test_bids02_vistas.py
.....                                                                    [100%]
5 passed in 1.20s

$ ORBIT_TEST_DSN=postgresql://orbit:***@127.0.0.1:5433/postgres .venv/bin/python -m pytest -q tests/test_arq_bids_ci.py tests/test_optimizer_goals.py -k "gasto_para_concluir or arq_bids or paso_guardas"
...                                                                      [100%]
3 passed, 53 deselected in 0.19s
```

## 3. Medicion de vistas (regla 8)

Base compartida antes de 0060: 70 tablas base, 15 vistas. Base desechable
migrada desde cero con 0060: 70 tablas, 17 vistas (suma con 70: 87). Esos
numeros quedaron en `verify/Launch.md:38` y `verify/Doctor.md:25-26`.

```
$ psql "$DSN" -tAc "select count(*) from information_schema.tables where table_schema='public' and table_type='BASE TABLE'"
70
$ psql "$DSN" -tAc "select count(*) from information_schema.views where table_schema='public'"
15
$ ... crear orbit_medicion_0b, aplicar migraciones menos 0011/reversas ...
TABLAS:
70
VISTAS:
17
```

## 4. Comprueba: los 4 archivos

```
$ ORBIT_TEST_DSN=postgresql://orbit:***@127.0.0.1:5433/postgres .venv/bin/python -m pytest -q -rs tests/test_bids02_vistas.py tests/test_schema_docs.py tests/test_arq_bids_ci.py tests/test_optimizer_goals.py
..............................................................           [100%]
62 passed in 1.74s
```

Cero `skipped`, cero `sin Postgres utilizable`.

## 5. Mutantes

M0 (obligatorio) + M1-M8, cada uno en rojo con su linea y de vuelta en
verde. Lineas literales en `mutantes.md` de este directorio. Tras los 9,
`grep` de restos limpio y los 4 archivos de Comprueba en `62 passed`.

## 6. Ruff y pre-commit

```
$ .venv/bin/ruff check --fix . && .venv/bin/ruff format .
All checks passed!
12 files reformatted, 646 files left unchanged
```

El `ruff format` del venv (0.16.4) reformatea bloques Python dentro de `.md`
y toco 10 archivos fuera del alcance (docs y plans). Se revertieron con
`git checkout --` (verificado: `git status` solo trae archivos de 0.b). El
ruff de pre-commit (0.15.20) no los toca.

```
$ /opt/homebrew/bin/pre-commit run --all-files
trim trailing whitespace....................................................Passed
fix end of files............................................................Passed
check for merge conflicts...................................................Passed
check for added large files.................................................Passed
check json..................................................................Passed
check yaml..................................................................Passed
ruff check..................................................................Passed
ruff format.................................................................Passed
capa de contexto dentro de presupuesto (repo-hygiene).......................Passed
EXIT: 0
```

## 7. Copia de produccion y ensayo

```
$ ORBIT_TEST_DSN=postgresql://orbit:***@127.0.0.1:5433/postgres bash docs/evidencia/bids-02/ejecucion/0.b/copia.sh
== esquema de prod (orbit_read, solo lectura)
== datos de prod (solo lectura, 6 tablas)
pg_dump: warning: there are circular foreign-key constraints on this table:
pg_dump: detail: ad_entity
pg_dump: hint: You might not be able to restore the dump without using --disable-triggers or temporarily dropping the constraints.
pg_dump: hint: Consider using a full dump instead of a --data-only dump to avoid this problem.
ad_entity: 18524
ad_entity_state: 18524
decision: 2814
decision_application: 440
optimizer_cycle: 113
apply_attempt: 478
dumps en: /var/folders/hp/sc_3c6qs7nvdswl_x3w5sdbr0000gn/T/tmp.jYL0K2Bt0B
COPIA OK
```

(El aviso de FK circular es esperado: `ad_entity.parent_id` se
auto-referencia; la carga en modo `replica` lo salta y los conteos prueban
que los datos entraron.)

```
$ ORBIT_TEST_DSN=postgresql://orbit:***@127.0.0.1:5433/postgres bash docs/evidencia/bids-02/ejecucion/0.b/ensayo.sh
esquema de la copia: 70 tablas
== 1) aplicar 0060
psql:~/dev/wt-bids-02-s1/migrations/0060_bids02_base_lectura.sql:20: WARNING:  there is already a transaction in progress
WARNING:  there is no transaction in progress
aplicada 0060_bids02_base_lectura.sql
== 2) control: vista contra tablas base
control|vista|difieren = 588|588|0
OK: difieren = 0 y control = vista (588 hojas)
== 3) permisos esperados (los mismos que verifica el DO de 0060)
permisos OK
== 4) reversa de 0060
psql:~/dev/wt-bids-02-s1/migrations/0060_reversa_bids02_base_lectura.sql:11: WARNING:  there is already a transaction in progress
WARNING:  there is no transaction in progress
aplicada 0060_reversa_bids02_base_lectura.sql
OK: la reversa deja el esquema identico al de produccion
ENSAYO OK
```

(Los `WARNING` de transaccion son el patron S.3: `psql -1` + `BEGIN` en el
archivo. Benignos.)

Ultima linea literal del ensayo: `ENSAYO OK`.

Comprobaciones extra de solo lectura sobre la copia tras el ensayo: 0
vistas 0060 (revertida) y split `amazon_mx|355`, `amazon_us|233` — identico
a la referencia del 2026-10-09 (informativo; el DoD es control = vista,
cumplido). La base `orbit_copia_bids02_0b` quedo en el Postgres local,
revertida.
