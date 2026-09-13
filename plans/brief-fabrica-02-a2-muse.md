# Brief para Muse: FABRICA 02 (F2) — tarea A.2, migración `0038`

Base `origin/master` `c466dfe` (A.0 y A.1 cerradas; tras `git fetch` usa el HEAD
vigente). Rama desde `origin/master`, **jamás** desde tu master local. **Una
sola tarea: A.2.** Nada de A.3 ni de A.4, aunque los roces.

Contrato: fila **A.2** de `plans/fabrica-02.md` (DoD literal, no lo reformules)
y el bloque **«Migración `00NN`» (a)–(g)** de la sección «Diseño». Este brief
solo baja ese contrato a instrucciones verificables; no reabre decisiones.

## Antes de escribir una línea

1. `plans/fabrica-02.md`: fila A.2, el bloque «Migración `00NN`» y el bloque
   «Biblioteca» del diseño.
2. `docs/CONTEXTO.md` reglas 1–10. Innegociables.
3. Las tres migraciones que vas a tocar **sin editarlas**: `0001` (CHECK de
   `harvest_job.fase` l.981–983, índice `harvest_job_en_vuelo` l.994, CHECK
   `goal_harvest_completo` l.713–718 y su COMMENT l.773), `0002` (trigger
   `harvest_job_sella_fases` l.602–640, `attempt_tipo_valido` l.299, COMMENT de
   `apply_attempt` l.313–326) y `0018` (GRANTs l.449–455: `app_decide` **solo
   lee** bibliotecas y `harvest_excepcion`; `app_admin` INSERT/UPDATE + USAGE de
   secuencias).
4. `migrations/0037_ingest_run_salud_idx.sql` como patrón de cabecera: por qué,
   «no re-runnable», `BEGIN`/`COMMIT`, y un `DO $$` que es candado de privilegio.

## Número y naturaleza

- **`0038`**. Hoy es el siguiente libre (última: 0037; nada en disco con ese
  prefijo). Si otra migración se te adelanta, renumera; el número se reserva al
  implementar, no en el plan.
- **No re-runnable** y **no puramente expansiva**: recrea un índice y suelta un
  CHECK, ambos dentro de la transacción. Va declarado en la cabecera y en
  `docs/DEPLOY.md` como subsección «Migración 0038» bajo «Aplicar migraciones»
  (patrón de la 0019, l.977): backup del schema antes, `-1`, y qué se suelta.
- `0001`, `0002` y `0018` **no se editan**. Todo es `ALTER`/`CREATE OR REPLACE`
  en 0038.

## Los siete puntos, con lo que exige cada uno

**(a) `harvest_job.fase` admite `hermanas_negadas`.** El CHECK de 0001 es
inline y sin nombre: Postgres lo bautizó solo. **Léelo de `pg_constraint` en la
base de test, no lo supongas** (probablemente `harvest_job_fase_check`). `DROP
CONSTRAINT` + `ADD CONSTRAINT` con el valor nuevo. Y `CREATE OR REPLACE
FUNCTION harvest_job_sella_fases()` con la progresión nueva:

```
las seis de hoy, intactas
('exact_created',    'hermanas_negadas')
('hermanas_negadas', 'done')
('hermanas_negadas', 'failed')
```

**`exact_created → done` se conserva** (jobs viejos). La obligación «un harvest
de grupo pasa por la fase» **no va en plpgsql**: un trigger que consulte la
membresía viva del grupo deja jobs sin salida legal. Eso lo cumple la app con su
test, en A.3. Actualiza el mensaje del `RAISE` y el `COMMENT ON FUNCTION`. **No
toques el trigger de INSERT** (`harvest_job_decision_coherente`, de 0001):
`test_0002_harvest_job_sella_progresion` lo vigila.

**(b) Índice parcial.** `DROP INDEX harvest_job_en_vuelo` + `CREATE UNIQUE INDEX
harvest_job_en_vuelo ON harvest_job (platform, ad_entity_id, search_term) WHERE
fase IN ('pending','negative_created','exact_created','hermanas_negadas')`. Sin
`CONCURRENTLY` (va en la transacción; la tabla es chica).

**(c) `attempt_tipo_valido` gana `'hermana'`.** `DROP` + `ADD` con los cuatro
valores. `attempt_probe_sin_decision` queda como está: una `hermana` sin
`decision_id` sigue siendo un efecto sin causa. COMMENT de `apply_attempt`: donde
hoy dice «harvest = 1 operación lógica (aunque sean 2 HTTPs)» pasa a «2+N HTTPs
por operación lógica: `[(1,'normal',cobrada), (2,'normal',no),
(3..N,'hermana',no)]`». El tope de reintentos por decisión es de la app y **no
cuenta hermanas**; eso es A.3. Aquí solo el esquema.

**(d) El CHECK `goal_harvest_completo` se va; entra un trigger.**
`ALTER TABLE ads_optimizer_goal DROP CONSTRAINT goal_harvest_completo` y un
trigger `BEFORE INSERT OR UPDATE` que admite exactamente tres estados:

