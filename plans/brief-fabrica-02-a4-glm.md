# Brief para GLM: FABRICA 02 (F2) — tarea A.4

Base `origin/master` `8271c7f` (A.0, A.1, A.2, A.3a y A.3 cerradas; tras
`git fetch` usa el HEAD vigente). Rama desde `origin/master`, **jamás** desde
tu master local. **Una sola tarea: A.4, biblioteca escrita por el motor.**

Contrato: fila A.4 de `plans/fabrica-02.md`, sección «Biblioteca» del diseño
del plan, §6 y §7 (decisiones 5 y 7) del spec de FABRICA, migración 0038
(bloque (e) y (f)) y las decisiones 1, 5 y 7 del dueño. Este brief es
ejecutable y subordinado a esos documentos; donde precisa la letra de la fila
A.4, lo dice explícitamente y manda.

## Resultado exacto

Cuando el motor aplica de verdad un término en campañas de un grupo, la
biblioteca de ese `tipo_producto` lo aprende sola:

1. cada harvest **de grupo** confirmado por readback deja su término en
   `keyword_biblioteca`;
2. cada decisión `kind = negative` **aplicada** en una campaña de grupo deja
   su término en `negative_biblioteca`;
3. la escritura vive en un SAVEPOINT dentro de la transacción del sello y
   **jamás** bloquea ni degrada el sello: si falla, el harvest/negative queda
   igual de aplicado, con rastro durable del fallo y una alerta veraz;
4. solo se escriben palabras: `texto` normalizado, `origen` y fechas. Las
   columnas `cost`, `revenue` y `moneda` quedan NULL en toda fila que escriba
   el motor y `orders` se queda en su default;
5. los negativos que nacen de un harvest (origen y hermanas) **nunca** entran
   a `negative_biblioteca`: son ruteo, no exclusión;
6. el statement que ejecuta el motor es **idéntico** al que 0038 selló bajo
   `SET ROLE app_decide`.

Sin grupo (harvest por excepción o por terna, negative en una campaña que no
está en `campana_grupo_rol`) no hay `tipo_producto` que inventar: no se
escribe nada y no es error.

## Antes de escribir una línea

Lee completos, en este orden:

1. `docs/CONTEXTO.md`, reglas 1–10.
2. `plans/fabrica-02.md`: «Resultado y límites», la sección **Biblioteca**
   del diseño, fila A.4, propiedad de archivos (fila A.4), AC5/AC6/AC8/AC10 y
   «Decisiones del dueño».
3. `docs/superpowers/specs/2026-09-05-fabrica-campanas-grupos-design.md`
   §6 y §7 (decisiones 5 y 7).
4. `migrations/0038_fabrica_hermanas_biblioteca.sql`, bloques (e) y (f)
   completos, con sus comentarios: ahí está sellado el statement.
5. `tests/test_fabrica_0038.py`: `SQL_BIBLIOTECA_KEYWORD`,
   `SQL_BIBLIOTECA_NEGATIVE`, `test_0038_canon_biblioteca_en_do` y
   `test_0038_app_decide_bibliotecas_statement_literal`.
6. `app/apply_harvest.py`: `_Contexto`, `_nace_job` (patrón SAVEPOINT),
   `_avanza`, `_paso_readback` (el sello del evento de valor de un harvest de
   grupo: la transacción que deja resumen confirmado, cola `applied`, fase
   `hermanas_negadas` y roster), `_cierra_o_sigue` y `_paso_hermanas`.
7. `app/apply_cola.py`: `_ejecuta_negative` (la transacción que sella
   `applied` de un negative) y `FilaCola`.
8. `app/apply_harvest_reconciliacion.py`: `_reconcilia_negativas` (la rama
   `propio is not None` que confirma un negative huérfano por identidad).
9. `app/optimizer/hygiene.py`: `_normaliza_texto` (strip + casefold; la misma
   normalización del dedupe de harvest).
10. `app/notifica.py`: `AlertaHarvest` por duck typing,
    `alerta_harvest_hermanas` / `notifica_harvest_hermanas` como patrón de
    sender fail-silent.
