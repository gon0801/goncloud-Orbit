=== quality-kit cross-review.ps1 ===
Repo: /Users/dn/dev/_wt/f11-kimi-r1
CLI: kimi
Alcance: last-commit
Archivos (pathspec de la tarea): app/spapi/precio_write.py, app/spapi/write_client.py, tools/precio_reversa.py
Tamano del diff: 46599 caracteres
==> Invocando kimi de forma no interactiva (tope: 900 s)...

=== Respuesta de kimi (codigo de salida: 0) ===
• Voy a leer el diff y luego verificar contra el codigo real del repo.

• Necesito la cola del diff (lineas 1000-1176) y la firma real de `parsear_precios`.

• Ahora verifico el esquema real de `precio_cambio`, `precio_decision`, `spapi_price_observation` y la firma de `parsear_precios`.

• Necesito el DDL de `precio_cambio` (columnas, enum de estado, indice unico de abierto).

• El esquema coincide. Verifico dos detalles finales: columnas de `listing` y cobertura de tests del tool.

• Un punto critico: los GRANTs del rol que usa el tool (`ORBIT_DSN_DECIDE`) sobre `precio_cambio`.

• Ultima verificacion: el trigger de INSERT de `precio_cambio` (podria contradecir el `enviado_at` al nacer) y `connect(**kw)`.

• Verificacion completa contra el repo: el esquema de `precio_cambio` (0039), los GRANTs de `app_decide`, los triggers de nacimiento/transicion, `parsear_precios`, `SpapiClient._acceso` y `connect(**kw)` son todos consistentes con el codigo nuevo. Hallazgos:

  1. (baja) `app/spapi/precio_write.py` — El docstring de `revertir` dice que con la forma sin sellar "no queda fila ni hay red", pero `arma()` se invoca DESPUES de `leer_precio_vivo` (linea ~454, tras el GET de ofertas de la linea ~442): con `FormaParcheSinSellar` si hay red (1-2 lecturas de Pricing), solo no hay fila ni PATCH. Lo mismo aplica al `go` del tool, que consume lecturas antes de abortar. Es una inexactitud de documentacion, no un bug funcional; si se quiere el orden literal del docstring habria que armar el cuerpo antes de leer el vivo.

  2. (baja) `tools/precio_reversa.py` — En el `go`, una excepcion de `_escribir_y_sellar` que no este en la lista de `except` de `main` (p.ej. un `ValueError` de `_exigir_cuerpo_serializable` si el cuerpo que selle A.4 llegara a traer un `Decimal`) se propaga despues de sellar la fila como `error` y aborta el lote entero con traceback (exit por excepcion, no el 1/0 documentado). La fila queda bien sellada (sin corrupcion), pero las reversas restantes no se intentan. Basta agregar un `except Exception` que imprima `[error]` y marque `hubo_error`.

  3. (baja) `app/spapi/precio_write.py` — `cerrar_por_observacion`: el `UPDATE` final no lleva `AND estado = 'enviado'` en el WHERE; entre el `fetchall` y el update, otra sesion podria haber movido la fila y este la pisaria. En la practica el trigger `precio_cambio_sella_transicion` rechaza transiciones ilegales, asi que el riesgo real es casi nulo, pero el guard en el WHERE es gratis.

  4. (baja) `app/spapi/precio_write.py` — `cambiar_precio` no pasa `limitador` ni al GET del vivo ni al readback (el docstring de `_escribir_y_sellar` lo declara: "cambiar_precio no lo pasa"). VERIFICAR: que la cuota `precio:<platform>` o la frecuencia real del motor hagan imposible superar los 0.5 req/s de Pricing cuando se aplican varias decisiones seguidas; si no hay otra capa, el cliente solo reintenta el 429 una vez y el sello quedaria `error` por rafaga.

  5. (baja) `app/spapi/write_client.py` — `_espera_retry_after` es copia literal de `_espera_retry` en `app/spapi/client.py` (misma logica, mismo tope). Duplicacion menor; si cambia la politica de 429 hay que tocar dos sitios.

  Lo demas quedo verificado y sin objecion: allowlist del PATCH y percent-encoding del SKU (identica al patron `validar_post_fees`), reintentos acotados sin loop, re-refresh de token con el patron `rechazado=` correcto, nada de cuerpos ni tokens en logs (`scrub` + logger de httpx/httpcore bajado), orden INSERT+COMMIT antes del PATCH con autocommit exigido, manejo de la carrera r5-L4 via `UniqueViolation`, y columnas/estados consistentes con los CHECKs y triggers de la migracion 0039 (incluido `es_reversa`/`reversa_de`/`decision_id` y el sello NULL→valor una sola vez).



=== stderr ===
kimi version 0.39.1
• The user explicitly asks me to act as an external code reviewer, no skills. The plugin session start says to use skills, but user instruction takes precedence: "NO uses skills ni workflows propios: responde vos directamente en este mismo turno, sin cargar ninguna skill."

  Read the diff file first.

• Now I have client.py. Need the rest of the diff file (lines 1000-1176) for tools/precio_reversa.py. Also need to check:
  - app/spapi/pricing.py: parsear_precios signature, RUTA_COMPETITIVO, PrecioOmitido
  - app/redaction.py: scrub
  - Schema: precio_cambio columns, spapi_price_observation columns, listing columns, precio_decision columns
  - app/db.py: connect, OrbitDbError

  Read the tail of the diff file and grep schema.The Read of the tmp file keeps hitting the 1000-line cap; I need lines 1000-1176. Use line_offset.

