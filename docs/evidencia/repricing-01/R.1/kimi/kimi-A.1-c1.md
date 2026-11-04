=== quality-kit cross-review.ps1 ===
Repo: /Users/dn/dev/_wt/f11-kimi-r1
CLI: kimi
Alcance: last-commit
Archivos (pathspec de la tarea): app/precio/goals_write.py, tools/precio_goal.py, tests/test_architecture.py
Tamano del diff: 36250 caracteres
==> Invocando kimi de forma no interactiva (tope: 900 s)...

=== Respuesta de kimi (codigo de salida: 0) ===
• Voy a leer el diff y verificar en el repo lo que haga falta.

• Ahora verifico en el repo las piezas externas al diff.

• Ahora verifico `app/db.py` y corro los tests focalizados.

• Unas verificaciones finales: GRANTs de `app_admin` y columna `seller_sku`.

• Revision completa. Verifique contra el repo: el DDL de `precio_goal` (migrations/0039_precio.sql:60-92: CHECK banda 0.10-0.60, `precio_goal_live_exige_go`, `precio_goal_unico_por_fecha`, indice parcial `un_vigente`, EXCLUDE `sin_solape`, trigger que solo admite cerrar `valid_to`), los GRANTs de `app_admin` (SELECT sobre listing/config_version/estimacion_* en 0001/0028, INSERT + UPDATE(valid_to) + USAGE de secuencia en 0039:1136-1139), las firmas de `derivar_ref_fijo`/`precio_estrella`/`techo_centavo` (app/precio/objetivo.py), la forma del JSON de `componentes` y `fee_details` (app/estimacion_repository.py:186-215) y las monedas (moneda_venta == price_currency siempre, app/estimacion_repository.py:479). Los tests focalizados pasan: `tests/test_architecture.py` + `tests/test_precio_goals.py` → 114 passed.

  Hallazgos (todos menores; no hay altos ni medios):

  1. (baja) `tools/precio_goal.py::_referencia` reconstruye `tasa_isr` y `divisor_iva` dividiendo componentes ya cuantizados a 4 decimales (`isr/ingreso`, `precio_bruto/ingreso`), asi que la `P*` impresa y el chequeo del 25% pueden diferir en centavos de lo que el motor calcularia con las tasas exactas de la politica. Es solo dry-run/display y esta declarado ("se reconstruyen de los componentes congelados"), pero el operador puede ver una P* ligeramente distinta de la que luego produce el motor.

  2. (baja) En el mismo `_referencia`, el `except` atrapa `TypeError`, `ValueError`, `KeyError`, `ArithmeticError` ademas de los errores de dominio: un bug de programacion en `precio_estrella`/`derivar_ref_fijo` se disfrazaria de `referencia_sin_derivar` y en `shadow` la siembra seguiria adelante con solo un aviso, sin senal de que el codigo esta roto.

  3. (baja) TOCTOU en el cierre: la huella incluye el `goal_id` visto en el dry-run (`cerrar:listing:platform:goal_id`), pero `_go_con_ceremonia` llama a `cerrar_goal`, que re-resuelve el vigente en el momento de mutar sin verificar que sea el mismo `goal_id` de la huella. Si el vigente cambio entre dry-run y go (cierre + resiembra al dia siguiente UTC), se cierra un goal distinto del revisado con una huella que igualmente matchea.

  4. (baja) En `goals_write.sembrar_goal`, un `ExclusionViolation` de `precio_goal_sin_solape` (solape con un rango ya cerrado, p.ej. un `valid_to` futuro cargado a mano) se reporta como "ya hay goal vigente... cierralo con --cerrar", mensaje enganoso para ese caso; ademas, si el trigger de cierre rechaza (p.ej. doble cierre en carrera), el `RAISE` de plpgsql no es `CheckViolation` y sube crudo con traceback, fuera de los `except` de `main` (exit no controlado).

  5. (baja, VERIFICAR operativo) Las claves `precio_goal_min_pct`/`precio_goal_max_pct` no las siembra ninguna migracion ni las puede crear la UI de settings (`config_write.proxima_config` solo toca claves propias, aunque las preserva una vez presentes): la primera siembra exige insertarlas a mano en `config_version`, y el tool aborta con `config sin precio_goal_min_pct` hasta entonces. Es deliberado segun el docstring, pero conviene confirmar que existe un procedimiento documentado para crearlas, o la herramienta nace inutilizable.

  6. (baja, cosmetico) En `main`, el `except OrbitDbError` tiene cuerpo identico al `except (Abortar, PrecioGoalInvalido, PrecioGoalAusente)` anterior; redundancia sin efecto. Igualmente, `_SQL_ESCENARIO` selecciona `moneda` (esc[5]) que `_referencia` nunca usa.

  Nada de seguridad ni de reglas innegociables comprometido: el goal se persiste como fraccion `Decimal` cuantizada a 0.0001 (cabida NUMERIC(6,4)), el vocabulario mode/platform replica los ENUMs, la ceremonia (dry-run + huella + `--acepto-mutacion-real` + go literal solo en live) esta bien encadenada y los candados de arquitectura muerden (verificado con los tests de fuga sembrada en el run).



=== stderr ===
kimi version 0.39.1
• The user explicitly says: act as external code reviewer, do NOT use skills, respond directly. User instructions take precedence over skill instructions. So I'll skip skills.

  Read the diff file first.