11. `tests/test_fabrica_f2.py` (`db_f2`, `_semilla_grupo`),
    `tests/test_fabrica_f2_hermanas.py` (`_grupo_listo`,
    `_corre_harvest_grupo`, `_job_de`, `_reconcilia`,
    `test_ciclo_3_cierra_done_con_pendientes_y_alerta_veraz`,
    `test_terna_sin_grupo_cierra_como_hoy`, `test_grupo_en_shadow_cero_jobs`),
    `tests/test_apply_cola.py` (`_semilla`, `_encola_fila`, `_handler_cortes`,
    `_aplicador`, `test_negative_se_aplica_y_reversa_negative_delete_exenta_de_quota`)
    y `tests/test_apply_harvest.py`
    (`test_matriz_applying_huerfano_negative_confirmado_por_identidad`,
    `test_harvest_vetado_jamas_crea_harvest_job`).
12. `tests/test_architecture.py`: presupuesto de tamaño y allowlist. No
    aumentes el allowlist ni el tamaño de módulos ya listados.

El repo ya tiene baseline de Ruff, pre-commit y CI. Antes del primer rojo
registra con un DSN de prueba real:

```bash
ORBIT_TEST_DSN=<dsn-test> uv run --frozen python -m pytest -q \
  tests/test_fabrica_f2_hermanas.py tests/test_fabrica_f2.py \
  tests/test_apply_harvest.py tests/test_apply_cola.py \
  tests/test_fabrica_0038.py tests/test_architecture.py
```

Anota el conteo exacto como baseline (`N passed, 0 skipped`). Sin DSN, un
verde con skips no sirve como evidencia.

## Decisiones cerradas

### Un módulo nuevo y delgado: `app/biblioteca.py`

Toda la lógica de biblioteca vive en un módulo nuevo `app/biblioteca.py`,
sin I/O propio de red y sin importar `apply`, `apply_cola` ni
`apply_harvest` (evita ciclos y no engorda módulos ya en allowlist de
tamaño). Solo importa `psycopg`, `app.optimizer.hygiene` (normalización) y
`app.notifica`. Superficie mínima:

```python
SQL_BIBLIOTECA_KEYWORD: str    # idéntico al canon de tests/test_fabrica_0038.py
SQL_BIBLIOTECA_NEGATIVE: str   # idéntico al canon de tests/test_fabrica_0038.py
MOTIVO_BIBLIOTECA_FALLO = "biblioteca_no_escrita"
MOTIVO_TERMINO_EN_KEYWORD_BIBLIOTECA = "termino_en_keyword_biblioteca"

def grupo_de_ad_group(conn, ad_group_ad_entity_id: int) -> tuple[int, str, int] | None
    # (grupo_id, tipo_producto, campaign_ad_entity_id) vía campana_grupo_rol
    # por ad_group_ad_entity_id o por ad_entity.parent_id; None si no está en grupo.

def registra_keyword(conn, *, grupo_id, tipo_producto, platform, texto, origen) -> dict
def registra_negative(conn, *, grupo_id, tipo_producto, platform, texto, origen) -> dict
```

`registra_*` reciben la conexión **ya dentro** de la transacción del sello,
abren el SAVEPOINT (`with conn.transaction():`, el mismo patrón CX2 de
`_nace_job`), ejecutan el statement canónico y devuelven un dict de rastro
(abajo). Capturan `Exception` —no solo `psycopg.Error`— con `# noqa: BLE001`
y comentario: el sello del evento de valor jamás puede abortar por la
contabilidad derivada (una keyword ya creada en Amazon sin sello sería peor
que una fila de biblioteca perdida). Registran el fallo con `scrub` en el
log y emiten la alerta; nunca levantan.

Mueve las dos constantes canónicas de `tests/test_fabrica_0038.py` a
`app/biblioteca.py` y haz que el test las **importe** desde `app` (una sola
fuente). Endurece el test de fragmentos: además de la subcadena, el statement
de keyword de `app/` debe coincidir con el del DO de 0038 **columna por
columna, `ON CONFLICT`, `SET` y `RETURNING id`** (modulo `%s` vs variables
PL). Si tu statement difiere del DO en cualquier fragmento, 0038 ya no lo
cubre y hay que decirlo en el PR: la salida correcta es igualarlo, no
declararlo.

### Normalización y `origen`

`texto` = `hygiene._normaliza_texto(search_term)` (strip + casefold). No
dupliques la función; si prefieres una alias pública `normaliza_texto` en
`hygiene.py`, agrégala sin cambiar comportamiento ni tocar `decide_hygiene`.
El `texto` que se escribe es el normalizado, no el crudo del `search_term`
ni el `keywordText` del payload.

`origen` (TEXT, informativo, sin PII):

- keyword: `grupo:<grupo_id>/campana:<campaign_ad_entity_id>/harvest:<job_id>`
- negative: `grupo:<grupo_id>/campana:<campaign_ad_entity_id>/decision:<decision_id>`

