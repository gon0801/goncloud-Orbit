=== quality-kit cross-review.ps1 ===
Repo: /Users/dn/dev/_wt/f11-kimi-r1
CLI: kimi
Alcance: last-commit
Archivos (pathspec de la tarea): tests/test_precio_pantalla.py
Tamano del diff: 112395 caracteres (truncado a 60000)
==> Invocando kimi de forma no interactiva (tope: 1500 s)...

=== Respuesta de kimi (codigo de salida: 0) ===
• Voy a verificar contra el repo: el resto del archivo, `app/notifica.py` y las migraciones.

• PG esta arriba. Corro la bateria del archivo y verifico el hook `_transporte_test`.

• Mientras corre pytest, verifico imports y helpers referenciados.

• 74/74 en verde con PG real. Verifico lint del archivo como ultimo chequeo.

• Revise el diff completo (el archivo de la tarea estaba truncado en ~60KB; lei el resto directo del repo), verifique los simbolos referenciados contra `app/notifica.py`, `app/api_dashboard.py`, `tests/test_precio_corrida.py` y `tests/test_schema.py`, y corri la bateria real: `uv run --frozen python -m pytest tests/test_precio_pantalla.py -q` -> 74 passed, 0 skipped (PG real en localhost:5432), y `ruff check` + `ruff format --check` en verde.

  Hallazgos:

  1. NO BLOQUEANTE: `_html_precios(conn, dsn, monkeypatch)` (linea ~904 del archivo) recibe `conn` y no lo usa; igual `_db_pantalla(sufijo="")` con `sufijo` (documentado como compat, pero es firma muerta en un archivo nuevo). Limpieza cosmetica, nada mas.
  2. NO BLOQUEANTE: el par DISABLE/ENABLE del trigger `precio_decision_fecha_utc` es inconsistente entre tests (algunos con `try/finally`, otros como `test_precio_aviso_umbral_flanco_en_base` y `test_r1_a1_...` sin re-enable); inocuo porque la DB temporal se dropea, pero un patron uniforme evitaria sorpresas si el helper cambia.
  3. NO BLOQUEANTE: la asercion de CDN en U10 (`"cero CDN" in html or "cdn" not in ...`) es debil; queda cubierta por el test estricto `test_r1b_b12_cero_cdn_absoluto_en_precios` (regex sobre `src`/`href` absolutos), asi que no hay hueco real de discriminacion.

  Sin hallazgos bloqueantes: los tests discriminan (flanco con hueco, umbral que rechaza `True`, scrub afirmado con WARNING presente en el log, conteos exactos contra SELECT directo), no hay secretos ni salida a red real (Telegram via `httpx.MockTransport` y `_transporte_test`, verificado en `app/notifica.py:57`), y el DDL destructivo (`DROP DATABASE ... WITH (FORCE)`) va solo sobre bases `pp_<uuid>` creadas por el propio helper con `sql.Identifier`.