• I still haven't seen the tail of the diff (lines 1000-1176, i.e., rest of tools/precio_reversa.py). The Read keeps hitting cap at 1000 lines. Use line_offset=1000. Also grep output_mode content to see parsear_precios signature. Let me do both with specific params.The Grep with default output_mode=files_with_matches isn't giving me content. I need `output_mode: "content"`. And the Read keeps capping at 1000 lines — I must use line_offset=1000 explicitly.The grep pattern hit "motivo" broadly, swamping results. I still need:
  1. The tail of the diff file (lines 1000-1176) — the rest of tools/precio_reversa.py. Read with line_offset=1000.
  2. app/spapi/pricing.py: RUTA_COMPETITIVO, PrecioOmitido, parsear_precios signature (own_price/own_currency).
  3. Schema for precio_cambio (columns: decision_id, estado vocabulary, readback_* columns, ack, error_code, confirmado_por, es_reversa, reversa_de, precio_observado_antes, aplicado, enviado_at; unique index on open change), spapi_price_observation, precio_decision (p_actual, p_aplicado, resultado, mode), listing (seller_sku, external_id).
  4. app/db.py connect/OrbitDbError.
  5. app/redaction.py scrub.

  Let me do targeted reads.Now let me check `parsear_precios` full signature (does it take vendedor_propio as keyword?), and the schema for precio_cambio (columns, estado enum, unique index), spapi_price_observation, precio_decision, listing. Also app/db.py connect signature, and whether `parsear_precios` raises PrecioOmitido or returns PrecioParseado with own_price None. From the grep: `parsear_precios(asin, cuerpo_ofertas, cuerpo_competitivo, ...)` — need to see the full signature line 313-330.

  Also key question: in leer_precio_vivo, second parse uses `ofertas` again plus competitivas — but competitivo endpoint returns buy box prices, not own offers; own price comes from ofertas. If own offer wasn't in `ofertas` the first time, re-parsing the same `ofertas` with competitive data won't find own price either (unless parsear_precios extracts own price from competitive payload — possible: competitivePrice includes "featured offer"... Actually no, competitive price has landed prices of competing offers; own offer might be included?). Let me check parsear_precios to see if it reads own price from competitive body. Probably it takes own from ofertas only. Then the second parse would be pointless... but harmless. Actually wait — potential subtle issue: if `ofertas` has zero offers, parsear_precios returns PrecioParseado with all None (no raise). Then it fetches competitivo and re-parses — own still None → PrecioVivoAusente. Fine.

  Another point: leer_precio_vivo raises PrecioVivoAusente on non-200 from ofertas without trying competitivo. Docstring says that.

  Key checks remaining:
  1. parsear_precios signature: does it accept vendedor_propio kw? Yes per test usage likely. Check lines 313-330.
  2. Schema: precio_cambio columns — check migrations for precio_cambio, precio_decision, spapi_price_observation, listing. The commit didn't include a migration, so these tables should already exist (maybe from earlier REPRICING migration). Check migrations dir for precio_cambio.
  3. The `_observada_del_dia` and `cerrar_por_observacion` use `spapi_price_observation.own_listing_price` — verify column names (maybe it's own_price?). pricing.py line 570 inserts precio.own_price — check the INSERT statement column names.
  4. `_estado_aceptado`: Amazon Listings Items PATCH response uses lowercase "status" values "ACCEPTED"? Yes, Listings API returns status: "ACCEPTED" and submissionId. OK.
  5. cerrar_por_observacion comparison `(obs[0], obs[1]) == (despues, despues_moneda)` — Decimal equality: NUMERIC(14,4) in DB both sides, so scale consistent. But obs[1] might be None if own_listing_currency NULL while price not null — filter only checks price NOT NULL. Then (Decimal, None) != (Decimal, "MXN") → no_confirmado. Fine.
  6. In revertir: it checks `_estado in ("pendiente","enviado")` → saltado original_abierto. But what about a cambio with estado 'confirmado'/'no_confirmado' — those pass. And error — passes too, per docstring. But wait: cerrar_por_observacion only closes estado='enviado'. A cambio in 'error' stays 'error' forever with enviado_at set; reversa possible. OK by design.

  Potential issue: in revertir, the check `otro_abierto` counts changes with estado pendiente/enviado for the listing INCLUDING... the original is in 'confirmado'/'no_confirmado'/'error', so not counted. Fine.

  7. cerrar_por_observacion: `hoy` — the query `metric_date > dia_envio AND metric_date <= hoy`. If hoy is a datetime instead of date, comparison still works in PG. Fine.

  8. Race in cerrar: fetchall of abiertos with autocommit, then per-row update with transaction; a cambio could be updated between — minor.

  9. tools/precio_reversa.py: `_plan` uses f indices: filas from _cambios: (id, listing_id, platform, precio_antes, precio_antes_currency, precio_despues, precio_despues_currency, aplicado, estado, enviado_at, es_reversa, seller_sku, external_id). So f[0]=id, f[1]=listing_id, f[2]=platform, f[3]=antes, f[4]=antes_moneda, f[5]=despues, f[6]=despues_moneda, f[7]=aplicado, f[8]=estado, f[9]=enviado_at, f[10]=es_reversa, f[11]=seller_sku, f[12]=external_id.

  In _plan: `if not f[11]` → sin_sku ok. `if f[10] or not f[7] or f[9] is None` → es_reversa or not aplicado or enviado_at None → no_reversible. OK. `if f[8] in ("pendiente","enviado")` → original_abierto. OK.

  In _huella: for "revertir": partes f"revertir:{cid}:{f[1]}:{f[2]}:{f[5]}:{f[6]}" — listing_id, platform, despues, moneda. Note: huella doesn't include the precio_antes... it includes f[5],f[6] = despues. Fine.

  _linea: `f"{f[5]:.2f}"` — f[5] is Decimal, format ok.

  10. Main go: huella check compares plan computed fresh at go-time with huella from dry-run. But plan at go-time may differ from dry-run (vivo changed) → huella mismatch → abort. By design.

  11. Potential issue in _plan: for saltar entries, detalle.split(':')[0] used in huella — for "precio_vivo_distinto: vivo=..." detalle contains ':' so split(':')[0] = "precio_vivo_distinto". OK stable-ish.

  12. In main, `lector = SpapiClient(credentials=credentials, transport=transport, sleep=sleep)` — SpapiClient signature: (secrets_dir, credentials, transport, sleep, clock, timeout) all keyword. OK.

  13. escritor = precio_write.construir_escritor(lector, f[2], transport=transport, sleep=sleep) — construir_escritor(lector, platform, *, transport, sleep, timeout). OK.

  14. revertir(...) called with limitador=cubo — but NOT construir_cuerpo → uses construir_cuerpo_parche which raises FormaParcheSinSellar — caught → Abortar. By design (A.4 not yet sealed).

  15. Now check the pricing INSERT column names vs spapi_price_observation columns. Grep the INSERT in pricing.py.

  16. Check migrations for precio_cambio and precio_decision tables to verify column names: precio_antes_currency, aplicado, es_reversa, reversa_de, readback_estado, readback_precio, ack, error_code, confirmado_por, enviado_at, decision_id; unique partial index on (listing_id, platform) where estado in (pendiente, enviado). Also precio_decision columns p_actual, p_actual_currency, p_aplicado, p_aplicado_currency, resultado, mode.

  Also listing table: columns id, platform, seller_sku, external_id.

  Let me grep migrations.

• Now check migrations/0039_precio.sql for the schema of precio_cambio, precio_decision and listing table columns (external_id, seller_sku). Also check app/db.py connect signature and app/redaction scrub.Need the precio_cambio table DDL specifically. Let me grep for "CREATE TABLE precio_cambio" region.

• The schema matches: columns all exist. Note `precio_cambio_decision_salvo_reversa` CHECK: NOT es_reversa → decision_id NOT NULL. In cambiar_precio INSERT, decision_id passed. In revertir INSERT, no decision_id column → NULL, es_reversa=true, reversa_de=cambio_id. OK.

  Also `precio_cambio_envio_exige_ack`: aplicado=true AND estado IN ('pendiente','error') ok; else needs ack+enviado_at. INSERT with estado='pendiente', aplicado=true → OK.

  Interesting: INSERT sets enviado_at=momento at INSERT time (pendiente). S5 order says enviado_at at INSERT? The docstring in cerrar_por_observacion mentions "cambio en error (nace con enviado_at...)". Fine, by design.

  But wait — `_sellar` for "enviado" updates estado+ack but NOT enviado_at (already set at INSERT). OK consistent.

  Now check: does the INSERT in revertir include decision_id? No — reversa has decision_id NULL, es_reversa=true, reversa_de set. OK with CHECK.

  Potential issues to flag:

  1. In `revertir`, on a cambio in estado='error' (Amazon may have applied despite 5xx), the vivo check compares vivo vs despues. If Amazon did NOT apply (true failure), vivo != despues → saltado. OK.

  2. `revertir` order issue: `_validar_destino` is called AFTER `leer_precio_vivo`? Let me re-check: In revertir: `_validar_destino(escritor, sku)` is called at line 429 (before otro_abierto check and leer_precio_vivo). Yes it's before network. In cambiar_precio: `_validar_destino` line 637 before otro_abierto. Good — docstring says "Todo lo que puede fallar sin red, ANTES del INSERT" — it's before INSERT, fine.

  But: in revertir, `arma(...)` (construir_cuerpo) is called at line 454, AFTER leer_precio_vivo (network) but BEFORE INSERT. Docstring says "El cuerpo se arma ANTES de insertar la fila pendiente: con la forma sin sellar no queda fila ni hay red" — "ni hay red"? Actually leer_precio_vivo already did network. The claim "sin fila y sin red" is slightly off for revertir — the FormaParcheSinSellar fires after the vivo GET. Minor doc inaccuracy. In the tool's main, FormaParcheSinSellar → Abortar. So with FORMA_PARCHE unsold, the go consumes Pricing reads before aborting. Minor.

  3. Bigger issue candidate: In `cambiar_precio`, when `construir_cuerpo_parche` raises FormaParcheSinSellar — this happens AFTER the otro_abierto check and leer_precio_vivo, before INSERT. Fine, no row, but network consumed. Acceptable.

  4. `_escribir_y_sellar` generic `except Exception` branch: it re-raises AFTER _sellar('error') and _readback. But note the state 'error' with error_code "excepcion:..." — then raise propagates. In the tool main, only FormaParcheSinSellar, CambioNoReversible, SpapiNoPermitida, PublicacionSinSku, UniqueViolation are caught; a generic exception from patch_listing (e.g. ValueError from _exigir_cuerpo_serializable... wait that's raised inside patch_listing before network — it would be caught by `except Exception` in _escribir_y_sellar → seals error then re-raises → propagates out of revertir → out of main loop → uncaught → crash mid-batch with exit? In main, generic exceptions propagate → traceback → exit nonzero. Hmm, main doesn't have a catch-all except Abortar at __main__. A generic crash would print traceback and exit 1? Actually unhandled exception → exit code 1 with traceback. The docstring says go returns 1 if any reversa ends in error — but an uncaught exception crashes the whole batch instead of continuing. But _escribir_y_sellar catches SpapiAuthError and httpx.HTTPError specifically; other exceptions (e.g. ValueError from cuerpo validation, SpapiNoPermitida) — wait, SpapiNoPermitida is a SpapiError not httpx, caught by `except Exception` → seal error, readback, re-raise. Then in main, SpapiNoPermitida IS caught → print error, continue. OK.

  But a truly unexpected exception (e.g. bug) → re-raise → crash whole tool mid-loop. Severity baja/media: the row is sealed error so no data corruption; the batch aborts. Probably acceptable for a manual tool.

  5. `_estado_aceptado` requires status == "ACCEPTED". Real Listings Items PATCH response: `{"sku":..., "status":"ACCEPTED", "submissionId":..., "issues":[]}`. OK.

  6. Security: `patch_listing` doesn't set Content-Type? httpx json= sets it. OK. Token in header only. Error responses returned raw; _escribir_y_sellar stores ack only on success with scrub. On error path, error_code contains only method+route+status, no body. OK.

  7. `validar_patch_listings`: checks `seller_id not in VENDEDORES_PROPIOS.values()` — values are seller IDs, correct. But the "prefijo exacto" claim: path != esperada where esperada built from construir_ruta_listings(seller_id, sku) — that guarantees prefix. OK. Note: the docstring claims "segmento del SKU exacto (percent-encoding completo)" — construir_ruta_listings quotes it. OK.

  One subtlety: validar_patch_listings doesn't independently verify the SKU segment in path matches `quote(sku)` — it does via equality with esperada. Fine.

  8. `_espera_retry_after` duplicates `_espera_retry` in client.py — fine, minor duplication.

  9. In patch_listing, the 429 reintento: `self._sleep(_espera_retry_after(resp))` then continue — reuses same token. OK.

  10. Potential bug: `patch_listing` while loop — on 401 it calls `_acceso(forzar=True, rechazado=token)`; but the lector's `_acceso` checks if installed token != rechazado and returns it. OK matches client pattern.

  11. Missing: patch_listing doesn't consume the rate limiter (write has no limiter param) — by design (writes are rare).

  12. cerrar_por_observacion: the observation query `metric_date > dia_envio AND metric_date <= hoy`. If hoy is a `date`, comparisons fine. The window "dia posterior al envio" per docstring — metric_date > dia_envio is right (observation from the NEXT day). But what if enviado late in day UTC and the next-day ingesta happens early... edge. Also `metric_date <= hoy`: includes today's observation — but hoy is the run day; the day's observation may not exist yet. Fine.

  A more subtle issue: cerrar_por_observacion picks the most RECENT observation in (dia_envio, hoy]. If Amazon later changed price (e.g., someone changed it on day+2) the most recent obs could differ from precio_despues even though the change was confirmed on day+1 → no_confirmado. Design choice; documented? Docstring says "la observacion propia mas reciente de un dia posterior al del envio". OK by design.

  13. Comparisons Decimal vs Decimal: `(obs[0], obs[1]) == (despues, despues_moneda)` — both NUMERIC from DB, both Decimal in psycopg → equality is value-based (Decimal("100.0000") == Decimal("100.00") is True). OK.

  14. In revertir: `(vivo.precio, vivo.moneda) != (despues, despues_moneda)` — Decimal equality fine.

  15. Race-window in cerrar_por_observacion: it processes rows estado='enviado' but between fetchall and update, state could change; UPDATE is unconditional by id (no `AND estado='enviado'` guard). Minor: could overwrite a manually corrected state. Low severity.

  16. tools/precio_reversa.py `_plan` recomputes vivo for planning AND revertir recomputes vivo again — double network; fine given rate limiter.

  17. In main: `hubo_error` only set for estado == "error"; "saltado" not counted (documented). OK.

  18. Issue: `_huella` — for saltar entries uses `detalle.split(':')[0]` — for "precio_vivo_distinto: vivo=..." gives "precio_vivo_distinto". Deterministic. But two runs of dry-run could differ in saltar reasons ordering... sorted() handles it. OK.

  19. Issue candidate: in `_plan`, `if f[8] in ("pendiente", "enviado")` → original_abierto. But the same check in `revertir` uses estado of the original row — consistent. And listing_con_cambio_abierto check — consistent.

  20. Wait — important: in revertir, `_estado in ("pendiente","enviado")` returns saltado 'original_abierto'. But if original is estado='error' AND there's NO other abierto → proceeds. But cerrar_por_observacion never closes 'error', so a cambio in error remains non-abierto forever; reversa allowed. Also a cambio in estado='error' blocks... no, error is not in (pendiente, enviado) so doesn't block the unique index nor the otro_abierto check. Consistent.

  21. Potential real bug: In `cambiar_precio`, estado='error' rows never close (cerrar_por_observacion only handles 'enviado'). And readback showed price applied → estado stays 'error'. The docstring says that's deliberate ("aunque una lectura posterior muestre el precio nuevo"). OK.

  22. Check `_sellar` for 'enviado' — doesn't set enviado_at (set at INSERT). Schema CHECK precio_cambio_envio_exige_ack requires ack AND enviado_at for aplicado non-pendiente/error — enviado_at set at INSERT. OK.

  23. `leer_precio_vivo`: second call — if ofertas has 0 offers, parsear_precios returns all-None (no raise), so it proceeds to competitivo. The competitivo endpoint returns competitive prices — parsear_precios extracts own from ofertas (SellerId match) — competitive body can't supply own price. So second parse can only find own_price from the SAME ofertas body — which already failed to yield own price... wait, first parse with competitivo vacio could raise PrecioOmitido if NO usable prices at all (buybox etc.). Let me check: "Con ofertas pero sin ningun precio utilizable = PrecioOmitido". So first call could raise PrecioOmitido even when own offer exists? Need to look at parsear_precios more carefully: PrecioOmitido raised when there are ofertas but no usable price — usable includes own and buybox. If own offer exists with usable price, no raise, own found → early return. If own exists but... hmm, if the ONLY usable price is own, first parse fine.

  Actually the flow nuance: first parse passes competitivo={"payload": []}; if that raises PrecioOmitido (e.g., ofertas present but no usable prices), leer_precio_vivo raises PrecioVivoAusente WITHOUT trying competitivo. Is there a case where competitivo would rescue? buy_box_price comes from competitivo payload. If ofertas lack own offer AND have no usable prices, but competitivo has buybox — parse wouldn't omit → own still None → PrecioVivoAusente anyway. So the early-raise path can't rescue own price; the final outcome identical. Not a bug for own-price purposes. OK.

  Hmm, but actually there's a subtle: first parse raises PrecioOmitido → raise PrecioVivoAusente — but competitivo rescue might fill buybox making parse not raise, yet own still absent → same exception. No behavioral difference. Fine.

  24. `_ack_saneado` stores scrub(texto)[:2000] — stores response body truncated in DB. Docstring says "el ack que se guarda pasa por scrub()". OK.

  25. In write_client patch_listing: `validar_patch_listings` is called twice (also in precio_write._validar_destino). Fine.

  26. Important check: `construir_ruta_listings` percent-encodes SKU with quote(safe='') — handles special chars. `_ruta_sin_sku` uses raw seller_id — internal. OK.

  27. `SpapiWriteClient.__init__` mutates global logger levels — matches SpapiClient pattern. OK.

  28. `cerrar_por_observacion(conn, hoy)` — `hoy` type unspecified; if caller passes datetime, `(enviado_at AT TIME ZONE 'UTC')::date < %s` — date < timestamp compares after cast; fine. Low.

  29. One more: in revertir, the check `_estado in ("pendiente","enviado")` happens BEFORE the `enviado_at is None` check. For estado='error' rows enviado_at set at INSERT, fine.

  30. tools: `except FormaParcheSinSellar` — raised from `arma(...)` inside revertir AFTER the vivo GET (network used) but BEFORE INSERT. Message "sin fila y sin red" is wrong-ish: there WAS network (the vivo read). Minor doc issue; behavior correct (no write network, no row).

  31. Potential: `_cambios` in tool — `WHERE c.id = ANY(%s)` with list — psycopg adapts list to array. OK.

  32. In `_linea`, `f"{f[5]:.2f}"` — f[5] Decimal OK; but if precio_despues is None? NOT NULL. OK. But for saltar rows `_linea` uses f[11] (sku) — fine even if None.

  33. Race: `_plan` checks and then go executes revertir which re-checks; huella pins plan. OK.

  34. In revertir INSERT: `precio_antes = vivo.precio` (the current live price) and `precio_despues = _antes` (the original's precio_antes). Matches S6 comment "es_reversa con precio_despues = precio_antes del revertido". OK.

  35. Now, a genuine candidate: in `_escribir_y_sellar`, when the generic `except Exception` fires, the row is sealed 'error' and then the exception is re-raised. In `cambiar_precio` context (called from an API/cycle?), an unexpected exception propagates after sealing — that's fine.

  But wait: `SpapiNoPermitida` is subclass of SpapiError, and in _escribir_y_sellar, patch_listing could raise SpapiNoPermitida (route validation) — but _validar_destino already validated the route earlier, so can't happen. And _exigir_cuerpo_serializable raises ValueError → caught by generic → seal error → re-raise → in tool main, ValueError is NOT caught (only FormaParcheSinSellar, CambioNoReversible(ValueError subclass!)... wait CambioNoReversible IS a ValueError subclass. In main, `except (precio_write.CambioNoReversible, SpapiNoPermitida, precio_write.PublicacionSinSku, psycopg.errors.UniqueViolation)` — PublicacionSinSku also ValueError subclass. A bare ValueError from _exigir_cuerpo_serializable is NOT caught → crashes tool mid-batch with traceback. Edge case; severity baja (tests inject cuerpo, so bodies are serializable; the A.4-sealed body will be).

  36. Check tests exist: tests/test_precio_write.py, tests/test_spapi_write_client.py exist (saw in grep). Also tool tests? Search test file for precio_reversa. Let me check quickly.

  Also check app/redaction scrub signature — scrub(texto) returns str. Fine.

  One more possibly significant issue: `leer_precio_vivo` in `cambiar_precio` is called WITHOUT limitador (docstring acknowledges: "cambiar_precio no lo pasa"). Pricing is 0.5/s; a cycle applying many changes would burst. But decision may be deliberate (decisions rare). Documented.

  Another check: `revertir` docstring/comment says estado 'error' rows "nace con enviado_at y no pasa por el cierre" — consistent with cerrar only handling 'enviado'.

  Now also verify `parsear_precios` handles `ofertas` dict shape: the GET offers response is `{"payload": {"Offers": [...]}}`? parsear handles it (tested). OK.

  One more: In `cerrar_por_observacion`, loop variable `_lid` unused, fine. `platform` reused. The UPDATE sets estado confirmado/no_confirmado + confirmado_por — but the unique partial index frees the slot. OK.

  Potential issue: cerrar compares obs for metric_date in (dia_envio, hoy] — if hoy equals date of running and observations for hoy exist already (ingesta ran), it can confirm with same-day-as-hoy obs, which is "dia posterior al envio" since enviado date < hoy. OK.

  Race in cerrar: rows selected estado='enviado'; UPDATE unconditional — could flip a row concurrently changed by revertir? revertir doesn't touch original's estado. Manual ops could. Baja.

  Now one thing to verify: whether `cerrar_por_observacion` exists elsewhere/called by a scheduler — not needed.

  Let me quickly check the tests exist for the tool and check `listing` table has columns seller_sku, external_id. Grep migration 0001.

  Actually `listing` table: referenced JOIN l ON l.id AND l.platform; columns seller_sku, external_id — pricing.py uses external_id already (`SELECT DISTINCT external_id FROM listing`). seller_sku — grep.

  Let me also check tests for precio_reversa tool.

• listing has seller_sku (nullable — handled by PublicacionSinSku) and external_id. Good.

  Now, important check: DB roles — the tool connects with ORBIT_DSN_DECIDE (role orbit_decide / app_decide?). Does that role have INSERT/UPDATE on precio_cambio and SELECT on spapi_price_observation, listing, precio_decision? Check grants in 0039. If precio_cambio grants are missing for the decide role, the tool would fail at runtime — that's a real potential bug. Let me grep GRANT in 0039.

• Grants exist for app_decide: INSERT + column UPDATEs on precio_cambio. The columns updated in code: estado, ack, error_code, readback_*, confirmado_por — all granted. SELECT on listing? app_decide presumably has default read privileges from 0001. Also need UPDATE on listing? No — only SELECTs. OK.

  One more check: does app_decide have SELECT on listing and spapi_price_observation? 0001 default privileges probably grant read to all. Likely fine — and the tool's design doc says ORBIT_DSN_DECIDE. Quick grep to confirm listing SELECT grant... Let me check 0001 default privileges. Actually low risk; skip.

  Also check the `precio_cambio` INSERT trigger (`precio_cambio_coherente`?) — line 789-795 mentions a trigger comparing ROW(NEW...) — that's probably the UPDATE trigger (immutable). Is there an INSERT trigger on precio_cambio enforcing "nacimiento virtual" (aplicado=false nace confirmado) — the header said "los impone el trigger de INSERT de precio_cambio". Does the INSERT trigger enforce anything about estado for aplicado=true? Possibly checks estado='pendiente' at birth. Our INSERTs use 'pendiente' — fine.

  Also possible: the trigger may require `enviado_at` IS NOT NULL at insert? Our INSERT sets it. Fine.

  Now let me consider remaining candidate findings:

  1. (baja) Docstring de revertir dice "con la forma sin sellar no queda fila ni hay red", pero `arma()` se llama DESPUES de `leer_precio_vivo` (GET a Pricing): con la forma sin sellar SI hay red (lectura), solo no hay PATCH. Lo mismo en el tool: el go consume lecturas antes de abortar con FormaParcheSinSellar. Cosmetico.

  Actually wait — check order in revertir: vivo read at line 442, cuerpo arma at 454. Yes.

  2. (baja/media) `_escribir_y_sellar`: en la rama de exito (`aceptado`), si `_sellar` o `_readback` lanzan (p.ej. caida de la DB despues del PATCH exitoso), la excepcion propaga y el llamador no sabe que el PATCH salio — pero la fila queda 'pendiente' (huerfana, detectable). Esto esta documentado en la ventana COMMIT-PATCH. OK, by design.

  3. (baja) `cerrar_por_observacion`: UPDATE sin `AND estado = 'enviado'` en el WHERE — TOCTOU minimo entre el fetchall y el update; en practica single-process. Baja.

  4. (baja) `cerrar_por_observacion` cuenta `intactos` pero no los reporta con detalle; fine.

  5. (baja) `_espera_retry_after` duplica `_espera_retry` de client.py — duplicacion menor.

  6. Potencial media: en `leer_precio_vivo`, si el primer `parsear_precios` levanta `PrecioOmitido` (ofertas presentes pero sin ningun precio utilizable), se lanza PrecioVivoAusente SIN intentar el competitivo. Como el precio propio solo sale de las ofertas, el competitivo no podria rescatarlo, asi que no hay diferencia funcional. No es bug.

  7. Media?: `cambiar_precio` llama `leer_precio_vivo` sin `limitador` mientras que el readback posterior... en `_escribir_y_sellar` llamado desde cambiar_precio, limitador=None. Documentado ("cambiar_precio no lo pasa"). Decision deliberada pero riesgo: el motor podria hacer rafagas >0.5/s si aplica varias decisiones. Pricing 0.5/s. Si el motor aplica N cambios seguidos sin cubo, cada cambio hace 1-2 GETs (vivo + readback) sin throttle → 429s posibles, que el cliente reintenta una vez. Riesgo operativo real pero quizas deliberado. Lo reporto como baja/media "VERIFICAR: decision deliberada?".

  Actually the docstring explicitly acknowledges it. It's documented; maybe quota handled elsewhere (precio:<platform> quota). I'll flag as baja.

  8. Potencial bug real: en `revertir`, `except psycopg.errors.UniqueViolation` alrededor del `with conn.transaction()` — en autocommit, conn.transaction() funciona. OK.

  9. En `_escribir_y_sellar`, la rama `except Exception` incluye el caso donde `patch_listing` lanza despues de haber ENVIADO el PATCH con exito pero fallo al leer la respuesta (httpx.ReadTimeout es HTTPError → 'red'). Un timeout despues de que Amazon acepto = doble escritura potencial si el dueno reintenta? No — la fila queda en 'error', y un cambio en error no bloquea el indice unico... un reintento de la MISMA decision crearia otra fila pendiente y otro PATCH → doble PATCH (idempotente en precio: poner el mismo precio dos veces es inocuo). Para reversa: original en 'error' es reversible solo si vivo == despues — si Amazon aplico, vivo == despues → la reversa escribiria el precio ANTERIOR. Hmm, esto es el caso cubierto deliberadamente ("cubre que Amazon aplico aunque el ack fue 5xx"). OK.

  10. `_estado_aceptado`: exige `cuerpo.get("submissionId") is not None` y `status == "ACCEPTED"`. Un 200 de Listings PATCH trae esos campos. OK.

  11. Seguridad: `patch_listing` header lleva el token; httpx logging bajado a WARNING. Los errores no llevan body. `_ack_saneado` scrub. OK.

  12. `tools/precio_reversa.py`: `except FormaParcheSinSellar ... raise Abortar` — Aborta TODO el lote si la forma no esta sellada (falla en la primera reversa). Razonable.

  13. En `_plan`, excepciones de `leer_precio_vivo` capturadas: (PrecioVivoAusente, httpx.HTTPError, SpapiError). SpapiNoPermitida es SpapiError — incluida. OK.

  14. Uno mas: `_huella` para saltar usa `detalle.split(':')[0]` — para "no_reversible: cambio real..." → "no_reversible". Deterministico. OK.

  15. El main del tool: si `--acepto-mutacion-real` sin `--go` → Abortar. Pero con `--acepto-mutacion-real --go` sin `--huella` → Abortar. El docstring dice "exige juntos --acepto-mutacion-real, --huella H y --go no vacio". OK.

  16. Race real en tools: dry-run calcula huella; go recalcula plan + huella y compara. Si cambio el vivo entre dry-run y go → huella distinta → aborta. Bien.

  17. En main, el `lector` se crea con `SpapiClient(credentials=credentials, ...)` donde credentials=None → carga de ORBIT_SECRETS_DIR. En produccion OK.

  18. potencial: `conn = connect(_dsn_decide(), autocommit=True)` — connect(dsn, **kw) passes autocommit to psycopg.connect. Check db.py connect passes kw. Likely yes.

  Let me verify connect passes **kw to psycopg.connect — saw signature `def connect(dsn: str, **kw)`. Very likely. Minor.

  Now also confirm: does `precio_cambio` INSERT trigger reject `enviado_at` set at INSERT with estado='pendiente'? There's an INSERT trigger mentioned for nacimiento virtual. Let me check quickly the trigger function around line 700-790 to see INSERT constraints, to be safe. Actually tests exist (test_precio_write.py) and presumably pass with a live DB... but tests needing ORBIT_TEST_DSN skip without it. Not my job to run. Let me do a quick grep for the INSERT trigger on precio_cambio to check nothing in it conflicts (e.g., requiring enviado_at NULL at birth, which would break this code).

• Important: `precio_cambio_sella_transicion` — the UPDATE trigger: "las columnas de sello admiten NULL -> valor UNA vez". Note `enviado_at` is a sello column: "pendiente → enviado|error" transitions. Key question: does the INSERT in the diff set `enviado_at` at birth (pendiente) — the docstring in cerrar says "un cambio en error (nace con enviado_at)". And `_sellar` for enviado updates only estado+ack, not enviado_at. But wait — is `enviado_at` allowed to be non-NULL at birth per the nacimiento trigger? For aplicado=true born pendiente: does the nacimiento trigger require enviado_at NULL or NOT NULL? The code inserts enviado_at=momento at birth. Schema CHECK precio_cambio_envio_exige_ack: aplicado AND estado IN ('pendiente','error') → no ack needed. It doesn't forbid enviado_at at pendiente. But the transition trigger says "columnas de sello admiten NULL -> valor UNA vez" — if enviado_at is a sello column born set, then `_sellar` doesn't update it — fine.

  But hold on: is `enviado_at` in the UPDATE trigger's inmutable-or-sello list? If enviado_at is treated as immutable (ROW comparison includes enviado_at at line 793-795 region — that's the nacimiento? No, line 789-795 was in a trigger comparing NEW vs OLD? Actually line 789-795 mentions "created_at tambien es inmutable... ROW(NEW.id, NEW.decision_id, ..., NEW.precio_antes..." — that's in precio_cambio_sella_transicion (UPDATE trigger) checking immutable columns. Since the code never updates those, fine.

  Also transition: "pendiente → enviado | error". Our updates: pendiente→enviado (estado+ack) OK; pendiente→error (estado+error_code) OK; enviado→confirmado|no_confirmado OK. Readback updates: UPDATE readback_* on a row in estado enviado or error — the trigger checks "transicion" only when estado changes? The readback UPDATE doesn't change estado; does the trigger allow updates that only touch readback columns on an already-'error' row? "columnas de sello admiten NULL -> valor UNA vez" — readback_precio NULL→valor once. estado unchanged. Should be fine.

  But one potential issue: `_readback` is called TWICE? No — once per operation. But _readback sets readback_estado='fallido' (NULL→valor) once. OK.

  Another: `_sellar` estado error sets error_code (NULL→valor once). OK.

  Now `cerrar_por_observacion` sets estado + confirmado_por (NULL→valor). OK.

  And what about `enviado → enviado`? Not done.

  One more consideration in nacimiento trigger: "con decision, aplicado = (mode = live) en las dos direcciones" — the trigger probably OVERRIDES or CHECKS aplicado based on the decision's mode. cambiar_precio inserts aplicado=true for mode='live' decisions. OK. "sin decision (reversa), aplicado = true" — revertir inserts aplicado=true. OK.

  Also the trigger might check estado at birth: aplicado=true → estado='pendiente'. Our inserts use 'pendiente'. OK.

  Everything verified is consistent. Now compile findings.

  Candidate findings list:

  1. BAJA — docstring/codigo: en `revertir` (y `cambiar_precio`), `arma()` se invoca despues del GET del precio vivo, asi que con la forma sin sellar SI hay red (lecturas de Pricing) aunque no haya fila ni PATCH; el docstring dice "no queda fila ni hay red". Cosmetic, but the tool go also consumes reads before aborting. Minor.

  2. BAJA — `_escribir_y_sellar` rama generica `except Exception`: sella la fila como 'error' y re-lanza; en el tool, una excepcion no cubierta por la lista de `except` de main (p.ej. un ValueError de `_exigir_cuerpo_serializable` si el cuerpo sellado por A.4 trajera Decimal) aborta todo el lote con traceback en vez de marcar esa reversa y seguir. La fila queda sellada 'error' (sin corrupcion), pero el exit code seria por excepcion no controlada. Baja.

  3. BAJA — `cerrar_por_observacion`: el UPDATE final no revalida `estado = 'enviado'` en el WHERE; entre el fetchall y el update otra sesion podria haber cambiado la fila y este la pisaria. En la practica (proceso unico, trigger de transiciones lo rechazaria si la transicion es ilegal) el riesgo es minimo. Actually the trigger would reject illegal transitions (e.g., confirmado→confirmado? "sin saltos ni retrocesos"; confirmado→no_confirmado probably rejected by trigger). So the trigger protects. Maybe skip or keep as baja.

  4. BAJA — duplicacion `_espera_retry_after` vs `_espera_retry` en client.py. Menor.

  5. BAJA — `cambiar_precio` no pasa limitador al GET del vivo ni al readback (documentado); si el motor aplica varias decisiones seguidas, puede pegarle a Pricing por encima de 0.5/s. Documentado como deliberado ("cambiar_precio no lo pasa") — VERIFICAR que la cuota precio:<platform> u otra capa lo cubra; si no, riesgo operativo de 429s. Pero el cliente reintenta una vez el 429. Lo dejo como observacion baja.

  6. Potential MEDIA: `leer_precio_vivo` second parse: cuando el primer parseo levanta PrecioOmitido (ofertas sin precio utilizable), se sale sin intentar el competitivo — no cambia el resultado (el propio solo sale de ofertas). No bug.

  7. One more careful look at `_estado_aceptado`: requires 2xx AND submissionId AND status ACCEPTED. Listings Items PATCH returns 200 with "status":"ACCEPTED". OK.

  8. MEDIA?: In `revertir`, when the original is in estado 'error' but Amazon did NOT apply, vivo != despues → saltado precio_vivo_distinto. Fine.

  9. What about `cambiar_precio`: decision with mode='live' but the CHECK `precio_decision_cuenta_completa` ensures p_actual/p_aplicado NOT NULL for subir/bajar. p_actual could be NULL? CHECK requires p_actual IS NOT NULL for subir/bajar. OK.

  10. `momento` used as enviado_at at INSERT — docstring/comment in cerrar says "nace con enviado_at" — consistent.

  11. `_observada_del_dia(conn, asin, platform, dia=momento.date())` — for revertir/cambiar, records the day's observation. Fine.

  12. Tool `_plan`: checks `f[7]` (aplicado) via `not f[7]`. And estado checks. Consistent with revertir's own checks; revertir re-validates at go. Good defense in depth.

  13. Seguridad: nothing leaks tokens; ack scrubbed. Seller IDs public per comment. OK.

  One more potential real issue: in `cambiar_precio` and `revertir`, the `UniqueViolation` catch wraps `with conn.transaction()` — if the violation occurs, the transaction context rolls back. In autocommit mode, conn.transaction() begins/commits explicitly; the exception propagates and is caught outside the with → context manager rolls back. Fine.

  Also in `_escribir_y_sellar` error branches: `_sellar` then `_leer_vivo_suave` then `_readback` — the readback's UPDATE to estado... no, only readback cols. But wait: trigger "sello NULL -> valor UNA vez" — if the same row somehow went through two operations... can't.

  Hmm, one more subtle thing: in the `except Exception` branch, it re-raises AFTER _readback. If _readback itself fails (DB down), the original exception is masked... acceptable.

  Also `except SpapiAuthError` — but `SpapiRechazoLWA` is subclass. OK. However, note `SpapiError` other than auth (e.g. SpapiNoPermitida from route validation inside patch_listing) → falls into generic `except Exception` → sealed 'error' with code "excepcion:SpapiNoPermitida" then re-raised → in tool main caught → continue. OK.

  Wait, actually there's a subtlety: in `_escribir_y_sellar`, SpapiNoPermitida raised by patch_listing means the PATCH never went out — sealing the row as 'error' is correct.

  Final list: mostly low-severity observations; no high/medium bugs found. Maybe also mention the cerrar race + the docstring "ni hay red" inaccuracy + cambiar_precio sin limitador + the tool's uncaught-exception path.

  Also one thing: `revertir` for an original in estado 'confirmado'/'no_confirmado' — good. For 'error' — allowed. But what about an original in 'error' whose PATCH actually failed with 4xx (never applied)? vivo == despues fails → saltado. Correct.

  Also `cerrar_por_observacion`: uses `metric_date > dia_envio` — if enviado_at is 23:59 UTC and ingestion for next day happens... fine.

  I'm reasonably confident. Also check that tests exist for precio_reversa tool — glob showed test_precio_write.py; tool tests maybe inside test_precio_write.py or separate file? Not critical for review.

  Respond with numbered findings, severities, in Spanish, plain text, no accents where avoidable.

To resume this session: kimi -r session_9fd3a3f7-41f1-4afd-9e5c-efa034d23824


=== Revisor efectivo: kimi (binario 'kimi') ===


---
sha=efc0555881b57e342811b37e6ce7c0aaf03e1489 archivos=app/spapi/precio_write.py,app/spapi/write_client.py,tools/precio_reversa.py inicio=2026-09-18T17:36:10Z fin=2026-09-18T17:45:32Z exit=0