Los ids son internos de Orbit (`ad_entity.id`, `harvest_job.id`,
`decision.id`), no externos de Amazon. Si la fila ya existe, `origen` no se
pisa: el `ON CONFLICT` solo mueve `updated_at` (keyword) o no hace nada
(negative); ese es el contrato de 0038 y de los GRANTs por columna.

### Keyword: se escribe en el sello del evento de valor (precisión del lead)

La fila A.4 dice «al sellar `done`». Precisión que manda: para un harvest de
grupo, el momento «aplicado y leído de vuelta» es el sello del evento de
valor en `_paso_readback` (la transacción que confirma el resumen, deja la
cola `applied` y avanza a `hermanas_negadas` con el roster). Ahí hay
`_Contexto` con `grupo_id`, y desde ahí el job **ya no puede** volverse
`failed` (A.3): `done` es solo el final de la higiene. La escritura de
`keyword_biblioteca` va dentro de **esa** transacción, en SAVEPOINT, después
de `_confirma_resumen` y `_termina_cola`. Consecuencias probadas:

- una segunda conexión ve la fila de biblioteca en cuanto ve la cola
  `applied` y la fase `hermanas_negadas`, antes del primer POST de hermana;
- al llegar a `done` (limpio, por tope de ciclos o con pendientes) la fila
  existe y **no se escribe otra vez** (cero statements de biblioteca en
  `_paso_hermanas` y `_cierra_o_sigue`);
- la reconciliación que retoma un job en `hermanas_negadas` no escribe
  biblioteca: el sello ya la escribió;
- `failed` antes del readback (keyword ausente, señuelo, roster imposible)
  → cero filas; excepción/terna (`resuelto_por != grupo`) → cero filas;
  vetado, `shadow` y `perdida` → cero filas.

`tipo_producto` se lee de `campana_grupo` por `ctx.grupo_id` (jamás por
nombre de campaña). `campaign_ad_entity_id` del `origen` es `job.ad_entity_id`
(la campaña de origen del harvest), no la exacta destino.

### Negative: dos sitios reales, la misma función

`negative_biblioteca` recibe SOLO decisiones `kind = negative` confirmadas
como `applied`, en los dos sitios que hoy existen y sin abrir un tercero:

1. `apply_cola._ejecuta_negative`: dentro del `with conn.transaction():` que
   sella ledger + resumen + cola `applied`, **solo** cuando `verify` es
   True. Un 2xx sin id (`fallo:ack_sin_id`) no escribe.
2. `apply_harvest_reconciliacion._reconcilia_negativas`: dentro de la
   transacción de la rama `propio is not None` (confirmación por identidad).
   Las ramas señuelo, ausente/reintento y `ancestro_no_enabled` no escriben.

En ambos, el grupo se resuelve con `biblioteca.grupo_de_ad_group(conn,
fila.ad_entity_id)` (la fila de un negative ES el ad group). Sin grupo →
`None` → cero statements y cero alerta (no es fallo). El `texto` es el
`search_term` de la fila normalizado.

Los negativos de harvest (el del origen en `_paso_negative` y las hermanas en
`_paso_hermanas`) no pasan por ninguno de esos dos sitios y **no** deben
ganar uno: un mutante que llame `registra_negative` desde `apply_harvest.py`
tiene que morir.

### Precedencia entre bibliotecas (invariante de intersección vacía)

El motor garantiza la intersección vacía **en el momento de escribir**:

- `registra_negative` consulta primero `keyword_biblioteca` por
  `(tipo_producto, platform, texto)`; si el término ya es keyword de ese
  tipo de producto, **no inserta** y devuelve
  `{"escrita": false, "motivo": "termino_en_keyword_biblioteca"}` sin alerta
  (un término que ya vendió en un grupo no se enseña como exclusión al
  siguiente).
- `registra_keyword` inserta siempre. Si existe una fila en
  `negative_biblioteca` con la misma clave, la deja (app_decide no tiene
  `DELETE` por diseño de 0038) y devuelve además `"conflicto_negative": true`.
  Ese caso queda visible en el rastro y se declara como residual para R.1 y
  para el consumidor (la siembra de F1): **keyword gana** cuando ambas
  existen. No lo resuelvas en A.4 con un DELETE, un GRANT nuevo ni un
  UPDATE.

