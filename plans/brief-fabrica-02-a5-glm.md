# Brief para GLM: FABRICA 02 (F2) — tarea A.5

Base `origin/master` (A.0–A.4 cerradas; tras `git fetch` usa el HEAD vigente:
el código de A.4 vive en el PR #269, en cola de merge — si al cortar la rama
todavía no está en master, **corta desde `origin/feat/fabrica-02-a4`** y dilo
en el PR; A.5 no toca los archivos de A.4). Rama desde el remoto, **jamás**
desde tu master local. **Una sola tarea: A.5, `tools/harvest_excepcion.py`.**

Contrato: fila A.5 de `plans/fabrica-02.md`, decisiones 4 y 6 del spec de
FABRICA (§7), hechos 13–14 del plan (E/0.2) y las decisiones 4 y 6 del
dueño. Este brief es ejecutable y subordinado a esos documentos; donde
precisa la letra de la fila A.5, lo dice y manda.

## Resultado exacto

Una herramienta de línea de comandos, **solo Postgres y solo `app_admin`,
cero Amazon**, con dos modos excluyentes, dry-run por defecto y la ceremonia
sellada del repo (`--acepto-mutacion-real --esperado N --huella H --go
"<literal>"`):

1. **`--migrar`**: mete UNA campaña sin grupo en `harvest_excepcion` con un
   destino `(campaña, ad group)` **validado contra `ad_entity`**: el ad group
   existe con `kind = 'ad_group'`, su `parent_id` es la campaña destino, y
   las tres entidades (origen, campaña destino, ad group destino) son de la
   **misma plataforma**. Texto libre rechazado: el par se resuelve a filas
   reales antes de escribir, y se escriben los `external_id` leídos de esas
   filas, no los que tecleó el operador.
2. **`--limpiar-terna`**: pone a NULL la terna `harvest_campaign_id /
   harvest_ad_group_id` (bid intacto) de los goals de campaña de UN grupo,
   **solo** por `goals_write.edita_goal(harvest_limpia_destino=True)`. Cero
   SQL de escritura contra `ads_optimizer_goal` en el tool: el candado de
   escritor único (`tests/test_architecture.py`) ya escanea `tools/`.

Una campaña o un grupo por corrida y por `go`. Todo lo que cambia queda
legible en el dry-run antes, y leído de vuelta después.

## Antes de escribir una línea

Lee completos, en este orden:

1. `docs/CONTEXTO.md`, reglas 1–10.
2. `plans/fabrica-02.md`: «Resultado y límites» (destino del harvest y
   migración de existentes), hechos 10, 13, 14 y 15, sección «Diseño» pasos
   1–4 del resolutor, fila A.5, propiedad de archivos, D.2, AC8 y
   «Decisiones del dueño» 4 y 6.
3. `docs/superpowers/specs/2026-09-05-fabrica-campanas-grupos-design.md`
   §7 (decisiones 4 y 6, pasos 1–3 del destino, transición).
4. `migrations/0018_fabrica_campanas.sql`: tabla `harvest_excepcion`
   (PK `ad_entity_id` → campaña, `destino_*_external` TEXT, `go_literal`),
   trigger `harvest_excepcion_kind` y los GRANTs (INSERT/UPDATE solo
   `app_admin`; `app_decide` solo SELECT).
5. `migrations/0038_fabrica_hermanas_biblioteca.sql`: triggers
   `ads_optimizer_goal_harvest_coherente` (estado 3 «bid-solo» solo con la
   campaña en `campana_grupo_rol`) y `campana_grupo_rol_destino_protegido`.
6. `app/goals_write.py` completo: docstring del módulo, `edita_goal`
   (`updated_at` obligatorio tz-aware; `harvest_limpia_destino=True` pone a
   NULL solo campaign/ad_group, valida scope y membresía DESPUÉS de leer,
   restaura `row_factory`, **hace `conn.commit()` por llamada**),
   `GoalInvalido`, `GoalInexistente`.
7. `app/optimizer/harvest_destino.py` completo: `_Lectura`, `_lee`,
   `decide` (pura), `resolver_destino`, `RESUELTO_*`, `SaltoHarvest`,
   `DestinoHarvest`.
