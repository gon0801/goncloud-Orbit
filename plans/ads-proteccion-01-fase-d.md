# ADS PROTECCION 01 — Fase D: plan de implementación de D.2, D.1b y C.2a

Este plan lleva las tres filas abiertas de la Fase D de
[`ads-proteccion-01.md`](ads-proteccion-01.md) a tres PRs, en este orden:
D.2, D.1b y C.2a. Es la guía del implementador. El diseño con firmas, SQL
y alternativas descartadas está en la evidencia
[`fase-d/diseno.md`](../docs/evidencia/ads-proteccion-01/fase-d/diseno.md).

Base: `origin/master` `c6e3fc1`. Las líneas citadas son de esa base.
Antes de empezar cada tarea, vuelve a ubicar cada símbolo con
`git grep -n <símbolo> origin/master`, porque las líneas se mueven.

## Las cuatro decisiones del dueño ya están tomadas

El dueño aceptó las cuatro recomendaciones el 2026-09-27, con el literal
"sí a las 4 recomendaciones". Cada decisión aplica a la tarea que indica.

| # | Decisión | Aplica a | Resuelto |
| --- | --- | --- | --- |
| P1 | ¿D.2 avisa por Telegram cuando frena una inversión? | D.2 | No hay aviso por Telegram. Basta el contador en `notes.skips`. |
| P2 | R-C3-1 pasa de "solo test" a arreglo de 3 líneas en `app/cycle.py`. ¿Se aprueba dentro de D.1b? | D.1b | Sí, se arregla en D.1b. Es un bug latente que puede sellar un ciclo como `failed`. |
| P3 | C.2a borra el fallback de `ads_optimizer_goal.updated_at` en el replay y baja la cobertura histórica de C.2. ¿Se acepta, o se vuelve a medir C.2 antes? | C.2a | Se acepta sin volver a medir C.2. Ese fallback no era durable. |
| P4 | C.2a congela solo hojas (keyword y product_target). ¿Está bien? | C.2a | Sí, solo hojas. Ningún consumidor mide `ad_group`. |

## Sigue estas reglas en las tres tareas

- Crea cada rama desde `origin/master` recién traído. Un PR por tarea.
- Escribe primero la prueba que falla. Después el código.
- Corre las pruebas focalizadas con Postgres real, `ruff check`,
  `ruff format --check` y `pre-commit run --all-files`. Nunca uses `--no-verify`.
- Corre la batería completa una vez sobre el SHA final con
  `gh workflow run quality.yml --ref <rama>`. En un PR, el job `completa` sale
  `skipped`, así que ese run es el único que la ejecuta.
- Espera a que terminen `gate` y `review`. La protección de `master` no deja
  mergear antes. Corrige en el mismo PR todo hallazgo `High` o `Critical` del
  revisor. Anota `Medium` y `Low` en la descripción como residuales, sin otra
  ronda.
- No toques producción. El dueño despliega cada tarea aparte.

## Por qué este orden

D.2 va primero porque es la única con fecha: tiene que estar en producción
antes del ~12-oct, cuando podría llegar la primera inversión de un bid en
live. Además no lleva migración, así que no compite por números.

D.1b va segunda y toma la migración `0045`. C.2a va al final, toma `0046` y
es la que más cadenas de migraciones de tests toca.

D.2 y C.2a cambian la misma función, `_procesa_decisora`
(`app/cycle.py:1457`), pero en bloques distintos. C.2a captura el target donde
hoy se calcula (`cycle.py:~1542-1545`), antes de `decide_bid` (`~1550`). D.2
agrega su `return` después del cooldown de B.2 (`~1573-1579`). Por eso no
chocan en cualquier orden de merge.

## D.2: frena la inversión de un bid sin 10 días de evidencia

Resultado: una hoja con un BID aplicado no recibe un BID en la dirección
contraria hasta que la ventana de bids tenga 10 días de métrica posteriores
al cambio. Si no los tiene, la hoja se salta con `inversion_sin_evidencia`.

1. Trae la decisión del dueño con `git cherry-pick 2b0b4e7` (rama local
   `docs/ads-d2-decision-n10`). Ese commit trae
   `docs/evidencia/ads-proteccion-01/D.2/decision.md` con el literal
   "N = 10". Si choca con `master`, conserva el texto de la decisión.