=== stderr ===
kimi version 0.39.1
• The user explicitly says: act as external code reviewer, do NOT use skills, respond directly this turn. So I skip the superpowers skill loading per explicit user instruction. Read the diff file.The diff is truncated at the end (~1472 lines shown, diff truncated). I need to verify claims against actual code: `app/notifica.py`, `app/api_dashboard.py`, `tests/test_precio_corrida.py` helpers, and run the test file possibly.

  Key things to verify:
  1. Does `app/notifica.py` have all the referenced names (GrupoPrecio, ProductoPrecio, HuerfanaPrecio, notifica_precio, aviso_precio_grupo, aviso_precio_producto, aviso_precio_huerfana, flanco_umbral, flanco_nuevos, validar_precio_aviso_dias, avisar_precio, _reset, _transporte_test, canal_activo)?
  2. `_db_nueva` migration list ORDEN59 — do those migrations exist and does 0001/0002 suffice (e.g. config_version, product, listing tables, spapi_listing_estado_observation from 0035, estimacion_oferta_observation from 0028, precio tables from 0039)? Skipping intermediate migrations might break if 0039 depends on tables from other migrations.
  3. Tests: does the test file pass? Running the whole suite needs PG at localhost:5432 — docker-compose has PG. May be available.
  4. The truncation: the diff file was truncated at 60KB, so the last ~portion of the file is missing. I should read the rest of tests/test_precio_pantalla.py directly from the repo.
  5. Potential issues: `_canal_falso` uses `notifica._transporte_test` — a test hook; verify it exists. `test_precio_aviso_flanco_sigue_no_reavisa` uses hardcoded date 2026-09-18 (today) — but flanco_umbral with explicit hoy arg, fine, deterministic.
  6. `_decision` in the flanco base test disables trigger `precio_decision_fecha_utc` and never re-enables it in test 8 (umbral_flanco_en_base) — no try/finally, but DB is dropped anyway. Not a bug per se.
  7. Test 6 `test_precio_aviso_flanco_sigue_no_reavisa` — pure function test.
  8. `test_precio_pantalla_decisiones_exactas_vs_select` asserts `bloque["decisiones"] == {"no_evaluado": 2, "subir": 1}` — dict equality with count types: `count(*)` returns int; dash.precios presumably returns dict from DB — ok.
  9. The `_db_nueva` helper runs migrations 0001, 0002, 0028, 0032, 0035, 0039 — need to confirm 0002 doesn't depend on things between, and 0028/0032/0035 don't depend on migrations 0003-0027. Worth checking quickly whether those migrations reference tables created in-between.

  Best approach: verify the key facts and actually run the test file if PG is available. Let me delegate? The instruction says respond directly this turn, no skills/workflows — but using tools (Read/Grep/Bash) is fine and even requested ("tu directorio de trabajo ES el repositorio real: verificalo primero").

  Plan:
  - Read the remaining part of tests/test_precio_pantalla.py (from ~line 1450 onward).
  - Grep app/notifica.py for the referenced names.
  - Check migrations for cross-dependencies.
  - Try running pytest on the file (check if PG is up: `pg_isready` or just run and see skips).

  Let me do these. First check PG and read file tail + grep notifica.