8. `tools/reversa_harvest.py` (el CLI más reciente: `Abortar`, DSN por
   entorno, huella, ceremonia, `main(argv)`, `__main__` con exit 2) y
   `tools/archiva_inertes.py` líneas 236–250, 319–326 y 540–590 (huella
   del conjunto, gate del go, `_dsn_admin`).
9. `tests/test_reversa_harvest.py` (`_carga_tool`, tests de CLI: dry-run,
   ceremonia, `test_tool_no_importa_ni_construye_cliente_escritura`),
   `tests/test_archiva_inertes.py` 459–520 (aborta antes de tocar nada),
   `tests/test_harvest_destino.py` (tests de excepción: cómo se siembra
   `harvest_excepcion`; tests de `harvest_limpia_destino` 425–460, 605–660,
   720–770, 840+), `tests/test_fabrica_f2.py` (`db_f2`, `_semilla_grupo`
   con `con_terna`/`con_goals`), `tests/test_apply_harvest.py` (`_semilla`:
   campaña 7001 / ad group 7101 sin grupo, goal de plataforma con terna).
10. `tests/test_architecture.py`: `test_escritura_de_goals_vive_solo_en_goals_write`,
    `test_patrones_sql_goals_resisten_case_y_whitespace`,
    `ALLOWLIST_IMPORTS_FABRICA_CAMPANAS` + `test_fabrica_campanas_solo_importa_lo_declarado`
    + `test_allowlist_fabrica_caza_import_de_escritura(tmp_path)` (el patrón
    de allowlist positiva de imports de un tool y su negativo con
    `tmp_path`), `PERMITIDOS_IMPORTAR_ADS_WRITE`.

El repo ya tiene baseline de Ruff, pre-commit y CI. Antes del primer rojo
registra con un DSN de prueba real:

```bash
ORBIT_TEST_DSN=<dsn-test> uv run --frozen python -m pytest -q \
  tests/test_harvest_destino.py tests/test_fabrica_f2.py \
  tests/test_goals_write.py tests/test_architecture.py
```

Anota el conteo exacto como baseline (`N passed, 0 skipped`). Sin DSN, un
verde con skips no sirve como evidencia.

## Decisiones cerradas

### Interfaz del tool

```text
tools/harvest_excepcion.py --migrar --plataforma amazon_us --campana <ext> \
    --destino-campana <ext> --destino-ad-group <ext> \
    [--acepto-mutacion-real --esperado 1 --huella H --go "<literal>"]

tools/harvest_excepcion.py --limpiar-terna --grupo <campana_grupo.id> \
    [--acepto-mutacion-real --esperado N --huella H --go "<literal>"]
```

- `--migrar` y `--limpiar-terna` son excluyentes y uno es obligatorio
  (`add_mutually_exclusive_group(required=True)`).
- Los ids de campaña y ad group son **`external_id` de Amazon**, siempre
  con `--plataforma` (un `external_id` solo es único con su plataforma;
  precedente `_huella_conjunto` de `archiva_inertes`). El grupo es el `id`
  interno de `campana_grupo` (es el que muestra el dashboard de fábrica).
- `main(argv=None) -> int` parsea `argv` (los tests lo llaman en proceso,
  patrón `_carga_tool`), `Abortar(RuntimeError)` para todo fallo
  fail-closed, `__main__` imprime `ABORTAR: <motivo>` y sale con 2. Salida
  0 = hecho o nada que hacer. Salida por stdout en texto plano, una línea
  por hecho, última línea `huella: <h>` en el dry-run.

### DSN y rol

Un solo DSN: `ORBIT_DSN_ADMIN` (los INSERT de `harvest_excepcion` y los
UPDATE de goals son de `app_admin` por los GRANTs de 0001/0018). Sin la
variable → `Abortar`. El tool **no** lee `ORBIT_DSN_DECIDE`,
`ORBIT_DSN_READ` ni construye DSN por argumentos. Conexión por
`app.db.connect` (sin autocommit): el dry-run **no escribe**; el go
escribe y confirma en una transacción por unidad (ver abajo).

### `--migrar`: validación y escritura

