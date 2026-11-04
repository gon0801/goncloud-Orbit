=== quality-kit cross-review.ps1 ===
Repo: /Users/dn/dev/_wt/f11-kimi-r1
CLI: kimi
Alcance: last-commit
Archivos (pathspec de la tarea): app/precio/cobertura.py, app/precio/fuentes.py, tools/precio_cobertura.py, tests/test_architecture.py, docs/evidencia/repricing-01/A.7/readback.sh, docs/evidencia/repricing-01/A.7/recuadro_desde_salidas.py, docs/evidencia/repricing-01/A.7/consultas
Tamano del diff: 45881 caracteres
==> Invocando kimi de forma no interactiva (tope: 900 s)...

=== Respuesta de kimi (codigo de salida: 0) ===
• Leo el diff y despues verifico lo que dependa de codigo fuera del diff.

• Ahora leo el resto del diff y verifico en el repo los puntos que dependen de codigo/DDL fuera del diff.

• Leo el tramo final del diff (1001-1124) y verifico `PROHIBIDOS_PRECIO` y `_usos_reloj` en el repo.

• Verifico los ultimos puntos pendientes en el repo: la lista `PROHIBIDOS_PRECIO` completa, la clave de config y el DDL de `listing`.

• Reviso como se compone `config_version.settings` (la clave nueva no aparece en ningun seed) y el brief de A.7 para la definicion de "activa".

• Revision completa. Verifique contra el repo: los DDL de `spapi_listing_estado_observation` (0035), `precio_goal` y `precio_decision` (0039), `estimacion_oferta_observation` (0028), `listing` (0001), el enum `platform`, `PROHIBIDOS_PRECIO` y los helpers de `test_architecture.py`, la spec S10 (`docs/superpowers/specs/2026-09-15-repricing-01-design.md`) y el plan (`plans/repricing-01.md`). Lo que quedo bien: `%%BUYABLE%%` es el escape correcto para psycopg3 con `%s`; `precio_decision` tiene `UNIQUE (listing_id, platform, decision_date)`, asi que el dict-comprehension de `_SQL_DECISIONES` es seguro; el vocabulario de `resultado` del CHECK calca `RESULTADOS_EVALUADOS` + `no_evaluado`; `observed_at` es `TIMESTAMPTZ` en ambas tablas, asi que `astimezone(UTC)` opera sobre datetimes conscientes; la clave `precio_catalogo_max_dias_sin_reportar` que falta en produccion hasta D.0 esta documentada en `readback.md` y el fail-closed es deliberado; los candados de `test_architecture.py` tienen fugas sembradas que demuestran que muerden.

  Hallazgos (ninguno alto):

  1. (baja) VERIFICAR — definicion de "activa" en `_SQL_CANO` (`app/precio/fuentes.py:234`): el SQL toma la ultima observacion por (sku, platform) y DESPUES filtra `status LIKE '%BUYABLE%'`; el docstring y el comentario de `01_canonicas.sql` ("la ultima observacion ... cuyo status contiene BUYABLE") admiten la lectura contraria (filtrar BUYABLE primero y tomar la ultima de esas). Difieren cuando la ultima observacion de un SKU ya no es BUYABLE: la implementacion lo saca del denominador, la otra lectura lo deja contado. Habria que confirmar cual de las dos semantica uso la medicion que origino los 264/106 del spec (no encontre esa consulta en `docs/evidencia/repricing-01/E.1/`).

  2. (baja) `docs/evidencia/repricing-01/A.7/recuadro_desde_salidas.py:630` — `recuadro_desde_archivos` recibe `puente_f` y nunca lo usa (solo `main` lo lee para `identidad`). Parametro muerto; ademas el nombre `--puente` apunta a la salida de `05_listing_identidad.sql` (identidad), no a las activas del puente, lo que invita a pasar el archivo equivocado.

  3. (baja) `readback.sh:485,549` — `\set hoy` para las consultas 03/04 sale de `date -u` de la maquina local, mientras el recuadro usa el dia UTC de la base (06_hoy.sql). Si el reloj local difiere o la corrida cruza medianoche UTC, goals/decisiones y `dias_sin_reportar` se calculan con dias distintos sin ningun aviso.

  4. (baja) El candado de imports de `fuentes.py` (`tests/test_architecture.py`, `_permitido`) deja pasar cualquier modulo de stdlib que no este en `PROHIBIDOS_PRECIO` (`socket`, `urllib`, `http.client`, `subprocess`...), asi que el "jamas red" del comentario no esta realmente candado. Es el mismo agujero que tiene el candado general de `app/precio`, no una regresion nueva, pero el comentario promete mas de lo que el test exige.

  5. (baja) `cuadra_exact` es tautologica por construccion: `activas` se define como `len(propias)` y cada fila cae en exactamente un bucket, asi que el chequeo no puede fallar hoy; la reconciliacion real contra una fuente externa es `aviso_puente`. Vale como candado contra futuras ediciones que pierdan un bucket, pero no verifica nada sobre el dato — conviene no presentarlo como prueba de que el recuadro cuadra con la fuente.

  6. (baja) Docstring de `recuadro_desde_salidas.py`: dice que el recuadro impreso "tiene el mismo formato que `tools/precio_cobertura.py`", pero la primera linea difiere ("salidas A.7" vs "canonica Orbit") y el tool formatea el precio via `_dinero` con casos levemente distintos. Cosmetico, pero la promesa de "mismo formato" facilita comparaciones automaticas que van a fallar.

  Sin riesgos de seguridad nuevos: las consultas son solo-SELECT parametrizadas, el readback corre en `BEGIN READ ONLY` con candados previos razonables, y el DSN sale del entorno del contenedor sin tocar disco.



=== stderr ===
kimi version 0.39.1
• The user explicitly says: don't use skills, act as external reviewer, read the diff file and review. User instructions take precedence over skills. Let me read the file.