1. los tres campos NULL;
2. los tres NOT NULL;
3. `harvest_campaign_id` y `harvest_ad_group_id` NULL con `harvest_default_bid`
   NOT NULL — **únicamente** si `scope = 'campaign'` y la campaña está en
   `campana_grupo_rol`.

Todo lo demás (cualquier parcial, o el estado 3 sin grupo o con `scope =
platform`) → `RAISE ... USING ERRCODE = 'check_violation'`. **El errcode
importa**: `goals_write` ya traduce `CheckViolation` a 422; si lanzas otro, la
API cambia de comportamiento. Mueve el sentido del COMMENT del CHECK a la
función.

**Trigger simétrico en `campana_grupo_rol`** (`BEFORE UPDATE OR DELETE`): si la
campaña de la fila (`OLD.ad_entity_id`) tiene un goal en el estado 3, rechaza
**sacarla** (`DELETE`) o **re-apuntarla** (`UPDATE` que cambie `ad_entity_id` o
`grupo_id`). Sin esto, `app_admin` deja un goal sin destino en silencio:
`sin_destino_de_harvest` sin que nadie lo pida. Con goal en estado 1 o 2, el
`DELETE` sigue siendo legal.

**(e) GRANTs a `app_decide`.** Punto de partida (0018): solo `SELECT`. Agregas:
`INSERT` en `keyword_biblioteca` y `negative_biblioteca`; **`USAGE ON SEQUENCE
keyword_biblioteca_id_seq, negative_biblioteca_id_seq`** (son `BIGSERIAL`: sin
USAGE el INSERT truena aunque tenga el GRANT de tabla — es la clase de bug
0033→0034); y `UPDATE (updated_at)` **por columna y solo en
`keyword_biblioteca`** (`negative_biblioteca` no tiene esa columna). Decisión
del dueño 2026-09-10: sin dinero — el statement del motor no toca otra columna.
`harvest_excepcion` **sigue solo `app_admin`**. Nada para `app_read` ni
`app_ingest`.

**(f) El `DO $$` es el candado, no un adorno.** Bajo `SET ROLE app_decide`
ejecuta **el statement literal del motor** y lee la fila de vuelta. Ojo con
esto, que es lo más fácil de hacer mal en la tarea: **el motor que escribe la
biblioteca es A.4 y todavía no existe.** Así que 0038 lo **sella**, y A.4 lo
tendrá que usar tal cual. Es este, derivado del bloque «Biblioteca» del diseño
(si ya existe: solo `updated_at`; `origen` no se pisa):

```sql
INSERT INTO keyword_biblioteca (tipo_producto, platform, texto, origen)
VALUES (...)
ON CONFLICT (tipo_producto, platform, texto) DO UPDATE SET updated_at = now()
RETURNING id;

INSERT INTO negative_biblioteca (tipo_producto, platform, texto, origen)
VALUES (...)
ON CONFLICT (tipo_producto, platform, texto) DO NOTHING;
```

Positivos: la primera corrida inserta (fila leída); la segunda del mismo término
devuelve **el mismo `id`** y `updated_at` se movió. Negativos, todos con
`InsufficientPrivilege`: `DELETE` en las dos bibliotecas; `UPDATE` de `origen`,
`texto`, `first_seen_at` y `cost` en `keyword_biblioteca`; `INSERT` y `UPDATE`
en `harvest_excepcion`. `RESET ROLE` y **cero filas de prueba al salir** del
bloque (bórralas como dueño y asértalo). Un `INSERT` con `cost`/`moneda`
**pasa** por GRANT — el «sin dinero» lo garantiza la app (A.4 DoD (g)), no el
esquema; déjalo dicho en el comentario del bloque.

**(g) Tests y docs.**
- `tests/test_schema.py`: `PROGRESION_HARVEST` pasa a ser el conjunto de F2
  (nueve pares). El test actual parsea el cuerpo de la función en **0002** y
  compara contra la constante: como 0002 no cambia, ese test debe comparar
  contra el conjunto histórico (nómbralo `PROGRESION_HARVEST_0002`) y un test
  nuevo parsea **0038** (el `CREATE OR REPLACE`) contra `PROGRESION_HARVEST`.
- **Constante de fases en vuelo** en el módulo de tests, cruzada contra
  `pg_index.indpred` del índice real en la base de test: las cuatro fases,
  ni una más. **No toques `app/apply_harvest.py`** (sus tres `fase IN (...)` de
  l.145, 152 y 267 siguen con tres fases): partirlo y cablearlo es A.3a/A.3, y
  es seguro porque ningún job puede llegar a `hermanas_negadas` hasta que A.3
  escriba ese código, y D.1 exige cola de harvest vacía al desplegar.