Orden fail-closed, todo antes de imprimir el plan (cualquier fallo →
`Abortar` con el motivo y cero escritura):

1. Campaña origen: `ad_entity` con `platform = --plataforma`, `kind =
   'campaign'`, `external_id = --campana`. Ausente → Abortar.
2. La campaña **no** está en `campana_grupo_rol` (una campaña de grupo
   resuelve por grupo; una excepción ahí sería letra muerta y confundiría
   al operador). En grupo → Abortar con el `grupo_id` y el rol.
3. Ad group destino: `ad_entity` con la misma `platform`, `kind =
   'ad_group'`, `external_id = --destino-ad-group`, y `parent_id` = la fila
   de `ad_entity` con `kind = 'campaign'`, misma `platform`, `external_id =
   --destino-campana`. Cualquier eslabón que falte (ad group inexistente,
   campaña destino inexistente, `parent_id` de otra campaña, otra
   plataforma) → Abortar con cuál eslabón falló.
4. Estado previo en `harvest_excepcion`:
   - sin fila → candidata;
   - fila con el **mismo** par → «ya migrada, nada que hacer», exit 0, cero
     escritura (idempotente), sin exigir ceremonia;
   - fila con **otro** par → Abortar: la excepción está congelada («que
     queden así ya», decisión 4); cambiarla no es tarea de este tool.

Plan del dry-run (lo que ve el dueño para autorizar):

- origen: plataforma, `external_id`, `ad_entity.id`;
- destino: `external_id` de campaña y ad group **leídos de `ad_entity`**,
  con sus `ad_entity.id`;
- **resolución de hoy**: `harvest_destino.resolver_destino(conn, platform,
  campaign_id)` impresa como `resuelto_por` + `motivo` (o `Skip(motivo)`);
- **resolución después**: `harvest_destino.decide(replace(lectura,
  excepcion=(camp_ext, ag_ext)))` sobre la misma lectura, **sin escribir**
  (`decide` es pura). Para eso agrega en `app/optimizer/harvest_destino.py`
  una función pública pequeña `simula_excepcion(conn, platform,
  campaign_id, par) -> DestinoHarvest | SaltoHarvest` que llama `_lee` y
  `decide` con la excepción sustituida; es el **único** cambio permitido
  fuera de la lista de archivos de A.5, sin tocar `decide` ni `_lee`;
- si la resolución «después» trae `bid is None` (la campaña no tiene goal
  con `harvest_default_bid`, ni propio ni de plataforma) el plan lo dice en
  una línea de aviso: el motor saltará con `harvest_sin_config` hasta que
  exista bid. No es error del tool (el bid lo fija el goal, decisión 12 del
  spec) pero el dueño lo tiene que ver antes del go.
- huella: `sha256("{platform}:{origen_ext}>{destino_camp_ext}/{destino_ag_ext}")[:16]`.

Go (`--acepto-mutacion-real` + `--esperado 1` + `--huella` igual + `--go`
no vacío; el orden de los mensajes de Abortar es el de
`reversa_harvest.py`: esperado → go → huella): en una transacción, `INSERT
INTO harvest_excepcion (ad_entity_id, destino_campaign_external,
destino_ad_group_external, go_literal) VALUES (%s, %s, %s, %s)` con los
externos **leídos** y `go_literal = --go` tal cual; `commit`; readback de
la fila y de `resolver_destino` (debe dar `resuelto_por = "excepcion"` con
ese par; si no, Abortar después del commit con el estado leído — no se
revierte a mano, se declara). `--esperado` distinto de 1 → Abortar.

### `--limpiar-terna`: validación y escritura

1. `campana_grupo` con ese `id` existe (plataforma y `tipo_producto` se
   imprimen). Ausente → Abortar.
2. Exacta del grupo: `campana_grupo_rol` rol `category_exact` → `ad_entity`
   de campaña y ad group con sus `external_id` (es el destino que la terna
   de F1 apuntó).
3. Goals candidatos: para cada fila de `campana_grupo_rol` del grupo, el
   goal `scope = 'campaign'` de su `ad_entity_id` (puede no existir: se
   lista como «sin goal», no es error). De cada goal: `id`, rol, terna
   actual y `harvest_default_bid`.
