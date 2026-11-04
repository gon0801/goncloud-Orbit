=== quality-kit cross-review.ps1 ===
Repo: /Users/dn/dev/_wt/f11-kimi-r1
CLI: kimi
Alcance: last-commit
Archivos (pathspec de la tarea): tests/test_precio_cobertura.py
Tamano del diff: 40051 caracteres
==> Invocando kimi de forma no interactiva (tope: 900 s)...

=== Respuesta de kimi (codigo de salida: 0) ===
• Voy a leer el diff del commit y revisarlo directamente.

• El diff es un archivo de tests nuevo. Voy a verificar que los modulos, guiones y migraciones que referencia existen y coinciden con lo que los tests asumen.

• Verifico puntos finos: el rol `app_read` en las migraciones, y el comportamiento del tool y del guion.

• Ahora verifico el esquema de `precio_decision`/`precio_goal` en 0039 y los permisos del rol `app_read`, que el test CR3 asume.

• Todo cuadra con el esquema. Corro los tests que no requieren Postgres para verificar las afirmaciones del diff (porcentajes, igualdad de consultas, exit codes).

• Revise el diff (tests/test_precio_cobertura.py, 1024 lineas nuevas) y verifique contra el repo real: `app/precio/cobertura.py`, `app/precio/fuentes.py`, `tools/precio_cobertura.py`, las migraciones 0001/0002/0028/0035/0039, los SQL de `docs/evidencia/repricing-01/A.7/consultas/` y el guion `recuadro_desde_salidas.py`. Ademas corri el archivo completo: 32 passed, 0 skipped (Postgres local disponible, asi que los tests de integracion tambien corrieron).

  Hallazgos:

  1. (baja) Comentario enganoso en `test_fuentes_status_contiene_buyable_y_manda_la_ultima` (linea ~583 del diff): dice "la BUYABLE vieja sigue contando si es la ultima", pero el escenario es al reves: la CLOSED es la vieja (hace 2h) y la `BUYABLE,DISCOVERABLE` es la ultima (now). El assert es correcto; el comentario describe otra cosa y confunde al lector.

  2. (baja) VERIFICAR: `test_app_read_no_escribe_tablas_de_fuentes` hace `SET ROLE app_read` con la conexion del DSN base. Las migraciones no otorgan membresia de `app_read` a ningun rol LOGIN (no hay `GRANT app_read TO ...` en `migrations/`); funciona porque el usuario `orbit` del docker-compose es superuser. Si `ORBIT_TEST_DSN` apunta a un Postgres donde el usuario no es superuser ni miembro de `app_read`, el test falla con `permission denied to set role` en vez de skipear. Pasa en el entorno del repo (lo corri), pero habria que confirmar que el DSN de VERIFY que menciona el docstring tambien usa un superuser.

  3. (baja) Empate de `observed_at` en el mismo test del punto 1: `_pub` inserta BUYABLE con `now()` y el test inserta `BUYABLE,DISCOVERABLE` tambien con `now()`, y `_SQL_CANO` ordena por `observed_at DESC` sin desempate determinista de status. Inofensivo aqui (ambas contienen BUYABLE, el resultado es el mismo con cualquiera), pero si alguien edita el test y cambia uno de los status, el resultado queda a merced del orden fisico. No es bug actual.

  Todo lo demas verificado y correcto: la ecuacion de cuadre, los porcentajes de `aviso_puente` (16.7%, 7.6%, umbral >5%), el vocabulario de `precio_decision` (CHECKs de 0039 admiten `mantener`/`no_evaluado` con motivo y sin importes), las 8 monedas de `_MONEDAS` contra las 8 columnas currency, los GRANTs de `app_read` (solo SELECT), la vigencia semiabierta `[valid_from, valid_to)` de goals, el calculo de `dias_sin_reportar` en UTC, y la igualdad normalizada entre `consultas/*.sql` y las constantes de `fuentes.py`. Sin riesgos de seguridad (subprocess con args fijos, credenciales solo del DSN de test local).



=== stderr ===
kimi version 0.39.1
• The user explicitly says: act as external reviewer, do NOT use skills or workflows, respond directly. User instructions take precedence. Read the diff file and review.

