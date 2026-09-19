=== quality-kit cross-review.ps1 ===
Repo: /Users/dn/dev/_wt/f11-kimi-r1
CLI: kimi
Alcance: last-commit
Archivos (pathspec de la tarea): app/cli.py, app/precio/cuota.py, app/precio/tipos.py, app/spapi/precio_write.py, docs/DEPLOY.md, tests/test_architecture.py, docs/evidencia/repricing-01/A.5/mutantes.md
Tamano del diff: 46928 caracteres
==> Invocando kimi de forma no interactiva (tope: 1500 s)...

=== Respuesta de kimi (codigo de salida: 0) ===
• Voy a leer el diff y luego verificar el contexto necesario en el repo.

• Ahora verifico en el repo los puntos que el diff no muestra: firmas de `leer_precio_vivo` y `CuboTasa`, el detector `_usos_reloj`, y de donde sale `hoy` en la corrida.

• Verifico lo ultimo: imports en `cli.py` y el comportamiento de `construir_escritor` con `meli`.

• LGTM



=== stderr ===
kimi version 0.39.1
• The user explicitly says: act as an external code reviewer, no skills, respond directly. Read the diff file and review. Let's read it.

• The output was saved to a file. I need to read the full diff (832 lines). Let me read the saved output file in pages.Now I have the full diff. Let me analyze potential issues and verify against the repo.

  Key observations / candidate findings:

  1. `app/cli.py` `_precio`: in the non-reporte path, `_conexion_decide` uses `ORBIT_DSN_DECIDE`, but the reporte path uses `ORBIT_DSN_READ`. DEPLOY.md says `ORBIT_DSN_DECIDE` must be present inside the container — consistent. Fine.

  2. `cuota.py::reservar`: INSERT with `cap` passed by caller and `WHERE apply_quota_state.used + %s < apply_quota_state.cap`. The guard uses the row's cap (trigger-set), good. But note: on INSERT conflict path, if the trigger sets cap from config and caller's `cap` differs, the INSERT inserts caller's cap — the docstring says trigger fija cap. On INSERT (no conflict), the row is inserted with used=1 unconditionally (no guard beyond `cap - extra <= 0` pre-check). Fine-ish.

  Potential race: two concurrent transactions both doing INSERT...ON CONFLICT — that's atomic via row lock. OK.

  3. `reservar` INSERT uses `(now() AT TIME ZONE 'UTC')::date` — server time. But repo rule says "invariantes de tiempo van en trigger con UTC fijado". Here it's inline SQL in app code. Note `cuota.py` has no clock per architecture test (no Python clock, but SQL now()). `reversas_hoy` takes `hoy` param, but `reservar` computes the date from DB server time. Minor inconsistency: `hoy` passed to reversas_hoy presumably computed where? Not in the diff — VERIFICAR. If corrida computes `hoy` from a clock... corrida.py is not allowed reloj. Actually corrida can't use datetime.now — probably from observation data. Not in diff; the diff is limited to the task files. cuota.reservar uses DB now() UTC. reversas_hoy receives `hoy`. If corrida computes `hoy` from DB too, fine. VERIFICAR: consistencia entre `hoy` de reversas_hoy y la quota_date de reservar (una viene de parametro, otra de now() UTC en la base; si la corrida calcula `hoy` con otra fuente, una reversa podria contarse en un dia distinto del cupo). Let me check corrida.py in the repo.

  4. `_precio` bug: in the non-reporte branch, if `LockOcupado` returns 0 — OK by design.

  5. `resumen.errores` printed with `scrub(error)` — errors are strings already scrubbed? fine.

  6. CLI: `_clientes_precio` imports inside function — fine. `limitador = CuboTasa(... tasa=0.5)` — CuboTasa signature: capacidad=1, tasa=0.5. Verify CuboTasa exists in app/spapi/client.py and signature.

  7. `precio_write.cambiar_precio` now accepts `limitador` and passes to `leer_precio_vivo` and `_escribir_y_sellar`. Check that `leer_precio_vivo` accepts `limitador` kwarg — verify in repo. The diff shows `_escribir_y_sellar` docstring mentions limitador already existed as param. Verify `leer_precio_vivo` signature.

  8. test_architecture.py: `_fugas_imports_corrida` — `_permitido` checks stdlib first: `nombre.split(".")[0] in _sys.stdlib_module_names`. Note: `import sys as _sys` inside function — fine. But potential issue: `PROHIBIDOS_PRECIO` entries starting with "<" are skipped in `_import_prohibido_corrida` via `if not entrada.startswith("<")`. OK.

  Potential issue: `_import_prohibido_corrida` checks `nombre == entrada or nombre.startswith(f"{entrada}.")`. For ImportFrom with candidates including `module.name`, e.g. `from app.precio import corrida` → candidates `app.precio`, `app.precio.corrida`. `app.precio.` permitido via prefix... `app.precio` vs permitido `"app.precio."`: `nombre == p`? No, p = "app.precio." with trailing dot. `nombre.startswith(p.rstrip(".") + ".")` → `app.precio.` startswith "app.precio." yes. OK. `from app.precio.cuota import reservar` → candidato `app.precio.cuota.reservar` — starts with "app.precio." → permitido. But `reservar` isn't a module — the check treats names as modules; harmless.

  Edge: `test_candado_cuota_caza_reloj_y_entorno` asserts `_usos_reloj(arbol) != []` for code that imports os and uses `os.environ.get(__import__('x'))`. Does `_usos_reloj` detect os.environ? The test name says "reloj y entorno". Need to check `_usos_reloj` definition — presumably it detects both os.environ and time/datetime. Verify in repo that `_usos_reloj` flags `os.environ.get`. If it only flags reloj, the test would fail — but presumably tests pass. Verify.

  9. cuota.py `reservar`: parameters `(motor_cuota(platform), cap, extra)` — SQL has placeholders: VALUES (%s, ..., %s, 1) → motor, cap; WHERE ... used + %s < cap → extra. Three placeholders, three params. Good.

  10. Potential bug in `reservar`: the INSERT path when there is no existing row — inserts used=1 with the caller's `cap`, but the docstring says trigger fija cap desde config. If trigger overrides on INSERT, fine. But also the pre-check `if cap - extra <= 0: return False` uses caller cap, while the ON CONFLICT guard uses row's cap (trigger-set). If config changed midday... trigger behavior not in diff — VERIFICAR? The docstring says "si difiere, el trigger revienta". Check migration 0039 for trigger apply_cap_de_config.

  11. `reversas_hoy` counts `enviado_at AT TIME ZONE 'UTC')::date = hoy` — reversas that are pendiente (not enviado, enviado_at NULL) don't count; but reversas are run by owner outside corrida. Fine per docstring.

  12. `_precio` — `except corrida_precios.LockOcupado ... return 0`. Hmm, lock occupied = success exit 0. Design decision; documented.

  13. In `main`, `args.comando == "precio"` placed before `return _ingest(args, rest)`. Need `comando` positional includes "precio" — added via sub.add_parser. Fine.

  14. `docs/DEPLOY.md`: cron line uses `docker exec orbit-app-1` without env — documented. Test pins the line. OK.

  15. One possible real bug: `_precio` reporte branch validates `desde > hasta` returns 2; fine.

  16. `_conexion_decide` returns `conn, conn.close` — cerrar is bound method; called in finally. Fine.

  17. `cuota.py` `_numero`: `Decimal(str(valor).strip() if isinstance(valor, str) else str(valor))` — both branches str(valor); redundant but harmless.

  18. `reservar` cap param: caller validated cap int 0-20. If cap=0, `cap - extra <= 0` → False return. Good.

  19. Missing: check `connect` and `scrub` and `socket`, `dt`, `os`, `sys` imports exist in cli.py (pre-existing, likely yes since _cycle uses them).

  Let me verify key things in the repo:
  - `leer_precio_vivo` accepts `limitador`.
  - `CuboTasa` signature in app/spapi/client.py.
  - `_usos_reloj` detects os.environ.
  - migration 0039 trigger behavior for apply_quota_state cap.
  - corrida.py `hoy` source for reversas_hoy.

  Let me grep.