4. Clasificación por goal:
   - terna NULL/NULL → «ya limpia», se salta (idempotente);
   - terna = exacta del grupo → candidata;
   - terna con **otro** destino → **Abortar**: es `destino_inconsistente`
     y lo decide el dueño (goals_write o la UI), no se limpia en silencio;
   - `harvest_default_bid` NULL con terna presente no puede existir por el
     trigger; si aparece, Abortar.
5. Huella: `sha256` de las líneas ordenadas
   `"{goal_id}:{harvest_campaign_id}/{harvest_ad_group_id}/{bid}"` de las
   candidatas, `[:16]`. `--esperado` = número de candidatas.

Go: por cada candidata, `goals_write.edita_goal(conn, goal_id,
harvest_limpia_destino=True, updated_at=<now UTC tz-aware>)`; el propio
`edita_goal` valida scope/membresía otra vez y **confirma por llamada**.
Un `GoalInvalido`/`GoalInexistente` a mitad → Abortar con el `goal_id` y
cuántas quedaron limpias: lo limpio queda limpio (cada goal en bid-solo es
válido por el trigger de 0038 y resuelve por grupo), y re-correr salta las
ya limpias. Declara esa semántica «por goal, reanudable» en el docstring y
en el PR; no la envuelvas en una transacción global (edita_goal ya
confirma). Tras el go: readback de los goals del grupo y de
`resolver_destino` de una campaña discovery del grupo (`resuelto_por =
"grupo"`, `motivo = None`).

Nunca toques goals de `scope = 'platform'` ni goals de campañas fuera del
grupo, y nunca `harvest_default_bid`.

### Fronteras del tool

- Imports de runtime permitidos: stdlib (`argparse`, `dataclasses`,
  `datetime`, `hashlib`, `os`, `sys`), `app.db` (`connect`,
  `OrbitDbError`), `app.goals_write` (`edita_goal`, `GoalInvalido`,
  `GoalInexistente`), `app.optimizer.harvest_destino`
  (`resolver_destino`, `simula_excepcion`, `RESUELTO_*`, dataclasses),
  `app.redaction` (`scrub`, si imprimes excepciones). **Nada de
  `app.ads.*`, `app.apply*`, `httpx`, `__import__`, `import_module`.**
- Cero SQL de escritura contra `ads_optimizer_goal` (ni en strings, ni en
  comentarios con verbo delante: el candado escanea texto con regex
  IGNORECASE). El único INSERT del tool es a `harvest_excepcion`.
- Sin `--go` no hay mutación; `--go` vacío o solo espacios cuenta como
  ausente.

## Archivos y fronteras

Cambios esperados:

- `tools/harvest_excepcion.py` (nuevo).
- `app/optimizer/harvest_destino.py`: solo `simula_excepcion` (público,
  pequeño, sin tocar `decide`/`_lee`/`resolver_destino`).
- `app/goals_write.py`: solo si encuentras un hueco real en
  `harvest_limpia_destino`; hoy no se espera cambio.
- `tests/test_harvest_excepcion.py` (nuevo): todo el comportamiento del
  tool sobre `db_f2`, con `_carga_tool` y `mod.main([...])`,
  `ORBIT_DSN_ADMIN` **derivado de `_test_dsn()` + `conn.info.dbname`** (no
  un DSN fijo `orbit:orbit@localhost`: hallazgo pendiente del PR #267, no
  lo repitas), y `SET ROLE app_admin` donde el test escriba a mano.
- `tests/test_architecture.py`: `ALLOWLIST_IMPORTS_HARVEST_EXCEPCION`
  (positiva, sincronizada con los imports reales, con razón escrita) +
  `test_harvest_excepcion_solo_importa_lo_declarado` + el negativo con
  `tmp_path` que agrega `from app.ads.write import AdsWriteClient`; y el
  DoD de la fila: **extrae** el escaneo de escritores crudos de `tools/`
  a un helper `_escritores_crudos_goals(raiz: Path) -> list[str]` usado
  por `test_escritura_de_goals_vive_solo_en_goals_write`, y un test nuevo
  `test_candado_tools_caza_update_crudo_de_goals(tmp_path)` que copia el
  tool a `tmp_path/tools/`, le agrega una línea con `UPDATE
  ads_optimizer_goal SET enabled = false` y asierta que el helper lo lista
  (regla 9: el candado tiene que **fallar** con la fuga sembrada).