- `docs/APPLY.md` §6.1: fila nueva para `harvest_job` fase `hermanas_negadas`
  con lo que el diseño ya fija (LIST de negativos filtrado por las hermanas,
  reintento idempotente **sin re-cobrar quota**, `TOPE_CICLOS_HERMANAS` → `done`
  con `external_ids["hermanas_pendientes"]` + alerta; **jamás se revierte la
  keyword por una hermana**). §7: `tipo='hermana'` en el ledger y el orden de
  reversa keyword → hermanos → origen (`tools/reversa_harvest.py`, A.3).
- `docs/DATABASE.md` l.345 (cadena de fases) y l.356 (predicado del índice).

## Lo que A.1 dejó esperándote — y es parte de tu DoD

`tests/test_harvest_destino.py` simula el esquema post-A.2 soltando el CHECK **a
mano** en tres sitios (l.~441, ~531 y ~763: `ALTER TABLE ads_optimizer_goal
DROP CONSTRAINT goal_harvest_completo`). Con 0038 en `ORDEN_F2`
(`tests/test_fabrica_f2.py`, el hueco ya está preparado) **esas tres líneas
sobran y se quitan**. Si alguno de esos tests dependía de bid-solo en una
campaña **sin** grupo, el trigger nuevo lo va a rechazar — y eso es correcto:
arregla el test, no el trigger. **Toda la suite de A.1 tiene que pasar sobre el
esquema real**, sin simulaciones.

## DoD — tests con rol real, rojo primero

1. Transiciones válidas: `exact_created → hermanas_negadas`, `hermanas_negadas →
   done`, `hermanas_negadas → failed`, y **`exact_created → done` conservada**.
   Inválidas (check_violation): `pending → hermanas_negadas`, `negative_created
   → hermanas_negadas`, `hermanas_negadas → exact_created`, `done →
   hermanas_negadas`.
2. Job sembrado en `hermanas_negadas` → un segundo job del mismo `(platform,
   ad_entity_id, search_term)` viola `harvest_job_en_vuelo`; el mismo job en
   `done` **no** bloquea.
3. Constante de fases en vuelo == `pg_index.indpred` del índice real.
4. `tipo='hermana'` con `quota_cobrada=false` entra; sin `decision_id` truena;
   el `COUNT` de filas `normal` de esa decisión no cambia.
5. Trigger de goal: estado 3 aceptado con campaña en grupo; rechazado con
   `scope = platform`, con campaña fuera de grupo, y con cualquier parcial
   distinto (solo `harvest_campaign_id`, por ejemplo). Estados 1 y 2 intactos.
   El errcode es `check_violation`.
6. Trigger simétrico: goal en estado 3 → `DELETE` de su fila en
   `campana_grupo_rol` rechazado, `UPDATE` que la re-apunta rechazado; goal en
   estado 2 → `DELETE` permitido.
7. `SET ROLE app_decide`: los dos statements literales insertan y actualizan de
   verdad (fila leída, `id` estable, `updated_at` movido); los negativos de (f)
   truenan. Patrón `tests/test_apply_schema.py:709-745`, **no** catálogo de
   GRANTs.
8. **Mutante obligatorio**: comenta el `GRANT USAGE ON SEQUENCE ... TO
   app_decide` y aplica 0038 en la base de test → **la migración tiene que
   tronar** en el `DO $$`. Cítalo en el PR con el error.
9. `PROGRESION_HARVEST` parsea 0038; el test histórico de 0002 sigue verde.
10. La suite de A.1 verde sobre 0038 sin los tres `DROP CONSTRAINT` manuales.

## Reglas de proceso (el lead las verifica)

- Rojo antes del arreglo en cada punto, citado en el PR.
- **Vuelvo a mutar.** Además del mutante 8, voy a probar: quitar
  `'hermanas_negadas'` del predicado del índice; permitir `pending →
  hermanas_negadas`; quitar la condición de grupo del trigger de goal; y
  cambiar el errcode. Cada uno tiene que matar al menos un test.
- `pytest_focal` con `ORBIT_TEST_DSN` apuntado: `0 skipped`. Sin DSN, todo esto
  skipea en verde y no prueba nada.
- Cero `--no-verify`. Cero producción, cero ssh, cero Amazon.
- **No toques**: `app/` (nada — ni `apply_harvest.py`, ni `goals_write.py`, que
  ya trae `permite_bid_solo`), las migraciones selladas, el tracker,
  `plans/ROADMAP.md`, `docs/CHAT-CONTEXT.md`.
- Un PR, una rama. Máximo una ronda de cross-review tuya.

## Entrega

PR a `master` con: los rojos citados, el mutante 8 con su error, la suite focal
+ `test_harvest_destino.py` + `test_fabrica_f2.py` + `test_schema.py` en verde
con `0 skipped`, y en la descripción **qué mutación mata cada test nuevo**.

Si algo del diseño te parece mal, **dilo en el PR y párate** — no lo cambies
por tu cuenta. Lo más probable que te tiente: meter la membresía del grupo en el
trigger de fases (no: deja jobs sin salida), o dar `UPDATE` de tabla entera en
`keyword_biblioteca` porque es más cómodo (no: el dueño decidió sin dinero, y
el GRANT por columna es lo que lo garantiza en la base).