• corrida.py:1436 `hoy = ahora.date()` — and corrida.py:13 says "El reloj es el de la base (`SELECT now()` una vez)". So `ahora` comes from `SELECT now()` in UTC presumably. Check line ~1430 for `ahora`. Also `reservar` uses `(now() AT TIME ZONE 'UTC')::date` — consistent if `ahora` is UTC now(). Fine.

  Now check migration 0039 trigger apply_cap_de_config and `precio_cambio` columns `enviado_at`, `es_reversa` exist (they're pre-existing per A.4 presumably). Check `_escribir_y_sellar` accepts `limitador` param. Also check `PROHIBIDOS_PRECIO` includes entries with "<" prefix, and that `corrida.py`'s actual imports pass — that's tested.

  One thing to check: `_fugas_imports_corrida` — `_permitido` uses stdlib check first: any module whose top-level name is in stdlib_module_names passes even if also in PROHIBIDOS? No — `if _import_prohibido_corrida(alias.name) or not _permitido(alias.name)` — prohibited check first, so `import os` → prohibited? Is "os" in PROHIBIDOS_PRECIO? Check.

  Also check `reservar` race nuance: `INSERT ... ON CONFLICT ... WHERE used + extra < cap` — with autocommit, two concurrent reservar calls: the ON CONFLICT DO UPDATE takes row lock, serialized. OK.

  Another check: `_precio` in cli.py — `except corrida_precios.LockOcupado` returns 0 — intended.

  Check `_escribir_y_sellar` signature includes `limitador` already (docstring says so). Let me verify quickly. Also verify migration 0039 trigger. And whether `precio_cap_<platform>` config keys / trigger `apply_cap_de_config` exist.Consistent: `hoy` from `SELECT now() AT TIME ZONE 'UTC'`, and `reservar` uses `(now() AT TIME ZONE 'UTC')::date` — same source (DB clock). Good.

  Check the trigger on INSERT of apply_quota_state (0002: `apply_quota_state_cap` trigger?) — the trigger fija cap from config on INSERT. In cuota.reservar the INSERT passes caller cap; does the trigger override NEW.cap on INSERT? Look at 0002 lines ~500-520.Trigger on INSERT enforces cap == config value; the docstring's claim matches. The `reservar` ON CONFLICT guard uses `apply_quota_state.cap` (row), consistent.

  Remaining checks:

  1. `_precio` reporte branch: uses `ORBIT_DSN_READ`, corrida branch uses `ORBIT_DSN_DECIDE`. DEPLOY says ORBIT_DSN_DECIDE inside container. OK.

  2. cli.py imports: `socket`, `dt`, `os`, `sys`, `connect`, `scrub` — check they're imported at module level in cli.py. Let me grep quickly.

  3. In `_precio`, `--platform` choices include meli; `construir_escritor(lector, platform)` — check it handles "meli" or raises. If meli not supported, what happens? Verify `construir_escritor` in precio_write.py.

  4. `reservar` extra param: `cap - extra <= 0` pre-check uses caller cap; fine.

  5. Potential issue: `reservar` INSERT when no row exists and `cap - extra > 0`: inserts used=1 even if... extra >= cap handled. But what if extra>0 and cap-extra>0, INSERT happens used=1 — total consumption extra+1 ≤ cap OK.

  Race: two concurrent corrida processes? Lock prevents. OK.

  6. test_architecture: `_PATRON_TABLA_ESCRITA` — regex `(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+(?!SET\b)([A-Za-z_]...)`. For "UPDATE\n            ads_optimizer_lock" with newline — `\s+` matches newline. OK. `(?!SET\b)` excludes `UPDATE SET`? In "ON CONFLICT ... DO UPDATE SET used = ..." — after UPDATE comes SET, excluded. Good.

  But: pattern would match `UPDATE` followed by `(SELECT ...)`? Parenthesis not matched by `[A-Za-z_]` — then no match, fine.

  Potential gap: multiword like `INSERT INTO` with newline between — `\s+` covers.

  7. `_tablas_escritas` uses `_textos_constantes_fuentes` — pre-existing helper. OK.

  8. `_fugas_imports_corrida` skips TYPE_CHECKING blocks — fine.

  One more: `test_precio_excepcion_por_nombre` now includes corrida.py and cuota.py in EXCEPCIONES_PURAS_PRECIO, meaning those two are exempt from the purity checks for app/precio — and they get their own candados. Reasonable.

  9. Check `construir_escritor` handles "meli". Also check `ProductFeesClient` exists in app/estimacion_fees.

  10. Check cli.py imports of socket.

• All consistent. `_clientes_precio` passes `platform` positionally to `construir_escritor(lector, platform)` — matches signature. meli: SpapiWriteClient would presumably fail for meli at some point — but corrida for meli likely fails earlier or goals don't exist; cron only schedules amazon_mx. `SpapiClient()` for meli might raise — that would be caught by generic except → exit 1. Acceptable.

  One remaining nuance worth flagging? In `_precio`, `resumen.errores` printed after `cerrar()` in finally — fine since resumen already computed.

  Minor: `_clientes_precio` creates SpapiClient/ProductFeesClient — do they read env/secrets at construction? Not relevant for review depth.

  Potential finding: in `_precio` non-reporte path, `_conexion_decide` connects before validating that config exists — fine.

  Another check: `test_cuota_sin_reloj_ni_entorno_ni_dinamico` and the seeded-leak test: `_cuota_mas("import os  # fuga\nfuga = os.environ.get(__import__('x'))\n")` → `_usos_reloj` finds `os.environ` attribute → non-empty. `_usos_import_dinamico` finds `__import__` call presumably. OK.

  One subtle thing in `_fugas_imports_corrida`: stdlib check `nombre.split(".")[0] in _sys.stdlib_module_names` — `import os` passes _permitido, but os must be in PROHIBIDOS_PRECIO for the veto to catch it. Check PROHIBIDOS_PRECIO includes "os".All verified. The change is consistent: signatures match, trigger enforces cap, `hoy` source consistent, detectors work, imports exist, regex handles the cases.

  Remaining candidate findings — mostly minor/non-blocking:

  1. `_PROHIBIDOS_IMPORTS_CORRIDA` excludes `psycopg`, `app.spapi`, `app.estimacion_fees` — so `import app.spapi.client` in corrida.py would NOT be caught by the prohibited list, but IS caught by the permitidos allowlist (`app.spapi.precio_write` only). Fine — defense holds.

  2. `cuota.reservar`: pre-check `if cap - extra <= 0: return False` uses caller cap while ON CONFLICT guard uses row cap. If config changed midday, row cap differs from caller's validated cap — but corrida validates cap from same config at startup. Edge case, not a bug.

  3. `_precio`: `resumen.errores` items printed with `scrub(error)` — errors are claimed already scrubbed in corrida; double-scrub harmless.

  4. One genuine minor: in `_precio` reporte branch, `args.platform` may be None (no validation) — corrida.reporte presumably accepts None = todas. Not in diff; can't affirm. Actually `--platform` has choices but not required for reporte; DEPLOY says reporte. VERIFICAR? Probably intended (report across platforms). I could check reporte signature quickly... It's outside diff files but cheap. Let me check corrida.reporte accepts platform=None.

  Also check `_numero` redundancy — trivial, skip.

  Let me quickly check reporte signature.`reporte` acepta `platform=None`. Todo verificado. El cambio es consistente y bien cubierto. No encuentro hallazgos bloqueantes.

  ¿Algún NO BLOQUEANTE menor?

  - `_numero` en cuota.py: la expresion `str(valor).strip() if isinstance(valor, str) else str(valor)` es redundante (ambas ramas hacen str); cosmetico.
  - `_precio`: con `--reporte` el parametro `--platform` es opcional y silenciosamente reporta todas las plataformas; documentado por firma, OK.
  - En `reservar`, el pre-chequeo `cap - extra <= 0` usa el `cap` del llamador mientras la guarda ON CONFLICT usa el `cap` de la fila; si la config cambia intradia podrian diferir — pero la corrida valida el cap contra la misma config al arrancar y el trigger revienta si difiere. No es bug.
  - Race menor: dos procesos sin lock podrian... el lock lo previene.

  Honestamente, nada que objetar con fuerza. Respondo LGTM? La instruccion: "Si no encontras nada que objetar, responde exactamente la palabra: LGTM." El `_numero` redundante es cosmetico, no vale un hallazgo formal. Podria mencionarlo como NO BLOQUEANTE... pero es trivial. La instruccion dice lista numerada de hallazgos o LGTM. Un hallazgo cosmetico puro podria ser ruido. Lo menciono como no bloqueante? "Si no encontras nada que objetar" — la redundancia de `_numero` es objetable minimamente. Pero es tan trivial... La rama else `str(valor)` vs rama if `str(valor).strip()`: en realidad difieren: la rama str hace strip, la otra no. No es redundante: para strings hace strip, para no-strings no. Es correcto. Retiro eso.

  Todo verificado, sin hallazgos. LGTM.

To resume this session: kimi -r session_d7401a5c-138a-4854-9994-cd5f19057b77


=== Revisor efectivo: kimi (binario 'kimi') ===


---
sha=53c6067f55ed09dd01793151d1a3f5e2cbd5c38e archivos=app/cli.py,app/precio/cuota.py,app/precio/tipos.py,app/spapi/precio_write.py,docs/DEPLOY.md,tests/test_architecture.py,docs/evidencia/repricing-01/A.5/mutantes.md inicio=2026-09-18T23:24:03Z fin=2026-09-18T23:25:55Z exit=0