El test de invariante (d) prueba las dos direcciones: harvest de grupo →
cero filas en `negative_biblioteca` para ese término, y negative aplicado
sobre un término que ya está en `keyword_biblioteca` → cero filas nuevas
con el motivo en el rastro.

### Rastro durable y alerta

El resultado de cada `registra_*` es un dict pequeño y estable:

```json
{"escrita": true, "id": 123}
{"escrita": false, "motivo": "biblioteca_no_escrita", "detalle": "<clase de excepción>"}
{"escrita": false, "motivo": "termino_en_keyword_biblioteca"}
```

- Harvest: se guarda en `harvest_job.external_ids["biblioteca"]` con
  `_avanza(conn, job, None, {"biblioteca": rastro})` en la misma transacción
  del sello (después del SAVEPOINT, ya fuera de él). Es una clave nueva y
  aditiva: no cambia ninguna clave del contrato A.3 (`hermanas_objetivo`,
  `hermanas`, `hermanas_ciclos`, `hermanas_pendientes`, `keyword_id`).
- Negative: no hay job. El rastro va en el `resultado` del ledger **solo si
  falla**: `ok` sigue siendo `ok`; el fallo de biblioteca no cambia el
  veredicto del negative. Sella en la misma transacción una nota
  `resultado = "ok|biblioteca_no_escrita"` o, si prefieres no tocar el
  formato del ledger, escribe el fallo en el log con `scrub` y déjalo dicho
  en el PR. Elige una y pruébala.

Ante `escrita = false` por excepción, `registra_*` emite una alerta por
`app/notifica.py` con un sender nuevo `notifica_biblioteca_no_escrita`
(mismo contrato fail-silent que `notifica_harvest_hermanas`: canal apagado
→ True; excepción → warning con scrub + False; jamás levanta). El texto
dice que el harvest/negative **sí** quedó aplicado y que la biblioteca no
aprendió el término; nunca «failed». La alerta no viaja en el tuple de
`_paso_readback` (no cambies ese contrato ni el de `ResultadoHarvest`): sale
en el punto del fallo, como `_falla_job`, y el rastro durable es la
visibilidad de respaldo si el canal falla.

### Sin dinero, por construcción y por test

Las columnas `cost`, `revenue` y `moneda` no aparecen en ningún statement
del motor; `orders` tampoco (se queda en su `DEFAULT 0` de 0018). El test
(g) asierta las dos cosas: el texto de `SQL_BIBLIOTECA_KEYWORD` no menciona
esas columnas, y la fila leída de vuelta las trae NULL. Un mutante que
agregue `cost` o `moneda` al INSERT muere aunque el GRANT lo deje pasar
(0038 lo advierte: el «sin dinero» lo garantiza la app, no el esquema).

### Rol real

El motor corre como `app_decide`. El test (f) ejecuta **las funciones de
`app/biblioteca.py`** (no el SQL a mano) bajo `SET ROLE app_decide` en el
fixture `db_f2` (que ya aplica 0038): insertan, actualizan `updated_at` en
la segunda llamada con el mismo término, y `DELETE` / `UPDATE origen` bajo
ese rol truenan con `InsufficientPrivilege`. `RESET ROLE` al terminar.

## Archivos y fronteras

Cambios esperados:

- `app/biblioteca.py` (nuevo): constantes canónicas, normalización,
  resolución de grupo, `registra_keyword`, `registra_negative`.
- `app/apply_harvest.py`: una llamada en el sello del evento de valor de
  `_paso_readback` y el rastro en `external_ids["biblioteca"]`. Nada en
  `_paso_hermanas`, `_cierra_o_sigue`, `_paso_negative` ni en la reversa.
- `app/apply_cola.py`: una llamada en `_ejecuta_negative` cuando `verify`.
- `app/apply_harvest_reconciliacion.py`: una llamada en la rama de
  confirmación de `_reconcilia_negativas`.
- `app/notifica.py`: `alerta_biblioteca_no_escrita` +
  `notifica_biblioteca_no_escrita`.
- `app/optimizer/hygiene.py`: solo si expones la alias pública de
  normalización.
- `tests/test_fabrica_f2_biblioteca.py` (nuevo): todo el comportamiento de
  A.4 sobre `db_f2`, reutilizando `_grupo_listo`, `_corre_harvest_grupo`,
  `_job_de` y `_reconcilia` de `test_fabrica_f2_hermanas.py` y `_semilla`,
  `_encola_fila`, `_handler_cortes`, `_aplicador` de `test_apply_cola.py`.
- `tests/test_fabrica_0038.py`: importa las constantes desde `app` y
  endurece el cruce con el DO.