2. En `app/optimizer/goals.py`, junto a `COOLDOWN` y `_SQL_EN_COOLDOWN`
   (`goals.py:79`), agrega:
   - `DIAS_EVIDENCIA_INVERSION = 10` y `POLITICA_INVERSION = "inversion_n10_v1"`.
   - Tres tipos para la historia del último bid: `SinHistoriaBid`,
     `HistoriaBidRota` y `UltimoBidAplicado(direccion, fecha_cambio)`. La
     dirección solo vale `-1` o `1`.
   - `ultimo_bid_aplicado(conn, ad_entity_id)`, la única función que consulta
     la base. Usa los mismos filtros que `_SQL_EN_COOLDOWN`: `verify_ok IS TRUE`
     y ciclo aplicador `live`. Convierte `confirmed_at` a fecha UTC en Python.
   - `permite_reversa_bid(historia, *, nueva_direccion, fin_ventana_bids)`,
     una función pura que decide.
3. En `_procesa_decisora`, después del cooldown de B.2 y del no-op, y solo si
   `resultado.kind == "bid"`, llama a las dos funciones. Si la reversa no
   está permitida, suma `MOTIVO_INVERSION_SIN_EVIDENCIA` en
   `contadores.skips_entidad` (`cycle.py:542`) y sal con `return`. Define el
   motivo junto a `MOTIVO_COOLDOWN_7D` (`cycle.py:239`).
4. Guarda `inputs.inversion_policy_version = POLITICA_INVERSION` en cada
   decisión de bid que pase.
5. Agrega una línea a `docs/CONTEXTO.md`, en la sección del cooldown, con la
   regla y su razón.
6. Mide septiembre con el replay existente: cuenta cuántas inversiones
   frenaría N = 10 y cuántas N = 7, incluido el caso 3835. Escribe el
   resultado en `docs/evidencia/ads-proteccion-01/D.2/replay.md`. No afirmes
   ahorro.

Estas pruebas tienen que fallar antes del cambio:

| Prueba | Mutante que mata |
| --- | --- |
| Caso 3835: subida aplicada en D. Con `fin_ventana_bids` D+9 no se emite la bajada. Con D+10 sí. | `>` en vez de `>=`, o contar días de reloj |
| Misma dirección con D+2 se emite. | Bloquear también la misma dirección |
| PAUSE y no-op no hacen la consulta (espía sobre `ultimo_bid_aplicado`). | Tocar PAUSE |
| Sin bid aplicado previo se emite. | Tratar "sin historia" como bloqueo |
| Historia rota o `fin_ventana_bids` en `None` bloquean. | `None` que deja pasar la inversión |
| Solo cuenta el bid aplicado más reciente. | Tomar el primero |
| `verify_ok` en `NULL` o `FALSE`, y ciclo shadow, no cuentan como aplicados. | Contar applies no verificados |
| `confirmed_at` cerca de medianoche con otra zona horaria de sesión da la fecha UTC correcta. | `::date` en SQL con la zona de la sesión |

El harness de B.2 (`tests/test_cycle_pause_cooldown.py`) pasa
`conn=object()`. Parchea `ultimo_bid_aplicado` como ya parchea `en_cooldown`.

Deploy: solo código, sin migración y sin flag. La regla solo quita
decisiones y falla cerrada.

## D.1b: registra el choque de clave y cierra los residuales de D.1

Resultado: ninguna decisión live queda huérfana por un choque de clave,
`perdida` deja de duplicar el desenlace de la cola y el ciclo ya no puede
caerse en el merge de evidencia económica.

1. En el `except psycopg.errors.UniqueViolation` de `encola_cortes`
   (`app/apply_cola.py:~561`), llama a
   `registra_sin_aplicar(conn, dec_id, cycle_id, MOTIVO_CHOQUE_CLAVE,
   detalle={...})` con `kind`, `entidad` y `termino`. Hazlo fuera del
   savepoint por fila (`apply_cola.py:530-565`) y solo si
   `modo_envelope == "live"`. Agrega `MOTIVO_CHOQUE_CLAVE = "choque_clave"` al
   final de `MOTIVOS_SIN_APLICAR` en `app/apply.py`.
2. Borra el `_registra` de `perdida` en `libera_vencidos`
   (`apply_cola.py:1183-1185`). Deja `perdida` en `MOTIVOS_SIN_APLICAR` y en el
   CHECK, porque ya puede haber filas. Invierte la prueba que la fija
   (`tests/test_apply_harvest.py:2513-2557`): ahora espera cero filas y la cola
   en `vetoed`.
3. Crea `migrations/0045_sin_aplicar_choque_clave.sql`. El CHECK de `0044` no
   tiene nombre. Búscalo en `pg_constraint`, aborta si no hay exactamente uno y
   créalo de nuevo con el nombre `decision_sin_aplicar_motivo_check` y la lista
   ampliada. Todo dentro de `BEGIN; ... COMMIT;`.