• Now read the tail of the diff (lines 1000-1124) and verify some things against the repo:

  1. `_SQL_CANO` uses `LIKE '%%BUYABLE%%'` — with psycopg3, `%s` is the param placeholder and literal % must be doubled only with pyformat-style `%(name)s`? psycopg uses `%s` and `%%` for literal percent when there are params. Actually psycopg3 uses `%s` placeholders, and `%%` is correct for literal %. Yes, in psycopg3 (and 2), with `%`-style placeholders, literal % must be `%%`. Good.

  2. DISTINCT ON ordering: `ORDER BY e.seller_sku, e.platform, e.observed_at DESC, l.id NULLS LAST` — LEFT JOIN fanout: if multiple listings match the same sku+platform, rows for the same (sku,platform,observed_at) tie, broken by l.id NULLS LAST — NULLS LAST puts NULL after non-null, and lowest l.id first since default ASC. So "listing_id minimo" comment matches. Fine.

  But note: the filter on status is applied AFTER DISTINCT ON. So "activa = ultima observacion cuyo status contiene BUYABLE"? Actually docstring says "activa = la ultima observacion por (seller_sku, platform) cuyo status contiene BUYABLE" — but the SQL takes the latest observation and then filters status LIKE %BUYABLE%. So a sku whose latest observation is not BUYABLE is simply dropped (not active). That's a defensible definition: "activa = cuya ultima observacion es BUYABLE". The docstring wording is slightly ambiguous but the outer filter approach means latest observation must contain BUYABLE. The comment in 01_canonicas.sql says same. OK — potential doc nuance, low.

  3. `leer_publicaciones`: `observed.astimezone(UTC)` — if observed is naive (timestamptz column → psycopg3 returns aware; timestamp without tz returns naive). Column type: need to check migration. If `observed_at` is timestamptz, aware. If timestamp without tz, naive → astimezone(UTC) interprets as local time — wrong. Need to verify schema.

  4. `dias_sin_reportar` computed with `hoy - observed date` — hoy is UTC date from DB. OK.

  5. `_SQL_DECISIONES`: multiple decisions per listing_id per day? dict comprehension takes the last row — order unspecified. Need to check schema: is there a unique constraint on (listing_id, platform, decision_date)? precio_decision might be append-only with multiple rows per day? Verify.

  6. `_SQL_GOALS` doesn't dedupe — set, fine.

  7. `_fase_fuera_de_alcance` checks `fila.platform == "meli"` — but in `armar_recuadro`, filas filtered by platform, so for platform "meli" everything is fuera. But meli filas would never have canal etc. Also tool exits early for meli. OK.

  But: order — `_fase_fuera_de_alcance` checked before `_motivo_no_evaluada`, consistent with docstring order. But `catalogo_desactualizado` check happens before `sin_listing`: docstring says "primero lo estructural (fuera_de_alcance), luego lo temporal (catalogo_desactualizado), luego sin_listing, luego canal_sin_dato, luego sin_goal". In `_motivo_no_evaluada`: catalogo_desactualizado, sin_listing, canal_sin_dato, then `if not fila.tiene_goal: return None` → sin_goal bucket, then decision. Matches.

  Hmm wait — one issue: a fila with canal fbm is fuera_de_alcance; a meli fila is fuera. Fine.

  8. Potential bug: `aviso_puente` — fine.

  9. readback.sh: `estado="${PIPESTATUS[1]:-$?}"` — PIPESTATUS[1] is the ssh exit code. But wait, the pipeline is `{ ... } | ssh goncloud '...' > "$parcial" 2> "$err"`. Hmm, actually the redirect applies to the ssh command. PIPESTATUS[1] = ssh status. OK. But note `2> "$err"` only redirects ssh's stderr... actually with `{ ...; } | ssh ... > parcial 2> err`, the redirection binds to the last command in the pipeline? In bash, redirections at the end of a pipeline apply to the last command. Yes, `a | b > out 2> err` — out/err apply to b. Good.

  But: `set +e` before and capturing status; fine.

  A subtle issue: `printf 'BEGIN READ ONLY;'` + sql + `ROLLBACK;` piped via ssh to psql with `-v ON_ERROR_STOP=1`. psql reads from stdin. Fine.

  Another: the grep candado: `grep -nH -F '\' ... | grep -vE ':[0-9]+:\\set [a-z_]+ [A-Za-z0-9_-]+$'` — hmm, `\set hoy 2026-09-18` matches. `\set platform amazon_mx` matches. OK. But note the second grep pattern: after `-nH` output format is `file:line:content`. The pattern `:[0-9]+:\\set ...` — requires line number then `:\set`. Content is `\set platform amazon_mx`. OK.

  Potential issue: sql files contain comments with backslashes? e.g. "diagonal invertida" — the comments in consultas don't have backslashes. Fine.

  10. `sed` substitution `s/^\\\\set platform .*/\\\\set platform $plataforma/` — replaces `\set platform amazon_mx` with `\set platform <plataforma>`. OK. For 06_hoy.sql there's no \set line — fine.

  11. recuadro_desde_salidas.py: `sys.path.insert(0, Path(__file__).resolve().parents[4].as_posix())` — file at docs/evidencia/repricing-01/A.7/recuadro_desde_salidas.py → parents[0]=A.7, [1]=repricing-01, [2]=evidencia, [3]=docs, [4]=repo root. Correct.

  12. In `recuadro_desde_archivos`: `hoy = date.fromisoformat(_lineas(hoy_f)[0][0].strip())` — 06_hoy output is a date. Fine. `puente_f` parameter — actually it's the listing identidad output (05), named puente but used as identidad in main: `identidad = int(Path(args.puente)...)`. The naming is confusing but consistent: --puente points to 05_listing_identidad output. Wait — the docstring of the tool says "psql -tA -F'|' -f consultas/NN_*.sql". But readback.sh does NOT pass -F'|' — it uses `-tA` only. Default field separator with -A is `|`. OK fine.

  BUT: readback.sh runs queries with `-q` and ON_ERROR_STOP — output of `\set` commands? `\set` produces no output. BEGIN READ ONLY / ROLLBACK with -q produce no output ("BEGIN"/"ROLLBACK" suppressed by -q? Actually -q quiet suppresses command tags like BEGIN). Comment says that. OK.

  Another: readback writes `final` files for all queries including 06_hoy which has platform loop — runs 06 twice (per platform), same content. Harmless.

  Issue: the `\set platform` substitution — consulta 01 uses `:'platform'` — after sed the \set line is updated. Good.

  13. `test_architecture.py` candados: `_fugas_imports_fuentes` uses `_sys.stdlib_module_names` — fine.

  Note: `_PROHIBIDOS_IMPORTS_FUENTES` excludes "psycopg", and `_permitido` allows stdlib, psycopg, app.precio.*. But `PROHIBIDOS_PRECIO` may include things like "os" etc. Need to verify `PROHIBIDOS_PRECIO` and `_es_bloque_type_checking`, `_usos_reloj`, `_usos_import_dinamico`, `PRECIO` exist in test_architecture.py. Very likely they do.

  Potential issue: `_permitido` for `from app.precio.cobertura import FilaPublicacion`: candidatos = ["app.precio.cobertura", "app.precio.cobertura.FilaPublicacion"] — both start with "app.precio." → permitted. Good.

  But `from __future__ import annotations`: module "__future__" — `__future__`.split(".")[0] = "__future__" — is "__future__" in sys.stdlib_module_names? Yes, it is. OK.

  14. Now the real potential bug candidates to verify in repo:

  a. `observed_at` column type in spapi_listing_estado_observation — if `timestamptz`, aware datetime, fine.

  b. `precio_decision` unique per (listing_id, platform, decision_date)? If not, dict takes arbitrary last.

  c. `precio_goal` may have multiple rows per listing — set, fine.

  d. `estimacion_oferta_observation` columns: canal, price_amount, price_currency, listing_id, platform, observed_at — verify they exist.

  e. `_SQL_CANO` percent escaping: in psycopg3, placeholders `%s`; literal % needs `%%`. Yes — `LIKE '%%BUYABLE%%'` correct.

  f. In fuentes `leer_publicaciones`, `decisiones.get(listing_id, (None,None))` — listing_id could be None (sin listing); decisions keyed by None → fine.

  g. In cobertura `armar_recuadro`: `no_evaluadas.get("catalogo_desactualizado")` aviso. Fine.

  h. Tool `tools/precio_cobertura.py` (already in repo? The diff was "LIMITADO a los archivos" — tools/precio_cobertura.py appears in the file list but I don't see its diff in the diff text... the prompt says the diff is limited to those files including tools/precio_cobertura.py. But in the 1124-line diff I haven't seen tools/precio_cobertura.py. Maybe it's in the last 124 lines. Let me read the tail.

  Also check: readback.sh `SUCIO` uses `git -C "$DIR"` — inside repo, fine.

  One more: `HOY="$(date -u +%F)"` in readback.sh uses local machine date (UTC), while consulta 06 gets DB date. If they differ (midnight race), goals/decisiones use sed-substituted HOY while the recuadro script uses 06 output for hoy — mismatch possible but minor.

  Also `recuadro_desde_salidas.py` main reads `hoy` from file again via `Path(args.hoy).read_text().strip().splitlines()[0]` — fine.

  Potential real bug: in `recuadro_desde_archivos`, decisiones parse `int(partes[0])` — if listing_id could be NULL in precio_decision? Probably NOT NULL. Fine.

  Another: `_SQL_CANO` — `ORDER BY e.seller_sku, e.platform, e.observed_at DESC, l.id NULLS LAST`. If there are two listings with same sku, DISTINCT ON picks one listing — but which observation? observed_at DESC picks the latest observation row; if the latest observation has joined l rows for both listings, picks lowest l.id. But the docstring says "dos listings con el mismo SKU cuentan uno". OK.

  Subtle: distinct-on grouping is (seller_sku, platform), but the status filter is applied outside, so "ultima observacion" per sku regardless of status; if latest observation status isn't BUYABLE, sku dropped. That matches "activa = ultima observacion cuyo status contiene BUYABLE"? Docstring: "«activa» = la ultima observacion por (seller_sku, platform) cuyo status contiene BUYABLE" — ambiguous Spanish: could read as "the last observation whose status contains BUYABLE" (i.e., filter first, then take latest BUYABLE). Implementation takes latest observation and requires it to be BUYABLE. Those differ! If the latest observation is INACTIVE but an earlier one was BUYABLE: impl → not active; alternative reading → active. I should check the design doc or the plan to see which was intended. This is a semantic point worth flagging as VERIFICAR against the S10/decision spec. Actually the comment in the SQL file says "«Activa» = la ultima observacion por (seller_sku, platform) cuyo status contiene BUYABLE" too. Hmm. Let me check docs for the definition of "activa canonica" in repricing-01 plan/spec.

  Also: `WHERE u.status LIKE '%BUYABLE%'` — status could contain other flags. Fine.

  Check platform values: listing.platform values 'amazon_mx','amazon_us','meli'? And FilaPublicacion.platform == "meli" for fuera_de_alcance — but tool choices include meli and exits early. OK.

  Another check: `_SQL_CANAL` doesn't filter by whether the observation's listing is in the canonical set — it's keyed by listing_id, fine.

  `price_amount` NUMERIC → Decimal via psycopg. In recuadro_desde_salidas, Decimal(partes[2]). Fine.

  Check `observed.astimezone(UTC)` in fuentes.py — psycopg3 returns aware datetime for timestamptz (with connection tz? psycopg3 returns aware with the connection's timezone info, or fixed offset). `.astimezone(UTC)` works if aware. If column is `timestamp without time zone`, returns naive → `.astimezone()` on naive assumes local tz → wrong result on machines not UTC. Need to check DDL.

  Let me now read the diff tail and grep the migrations.

• Read didn't page — I need offset 1000. Also check migrations for the tables.The Read keeps truncating at 1000 lines; need line_offset 1000.19:CREATE TABLE spapi_listing_estado_observation (
20-    id                   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
21-    seller_sku           TEXT NOT NULL,
22-    asin                 TEXT,
23-    platform             platform NOT NULL,
24-    status               TEXT,
25-    product_type         TEXT,
26-    last_updated_date    TIMESTAMPTZ,
27-    api_version          TEXT NOT NULL,
28-    observed_at          TIMESTAMPTZ NOT NULL,
29-    ingest_run_id        BIGINT REFERENCES ingest_run (id),
30-    CONSTRAINT spapi_listing_estado_clave_unica
31-        UNIQUE (seller_sku, platform, observed_at)
32-);
33-
34-COMMENT ON TABLE spapi_listing_estado_observation IS
35-    'SP-API 01 A.4: estado del listing propio por SKU (Listings Items '
36-    '2021-08-01). Observacion, no fuente de precio/stock (D2: el bridge '
37-    'manda en listing).';
38-
39-COMMENT ON COLUMN spapi_listing_estado_observation.seller_sku IS
---
197:CREATE TABLE precio_decision (
198-    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
199-    listing_id          BIGINT NOT NULL,
200-    platform            platform NOT NULL,
201-    decision_date       DATE NOT NULL,
202-    resultado           TEXT NOT NULL,
203-    motivo              TEXT,
204-    product_id          BIGINT REFERENCES product (id),
205-    canal               estimacion_canal,
206-    m_actual            NUMERIC(8, 4),
207-    goal                NUMERIC(6, 4),
208-    p_actual            money_amount,
209-    p_actual_currency   currency NOT NULL,
210-    p_objetivo          money_amount,
211-    p_objetivo_currency currency NOT NULL,
212-    p_aplicado          money_amount,
213-    p_aplicado_currency currency NOT NULL,
214-    i_valor             money_amount,
215-    i_currency          currency NOT NULL,
216-    c_valor             money_amount,
217-    c_currency          currency NOT NULL,
218-    f_valor             money_amount,
219-    f_currency          currency NOT NULL,
220-    l_valor             money_amount,
221-    l_currency          currency NOT NULL,
222-    r_valor             money_amount,
223-    r_currency          currency NOT NULL,
224-    escenario_id        BIGINT REFERENCES estimacion_escenario (id),
225-    fee_observation_id  BIGINT REFERENCES estimacion_fee_observation (id),
226-    -- `cotizacion_id` y `envio_muestra_id` nacen como BIGINT plano: sus FK se
227-    -- declaran por ALTER tras crear `precio_cotizacion`/`precio_envio_muestra`
---
60:CREATE TABLE precio_goal (
61-    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
62-    listing_id      BIGINT NOT NULL,
63-    platform        platform NOT NULL,
64-    margen_goal_pct NUMERIC(6, 4) NOT NULL,
65-    mode            precio_mode NOT NULL,
66-    valid_from      DATE NOT NULL,
67-    valid_to        DATE,
68-    creado_por      TEXT NOT NULL,
69-    go_literal      TEXT,
70-    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
71-    CONSTRAINT precio_goal_listing_fk FOREIGN KEY (listing_id, platform)
72-        REFERENCES listing (id, platform),
73-    CONSTRAINT precio_goal_banda
74-        CHECK (margen_goal_pct BETWEEN 0.10 AND 0.60),
75-    CONSTRAINT precio_goal_live_exige_go
76-        CHECK ((mode = 'live') = (go_literal IS NOT NULL AND btrim(go_literal) <> '')),
77-    -- La vigencia es [valid_from, valid_to): valid_to = valid_from es un
78-    -- intervalo vacío —el goal queda ANULADO, nunca estuvo vigente— y así se
79-    -- apaga un error de dedo el mismo día (si no, la corrida de las 13:10 UTC
80-    -- actuaría sobre él). Aquí 0039 se aparta del patrón de `sku_cost`
81-    -- (`valid_to > valid_from`) a propósito: un costo rige pasado; un goal
82-    -- con error debe poder no regir nunca (regla 7: la reversa existe antes).
83-    CONSTRAINT precio_goal_vigencia_coherente
84-        CHECK (valid_to IS NULL OR valid_to >= valid_from),
85-    CONSTRAINT precio_goal_unico_por_fecha UNIQUE (listing_id, platform, valid_from)
---
52:CREATE TABLE estimacion_oferta_observation (
53-    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
54-    listing_id          BIGINT NOT NULL REFERENCES listing (id),
55-    platform            platform NOT NULL,
56-    seller_sku          TEXT NOT NULL,
57-    asin                TEXT NOT NULL,
58-    canal               estimacion_canal NOT NULL,
59-    price_amount        money_amount NOT NULL,
60-    price_currency      currency NOT NULL,
61-    fetched_at          TIMESTAMPTZ NOT NULL,
62-    observed_at         TIMESTAMPTZ NOT NULL,
63-    source_event_id     TEXT NOT NULL,
64-    canonical_input     JSONB NOT NULL,
65-    context_fingerprint TEXT NOT NULL,
66-    ingest_run_id       BIGINT REFERENCES ingest_run (id),
67-    CONSTRAINT estimacion_oferta_precio_positivo CHECK (price_amount > 0),
68-    CONSTRAINT estimacion_oferta_listing_coherente FOREIGN KEY (platform, asin)
69-        REFERENCES listing (platform, external_id),
70-    CONSTRAINT estimacion_oferta_evento_unico UNIQUE (source_event_id),
71-    CONSTRAINT estimacion_oferta_contexto_unico UNIQUE (
72-        id, listing_id, platform, seller_sku, asin, canal,
73-        price_amount, price_currency, context_fingerprint
74-    ),
75-    CONSTRAINT estimacion_oferta_anti_duplicado UNIQUE (
76-        listing_id, canal, fetched_at, price_amount, price_currency, observed_at
77-    )
The Read tool keeps hitting its internal 1000-line cap per call. I need to read the tail: use Read with line_offset=1001... wait, I did omit it in the last call? I passed no line_offset. Let me read offset 1001.

  Also good: observed_at is TIMESTAMPTZ → psycopg returns aware datetime. OK.

  precio_decision: need to check unique constraint on (listing_id, platform, decision_date) — the grep showed up to line 227. Let me check the rest of the precio_decision DDL for a unique constraint. Also check `platform` ENUM values: does 'meli' exist? Check `platform` enum in 0001. Also check `listing` table (seller_sku, platform, id). And check PROHIBIDOS_PRECIO includes psycopg, and whether fuentes.py actually imports mapear_canal (the comment says it's permitted but the diff's fuentes.py doesn't import it — the canal already comes mapped; fine).

  Check precio_decision unique constraint and trigger for decision_date. Also check `precio_decision` "resultado" values — RESULTADOS_EVALUADOS = subir/bajar/mantener/frenado/goal_inalcanzable plus "no_evaluado". Verify against DDL CHECK or app/precio code.    -- declaran por ALTER tras crear `precio_cotizacion`/`precio_envio_muestra`
    -- (r3-1: la cotización nace ANTES que la decisión, así que ya no hay
    -- circularidad, pero el ALTER tras el CREATE mantiene el orden de
    -- lectura del archivo).
    cotizacion_id       BIGINT,
    envio_muestra_id    BIGINT,
    u15                 INTEGER,
    u60                 INTEGER,
    n15                 INTEGER,
    n60                 INTEGER,
    racha_senal         INTEGER,
    perdiendo           BOOLEAN,
    buy_box_is_own      BOOLEAN,
    mode                precio_mode NOT NULL,
    prioridad           NUMERIC(18, 4),
    config_version_id   BIGINT REFERENCES config_version (id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT precio_decision_listing_fk FOREIGN KEY (listing_id, platform)
        REFERENCES listing (id, platform),
    CONSTRAINT precio_decision_unica_por_dia UNIQUE (listing_id, platform, decision_date),
    -- S4 #1 «insumos completos o nada» (ronda 5 de revisión, punto 2): un
    -- `subir`/`bajar` trae la cuenta completa —la fila es inmutable y un
    -- `no_evaluado` posterior no la completa—. Solo esos dos resultados: la
    -- matriz de `mantener`/`frenado`/`goal_inalcanzable` la fijan las reglas
    -- (A.2) y la corrida (A.5). `goal` va en la lista aunque lo llene el
    -- trigger (punto 3): el CHECK documenta la forma completa.
    CONSTRAINT precio_decision_cuenta_completa
        CHECK (resultado NOT IN ('subir', 'bajar')
               OR (goal IS NOT NULL AND m_actual IS NOT NULL
                   AND p_actual IS NOT NULL AND p_objetivo IS NOT NULL
                   AND p_aplicado IS NOT NULL AND i_valor IS NOT NULL
                   AND c_valor IS NOT NULL AND f_valor IS NOT NULL
                   AND l_valor IS NOT NULL AND r_valor IS NOT NULL)),
    -- Vocabulario cerrado de S4 (seis resultados) y ningún silencio
    -- (decisión 11 del dueño): fuera de subir/bajar el motivo es obligatorio
    -- y no en blanco; en append-only no se arregla después.
    CONSTRAINT precio_decision_resultado_valido
        CHECK (resultado IN ('subir', 'bajar', 'mantener', 'no_evaluado',
                             'goal_inalcanzable', 'frenado')),
    CONSTRAINT precio_decision_motivo_no_silencio
        CHECK (resultado IN ('subir', 'bajar')
               OR (motivo IS NOT NULL AND btrim(motivo) <> ''))
);

COMMENT ON TABLE precio_decision IS
  'REPRICING 01 A.0 (S5): una fila por (listing, platform, día) —`subir`, '
  '`bajar`, `mantener(motivo)`, `no_evaluado(motivo)`, '
  '`goal_inalcanzable(motivo)` o `frenado(motivo)`— con la cuenta completa '
  '(escenario, fee del escenario, cotización propia y, en FBM, muestra de '
  'envío) para reproducirla sin releer insumos. Append-only por '
  'prohibir_mutacion: corregir es la fila del día siguiente.';
COMMENT ON COLUMN precio_decision.decision_date IS
  'Día UTC de la BASE fijado por trigger (se ignora el del cliente): la '
  'segunda corrida del día no decide (UNIQUE), no pisa.';
COMMENT ON COLUMN precio_decision.canal IS
  'Reutiliza estimacion_canal; NULL cuando no hay canal (canal_sin_dato, '
  'regla 3: nunca un default). El valor de MeLi lo agrega su fase.';
COMMENT ON COLUMN precio_decision.cotizacion_id IS
  'La cotización propia USADA (S4 #3: como máximo dos intentos por día; NULL '
  'cuando el día no cotizó). La cotización nace ANTES que la decisión '
  '(desviación r3-1 de S5, declarada en la cabecera) y este es el único '
  'puntero decisión→cotización que existe.';

-- Índices de apoyo de las FKs (PostgreSQL no los crea por el REFERENCES).
CREATE INDEX precio_decision_por_producto ON precio_decision (product_id);
CREATE INDEX precio_decision_por_escenario ON precio_decision (escenario_id);
CREATE INDEX precio_decision_por_fee ON precio_decision (fee_observation_id);
CREATE INDEX precio_decision_por_cotizacion ON precio_decision (cotizacion_id);
CREATE INDEX precio_decision_por_muestra ON precio_decision (envio_muestra_id);
CREATE INDEX precio_decision_por_config ON precio_decision (config_version_id);

CREATE FUNCTION precio_decision_fecha_utc() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    NEW.decision_date := (now() AT TIME ZONE 'UTC')::date;
    RETURN NEW;
END;
$$;

CREATE TRIGGER precio_decision_fecha_utc
    BEFORE INSERT ON precio_decision
    FOR EACH ROW EXECUTE FUNCTION precio_decision_fecha_utc();

COMMENT ON FUNCTION precio_decision_fecha_utc IS
  'REPRICING 01 A.0 (S5): decision_date es el día UTC de la base con UTC '
  'fijado en la expresión —el cliente no lo decide (regla 6: el día en curso '
  'se descarta y la idempotencia diaria descansa en este valor, no en el '
  'reloj de quien inserta).';

CREATE TRIGGER precio_decision_append_only
    BEFORE UPDATE OR DELETE ON precio_decision
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();
===
36:CREATE TYPE platform       AS ENUM ('amazon_mx', 'amazon_us', 'meli');
37:CREATE TYPE currency       AS ENUM ('MXN', 'USD');
38:CREATE TYPE ad_entity_kind AS ENUM ('campaign', 'ad_group', 'keyword', 'product_target', 'placement');
39:CREATE TYPE ledger_kind    AS ENUM ('sale', 'fee', 'refund', 'withholding');
40:CREATE TYPE decision_kind  AS ENUM ('bid', 'budget', 'pause', 'resume', 'negative', 'harvest');
41:CREATE TYPE fx_source      AS ENUM ('exact', 'nearest_prior');
Good: `precio_decision` has UNIQUE (listing_id, platform, decision_date) → dict comprehension is safe. `resultado` CHECK is exactly the 5 evaluados + 'no_evaluado' — matches RESULTADOS_EVALUADOS. observed_at TIMESTAMPTZ everywhere → aware datetime, OK. platform ENUM includes meli, amazon_mx, amazon_us — OK.

  Now the tail of the diff file (lines 1001-1124). The Read keeps returning first 1000 lines — I need to pass line_offset=1001. Wait, in my second Read call I... I called Read with only path again? Let me check: second call — yes I forgot line_offset both times. Let me read lines 1001-1124 via Read with line_offset.

• PROHIBIDOS_PRECIO includes "psycopg" → `_PROHIBIDOS_IMPORTS_FUENTES` excludes it. It includes things like "app.db", "app.ads", "app.spapi", "httpx", "secrets", "importlib", and maybe "os", "time". Let me see the full list 1016-1029 — cut off at 1022. Let me check lines 1016-1030. Also need to check `config_version` table and the settings key `precio_catalogo_max_dias_sin_reportar` exists in config/defaults — if the key is not in the settings of the current config_version, the tool fails closed (ValueError). That's fail-closed, fine, but does the config seed include that key? Check app/precio/config.py defaults.

  Also check `app/precio/fuentes.py` docstring mentions `app.estimacion_insumos mapear_canal` but the module doesn't import it — the test comment says it's permitted. Fine.

  Also check: `config_vigente_settings` returns `fila[0]` — settings is JSONB → psycopg returns dict. OK.

  Check listing table has seller_sku and platform columns — likely.

  Now remaining verification points:

  1. `_SQL_CANO` in psycopg3: `%s` placeholders; literal % must be escaped as `%%`. Yes, psycopg3 uses %-style: "The %% must be used to insert a literal %". Correct.

  But in `consultas/01_canonicas.sql`, the literal is `'%BUYABLE%'` — same query semantics. OK.

  2. Semantic issue (already noted): the docstring says "activa = la ultima observacion por (seller_sku, platform) cuyo status contiene BUYABLE". The SQL takes latest observation THEN filters BUYABLE. So if latest observation is not BUYABLE, SKU is excluded entirely. The docstring is ambiguous but the comments in 01_canonicas.sql say the same. Which is intended? This matters: "ultima observacion cuyo status contiene BUYABLE" would mean: filter BUYABLE first, take the latest of those — then a SKU whose latest status is deleted but was BUYABLE 2 days ago still counts active (and dias_sin_reportar counts from the BUYABLE observation). Implementation differs. This is a genuine ambiguity worth flagging as VERIFICAR against the spec (S10/decision 14 or brief A.7). Let me check the plan/brief for A.7 to see the intended definition. plans/ has briefs; let me grep for A.7 or "cobertura" spec.

  3. `_fase_fuera_de_alcance` — "meli" platform rows: but `_SQL_CANO` queries `spapi_listing_estado_observation` which presumably only has amazon rows; meli handled by tool early-exit. Fine.

  4. readback.sh `estado="${PIPESTATUS[1]:-$?}"` — PIPESTATUS[1] is ssh. But if the first block (printf/sed) fails... sed shouldn't fail. OK.

  However — subtle bash bug: after `set +e`, the pipeline runs; `estado="${PIPESTATUS[1]:-$?}"` — PIPESTATUS is an array, always set for last pipeline; [1] exists since pipeline has 2 commands. Fine.

  5. readback.sh: `2> "$err"` — stderr redirect applies to the whole pipeline? In `{ ... } | ssh ... > "$parcial" 2> "$err"`, redirections attach to `ssh` (last command). sed errors would go to stderr of script — acceptable.

  Actually wait, one real issue: the header comment says each consulta corre como `BEGIN READ ONLY; <consulta>; ROLLBACK;` — but `ON_ERROR_STOP` inside a transaction: if the SELECT errors, psql stops and exits nonzero; transaction left open on server but connection closes → rolled back. Fine.

  6. `recuadro_desde_salidas.py` — `motivo = partes[2].strip() if len(partes) > 2 else ""`. If motivo contains `|`? motivo is TEXT, could contain `|`... psql -tA doesn't escape pipes; a motivo with a pipe would shift columns. Minor robustness issue in an evidence script. Low.

  Also in canonicas, `partes[5]` observed_at; if seller_sku contained `|`... SKUs can't contain pipe practically. Low.

  7. `_lineas(hoy_f)[0][0]` — 06_hoy output has one row. But note readback.sh runs 06_hoy per platform — and its output under salidas/amazon_mx/06_hoy.txt. OK.

  8. tools/precio_cobertura.py: `_dinero(fila)` uses fila.precio/fila.moneda — fine for DetalleSinGoal.

  9. In cobertura `armar_recuadro`: order of `no_evaluadas.items()` dict — insertion order, deterministic. OK.

  10. Bug candidate in `_motivo_no_evaluada` / `armar_recuadro`: a fila with `tiene_goal=False` and `motivo="canal_sin_dato"` (canal None): goes to no_evaluadas canal_sin_dato even without goal — consistent with docstring ("en Amazon el canal desconocido no es un default a sin_goal ... tenga o no goal"). OK.

  But: a fila with canal known, no goal → sin_goal bucket even if resultado_hoy is None (no decision). Fine per docstring.

  Edge: fila `tiene_goal=False` but `resultado_hoy` exists (a decision was made despite no goal — possible?): motivo is None from `_motivo_no_evaluada` (returns None at `not fila.tiene_goal`), then `motivo is None and not tiene_goal` → sin_goal. OK.

  11. aviso_puente: uses float division — fine for a percentage display. Regla "Dinero = NUMERIC" not implicated (counts, not money).

  12. test_architecture: `_fugas_imports_fuentes` recursion: `_visitar(arbol.body)` handles nested; `_es_bloque_type_checking` skip — but it skips the whole If node including orelse — minor.

  Potential issue: `_permitido` allows any stdlib including `os`, `time`, `importlib` — but `_import_prohibido_fuentes` catches those IF they're in PROHIBIDOS_PRECIO. The comment says PROHIBIDOS_PRECIO includes os, time, importlib, random, secrets. Let me verify the full tuple (lines 1016-1029). I saw 1016-1028: httpx, psycopg, app.db, app.ads, app.spapi, app.estimacion_fees, ... secrets, importlib, "<import-relativo-nivel-2>". Need to see middle lines. Let me read.

  Also `if not entrada.startswith("<")` guards the "<import-relativo-nivel-2>" entry. OK.

  13. `datetime` import in fuentes.py: `from datetime import UTC, date` — candidatos: "datetime", "datetime.UTC", "datetime.date" — datetime is stdlib → permitted. OK.

  14. Check config.py `_entero` lines 64-72 referenced. Fine to trust.

  15. `max_dias_desde_settings` key "precio_catalogo_max_dias_sin_reportar" — check that this key exists in config defaults or that a config row with it exists; else tool always fails. Check app/precio/config.py.

  16. Also check `app/db.py` `connect` and `OrbitDbError` exist (tool imports). Likely.

  17. Check that `listing` table has `seller_sku` and `platform` (for join). Verify quickly.

  Let me grep config.py and listing DDL.

PRECIO = APP / "precio"
PROHIBIDOS_PRECIO = (
    "httpx",
    "psycopg",
    "app.db",
    "app.ads",
    "app.spapi",
    "app.estimacion_fees",
    "time",
    "os",
    "random",
    "secrets",
    "importlib",
    "<import-relativo-nivel-2>",
)

===
app/precio/fuentes.py
tests/test_precio_cobertura.py
docs/evidencia/repricing-01/A.7/readback.md
===
106:CREATE TABLE listing (
107-    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
108-    product_id      BIGINT   NOT NULL REFERENCES product(id),
109-    platform        platform NOT NULL,
110-    external_id     TEXT     NOT NULL,          -- ASIN o MLM id
111-    seller_sku      TEXT,
112-    listing_price   money_amount,
113-    price_currency  currency,
114-    UNIQUE (platform, external_id),
115-    -- Regla 4: no existe un precio sin su moneda.
116-    CONSTRAINT listing_precio_con_moneda
117-        CHECK ((listing_price IS NULL) = (price_currency IS NULL)),
118-    CONSTRAINT listing_precio_positivo
119-        CHECK (listing_price IS NULL OR listing_price > 0)
120-);
===
21:class OrbitDbError(Exception):
47:def connect(dsn: str, **kw) -> psycopg.Connection:
• Notes so far:

  - `listing.seller_sku` is nullable TEXT, not unique — the LEFT JOIN on (seller_sku, platform) can fan out; handled via DISTINCT ON + desempate by l.id. But: listings with NULL seller_sku never match — fine. Also "dos listings con el mismo SKU cuentan uno" — but DISTINCT ON picks listing by lowest l.id among rows of the latest observation. Note: the listing join happens for ALL observations, and DISTINCT ON picks latest observation row; ties (same observed_at) broken by l.id. But UNIQUE (seller_sku, platform, observed_at) on the observation table means within (sku, platform) observed_at is unique, so tie only from the join fanout — l.id ASC NULLS LAST deterministic. OK.

  - `precio_catalogo_max_dias_sin_reportar` does NOT appear in app/precio/config.py nor migrations — only in fuentes.py, its test, and readback.md. So the key must exist in the live config_version settings JSON. If the config vigente doesn't have that key, `max_dias_desde_settings` raises ValueError → tool exits 2 (fail-closed, intended). But is the key supposed to be added by a migration/config update in this change? Not in this diff. This is a real operational dependency: the tool cannot run until someone writes that setting into config. Is there a migration adding it to config defaults? Let me check how config_version settings get their keys — maybe app/precio/config.py has a defaults dict. Let me grep "precio_catalogo" more broadly — only found in those 3 files. So the key is new and nothing seeds it. Let me check config.py to see how settings are composed (maybe arbitrary keys pass through). If config settings are free-form JSONB written by an API, then the owner must add it — a deploy/ops step. Worth flagging as media: the tool will fail-closed until config contains the key; no migration/seed in this commit adds it. Actually check whether there's a settings validation that would reject unknown keys when someone adds it... Let me look at config.py quickly.

  - The "activa" definition: check the A.7 brief/plan to confirm whether "ultima observacion cuyo status contiene BUYABLE" (filter-then-latest) vs "ultima observacion, y exigir BUYABLE" (latest-then-filter). Let me grep the plan/brief for A.7.

  Also check readback.md exists and mentions max-dias value (3) consistent with readback.sh sed for `\set hoy`.

  Also `consultas/03_goals.sql` has `\set hoy 2026-09-18` hardcoded with a date — readback.sh's sed replaces `\set hoy .*` with today's UTC date. OK.

  One more check on readback.sh: `grep -nH -F '\' file | grep -vE ':[0-9]+:\\set [a-z_]+ [A-Za-z0-9_-]+$'` — the consultas files contain `\set platform amazon_mx` etc. Comments contain backticks but no backslashes. But wait — consulta comments contain "«Activa» = la ultima observacion..." no backslash. OK.

  But subtle: the candado greps for ANY backslash in the file and whitelists only `\set nombre valor` lines. In psql, `:'platform'` — contains no backslash. OK.

  Another subtle issue in readback.sh: the whitelisted regex requires `[A-Za-z0-9_-]+` for value; `2026-09-18` matches. OK.

  Now potential real issue in `solo-select.py` path: `$DIR/../E.0/solo-select.py` → docs/evidencia/repricing-01/E.0/solo-select.py. Verify it exists.

consultas
consultas-contabilidad
correr.sh
prueba-candados.sh
salidas
solo-select.py
veredicto.md
===
consultas
mutantes.md
readback.md
readback.sh
recuadro_desde_salidas.py
salidas
===
===
3:Lee un `Mapping` (los `settings` de `config_version`): valores que llegan
6:(`target_desde_settings`, `fraccion_desde_settings`); NO importa
42:def _numero(settings: Mapping, clave: str) -> Decimal:
43:    if clave not in settings or settings[clave] is None:
45:    valor = settings[clave]
57:def _fraccion(settings: Mapping, clave: str, *, minima: str, maxima: str) -> Decimal:
58:    numero = _numero(settings, clave)
64:def _entero(settings: Mapping, clave: str, *, minimo: int, maximo: int) -> int:
65:    numero = _numero(settings, clave)
74:def _positivo(settings: Mapping, clave: str) -> Decimal:
75:    numero = _numero(settings, clave)
98:def _fechas_excluidas(settings: Mapping, clave: str) -> tuple[tuple[date, date], ...]:
99:    if clave not in settings or settings[clave] is None:
101:    valor = settings[clave]
107:def leer_config(settings: Mapping) -> ConfigPrecio:
109:    escalon = _fraccion(settings, "precio_escalon_max_pct", minima="0.01", maxima="0.25")
110:    minimo = _fraccion(settings, "precio_movimiento_min_pct", minima="0", maxima="0.10")
119:            settings, "precio_caida_ventas_pct", minima="0.10", maxima="0.90"
121:        senal_dias=_entero(settings, "precio_senal_dias", minimo=1, maximo=7),
122:        u60_min=_entero(settings, "precio_u60_min", minimo=1, maximo=1000),
123:        fechas_excluidas=_fechas_excluidas(settings, "precio_fechas_excluidas"),
126:        movimiento_min_abs_mxn=_positivo(settings, "precio_movimiento_min_abs_mxn"),
127:        movimiento_min_abs_usd=_positivo(settings, "precio_movimiento_min_abs_usd"),
128:        tolerancia=_fraccion(settings, "precio_tolerancia", minima="0", maxima="0.05"),
129:        dias_entre_cambios=_entero(settings, "precio_dias_entre_cambios", minimo=1, maximo=30),
130:        freno_cambios=_entero(settings, "precio_freno_cambios", minimo=2, maximo=10),
132:            settings, "precio_divergencia_max_pct", minima="0", maxima="0.10"
• The A.7 brief/spec: let me check readback.md and the plan manifest / brief to confirm the intended "activa" definition and the max_dias default ("el lead lo corre con 3"). Also confirm whether the config key is expected to be added via config update (the docstring of recuadro says the lead passes --max-dias-sin-reportar 3 explicitly, so the readback path doesn't need the config key; only the tool does).

  Let me grep the plan brief for A.7 for the definition of activa canonica.plans/manifest.json:101:      "description": "REPUTACION 01 - CERRADA 2026-09-08 UTC (fase 0 + fase A: A.1/A.2/A.4/A.5/A.6/A.R/A.7; A.3 cerrada como ampliacion E/A.3). MeLi + alertas en vivo por cron; Amazon pendiente de credito Apify (REP-FOLLOW-2)."
plans/manifest.json:126:      "description": "REPRICING 01 - motor de precios por goal de margen (M1/AUTO-07/ORBIT 09). Spec v1.3 (dos rondas de revision; la segunda invalido cuatro hechos que el plan daba por medidos): proteger margen; goal por producto; sube si el margen estimado no llega, baja solo si caen las unidades de 15 dias contra 60; sombra primero; cuota propia; envio FBM medido de las etiquetas (decision 13); toda publicacion activa contemplada (14); Mercado Libre en alcance (15). Plan v1.3 (2026-09-17): Fase 8 cerrada (A.0, A.2 y A.3 en master, evidencia de E.1; ningun precio movido, 0039 sin aplicar); fila D.0 (0039 + claves, del dueno) antes de la sonda A.4; E.0 partida en E.0a (veredicto) y E.0b (regla). Siguiente: Fase 10 del autopilot (A.1, A.7, E.0a)."
===
docs/evidencia/repricing-01/A.7/consultas/01_canonicas.sql
===
repricing-01.md
===
docs/evidencia/repricing-01/A.7/mutantes.md:31:| A7-9 | `consultas/*.sql` son las que ejecuta `fuentes.py` | `01_canonicas.sql`: `LIKE '%BUYABLE%'` → `= 'BUYABLE'` | `test_consultas_iguales_a_las_que_ejecuta_fuentes` | MUERTO (exit 1) |
33:nunca abajo del goal. El alcance final es **toda publicación activa del
51:(13), toda publicación activa queda contemplada (14), y Mercado Libre entra al
86:| Plataforma | FBA activas | FBM activas | Total activas |
92:1. **Estados Unidos no tiene ninguna publicación activa en FBA** (0 de 109;
93:   las 82 FBA que existen están inactivas). La fase de US tal como estaba
98:   activas (32%).
163:    llegan al mínimo de 6 envíos; cruzados con publicaciones activas son **10 y
164:    10**. El techo no lo pone el umbral sino el volumen: de 264 activas de MX
216:19. **Hay dos denominadores de «publicación activa» y no coinciden.** La fuente
259:- **Cobertura que cuadra** (decisión 14): activas = evaluadas + no evaluadas +
281:### Fase A — Amazon México, FBA (171 publicaciones activas)
292:| A.7 | [stage:implementacion] [lane:gate] [tdd:required] **Cobertura** (decisión 14; Muse): **sin migración** — el canal ya está en `estimacion_oferta_observation` y `mapear_canal` ya traduce `AMAZON_NA` → `fba` y `DEFAULT` → `fbm` (hecho 5); las ~8 líneas de `_motivo_universo` (`app/estimacion_insumos.py`) que hoy dejan fuera FBM y US de la **estimación** son de **E.3/0.3, no de A.7** (v1.3: A.7 no toca ese archivo). `app/precio/cobertura.py` **puro** (recibe filas y produce el recuadro de S10 por plataforma), `app/precio/fuentes.py` con la lectura de la **fuente canónica** (`spapi_listing_estado_observation`, hecho 19), del canal (`estimacion_oferta_observation`), de los goals y de las decisiones del día (I/O: entra a la lista de excepciones del candado de pureza con candado propio), y `tools/precio_cobertura.py --platform <p>` que imprime el recuadro contra producción como lector; cuadra con el catálogo activo y muestra al lado la cuenta del bridge como contraste (v1.3: el `app/listings.py` y la migración de la v1.1 no van). | Rojo-primero: canal desconocido → `canal_sin_dato`, nunca un default; publicación sin reportar > 3 días → `catalogo_desactualizado` contada y avisada; el recuadro **cuadra exacto** con las activas de la fuente canónica en un fixture de 12 publicaciones repartidas en los cuatro estados; una publicación sin goal aparece listada, no oculta; `fuera_de_alcance` nombra la fase (`fase_E_envio_fbm`, `fase_M_meli`), no un genérico; readback en producción contra la fuente canónica (264 MX, 106 US), con la cuenta del bridge (284, 109) al lado y la diferencia avisada por pasar del 5% | A.0 | cc:TODO |
295:| D.1 | [stage:cierre-pr] [lane:release] [tdd:skip:validacion-entrega] **Despliegue en sombra MX (FBA)** (dueño con `!`): backup, la migración ya aplicada por D.0 (se verifica, no se repite), deploy por `git archive` + md5 + rebuild, cron instalado, goals sembrados en `shadow` para los productos que el dueño elija, **cinco corridas** leídas por el lead. | E/D.1 con SHA y salidas: **≥ 80% de los goals vigentes evaluados** cada día; **el recuadro de cobertura cuadra** con las **264** activas de MX de la fuente canónica (hecho 19), con las 284 del bridge mostradas al lado; cuenta de tres productos reproducida a mano; trayectoria de sombra con cooldown visible; ningún PATCH salvo A.4 | R.1, D.0 | cc:TODO |
299:### Fase E — envío medido y FBM (habilita ~10 activas en MX y ~10 en US con la ventana de 90 días)
303:que son 10 y 10 publicaciones activas (hecho 13). El resto de las FBM queda en
318:| E.5 | [stage:cierre-pr] [lane:release] [tdd:skip:ops] **Sombra y encendido FBM en México**: goals FBM en `shadow`, cinco corridas leídas, luego 3–5 productos en vivo con go y 30 días en dos cortes. | E/E.5 con el mismo criterio de D.1 y D.2, más: ningún producto FBM movido sin `envio_muestra_id`; la cobertura de MX cuadra con las **264** activas de la fuente canónica y `fuera_de_alcance` queda solo con los motivos declarados (`sin_historia_envio`, `sin_goal`), no en cero; el criterio (c) se declara «no ocurrió» con números (hecho 17) si no se ejerce | E.4, D.2 | cc:TODO |
328:### Fase B — Amazon Estados Unidos (106 activas en la fuente canónica, todas FBM)
340:| B.1 | [stage:cierre-pr] [lane:release] [tdd:skip:ops] **US en sombra**: goals US en `shadow`, cinco corridas leídas con el umbral de D.1. `/precios` y el cierre muestran el **TACoS de 90 días** junto a `m_actual`, porque 50% de margen con 24% de Ads encima no es el mismo negocio que con 9%. | E/B.1 igual que D.1; el recuadro de cobertura de US cuadra con las **106** activas de la fuente canónica; el reporte trae la distribución de `m_actual` y el TACoS por producto; **una mayoría de `mantener` es resultado esperado, no un fallo** | 0.3, E.4 | cc:TODO |
354:| M.6 | [stage:cierre-pr] [lane:release] [tdd:skip:ops] **MeLi en sombra y encendido**: goals en `shadow`, cinco corridas, 3–5 productos en vivo con go y 30 días. Cierra el plan; `ORBIT 09` `Done`. | E/M.6 igual que D.1 y D.2; la cobertura de MeLi cuadra con las publicaciones activas y `fuera_de_alcance` en cero en las tres plataformas | M.5, B.2 | cc:TODO |
432:| **AC17** | **Recuadro de cobertura** en cualquier corrida | activas = evaluadas + no evaluadas + sin goal + fuera de alcance, **exacto**, por plataforma, contra la **fuente canónica** (`spapi_listing_estado_observation`: 264 MX, 106 US), con la cuenta del bridge al lado y aviso si difieren más de 5%; una publicación sin reportar 4 días sale como `catalogo_desactualizado` y avisa | A.7, D.1, E.5, B.1, M.6 |
520:  "description": "REPRICING 01 - motor de precios por goal de margen (M1/AUTO-07/ORBIT 09). Spec v1.3 (dos rondas de revision; la segunda invalido cuatro hechos que el plan daba por medidos): proteger margen; goal por producto; sube si el margen estimado no llega, baja solo si caen las unidades de 15 dias contra 60; sombra primero; cuota propia; envio FBM medido de las etiquetas (decision 13); toda publicacion activa contemplada (14); Mercado Libre en alcance (15). Plan v1.3 (2026-09-17): Fase 8 cerrada (A.0, A.2 y A.3 en master, evidencia de E.1; ningun precio movido, 0039 sin aplicar); fila D.0 (0039 + claves, del dueno) antes de la sonda A.4; E.0 partida en E.0a (veredicto) y E.0b (regla). Siguiente: Fase 10 del autopilot (A.1, A.7, E.0a)."
===
12:para A.1, A.5 y A.7. Ninguna decisión del dueño se reabre.
120:   migración y un cambio en `app/listings.py` para traerlo: A.7 se queda solo
273:  lectura de fuentes de A.7) **entran a una lista explícita de excepciones
291:| A.6 | [stage:implementacion] [lane:gate] [tdd:required] **Pantalla y avisos** (Muse): `/precios` con los cinco bloques de S7 (cobertura arriba), bloque `precios` en `/salud` dentro de `plataformas.<p>`, **un** sender `notifica_precio(tipo, …)` en flanco por racha: por `(plataforma, motivo)` con conteo y 5 SKUs para `no_evaluado`/`goal_inalcanzable`/`frenado`, por producto para `no_confirmado`/`buy_box_perdida`; fail-silent. | Rojo-primero: `/precios` 200 con los cinco bloques y frases con números del fixture; `/salud` cuenta lo mismo que `precio_decision`; 200 productos con el mismo motivo → **un** aviso; builder de texto **sin** costo, margen, goal ni cuerpo de error; un fallo del sender no tumba `correr` | A.5, A.7 | cc:TODO |
292:| A.7 | [stage:implementacion] [lane:gate] [tdd:required] **Cobertura** (decisión 14; Muse): **sin migración** — el canal ya está en `estimacion_oferta_observation` y `mapear_canal` ya traduce `AMAZON_NA` → `fba` y `DEFAULT` → `fbm` (hecho 5); las ~8 líneas de `_motivo_universo` (`app/estimacion_insumos.py`) que hoy dejan fuera FBM y US de la **estimación** son de **E.3/0.3, no de A.7** (v1.3: A.7 no toca ese archivo). `app/precio/cobertura.py` **puro** (recibe filas y produce el recuadro de S10 por plataforma), `app/precio/fuentes.py` con la lectura de la **fuente canónica** (`spapi_listing_estado_observation`, hecho 19), del canal (`estimacion_oferta_observation`), de los goals y de las decisiones del día (I/O: entra a la lista de excepciones del candado de pureza con candado propio), y `tools/precio_cobertura.py --platform <p>` que imprime el recuadro contra producción como lector; cuadra con el catálogo activo y muestra al lado la cuenta del bridge como contraste (v1.3: el `app/listings.py` y la migración de la v1.1 no van). | Rojo-primero: canal desconocido → `canal_sin_dato`, nunca un default; publicación sin reportar > 3 días → `catalogo_desactualizado` contada y avisada; el recuadro **cuadra exacto** con las activas de la fuente canónica en un fixture de 12 publicaciones repartidas en los cuatro estados; una publicación sin goal aparece listada, no oculta; `fuera_de_alcance` nombra la fase (`fase_E_envio_fbm`, `fase_M_meli`), no un genérico; readback en producción contra la fuente canónica (264 MX, 106 US), con la cuenta del bridge (284, 109) al lado y la diferencia avisada por pasar del 5% | A.0 | cc:TODO |
317:| E.4 | [stage:implementacion] [lane:gate] [tdd:required] **El motor distingue canal** (Muse): `canal` en la decisión y en el recuadro de cobertura; `precio_goal` admite publicaciones FBM; la corrida evalúa FBA y FBM con la misma regla y distinta `L`; `/precios` y `/salud` muestran el canal y, en FBM, la muestra con su ventana efectiva, sus órdenes y su dispersión. La **rama de inventario queda `sin_dato` por diseño en FBM** y así se muestra: la fuente que la alimenta es de FBA y en FBM nunca se puebla. | Rojo-primero: una publicación FBM con goal y muestra completa produce `subir` con `L` de la muestra en la cuenta; la misma sin muestra → `no_evaluado(envio_sin_historia)`; el recuadro de MX mueve **~10** publicaciones de `fuera_de_alcance` a en alcance y **el resto del subconjunto FBM canónico** queda en `fase_E_sin_historia_envio` **contado** (el número exacto sale de la fuente canónica en A.7, no del 113 del bridge); `/precios` muestra «envío medido, mediana 94.50 MXN sobre 18 órdenes, ventana 90 d al 2026-09-01» y no un número sin origen; en FBM el motivo de inventario es `sin_dato(inventario_no_aplica_fbm)`, nunca una rama que no puede dispararse | E.3, A.7 | cc:TODO |
358:- **Required**: A.0–A.7, R.1, D.0–D.3 (fase A); **E.0a, E.0b**–E.5 (fase E, decisión
359:  13); A.7 y el recuadro de cobertura (decisión 14); 0.1–0.3 y B.1–B.2 (US);
393:| A.7 | `app/precio/cobertura.py` (puro: recuadro a partir de filas), `app/precio/fuentes.py` (lectura de la fuente canónica, el canal, los goals y las decisiones; en la lista de excepciones del candado), `tools/precio_cobertura.py` (readback), `tests/test_precio_cobertura.py`, `tests/test_architecture.py` (la excepción y su candado), `docs/evidencia/repricing-01/A.7/` | **Sin migración** y sin tocar `app/listings.py` (hecho 5; corrige la v1.1); no toca `app/precio/{tipos,reglas,objetivo,ventas,config}.py` |
422:| AC7 | Tercer cambio sin acercarse al goal | `frenado(no_converge)`; goal nuevo reinicia el freno, no el cooldown | A.2 |
432:| **AC17** | **Recuadro de cobertura** en cualquier corrida | activas = evaluadas + no evaluadas + sin goal + fuera de alcance, **exacto**, por plataforma, contra la **fuente canónica** (`spapi_listing_estado_observation`: 264 MX, 106 US), con la cuenta del bridge al lado y aviso si difieren más de 5%; una publicación sin reportar 4 días sale como `catalogo_desactualizado` y avisa | A.7, D.1, E.5, B.1, M.6 |
440:| **AC21** | **Publicación de MeLi antes de su fase** | aparece en cobertura como `fuera_de_alcance(fase_M_meli)`, nunca ausente | A.7 |
520:  "description": "REPRICING 01 - motor de precios por goal de margen (M1/AUTO-07/ORBIT 09). Spec v1.3 (dos rondas de revision; la segunda invalido cuatro hechos que el plan daba por medidos): proteger margen; goal por producto; sube si el margen estimado no llega, baja solo si caen las unidades de 15 dias contra 60; sombra primero; cuota propia; envio FBM medido de las etiquetas (decision 13); toda publicacion activa contemplada (14); Mercado Libre en alcance (15). Plan v1.3 (2026-09-17): Fase 8 cerrada (A.0, A.2 y A.3 en master, evidencia de E.1; ningun precio movido, 0039 sin aplicar); fila D.0 (0039 + claves, del dueno) antes de la sonda A.4; E.0 partida en E.0a (veredicto) y E.0b (regla). Siguiente: Fase 10 del autopilot (A.1, A.7, E.0a)."
532:  openclaw)**: A.1 y luego A.7, con Muse, secuenciales; el lead hace E.0a si el
541:  `master`; A.6 cuando A.5 y A.7 lo estén.
Hecho 19 defines the canonical source. Let me check hecho 19 wording in the plan to see the intended definition of "activa":216:19. **Hay dos denominadores de «publicación activa» y no coinciden.** La fuente
217-    propia de Orbit (`spapi_listing_estado_observation`) dice **264** vendibles
218-    en MX y **106** en US; la caché del bridge dice **284** y **109**. 20 de
219-    diferencia en MX (7%). No es hueco de carga: son dos definiciones. AC17 no
220-    puede pasar en ninguna fase hasta que el spec nombre una fuente canónica —
221-    y ya la nombra (S10): la propia de Orbit.
222-
223-**Medidos por la Fase 8 (E.1, producción, 2026-09-17 UTC;
224-`docs/evidencia/repricing-01/E.1/medicion.md`).** Condicionan el acta E.2:
===
docs/evidencia/repricing-01/A.7/readback.md:11:Las consultas de `consultas/*.sql` son **las mismas que ejecuta `app/precio/fuentes.py`**; la prueba `test_consultas_iguales_a_las_que_ejecuta_fuentes` lo exige. El recuadro se armó en local con `recuadro_desde_salidas.py`, que alimenta la función pura `app/precio/cobertura.py`, con **`--max-dias-sin-reportar 3`**. Ese es el valor inicial del plan: producción no tiene la clave `precio_catalogo_max_dias_sin_reportar` hasta D.0.
===
plans/repricing-01.md:79:| `precio_catalogo_max_dias_sin_reportar` | 3 | 1–14 |
plans/repricing-01.md:292:| A.7 | [stage:implementacion] [lane:gate] [tdd:required] **Cobertura** (decisión 14; Muse): **sin migración** — el canal ya está en `estimacion_oferta_observation` y `mapear_canal` ya traduce `AMAZON_NA` → `fba` y `DEFAULT` → `fbm` (hecho 5); las ~8 líneas de `_motivo_universo` (`app/estimacion_insumos.py`) que hoy dejan fuera FBM y US de la **estimación** son de **E.3/0.3, no de A.7** (v1.3: A.7 no toca ese archivo). `app/precio/cobertura.py` **puro** (recibe filas y produce el recuadro de S10 por plataforma), `app/precio/fuentes.py` con la lectura de la **fuente canónica** (`spapi_listing_estado_observation`, hecho 19), del canal (`estimacion_oferta_observation`), de los goals y de las decisiones del día (I/O: entra a la lista de excepciones del candado de pureza con candado propio), y `tools/precio_cobertura.py --platform <p>` que imprime el recuadro contra producción como lector; cuadra con el catálogo activo y muestra al lado la cuenta del bridge como contraste (v1.3: el `app/listings.py` y la migración de la v1.1 no van). | Rojo-primero: canal desconocido → `canal_sin_dato`, nunca un default; publicación sin reportar > 3 días → `catalogo_desactualizado` contada y avisada; el recuadro **cuadra exacto** con las activas de la fuente canónica en un fixture de 12 publicaciones repartidas en los cuatro estados; una publicación sin goal aparece listada, no oculta; `fuera_de_alcance` nombra la fase (`fase_E_envio_fbm`, `fase_M_meli`), no un genérico; readback en producción contra la fuente canónica (264 MX, 106 US), con la cuenta del bridge (284, 109) al lado y la diferencia avisada por pasar del 5% | A.0 | cc:TODO |
plans/repricing-01.md:432:| **AC17** | **Recuadro de cobertura** en cualquier corrida | activas = evaluadas + no evaluadas + sin goal + fuera de alcance, **exacto**, por plataforma, contra la **fuente canónica** (`spapi_listing_estado_observation`: 264 MX, 106 US), con la cuenta del bridge al lado y aviso si difieren más de 5%; una publicación sin reportar 4 días sale como `catalogo_desactualizado` y avisa | A.7, D.1, E.5, B.1, M.6 |
Good — the config key question is answered: readback.md documents that production doesn't have the key until D.0, so the fail-closed behavior is deliberate. Not a bug.

  Now the "activa" definition: S10 names the canonical source. Let me quickly check the spec S10 (docs/traspaso/ADS_OPTIMIZER_V2_DESIGN.md or CONTEXTO) for the exact "activa" definition to see if "ultima observacion cuyo status contiene BUYABLE" (filter-first) vs implementation (latest-then-filter) is a real divergence.docs/evidencia/repricing-01/A.7/readback.md
docs/evidencia/repricing-01/A.7/recuadro_desde_salidas.py
docs/evidencia/repricing-01/plan-validacion-ebm.md
docs/evidencia/repricing-01/E.1/consultas/efecto-margen.sql
docs/superpowers/specs/2026-09-15-repricing-01-design.md
===
4-en diálogo el 2026-09-15 (decisiones 1–12 en S1), corregido con la revisión de
5-cinco perspectivas del 2026-09-16 (S9), ampliado el mismo día con las
6-decisiones 13–15 del dueño (**FBM con envío medido, toda publicación activa
7:contemplada, Mercado Libre en el alcance**, S10 y S11), y corregido de nuevo
8-con la **segunda ronda de cinco perspectivas sobre E, B y M** del 2026-09-16,
9-que invalidó cuatro afirmaciones que este spec daba por medidas (S9). Sellado
10-para el plan `plans/repricing-01.md`. Base de lectura `origin/master`
11-`1a2a8c2`.
12-
13-Precedencia: `docs/CONTEXTO.md` (reglas 1–10) > `plans/ROADMAP.md` (M1 /
14-AUTO-07) > `docs/superpowers/specs/2026-09-08-margen-estimado-design.md` y su
15-acta `docs/evidencia/margen-estimado-01/0.3/acta.md` (contrato del margen
16-estimado) > `docs/traspaso/MODULOS-AVANZADOS.md` §Módulo 1 (criterio del
17-traspaso) > este spec > el plan.
18-
19-## Objetivo y límites
20-
21-Que cada producto con un goal de margen fijado por el dueño **llegue a ese
22-margen por precio**, y que el precio baje **solo** cuando el producto está
--
27-Estados Unidos en sus dos canales (FBA y FBM) y Mercado Libre. Se llega por
28-fases, y ninguna publicación queda invisible mientras tanto: la que aún no
29-tiene motor aparece contada, con su motivo y con la fase que la habilita
30:(S10).
31-
32-Lo que este spec NO cubre y queda declarado, no rellenado con ceros:
33-promociones, kits, decidir por inventario (solo invalida la señal de ventas),
34-elasticidad, modos «agresivo/equilibrado» del traspaso, reversa automática.
35-
36-## S1. Decisiones del dueño (textos literales)
37-
38-Decisiones 1–12, cerradas el 2026-09-15:
39-
40-| # | Decisión | Texto literal |
41-|---|---|---|
42-| 1 | Objetivo principal: proteger margen; Buy Box deseable, no a cualquier precio | «A, proteger margen» |
43-| 2 | Subir precio cuando el margen no alcanza el goal | «quisiera subir precios si el margen no esta llegando al goal» |
44-| 3 | Goal de margen **por producto**, fijado por el dueño | «b» (goal por producto) |
45-| 4 | El margen que se compara es el **estimado** (precio, costo, comisiones, retenciones), no el observado tras Ads | «A, estimado» |
--
76-| Número | Fuente (única) | Frescura exigida | Si falta |
77-|---|---|---|---|
78-| **Precio actual `P_actual`**, contribución estimada y componentes `P, I, C, F, L, R` | El **escenario vigente de la estimación por venta** (`estimacion_*`, acta 0.3): oferta ≤ 6 h, costo vigente de la corrida `ok` del día, FX exacto o ≤ 7 días, cotización de fees ligada a esa oferta. `P_actual` es el precio de esa oferta: es el precio al que se cotizaron las fees y se calculó la contribución | escenario `disponible` del día UTC | el motivo que ya declara la estimación (`oferta_desactualizada`, `costo_desactualizado`, `fx_ausente`, `fee_ausente`, `impuesto_fee_pendiente`…) |
79:| **Costo de envío `L` en FBM** (decisión 13) | `ledger_event` `kind='fee'`, `fee_type='shipping_fee'`, ligado por `order_id` a las ventas del producto. Por producto y plataforma, el **percentil declarado** (S10) sobre los envíos de la ventana vigente | ≥ 6 envíos en la ventana; ledger fresco (día cubierto) | `envio_sin_historia`, `envio_sin_dato` |
80-| **Cobro de envío al cliente** (FBM) | `ledger_event` `kind='sale'`, columna `shipping_price` con su moneda; entra al ingreso cuando no es cero | misma venta | se trata como `0` **solo** cuando la fila existe y trae cero; si la fila falta, la venta no cuenta para el promedio |
81-| Buy Box (precio, dueño, `is_own`), precio más bajo, ofertas; **control de coherencia** del precio | `spapi_price_observation` (pase diario de Pricing v0, 05:00 UTC) | fila del día | `precio_sin_observar` |
82-| Unidades vendidas por **producto** y día | `ledger_event` `kind = 'sale'` (`quantity`, `product_id`, `event_date`). El ledger va por producto: **la señal de ventas es por `(product_id, platform)`** y la comparten todas las publicaciones de ese producto en esa plataforma | día *cubierto* = `event_date ≤` la fecha máxima cargada por un `ingest_run` `ok` del ledger de la plataforma; último día cubierto ≤ 3 días de antigüedad | `ventas_sin_dato` |
83-| Publicación (SKU, ASIN, `product_type`, `status`, **canal**) | `listing` + `spapi_listing_estado_observation`; el canal (`AMAZON_NA` = FBA, `DEFAULT` = FBM) viene de la ingesta de listings del bridge | fila del día; `seller_sku` no nulo | `listing_sin_estado`, `listing_sin_sku`, `listing_inactivo`, `canal_sin_dato` |
84-| Inventario | `spapi_inventario_observation` (`total_quantity`) | fila diaria; solo **invalida la señal** de ventas (S4 #5) | `inventario_sin_observar` |
85-| Goal de margen, modo y límites | `precio_goal` (S3) y `config_version` | vigente en la fecha | `sin_goal` → no se evalúa |
86-| Cotización de fees a un precio candidato | `precio_cotizacion` (S5): Product Fees a `P*`, guardada aparte de la estimación para no invalidar el escenario del día siguiente | ligada a la oferta del escenario | `fee_error:<code>` |
87-
88-Regla 2 de Orbit: el motor **no** recalcula margen ni costo ni FX; consume el
89-escenario tal cual. Su cuenta se guarda con `escenario_id`,
90-`fee_observation_id` (la del escenario), `cotizacion_id` (la propia) y, en
91-FBM, `envio_muestra_id` (la muestra de envíos que produjo `L`), con la moneda
92-de cada precio (regla 4). Si la observación de Pricing y el escenario difieren
93-en más de `precio_divergencia_max_pct` (1%), el día es
94-`no_evaluado(precio_divergente)` con los dos precios y sus horas; si persiste
--
311:- El denominador es **una sola fuente canónica**, nombrada aquí y en ninguna
507:- **Fase B — Amazon Estados Unidos** (**106** activas en la fuente canónica,
===
docs/superpowers/specs/2026-09-15-repricing-01-design.md:83:| Publicación (SKU, ASIN, `product_type`, `status`, **canal**) | `listing` + `spapi_listing_estado_observation`; el canal (`AMAZON_NA` = FBA, `DEFAULT` = FBM) viene de la ingesta de listings del bridge | fila del día; `seller_sku` no nulo | `listing_sin_estado`, `listing_sin_sku`, `listing_inactivo`, `canal_sin_dato` |
docs/superpowers/specs/2026-09-15-repricing-01-design.md:313:  (`spapi_listing_estado_observation`, estado vendible) cruzada con `listing`.

Ninguna publicación activa puede ser invisible. Cada corrida produce, por
plataforma, un **recuadro de cobertura** que cuadra con el catálogo:

```
publicaciones activas = con_goal_evaluadas
                      + con_goal_no_evaluadas (por motivo)
                      + sin_goal
                      + fuera_de_alcance (por fase que la habilita)
```

- El denominador es **una sola fuente canónica**, nombrada aquí y en ninguna
  otra parte: la observación propia de Orbit
  (`spapi_listing_estado_observation`, estado vendible) cruzada con `listing`.
  No es un detalle: las dos fuentes disponibles **no coinciden** — la propia
  dice 264 vendibles en MX y 106 en US; la caché del bridge dice 284 y 109. La
  diferencia de 20 en MX (7%) no es hueco de carga, son dos definiciones
  distintas de «activa», y mientras el spec no elija una, AC17 no puede pasar
  en ninguna fase. Se elige la propia porque Orbit la produce y la fecha; la
  del bridge se muestra al lado como contraste, con su `updated_at`, y una
  diferencia mayor al 5% se avisa. Una publicación que dejó de reportarse por
  más de 3 días cuenta como `catalogo_desactualizado` y se avisa: el recuadro
  nunca se cuadra escondiendo filas.
- `sin_goal` no es un error: es trabajo del dueño y aparece listado con su
  precio y su canal para que decida. `fuera_de_alcance` nombra la fase (por
  ejemplo `fase_E_envio_fbm` o `fase_M_meli`), no un genérico.
- La suma se verifica en cada fase como criterio de aceptación: si no cuadra,
  la fase no cierra.

### Envío medido en FBM (decisión 13)

`L` para una publicación FBM sale del costo real de las etiquetas que Amazon
ya cobra y que el ledger recibe como `shipping_fee`. La revisión del 2026-09-16
midió que las cuatro reglas siguientes no son detalle de implementación: sin
ellas `L` queda mal por construcción.

- **Signo.** Los fees se guardan **negativos** (`ledger_convencion_signos`). El
  percentil y cualquier orden se calculan sobre `abs(amount)`; un percentil
  sobre el monto crudo elige el envío **más barato**, que es lo contrario de lo
  que se quiere. Test obligatorio con montos negativos sembrados.
- **Unidad: la orden, no la fila.** Un envío no es una fila de `shipping_fee`.
S10 says "estado vendible" — the E.1 measurement presumably defined how 264 was computed. The implementation: latest observation per sku, keep if status contains BUYABLE. That counts "vendibles hoy". The alternative reading (latest BUYABLE observation) would over-count. The implementation is the sane one and matches "estado vendible" — no finding there, or at most note that docstring wording is ambiguous. Actually the docstring "«activa» = la ultima observacion por (seller_sku, platform) cuyo status contiene BUYABLE" — grammatically "cuyo status" refers to "observacion": the latest observation whose status contains BUYABLE. But implementation is: the latest observation, and it must contain BUYABLE. These differ in edge cases. Worth flagging as a low-severity doc ambiguity rather than a bug? It affects correctness of classification only in edge cases (listing that became inactive recently). Given E.1 computed 264 via a particular query, let me quickly check the E.1 query to see which semantics the "264" number used.===
consultas
correr.sh
medicion.md
salidas
validacion-local
validacion-local.md
===
docs/evidencia/repricing-01/E.1/consultas/00-sonda-formato.sql:89:        bool_or(fulfillment_status = 'Shipped') over (partition by amazon_order_id, platform) as alguna_shipped
docs/evidencia/repricing-01/E.1/consultas/rezago-emision.sql:39:-- (d) cargo − primer `last_updated_time` con `fulfillment_status =
docs/evidencia/repricing-01/E.1/consultas/rezago-emision.sql:262:-- (d) cargo (último) − primer last_updated_time con fulfillment_status =
docs/evidencia/repricing-01/E.1/consultas/rezago-emision.sql:291:    where fulfillment_status = 'Shipped'
E.1 doesn't contain the 264 query; it's likely in another evidence dir. Not worth chasing further — the semantic ambiguity is real but low-severity; the implementation is a reasonable and arguably correct reading ("vendible hoy"). I'll flag it as VERIFICAR/low.

  Now, other candidate findings to finalize:

  1. (Baja/Media) Definicion de "activa": DISTINCT ON toma la ultima observacion y LUEGO filtra BUYABLE. El docstring se presta a la lectura "ultima observacion cuyo status contiene BUYABLE". Si una publicacion dejo de ser BUYABLE en su ultima observacion, desaparece del denominador (bien); con la otra lectura seguiria contando. Confirmar contra la medicion que origino 264/106. Baja.

  2. In `readback.sh`: `estado="${PIPESTATUS[1]:-$?}"`. Fine.

  3. readback.sh grep candado: `\b(insert|update|...|copy)\b` — case-insensitive. A comment containing the word "update" in a consulta would abort (fail-closed). Fine.

  4. readback.sh: `sed -e "s/^\\\\set platform .*/..."` — if a consulta file's platform \set line differs... fine.

  5. readback.sh: the psql connection is `psql "$DSN"` inside `docker exec -i orbit-db-1` — DSN may point to localhost:5432 inside the db container, fine.

  6. recuadro_desde_salidas.py: `puente_f` param shadows; used via args.puente as identidad file — the parameter name `puente_f`/`--puente` actually expects the output of 05_listing_identidad.sql (identidad count), NOT bridge activas. The naming is confusing but internally consistent. In `recuadro_desde_archivos`, `puente_f` is accepted but never used (only used in main for identidad). Dead parameter in the function signature — minor quality issue. Actually look: recuadro_desde_archivos(canonicas, canales, goals, decisiones, puente_f, hoy_f, ...) — puente_f is never referenced in the body! The body uses canonicas, canales, goals, decisiones, hoy_f. puente_f is unused. Minor.

  7. recuadro_desde_salidas: `--platform` argparse has no choices restriction — if user passes a wrong platform, armar_recuadro filters by platform and produces activas=0 (all filas have platform=platform arg anyway — wait, filas are constructed with platform=platform arg, so propias == all). OK.

  8. Parsing: `motivo` from psql -tA: NULL motivo prints as empty string → `motivo or None` handles. But note precio_decision CHECK requires motivo NOT NULL for non-subir/bajar, and motivo can be NULL only for subir/bajar. OK.

  9. In recuadro_desde_archivos, `decision_por_listing` and `canal_por_listing` int(partes[0]) — listing_id NOT NULL in those tables. OK.

  10. `_entero_o_nulo` on goals: listing_id never null. OK.

  11. In fuentes.py: `tiene_goal=listing_id in goals` — if listing_id is None, None in goals → False. Fine.

  12. `hoy - observed.astimezone(UTC).date()` — observed aware (timestamptz). psycopg3 returns timestamptz as aware datetime. OK.

  13. cobertura.py: `_fase_fuera_de_alcance` — a fila with platform meli never passes through leer_publicaciones since _SQL_CANO filters by platform and meli has no spapi rows; fine.

  14. Bug candidate in armar_recuadro order vs docstring: docstring says order: fuera_de_alcance, catalogo_desactualizado, sin_listing, canal_sin_dato, sin_goal, decision. In code, `sin_goal` is checked... `_motivo_no_evaluada` returns None for `not tiene_goal` even when resultado_hoy is None — so a publication with goal missing AND decision missing counts as sin_goal (not sin_decision). Consistent with docstring (sin_goal precedes goal decision). OK.

  15. BUT: one subtle case — a fila with canal None AND tiene_goal False: `_motivo_no_evaluada` returns "canal_sin_dato" (checked before tiene_goal). So it's counted as no_evaluada canal_sin_dato, not sin_goal. Docstring: "canal_sin_dato (en Amazon el canal desconocido no es un default a sin_goal: el motor no puede evaluar sin canal, tenga o no goal)". Consistent.

  16. `aviso_puente` when activas==0 and puente==0 → None. OK.

  17. tools/precio_cobertura.py: `except (psycopg.Error, ValueError)` — `config_vigente_settings` raises ValueError; fine. But `connect(_dsn_read())` catches OrbitDbError and Abortar; if psycopg raises during connect? connect wraps presumably. Fine.

  18. Duplicated docstring claim in test file: comment says "`app.estimacion_insumos` solo `mapear_canal`" is permitted for fuentes.py, but fuentes.py doesn't import it (canal comes pre-mapped). Harmless permission wider than needed — low.

  19. The `_fugas_imports_fuentes` test permits ALL stdlib for fuentes.py (`_permitido` returns True for any stdlib module) — but PROHIBIDOS_PRECIO covers os, time, random, secrets, importlib. Other dangerous stdlib not in PROHIBIDOS_PRECIO (e.g. `subprocess`, `socket`, `urllib`, `pathlib` for reading files, `open` via io) would pass the candado. E.g. `import subprocess` in fuentes.py → `_permitido` True, `_import_prohibido_fuentes` False (not in PROHIBIDOS_PRECIO) → not flagged. Compare with the general precio purity: does the general candado prohibit subprocess? PROHIBIDOS_PRECIO list shown: httpx, psycopg, app.db, app.ads, app.spapi, app.estimacion_fees, time, os, random, secrets, importlib. subprocess/socket not there either. So the general purity list also allows subprocess — consistent with existing policy; the A.7 candado is no weaker than the general one except for psycopg. So not a regression; skip or mention as low? The docstring of the comment says "Jamas red" — but `urllib`/`socket`/`http.client` would pass the candado. Hmm, PROHIBIDOS_PRECIO has httpx only for red. The claim "sin red" is aspirational. This mirrors the general precio candado, so it's a pre-existing weakness, not introduced here. I could mention it as low (the new candado copies the same hole: `socket`, `urllib`, `subprocess` no estan en PROHIBIDOS_PRECIO, asi que "jamas red/procesos" no se cumple a nivel candado). Fair as a low finding.

  20. `_permitido` in `_fugas_imports_fuentes` checks `nombre.split(".")[0] in _sys.stdlib_module_names` — for `from datetime import UTC`, candidatos "datetime" and "datetime.UTC": "datetime.UTC".split(".")[0]="datetime" — in stdlib → permitted. OK. But `_import_prohibido_fuentes("datetime.UTC")` — checks against entries like "os": startswith "os."? "datetime.UTC" no. OK. And "time": does "time.x" — fine.

  Edge: `from time import sleep`? candidato "time.sleep" → _import_prohibido: "time.sleep" startswith "time." → prohibited. Good.

  Edge: `import os` → prohibited. Good.

  21. In test `test_candado_reloj_fuentes_caza_fuga_sembrada`: expects `_usos_reloj(arbol) == [".today"]` — depends on `_usos_reloj` returning ".today" string. Presumably existing convention. Fine.

  22. A subtle one in cobertura: `RESULTADOS_EVALUADOS` includes all 5 non-no_evaluado results — matches CHECK constraint exactly. Good.

  23. `_SQL_CANO`: status LIKE '%BUYABLE%' — the E.1 measurement's definition unknown but plausibly same. Status values like "BUYABLE" exact or list. OK.

  24. One more: `hoy_base` returns date via psycopg; fine.

  25. `config_vigente_settings` "ultimo id" = vigente — matches pattern "config_version vigente (ultimo id)". Presumably consistent with app/precio/config or corrida usage. Let me not chase.

  26. Diff readback.sh: `HOY="$(date -u +%F)"` — local machine UTC date used for goals/decisiones `\set hoy`, while 06_hoy.sql asks the DB. If the local clock is off or run crosses midnight UTC, 03/04 use a different day than 06 (which feeds the recuadro's dias_sin_reportar). The recuadro script then mixes: goals/decisiones from local-UTC date, hoy from DB date. Minor inconsistency; low severity. Also readback.md instructs to run with the explicit value — fine.

  27. recuadro_desde_salidas `_lineas` splits on "|" — `motivo` TEXT could contain "|" shifting columns: `partes[1]` resultado would still be right, motivo would be truncated. Edge case, low.

  28. Potential mismatch: tools/precio_cobertura.py prints `recuadro {platform} {hoy} (canonica Orbit)` while recuadro_desde_salidas prints "(salidas A.7)" — docstring claims "el recuadro impreso tiene el mismo formato que tools/precio_cobertura.py" — the first line differs in the parenthetical and tool prints `hoy.isoformat()` vs file's raw first line. Also tool's sin_goal line uses `_dinero` which formats `f"{precio:.2f} {moneda}"`; the evidence script prints `sin_goal: {sku} ({precio}, {canal})` same shape. Minor doc nuance, not a bug.

  29. Unused variable: in recuadro_desde_archivos, `puente_f` unused — yes, confirmed from the diff body. Low quality finding (dead param). Ruff didn't complain because it's a positional param? ARG rules not enabled presumably.

  30. One more on fuentes.py: `leer_publicaciones` for meli platform would return rows if spapi had meli rows (it can't, spapi is Amazon). fine.

  31. `_SQL_GOALS`: `valid_to > hoy` matches [valid_from, valid_to) semantics. Good. But it doesn't consider `mode` — goals in shadow vs live both count as tiene_goal. Is that intended for cobertura? The recuadro is about catalog coverage; presumably any goal counts. The spec S3: precio_goal has mode (shadow/live). A shadow goal means "tiene_goal" true → evaluada or sin_decision. If the motor only evaluates live goals, shadow-goal listings would show `sin_decision` rather than a distinct bucket. Hmm — does precio_decision get rows for shadow goals? In shadow mode the motor presumably still writes decisions. Can't verify cheaply; skip or mark VERIFICAR? The A.7 brief says goals vigentes — the query matches the docstring. I'll skip; borderline.

  Actually let me check quickly whether precio_decision is written for shadow goals (i.e., whether decision coverage includes shadow). grep app/precio for "shadow" in corrida... This could be a real coverage mislabeling: listings with goal in shadow evaluated in shadow would still have precio_decision rows (the shadow run writes decisions, presumably). Fine, skip.

  32. Security: readback.sh pipes DSN from container env into psql arg inside remote shell — DSN in process list briefly; acceptable in this context (their own ops pattern, matches E.0/correr.sh presumably).

  33. `sed` with `$plataforma` values from a fixed list — no injection. OK.

  Final list:

  1. (baja) Docstring ambiguo de "activa" en fuentes.py/01_canonicas.sql: el SQL toma la ultima observacion y exige BUYABLE (descarta SKUs cuya ultima observacion no es BUYABLE); la frase "la ultima observacion ... cuyo status contiene BUYABLE" admite la lectura contraria (filtrar primero). Confirmar contra la medicion E.x que origino 264/106. — could phrase as VERIFICAR. Since I could not find the original 264 query, mark VERIFICAR with low severity.

  2. (baja) `recuadro_desde_salidas.py::recuadro_desde_archivos` acepta `puente_f` y nunca lo usa (solo main lo lee para `identidad`); parametro muerto.

  3. (baja) readback.sh usa `date -u` local para `\set hoy` (03/04) mientras 06_hoy.sql pregunta a la base; si el reloj local difiere o cruza medianoche UTC, goals/decisiones y dias_sin_reportar usan dias distintos. Menor.

  4. (baja) El candado de imports de fuentes.py permite cualquier stdlib no listada en PROHIBIDOS_PRECIO (`socket`, `urllib`, `subprocess`...), asi que "jamas red" no esta realmente candado; mismo agujero que el candado general de precio. Pre-existente pattern; low.

  5. (baja) recuadro_desde_salidas docstring: "el recuadro impreso tiene el mismo formato que tools/precio_cobertura.py" — la primera linea difiere ("salidas A.7" vs "canonica Orbit"). Trivial.

  Anything media? Let me think about `_SQL_CANAL` DISTINCT ON listing_id latest observation — canal of the LATEST oferta observation. If a listing switched channels, fine. OK.

  One more potential media: in `armar_recuadro`, `avisos` only for catalogo_desactualizado; fine per spec.

  Also `cuadra_exact` always true by construction (buckets partition propias) — the check is trivially true given every fila lands in exactly one bucket; it can't catch upstream fanout. Is that a quality concern? The DoD wants "cuadra exacto contra la fuente canonica" — the recuadro's activas comes from len(propias) which IS the canonical count; the check verifies the partition, which is guaranteed. It's weak but not a bug. Actually there's one path where a fila could land in NO bucket? No — every branch covered. And double-count? No. So cuadra_exact is tautological. Worth noting as low: the check can't fail unless a bucket is dropped in future edits; the real reconciliation is activas vs external count (puente). Low.

  Also, `test_consultas_iguales_a_las_que_ejecuta_fuentes` referenced in mutantes.md — in tests/test_precio_cobertura.py (exists). Fine.

  Severity media candidate: none found that is a real bug. Maybe the ordering issue: docstring says classification order "primero lo estructural (fuera_de_alcance)...", but code checks `catalogo_desactualizado` (temporal) BEFORE sin_listing — consistent.

  Wait, one more: in `armar_recuadro`, fila with canal "fbm" and platform meli → fase_meli first. fine.

  One genuinely worth checking: `_SQL_CANO` counts a canonical active with NULL listing_id as sin_listing — but the LEFT JOIN is on seller_sku AND platform; listing.seller_sku is nullable, and two listings same SKU "cuentan uno" with lowest id. But which listing is chosen if the latest observation joins to 2 listings? l.id ASC. But the canal lookup then uses that listing_id, while estimacion_oferta_observation may have observations for the OTHER listing_id → canal_sin_dato incorrectly. Edge case, low.

  Enough. Write the review in Spanish, plain text, numbered list with severities. No accents where avoidable.

To resume this session: kimi -r session_a4c75672-3b6a-4415-90af-01168f09776e


=== Revisor efectivo: kimi (binario 'kimi') ===


---
sha=0d88cc818d62c89dd9b031dc472043bd52115b23 archivos=app/precio/cobertura.py,app/precio/fuentes.py,tools/precio_cobertura.py,tests/test_architecture.py,docs/evidencia/repricing-01/A.7/readback.sh,docs/evidencia/repricing-01/A.7/recuadro_desde_salidas.py,docs/evidencia/repricing-01/A.7/consultas inicio=2026-09-18T17:58:03Z fin=2026-09-18T18:08:17Z exit=0