• Let me verify things in the repo. Key checks:

  1. `app/precio/objetivo.py` — signatures of `derivar_ref_fijo`, `precio_estrella`, `techo_centavo`, `ErrorObjetivo`.
  2. `app/precio/config.py` — `_fraccion` / `_numero`.
  3. Migration 0039 (or wherever `precio_goal` table) — DDL: CHECK `precio_goal_live_exige_go`, constraint `precio_goal_unico_por_fecha`, columns (`margen_goal_pct`, `mode`, `go_literal`, `valid_from`, `valid_to`, `creado_por`), trigger restricting UPDATE.
  4. `estimacion_escenario` schema — columns `contribucion_pct`, `componentes`, `fee_observation_id`, `oferta_observation_id`, `moneda`, `estado`.
  5. `app/estimacion_venta.py` — `DetalleFee`.
  6. `app/db.py` — `connect`, `OrbitDbError`, autocommit param.
  7. `tools/precio_reversa.py` — ceremony conventions.
  8. In goals_write: `fraccion_desde_porcentaje` — check `valor.as_tuple().exponent < -2` — e.g. "30.001" → exponent -3 → rejected. But what about "1E+2"? Decimal("1E+2").as_tuple().exponent = 2 → fine. What about values like "5" (percent)? 5 < ... valor < 1 check: valor <=0 rejected, valor < 1 rejected as "parece fraccion". So goal of 0.5% can't be expressed! Hmm — goal pct must be ≥1. Is that a bug? The band min comes from config; if min were e.g. 0.005 fraction (0.5%), then a goal of 0.50% would be rejected as "parece fraccion". Check the config values in migration 0039 to see min. Also note the validation order: `valor <= 0` first, then `valor < 1` — fine.

  Also `valor.as_tuple().exponent < -2` — but what about "30.005"? rejected. Good. But "0.30" rejected. OK.

  Potential bug: in `sembrar_goal`, when UniqueViolation/ExclusionViolation for a constraint other than `precio_goal_unico_por_fecha`, it claims "ya hay goal vigente" — fine.

  But note: the `except` clause catches UniqueViolation raised from INSERT; however the message check for constraint `precio_goal_unico_por_fecha` — verify the actual constraint name in the migration.

  Also: `cerrar_goal` UPDATE sets `valid_to = today UTC`. If the goal was created today, valid_from == valid_to — is that allowed? Check exclusion constraint / trigger. Also `precio_goal_unico_por_fecha` probably is a unique on (listing_id, platform, valid_from) — so closing and re-seeding same day blocked. That matches the error message.

  Another: in `_referencia`, `esc[2]` componentes — is it a list of dicts with `nombre` and `importe_normalizado`? Verify against `estimacion_escenario` writer.

  `tasa_isr = isr_importe / ingreso` and `divisor_iva = precio_bruto / ingreso` — verify semantics against objetivo.precio_estrella signature.

  Also `_huella` sorts parts; mode included for siembra. Cerrar huella doesn't include mode — mode is in huella call `_huella(plan, args.mode)`; for cerrar, mode ignored in partes but plan only cerrar rows. Fine.

  Potential issue: `_huella` doesn't include `creado_por` or go... fine.

  Potential bug in `_go_con_ceremonia`: after plan built in dry-run, then mutation path re-runs `sembrar_goal` which re-validates against current config — fine.

  Autocommit concern: `connect(_dsn_admin(), autocommit=True)` — but `cerrar_goal` and `sembrar_goal` each execute; in autocommit each statement commits. Docstring says per-row confirm. OK.

  Potential issue: `test_precio_puro_sin_io` — `EXCEPCIONES_PURAS_PRECIO` defined AFTER `_puros_precio` uses it? In Python, function body resolves at call time, so order is fine. But note the diff: `_puros_precio` references `EXCEPCIONES_PURAS_PRECIO` which is defined below `_puros_precio` but before the test functions that call it — fine at runtime.

  Wait — actually check: `_puros_precio` defined at line ~1057, `EXCEPCIONES_PURAS_PRECIO` defined right after at line ~1077. Fine.

  But: excluding `goals_write.py` from `test_precio_puro_sin_io` — the purity test presumably checks no psycopg/network imports in app/precio. goals_write imports psycopg, so exception needed. The exception is by name only, so future files can't sneak. But also check other purity tests — e.g., maybe there's a test that app/precio has no DB access at all, or "precio puro" test checks for `conn` / `execute`. `_puros_precio` is used by multiple tests possibly. Need to check which tests use `_puros_precio` and whether excluding goals_write from all of them weakens something (e.g., a test that forbids `now()` / reloj — goals_write uses `now()` in SQL, not Python, so AST-based `_usos_reloj` wouldn't catch it anyway).

  Also check whether there's an existing test scanning tools for SQL (like `_escritores_crudos` for ads goals) that precio_goal.py might trip — e.g., a general "tools no SQL crudo" guard. The diff's `_SQL_ESCENARIO` etc. are SELECTs in tools/precio_goal.py; if there's an existing guard forbidding raw SQL in tools, this could fail. Verify.

  Also `fila[0]` from `config_vigente_settings`: `SELECT settings FROM config_version ORDER BY id DESC LIMIT 1` — verify config_version has `settings` column (jsonb → dict). Verify `leer_config` in config.py uses similar.

  Check `Decimal(str(fee[0]))` — fee[0] total_fees is NUMERIC → already Decimal from psycopg; str() fine.

  `_detalle_fee(nodo)` iterates `fee[1]` fee_details — verify jsonb structure keys: `fee_type`, `final_fee`, `tax_amount`, `included_fee_details`. Check estimacion_venta / wherever fee_details is written.

  `derivar_ref_fijo(detalles, Decimal(str(fee[0])), Decimal(str(fee[2])))` — verify signature: (detalles, total_fees, quoted_price_amount)? Check objetivo.py.

  `precio_estrella(costo, fijo, envio, tasa_isr, goal, divisor_iva, divisor_iva != 1, ref)` — verify signature order.

  Also `techo_centavo`.

  Another possible issue: `_SQL_FEE` selects `estado` and checks `fee[3] != "success"` — verify estado values.

  Also check `_componente(componentes, "logistica")` vs names used in escenario writer: "costo_normalizado", "ingreso_normalizado", "logistica", "isr", "precio_bruto". Verify against wherever componentes is written (app/estimacion* or app/precio/escenario?).

  Also in `main`, `except OrbitDbError` duplicates the previous except — both return 2 with same message; minor redundancy, not a bug.

  Also: `args.goal_pct` when `--cerrar` with csv: fine.

  Bug candidate: in `_entradas_desde_args`, when `--sku` given, returns [] and resolved later; but the check `if not args.cerrar and args.csv is None and args.goal_pct is None` happens before resolution — fine.

  Bug candidate: `_huella` includes `mode` only in siembra parts; but a plan mixing? cerrar plan is all cerrar. Fine.

  Huella weakness: two different plans could collide? sorted parts joined — fine.

  Important: `sembrar_goal` catches `psycopg.errors.UniqueViolation` — need `import psycopg.errors` — it's imported. Fine.

  Potential issue: in `sembrar_goal`, the pre-check SELECT for vigente and then INSERT — race condition possible but with unique/exclusion constraints handled. OK.

  Potential issue: `cerrar_goal` sets valid_to = today UTC even if valid_from == today → valid_to == valid_from. Does the CHECK require valid_to > valid_from? If CHECK requires strictly greater, closing same-day would fail with CheckViolation — but `cerrar_goal` doesn't catch CheckViolation, so it would propagate as psycopg error, not caught in main (only OrbitDbError). Actually main catches Abortar, PrecioGoalInvalido, PrecioGoalAusente, OrbitDbError. A raw psycopg CheckViolation would crash with traceback → exit 1 not graceful. But that's an edge; and message in sembrar says "el siguiente entra al dia siguiente UTC" implying same-day close+reseed is expected flow, so closing same day should be allowed (valid_to >= valid_from). Verify DDL.

  Let me read the relevant files. Start with migration for precio_goal (search migrations for precio_goal), app/precio/objetivo.py, app/precio/config.py, and where estimacion_escenario/componentes is written, plus tests/test_architecture.py around _puros_precio usage, and check existing guards for tools SQL.

• Signatures match. Now check migration 0039 for precio_goal DDL (constraints, CHECK live_exige_go, unico_por_fecha, valid_from/valid_to semantics, trigger, GRANTs), and config_version settings with precio_goal_min_pct/max_pct values.CREATE TABLE precio_goal (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    listing_id      BIGINT NOT NULL,
    platform        platform NOT NULL,
    margen_goal_pct NUMERIC(6, 4) NOT NULL,
    mode            precio_mode NOT NULL,
    valid_from      DATE NOT NULL,
    valid_to        DATE,
    creado_por      TEXT NOT NULL,
    go_literal      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT precio_goal_listing_fk FOREIGN KEY (listing_id, platform)
        REFERENCES listing (id, platform),
    CONSTRAINT precio_goal_banda
        CHECK (margen_goal_pct BETWEEN 0.10 AND 0.60),
    CONSTRAINT precio_goal_live_exige_go
        CHECK ((mode = 'live') = (go_literal IS NOT NULL AND btrim(go_literal) <> '')),
    -- La vigencia es [valid_from, valid_to): valid_to = valid_from es un
    -- intervalo vacío —el goal queda ANULADO, nunca estuvo vigente— y así se
    -- apaga un error de dedo el mismo día (si no, la corrida de las 13:10 UTC
    -- actuaría sobre él). Aquí 0039 se aparta del patrón de `sku_cost`
    -- (`valid_to > valid_from`) a propósito: un costo rige pasado; un goal
    -- con error debe poder no regir nunca (regla 7: la reversa existe antes).
    CONSTRAINT precio_goal_vigencia_coherente
        CHECK (valid_to IS NULL OR valid_to >= valid_from),
    CONSTRAINT precio_goal_unico_por_fecha UNIQUE (listing_id, platform, valid_from)
);

-- Un solo vigente por (listing, platform): el índice parcial es el que
-- rechaza el segundo vigente (el UNIQUE por fecha admite dos filas con
-- `valid_from` distinto y `valid_to` NULL).
CREATE UNIQUE INDEX precio_goal_un_vigente
    ON precio_goal (listing_id, platform)
    WHERE valid_to IS NULL;

-- Pero ni el parcial (solo filas abiertas) ni el UNIQUE (solo `valid_from`)
-- impiden dos goals vigentes el mismo día con una vigencia ya cerrada
-- (A cerrada `[09-01, 09-20)` + B desde `09-10`: del 10 al 19 hay dos goals y
-- «el goal vigente del día» devuelve dos filas). El invariante lo garantiza
-- la base con EXCLUDE, igual que `sku_cost` en 0001 (ronda 2 de revisión):
-- la vigencia es `[valid_from, valid_to)` semiabierta y un rango vacío
-- (`valid_to = valid_from`, goal anulado) no se solapa con nada, así que la
-- decisión 8 de r1 sigue pasando. El parcial y el UNIQUE se quedan (S3).
ALTER TABLE precio_goal ADD CONSTRAINT precio_goal_sin_solape
    EXCLUDE USING gist (
        listing_id WITH =,
        platform WITH =,
        daterange(valid_from, valid_to, '[)') WITH &&
    );

COMMENT ON TABLE precio_goal IS
  'REPRICING 01 A.0 (S3): goal de margen por publicación, vigencia con el '
  'patrón de `sku_cost` (solo se cierra `valid_to`, una vez; corregir el goal '
  'es fila nueva) SALVO la coherencia: la vigencia es [valid_from, valid_to) '
  'y `valid_to = valid_from` es intervalo vacío —el goal queda ANULADO, nunca '
  'estuvo vigente— para apagar un error de dedo el mismo día (un costo rige '
  'pasado; un goal con error debe poder no regir nunca). Y como en `sku_cost`, '
  'el EXCLUDE `precio_goal_sin_solape` garantiza UN goal por día: ni el índice '
  'parcial (solo abiertas) ni el UNIQUE (solo `valid_from`) impiden dos goals '
  'vigentes el mismo día con una vigencia ya cerrada (ronda 2 de revisión). '
  'Sin fila vigente no hay decisión: sin defaults por plataforma ni herencia '
  '(decisión 3, regla 3). `platform` admite amazon_mx, amazon_us y meli; el '
  'motor solo evalúa las combinaciones habilitadas por la fase vigente y '
  'declara el resto `fuera_de_alcance(fase)`.';
COMMENT ON COLUMN precio_goal.margen_goal_pct IS
  'Fracción sobre el ingreso sin impuesto (I), igual que la contribución '
  'estimada. Banda 0.10–0.60 en CHECK literal (claves precio_goal_min_pct / '
  'precio_goal_max_pct, replicada en app/precio/goals_write.py, A.1).';
COMMENT ON COLUMN precio_goal.go_literal IS
  'Go literal del dueño para subir a `live` (ceremonia de los goals de Ads): '
  'el CHECK precio_goal_live_exige_go lo impone también contra `psql`.';

CREATE FUNCTION precio_goal_solo_cierra_vigencia() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'precio_goal es histórico: una vigencia publicada no se BORRA. Se '
            'CIERRA (UPDATE valid_to) y el goal nuevo entra como fila nueva.'
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.valid_to IS DISTINCT FROM OLD.valid_to
       AND (OLD.valid_to IS NOT NULL OR NEW.valid_to IS NULL) THEN
        RAISE EXCEPTION
            'precio_goal %: valid_to sólo puede pasar de NULL a una fecha, una vez. '
            'Mover el corte de una vigencia cerrada o reabrirla reescribe el '
            'período en que ese goal aplicó.', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    -- `created_at` también es inmutable (r3-5, como `started_at` en
    -- `apply_attempt_solo_sella_resultado`): ni con GRANT amplio se reescribe.
    IF ROW(NEW.id, NEW.listing_id, NEW.platform, NEW.margen_goal_pct,
           NEW.mode, NEW.valid_from, NEW.creado_por, NEW.go_literal,
           NEW.created_at)
       IS DISTINCT FROM
       ROW(OLD.id, OLD.listing_id, OLD.platform, OLD.margen_goal_pct,
           OLD.mode, OLD.valid_from, OLD.creado_por, OLD.go_literal,
           OLD.created_at) THEN
        RAISE EXCEPTION
            'precio_goal %: de una fila publicada sólo se puede cerrar la vigencia '
            '(valid_to). Corregir el goal es INSERTAR una fila nueva con '
            'vigencia nueva.', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER precio_goal_solo_cierra_vigencia
    BEFORE UPDATE OR DELETE ON precio_goal
    FOR EACH ROW EXECUTE FUNCTION precio_goal_solo_cierra_vigencia();

-- Misma razón que en las append-only: los triggers de fila no se disparan
-- con TRUNCATE.
CREATE TRIGGER precio_goal_append_only_truncate
    BEFORE TRUNCATE ON precio_goal
    FOR EACH STATEMENT EXECUTE FUNCTION prohibir_mutacion();

COMMENT ON FUNCTION precio_goal_solo_cierra_vigencia IS
  'REPRICING 01 A.0 (S3, patrón sku_cost_solo_cierra_vigencia): la única '
  'mutación legítima es cerrar la vigencia —valid_to y nada más, transición '
  'NULL -> fecha una vez—; el DELETE jamás, sin depender de qué GRANT tenga '
  'el rol. El segundo vigente lo rechaza el índice parcial '
  'precio_goal_un_vigente, no este trigger.';

-- (d) Decisión diaria por publicación (S5): exactamente una fila por
-- (listing, platform, día). `decision_date` lo fija el trigger al día UTC de
-- la base —el valor que mande el cliente se ignora— porque un DATE validado
-- contra CURRENT_DATE dependería de la TimeZone de la sesión (misma defensa
-- que `apply_quota_fila_desde_config`, r2 codex). Dinero con moneda
-- SIEMPRE (regla 4): cada importe lleva su columna `currency NOT NULL`;
-- el importe es NULL cuando el día es `no_evaluado` (regla 3: nunca un
-- default ni un cero inventado), la moneda no. `canal` reutiliza
-- `estimacion_canal` y admite NULL porque un `no_evaluado(canal_sin_dato)`
-- no tiene canal (el valor de MeLi lo agrega la migración de su fase).
CREATE TABLE precio_decision (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    listing_id          BIGINT NOT NULL,
    platform            platform NOT NULL,
    decision_date       DATE NOT NULL,
    resultado           TEXT NOT NULL,
    motivo              TEXT,
    product_id          BIGINT REFERENCES product (id),
    canal               estimacion_canal,
    m_actual            NUMERIC(8, 4),
    goal                NUMERIC(6, 4),
    p_actual            money_amount,
    p_actual_currency   currency NOT NULL,
    p_objetivo          money_amount,
    p_objetivo_currency currency NOT NULL,
    p_aplicado          money_amount,
    p_aplicado_currency currency NOT NULL,
    i_valor             money_amount,
    i_currency          currency NOT NULL,
    c_valor             money_amount,
    c_currency          currency NOT NULL,
    f_valor             money_amount,
    f_currency          currency NOT NULL,
    l_valor             money_amount,
DDL verified: constraints match (precio_goal_banda 0.10–0.60, live_exige_go, unico_por_fecha, un_vigente partial index, sin_solape EXCLUDE, trigger solo cierra valid_to). Note the exclusion constraint `precio_goal_sin_solape` — ExclusionViolation could be raised when sembrar a new goal... but the partial unique index prevents a second vigente anyway. When would ExclusionViolation occur in sembrar? If previous closed goal overlaps with new valid_from — e.g. closed with valid_to = future date? cerrar sets valid_to = today. New goal valid_from = today; old range [from, today) doesn't overlap [today, ...). OK.

  One thing: `cerrar_goal` sets valid_to = today UTC. If goal was created today (valid_from = today), valid_to = valid_from → allowed by CHECK (>=). OK.

  But note: in cerrar, trigger allows NULL→fecha once. Fine.

  Now check GRANTs — "Solo app_admin (GRANTs de la 0039)". Let me check GRANT section in 0039 and config_version table / settings keys precio_goal_min_pct. Also `app/precio/config.py` `_fraccion` and whether `leer_config` knows those keys; and `estimacion_escenario` schema + componentes structure; DetalleFee in app/estimacion_venta.py; app/db connect signature; tools/precio_reversa ceremony; and existing architecture tests using _puros_precio and whether any guard scans tools for raw SQL that would now trip on the SELECTs in tools/precio_goal.py. Also check tests/test_precio_goals.py exists (mentioned).

  Let me grep for the GRANTs and precio_goal_min_pct in 0039.59:-- `goals_write` (claves `precio_goal_min_pct`/`precio_goal_max_pct`).
126:  'estimada. Banda 0.10–0.60 en CHECK literal (claves precio_goal_min_pct / '
127:  'precio_goal_max_pct, replicada en app/precio/goals_write.py, A.1).';
152:    -- `apply_attempt_solo_sella_resultado`): ni con GRANT amplio se reescribe.
183:  'NULL -> fecha una vez—; el DELETE jamás, sin depender de qué GRANT tenga '
792:    -- `apply_attempt_solo_sella_resultado`): ni con GRANT amplio se reescribe.
1118:-- (i) GRANTs por columna (S5 y hecho 2 del brief): el motor (`app_decide`)
1125:GRANT SELECT ON precio_goal, precio_decision, precio_cotizacion,
1129:GRANT INSERT ON precio_decision, precio_cotizacion, precio_envio_muestra
1131:GRANT INSERT ON precio_cambio TO app_decide;
1132:GRANT UPDATE (estado, enviado_at, ack, readback_precio, readback_precio_currency,
1136:GRANT INSERT ON precio_goal TO app_admin;
1137:GRANT UPDATE (valid_to) ON precio_goal TO app_admin;
1139:GRANT USAGE ON SEQUENCE precio_goal_id_seq TO app_admin;
1140:GRANT USAGE ON SEQUENCE precio_decision_id_seq, precio_cotizacion_id_seq,
./migrations/0039_precio.sql:59:-- `goals_write` (claves `precio_goal_min_pct`/`precio_goal_max_pct`).
./migrations/0039_precio.sql:126:  'estimada. Banda 0.10–0.60 en CHECK literal (claves precio_goal_min_pct / '
./migrations/0039_precio.sql:127:  'precio_goal_max_pct, replicada en app/precio/goals_write.py, A.1).';
./app/precio/goals_write.py:15:(`precio_goal_min_pct`/`precio_goal_max_pct`) con la FORMA de `_fraccion`
./app/precio/goals_write.py:18:aborta con `config sin precio_goal_min_pct`, nunca con un default. La
./app/precio/goals_write.py:113:    minimo = _fraccion(settings, "precio_goal_min_pct", minima="0", maxima="1")
./app/precio/goals_write.py:114:    maximo = _fraccion(settings, "precio_goal_max_pct", minima="0", maxima="1")
./app/precio/goals_write.py:126:        raise PrecioGoalInvalido("config sin precio_goal_min_pct: no hay config_version vigente")
Interesting: `precio_goal_min_pct`/`precio_goal_max_pct` don't appear as actual seed values in any migration. Where do the config_version settings come from? Is there a seed inserting config_version with those keys? Check 0039's DO block seed and other migrations / app code that writes config_version.migrations/0001_initial.sql
migrations/0002_apply.sql
migrations/0039_precio.sql
migrations/0039_precio.sql:242:    config_version_id   BIGINT REFERENCES config_version (id),
migrations/0039_precio.sql:296:CREATE INDEX precio_decision_por_config ON precio_decision (config_version_id);
migrations/0039_precio.sql:1171:          FROM config_version
app/config_write.py:164:_SQL_INSERTA = "INSERT INTO config_version (label, settings) VALUES (%s, %s) RETURNING id"
tools/smoke_apply.py:27:      INSERT INTO config_version (label, settings)
So config_version.settings is written via UI (or seeds) — `precio_goal_min_pct`/`precio_goal_max_pct` keys: nothing in the repo inserts them. The tool aborts if the keys are absent — that's deliberate ("sin las claves se aborta"), so the tool is unusable until someone seeds those keys manually. That's a declared design; maybe fine, but worth noting? It says "sin las claves se aborta... nunca con un default". It's intentional. But is there any path to ever set these keys? The UI settings editor only touches specific keys and copies everything else, so if seeded manually once, they persist. OK — a VERIFICAR-level note at most: no migration seeds those keys, so first use requires manual config edit; intentional per docstring.

  Now check the purity test context in test_architecture.py — what does test_precio_puro_sin_io check, and does anything else scan tools for SQL. Also check `estimacion_escenario` schema and componentes JSON shape, `DetalleFee`, `app/db.connect`, and fee_observation estado values, plus `listing` columns seller_sku/platform.1056:def _puros_precio(raiz=None):
1080:def test_precio_puro_sin_io():
1083:    modulos = _puros_precio()
1111:    de `test_precio_puro_sin_io` no lo ve (`importlib` sí cae por
1115:    modulos = _puros_precio()
1134:        test_precio_puro_sin_io()
1158:    modulos = _puros_precio()
1177:        test_precio_puro_sin_io()
1186:        test_precio_puro_sin_io()
1223:        test_precio_puro_sin_io()
1080:def test_precio_puro_sin_io():
1081-    """Ningun modulo de `app/precio/` importa I/O en runtime (rglob: un
1082-    subpaquete anidado con IO tambien es fuga)."""
1083-    modulos = _puros_precio()
1084-    assert modulos, "no se encontro el motor de precios: ¿se movio app/precio/?"
1085-    fugas = {
1086-        p.relative_to(PRECIO).as_posix(): v
1087-        for p in modulos
1088-        if (v := _violaciones(_imports_runtime(p), PROHIBIDOS_PRECIO))
1089-    }
1090-    assert not fugas, f"app/precio debe ser PURO; imports de IO encontrados: {fugas}"
1091-
1092-
1093-def _usos_import_dinamico(arbol: ast.AST) -> list[str]:
1094-    """`__import__` por AST: `Name` con `id == "__import__"` o `Attribute`
1095-    con `attr == "__import__"` (`__import__ ("httpx")` con espacio y
1096-    `builtins.__import__("httpx")` incluidos; r6-C3: el barrido de texto
1097-    `"__import__("` no ve el espacio)."""
1098-    hallados: list[str] = []
1099-    for nodo in ast.walk(arbol):
1100-        if isinstance(nodo, ast.Name) and nodo.id == "__import__":
1101-            hallados.append("__import__")
1102-        elif isinstance(nodo, ast.Attribute) and nodo.attr == "__import__":
1103-            hallados.append(f".{nodo.attr}")
1104-    return hallados
1105-
1106-
1107-def test_precio_sin_import_dinamico():
1108-    """Ni `import importlib` ni `__import__("...")` en `app/precio/*`.
1109-
1110-    R5-J5: el import dinámico no produce nodos de import y el candado
1111-    de `test_precio_puro_sin_io` no lo ve (`importlib` sí cae por
1112-    `PROHIBIDOS_PRECIO`; `__import__` por este detector AST).
1113-    Precedente: el candado de `snapshot_listas` en este mismo archivo.
1114-    """
1115-    modulos = _puros_precio()
1116-    assert modulos, "no se encontro el motor de precios: ¿se movio app/precio/?"
1117-    fugas = {
1118-        p.relative_to(PRECIO).as_posix(): v
1119-        for p in modulos
1120-        if (v := _usos_import_dinamico(ast.parse(p.read_text(encoding="utf-8"))))
3:Entrada congelada + politica versionada -> contribucion, porcentaje, componentes,
61:    importe_normalizado: Decimal | None = None
71:    precio_bruto: Decimal | None
95:    componentes: tuple[ComponenteEconomico, ...]
97:    ingreso_normalizado: Decimal | None = None
98:    logistica: Decimal | None = None
99:    isr: Decimal | None = None
224:        isr_tasa = _validar_tasa_no_negativa(
225:            _decimal_setting(settings, "isr_tasa"), campo="isr_tasa"
227:        logistica = _validar_tasa_no_negativa(
228:            _decimal_setting(settings, "logistica"), campo="logistica"
234:        validar_dinero(isr_tasa, campo="isr_tasa", positivo=False, permitir_cero=True)
235:        validar_dinero(logistica, campo="logistica", positivo=False, permitir_cero=True)
239:        if logistica == Decimal("0"):
240:            sem = settings.get("logistica_semantica")
252:    componentes: list[ComponenteEconomico],
270:    componentes.append(
279:        componentes.append(
281:                nombre="costo_normalizado",
284:                importe_normalizado=costo_orig,
310:    componentes.append(
320:    componentes.append(
322:            nombre="costo_normalizado",
325:            importe_normalizado=costo_norm,
344:    componentes: list[ComponenteEconomico] = []
358:            componentes=tuple(componentes),
369:            componentes=tuple(componentes),
380:            componentes=tuple(componentes),
388:    if entrada.precio_bruto is None or entrada.precio_moneda is None:
392:            precio = validar_dinero(entrada.precio_bruto, campo="precio", positivo=True)
397:            componentes.append(
399:                    nombre="precio_bruto",
402:                    importe_normalizado=precio,
414:            componentes.append(
416:                    nombre="ingreso_normalizado",
417:                    importe_normalizado=ingreso,
451:            componentes.append(
454:                    importe_normalizado=fee,
459:                componentes.append(
462:                        importe_normalizado=det.final_fee,
49:class DetalleFee:
50-    fee_type: str
51-    final_fee: Decimal
52-    tax_amount: Decimal | None = None
53-    included_fee_details: tuple[DetalleFee, ...] = ()
54-
55-
56-@dataclass(frozen=True)
57-class ComponenteEconomico:
58-    nombre: str
59-    importe_original: Decimal | None = None
60-    moneda_original: str | None = None
61-    importe_normalizado: Decimal | None = None
Now check how `componentes` JSON is serialized into estimacion_escenario (is it a list of dicts with "nombre" and "importe_normalizado"?). Find writer of estimacion_escenario. Also check the names "logistica", "isr" in componentes. And check fee_details JSONB keys (fee_type, final_fee, tax_amount, included_fee_details). And estimacion_fee_observation columns (total_fees, fee_details, quoted_price_amount, estado) and estado values ("success"?). And estimacion_oferta_observation columns (price_amount, price_currency). And estimacion_escenario columns (contribucion_pct, componentes, fee_observation_id, oferta_observation_id, moneda, estado, observed_at) with estado='disponible'.app/estimacion_reader.py
app/estimacion_repository.py
app/estimacion_repository.py:624:        "INSERT INTO estimacion_escenario"
app/estimacion_repository.py-625-        " (listing_id, platform, seller_sku, asin, canal, valoracion_date, observed_at,"
app/estimacion_repository.py-626-        " politica_version_id, formula_version, oferta_observation_id, fee_observation_id,"
app/estimacion_repository.py-627-        " sku_cost_id, costo_validation_run_id, costo_validated_at,"
app/estimacion_repository.py-628-        " fx_rate_date, fx_base, fx_quote, fx_rate, fx_source,"
app/estimacion_repository.py-629-        " moneda, contribucion, contribucion_pct, estado, motivos, componentes,"
app/estimacion_repository.py-630-        " exclusiones, canonical_input, context_fingerprint, source_event_id)"
app/estimacion_repository.py-631-        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
app/estimacion_repository.py-632-        " %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
app/estimacion_repository.py-633-        " ON CONFLICT (source_event_id) DO NOTHING"
app/estimacion_repository.py-634-        " RETURNING id, observed_at",
app/estimacion_repository.py-635-        (
app/estimacion_repository.py-636-            escenario.listing_id,
app/estimacion_repository.py-637-            escenario.platform,
app/estimacion_repository.py-638-            escenario.seller_sku,
app/estimacion_repository.py-639-            escenario.asin,
app/estimacion_repository.py-640-            escenario.canal,
app/estimacion_repository.py-641-            escenario.valoracion_date,
app/estimacion_repository.py-642-            escenario.observed_at,
app/estimacion_repository.py-643-            escenario.politica_version_id,
app/estimacion_repository.py-644-            escenario.formula_version,
estimacion tables not in 0001? Check which migration defines them, the estado vocabulary, and componentes serialization (dataclass → json). Also check `componentes` JSON keys: serialized via asdict? Let's find where componentes json is built.migrations/0028_estimacion_venta.sql
84:    componentes: list[dict[str, Any]]
186:def _componentes_a_json(resultado: ResultadoCalculo) -> list[dict[str, Any]]:
188:    for c in resultado.componentes:
521:            componentes=(),
609:        componentes=_componentes_a_json(resultado),
629:        " moneda, contribucion, contribucion_pct, estado, motivos, componentes,"
660:            Json(escenario.componentes),
768:        componentes=(),
796:        componentes=_componentes_a_json(resultado_stub),
app/estimacion_repository.py:659:            Json(motivos_json),
app/estimacion_repository.py:660:            Json(escenario.componentes),
app/estimacion_repository.py:661:            Json(list(escenario.exclusiones)),
app/estimacion_repository.py:662:            Json(escenario.canonical_input),
        return ()
    if not isinstance(fee_details, list):
        raise ValueError("fee_details no es lista")
    return tuple(_parse_detalle_fee(raw) for raw in fee_details)


def _componentes_a_json(resultado: ResultadoCalculo) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for c in resultado.componentes:
        item: dict[str, Any] = {"nombre": c.nombre, "pertenece_a_total": c.pertenece_a_total}
        if c.importe_original is not None:
            item["importe_original"] = str(c.importe_original)
        if c.moneda_original is not None:
            item["moneda_original"] = c.moneda_original
        if c.importe_normalizado is not None:
            item["importe_normalizado"] = str(c.importe_normalizado)
        if c.moneda_normalizada is not None:
            item["moneda_normalizada"] = c.moneda_normalizada
        if c.tasa is not None:
            item["tasa"] = str(c.tasa)
        if c.fecha is not None:
            item["fecha"] = c.fecha.isoformat()
        if c.fuente is not None:
            item["fuente"] = c.fuente
        out.append(item)
    return out


def _detalle_fee_a_json(detalle: DetalleFee) -> dict[str, Any]:
    return {
        "fee_type": detalle.fee_type,
        "final_fee": str(detalle.final_fee),
        "tax_amount": str(detalle.tax_amount) if detalle.tax_amount is not None else None,
        "included_fee_details": [
            _detalle_fee_a_json(incluido) for incluido in detalle.included_fee_details
        ],
15:CREATE TYPE estimacion_estado AS ENUM (
16:    'disponible', 'incompleta', 'desactualizada', 'identidad_ambigua'
19:CREATE TYPE estimacion_fee_estado AS ENUM ('success', 'error');
112:    estado                estimacion_fee_estado NOT NULL,
119:    CONSTRAINT estimacion_fee_success_exige_total CHECK (
120:        (estado = 'success' AND total_fees IS NOT NULL)
121:        OR (estado = 'error' AND total_fees IS NULL)
124:        estado <> 'error' OR error_code IS NOT NULL
148:    'estado=error registra intentos fallidos aparte del ultimo success (A.3). '
177:    estado                estimacion_estado NOT NULL,
185:    CONSTRAINT estimacion_escenario_disponible_exige_total CHECK (
186:        estado <> 'disponible'
199:    CONSTRAINT estimacion_escenario_no_disponible_sin_total CHECK (
200:        estado = 'disponible'
244:    'S5: solo disponible lleva contribucion/moneda; otros estados exigen NULL. '
300:-- Temporalidad fee: success exige fees_estimated_at acotado; oferta no posterior al fee.
321:    IF NEW.estado = 'success' THEN
324:                'estimacion_fee: success exige fees_estimated_at'
374:    v_fee_estado estimacion_fee_estado;
414:        IF NEW.estado = 'disponible' AND NOT (
453:        IF NEW.estado = 'disponible' AND (
458:                'estimacion_escenario: oferta vencida o futura para estado disponible'
464:        SELECT f.observed_at, f.oferta_observation_id, f.estado
465:          INTO v_fee_observed_at, v_fee_oferta_id, v_fee_estado
498:        IF NEW.estado = 'disponible' AND v_fee_estado <> 'success' THEN
500:                'estimacion_escenario: disponible exige fee success'
535:        IF NEW.estado = 'disponible' THEN
538:                    'estimacion_escenario: disponible exige includes_tax=false en sku_cost %',
553:                    'estimacion_escenario: disponible exige validacion de costo'
105:    quoted_price_amount   money_amount NOT NULL,
106:    quoted_price_currency currency NOT NULL,
107:    total_fees            money_amount,
108:    fee_details           JSONB NOT NULL DEFAULT '[]',
118:    CONSTRAINT estimacion_fee_precio_positivo CHECK (quoted_price_amount > 0),
120:        (estado = 'success' AND total_fees IS NOT NULL)
121:        OR (estado = 'error' AND total_fees IS NULL)
129:        quoted_price_amount, quoted_price_currency, context_fingerprint
133:        quoted_price_amount, quoted_price_currency, context_fingerprint
149:    'total_fees NULL en error — regla 3, no cero inventado. FK compuesta '
Good. So componentes JSON uses keys "nombre" and "importe_normalizado" — matches `_componente`. fee_details JSON keys match `_detalle_fee`. Estado values 'disponible'/'success' match.

  Now key semantic question: in `_referencia`, the tool computes:
  - tasa_isr = isr_importe / ingreso
  - divisor_iva = precio_bruto / ingreso

  where ingreso = "ingreso_normalizado" component, isr = "isr" component, precio_bruto = "precio_bruto" component. Are those the semantics `precio_estrella` expects? precio_estrella(costo, fijo, envio, isr_tasa, goal, iva_divisor, incluye_iva, ref): denominador = (1 - isr_tasa - goal)/divisor - ref; I = P/divisor. Here isr_tasa should be fraction of I (ingreso). isr component: check how "isr" componente is computed in estimacion_venta — is isr = tasa * ingreso? And "logistica" component — the tool does `envio = _componente(componentes, "logistica")`. Check what "logistica" componente holds: is it an amount (importe) or a tasa? In estimacion_venta line 227: `logistica = _validar_tasa_no_negativa(_decimal_setting(settings, "logistica"))` — that's a tasa from settings, but the componente named "logistica" might store the amount with a `tasa` field. Need to check.

  Also "isr" componente — check how it's appended and whether importe_normalizado is the amount.

  Also important: componentes may have MULTIPLE entries with nombre "costo_normalizado" (lines 281 and 322 append "costo_normalizado" twice — original and normalized?). `_componente` returns the FIRST match. Check lines 270-330 of estimacion_venta to see the two "costo_normalizado" entries — one with importe_original only maybe, one with importe_normalizado. If the first match has importe_normalizado None, then `Decimal(str(None))` → InvalidOperation... Actually `item.get("nombre") == "costo_normalizado"` then `Decimal(str(item["importe_normalizado"]))` — if key missing → KeyError (caught in _referencia except KeyError → "referencia_sin_derivar"). Hmm, but if first entry lacks importe_normalizado, we'd silently get "referencia_sin_derivar" even though a valid second entry exists. Let me read that region.    moneda_venta: str,
    motivos: list[str],
    componentes: list[ComponenteEconomico],
) -> Decimal | None:
    if entrada.costo_original is None or entrada.costo_moneda is None:
        motivos.append("costo_ausente")
        return None
    try:
        costo_orig = validar_dinero(entrada.costo_original, campo="costo", positivo=True)
    except ValueError:
        motivos.append("costo_invalido")
        return None

    if entrada.costo_includes_tax is not False:
        motivos.append(
            "costo_impuesto_incompatible"
            if entrada.costo_includes_tax is True
            else "costo_base_fiscal_ausente"
        )

    componentes.append(
        ComponenteEconomico(
            nombre="costo_original",
            importe_original=costo_orig,
            moneda_original=entrada.costo_moneda,
        )
    )

    if entrada.costo_moneda == moneda_venta:
        componentes.append(
            ComponenteEconomico(
                nombre="costo_normalizado",
                importe_original=costo_orig,
                moneda_original=entrada.costo_moneda,
                importe_normalizado=costo_orig,
                moneda_normalizada=moneda_venta,
            )
        )
        return costo_orig

    if entrada.fx_rate is None or entrada.fx_base is None or entrada.fx_quote is None:
        if "fx_ausente" not in motivos:
            motivos.append("fx_ausente")
        return None
    if entrada.fx_base != moneda_venta or entrada.fx_quote != entrada.costo_moneda:
        motivos.append("fx_direccion_invalida")
        return None
    tasa = entrada.fx_rate
    if (
        not isinstance(tasa, Decimal)
        or not tasa.is_finite()
        or tasa <= 0
        or tasa >= Decimal(10) ** 10
        or tasa != tasa.quantize(Decimal("0.00000001"))
    ):
        motivos.append("fx_tasa_invalida")
        return None

    costo_norm_exacto = costo_orig / tasa
    costo_norm = _cuantizar_frontera(costo_norm_exacto)
    componentes.append(
        ComponenteEconomico(
            nombre="fx",
            tasa=tasa.quantize(Decimal("0.00000001")),
            fecha=entrada.fx_rate_date,
            fuente=entrada.fx_source,
            moneda_original=entrada.costo_moneda,
            moneda_normalizada=moneda_venta,
        )
    )
    componentes.append(
        ComponenteEconomico(
            nombre="costo_normalizado",
            importe_original=costo_orig,
            moneda_original=entrada.costo_moneda,
            importe_normalizado=costo_norm,
            moneda_normalizada=moneda_venta,
            tasa=tasa.quantize(Decimal("0.00000001")),
            fecha=entrada.fx_rate_date,
            fuente=entrada.fx_source,
        )
    )
    return costo_norm_exacto


def calcular_contribucion(
    entrada: EntradaCalculo,
    politica: PoliticaCalculo,
    *,
    valoracion_date: date,
    universo_esperado: str | None = None,
) -> ResultadoCalculo:
    """Formula S3: I - C - F - L - R; retencion IVA solo conciliacion."""
    motivos: list[str] = list(entrada.motivos_entrada)
    componentes: list[ComponenteEconomico] = []
    exclusiones = EXCLUSIONES_FIJAS

    motivo_pol = _validar_politica(politica, valoracion_date, universo_esperado=universo_esperado)
    if motivo_pol:
        motivos.append(motivo_pol)

    if "identidad_ambigua" in motivos:
        return ResultadoCalculo(
            estado="identidad_ambigua",
            motivos=tuple(dict.fromkeys(motivos)),
            contribucion=None,
            contribucion_pct=None,
            moneda=None,
            componentes=tuple(componentes),
            exclusiones=exclusiones,
        )

    if any(m in ESTADOS_DESACTUALIZADA for m in motivos):
        return ResultadoCalculo(
            estado="desactualizada",
            motivos=tuple(dict.fromkeys(motivos)),
            contribucion=None,
            contribucion_pct=None,
            moneda=None,
            componentes=tuple(componentes),
            exclusiones=exclusiones,
        )

    if motivo_pol:
        return ResultadoCalculo(
            estado="incompleta",
            motivos=tuple(dict.fromkeys(motivos)),
            contribucion=None,
            contribucion_pct=None,
            moneda=None,
            componentes=tuple(componentes),
            exclusiones=exclusiones,
        )

    moneda_venta = entrada.moneda_base or entrada.precio_moneda

    ingreso_exacto: Decimal | None = None
    ingreso: Decimal | None = None
    if entrada.precio_bruto is None or entrada.precio_moneda is None:
        motivos.append("precio_ausente")
    else:
        try:
            precio = validar_dinero(entrada.precio_bruto, campo="precio", positivo=True)
        except ValueError:
            motivos.append("precio_invalido")
            precio = None
        if precio is not None:
            componentes.append(
                ComponenteEconomico(
                    nombre="precio_bruto",
                    importe_original=precio,
                    moneda_original=entrada.precio_moneda,
                    importe_normalizado=precio,
                    moneda_normalizada=entrada.precio_moneda,
                )
            )
            if politica.settings.get("precio_incluye_iva"):
                divisor = _decimal_setting(politica.settings, "iva_divisor")
                ingreso_exacto = precio / divisor
            else:
                ingreso_exacto = precio
            ingreso = _cuantizar_frontera(ingreso_exacto)
            if moneda_venta is None:
                moneda_venta = entrada.precio_moneda
            componentes.append(
                ComponenteEconomico(
                    nombre="ingreso_normalizado",
                    importe_normalizado=ingreso,
                    moneda_normalizada=moneda_venta,
                )
            )

    fee_exacto: Decimal | None = None
    fee: Decimal | None = None
    if entrada.fee_total is None or entrada.fee_estado != "success":
        motivos.append("fee_ausente")
    else:
        for det in entrada.fee_detalles:
            if _tiene_tax_amount(det):
                motivos.append("impuesto_fee_pendiente")
                break
        motivo_fee_det = _validar_fee_detalles(
            entrada.fee_total,
            entrada.fee_detalles,
            fee_estado=entrada.fee_estado,
        )
        if motivo_fee_det:
            motivos.append(motivo_fee_det)
        try:
            fee_exacto = validar_dinero(
                entrada.fee_total,
                campo="fee_total",
                positivo=False,
                permitir_cero=True,
            )
            fee = fee_exacto
        except ValueError:
            motivos.append("fee_invalido")
            fee_exacto = None
            fee = None
        if fee is not None:
            componentes.append(
                ComponenteEconomico(
                    nombre="fee_total",
                    importe_normalizado=fee,
                    moneda_normalizada=moneda_venta,
                )
            )
            for det in entrada.fee_detalles:
                componentes.append(
                    ComponenteEconomico(
                        nombre=f"fee_detalle:{det.fee_type}",
                        importe_normalizado=det.final_fee,
                        moneda_normalizada=moneda_venta,
                        pertenece_a_total=False,
                    )
                )

    costo_exacto = _normalizar_costo(entrada, moneda_venta or "", motivos, componentes)

    logistica = _decimal_setting(politica.settings, "logistica")
    logistica = _decimal_setting(politica.settings, "logistica")
    isr_tasa = _decimal_setting(politica.settings, "isr_tasa")
    ret_iva_tasa = _decimal_setting(politica.settings, "retencion_iva_reconciliacion")

    isr_exacto: Decimal | None = None
    isr: Decimal | None = None
    ret_iva: Decimal | None = None
    if ingreso_exacto is not None:
        isr_exacto = ingreso_exacto * isr_tasa
        isr = _cuantizar_frontera(isr_exacto)
        ret_iva = _cuantizar_frontera(ingreso_exacto * ret_iva_tasa)
        componentes.append(
            ComponenteEconomico(
                nombre="logistica",
                importe_normalizado=_cuantizar_frontera(logistica),
                moneda_normalizada=moneda_venta,
            )
        )
        componentes.append(
            ComponenteEconomico(
                nombre="isr",
                importe_normalizado=isr,
                moneda_normalizada=moneda_venta,
            )
        )
        componentes.append(
            ComponenteEconomico(
                nombre="retencion_iva_conciliacion",
                importe_normalizado=ret_iva,
                moneda_normalizada=moneda_venta,
                pertenece_a_total=False,
            )
        )

    bloqueantes = {
        "precio_ausente",
        "precio_invalido",
        "costo_ausente",
        "costo_invalido",
        "costo_impuesto_incompatible",
        "costo_base_fiscal_ausente",
        "fee_ausente",
        "fee_invalido",
        "fx_ausente",
        "fx_direccion_invalida",
        "fx_tasa_invalida",
        "impuesto_fee_pendiente",
        "politica_no_vigente",
        "politica_invalida",
        "fee_incompatible",
        "politica_ausente",
        "politica_ambigua",
    }
    if any(m in bloqueantes for m in motivos):
        return ResultadoCalculo(
            estado="incompleta",
            motivos=tuple(dict.fromkeys(motivos)),
            contribucion=None,
            contribucion_pct=None,
            moneda=None,
            componentes=tuple(componentes),
            exclusiones=exclusiones,
            ingreso_normalizado=ingreso,
            logistica=logistica,
            isr=isr,
        )

    assert (
        ingreso_exacto is not None
        and costo_exacto is not None
        and fee_exacto is not None
        and moneda_venta
    )

    contrib_exacto = (
        ingreso_exacto - costo_exacto - fee_exacto - logistica - (isr_exacto or Decimal("0"))
    )
    contrib = _cuantizar_frontera(contrib_exacto)
    pct = _cuantizar_frontera(contrib_exacto * Decimal("100") / ingreso_exacto)

    return ResultadoCalculo(
        estado="disponible",
        motivos=(),
        contribucion=contrib,
        contribucion_pct=pct,
        moneda=moneda_venta,
        componentes=tuple(componentes),
        exclusiones=exclusiones,
        ingreso_normalizado=ingreso,
        logistica=logistica,
        isr=isr,
Semantics check:

  - `logistica` component: importe_normalizado = cuantizar(logistica) where logistica is an amount from settings (a fixed amount per unit? `_validar_tasa_no_negativa` — but used as `contrib = ingreso - costo - fee - logistica - isr` — so it's an amount). Tool: `envio = _componente(componentes, "logistica")` — correct, amount.
  - `isr` component: isr = cuantizar(ingreso_exacto * isr_tasa). Tool computes tasa_isr = isr_importe / ingreso where ingreso = component "ingreso_normalizado" = cuantizar(ingreso_exacto). Slight rounding: isr/ingreso ≈ isr_tasa with rounding error from double quantization. precio_estrella with isr_tasa slightly off → P* slightly off. Minor precision issue: reconstructed tasa differs from original isr_tasa by quantization. The docstring says "se reconstruyen de los componentes congelados: sin fuentes nuevas" — deliberate. But note: this yields an approximate tasa; P* could be off by a centavo. It's used only for display/dry-run guard, not for actual pricing decisions. Low severity note.
  - `divisor_iva = precio_bruto / ingreso`: precio_bruto component importe_normalizado = precio (venta currency). ingreso_normalizado = cuantizar(precio/divisor) or precio. So divisor_iva ≈ iva_divisor (e.g., 1.16) with rounding; if precio_incluye_iva false, ingreso == precio exactly → divisor = 1 → incluye_iva False. Good.

  But wait — `incluye_iva` flag: `divisor_iva != 1` — with quantization, if precio doesn't include IVA, ingreso_exacto = precio, ingreso = cuantizar(precio). Is precio already quantized to same frontier? precio passed `validar_dinero` — likely 2 decimals; _cuantizar_frontera probably quantizes to more decimals. So equal. OK.

  If precio includes IVA: divisor_iva = precio/cuantizar(precio/1.16) ≈ 1.16 ± epsilon. Fine for display.

  Now potential issue: moneda. `p_actual, moneda = oferta` — oferta price_currency. escenario moneda is moneda_venta which may differ from oferta currency if moneda_base conversion... The tool uses oferta's currency for P* display but P* computed from componentes in moneda_venta (moneda_normalizada). If moneda_base differs from precio_moneda (e.g. normalized to MXN while listing in USD?), P* would be in moneda_venta but printed with oferta's price_currency. Hmm: `moneda_venta = entrada.moneda_base or entrada.precio_moneda`. If moneda_base is set and differs from price currency, componentes are in moneda_venta, but the tool prints them with oferta price_currency. Also fee quoted_price currency? The escenario has a `moneda` column (esc[5]) which the tool SELECTs but never uses! Look: `_SQL_ESCENARIO` selects `moneda` (esc[5]) but `_referencia` never uses esc[5]; it returns moneda from oferta. If escenario.moneda != oferta.price_currency, P*/m_actual mix currencies. Is that possible? estado=disponible requires... moneda_venta = moneda_base or precio_moneda. Where does moneda_base come from? Check EntradaCalculo. This could be a real inconsistency: P* computed in moneda_venta but displayed/compared against P_actual in oferta currency. And the 25% salto guard compares p_estrella (moneda_venta) vs p_actual (oferta currency) — if currencies differ, guard is meaningless. Let me check whether they can differ: search moneda_base.app/estimacion_venta.py:84:    moneda_base: str | None = None
app/estimacion_venta.py:384:    moneda_venta = entrada.moneda_base or entrada.precio_moneda
app/estimacion_repository.py:504:        moneda_base=moneda_venta,
174:    moneda                currency,
190:            AND moneda IS NOT NULL
204:            AND moneda IS NULL
244:    'S5: solo disponible lleva contribucion/moneda; otros estados exigen NULL. '

    fee_total = fee_raw["total_fees"] if fee_raw else None
    fee_estado = fee_raw["estado"] if fee_raw else None
    fee_detalles: tuple[DetalleFee, ...] = ()
    if fee_raw is not None:
        try:
            fee_detalles = _detalles_desde_json(fee_raw["fee_details"])
        except ValueError:
            motivos.append("fee_incompatible")

    entrada = EntradaCalculo(
        precio_bruto=oferta_raw["price_amount"],
        precio_moneda=oferta_raw["price_currency"],
        costo_original=costo_fx.amount,
        costo_moneda=costo_fx.currency,
        fee_total=fee_total,
        costo_includes_tax=costo_fx.includes_tax,
        fee_detalles=fee_detalles,
        fee_estado=fee_estado,
        fx_rate=costo_fx.fx_rate,
        fx_rate_date=costo_fx.fx_rate_date,
        fx_base=costo_fx.fx_base,
        fx_quote=costo_fx.fx_quote,
        fx_source=costo_fx.fx_source,
        moneda_base=moneda_venta,
        motivos_entrada=tuple(dict.fromkeys(motivos)),
    )

    if politica is None:
        if "identidad_ambigua" in motivos:
            estado_sin_pol = "identidad_ambigua"
        elif any(m in ESTADOS_DESACTUALIZADA for m in motivos):
            estado_sin_pol = "desactualizada"
        else:
            estado_sin_pol = "incompleta"
        resultado = ResultadoCalculo(
            estado=estado_sin_pol,
            motivos=tuple(dict.fromkeys(motivos)),
            contribucion=None,
            contribucion_pct=None,
            moneda=None,
280:        "SELECT listing_id, platform, seller_sku, asin, canal, price_amount, price_currency,"
294:        "price_currency": fila[6],
305:        " quoted_price_amount, quoted_price_currency, total_fees, fee_details, estado,"
320:        "quoted_price_currency": fila[7],
354:    if fee["quoted_price_currency"] != oferta["price_currency"]:
373:    moneda_venta: str | None,
397:    if moneda_venta and costo.cost_currency != moneda_venta:
401:            base=moneda_venta,
476:        moneda_venta=oferta_raw["price_currency"],
479:    moneda_venta = oferta_raw["price_currency"]
492:        precio_moneda=oferta_raw["price_currency"],
504:        moneda_base=moneda_venta,
539:        "precio_moneda": oferta_raw["price_currency"],
Line 479: `moneda_venta = oferta_raw["price_currency"]` — so moneda_venta == price_currency always. Good, no currency mismatch. The tool's unused esc[5] (moneda) is just dead select — harmless.

  Now check remaining items:

  1. `app/db.py` — `connect` signature with autocommit, OrbitDbError. Check.
  2. `tools/precio_reversa.py` ceremony — compare; not strictly needed.
  3. test_architecture.py: check that `re` and `Path`, `RAIZ`, `APP`, `PRECIO` are defined (they are, existing file). Check `_puros_precio(raiz=None)` — new version references `EXCEPCIONES_PURAS_PRECIO` — defined after the function but before tests run. But wait: `_puros_precio` is also called with `raiz` param in some tests (e.g. tmp_path with sembrada fuga?). Check tests at line 1134+ that call `_puros_precio(tmp_path)` — the exclusion is relative to `base`, fine.

  But wait: those tests at 1134-1223 call `_puros_precio()` maybe with tmp copies. Fine.

  4. Check that `goals_write.py` being excepted from purity tests is acceptable: it's in app/precio but imports psycopg — a layering exception, declared and locked by name. But also: is there a test scanning app/ for SQL writers that might now conflict (e.g. a guard "solo X modulos escriben en la base")? Check `_escritores_app...` analogs and whether some existing test asserts app/precio has no psycopg at all beyond this. Also check whether `test_precio_puro_sin_io`'s exception weakens: the exception removes goals_write from ALL tests using _puros_precio — including tests at 1107 (import dinamico) and others (1158, 1186, 1223?). Let me check what those tests are.

  5. `fraccion_desde_porcentaje`: goal pct of "0.5" (0.5%) impossible — band min is 0.10 fraction = 10%, so pct >= 10 anyway. Not an issue. What about "100"? valor=100 → fraccion 1.0 → banda check max < 1 rejects. OK. What about exponent check with trailing zeros: "30.000" → exponent -3 → rejected "maximo dos decimales". Hmm, is that intended? "30.000" == 30.00 numerically but rejected. Nitpick; arguably intentional strictness. Also "30.10" fine.

  Edge: Decimal("3E+1") → exponent 1, value 30 → accepted. Fine.

  Wait, one more: `valor.as_tuple().exponent` can be 'n' for NaN? Already filtered by is_finite. OK.

  6. `_validar_fila` — shadow with go_literal: `elif go_literal is not None: raise`. But what if go_literal is empty string "" in shadow? `go_literal is not None` → raises. Good. In live: `go_literal is None or not go_literal.strip()` → raise. Good. Matches CHECK (btrim <> '').

  7. `sembrar_goal`: ExclusionViolation — constraint name for exclusion is `precio_goal_sin_solape`, and partial unique index violation raises UniqueViolation with constraint_name `precio_goal_un_vigente`. Both map to "ya hay goal vigente" message. But `precio_goal_sin_solape` ExclusionViolation could also trigger when re-seeding after closing with valid_to in the future? cerrar always sets today. Or when the previous goal was closed with valid_to > today? Can't — valid_to only set to today. Actually could a goal be inserted directly via SQL with future valid_to? Manual. Edge case: message would be misleading ("ya hay goal vigente") when actually solape with closed range. Minor.

  8. RACE: pre-check then INSERT — fine, DB constraints back it.

  9. `cerrar_goal` doesn't handle the case where UPDATE fails due to trigger (RaiseError → psycopg.errors.RaiseException) — propagates raw; main catches only Abortar/PrecioGoal*/OrbitDbError. A trigger RAISE EXCEPTION with errcode restrict_violation → psycopg.errors.lookup('restrict_violation')? RAISE with ERRCODE maps to specific error class. Would crash with traceback. Edge case; low.

  10. In tools/precio_goal.py `_huella`: for cerrar rows, mode not included — but `_huella(plan, args.mode)` and cerrar with `--mode live`? cerrar + --mode live + --acepto: `args.cerrar or args.mode == "shadow"` → cerrar goes without go. Fine. But cerrar default mode is shadow; user could pass --mode live with --cerrar — harmless.

  11. Huella for siembra includes fraccion (Decimal str). Decimal("0.3000") str → "0.3000". Deterministic. OK.

  12. `_guardas_del_plan`: live check `fila.m_actual is None` — but also caso "oferta_ausente" where m_actual not None but P* None: live allowed with aviso. Declared residual ("solo la falta de escenario aborta live"). Hmm wait: docstring says fee_error/margen_imposible continues; but oferta_ausente returns m_actual with P* None — the escenario exists but oferta row missing (shouldn't happen; FK). Fine.

  13. `_referencia` — `fee[1]` fee_details could be None? DEFAULT '[]', NOT NULL. OK. `Decimal(str(fee[2]))` quoted_price_amount NOT NULL. OK.

  14. `_construir_plan` reads config once. In `_go_con_ceremonia` mutation, `sembrar_goal` re-reads config (config_vigente_settings per row). If config changed between dry-run and go, the banda may differ but huella doesn't include config → goal rejected by sembrar or seeded with different band. Since dry-run and go are separate invocations, the config could change. But huella only covers plan rows; banda validation identical unless config changes. sembrar re-validates so it fails closed. OK.

  15. Test architecture: `test_candado_precio_goal_caza_update_crudo_en_tools` reads the real tool file and appends a fuga. Fine.

  16. `_escritores_crudos_precio_goal(RAIZ)` scans tools/ — but tools/precio_goal.py itself contains strings like "UPDATE precio_goal"? It contains only SELECTs (_SQL_ESCENARIO etc.). And the fuga test... the real tool must not contain "UPDATE precio_goal" text. In the docstring: "la mutacion real exige..." no UPDATE text. Check: docstring mentions "UPDATE de `valid_to`" in goals_write, not in tool. Tool docstring: "cerrar (`cerrar_goal`, UPDATE de `valid_to`..." — wait that's goals_write docstring. In test_architecture comment: "`app/precio/goals_write.py` es el unico que escribe `precio_goal` (INSERT al sembrar, UPDATE de `valid_to` al cerrar..." — the pattern needs "UPDATE\s+precio_goal" — "UPDATE de `valid_to`" doesn't match (UPDATE followed by "de"). OK. But careful in goals_write docstring: "cerrar (`cerrar_goal`, UPDATE de `valid_to` y nada mas)" — no match. `_escritores_app_precio_goal` scans app/ and asserts == ["app/precio/goals_write.py"] — goals_write.py contains real INSERT/UPDATE SQL. Also app/optimizer/goals.py — does it write ads_optimizer_goal, not precio_goal. Pattern is precio_goal specific. But wait — `_IDENT_PRECIO_GOAL = r'(?:"?\w+"?\.)?"?precio_goal"?'` — "ads_optimizer_goal"? No. But would "UPDATE precio_goal" appear elsewhere in app/, e.g. in a migration runner or api? Probably not; test asserts.

  Hmm — but careful: does the regex match e.g. "precio_goal" inside longer identifier? `"?precio_goal"?` — no word boundary at the end! E.g., "INSERT INTO precio_goal_archivo" would match. Not present probably. Also no leading word boundary: "UPDATE foo_precio_goal" would match. Edge, low.

  17. Big one to check: does an existing architecture test forbid `psycopg` imports in app/precio beyond test_precio_puro_sin_io? The exception covers _puros_precio users. Are there other tests that enumerate app/precio files differently (e.g., `PRECIO.rglob` directly)? Let me grep tests for "precio" rglob usage. Also, run the actual test suite for these files to be sure? Running focused tests: pytest tests/test_architecture.py tests/test_precio_goals.py. That would be a strong verification. Let me do that.

  Also check app/db.connect signature and OrbitDbError.

21:class OrbitDbError(Exception):
47:def connect(dsn: str, **kw) -> psycopg.Connection:
Using CPython 3.14.7
Creating virtual environment at: .venv
Installed 33 packages in 44ms
........................................................................ [ 63%]
..........................................                               [100%]
114 passed in 12.81s
• Tests pass. Now check a couple more subtle things:

  1. `_referencia` catches broad exceptions including `TypeError`, `ValueError`, `ArithmeticError`, `KeyError`, `InvalidOperation` → returns "referencia_sin_derivar". This swallows programming errors (e.g., a bug in precio_estrella's ValueError from exigir_decimal when given wrong type) — but it's a display-only path; declared. Note: `exigir_decimal` raises... if componentes json importe is a string, Decimal(str(...)) is Decimal, fine.

  2. `_componente` — "costo_normalizado": two possible appends but only one per path (same-currency path returns early). So single. OK. "precio_bruto" has importe_normalizado = precio — but only appended when precio valid. "ingreso_normalizado" appended only if precio valid. "logistica"/"isr" appended only if ingreso_exacto not None. For estado='disponible', all present. OK.

  3. But note: `_componente(componentes, "logistica")` uses `item["importe_normalizado"]` — for componentes lacking that key (e.g., "fx" has tasa but no importe_normalizado), names differ so no collision.

  4. In `_referencia`, m_actual = esc[1] contribucion_pct — in POR CIENTO (e.g. 39.0000). `_linea` prints `f"{fila.m_actual:.2f}%"`. Consistent. And goal printed `fraccion*100`. OK.

  5. The salto guard uses p_estrella vs p_actual — P* from formula, fine.

  6. `main` — `_entradas_desde_args(args)` may raise Abortar before connect — caught. OrbitDbError catch is redundant duplicate of the first except (same body) — cosmetic.

  7. Duplicated `except` clauses — fine.

  8. One real check: `_huella` does NOT include `cerrar` vs mode distinction for siembra across... includes mode. For cerrar plan, huella = cerrar parts. If user dry-runs --cerrar then runs go with --cerrar --acepto --huella — fine. But could a user take the huella from a siembra dry-run and use it on a cerrar run? Parts differ. OK.

  9. `fraccion_desde_porcentaje` accepts "10" → 0.10; band min 0.10 inclusive. CHECK is BETWEEN 0.10 AND 0.60 — inclusive. Consistent.

  10. Quantize to 0.0001: NUMERIC(6,4) fits. OK.

  11. Concern: `sembrar_goal` catches UniqueViolation for `precio_goal_unico_por_fecha` giving a special message; but if the same listing had goal today and user seeds again, unique (listing, platform, valid_from) — matches. Good.

  12. `config_vigente_settings` — ORDER BY id DESC LIMIT 1: is "vigente" defined as latest id? config_write uses the same query. Consistent.

  13. The tool's `_SQL_VIGENTE` SELECTs mode::text — used only for existence; fine.

  14. `test_precio_excepcion_por_nombre` asserts `(PRECIO / "tipos.py").is_file()` — exists.

  15. Potential issue: `_puros_precio` with `raiz` param — exclusion compares `p.relative_to(base)` — fine for tmp_path copies.

  16. Check that no other existing guard scans app/ for DB writes that now flags goals_write... tests passed (test_architecture full run passed). Good enough.

  17. Security: DSN admin from env, redaction? `OrbitDbError` message might include DSN? app/db handles redaction presumably. Not in diff; fine.

  18. One more: in `_go_con_ceremonia`, when plan is empty (e.g., --csv with... no, csv aborts empty; listing path always 1 row). OK.

  19. `--cerrar` with `--mode live` and no `--go`: goes through (cerrar sin go). OK per design.

  20. `_entradas_desde_args`: if `--csv` given, `--platform` not required — good. But `--cerrar` + `--csv` without goal_pct column — fine.

  21. Subtle: in `main`, when args.sku resolution happens AFTER connect — fine. But `entradas` for sku path doesn't check cerrar/goal combos beyond earlier checks — earlier `_entradas_desde_args` checked `--goal-pct` presence unless cerrar. OK.

  22. `_linea` for cerrar prints goal_id from dry-run's vigente lookup; between dry-run and go, the vigente could change (race) — cerrar_goal re-selects vigente at mutation time; huella includes goal_id from dry-run; if the vigente changed in between, the huella still matches but cerrar closes a different goal than reviewed. Minor TOCTOU — but same for all such tools; the ceremony mitigates. Also huella includes goal_id, but go doesn't re-verify goal_id matches. Low severity note? Given cerrar_goal re-checks and closes whatever is vigente — if someone closed and re-seeded between dry-run and go, you'd close the new one unknowingly. It's an operational race; worth a low note. Actually wait — for siembra, R1: dry-run aborts if vigente exists; at go-time, sembrar_goal also re-checks. Good.

  23. The `_referencia` reconstruction of tasa_isr from rounded components introduces error: isr = cuantizar(ingreso_exacto * tasa), ingreso = cuantizar(ingreso_exacto). tasa_reconstructed = isr/ingreso differs from tasa by up to quantization. Then P* computed with that. For display/guard only. Also `envio` = cuantized logistica vs exact logistica. Fine for guard.

  One more check: `_cuantizar_frontera` — how many decimals? If it quantizes to 4 decimals, isr/ingreso error tiny. OK, low.

  24. In `goals_write.fraccion_desde_porcentaje`: rejects values with >2 decimals ("30.001") — but also rejects "30.000"? Yes exponent -3. Edge, intentional.

  Also `valor < 1` rejected — so goal of exactly 1% min? band min is 10% anyway.

  25. Exception ordering in main: `except (Abortar, PrecioGoalInvalido, PrecioGoalAusente)` then `except OrbitDbError` with identical body — dead redundancy, cosmetic.

  26. Check the test file tests/test_precio_goals.py covers cerrar etc. — tests passed, fine.

  27. Possible issue: `test_precio_puro_sin_io` exception also removes goals_write from `test_precio_sin_import_dinamico` and the other tests at 1158/1186/1223 (which use _puros_precio). Check what those are — if one is e.g. "sin datetime.now/reloj" then goals_write escapes the reloj guard. goals_write uses SQL `now() AT TIME ZONE 'UTC'` — not Python reloj, so AST guard wouldn't catch anyway. Let me quickly check what tests use _puros_precio.1080-def test_precio_puro_sin_io():
1083:    modulos = _puros_precio()
1115:    modulos = _puros_precio()
1155-def test_precio_sin_reloj_ni_entorno():
1158:    modulos = _puros_precio()
def test_precio_frontera_caza_importlib_dinamico(tmp_path, monkeypatch):
    """R5-J5, fuga sembrada: `importlib.import_module("psycopg")` dispara
    el candado de imports."""
    (tmp_path / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "dyn.py").write_text(
        'import importlib\nx = importlib.import_module("psycopg")\n', encoding="utf-8"
    )
    monkeypatch.setattr("test_architecture.PRECIO", tmp_path)
    with pytest.raises(AssertionError, match="dyn.py"):
        test_precio_puro_sin_io()


@pytest.mark.parametrize(
    "cuerpo",
    [
        'x = __import__("httpx")\n',
        'x = __import__ ("httpx")\n',
        'import builtins\nx = builtins.__import__("httpx")\n',
    ],
)
def test_precio_frontera_caza_dunder_import(tmp_path, monkeypatch, cuerpo):
    """R5-J5, fuga sembrada: `__import__("httpx")` dispara el detector
    (r6-C3: también con espacio y por atributo)."""
    (tmp_path / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "dyn.py").write_text(cuerpo, encoding="utf-8")
    monkeypatch.setattr("test_architecture.PRECIO", tmp_path)
    with pytest.raises(AssertionError, match="dyn.py"):
        test_precio_sin_import_dinamico()


def test_precio_sin_reloj_ni_entorno():
    """`hoy` entra como argumento: ni datetime.now, ni date.today, ni
    time.time, ni os.environ en el AST de `app/precio/*`."""
    modulos = _puros_precio()
    assert modulos, "no se encontro el motor de precios: ¿se movio app/precio/?"
    fugas = {
        p.relative_to(PRECIO).as_posix(): v
        for p in modulos
        if (v := _usos_reloj(ast.parse(p.read_text(encoding="utf-8"))))
    }
    assert not fugas, f"app/precio lee reloj o entorno: {fugas}"


def test_precio_frontera_caza_fuga_en_subpaquete(tmp_path, monkeypatch):
    """Fuga sembrada: un `sub/fuga.py` con `import httpx` hace fallar el
    candado con el nombre del archivo."""

    (tmp_path / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "fuga.py").write_text("import httpx\n", encoding="utf-8")
    monkeypatch.setattr("test_architecture.PRECIO", tmp_path)
    with pytest.raises(AssertionError, match="sub/fuga.py"):
        test_precio_puro_sin_io()


def test_precio_init_tambien_se_escanea(tmp_path, monkeypatch):
    """r1-B5: una fuga en `__init__.py` también dispara el candado."""

    (tmp_path / "__init__.py").write_text("import httpx\n", encoding="utf-8")
    monkeypatch.setattr("test_architecture.PRECIO", tmp_path)
    with pytest.raises(AssertionError, match="__init__.py"):
        test_precio_puro_sin_io()


def test_precio_frontera_caza_reloj_con_alias(tmp_path, monkeypatch):
    """r1-B5, fuga sembrada de reloj: `from datetime import datetime as dt`
    + `dt.now()` hace fallar el candado con el nombre del archivo."""

    (tmp_path / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "reloj.py").write_text(
        "from datetime import datetime as dt\nx = dt.now()\n", encoding="utf-8"
    )
    monkeypatch.setattr("test_architecture.PRECIO", tmp_path)
    with pytest.raises(AssertionError, match="sub/reloj.py"):
        test_precio_sin_reloj_ni_entorno()


@pytest.mark.parametrize(
    "cuerpo",
    [
        "import os\n",
        "import time\n",
        "import random\n",
        "import secrets\n",
        "from os import getenv\n",
    ],
)
def test_precio_frontera_caza_imports_de_reloj_entorno_azar(tmp_path, monkeypatch, cuerpo):
    """r2-A4: cada import prohibido hace fallar el candado con el nombre
    del archivo (reemplaza al test tautológico que solo miraba la
    constante)."""

    (tmp_path / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "fuga.py").write_text(cuerpo, encoding="utf-8")
    monkeypatch.setattr("test_architecture.PRECIO", tmp_path)
    with pytest.raises(AssertionError, match="sub/fuga.py"):
        test_precio_puro_sin_io()


def test_r4_g9_reloj_caza_acceso_sin_llamada(tmp_path, monkeypatch):
    """r4-G9: `reloj = dt.now` (referencia sin llamar) también dispara."""
    (tmp_path / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "reloj.py").write_text(
`test_precio_sin_reloj_ni_entorno` uses `_puros_precio()` — so goals_write.py is excluded from the reloj/entorno guard too. goals_write uses `(now() AT TIME ZONE 'UTC')::date` in SQL — not Python reloj, so the AST guard wouldn't catch it anyway. But it does read no env. OK — declared in the comment.

  One subtle thing: `test_precio_sin_reloj_ni_entorno` forbids os.environ etc. in app/precio — goals_write doesn't use those. Fine.

  Now, remaining possible findings:

  1. `_huella` does not bind the huella to `creado_por`... irrelevant.

  2. In tools `_go_con_ceremonia`, when mode == "live" and plan has rows with `motivo` = "referencia_sin_derivar" (fee no utilizable) — allowed with just aviso, but the docstring says declared residual. OK.

  3. Potential real bug: `_construir_plan` in cerrar mode never checks `args.mode`... fine.

  4. Check: `_entradas_desde_args` — `--cerrar` with `--goal-pct` aborts; but `--cerrar` with `--sku`: returns [] then main resolves sku. Fine.

  5. `main` catches exceptions but not generic `psycopg.Error` — e.g., unique violation race in cerrar (none), trigger RAISE (RaiseException). If the DB rejects an INSERT with a RaiseException from a trigger (precio_goal has no INSERT trigger besides... actually no INSERT trigger; only UPDATE/DELETE/TRUNCATE). INSERT can raise FK/Check/Unique/Exclusion — all handled in sembrar_goal. But sembrar_goal handles CheckViolation with generic message. OK.

  What about `cerrar_goal` UPDATE failing trigger `precio_goal_solo_cierra_vigencia`? The update only sets valid_to NULL→date, allowed. Race: two concurrent cerrar → second's UPDATE... both selected vigente, both update same row: second would set valid_to again → trigger raises restrict_violation → raw psycopg crash with traceback (exit 1, ugly). Low severity, operational race.

  6. autocommit: `connect(dsn, autocommit=True)` — check connect accepts kw → passes to psycopg.connect presumably. Fine.

  7. Now the main design check: `config_vigente_settings` — "ORDER BY id DESC" matches config_write's vigente. OK.

  8. The `_referencia` reconstruction: `divisor_iva = precio_bruto / ingreso` — if `precio_incluye_iva` false, ingreso == precio → divisor == 1 exactly? ingreso = _cuantizar_frontera(precio). precio = validar_dinero(precio_bruto) — check _cuantizar_frontera decimals. If _cuantizar_frontera rounds to, say, 4 decimals and validar_dinero allows 4 decimals, equal → divisor exactly 1. But precio_bruto component stores importe_normalizado=precio (post validar_dinero, pre-cuantizar). If validar_dinero allows more decimals than _cuantizar_frontera, divisor could be 1.0000001 → incluye_iva True wrongly. Check _cuantizar_frontera and validar_dinero precision.app/estimacion_venta.py:102:def validar_dinero(
app/estimacion_venta.py:138:def _cuantizar_frontera(valor: Decimal) -> Decimal:
app/estimacion_venta.py:309:    costo_norm = _cuantizar_frontera(costo_norm_exacto)
app/estimacion_venta.py:411:            ingreso = _cuantizar_frontera(ingreso_exacto)
app/estimacion_venta.py:479:        isr = _cuantizar_frontera(isr_exacto)
app/estimacion_venta.py:480:        ret_iva = _cuantizar_frontera(ingreso_exacto * ret_iva_tasa)
app/estimacion_venta.py:484:                importe_normalizado=_cuantizar_frontera(logistica),
app/estimacion_venta.py:547:    contrib = _cuantizar_frontera(contrib_exacto)
app/estimacion_venta.py:548:    pct = _cuantizar_frontera(contrib_exacto * Decimal("100") / ingreso_exacto)
app/estimacion_venta.py:102:def validar_dinero(
138:def _cuantizar_frontera(valor: Decimal) -> Decimal:
139-    return valor.quantize(_MAX_DECIMALES)
140-
141-
142-def _validar_tasa_no_negativa(valor: Decimal, *, campo: str) -> Decimal:
143-    if not valor.is_finite():
144-        raise ValueError(f"{campo} no finito")
145-    if abs(valor) >= _MAX_DINERO:
146-        raise ValueError(f"{campo} fuera de rango")
app/estimacion_venta.py:102:def validar_dinero(
app/estimacion_venta.py-103-    valor: Decimal,
app/estimacion_venta.py-104-    *,
app/estimacion_venta.py-105-    campo: str,
app/estimacion_venta.py-106-    positivo: bool = True,
app/estimacion_venta.py-107-    permitir_cero: bool = False,
app/estimacion_venta.py-108-) -> Decimal:
app/estimacion_venta.py-109-    """Decimal finito dentro de NUMERIC(14,4); positivo si se exige."""
app/estimacion_venta.py-110-    if not isinstance(valor, Decimal):
app/estimacion_venta.py-111-        raise ValueError(f"{campo} debe ser Decimal")
app/estimacion_venta.py-112-    if not valor.is_finite():
app/estimacion_venta.py-113-        raise ValueError(f"{campo} no finito")
app/estimacion_venta.py-114-    if abs(valor) >= _MAX_DINERO:
app/estimacion_venta.py-115-        raise ValueError(f"{campo} fuera de rango NUMERIC(14,4)")
app/estimacion_venta.py-116-    try:
app/estimacion_venta.py-117-        cuantizado = valor.quantize(_MAX_DECIMALES)
app/estimacion_venta.py-118-    except InvalidOperation as exc:
app/estimacion_venta.py-119-        raise ValueError(f"{campo} fuera de rango NUMERIC(14,4)") from exc
app/estimacion_venta.py-120-    if valor != cuantizado:
app/estimacion_venta.py-121-        raise ValueError(f"{campo} excede 4 decimales")
app/estimacion_venta.py-122-    if positivo and cuantizado <= 0:
validar_dinero requires ≤4 decimals and equality with quantized → precio has ≤4 decimals, _cuantizar_frontera quantizes to same _MAX_DECIMALES (0.0001 presumably) → ingreso == precio exactly when no IVA. So divisor_iva == 1 exactly. Good.

  When IVA included: ingreso = cuantizar(precio/divisor). divisor_iva_reconstructed = precio/ingreso ≈ divisor with error up to ~0.0001/ingreso. E.g. precio=100, divisor=1.16 → ingreso=86.2069 → recon = 100/86.2069 = 1.15999963... — close. P* slightly off. Display/guard only. Low.

  More significant check: tasa_isr reconstruction — same order of error. Low.

  Now let me double-check `precio_estrella` call in tool: `precio_estrella(costo, fijo, envio, tasa_isr, goal, divisor_iva, divisor_iva != 1, ref)` — positional matches signature (costo, fijo, envio, isr_tasa, goal, iva_divisor, incluye_iva, ref). Good.

  `derivar_ref_fijo(detalles, Decimal(str(fee[0])), Decimal(str(fee[2])))` — (detalles, fee_total, p_cotizado). fee[0]=total_fees, fee[2]=quoted_price_amount. Good.

  One thing: `derivar_ref_fijo` uses quoted_price from the FEE observation (which may differ from current oferta price). Deliberate (fee quoted at that price). OK.

  `exigir_decimal` — the tool passes Decimals. `_detalle_fee` converts str → Decimal. OK.

  Now check `DetalleFee` import — app.estimacion_venta.DetalleFee exists. Yes.

  `_referencia` returns moneda from oferta; esc[5] (moneda del escenario) selected but unused — cosmetic.

  Now the goals_write `_numero` quirk: `Decimal(str(valor).strip() if isinstance(valor, str) else str(valor))` — redundant but fine.

  Check `banda_desde_settings` bounds: `_fraccion(settings, "precio_goal_min_pct", minima="0", maxima="1")` — allows min=0 or 1, then the `0 < min < max < 1` check catches. Consistent.

  Check that settings values come as strings ("0.10") — config values stored as strings per config_write. If someone seeds them as JSON numbers (0.1 float via JSON), psycopg jsonb → Python float 0.1 → str(0.1)="0.1" → Decimal("0.1") fine. Bool check guards true/false. OK.

  Now let me check `tests/test_precio_goals.py` quickly for coverage of the important paths? Not strictly necessary; tests pass.

  One more potential issue: the architecture candado `_escritores_crudos_precio_goal` only scans `tools/`, and `_escritores_app_precio_goal` scans `app/` — but migrations/ or other dirs could also carry SQL. Migrations legitimately carry SQL. OK.

  Regex issue: `_IDENT_PRECIO_GOAL = r'(?:"?\w+"?\.)?"?precio_goal"?'` — matches "UPDATE precio_goal" but also would match "UPDATE precio_goal_extra"? There's no word boundary after; `\w` after would still match since pattern ends. E.g. text "UPDATE precio_goal_x" → matches (false positive). Also "reprice_goal"? `(?:"?\w+"?\.)?` optional prefix requires a dot; without dot, `"?precio_goal"?` needs literal "precio_goal" — "foo_precio_goal" contains "precio_goal" preceded by "_", and since there's no leading boundary, "UPDATE foo_precio_goal" would match → false positive in guard. Guard false-positives are fail-closed (annoying but safe). Low/non-issue.

  Real issue candidate: in `test_escritura_precio_goal_vive_solo_en_goals_write`, the assertion `_escritores_app_precio_goal() == ["app/precio/goals_write.py"]` — but does any docstring in the repo app/ contain "INSERT INTO precio_goal"? The tests pass, so currently no. But `app/precio/goals_write.py` docstring mentions "cerrar (`cerrar_goal`, UPDATE de `valid_to`..." no match. OK.

  Now, one genuine question: `cerrar_goal` returns the id from the SELECT, then UPDATE by id — fine.

  Another: `_go_con_ceremonia` for siembra calls `sembrar_goal` with `goal_pct=fila.goal_texto` and re-validates; mode from args. If user dry-ran with --mode shadow and then runs go with --mode live, huella differs (mode in huella) → aborts. Good.

  But wait — huella includes mode only for siembra parts. For cerrar, mode not included — fine since mode irrelevant to cerrar.

  One more potential gap: `--confirmar-salto` is in the guard but NOT in the huella. So: dry-run with salto blocked; user re-runs dry-run with --confirmar-salto → gets huella H. Then mutation run with --huella H --acepto --go... does the mutation re-check the salto guard? In main flow, yes: `_guardas_del_plan(plan, args)` runs BEFORE `_go_con_ceremonia` in every invocation (dry-run and mutation). So in the mutation run, the guard runs again and requires --confirmar-salto again. Good — the mutation command must repeat --confirmar-salto. OK.

  But: `_guardas_del_plan` also runs in the mutation run, re-querying the DB (plan rebuilt). If between dry-run and mutation the price changed so salto now >25% and user doesn't pass --confirmar-salto again → abort. Fine, fail-closed. But the huella doesn't cover m_actual/P* — that's expected since they change.

  Now check the live guard: `args.mode == "live" and any(fila.m_actual is None ...)` — m_actual None only when sin_escenario. OK.

  Alright — also check `tools/precio_goal.py` imports `app.estimacion_venta` and `app.precio.objetivo` — purity guard only applies to app/precio, tools can import. OK.

  Potential issue in `_filas_csv`: `csv.DictReader(texto.splitlines())` — fine. Duplicated (listing_id, platform) aborts. Good.

  `_resolver_sku` — listing table has seller_sku? grep quickly. Also check `listing` platform values. And GRANTs: app_admin has SELECT on listing, estimacion_*? The tool does SELECTs as app_admin — check GRANT SELECT for app_admin on estimacion_escenario/fee/oferta and listing, config_version. Let me grep migrations for GRANT to app_admin.

migrations/0028_estimacion_venta.sql:710:GRANT USAGE ON SEQUENCE estimacion_politica_version_id_seq TO app_admin;
migrations/0039_precio.sql:1139:GRANT USAGE ON SEQUENCE precio_goal_id_seq TO app_admin;
103:  'La identidad canónica del producto es el SKU de Odoo, NO el seller_sku de '
111:    seller_sku      TEXT,
migrations/0001_initial.sql:1460:GRANT SELECT ON ALL TABLES IN SCHEMA public TO app_ingest, app_decide, app_admin;
migrations/0001_initial.sql:1507:GRANT INSERT, UPDATE ON ads_optimizer_goal TO app_admin;
migrations/0001_initial.sql:1508:GRANT INSERT ON config_version TO app_admin;
migrations/0001_initial.sql:1512:GRANT INSERT ON apply_quota_state TO app_admin;
migrations/0001_initial.sql:1517:GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO app_ingest, app_decide, app_admin;
migrations/0001_initial.sql:1522:    GRANT SELECT ON TABLES TO app_read, app_ingest, app_decide, app_admin;
migrations/0002_apply.sql:712:GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO app_ingest, app_decide, app_admin;
migrations/0014_keyword_archivo_manual.sql:64:GRANT INSERT, UPDATE ON keyword_archivo_manual TO app_admin;
migrations/0014_keyword_archivo_manual.sql:65:GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO app_ingest, app_decide, app_admin;
migrations/0015_target_margen_plataforma.sql:169:GRANT SELECT ON v_target_margen_plataforma TO app_read, app_ingest, app_decide, app_admin;
migrations/0016_target_margen_correcciones.sql:179:GRANT SELECT ON v_target_margen_plataforma TO app_read, app_ingest, app_decide, app_admin;
migrations/0018_fabrica_campanas.sql:438:GRANT SELECT ON v_margen_producto TO app_read, app_ingest, app_decide, app_admin;
migrations/0018_fabrica_campanas.sql:441:-- GRANTs: app_admin escribe (la fabrica corre con ORBIT_DSN_ADMIN); el motor
migrations/0021_economia_observada.sql:179:GRANT SELECT ON v_economia_producto TO app_read, app_ingest, app_decide, app_admin;
migrations/0022_disponibilidad_snapshot.sql:67:GRANT SELECT ON disponibilidad_observation TO app_ingest, app_decide, app_admin;
migrations/0022_disponibilidad_snapshot.sql:69:GRANT USAGE ON SEQUENCE disponibilidad_observation_id_seq TO app_ingest, app_decide, app_admin;
migrations/0028_estimacion_venta.sql:698:GRANT SELECT ON estimacion_politica_version TO app_read, app_ingest, app_decide, app_admin;
migrations/0028_estimacion_venta.sql:699:GRANT INSERT ON estimacion_politica_version TO app_admin;
migrations/0028_estimacion_venta.sql:701:GRANT SELECT ON estimacion_oferta_observation TO app_read, app_ingest, app_decide, app_admin;
migrations/0028_estimacion_venta.sql:704:GRANT SELECT ON estimacion_fee_observation TO app_read, app_ingest, app_decide, app_admin;
migrations/0028_estimacion_venta.sql:707:GRANT SELECT ON estimacion_escenario TO app_read, app_ingest, app_decide, app_admin;
migrations/0028_estimacion_venta.sql:710:GRANT USAGE ON SEQUENCE estimacion_politica_version_id_seq TO app_admin;
migrations/0028_estimacion_venta.sql:715:GRANT SELECT ON estimacion_reversa_registro TO app_read, app_ingest, app_decide, app_admin;
migrations/0030_spapi_orders.sql:94:GRANT SELECT ON spapi_order_observation TO app_read, app_ingest, app_decide, app_admin;
migrations/0031_spapi_orders_bitemporal.sql:67:GRANT SELECT ON v_spapi_order_ultima TO app_read, app_ingest, app_decide, app_admin;
migrations/0032_spapi_pricing.sql:116:GRANT SELECT ON spapi_price_observation TO app_read, app_ingest, app_decide, app_admin;
migrations/0035_spapi_listings_inventario.sql:70:GRANT SELECT ON spapi_listing_estado_observation TO app_read, app_ingest, app_decide, app_admin;
migrations/0035_spapi_listings_inventario.sql:157:GRANT SELECT ON spapi_inventario_observation TO app_read, app_ingest, app_decide, app_admin;
migrations/0036_ingest_run_platform.sql:35:GRANT SELECT (platform) ON ingest_run TO app_read, app_ingest, app_decide, app_admin;
migrations/0039_precio.sql:1136:GRANT INSERT ON precio_goal TO app_admin;
• GRANTs verified: app_admin has SELECT on listing (ALL TABLES 0001 + default privileges), estimacion_escenario/oferta/fee (0028), config_version (0001 ALL TABLES), INSERT + UPDATE(valid_to) on precio_goal (0039), USAGE on sequence. All good.

  Everything verified. Tests pass. Remaining findings are mostly low-severity notes:

  1. (baja) `_referencia` reconstructs `tasa_isr` and `divisor_iva` from quantized components → small error vs original tasa; P* used for display and 25% guard may differ slightly from what the motor would compute with the exact politica settings. Also broad except swallowing bugs silently (TypeError/ValueError from programming errors → "referencia_sin_derivar"). Documented residual but the broad catch hides code bugs.

  2. (baja) TOCTOU between dry-run and go in cerrar: huella includes goal_id from dry-run but `cerrar_goal` re-resolves vigente at mutation time without checking it equals the reviewed goal_id. If the vigente changed between dry-run and go (someone closed + reseeded... wait, reseeded same day blocked by unico_por_fecha; closed then re-seeded next day), the tool closes a goal different from the one reviewed with the same huella. Actually huella includes goal_id, but go doesn't compare. Low.

  3. (baja) `main` duplicate except clauses (OrbitDbError identical body) — cosmetic.

  4. (baja) In `sembrar_goal`, ExclusionViolation from `precio_goal_sin_solape` (solape with a closed range, e.g. someone set valid_to in the future manually) is reported as "ya hay goal vigente... cierralo con --cerrar", misleading. Low.

  5. (VERIFICAR/baja) The config keys `precio_goal_min_pct`/`precio_goal_max_pct` are not seeded by any migration nor writable via the settings UI (config_write.proxima_config only touches specific keys but copies the rest, so once seeded manually they persist). First use requires manual seeding; intentional per docstring ("sin las claves se aborta"). Worth flagging as operational gap: no documented path to create them.

  6. Also: uncaught psycopg errors from triggers in cerrar (RaiseException) → traceback, exit code... main returns 2 only for known exceptions; a psycopg RaiseException would propagate → exit with traceback. Low.

  Also check: `fraccion_desde_porcentaje` rejects "30.000" (3 decimales) even though numerically equal — intentional strictness, skip.

  One more possible real bug to double-check: `_linea` prints m_actual with `.2f%` — m_actual is Decimal from NUMERIC(8,4) in percent. OK.

  And the salto guard compares `abs(p_estrella - p_actual) > 0.25 * p_actual` — both in oferta currency. P* computed in moneda_venta == price_currency (verified line 479). OK.

  Also `goals_write.sembrar_goal`: `(now() AT TIME ZONE 'UTC')::date` — consistent with DDL.

  Wait — one more: the tool `_construir_plan` computes the banda from config, and `sembrar_goal` re-validates with `config_vigente_settings` inside. But between building plan (dry-run) and go (separate invocation), huella doesn't include config version. sembrar re-checks → fail closed. OK.

  Also potential: `_huella` sorts partes — a plan with duplicate (listing, platform) already rejected. OK.

  Another check on `test_precio_excepcion_por_nombre`: asserts EXCEPCIONES_PURAS_PRECIO == ("goals_write.py",) — self-referential test; fine.

  I think there are no high/medium bugs. Let me also double check `_detalle_fee` in tool vs `_parse_detalle_fee` in repository — fee_details JSON from DB: keys fee_type, final_fee (str), tax_amount, included_fee_details. Tool's `_detalle_fee` uses `nodo["fee_type"]` (KeyError if missing — caught), `Decimal(str(nodo["final_fee"]))`. OK.

  One subtle: in `_referencia`, `fee[1]` may be empty list → derivar_ref_fijo raises referral_ausente → caught → "referencia_sin_derivar". OK.

  Also `_componente` uses `item.get("nombre")` and `item["importe_normalizado"]` — if importe_normalizado key absent → KeyError caught. OK.

  Final: severity assessment. I'll report ~5 findings, all baja, plus note verification done. Actually should I report "no high/medium"? The instructions: numbered list with severity. If nothing objectionable → LGTM. There are minor findings worth listing as baja. I'll list them.

To resume this session: kimi -r session_542fafb8-26dd-4359-b00e-4a563d88fa66


=== Revisor efectivo: kimi (binario 'kimi') ===


---
sha=662db383ee517cccfac757cc815531ca70fc786a archivos=app/precio/goals_write.py,tools/precio_goal.py,tests/test_architecture.py inicio=2026-09-18T16:58:17Z fin=2026-09-18T17:08:11Z exit=0