- `tests/test_notifica.py`: texto y envío del sender nuevo.
- `tests/test_architecture.py`: solo si necesitas declarar que
  `app/biblioteca.py` no importa `app.ads.write` ni módulos de apply (un
  candado nuevo con razón escrita es bienvenido; el allowlist de tamaño no
  crece).

No cambies migraciones, GRANTs, `app/ads/write.py`, verbos HTTP, la máquina
de fases de `harvest_job`, el shape de `hermanas_*`, `tools/`, la
visibilidad de A.6, tracker, `plans/ROADMAP.md` ni deploy. No escribas
`harvest_excepcion`. No toques `decide_hygiene`. No arregles en A.4 el
residual ADV-10 de A.3 ni los dos hilos CodeRabbit menores del PR #267
(`test_plan_precondiciones_fallan_cerrado` sin discriminar ramas; DSN fijo
en tres tests del CLI): quedan para R.1, decláralos en el PR.

## Orden TDD obligatorio

En cada bloque: demuestra el rojo contra el código anterior (regla 9),
implementa el mínimo verde y ejecuta solo los archivos focales.

### 1. Canon y módulo

Rojo: `from app.biblioteca import SQL_BIBLIOTECA_KEYWORD, SQL_BIBLIOTECA_NEGATIVE`
falla; el cruce endurecido contra el DO de 0038 falla si cambias un
fragmento. Verde: módulo con las constantes movidas; `test_fabrica_0038.py`
importándolas.

### 2. Keyword en el sello

Rojos: harvest de grupo hasta `done` → fila con `tipo_producto` del grupo,
`texto` normalizado (siembra el término con mayúsculas y espacios
sobrantes), `origen` exacto, `cost/revenue/moneda` NULL; segunda conexión ve
la fila con la cola `applied` y fase `hermanas_negadas` antes del primer
POST de hermana; mismo término harvesteado otra vez (otro job, mismo
grupo) → una sola fila con `updated_at` movido; `external_ids["biblioteca"]`
con `escrita: true` e `id`; `done` por tope de ciclos y `done` limpio no
duplican ni reescriben; reconciliación de un job retomado en
`hermanas_negadas` no escribe.

### 3. Cero filas donde no toca

Rojos: excepción/terna (`test_terna_sin_grupo_cierra_como_hoy` extendido)
→ cero filas; keyword ausente en destino → `failed` y cero filas; vetado y
`shadow` → cero filas; el negativo de origen y las hermanas del harvest →
cero filas en `negative_biblioteca` (invariante (d), dirección harvest).

### 4. Negative en cola y en reconciliación

Rojos: negative `kind = negative` aplicado por `libera_vencidos` en una
campaña de grupo → fila en `negative_biblioteca` con `origen` de decisión;
el mismo negative en una campaña sin grupo → cero filas y cero alerta; 2xx
sin id → cero filas; negative huérfano `applying` confirmado por identidad
en `_reconcilia_negativas` → fila; señuelo y ausente → cero filas; término
ya en `keyword_biblioteca` → cero filas nuevas y motivo
`termino_en_keyword_biblioteca` (invariante (d), dirección negative).

### 5. Fallo inyectado y rol

Rojos: `monkeypatch` que hace fallar el statement de keyword (por ejemplo
`SQL_BIBLIOTECA_KEYWORD` roto o una excepción en `registra_keyword`) → el
job sella igual, cola `applied`, resumen confirmado, fase `hermanas_negadas`,
`external_ids["biblioteca"]` con `escrita: false` y motivo, y el sender
nuevo llamado una vez con texto que no dice «failed»; lo mismo para
negative en cola (veredicto `applied` intacto). Bajo `SET ROLE app_decide`,
`registra_keyword` y `registra_negative` escriben; `DELETE` y
`UPDATE origen` truenan.

## Mutantes que deben morir

Como mínimo:

- escribir la keyword en `_cierra_o_sigue` o `_paso_hermanas` en vez del
  sello del evento de valor (la segunda conexión no la ve antes de las
  hermanas);
- escribir la keyword para `resuelto_por != grupo`;
- escribir la keyword en el camino `failed` antes del readback;
- escribir `negative_biblioteca` desde `_paso_negative` o `_paso_hermanas`;
- escribir un negative con `verify == False` o en la rama señuelo/ausente;
- resolver el grupo por nombre de campaña o por `parent_id` sin cruzar
  `campana_grupo_rol`;