localhost:5432 - accepting connections
---
0001_initial.sql
0002_apply.sql
0003_goal_bounds_explicit.sql
0004_ad_entity_kind_product_ad.sql
0005_v_tacos_grano_unico.sql
0006_contribucion_entidad.sql
0007_contribucion_perf.sql
0008_precio_multilisting_us.sql
0009_contribucion_redondeo.sql
0010_contribucion_cross_review.sql
0011_costo_historico_peseta.sql
0011_reversa_costo_historico_peseta.sql
0012_tacos_residuo_fail_loud.sql
0013_entidad_inerte.sql
0014_keyword_archivo_manual.sql
0015_target_margen_plataforma.sql
0016_target_margen_correcciones.sql
0017_first_seen_at.sql
0018_fabrica_campanas.sql
0019_fabrica_grupo_publicacion_v2.sql
0020_ads_producto_metrica.sql
0021_economia_observada.sql
0022_disponibilidad_snapshot.sql
0023_append_only_bloque_b.sql
0024_reputacion.sql
0025_reputacion_sin_fk.sql
0026_reputacion_preguntas_reobservacion.sql
0027_reputacion_reviews_reobservacion.sql
0028_estimacion_venta.sql
0029_estimacion_politica_vigencia.sql
0030_spapi_orders.sql
0031_reversa_spapi_orders_bitemporal.sql
0031_spapi_orders_bitemporal.sql
0032_spapi_pricing.sql
0033_ingest_run_llamadas.sql
0034_ingest_run_llamadas_grant.sql
0035_spapi_listings_inventario.sql
0036_ingest_run_platform.sql
0037_ingest_run_salud_idx.sql
0038_fabrica_hermanas_biblioteca.sql
0039_precio.sql
• PG is up. Let me run the test file to see if it passes. Also check `_transporte_test` exists in notifica.py (referenced by the test's `_canal_falso`), and check `test_schema._postgres_obligatorio_ausente` and `test_precio_corrida` helpers exist.

  Run: `uv run --frozen python -m pytest tests/test_precio_pantalla.py -q` — could take a while (many tests with real PG, creating databases). Let me run it in background or with a generous timeout. First check `_transporte_test`.

• While the test runs, verify other things:

  1. `_canal_falso` sets `notifica._transporte_test` via monkeypatch — exists, good.
  2. `test_schema._postgres_obligatorio_ausente` — check it exists.
  3. `test_precio_corrida` helpers `_cadena, _corre, _price_obs, _RedFalsa, _goal, _listing, _producto` — check.
  4. `_db_nueva` runs migrations 0001, 0002, 0028, 0032, 0035, 0039 — check that 0028 doesn't depend on migrations 0003–0027 (e.g. references to tables created there). Quick grep: does 0028 reference e.g. `contribucion` or other tables from intermediate migrations? 0039 references? Also 0002 references? Let me grep the migration files for references to tables created in skipped migrations.

  Actually the DB is created and migrations applied in tests — if tests pass, this is verified empirically. Wait for pytest.

  5. Potential quality issues I noticed reading the diff:
     - `test_precio_pantalla_decisiones_exactas_vs_select` — the assert `bloque["decisiones"] == _select_decisiones(...)` — fine.
     - `_db_nueva` uses `DROP DATABASE ... WITH (FORCE)` — PG 13+, fine with PG16.
     - `test_precio_aviso_flanco_sigue_no_reavisa` uses `date(2026, 9, 18)` hardcoded — passed as explicit hoy, deterministic. Fine.
     - In `test_precio_cierre_corrida_real_telegram_caido`: seeds a `frenado/api_error` decision for lid2, then runs `_corre`. Asserts `len(mensajes) >= 1`. Hmm, but the seeded frenado decision is "de hoy" — avisar_precio with flanco would send a frenados group aviso. OK.
     - `test_r1_b4_umbral_invalido_solo_apaga_no_evaluado` — with config `{}`, `validar_precio_aviso_dias` raises ValueError → warning + no_evaluado skipped. But wait: the pantalla R2 test asserts 503 when the key is missing, while B4 asserts avisar_precio just warns. Both consistent with different code paths.
     - `test_r1_b6_un_tipo_roto_no_calla_los_otros`: monkeypatches `notifica._grupos_precio_dia` — but `_avisar_grupos_precio` may call it via module-level reference; monkeypatching module attribute works if `_avisar_grupos_precio` calls `_grupos_precio_dia(...)` unqualified. Fine.
     - U10 test: `assert "cero CDN" in html or "cdn" not in html.lower().replace("cero cdn", "")` — this is a weak assertion but the R1-b test later does the strict regex check. OK.
     - `_html_precios(conn, dsn, monkeypatch)` — `conn` param unused. Minor.
     - `_db_pantalla(sufijo="")` — sufijo unused, documented as compat. Minor.
     - Potential issue: `_canal_falso` writes `telegram.json` to tmp_path/"secrets-precio" and sets ORBIT_SECRETS_DIR. `_reset()` clears cached config. Good.
     - `test_precio_aviso_excepcion_warning_scrub_y_false` — asserts FAKE_CHAT_ID not in caplog.text. Fine.
     - One thing: `_conn_cerrada` class has `execute` raising OperationalError — `avisar_precio` presumably catches broad exceptions. Test asserts returns 0. OK.
     - `test_precio_aviso_umbral_dias`: `{"precio_aviso_dias_sin_evaluar": True}` — bool is instance of int; validator must reject bool. Test asserts ValueError. Good discriminating.

  6. Check rule 8/9 of repo: regression tests demonstrated failing — can't verify, skip.

  7. Rule about migration invariance: tests disable trigger `precio_decision_fecha_utc` — that's the sanctioned way for seeding historic dates presumably (used in other test files too?). Grep quickly.

  8. The docstring in notifica.py for avisar_precio must contain "segunda corrida" and "reenv" — verify.

  9. Also check `dash.validar_precio_aviso_dias is notifica.validar_precio_aviso_dias` — dash imports it. Verify in api_dashboard.py.

  10. Check `dash._acciones_precio` exists, `dash.precios`, `dash.salud` exist.

  Let me grep a few of these while pytest runs.

• 74 passed, 0 skipped — with real PG. Good.

  Now a few remaining checks:

  1. `test_precio_aviso_flanco_sigue_no_reavisa` uses `date(2026, 9, 18)` — but wait, the test names: `test_precio_aviso_flanco_sigue_no_reavisa` asserts `flanco_umbral(presentes, hoy, 3) is False` with presentes = last 4 days including hoy. Hmm — that seems odd: 4 consecutive days with umbral 3 → flanco should be... depends on semantics. flanco_umbral(presentes, hoy, 3) returns True only when the streak reaches exactly the threshold (flanco = edge, i.e., today completes the streak but yesterday it didn't). With 4 days present, the streak already passed the threshold yesterday → no edge → False. Consistent with test 7 where 3 days → True. Verified logically; and tests pass.

  2. Check ruff on the file? Pre-commit would catch. Quick: `ruff check tests/test_precio_pantalla.py`. Might be worth it.

  3. Discrimination check (repo rule: tests must discriminate). Spot-check a couple of tests for tautologies:
     - U10: `assert "cero CDN" in html or "cdn" not in html.lower().replace("cero cdn", "")` — weak but there's the strict R1-b regex test later. OK.
     - `test_precio_aviso_umbral_flanco_en_base` disables trigger and doesn't re-enable — but DB dropped. Other tests use try/finally inconsistently (some do, some don't). Not a bug.
     - `_html_precios(conn, dsn, monkeypatch)` — unused `conn` param; cosmetic.
     - `_db_pantalla(sufijo="")` unused param, documented as compat. Cosmetic.

  4. One thing to check: `test_precio_pantalla_decisiones_exactas_vs_select` compares `bloque["decisiones"]` (from dash.precios, likely dict resultado→count as ints) with `_select_decisiones` — both from DB, int. Passes.

  5. Data-seeding realism: `_decision` inserts with `p_actual_currency` etc but no p_actual values — allowed by CHECK presumably. Tests pass so constraints satisfied.

  6. Security: no secrets; fake tokens; Telegram mocked; DDL on temp DBs with uuid names; DROP DATABASE FORCE on test DBs — name is `pp_<8 hex>`; uses psycopg sql.Identifier — safe.

  7. One potential quality issue: `_db_nueva` connects to default DSN db "postgres" and creates DBs — if ORBIT_TEST_DSN points at production-like DB... it's the sanctioned test pattern (ORBIT_TEST_DSN). Fine.

  8. `test_precio_aviso_apagado_devuelve_true` — constructs ProductoPrecio with positional args; fine.

  9. Check that test file doesn't leak DBs on failure: `_db_nueva` drops in finally only if created; if CREATE DATABASE fails midway migrations, `creada=True` set right after CREATE, so drop happens. Good.

  10. Ruff check quick. Also verify the file compiles (it does — tests ran).

  Also should I check whether `mensajes == []` asserts in flanco base test: `avisar_precio` returns 0 because racha 2 < 3, then lid2 reaches 3 → 1 aviso. OK.

  Run ruff quickly on the file.

All checks passed!
1 file already formatted
• Everything passes. Now compose findings. Are there any real findings?

  Minor observations (NO BLOQUEANTE):
  1. `_html_precios(conn, dsn, monkeypatch)` — parametro `conn` sin uso; `_db_pantalla(sufijo="")` parametro `sufijo` sin uso (documentado como compat). Cosmetic.
  2. Test U10 assertion `"cero CDN" in html or "cdn" not in ...` is weak, but reinforced by strict test test_r1b_b12_cero_cdn_absoluto_en_precios. Not an issue.
  3. Inconsistent trigger re-enable: some tests use try/finally, others don't (e.g. test_precio_aviso_umbral_flanco_en_base, test_r1_a1...) — but DB is dropped afterwards, so harmless.
  4. Diff truncated in the review file — but I read the full file from repo. Fine.

  Given tests pass, lint passes, symbols verified, and the tests discriminate (spot-checked flanco, umbral bool rejection, scrub assertions with actual WARNING present), I could answer LGTM with maybe minor non-blocking notes. The instruction: "Si no encontras nada que objetar, responde exactamente la palabra: LGTM." I have only trivial cosmetic notes. I could list them as NO BLOQUEANTE or say LGTM. The unused `conn` parameter in `_html_precios` is a genuine minor nit — ruff doesn't flag unused args. I'll report it as one NO BLOQUEANTE finding rather than LGTM, since it's something to object to, however minor. Actually two minor items: unused `conn` param and unused `sufijo` param. And inconsistent try/finally. I'll list them as non-blocking, one line each.

To resume this session: kimi -r session_715fa01f-64a3-4af7-9f22-4556d209a704


=== Revisor efectivo: kimi (binario 'kimi') ===


---
sha=510beda33015edaba4c0aaddf2471974851ffc15 archivos=tests/test_precio_pantalla.py inicio=2026-09-19T03:00:43Z fin=2026-09-19T03:02:28Z exit=0