- `tests/test_harvest_destino.py`: solo un test de `simula_excepcion`
  (igual a `resolver_destino` cuando la excepción ya existe; distinto
  cuando se simula sobre una campaña sin nada).

No cambies migraciones, GRANTs, `app/ads/*`, `app/apply*`,
`app/biblioteca.py`, el resolutor (`decide`/`_lee`), `tools/` existentes,
visibilidad de A.6, tracker, `plans/ROADMAP.md` ni deploy. No migres nada
real: D.2 es del lead y el dueño, con go nuevo, y hoy la única candidata es
limpiar la terna del grupo 1 (hechos 13–14).

## Orden TDD obligatorio

En cada bloque: demuestra el rojo contra el código anterior (regla 9),
implementa el mínimo verde y ejecuta solo los archivos focales.

### 1. Esqueleto y candados de CLI

Rojos: `_carga_tool` importa el módulo; sin modo → `SystemExit` 2; los dos
modos juntos → `SystemExit` 2; sin `ORBIT_DSN_ADMIN` → `Abortar`; el tool
no importa `app.ads.*` ni menciona `AdsWriteClient`/`ORBIT_DSN_DECIDE` como
código; allowlist positiva verde y su negativo con `tmp_path`.

### 2. `--migrar` dry-run y validación

Rojos sobre `db_f2` + `_semilla` (campaña 7001 sin grupo) + `_semilla_grupo`
(campañas 61xx en grupo): dry-run imprime origen, par leído, resolución
hoy (`terna`/`migracion_pendiente` con el goal de plataforma de `_semilla`)
y después (`excepcion`), la huella, y **cero filas** en `harvest_excepcion`;
campaña inexistente, campaña de grupo (61xx), ad group inexistente, ad group
hijo de otra campaña, campaña destino de otra plataforma (siembra una
`amazon_mx` con el mismo `external_id`), texto libre (`--destino-ad-group
"exact-mx"`) → `Abortar` con motivo y cero filas; aviso de bid ausente
cuando el goal no tiene `harvest_default_bid`.

### 3. `--migrar` go

Rojos: ceremonia incompleta (sin `--esperado`, `--esperado 2`, sin `--go`,
`--go ""`, sin `--huella`, huella distinta) → `Abortar` y cero filas;
ceremonia completa → exactamente una fila con los externos **leídos** y
`go_literal` exacto, `resolver_destino` da `excepcion` con ese par;
segunda corrida igual → exit 0, «ya migrada», una fila; par distinto →
`Abortar`, la fila original intacta.

### 4. `--limpiar-terna` dry-run y validación

Rojos: grupo inexistente → `Abortar`; grupo con 5 goals con terna a la
exacta → 5 candidatas, huella, cero UPDATE (`updated_at` de los 5 sin
mover); un goal con terna a otro destino → `Abortar` sin tocar ninguno;
goals ya en NULL → «ya limpia» y no cuentan en `--esperado`; el goal de
plataforma de `_semilla` no aparece en la lista.

### 5. `--limpiar-terna` go

Rojos: ceremonia incompleta → `Abortar`, ternas intactas; completa → los
5 goals con campaign/ad_group NULL y `harvest_default_bid` intacto
(`11.6200`), `updated_at` movido, `resolver_destino` de la
`category_phrase` sigue dando `grupo`; segunda corrida → 0 candidatas,
exit 0, nada cambia; fallo inyectado en el tercer `edita_goal`
(`monkeypatch` que levanta `GoalInvalido` en la tercera llamada) → dos
limpias, tres intactas, `Abortar` con el `goal_id`, y una tercera corrida
limpia las tres restantes. Verifica en la fuente del tool que no hay SQL
de escritura contra goals y que el helper de arquitectura lo lista vacío
para `tools/` real.