4. Agrega a `tests/test_apply_schema.py` un helper
   `_ultima_migracion_con(marcador)` que devuelva la última migración que
   define el CHECK o la vista. Úsalo en los dos tests espejo (`~877` y `~907`) y
   en un test nuevo que compare los kinds de `v_decision_huerfana` con
   `KINDS_QUOTA`. Agrega `0045` a las cadenas de migraciones que hoy aplican
   `0044`, y `0044`+`0045` a la de `tests/test_cycle.py`.
5. En `_mezcla_evidencias_persistidas`
   (`cycle.py:1153`), convierte `destino` a lista antes de agregar. En el
   camino de éxito, `destino` es una tupla (`cycle.py:2116-2117`). El dueño
   aprobó este arreglo (P2).
6. Agrega a `docs/DATABASE.md` las fichas de `decision_sin_aplicar` y
   `v_decision_huerfana`. Documenta `perdida` y `choque_clave` en
   `docs/APPLY.md` §4.3.

Estas pruebas tienen que fallar antes del cambio:

| Prueba | Mutante que mata |
| --- | --- |
| Dos cortes del mismo término en un ciclo live dejan una fila `choque_clave` y la vista no lista la decisión. La transacción sigue usable. | Registrar dentro del savepoint abortado |
| El mismo choque en un ciclo shadow no escribe nada. | Registrar en shadow |
| Claim de harvest perdido contra un veto deja cero filas. | Seguir escribiendo `perdida` |
| El CHECK vigente acepta `choque_clave` y coincide con la constante. | Constante y CHECK desalineados |
| Kinds de la vista igual a `KINDS_QUOTA`. | Agregar un kind aplicable sin tocar la vista |
| Unitario de `_mezcla_evidencias_persistidas` con `destino` tupla (sin base). | El `append` sobre una tupla |
| Camino de éxito completo: una sola entrada por `decision_id`. | Quitar el filtro de `vistos` en `cycle.py:~1183` |

Deploy: el dueño aplica `0045` y después el código, con el mismo script del
deploy de D.1.

## C.2a: congela el target de cada hoja en cada ciclo

Resultado: el replay de un ciclo cerrado da el mismo target aunque después
alguien edite el goal, y ya no depende de `ads_optimizer_goal.updated_at`.

1. Crea `migrations/0046_target_acos_ciclo.sql` con la tabla
   `target_acos_ciclo`. Tiene las columnas `cycle_id`, `ad_entity_id`,
   `decided_at`, `target_acos_pct NUMERIC` sin escala y `procedencia` con un
   CHECK igual a `PELDANOS_CASCADA` (`goals.py:267`). La llave primaria es
   `(cycle_id, ad_entity_id)`. Da solo `INSERT` y `SELECT` a los roles de la
   app.
2. En `_procesa_decisora`, justo después de `cascada_target_acos` y
   `peldano_target_acos` (`cycle.py:~1542-1546`), guarda
   `(entidad, target, procedencia)` en un campo nuevo de `_Contadores`
   (`cycle.py:538`). No subas el cálculo por encima del veto ni de la salida
   por inerte: `cascada_target_acos` falla con un cache menor o igual a cero y
   metería fallas nuevas.
3. Escribe esas filas en TX3, junto a `_inserta_decisiones` (`cycle.py:1103`),
   con `ON CONFLICT DO NOTHING`.
4. En `tools/replay_ads_economico.py`, lee el target de la tabla nueva. Borra
   la consulta a `ads_optimizer_goal` (`:134-139`) y la rama
   `goal_estable_y_freeze_plataforma` (`:176-183`). Los ciclos anteriores a
   `0046` sin decisión quedan `sin_target_historico`.
5. Agrega `0046` a las cadenas de migraciones de los tests que corren
   `corre_ciclo`.

Estas pruebas tienen que fallar antes del cambio:

| Prueba | Mutante que mata |
| --- | --- |
| Una hoja en no-op y una en cooldown dejan su fila de target. | Congelar solo las hojas con decisión |
| Editar el goal después del ciclo no cambia el replay. | Leer el goal vigente |
| `decision.inputs.target_acos_pct_usado` es igual al valor de la tabla. | Dos fuentes que divergen |
| La app no puede hacer `UPDATE` ni `DELETE` en la tabla. | Tabla mutable |
| Un ciclo sin decisiones anterior a `0046` sale `sin_target_historico`. | Rellenar con el goal actual |

Deploy: el dueño aplica `0046` y después el código.

## Cierra cada tarea

Una tarea está cerrada cuando su PR tiene la batería completa en verde, está
mergeado y desplegado, y su evidencia está en
`docs/evidencia/ads-proteccion-01/<tarea>/`. Marca entonces su fila del plan
como `cc:完了` y agrega una entrada corta a `docs/CHAT-CONTEXT.md`. El
candado de CI lo exige.