- escribir `texto` sin normalizar;
- pisar `origen` en el conflicto (`DO UPDATE SET origen = ...`);
- agregar `cost`, `revenue`, `moneda` u `orders` al INSERT;
- quitar el SAVEPOINT (un fallo de biblioteca aborta el sello);
- capturar la excepción pero omitir el rastro o la alerta;
- insertar en `negative_biblioteca` un término que ya está en
  `keyword_biblioteca`;
- cambiar cualquier fragmento del statement canónico (el cruce con el DO
  debe caer).

## DoD binario

1. `app/biblioteca.py` existe, expone las constantes canónicas idénticas al
   DO de 0038, y `tests/test_fabrica_0038.py` las importa desde `app`.
2. Un harvest de grupo confirmado por readback deja exactamente una fila en
   `keyword_biblioteca` con `tipo_producto` del grupo, `texto` normalizado,
   `origen` con grupo/campaña/job y dinero NULL, visible desde otra conexión
   antes del primer POST de hermana; repetir el término mueve `updated_at`
   sin duplicar.
3. `failed`, excepción/terna, vetado, `shadow` y `perdida` dejan cero filas.
4. Un negative `kind = negative` aplicado (cola y reconciliación) en campaña
   de grupo deja exactamente una fila en `negative_biblioteca`; sin grupo,
   sin id en el ack, señuelo o ausente, cero filas.
5. Ningún negativo de harvest (origen ni hermanas) entra a
   `negative_biblioteca`; un negative sobre un término ya en
   `keyword_biblioteca` no entra y deja motivo.
6. Un fallo inyectado en la biblioteca deja el sello intacto (job, cola,
   resumen, fase), rastro durable `escrita: false` y una alerta veraz por el
   sender nuevo; el veredicto del negative no cambia.
7. Bajo `SET ROLE app_decide` las funciones del motor escriben y actualizan;
   `DELETE` y `UPDATE origen` truenan.
8. Ningún statement del motor menciona `cost`, `revenue`, `moneda` ni
   `orders`; toda fila escrita las trae NULL / default.
9. Ningún módulo ya en allowlist de tamaño crece por A.4; `app/biblioteca.py`
   no importa `app.ads.write` ni módulos de apply; `test_architecture.py`
   verde.
10. Rojos previos y mutantes documentados; focales, Ruff y pre-commit pasan;
    la batería completa pasa una sola vez en CI del PR.

## Verificación y entrega

Durante implementación:

```bash
ORBIT_TEST_DSN=<dsn-test> uv run --frozen python -m pytest -q \
  tests/test_fabrica_f2_biblioteca.py tests/test_fabrica_0038.py
ORBIT_TEST_DSN=<dsn-test> uv run --frozen python -m pytest -q \
  tests/test_fabrica_f2_hermanas.py tests/test_apply_cola.py \
  tests/test_apply_harvest.py tests/test_notifica.py
uv run --frozen python -m pytest -q tests/test_architecture.py
uv run --frozen ruff check app/biblioteca.py app/apply_harvest.py \
  app/apply_cola.py app/apply_harvest_reconciliacion.py app/notifica.py \
  tests/test_fabrica_f2_biblioteca.py tests/test_fabrica_0038.py \
  tests/test_notifica.py
uv run --frozen ruff format --check app/biblioteca.py app/apply_harvest.py \
  app/apply_cola.py app/apply_harvest_reconciliacion.py app/notifica.py \
  tests/test_fabrica_f2_biblioteca.py tests/test_fabrica_0038.py \
  tests/test_notifica.py
pre-commit run --all-files
```

Antes del PR, `git log origin/master..HEAD` debe mostrar solo los commits de
A.4. Abre PR a `master`; la batería completa corre **una sola vez en CI**
sobre el SHA final. No la repitas localmente si CI ya la validó.

La descripción del PR incluye: baseline y rojos por bloque, resultados
focales sin skips, el statement canónico y la prueba de que coincide con el
DO, el shape de `external_ids["biblioteca"]`, la prueba de la segunda
conexión, la matriz de cero filas, la prueba de fallo inyectado, la prueba
bajo `app_decide`, el log de commits, residuales (incluida la precedencia
keyword > negative cuando ambas existen) y el enlace al CI verde.

Cero producción, SSH, secretos o Amazon en A.4 (la biblioteca no habla con
Amazon; todo es Postgres). La revisión del lead se agrupa en una sola ronda
por bloque; una segunda solo si la primera encuentra severidad alta y nunca
una tercera.
