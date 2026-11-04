=== quality-kit cross-review.ps1 ===
Repo: /Users/dn/dev/_wt/f11-kimi-r1
CLI: kimi
Alcance: last-commit
Archivos (pathspec de la tarea): app/notifica.py, docs/evidencia/repricing-01/A.6/mutantes.md
Tamano del diff: 44541 caracteres
==> Invocando kimi de forma no interactiva (tope: 1500 s)...

=== Respuesta de kimi (codigo de salida: 0) ===
• Leo el diff y verifico en el repo lo que sea necesario.

• Verifico los imports y funciones externas en `app/notifica.py` y el esquema.

• Imports confirmados. Ahora verifico el esquema de las tablas que tocan las queries nuevas.

• Esquema confirmado. Verifico el formato real de los motivos `fee_error:` y el contrato del gancho `avisar` en la corrida.

• Reviso el plan/spec de A.6 para confirmar si el doble aviso frenado+no_confirmado es intencional.

• Verificacion final: existencia de los tests citados en el catalogo de mutantes.

• Revision completa. Verifique contra el repo todo lo que el diff no muestra: imports (`dt`, `Decimal`, `httpx`, `dataclass`, `canal_activo`, `_envia_texto`, `scrub` existen en `app/notifica.py`), el esquema (`precio_decision`, `precio_cambio`, `listing`, `config_version` en `migrations/0039_precio.sql` y `0001_initial.sql` tienen todas las columnas de las queries), el contrato del gancho (`avisar(conn, platform, hoy, resumen)` con `resumen.decisiones`/`resumen.huerfanas` en `app/precio/corrida.py:1557-1559`, coincide), el formato real de `fee_error:<code>` (un solo colon, codigos tipo `fee_http_429`) y los tests (74 en `tests/test_precio_pantalla.py`).

  Hallazgos:

  1. NO BLOQUEANTE — Doble aviso por el mismo evento: un producto `frenado(no_confirmado)` dispara el aviso de grupo `(frenado, no_confirmado)` en `_avisar_grupos_precio` (via `_SQL_PRECIO_GRUPOS_DIA`, que incluye `frenado` sin excluir motivo) Y el aviso por producto en `_avisar_noconf_precio`. El plan A.6 lista ambos tipos y AC5 sugiere que es deliberado, pero el dueno recibe dos mensajes por el mismo hecho el mismo dia. Conviene declararlo en el docstring o excluir `motivo='no_confirmado'` del grupo.

  2. NO BLOQUEANTE — `_avisar_buybox_precio` clavea hoy/ayer por `COALESCE(seller_sku, external_id)` y `seller_sku` NO es unico por plataforma (el propio COMMENT de `listing` en 0001 documenta 2-4 listings por SKU): dos listings con el mismo SKU colapsan en `{r[0]: r}` y la perdida de buy box de uno queda escondida o su flanco mezclado con el del otro. Mas robusto clavear por `listing_id` (ya esta en la fila y se descarta como `_id`).

  3. NO BLOQUEANTE — En `_SQL_PRECIO_BUYBOX_DIA` no se filtra `resultado`: una fila `no_evaluado` con `buy_box_is_own=false` genera un aviso `buy_box_perdida` con `estado: no evaluado`, combinacion confusa para el dueno (perdio la buy box o no se evaluo?). Filtrar a resultados evaluados aclararia el mensaje.

  4. NO BLOQUEANTE — `avisar_precio` hace `return 0` cuando `config_version` esta vacia (`fila is None`), lo que tambien suprime el aviso de `huerfana_sin_patch`, que no depende de ninguna clave de config. En la practica siempre hay una fila de config, pero el acoplamiento es innecesario.

  5. NO BLOQUEANTE — `motivo_precio_es` usa `partition(":")[2]`, que arrastra todo lo que siga al primer `:`; hoy los codigos (`fee_http_429`, `referral_ausente`) no contienen `:` ni cuerpo de error, asi que no hay fuga, pero la garantia "el cuerpo del error jamas viaja" queda delegada al formato del emisor en vez de ser estructural (un `split(":", 2)[1]` o validar el codigo contra un vocabulario la haria robusta).

  6. NO BLOQUEANTE — Cosmeticos: `TIPOS_PRECIO` se define y no se usa (solo los sub-conjuntos y el literal `huerfana_sin_patch`); en `validar_precio_aviso_dias` la expresion `str(valor).strip() if isinstance(valor, str) else str(valor)` es redundante (`str` sobre `str` es identidad, el `.strip()` podria aplicarse siempre).

  7. NO BLOQUEANTE — N+1 acotado: `_avisar_noconf_precio` ejecuta `_SQL_PRECIO_ULTIMO_NOCONF` una vez por listing en flanco; con el volumen diario esperado es despreciable, solo lo registro.

  Ningun hallazgo bloqueante: las queries matchean el esquema real, los invariantes de dinero/moneda se respetan (los precios viajan preformateados como texto), el contrato fail-silent es consistente con el resto del modulo, el umbral 1-14 y los flancos tienen tests y evidencia de mutantes muertos, y los builders no reciben campos de costo/margen/goal de forma estructural.



=== stderr ===
kimi version 0.39.1
• The user explicitly says: act as external code reviewer, do NOT use skills, respond directly. User instructions take precedence over the skill plugin instructions. So proceed: read the diff file, then verify in repo as needed.

• The diff shows app/notifica.py additions. I need to verify things not in the diff: imports (httpx, Decimal, dt, dataclass, canal_activo, _envia_texto, scrub, logger), and schema columns (precio_decision.resultado, motivo, decision_date, buy_box_is_own, p_actual; precio_cambio columns; config_version.settings; listing.seller_sku/external_id). Let me read the top of app/notifica.py and check the schema.