• The diff is a test file: tests/test_precio_cobertura.py. It references app/precio/cobertura.py, app/precio/fuentes.py, tools/precio_cobertura.py, docs/evidencia/repricing-01/A.7/... and test_schema._postgres_obligatorio_ausente. The commit is limited to the test file, but the commit message says it includes those modules. Let me verify the referenced modules exist and match the test assumptions (e.g., FilaPublicacion signature, armar_recuadro, aviso_puente thresholds, leer_publicaciones signature, max_dias_desde_settings, SQL constants).

  Key checks:
  1. app/precio/cobertura.py exists with FilaPublicacion, armar_recuadro, cuadra_exact, aviso_puente.
  2. app/precio/fuentes.py exists with _SQL_CANO etc., leer_publicaciones(conn, platform, hoy), hoy_base, config_vigente_settings, max_dias_desde_settings.
  3. tools/precio_cobertura.py exists.
  4. docs/evidencia/repricing-01/A.7/consultas/*.sql exist and match constants (test compares).
  5. test_schema._postgres_obligatorio_ausente exists.
  6. migrations ORDENCOB files exist (0028, 0033, 0034, 0035, 0039).
  7. Check the _decision helper: INSERT with hardcoded `_MONEDAS` string interpolation — check columns count match: 8 currency columns, _MONEDAS has 8 values. p_actual_currency, p_objetivo_currency, p_aplicado_currency, i_currency, c_currency, f_currency, l_currency, r_currency = 8, and _MONEDAS has 8 'MXN'. OK.
  8. test_fuentes_status_contiene_buyable_y_manda_la_ultima: comment says "la BUYABLE vieja sigue contando si es la ultima" but inserts CLOSED 2 hours ago and BUYABLE,DISCOVERABLE now — asserts SKU-JOIN in skus. Depends on leer_publicaciones logic (latest status contains BUYABLE).
  9. test_dias_en_utc_con_observed_en_otra_zona: timezone math — medianoche_mas_dos is 00:30 at GMT+2 = 22:30 UTC previous day, so dias >= 1. If hoy_base returns UTC date. Verify.
  10. aviso_puente(activas=12, puente=14): 2/12 = 16.67% → "16.7%" — depends on rounding.
  11. test_aviso_puente: aviso_puente(activas=100, puente=104) is None — 4% <= 5%. aviso_puente(activas=264, puente=284): 20/264 = 7.575% → "7.6%". OK depends on implementation.
  12. Check _normalizar_sql: replaces ":'platform'" with "%s" — psql variables. Fine.
  13. Potential issue: test_guion_acepta_puente_activas_e_identidad builds hoy via SQL now() — fine.
  14. test_recuadro_desde_salidas_roundtrip: canonicas dump uses "ASIN-X" hardcoded for all, and status "BUYABLE" — fine since fields may not matter. Also asserts rec == esperado (dataclass equality) — FilaPublicacion-based recuadro equality; depends on dataclass.

  Also check: `_tool` runs with env["ORBIT_DSN_ADMIN"] and DECIDE pointing to 127.0.0.1:1 — verify the tool doesn't connect those eagerly.

  Let me verify files exist and key signatures.

tools/precio_cobertura.py

app/precio/:
__init__.py
__pycache__
cobertura.py
config.py
fuentes.py
goals_write.py
objetivo.py
reglas.py
tipos.py
ventas.py

docs/evidencia/repricing-01/A.7/:
consultas
mutantes.md
readback.md
readback.sh
recuadro_desde_salidas.py
salidas

docs/evidencia/repricing-01/A.7/consultas/:
01_canonicas.sql
02_canales.sql
03_goals.sql
04_decisiones.sql
05_listing_identidad.sql
06_hoy.sql
migrations/0028_estimacion_venta.sql
migrations/0033_ingest_run_llamadas.sql
migrations/0034_ingest_run_llamadas_grant.sql
migrations/0035_spapi_listings_inventario.sql
migrations/0039_precio.sql
• Now check app/precio/cobertura.py and fuentes.py signatures match test assumptions. Also tools/precio_cobertura.py behavior (exit 2, messages), and recuadro_desde_salidas.py. Also verify FilaPublicacion field order (listing_id, seller_sku, platform, canal, precio, moneda, dias_sin_reportar, tiene_goal, resultado_hoy, motivo_hoy).Verify a few subtle points:

  1. `_SQL_CANO` DISTINCT ON (e.seller_sku, e.platform) with LEFT JOIN listing — if two listings share SKU, DISTINCT ON picks one (l.id NULLS LAST but ordering by observed_at DESC then l.id — if both listings have same sku, two rows with same observed_at; picks arbitrary l.id). Test test_fuentes_dos_listings_mismo_sku_cuentan_uno expects len==1. Fine.

  2. test_fuentes_status_contiene_buyable_y_manda_la_ultima: inserts BUYABLE via _pub (observed now), then CLOSED 2h ago, then "BUYABLE,DISCOVERABLE" now. Two rows with same observed_at (now). DISTINCT ON ordering by observed_at DESC — tie between the _pub BUYABLE and BUYABLE,DISCOVERABLE, both contain BUYABLE so either way the row is in. Fine. SKU-NULA with status=None — WHERE u.status LIKE '%BUYABLE%' excludes NULL. Fine.

  3. test_dias_en_utc: conn.execute("SET TIME ZONE 'Etc/GMT-2'") — note Etc/GMT-2 is UTC+2 (POSIX sign inverted). medianoche_mas_dos = 00:30 at +02:00 → 22:30 UTC previous day. hoy_base returns (now() AT TIME ZONE 'UTC')::date — independent of session TZ. Good. dias = hoy - observed.astimezone(UTC).date() = hoy - yesterday = 1. Fine.

  Edge: if the test runs when UTC now is, say, 00:15, then 00:30 +02:00 = 22:30 UTC yesterday → dias=1. Always >=1 since 00:30+2 always maps to previous UTC day (22:30 prev day). Good.

  4. test_decision_de_ayer_no_es_la_de_hoy: _pub inserts decision via _decision — what's decision_date default? precio_decision presumably has decision_date DEFAULT (now() AT TIME ZONE 'UTC')::date. Then calling leer_publicaciones with hoy+1 day makes goals: valid_from <= hoy+1 OK (goal valid_from = today, still valid), decision_date = hoy+1 doesn't match → sin_decision. dias_sin_reportar = (hoy+1 - today) = 1, <= max_dias=3 fine. OK.

  Wait — but in _goal, valid_from is (now() AT TIME ZONE 'UTC')::date. With hoy+1, valid. Good.

  5. test_goal_futuro_no_cuenta: goal with valid_from = hoy+10. _SQL_GOALS: valid_from <= hoy excludes. sin_goal = SKU-FUT. Good.

  6. test_app_read_no_escribe: SET ROLE app_read — does role app_read exist? Migration 0002_apply.sql likely creates roles. AGENTS.md says roles orbit_ingest/_decide/_read/_admin... but test uses app_read. Check migration 0001/0002 for role names.

• Things to verify:

  1. Role `app_read` — used in SET ROLE app_read. The test connects with `_dsn_base()` default "postgresql://orbit:orbit@localhost:5432/postgres" — user orbit must be able to SET ROLE app_read (membership granted in 0001 presumably). Also the test asserts INSERTs fail with InsufficientPrivilege for tables: spapi_listing_estado_observation, listing, estimacion_oferta_observation, precio_goal, precio_decision, config_version. But wait — INSERT on listing requires product FK etc., but the INSERT would fail on privilege before constraint check? Actually privilege check happens first. But INSERT into precio_goal references listing id — permission denied happens at planning/execution. Should be InsufficientPrivilege. OK.

  But: does app_read have INSERT granted on any of these via default privileges? 0001 ALTER DEFAULT PRIVILEGES gives SELECT to app_read. Need to check whether app_read gets INSERT anywhere (e.g., 0034_ingest_run_llamadas_grant grants to app_ingest). Grep for "TO app_read" with INSERT across migrations.

  2. The 0035 migration creates spapi_listing_estado_observation; check its GRANTs — does it GRANT INSERT to app_ingest only? And GRANT SELECT to app_read? If 0035 grants SELECT to app_read on spapi_listing_estado_observation — the test's SELECT is on precio_goal. precio_goal is created in 0039 — does 0039 GRANT SELECT to app_read? The test asserts `SELECT count(*) FROM precio_goal` passes under SET ROLE app_read. Need to check.

  3. SET ROLE with autocommit conn — fine. But note: the connection is user `orbit`. Does orbit have membership in app_read? Check 0001: probably GRANT app_read TO orbit or role created with... Let me grep.

  4. test_consultas_iguales — docs SQL files vs constants with normalization; fine, they're generated presumably.

  5. `_tool` in test_tool_error_de_base_es_exit_2: DB created empty (no migrations). Tool connects to it, runs hoy_base fine, config_vigente_settings: SELECT settings FROM config_version — relation doesn't exist → psycopg.Error → exit 2. But wait — connect() from app.db may raise OrbitDbError. Empty DB exists so connect succeeds. Then config_version missing → UndefinedTable (psycopg.Error) → caught. Good.

  6. `_dsn_base()` default "postgresql://orbit:orbit@localhost:5432/postgres" — creates DBs as user orbit; orbit needs CREATEDB. Presumably compose sets it. Existing tests use same pattern presumably (test_schema uses _test_dsn).

  7. Check `_decision` non-subir/bajar branch inserts with motivo NULL possibly — "no_evaluado" requires motivo? test passes motivo. In test_fuentes_doce: N3 has goal but no decision → sin_decision. Fine.

  8. Check precio_decision schema: decision_date column with default. Verify 0039. Also precio_decision for resultado subir requires not-null columns p_actual etc.? The subir/bajar branch provides all values; the mantener/no_evaluado branch omits goal, m_actual, p_actual, p_objetivo, p_aplicado, i_valor, c_valor, f_valor, l_valor, r_valor — are those nullable in 0039? If NOT NULL, the INSERT would fail. Test test_fuentes_doce uses resultado="mantener" with motivo="en_tolerancia" → uses the second branch omitting valor columns. Need to check 0039 DDL for NOT NULL on those.

  9. recuadro_desde_salidas.py: test asserts rec == esperado (Recuadro frozen dataclass equality) — includes avisos tuple. Both should be equal given same data. Check guion exists and parses "1|subir|\n" with empty motivo → None. Fine.

  10. test_guion_acepta_puente_activas_e_identidad: base dict builds txt files; "decisiones": "1|subir|\n" — empty motivo. OK.

  11. Potential issue: `test_tool_sin_dsn_read_es_exit_2_sin_traceback` pops ORBIT_DSN_READ from env but the tool may fall back? _dsn_read raises Abortar → exit 2. Good.

  12. aviso_puente(activas=100, puente=104): diff 4% not > 5 → None. Good. (12,14): 2/12=16.67 → "16.7%". Good. (264,284): 20/264=7.5758 → "7.6%". Good.

  13. test_aviso_puente... fine.

  14. Check `_MONEDAS` count: columns in the mantener INSERT: p_actual_currency, p_objetivo_currency, p_aplicado_currency, i_currency, c_currency, f_currency, l_currency, r_currency = 8 columns; _MONEDAS has 8. Good.

  Now verify precio_decision DDL in 0039 and grants to app_read, plus role membership.

• Checkpoints:

  - 0001 grants SELECT ON ALL TABLES to app_read, and default privileges. So SELECT on precio_goal works. INSERTs: check no INSERT granted to app_read anywhere — grep didn't show INSERT TO app_read. Good.
  - Role membership: does `orbit` user have SET ROLE app_read privilege? Check 0001: GRANT app_read TO orbit? grep.

  - precio_decision: the CHECK constraint at 255: for resultado in evaluated set, all importes NOT NULL required; the other branch requires motivo. The mantener test branch passes motivo and no valores — need resultado not in evaluated list for that CHECK. mantener IS in RESULTADOS_EVALUADOS in cobertura.py but the DB CHECK may define its own vocab. Line 246-268: need to see exact constraint: something like `(resultado IN ('subir','bajar',...) AND goal IS NOT NULL AND ...) OR (resultado NOT IN (...) AND motivo IS NOT NULL)`. The test inserts resultado='mantener' with motivo='en_tolerancia' and no valores. If 'mantener' is in the DB's evaluated list, the CHECK would fail. But _pub with resultado="mantener", motivo="en_tolerancia" — wait, in _decision, resultado in ("subir","bajar") uses first branch; "mantener" goes to second branch. If the DB CHECK requires valores for mantener, test would fail. But presumably these tests passed (commit was merged). Let me check the exact CHECK text to be sure — this is exactly the kind of thing to verify.

  Also test_app_read_no_escribe inserts resultado='mantener', motivo='candado' under app_read — but it expects InsufficientPrivilege, raised before CHECK. Fine.

  Let me read lines 246-270 of 0039.CHECK fine for mantener with motivo.

  Role membership: grep "GRANT app_" in 0001 found nothing. How does SET ROLE app_read work for user orbit? Maybe roles are created IN ROLE or granted elsewhere. Grep across migrations for "app_read" grant of role membership.No GRANT of app_read to any login user in migrations. So SET ROLE app_read as user `orbit` — would fail with "permission denied to set role" unless orbit is a superuser or member. In docker-compose, POSTGRES_USER=orbit creates a superuser. Superusers can SET ROLE to anything. So with default DSN (orbit superuser in compose) it works. With ORBIT_TEST_DSN pointing elsewhere (VERIFY DSN) — the docstring says "cero skipped con el DSN de VERIFY". If that DSN's user is not superuser and not a member of app_read, SET ROLE fails. But that's environmental; the repo's own tests already do similar? test_schema only checks plano text. Hmm, is there precedent of SET ROLE in other tests? Let me check quickly whether other test files use SET ROLE in live DB tests.

  Actually, potential issue: test_app_read_no_escribe does SET ROLE app_read then expects InsufficientPrivilege on INSERTs, then `conn.execute("SELECT count(*) FROM precio_goal")` — fine. RESET ROLE in finally. With autocommit, SET ROLE persists across statements. OK.

  One more subtle thing: in `_db()`, `conn.execute("SET TIME ZONE 'UTC'")` — in test_dias_en_utc they then SET TIME ZONE 'Etc/GMT-2' and rely on hoy_base using `now() AT TIME ZONE 'UTC'` which is TZ-independent. observed stored as timestamptz; psycopg returns it in session TZ as aware datetime? psycopg3 returns timestamptz as datetime with tzinfo of session... Actually psycopg3 returns aware datetime in the connection's timezone? No — psycopg3 returns timestamptz as datetime with fixed offset tzinfo corresponding to the value received from server, which is rendered in the session TimeZone. observed.astimezone(UTC) normalizes. Fine.

  Test asserts filas[0].dias_sin_reportar == (hoy - medianoche_mas_dos.astimezone(UTC).date()).days and >= 1. medianoche_mas_dos = 00:30 at +02:00 → 22:30 UTC previous day → dias = 1 as long as hoy is today UTC. If test runs when UTC time is such that... hoy is current UTC date; observed is 22:30 UTC yesterday. dias=1. Always true. Fine.

  Now, `_tool` sets env["ORBIT_DSN_ADMIN"] and DECIDE to 127.0.0.1:1/nula — connect() in app.db: does connect(_dsn_read()) read other env vars? It receives dsn directly. Why set ADMIN/DECIDE then? Maybe app.db.connect validates env or imports something. Not critical.

  Check app/db.py connect signature quickly? connect(dsn) — likely fine.

  Now check `test_consultas_iguales` normalization: _SQL_CANO contains '%%BUYABLE%%' and the doc file presumably has '%BUYABLE%' with psql \set... The normalization replaces "%%"→"%" and ":'platform'"→"%s", ":'hoy'"→"%s". But _SQL_CANO has two params? No — _SQL_CANO has one %s (platform). _SQL_GOALS has three %s (platform, hoy, hoy). The doc file would have :'platform' once and :'hoy' twice. Replace handles all occurrences. OK.

  Potential mismatch: _SQL_HOY = "SELECT (now() AT TIME ZONE 'UTC')::date" — file 06_hoy.sql may end with semicolon; normalization rstrips ";". OK.

  Also check docs files actually match — that test is self-checking in CI; presumably passed. But I can verify quickly by running the pure tests locally. Pure tests (no pg) include test_recuadro_*, test_consultas_iguales, test_max_dias, tool exit tests. Let me run the non-pg ones. That verifies claims like "16.7%", equality of consultas, etc.

................................                                         [100%]
32 passed in 3.35s
• All 32 passed locally — meaning Postgres IS available locally (no skips?) — "32 passed", 0 skipped. So the live tests ran too. Good.

  Now residual review concerns:

  1. `_dsn_base()` default points to `localhost:5432` with user orbit — creates databases on whatever Postgres is there. Matches repo convention (test_schema does the same). Fine.

  2. `_db()` CREATE DATABASE with hostname/pid/counter — fine; DROP with FORCE in finally. If admin connect fails, exception propagates — fine.

  3. test_app_read_no_escribe: SET ROLE app_read requires superuser or membership. Works with compose orbit (superuser). With a non-superuser ORBIT_TEST_DSN it would error — but the same pattern... actually is there precedent? Not verified, but the tests passed locally. Minor environmental note, low severity.

  4. Fragility: `_normalizar_sql` — whitespace/punctuation normalization; if the doc SQL drifts semantically but normalized-equal (e.g., reordering?) no. Fine.

  5. `test_recuadro_desde_salidas_roundtrip` hardcodes "ASIN-X"/"BUYABLE" in the dump — roundtrip reconstructs; equality rec == esperado holds since Recuadro only depends on fields preserved. Passed anyway.

  6. One genuine nit: in `_pub`, `ext = ext or sku.replace("SKU", "ASIN")` — fine.

  7. test_fuentes_status_contiene_buyable_y_manda_la_ultima: comment "la BUYABLE vieja sigue contando si es la ultima" is misleading — the CLOSED is older, the BUYABLE,DISCOVERABLE is newer; nothing about vieja being last. Minor comment inaccuracy (baja).

  Also the DISTINCT ON tie: two rows same observed_at (both now) — ordering ties broken by l.id, but status filter applied after; either picked row contains BUYABLE so test stable. OK.

  8. Security: tests use subprocess with fixed args, no injection. DSNs in env of subprocess include credentials to local test DB only. Fine.

  9. test_tool_error_de_base_es_exit_2 creates DB `cob_pelada_...` and drops with FORCE — fine.

  One more: `_CONTADOR = itertools.count()` module-level + pid ensures unique DB names across parallel pytest workers? Same pid different workers — pid unique per process. OK.

  Potential real issue: `test_guion_acepta_puente_activas_e_identidad` — `hoy` queried from DB but files built with listing ids 1,2 hardcoded — fine as guion only reads files.

  Everything verified and the full file passes (32/32, zero skips — Postgres available). The only thing I found is the misleading comment at line ~583. Also VERIFICAR item: SET ROLE app_read depends on connecting user being superuser/member — passed here with compose default; with a restricted ORBIT_TEST_DSN it would fail. Report as baja/VERIFICAR.

  Also check: the test docstring says ORDENCOB includes "0035 ... no esta en ORDEN39, que no se toca" — fine.

  One more check: `_decision` for "no_evaluado" with motivo provided — in test N1/N2 use motivo fee_ausente/oferta_desactualizada. CHECK resultado vocab includes no_evaluado, motivo required — ok.

  Response in Spanish, plain text, numbered findings with severity. Findings are minor; nearly LGTM. I'll give the list.

To resume this session: kimi -r session_c0157af6-8daf-433e-847e-bd559ce41b0d


=== Revisor efectivo: kimi (binario 'kimi') ===


---
sha=0d88cc818d62c89dd9b031dc472043bd52115b23 archivos=tests/test_precio_cobertura.py inicio=2026-09-18T18:08:17Z fin=2026-09-18T18:11:21Z exit=0