## Mutantes que deben morir

Como mínimo:

- escribir en el dry-run (quitar el gate de `--acepto-mutacion-real`);
- aceptar `--go ""` o saltar la comparación de huella;
- escribir los externos tecleados en vez de los leídos de `ad_entity`;
- validar el ad group sin `parent_id` (solo por existencia);
- validar sin plataforma (mismo `external_id` en MX y US);
- permitir `--migrar` sobre una campaña de grupo;
- pisar una excepción existente con otro par (UPDATE en vez de Abortar);
- limpiar un goal cuya terna apunta a otro destino;
- limpiar con SQL crudo (el candado de arquitectura y el test del helper lo
  cazan) o tocar `harvest_default_bid`;
- incluir goals de `scope = 'platform'` o de campañas fuera del grupo;
- seguir limpiando después de un `GoalInvalido` sin abortar;
- leer `ORBIT_DSN_DECIDE`/`ORBIT_DSN_READ` en vez de `ORBIT_DSN_ADMIN`;
- importar `app.ads.write` o `app.apply` (allowlist positiva + candado).

## DoD binario

1. `tools/harvest_excepcion.py` existe con los dos modos excluyentes, dry-run
   por defecto, ceremonia completa y `Abortar`/exit 2 fail-closed.
2. `--migrar` solo escribe un par resuelto contra `ad_entity` (kind,
   `parent_id`, plataforma), rechaza campañas de grupo y texto libre, es
   idempotente y nunca pisa una excepción existente.
3. El dry-run muestra la resolución de hoy y la de después sin escribir;
   avisa cuando no habrá bid.
4. `--limpiar-terna` limpia solo goals de campaña del grupo cuya terna es la
   exacta del grupo, solo por `goals_write.edita_goal`, con bid intacto,
   idempotente y reanudable tras un fallo por goal.
5. El tool usa únicamente `ORBIT_DSN_ADMIN` y no importa nada de Amazon ni
   de apply; allowlist positiva de imports y candado de escritor único
   verdes, y el candado **falla** con el UPDATE crudo sembrado en
   `tmp_path`.
6. `simula_excepcion` es pura sobre `_lee` y no cambia `resolver_destino`.
7. Rojos previos y mutantes documentados; focales, Ruff y pre-commit pasan;
   la batería completa pasa una sola vez en CI del PR.

## Verificación y entrega

Durante implementación:

```bash
ORBIT_TEST_DSN=<dsn-test> uv run --frozen python -m pytest -q \
  tests/test_harvest_excepcion.py tests/test_harvest_destino.py
uv run --frozen python -m pytest -q tests/test_architecture.py tests/test_goals_write.py
uv run --frozen ruff check tools/harvest_excepcion.py app/optimizer/harvest_destino.py \
  tests/test_harvest_excepcion.py tests/test_architecture.py tests/test_harvest_destino.py
uv run --frozen ruff format --check tools/harvest_excepcion.py \
  app/optimizer/harvest_destino.py tests/test_harvest_excepcion.py \
  tests/test_architecture.py tests/test_harvest_destino.py
pre-commit run --all-files
```

Antes del PR, `git log <base>..HEAD` debe mostrar solo los commits de A.5.
Abre PR a `master` (si la rama nació de `origin/feat/fabrica-02-a4`, dilo en
la descripción: el dueño mergea #269 antes); la batería completa corre
**una sola vez en CI** sobre el SHA final. No la repitas localmente si CI ya
la validó.

La descripción del PR incluye: baseline y rojos por bloque, resultados
focales sin skips, la salida literal de un dry-run de cada modo sobre el
fixture, la matriz de rechazos, la prueba de idempotencia y de reanudación,
la prueba del candado con la fuga sembrada, el log de commits, residuales
(declara: la migración real y la limpieza del grupo 1 son D.2 con go del
dueño; el tool no verifica el `state` vivo del ad group destino, lo hará el
LIST del flujo de harvest) y el enlace al CI verde.

Cero producción, SSH, secretos o Amazon en A.5. La revisión del lead se
agrupa en una sola ronda por bloque; una segunda solo si la primera
encuentra severidad alta y nunca una tercera.