• The file confirms imports exist (dt, Decimal, dataclass, httpx, canal_activo, _envia_texto, scrub, logger). Now verify the schema: precio_decision columns (resultado, motivo, decision_date, buy_box_is_own, p_actual, p_actual_currency, listing_id, platform), precio_cambio (precio_antes, precio_antes_currency, precio_despues, precio_despues_currency, listing_id, platform, estado, aplicado, es_reversa, id), listing (id, platform, seller_sku, external_id), config_version (settings). Let me grep migrations.

migrations/0001_initial.sql:106:CREATE TABLE listing (
migrations/0001_initial.sql:624:CREATE TABLE config_version (
migrations/0039_precio.sql:197:CREATE TABLE precio_decision (
migrations/0039_precio.sql:487:CREATE TABLE precio_cambio (
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
    l_currency          currency NOT NULL,
    r_valor             money_amount,
    r_currency          currency NOT NULL,
    escenario_id        BIGINT REFERENCES estimacion_escenario (id),
    fee_observation_id  BIGINT REFERENCES estimacion_fee_observation (id),
    -- `cotizacion_id` y `envio_muestra_id` nacen como BIGINT plano: sus FK se
    -- declaran por ALTER tras crear `precio_cotizacion`/`precio_envio_muestra`
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
-- 202 + `submissionId`, hecho 9), así que la fila nace `pendiente` con el
-- precio leído por GET justo antes, se sella a `enviado`/`error` con el ack
-- literal, y la observación del día siguiente la cierra a
-- `confirmado`/`no_confirmado`. El readback es informativo: nunca decide el
-- estado (un readback fallido es `readback_estado = fallido`, jamás
-- `no_confirmado`). `es_reversa` marca la reversa del dueño (S6: existe y se
-- prueba con ids reales antes del primer cambio real; nunca automática).
CREATE TABLE precio_cambio (
    id                        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    decision_id               BIGINT REFERENCES precio_decision (id),
    listing_id                BIGINT NOT NULL,
    platform                  platform NOT NULL,
    precio_antes              money_amount NOT NULL,
    precio_antes_currency     currency NOT NULL,
    precio_observado_antes    money_amount,
    precio_observado_antes_currency currency,
    precio_despues            money_amount NOT NULL,
    precio_despues_currency   currency NOT NULL,
    aplicado                  BOOLEAN NOT NULL,
    estado                    TEXT NOT NULL,
    enviado_at                TIMESTAMPTZ,
    ack                       JSONB,
    readback_precio           money_amount,
    readback_precio_currency  currency,
    readback_estado           TEXT,
    readback_at               TIMESTAMPTZ,
    confirmado_por            TEXT,
    error_code                TEXT,
    es_reversa                BOOLEAN NOT NULL DEFAULT false,
    reversa_de                BIGINT REFERENCES precio_cambio (id),
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT precio_cambio_listing_fk FOREIGN KEY (listing_id, platform)
        REFERENCES listing (id, platform),
    CONSTRAINT precio_cambio_estado_valido
        CHECK (estado IN ('pendiente', 'enviado', 'error', 'confirmado', 'no_confirmado')),
    CONSTRAINT precio_cambio_observado_con_moneda
        CHECK ((precio_observado_antes IS NULL) = (precio_observado_antes_currency IS NULL)),
    CONSTRAINT precio_cambio_readback_con_moneda
        CHECK ((readback_precio IS NULL) = (readback_precio_currency IS NULL)),
    CONSTRAINT precio_cambio_readback_estado_valido
        CHECK (readback_estado IS NULL OR readback_estado IN ('ok', 'fallido')),
    -- Precios de verdad (ronda 2 de revisión): como `listing_precio_positivo`
    -- en 0001 y `precio_cotizacion_precio_positivo` aquí, el ledger no admite
    -- 0 ni negativos; observado/readback solo cuando no son NULL.
    CONSTRAINT precio_cambio_precios_positivos
        CHECK (precio_antes > 0 AND precio_despues > 0
               AND (precio_observado_antes IS NULL OR precio_observado_antes > 0)
               AND (readback_precio IS NULL OR readback_precio > 0)),
    -- Un estado no avanza sin su sello: el error trae código, el cierre trae
    -- origen, y el vuelo (`enviado` en un cambio real) trae ack + enviado_at.
    -- El virtual (`aplicado = false`) queda fuera del tercero a propósito:
    -- nace `confirmado` sin ack.
    CONSTRAINT precio_cambio_error_exige_codigo
        CHECK (estado <> 'error' OR error_code IS NOT NULL),
    CONSTRAINT precio_cambio_cierre_exige_origen
        CHECK (estado NOT IN ('confirmado', 'no_confirmado')
               OR confirmado_por IS NOT NULL),
    CONSTRAINT precio_cambio_envio_exige_ack
        CHECK (NOT aplicado
               OR estado IN ('pendiente', 'error')
               OR (ack IS NOT NULL AND enviado_at IS NOT NULL)),
    -- Vocabularios cerrados del ledger (ronda 4 de revisión, punto 2): S6
    -- solo cierra la observación del día siguiente (más el virtual, que nace
    -- cerrado) y el código de error solo existe en `error`.
    CONSTRAINT precio_cambio_confirmado_por_valido
        CHECK (confirmado_por IS NULL
               OR confirmado_por IN ('observacion', 'virtual')),
    CONSTRAINT precio_cambio_error_code_solo_error
        CHECK (error_code IS NULL OR estado = 'error'),
    -- Reversa sin decisión propia (S6, decisión del lead): la corre el dueño
    -- con la herramienta y no tiene `precio_decision`.
    CONSTRAINT precio_cambio_reversa_binaria
        CHECK ((NOT es_reversa AND reversa_de IS NULL)
               OR (es_reversa AND reversa_de IS NOT NULL)),
    CONSTRAINT precio_cambio_decision_salvo_reversa
        CHECK ((NOT es_reversa AND decision_id IS NOT NULL)
               OR (es_reversa AND decision_id IS NULL))
);

-- Un solo cambio abierto por (listing, platform): el predicado queda literal
-- como en S5 —`estado IN ('pendiente','enviado')`— así que el virtual, que
);

COMMENT ON COLUMN product.odoo_sku IS
  'La identidad canónica del producto es el SKU de Odoo, NO el seller_sku de '
  'la plataforma. Confundirlos costó un JOIN que duplicaba ventas 48%.';

CREATE TABLE listing (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_id      BIGINT   NOT NULL REFERENCES product(id),
    platform        platform NOT NULL,
    external_id     TEXT     NOT NULL,          -- ASIN o MLM id
    seller_sku      TEXT,
    listing_price   money_amount,
    price_currency  currency,
    UNIQUE (platform, external_id),
    -- Regla 4: no existe un precio sin su moneda.
    CONSTRAINT listing_precio_con_moneda
        CHECK ((listing_price IS NULL) = (price_currency IS NULL)),
    CONSTRAINT listing_precio_positivo
        CHECK (listing_price IS NULL OR listing_price > 0)
);

COMMENT ON CONSTRAINT listing_precio_con_moneda ON listing IS
  'Regla 4. Un precio sin moneda es la semilla del error de 18.66x.';

COMMENT ON TABLE listing IS
  'Un producto puede tener 2-4 listings (varios ASIN por SKU). Ignorar esa '
  'cardinalidad es lo que infló las ventas 48% en el panel de salud del '
  'sistema viejo.';

-- FK SIN ÍNDICE: PostgreSQL no crea uno por el REFERENCES. Sin él, toda


-- =============================================================================
--  9. CONFIGURACIÓN VERSIONADA  —  qué config regía cuando se decidió
-- =============================================================================

CREATE TABLE config_version (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    label      TEXT,
    settings   JSONB NOT NULL
);

COMMENT ON TABLE config_version IS
  'Snapshot inmutable (append-only por trigger) del set completo de '
  'configuración; la config VIVA vigente es la fila más reciente. Una decisión '
  'apunta a la versión que regía cuando se tomó; sin esto no se puede '
  'reconstruir por qué el sistema hizo lo que hizo. La inserta app_admin '
  '(config humana, escalera off->shadow->live), no los motores. '
  'settings JAMÁS contiene credenciales: app_read tiene SELECT aquí.';


-- =============================================================================
--  10. ESTADO ACTUAL DE ENTIDADES  —  MUTABLE, cache. Excepción deliberada.
-- =============================================================================

CREATE TABLE ad_entity_state (
    ad_entity_id   BIGINT PRIMARY KEY REFERENCES ad_entity(id),
• Schema confirms: precio_decision has resultado, motivo, decision_date, buy_box_is_own, p_actual, p_actual_currency, listing_id, platform. precio_cambio has precio_antes, precio_antes_currency, precio_despues, precio_despues_currency, estado, aplicado, es_reversa, id. listing has seller_sku, external_id, id, platform. config_version has settings JSONB.

  Note: `settings` is JSONB; psycopg3 returns dict for JSONB by default — validar_precio_aviso_dias handles dict. OK.

  Now potential issues to check in the diff logic:

  1. `_avisar_grupos_precio`: `nuevos = flanco_nuevos(sorted(grupos_hoy), sorted(grupos_ayer))` — computed before the per-group loop. But `nuevos` is only used for non-no_evaluado. For no_evaluado, the flanco_umbral uses days with that motivo present. However: a group could be both new today AND... that's fine.

  Wait — subtle issue: for `no_evaluado`, it doesn't check `nuevos` at all; it checks flanco_umbral. If the racha continues (racha == umbral+1), no aviso. Fine.

  Another subtle issue: `flanco_umbral` uses `dias` set from `_SQL_PRECIO_DIAS_MOTIVO` limited to BETWEEN hoy-umbral AND hoy. The racha counts consecutive days backward from hoy. Since the window is exactly umbral+1 days, if racha > umbral it can't be detected... wait, racha counts backward while dia in presentes; window only contains hoy down to hoy-umbral. If all umbral+1 days present, racha = umbral+1 ≠ umbral → False. Good. If the racha is longer than umbral+1 (e.g., umbral+2 days), window truncated → still racha=umbral+1 → False. Correct, since it already avisó at racha==umbral. But edge: if hoy-umbral day present and hoy-umbral-1 present too (racha continues), window doesn't include it, so racha capped at umbral+1 → False. Correct behavior.

  But there's an edge: if racha==umbral exactly but days beyond window are present with a gap... e.g., umbral=3, days present: hoy, hoy-1, hoy-2, and hoy-5, hoy-6. racha = 3 == umbral → True. Correct.

  Potential issue: `_SQL_PRECIO_DIAS_MOTIVO` filters `resultado='no_evaluado' AND motivo = %s`, where motivo comes from grupos_hoy key which uses `motivo or "desconocido"`. If motivo is NULL in DB, the group key is "desconocido" but the SQL `motivo = 'desconocido'` won't match NULL rows → dias won't include hoy → no aviso ever for NULL-motivo no_evaluado groups. But schema: `precio_decision_motivo_no_silencio` CHECK forces motivo NOT NULL for no_evaluado. So NULL motivo can't exist for no_evaluado. For goal_inalcanzable/frenado also motivo NOT NULL. OK, non-issue.

  2. `_SQL_PRECIO_GRUPOS_DIA` selects resultado, motivo, sku and groups by (resultado, motivo). The aviso for `frenado` — but `_avisar_noconf_precio` separately handles frenado+no_confirmado as per-product avisos. Does that mean frenado(no_confirmado) products get BOTH a group aviso (frenado/no_confirmado group) and individual no_confirmado avisos? Looking at `_avisar_grupos_precio`: grupos include resultado='frenado' with any motivo, including 'no_confirmado'. So a product frenado por no_confirmado triggers: (a) group aviso "frenados / motivo no confirmado por la observacion" when the (frenado, no_confirmado) group is new, and (b) per-product no_confirmado aviso via `_avisar_noconf_precio`. Possible duplicate notification. Is this intended? Hard to say without spec. The spec (REPRICING 01) might define both. This could be a "NO BLOQUEANTE, VERIFICAR" — intended? The design seems deliberate: group aviso is by (plataforma, motivo) flanco, and per-product aviso for no_confirmado. Hmm, but the group tipo "frenado" flanco is on (resultado, motivo) new vs ayer, while per-product no_confirmado is per-listing flanco. So yes, a product newly frenado-no_confirmado would generate two messages. Could be intended (one group-level, one product-level). Can't prove bug. Could mention as VERIFICAR/no bloqueante.

  3. `_avisar_buybox_precio`: `bb_ayer` is a set of r[0] (COALESCE sku/external_id), `bb_hoy` dict keyed by same. flanco: product whose buy box lost today and... wait, bb_ayer is the set of products that had buy_box_is_own=false AYER (lost yesterday too). flanco_nuevos returns keys in hoy not in ayer → lost today but NOT lost yesterday. Comment says "pierde la buy box hoy y ayer la tenia". If ayer had no decision row at all for that listing (not evaluated), then it's not in bb_ayer → aviso fires even though we don't know ayer's state. Minor; acceptable.

  Also `buy_box_is_own = false` — NULL buy_box_is_own rows excluded. Fine.

  Edge: key is COALESCE(seller_sku, external_id); two listings could share the same seller_sku? seller_sku not unique per platform necessarily. Collisions would merge listings. Minor.

  4. `_SQL_PRECIO_ULTIMO_NOCONF`: picks latest no_confirmado cambio with aplicado AND NOT es_reversa, ORDER BY id DESC. Fine.

  5. `validar_precio_aviso_dias`: `Decimal(str(valor).strip() if isinstance(valor, str) else str(valor))` — the conditional is redundant (str(valor) on a str is itself), cosmetic. Decimal accepts 'inf'/'nan' strings — checked with is_finite. Decimal('1e2') → 100, integral → ok. Fine.

  Hmm wait: `Decimal(str(valor))` for a float 3.0 → Decimal('3.0') integral → 3. OK.

  6. `flanco_umbral`: `umbral = int(umbral)` — if umbral is None (caller passes None? `_avisar_grupos_precio` checks `if umbral is None: continue` before calling). int(None) raises TypeError → caught → False. OK.

  7. `avisar_precio`: `_SQL_PRECIO_CONFIG` selects latest config_version settings. If fila None → return 0 — but huerfana aviso also suppressed when no config row. Hmm: `if fila is None: return 0` before `_avisar_huerfana_precio`. So with no config_version row, huerfanas never avisan. Edge case; config always exists presumably. Minor.

  Also note: `resumen.decisiones > 0` — attribute access; if resumen lacks the attr, exception caught → 0 and huerfana also skipped. Contract of resumen from corrida — can't verify; likely has .decisiones and .huerfanas. Could VERIFICAR. Actually let me check app/precio/corrida.py for the avisar hook signature and resumen fields.

  8. `_enviar_productos_precio_con_tope`: resto agrupado GrupoPrecio with tipo = tipo (e.g. "buy_box_perdida") and motivo param. notifica_precio for producto tipos accepts GrupoPrecio and checks payload.tipo != tipo — GrupoPrecio built with tipo=tipo. OK.

  9. In `aviso_precio_grupo`, FRASEO_TIPO_PRECIO[grupo.tipo] — KeyError if unknown tipo, but sender validates tipo. The builder is public and puro though — KeyError possible if called directly with bad tipo; acceptable.

  10. Timezone: `hoy - dt.timedelta` — hoy is a date presumably. If hoy is a datetime, `dia -= dt.timedelta(days=1)` works for both. SQL compares decision_date (DATE) with %s — if hoy is a datetime, psycopg casts; date vs timestamp comparison works in PG. Fine.

  11. `motivo_precio_es` for `fee_error:<code>` — code could contain error details? partition takes code after colon; "colapsa al codigo corto (el cuerpo del error jamas viaja)". If format is `fee_error:<code>:<body>`, partition(":")[2] returns "code:body" — everything after FIRST colon, which could include the body! `motivo.partition(":")` splits on first colon; [2] is the remainder including any subsequent colons. So `fee_error:500:Internal Server Error detalle...` → codigo = "500:Internal Server Error..." — the body WOULD travel into the Telegram message. The docstring says the body never travels. Need to verify the actual format of fee_error motivos in the codebase. Let me grep.

  Let me check app/precio for motivo formats and the corrida hook signature.

• The `avisar` hook contract confirmed: `(conn, platform, hoy, resumen)` with `resumen.decisiones` and `resumen.huerfanas`. Matches `avisar_precio`.

  Now the `fee_error` format: check where motivo with "fee_error" prefix is generated in precio. Grep in app/precio and estimacion for motivo strings like f"fee_error:{...}".fee_error format is `fee_error:<code>` where code is like `fee_http_429` — single colon, so partition(":")[2] gives the code. Codes come from `error_code` (cot.error_code). Could error_code contain a colon? error_code comes from estimacion_fees: `"fee_error"`, `str(exc) if str(exc).startswith("fee_")` — exc string could theoretically contain anything, but codes look like `fee_http_429`. Fine, minor.

  Now check other concerns:

  1. Double-notification frenado(no_confirmado): group aviso for (frenado, no_confirmado) AND per-product no_confirmado aviso. Let me check the spec — check the plan for A.6 to see if intended. Actually, the tests exist; the mutantes doc mentions this. This is arguably by design (group tells count, product gives specifics). But it means the same event sends two messages. It's a potential quality concern, not clearly a bug. Let me check the spec quickly for "no_confirmado" in the repricing spec.

  2. `_SQL_PRECIO_BUYBOX_DIA` selects 6 columns: COALESCE, seller_sku, external_id, resultado, p_actual, p_actual_currency. Unpacked as `_id, sku, asin, estado, p_actual, moneda`. OK.

  But wait — in `_avisar_buybox_precio`, the key `r[0]` is COALESCE(seller_sku, external_id), which is not unique per listing (two listings could share seller_sku or have same external_id? external_id unique per platform via UNIQUE(platform, external_id)). seller_sku could repeat across listings (multilisting: mismo SKU, varios ASIN). The dict `{r[0]: r}` collapses listings sharing seller_sku — one listing's buy box loss could hide another's. Minor data issue; also flanco compares keys, so if listing A had bb lost yesterday and listing B (same sku) today, no aviso. Edge case; NO BLOQUEANTE.

  3. `aviso_precio_producto`: when `precio_antes is not None and precio_despues is not None` — else precio = despues or antes. If both None → precio None → no line. OK.

  4. `notifica_precio` for tipos producto accepts GrupoPrecio — but GrupoPrecio's motivo for the agrupado resto is passed as "buy_box_perdida"/"no_confirmado" which are in MOTIVO_PRECIO_ES. OK.

  5. `avisar_precio` reads config via `_SQL_PRECIO_CONFIG` — `ORDER BY id DESC LIMIT 1`, matches "config viva vigente es la fila mas reciente". OK.

  6. `flanco_nuevos(sorted(grupos_hoy), sorted(grupos_ayer))` — sorting tuples (resultado, motivo); fine.

  7. In `_avisar_grupos_precio`, for `no_evaluado`, the umbral query uses `hoy - dt.timedelta(days=umbral)` and BETWEEN inclusive — that's umbral+1 days window. flanco_umbral counts racha backward from hoy within that window; racha==umbral required. If racha actual is umbral but day hoy-umbral (just outside) also present → racha would be umbral+1 in reality but window caps... wait: window includes hoy-umbral. racha counts hoy, hoy-1, ..., while in presentes. If days hoy..hoy-(umbral-1) present (racha=umbral) and hoy-umbral absent → racha=umbral → True. If hoy-umbral also present → racha=umbral+1 → False (correct, ya aviso ayer). Correct.

  Edge: racha > umbral but with the aviso day missing (e.g., umbral=3, aviso deberia haber salido el dia X pero la corrida no corrio ese dia). Then today racha=4 → no aviso nunca. Residual; declared in docstring partially ("si una corrida cae... no avisa"). OK.

  8. Type annotations: `def flanco_nuevos(hoy, ayer) -> list` — fine.

  9. `GrupoPrecio.skus: tuple` untyped param — fine.

  10. `validar_precio_aviso_dias(settings)` — settings from JSONB is dict. `Decimal(str(valor))` where valor could be float 3.5 → not integral → ValueError. bool check before. OK.

  11. Security: text includes SKU/ASIN/precios — no secrets. Telegram text plain. OK.

  12. `notifica_precio` checks `canal_activo()` AFTER building the text — builders are pure so fine.

  13. In `_avisar_noconf_precio`, `ult` query per listing inside loop — N+1 but capped... not capped actually; piezas loop over flanco nuevos, could be many listings each with a query. Minor perf.

  14. `_SQL_PRECIO_ULTIMO_NOCONF` filters `c.estado = 'no_confirmado' AND c.aplicado AND NOT c.es_reversa` — matches docstring.

  15. Duplicated frenado aviso: group (frenado, no_confirmado) aviso + per-product no_confirmado. Let me quickly check the spec file for the intended behavior. Search docs for repricing spec A.6 or the plan file.

repricing-01.md
256:  `pendiente → enviado | error` y `enviado → confirmado | no_confirmado`.
258:- **Sin insumo, sin decisión**: `no_evaluado(motivo)` es una fila.
264:- **Cuota propia** `precio:<platform>`; `no_confirmado`, `error` y reversas
287:| A.2 | [stage:implementacion] [lane:gate] [tdd:required] **Reglas puras** (Muse): `app/precio/{tipos,reglas,objetivo,ventas,config}.py`; `objetivo` con forma cerrada (`ref`, `fijo` de `fee_details`, `L` por canal) y ≤ 2 cotizaciones reales; `ventas` con días cubiertos, inventario y racha; `config` con cotas. Banco de pruebas con los fixtures de la acta. | Rojo-primero por regla de S4 con bordes: insumo faltante → motivo exacto; divergencia 1.01% sí y 0.99% no; goal 30% con `m = 29.50%` → `mantener` y `29.49%` → `subir`; `P*` hacia arriba con `m(P_aplicado) ≥ goal` y tope hacia abajo (`116.01·1.10 → ≤ 127.61`); `tax_amount` → `impuesto_fee_pendiente`; no concilia → `fee_error`; `u60=600, u15=90` no dispara y `89` sí; ventanas `[hoy−15,hoy−1]` y `[hoy−75,hoy−16]`; hueco de ledger, `u60=19`, `n15=9`, 74 días, un día sin stock, listing inactivo, racha 2 de 3 → cada uno su `sin_dato`; subida confirmada hace 10 días + caída → `frenado(perdiendo_tras_subida)`; `no_confirmado` de hace 6 días → `mantener(cooldown)`; 3 sin converger → `frenado`; `P* > 2P` y `P* ≤ C + L`; prioridad registrada; `shadow` igual con `aplicado=false`. Todo en `Decimal`. Catálogo de mutantes propio y test de pureza sobre `app/precio/*` | A.0 | cc:完了 `39cba88` (PR #299). Salvedades: `bajar` también verifica con cotización real (más allá de S4 #4); escenario incoherente → `no_evaluado(escenario_incoherente)` con candado de dirección; cobertura del ledger según S2 (≤ 3 días); el cupo solo lo consumen `subir`/`bajar` en `live`; ninguna clave `precio_*` sembrada todavía (sin ellas `leer_config` falla ruidoso); la rama de `bajar` casi no se dispara con el volumen de hoy (residual del spec); docstrings con acentos contra AGENTS.md (cosmético). **v1.3**: `L` por canal NO está en las reglas (`canal` viaja en `EntradaDecision` y nadie lo lee; `L` es el importe que pone quien arma el escenario: E.3/E.4); literales del spec fijos en código, no config: 22 días del freno, `n15 < 10`, 75 días de historia, tolerancia de coherencia 0.01; `repartir_cupo` revienta con monedas mixtas y no valida plataforma (A.5 no mezcla plataformas en una llamada); monedas fuera de MXN/USD → `escenario_incoherente` (MeLi necesita su mínimo absoluto); los frenos #6 y #10 ignoran cambios anteriores al goal vigente (`goal_vigente_desde`); `no_converge` con distancias iguales cuenta como «sin acercarse»; entrada principal `decidir(entrada, *, hoy, config, cotizaciones) -> Decision | PideCotizacion` |
288:| A.3 | [stage:implementacion] [lane:gate] [tdd:required] **Cliente de escritura, escritura y reversa de Amazon** (Muse): `app/spapi/write_client.py` (default-deny, `patch_listing` con `validar_patch_listings`, 401 un refresh, 429 un reintento, sin instancia compartida); `app/spapi/precio_write.py` (`leer_precio_vivo`, `cambiar_precio` con ack → `enviado`, readback informativo, `cerrar_por_observacion`, `revertir`); `tools/precio_reversa.py --cambio-id …`. Commits: write client → reversa → escritura. | Rojo-primero con cliente falso: ack `ACCEPTED` + GET viejo → `enviado`; ack error + GET nuevo → `error`; ack ok + GET distinto de ambos → `enviado` con readback ok; 429 agotado → `readback_estado=fallido` y cero escritura extra; observación D+1 igual → `confirmado`, distinta → `no_confirmado`; reversa se cierra por observación; reversa por lote salta el cambio con precio vivo distinto; dry-run no llama al cliente; **tres candados** con fuga sembrada (`httpx.patch` crudo, import del write client, imports de los tools); forma del parche `pendiente_sonda` hasta A.4; el cuerpo nunca se loguea | A.0 | cc:完了 `efc0555` (PR #300). Salvedades: la forma del cuerpo del PATCH sigue `pendiente_sonda` (A.4) y el go del tool aborta antes de insertar; ningún precio se mueve; la reversa NO entra el mismo día del cambio (el índice único de S5 bloquea mientras el original está abierto; entra al día siguiente tras el cierre por observación); `cambiar_precio` no escribe si el precio vivo ya no es el `p_actual` de la decisión; `leer_precio_vivo` usa Pricing (ofertas), no el GET del item (A.4 lo confirma); una `pendiente` huérfana por muerte del proceso entre COMMIT y PATCH se recupera en A.5. **v1.3**: `cambiar_precio(conn, decision_id, *, lector, escritor, construir_cuerpo=None, ahora=None)` exige `conn.autocommit` y una decisión `subir|bajar` en `live`; NO acepta `limitador` (el GET de Pricing previo al PATCH y el readback van sin cubo; `revertir` sí lo acepta): A.5 lo agrega; el dry-run de `precio_reversa.py` sí hace GETs de Pricing por cambio (cumple «no llama al escritor», no «cero red»); la reversa el mismo día SÍ entra si el cambio original está en `error` y el vivo coincide; `cerrar_por_observacion` toma la observación más reciente en `(día_envío, hoy]`, no estrictamente D+1; `FORMA_PARCHE` es una constante de módulo y `construir_cuerpo_parche` levanta incondicionalmente: sellarla (A.4) es escribir el cuerpo real ahí con el importe como `str` (el escritor prohíbe `Decimal`/`float`), cambiar el literal, la guarda (l.134) y el docstring (l.9), y ajustar **los dos** tests que fijan `pendiente_sonda` (`tests/test_precio_write.py` l.557 y l.1141); los `monkeypatch` de `construir_cuerpo_parche` siguen válidos |
290:| A.5 | [stage:implementacion] [lane:gate] [tdd:required] **Corrida diaria** (Muse): `app/precio/corrida.py` (claim en `ads_optimizer_lock` `precio:<platform>` + advisory lock; cierra por observación los `enviado` de ayer; decide para todos los goals vigentes; ordena por prioridad; aplica solo `live` bajo `app/precio/cuota.py` sin importar `app.apply`; en `shadow` cambios virtuales; orden INSERT+COMMIT → PATCH → sello), `app/cli.py precio --platform` y `precio --reporte --desde --hasta`, cron `10 13 * * *` con `flock` y log, claves de config. | Rojo-primero: día sin insumos → N `no_evaluado` y cero escrituras; cuota saturada → `mantener(cuota)` por prioridad con las de Ads intactas; reversas consumen cuota; segunda corrida del día no decide; **dos hilos con PostgreSQL real → exactamente un PATCH**; `shadow` nunca escribe en Amazon y sí consume cooldown; `enviado` de ayer se cierra antes de decidir; freno tras 3 días de `error`; línea de crontab pinzada por test; umbral fuera de cota → `ValueError` al arrancar | A.1, A.2, A.3 | cc:TODO |
291:| A.6 | [stage:implementacion] [lane:gate] [tdd:required] **Pantalla y avisos** (Muse): `/precios` con los cinco bloques de S7 (cobertura arriba), bloque `precios` en `/salud` dentro de `plataformas.<p>`, **un** sender `notifica_precio(tipo, …)` en flanco por racha: por `(plataforma, motivo)` con conteo y 5 SKUs para `no_evaluado`/`goal_inalcanzable`/`frenado`, por producto para `no_confirmado`/`buy_box_perdida`; fail-silent. | Rojo-primero: `/precios` 200 con los cinco bloques y frases con números del fixture; `/salud` cuenta lo mismo que `precio_decision`; 200 productos con el mismo motivo → **un** aviso; builder de texto **sin** costo, margen, goal ni cuerpo de error; un fallo del sender no tumba `correr` | A.5, A.7 | cc:TODO |
293:| R.1 | [stage:revision] [lane:gate] [tdd:required] **Revisión independiente** (kimi sobre un SHA; lead audita): catálogo de mutantes del implementador (una por regla y borde de S4, candados, transiciones, dinero, cuota, lock, cobertura) re-ejecutado con base real; cero sobrevivientes o se cierran con test en el mismo PR. | `docs/evidencia/repricing-01/R.1/` con catálogo, re-mutación y APPROVE de kimi y del lead sobre el SHA | A.6 | cc:TODO |
296:| D.2 | [stage:cierre-pr] [lane:release] [tdd:skip:ops] **Encendido de 3–5 productos MX (FBA)** con go literal y **medición de 30 días en dos cortes** (14: subida; 30: señal de ventas), con `precio --reporte`. | E/D.2 con la salida literal en ambos cortes. «Funcionó» = (a) 100% de `precio_cambio` en `confirmado`; (b) `abs(m_actual − goal) ≤ tol` o `frenado`/`goal_inalcanzable` con motivo; (c) al día 30 `u15` ≥ esperado salvo que haya disparado la rama de pérdida, y esa rama ejercida o declarada «no ocurrió» con números; (d) cero `no_evaluado` sin motivo; (e) Buy Box D y D+1. (a) y (d) en todos y (b) en ≥ N−1 → ampliar; cualquier `no_confirmado` → parar. Decisión literal del dueño | D.1, A.4 | cc:TODO |
317:| E.4 | [stage:implementacion] [lane:gate] [tdd:required] **El motor distingue canal** (Muse): `canal` en la decisión y en el recuadro de cobertura; `precio_goal` admite publicaciones FBM; la corrida evalúa FBA y FBM con la misma regla y distinta `L`; `/precios` y `/salud` muestran el canal y, en FBM, la muestra con su ventana efectiva, sus órdenes y su dispersión. La **rama de inventario queda `sin_dato` por diseño en FBM** y así se muestra: la fuente que la alimenta es de FBA y en FBM nunca se puebla. | Rojo-primero: una publicación FBM con goal y muestra completa produce `subir` con `L` de la muestra en la cuenta; la misma sin muestra → `no_evaluado(envio_sin_historia)`; el recuadro de MX mueve **~10** publicaciones de `fuera_de_alcance` a en alcance y **el resto del subconjunto FBM canónico** queda en `fase_E_sin_historia_envio` **contado** (el número exacto sale de la fuente canónica en A.7, no del 113 del bridge); `/precios` muestra «envío medido, mediana 94.50 MXN sobre 18 órdenes, ventana 90 d al 2026-09-01» y no un número sin origen; en FBM el motivo de inventario es `sin_dato(inventario_no_aplica_fbm)`, nunca una rama que no puede dispararse | E.3, A.7 | cc:TODO |
371:  `estimacion_fee_observation`; cooldown que ignore `no_confirmado`/`error`;
392:| A.6 | `app/api_dashboard.py`, plantillas, `app/notifica.py` (un sender), tests | `notifica_*` existentes intactos |
417:| AC2 | Sin cotización de fees hoy | `no_evaluado(fee_ausente)`, cero escritura, visible en pantalla | A.2/A.5/A.6 |
420:| AC5 | Escritura aceptada, lectura inmediata con precio viejo, observación D+1 distinta | `enviado` hoy; `no_confirmado` mañana; `frenado` + aviso; cero reversa automática | A.3/A.5/A.6 |
421:| AC6 | Reversa de un cambio real por la herramienta | cerrada por observación D+1 en el precio original; control negativo intacto | A.4 |
430:| AC15 | 200 productos con el mismo motivo tres días | un solo aviso con conteo y 5 SKUs, sin costo ni margen | A.6 |
431:| AC16 | Pricing 1.5% distinto de la oferta del escenario | `no_evaluado(precio_divergente)` con ambos precios y horas | A.2/A.6 |
434:| **AC19** | **Producto FBM con 5 envíos** | `no_evaluado(envio_sin_historia)` y cero escritura **si venía de fuera**; si ya estaba dentro, **sigue dentro** hasta caer a 3 (histéresis). Un producto que oscila entre 5 y 7 envíos no debe alternar estado ni avisar en cada corrida | E.3 |
541:  `master`; A.6 cuando A.5 y A.7 lo estén.
• The plan says A.6: "por producto para `no_confirmado`/`buy_box_perdida`". AC5: "no_confirmado mañana; frenado + aviso" — suggests both frenado aviso AND no_confirmado aviso may coexist? Ambiguous. The group aviso for (frenado, no_confirmado) and the per-product no_confirmado aviso would both fire. Actually AC5 says "frenado + aviso" — the frenado gets an aviso. And per-product no_confirmado aviso too. This seems to be intended design (the spec lists both types). So not a bug; at most a noise concern. I'll note it as NO BLOQUEANTE.

  Now check remaining verifiable items:

  - `_avisar_grupos_precio`: the `nuevos` flanco for goal_inalcanzable/frenado compares (resultado, motivo) keys — but the aviso is "por (plataforma, motivo)" per spec, while flanco key is (resultado, motivo) within one platform call. Fine since per-platform invocation.

  - `motivo_precio_es` startswith("fee_error") — a bare "fee_error" without colon → codigo "" → "falla de comision". OK.

  - One more check: `TIPOS_PRECIO` defined but seemingly unused (only GRUPO/PRODUCTO used). Cosmetic.

  - `aviso_precio_grupo` uses `grupo.total` for "y N mas" computed as total - len(skus shown). If total < len(skus) (caller passes inconsistent), resto negative → no line. Fine.

  - `_enviar_productos_precio_con_tope`: `skus` for the agrupado uses `(p.sku or p.asin)` — asin here is external_id, ok.

  - Tests exist? The diff only includes notifica.py + mutantes doc. Tests referenced (test_precio_aviso_*) presumably in another commit/file not in this diff (limited to task files). Let me quickly verify tests exist and pass? The user asked review only; running the full suite is heavy. Maybe just check test file exists.

  Also check line 1411: `sorted(grupos_hoy)` where keys are tuples (resultado, motivo) — motivo could be "desconocido" fallback; fine.

  One more potential issue: in `_avisar_grupos_precio`, `nuevos = flanco_nuevos(...)` computed from sorted keys — flanco_nuevos expects iterables of keys. sorted(dict) gives sorted keys. OK.

  `_SQL_PRECIO_BUYBOX_DIA`: `d.buy_box_is_own = false` — excludes NULL. Fine.

  `_avisar_buybox_precio` estado: passes `estado` = d.resultado (e.g. "subir"/"mantener"), estado_precio_es translates. OK.

  Edge: `flanco_umbral` dia could be datetime if `hoy` datetime; `hoy` comes from corrida as `ahora.date()`. In tests maybe date. OK.

  Check `Decimal(str(valor).strip() if isinstance(valor, str) else str(valor))` — redundant but harmless.

  Potential real issue: `notifica_precio` tipo check happens inside try; `payload.tipo != tipo` — HuerfanaPrecio has default tipo="huerfana_sin_patch". OK.

  Another: in `avisar_precio`, if `fila is None` returns 0 — skips huerfana aviso even though huerfanas don't need config. In practice config_version always has rows (created at setup). Minor.

  Also `_SQL_PRECIO_GRUPOS_DIA` includes resultado 'frenado' — group aviso "frenados" for all frenado motivos including no_confirmado. Double aviso per product for frenado(no_confirmado) — as discussed.

  Check whether the flanco for grupos uses `nuevos` but for no_evaluado groups present today with racha==umbral — even if also present ayer, it avisos at umbral crossing. Correct.

  Let me quickly check tests exist for these functions to confirm the "prueba que no discrimina" angle is covered — they ran mutantes, evidence exists. Good enough.

  One more: `HuerfanaPrecio` aviso says "revisar el log y revertir si el precio se movio" — fine.

  `_avisar_huerfana_precio(platform, resumen)`: `resumen.huerfanas or 0` — huerfanas=0 → falsy → 0. OK.

  Check `_grupos_precio_dia` — sku could be None (COALESCE of two NULLs impossible since external_id NOT NULL). OK.

  Verify the tests referenced exist, quickly grep one test name.

• Only 4 of the 4 patterns matched, all in test_precio_pantalla.py? Wait, count_matches counts lines matching the regex (any of the 4 alternatives). 4 occurrences in one file — could be each pattern once, or repeated. Some aviso tests might live in tests/test_precio_avisos.py or similar. Let me check which file(s) contain notifica_precio tests.tests/__pycache__/test_precio_pantalla.cpython-314-pytest-9.1.1.pyc
tests/test_precio_pantalla.py
74
All aviso tests live in tests/test_precio_pantalla.py (74 tests). Good — tests exist.

  Enough verification. Summary of findings:

  1. NO BLOQUEANTE — doble aviso por el mismo evento: un producto `frenado(no_confirmado)` dispara tanto el aviso de grupo (frenado/no_confirmado) en `_avisar_grupos_precio` como el aviso por producto en `_avisar_noconf_precio`. El plan A.6 lista ambos tipos, asi que parece deliberado, pero el dueno recibe dos mensajes por el mismo hecho. VERIFICAR intencion.

  2. NO BLOQUEANTE — `_avisar_buybox_precio` clavea por COALESCE(seller_sku, external_id); seller_sku no es unico por plataforma (multilisting), asi que dos listings con el mismo SKU colapsan en el dict {r[0]: r} y el flanco puede esconder la perdida de buy box de uno de ellos.

  3. NO BLOQUEANTE — `avisar_precio` devuelve 0 sin avisar huerfanas si `config_version` esta vacia (fila None) o si el umbral... no, umbral invalido solo apaga no_evaluado. Pero `fila is None -> return 0` salta tambien el aviso de huerfanas, que no depende de config. En la practica siempre hay config_version.

  4. NO BLOQUEANTE — `motivo_precio_es` con `fee_error:<code>` usa partition(":")[2], que arrastra todo tras el primer ':'; los codigos actuales (`fee_http_429`, etc.) no tienen ':' ni cuerpo de error, asi que hoy no hay fuga, pero la defensa contra "el cuerpo del error jamas viaja" depende del formato del emisor.

  5. NO BLOQUEANTE — `validar_precio_aviso_dias`: expresion redundante `str(valor).strip() if isinstance(valor, str) else str(valor)` (cosmetico).

  6. NO BLOQUEANTE — TIPOS_PRECIO definido pero no se usa (solo GRUPO/PRODUCTO y el literal huerfana). Cosmetico.

  7. NO BLOQUEANTE — N+1: `_avisar_noconf_precio` hace un SELECT por listing en flanco; acotado por el volumen diario.

  Any BLOQUEANTE? Security: no secrets in messages; scrub applied. Data: schema columns verified. Behavior matches plan. Tests exist and mutantes evidence claims all dead. No blocking finding with reproduction.

  One more consideration: `_SQL_PRECIO_BUYBOX_DIA` returns rows for all decision results with buy_box_is_own=false — including no_evaluado rows? buy_box_is_own could be false on a no_evaluado row. Then aviso "buy_box_perdida" with estado "no_evaluado"? Possible but semantically odd. Minor — fold into #2 or separate. I'll mention briefly.

  Respond in Spanish, plain text, numbered list with BLOQUEANTE/NO BLOQUEANTE.

To resume this session: kimi -r session_e457169a-63c7-401e-8ee0-dad067044bc4


=== Revisor efectivo: kimi (binario 'kimi') ===


---
sha=510beda33015edaba4c0aaddf2471974851ffc15 archivos=app/notifica.py,docs/evidencia/repricing-01/A.6/mutantes.md inicio=2026-09-19T02:54:54Z fin=2026-09-19T03:00:43Z exit=0
