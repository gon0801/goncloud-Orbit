# Plans — ORBIT 03: PR1 optimizador — SHADOW completo (cero escrituras a Amazon)

> Purpose: dejar el optimizador de Sponsored Products decidiendo EN SOMBRA
> sobre datos reales: ingesta Amazon Ads (estructura + métricas + search
> terms, us y mx), motor de decisiones puro con las reglas selladas del
> diseño v2, ciclo auditable (`optimizer_cycle` + `decision`), API de
> lectura, cron 8:40 UTC y escalera off→shadow→live fail-closed.
> **Cero escrituras a Amazon: el cliente HTTP de PR1 no tiene camino de
> mutación de campañas.** Registro: tarea `ORBIT 03` en EHV Tasks (AppFlowy).
>
> Plan validado por revisión de perspectivas (team_validation_mode:
> subagent — Product/Skeptic, Architecture/QA, Security; 2026-08-22).
> Los hallazgos altos están incorporados como tareas/DoD de este plan.
>
> Fuente de verdad de reglas y umbrales: `docs/traspaso/ADS_OPTIMIZER_V2_DESIGN.md`.
> Traducciones viejo→Orbit ya selladas en `docs/CONTEXTO.md` + `docs/DATABASE.md`:
> `ads_optimizer_audit`→`decision`; `bid_cache`+`ad_campaigns`→`ad_entity_state`;
> `motor_cycle_envelopes`→`optimizer_cycle`; `system_alerts` NO existe →
> `status='degraded'`+`notes`; "upsert replace-por-fecha"→append-only
> bitemporal con dedupe por `source_report_id` (regla 5 manda);
> `MOTOR_SETTINGS_SCHEMA`→`config_version.settings` + `ads_optimizer_goal.mode`.
> Roles: los del esquema (`app_ingest`/`app_decide`/`app_read`/`app_admin`)
> son NOLOGIN; los procesos conectan con los usuarios LOGIN `orbit_*` de
> ORBIT 01 (`orbit_ingest`→`app_ingest`, etc. — ver `docs/DEPLOY.md`).
>
> **Spec delta a `docs/CONTEXTO.md`** ("Cómo leer el diseño v2"; lo aplica 2.1
> en su mismo PR — tres traducciones nuevas):
> 1. **Doble ventana.** Bids: ventana 30d que termina en `max(metric_date) − 3d`.
>    Cortes (pause/negative/harvest): agregado SEPARADO cuya ventana termina en
>    `min(max(metric_date) − 3d, decided_at − 10d)` — madurez ≥10d, es decir
>    `window_end <= decided_at − 10d` (regla 6; el trigger
>    `decision_madurez_corte` lo hace imposible de violar). Prohibido calcular
>    con la ventana de bids y solo "bajar" la columna `window_end`.
> 2. **Opt-out por campaña.** `campaign_optimization_state` del viejo no
>    existe en Orbit: se traduce a goal de scope campaña con `enabled=false`,
>    que PISA a un goal de plataforma habilitado. La gracia de 7d por
>    reactivación manual sigue diferida a PR2 (residual #3 del diseño).
> 3. **Criterio de shadow para el cutover.** "Revisado contra lo que hizo
>    adaptive" ya no aplica: el sistema viejo está APAGADO (ORBIT 02). El
>    shadow se valida contra datos reales + recálculo manual, y se acepta el
>    costo declarado de que nadie optimiza las campañas durante el freeze.
>
> Datos: el histórico se re-jala de la FUENTE EXTERNA por el MISMO pipeline
> de ingesta (reglas 1 y 10). **NO se migra `competitive.db`** para métricas
> de ads (ver Reject). MeLi queda fuera: el optimizador es SP de Amazon.
>
> Anti-doble-conteo (regla 5, hallazgo alto de revisión): las tablas de
> métricas son bitemporales — el mismo `(entidad, metric_date)` tiene N
> observaciones. El motor SIEMPRE colapsa a la última observación por fecha:
> bids vía `v_metric_latest`; search terms (no hay vista) vía
> `DISTINCT ON (ad_entity_id, search_term, metric_date) ORDER BY observed_at DESC`.
>
> ACoS sellado (trampa del halo): **numerador = `cost / ad_revenue` COMPLETO**
> (`revenue_same_sku` solo atribuye, jamás entra al ACoS) — CONTEXTO.md manda.
>
> Ejecución: contra la base viva de goncloud se trabaja por túnel SSH
> (`ssh -L 5432:127.0.0.1:5432 goncloud`, gotcha del túnel muerto en
> `docs/DEPLOY.md`). Los tests de integración heredan el auto-skip por
> `ORBIT_TEST_DSN` de `tests/test_schema.py` (corren local con túnel vivo);
> la batería completa corre EN CI vía PR. Cada phase cierra con PR propio.
>
> unknowns declarados (`not_observed != absent`; las tareas los verifican):
> - lookback máximo real del reporting v3 por report type (1.5 lo mide; su
>   DoD incluye el gate de contingencia)
> - qué perfiles (us/mx) están activos y con campañas SP (1.2 lo lista)
> - nombres/formato exactos de los archivos en
>   `/mnt/data/appdata/orbit/secrets/` (candado secret-read; 1.1 los lee
>   tras la aprobación — el inventario vive en el server, no en el repo)
> - puerto libre para la app en goncloud (4.1 lo verifica; propuesta 8010)

## Phase 1 — Ingesta Amazon Ads [lane:gate]

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 1.1 | Cliente HTTP Amazon Ads READ-ONLY: refresh LWA (config/tokens desde `secrets/`), retries 429/5xx con backoff+jitter, timeouts, y **sin ningún método de mutación de campañas** (la única escritura permitida es el POST de report-request: crea un reporte, no toca campañas). **Redacción centralizada de secretos**: `Authorization`, `client_secret`/`refresh_token`, password del DSN y URLs firmadas de descarga jamás aparecen en logs/excepciones. Decisión sellada del refresh: el refresh_token LWA no rota → refresco por proceso es aceptable; un solo módulo cliente lo encapsula (la lección "un solo refrescador" era por los tokens rotativos de MeLi). Dependencias nuevas pinneadas en lockfile commiteado. `[tdd:required]` | Tests con transporte mock: token vencido→refresh→retry; 429→backoff→éxito; 5xx agotado→error. Test de redacción: ningún secreto en logs/repr de excepciones (incluye DSN fallando conexión). Test de superficie: el cliente no expone mutación de campañas. Lockfile en el repo; CI verde | - | cc:完了 |
| 1.2 | Sync de estructura → `ad_entity` (append de entidades nuevas) + `ad_entity_state` (estado mutable: bid+moneda, status, targeting_type, acos_target publicado, `synced_at`): perfiles → campañas SP → ad groups → keywords/targets, us y mx. Escribe como `orbit_ingest`. Deja evidencia de qué perfiles/campañas existen (unknown resuelto). `[tdd:required]` | Test de integración (Postgres, auto-skip por `ORBIT_TEST_DSN`) con fixtures de payloads: filas coherentes, moneda por plataforma, re-sync actualiza estado sin duplicar entidades. Corrida real contra goncloud: counts por (plataforma, kind) anotados en AppFlowy | 1.1 | cc:完了 |
| 1.3 | Pipeline reporting v3 asíncrono → `ads_metric_observation`: request/poll/download por (perfil, rango), inserción append-only con `observed_at=now()`, dedupe por `(ad_entity_id, metric_date, source_report_id)`, moneda LOCAL (el trigger de sello la valida), `ingest_run` con `rows_written`/`rows_skipped` y **`skip_reason` obligatorio cuando `rows_skipped > 0`**, y validación EN INGESTA de `observed_at >= metric_date` (no hay CHECK: es responsabilidad declarada del código). `[tdd:required]` | Integración CI: mismo reporte dos veces → segunda corrida todo `rows_skipped` con `skip_reason` poblado; lote MIXTO (nuevas+dedupe) → `rows_written+rows_skipped` cuadra; fila con moneda cruzada rechazada por trigger; payload con `observed_at < metric_date` rechazado por la ingesta (test demostrado fallando sin la guarda — regla 9). Test de privilegio negativo: `orbit_ingest` NO puede INSERT en `decision` | 1.2 | cc:完了 |
| 1.4 | Search terms report → `search_term_observation` con `is_asin_like` SIEMPRE clasificado (NOT NULL sin default: el clasificador es obligatorio; criterio documentado junto al código — patrón ASIN `B0…` de 10 alfanuméricos, case-insensitive). `[tdd:required]` | Tests del clasificador (ASIN real, asin en minúsculas, término normal, término con "b0" embebido); integración CI: fixture con mezcla → clasificación correcta | 1.3 | cc:完了 |
| 1.5 | Backfill histórico por el MISMO pipeline: us y mx, desde el lookback máximo que la API permita (medirlo y documentarlo). Corre por túnel o en el server; escribe como `orbit_ingest`. NO se toca `competitive.db`. **Gate de contingencia**: con el lookback medido, declarar qué tipos de decisión estarán disponibles en el primer shadow — un corte exige ≥7 fechas en la ventana madura (termina en `decided_at − 10d`); si el lookback no las junta, se anota qué kinds quedan en espera y desde cuándo, ligado al reloj de ORBIT 05. Verificar también el GRANO del reporte de search terms: si un mismo (ad_group, término, fecha) llega en varias filas (vía keywords) — RESUELTO con la corrida: grano múltiple confirmado en vivo y el planificador FUSIONA las filas de la misma clave sumando métricas (docs en `app/ads/reports.py`; hallazgo codex 1.4). `[tdd:skip:corrida-de-pipeline-ya-testeado]` | Counts por (plataforma, metric_date) continuos dentro del lookback real (huecos que reporte Amazon se anotan, no se rellenan — regla 3); watermark por plataforma y la tabla lookback→kinds disponibles documentados en AppFlowy | 1.3, 1.4 | cc:完了 |

## Phase 2 — Motor de decisión puro [lane:gate]

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 2.1 | Ventanas y guardas: ventana 30d por entidad **colapsando SIEMPRE a la última observación por fecha** (bids: `v_metric_latest`; terms: `DISTINCT ON` — ver header). Doble ventana del Spec delta: bids terminan en `max(metric_date) − 3d`; cortes usan agregado SEPARADO que termina en `min(max(metric_date) − 3d, decided_at − 10d)`. Completitud: ≥7 fechas distintas por ENTIDAD en su ventana (unidad sellada: la completitud es de la entidad; la evidencia por término la dan sus propios umbrales de clicks/cost — exigir 7 fechas por término mataría los negativos long-tail). Plataforma saltada si watermark > 7d o `synced_at` > 48h (ciclo `degraded`/`skipped` + motivo). Incluye aplicar el **Spec delta completo** (3 bullets) a `docs/CONTEXTO.md`. `[tdd:required]` | Tests: cada guarda dispara con su fixture; dos observaciones del mismo `(entidad, fecha)` con `source_report_id` distinto → la ventana usa UNA (sin doble conteo, en métricas Y en terms); decisión de corte generada con ventana de bids → rechazada por `decision_madurez_corte` (acople motor↔trigger); en decisiones de corte ningún `metric_date` de `inputs` > `window_end`; CONTEXTO.md actualizado en el mismo PR | 1.5 | cc:完了 |
| 2.2 | Bid engine PURO (sin IO), **ACoS = cost / ad_revenue COMPLETO** (halo incluido; `revenue_same_sku` jamás): PAUSE (orders=0 ∧ clicks≥25 ∧ cost≥{us:12 USD, mx:200 MXN}); −25% (ACoS>1.35×target, orders≥1); −12% (ACoS>1.15×target); +15% (ACoS<0.85×target ∧ orders≥3); clamp por decisión [−30%,+20%], resultado [floor,ceiling], \|Δ\|<0.01 → no-op. Precedencia de bandas explícita: PAUSE gana a cualquier ajuste; −25% gana a −12%. `[tdd:required]` | Tests de acoplamiento numérico sellados: ejemplo del diseño (target 25, bid 1.00, ACoS 36 → 0.75; floor 0.80 → 0.80); bordes EXACTOS de cada umbral por mercado (1.35×, 1.15×, 0.85×, 25 clicks, 12/200 cost, orders=3); precedencia (entidad que cumple PAUSE y banda → PAUSE; ACoS que dispara −25 y −12 → −25); Δ=0.009 no-op; test que FALLA si el ACoS usa `revenue_same_sku` (regla 9); test documental de que el clamp −30/+20 es hoy inalcanzable (máximo −25/+15) | 2.1 | cc:完了 |
| 2.3 | Hygiene PURO sobre terms colapsados: NEGATIVE_EXACT (orders=0 ∧ clicks≥20 ∧ cost≥{us:8, mx:130}; ASIN-like SIEMPRE skip); HARVEST (orders≥2 ∧ ACoS ≤ min(35%, target); exige `harvest_campaign_id`+`harvest_ad_group_id`+`harvest_default_bid` en el goal — config incompleta → skip CON MOTIVO, jamás placeholder; dedupe contra keywords existentes en la campaña destino vía `ad_entity`). Ambos kinds usan la ventana madura de cortes (2.1). `[tdd:required]` | Tests: umbrales exactos por mercado; ASIN-like skip; harvest sin config → skip con motivo auditable; harvest duplicado → skip; término con <7 fechas propias dentro de una entidad completa → SÍ decide si cumple sus umbrales (unidad de completitud sellada); entidad con <7 fechas → todos sus términos skip | 2.1 | cc:完了 |
| 2.4 | Goals y modo efectivo: precedencia campaña > plataforma **incluido `enabled`** (goal de campaña `enabled=false` PISA plataforma habilitada — es el opt-out del Spec delta); cascada de target ACoS: goal → `config_version` `ads_target_acos_pct_<platform>` → `ad_entity_state.acos_target` → default 55; floor/ceiling defaults 0.10/2.50. Modo efectivo = escalera global en `config_version` ∧ `goal.mode`, con **fail-closed de PR1: `live` degrada a shadow + nota**. Cooldown 7d como query sobre `decision_application.verify_ok IS TRUE` — en shadow nunca enfría, pero existe y se testea. `[tdd:required]` | Tests: cascada peldaño por peldaño; plataforma `enabled` + campaña `enabled=false` → entidad EXCLUIDA; live→shadow degradado con nota; cooldown con fixture sintética de `decision_application` (apply verificado hace <7d → excluida; divergencia `verify_ok=false` → NO enfría) | 2.1 | cc:完了 |

## Phase 3 — Ciclo shadow + API de lectura [lane:gate]

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 3.1 | Orquestador del ciclo: claim atómico (`ads_optimizer_lock`, `job_key = 'ads_optimizer:<platform>'`, TTL 30 min, heartbeat renovado, expiración evaluada al tomar — nunca SELECT-luego-INSERT); envelope `optimizer_cycle` por plataforma (nace `running`, cierra `done`/`degraded`/`skipped`/`failed`); **skips por entidad/término persistidos estructurados en `optimizer_cycle.notes`** (JSON: contadores + razones — la fuente que 4.4 cita); elegibilidad (goal habilitado con precedencia 2.4 ∧ `ad_entity_state.status` ENABLED); decide con el motor puro y escribe `decision` con `inputs` congelados (métricas colapsadas de la ventana, goal resuelto, `config_version_id`, `window_start/end`, `data_observed_at`). Escribe como `orbit_decide`. `[tdd:required]` | Integración CI (fixtures siembran su propia `config_version` + goals): ciclo shadow completo → decisiones esperadas EXACTAS; claim: lock vigente de otro owner NO robable, lock con TTL vencido SÍ reclamable (con rastro del ciclo muerto), dos owners concurrentes → uno gana (atomicidad); `decisions_count` cuadra contra `decision`; **golden test de replay**: re-alimentar `decision.inputs` al motor puro reproduce `kind`/`new_value`/`value_currency` idénticos; test de privilegio negativo: `orbit_decide` NO puede escribir `ads_optimizer_goal` ni `config_version` | 2.2, 2.3, 2.4 | cc:完了 |
| 3.2 | Router de SOLO LECTURA `/api/ads-optimizer/{status,audit,goals}` (GET, como `orbit_read`): `status` (último ciclo por plataforma + watermarks + skips del notes), `audit` (decisiones paginadas, filtros por ciclo/entidad/kind), `goals` (lectura). **Sin endpoints de escritura en PR1** (hallazgo Security: un endpoint sin auth en host compartido no puede portar `orbit_admin`): la escalera y los goals se escriben por el camino humano de 4.3; `/run` y `/goals` write llegan en PR2 con auth propia. La app SOLO escucha en 127.0.0.1 del server. `[tdd:required]` | Tests httpx de los 3 endpoints (paginación, filtros, 404s); test de superficie: el router no registra ningún método de escritura; smoke real en 4.1 | 3.1 | cc:完了 |
| 3.3 | CLI para cron y operación manual: `python -m app.cli {ingest,cycle}` — invoca exactamente el mismo camino que el orquestador (mismo claim/`job_key`, mismo envelope); el disparo manual del ciclo es este CLI vía ssh (no hay `/run` en PR1). `[tdd:required]` | Test unitario: el CLI llama al mismo orquestador (no duplica lógica) y usa el mismo `job_key` que el cron; `--help` documenta ambos subcomandos | 3.1 | cc:完了 |

## Phase 4 — Deploy, seed humano, primer shadow real y cierre [lane:release]

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 4.1 | Servicio `app` en el compose de deploy (`/mnt/data/appdata/orbit/`): imagen construida desde el repo CON el lockfile pinneado, uvicorn en `127.0.0.1:<puerto libre verificado>` (propuesta 8010), DSNs por servicio desde `.env` del server, SOLO `secrets/` montado read-only y **`user:` del contenedor = uid dueño de `secrets/`** (0600 se conserva; regla en DEPLOY.md: jamás relajar permisos). Puertos NUNCA en `0.0.0.0` (lección 8055/8056). `docs/DEPLOY.md`: cómo reconstruir app+DB desde cero. `[tdd:skip:infra-no-code]` | `curl 127.0.0.1:<puerto>/health` OK en goncloud; `ss -lntp` muestra el puerto SOLO en 127.0.0.1; el contenedor lee `secrets/` sin haber tocado permisos; DEPLOY.md responde "¿cómo reconstruyo esto?" | 3.2, 3.3 | cc:完了 |
| 4.2 | Crons en goncloud (crontab de `gon`, junto a los de accounting que NO se tocan): estructura 06:45 UTC, métricas+search terms 07:10 UTC, optimizador 08:40 UTC — vía `docker compose exec app python -m app.cli ...`, cada job con su `job_key` propio (`ingest:structure`, `ingest:metrics`, `ads_optimizer:<platform>`). La tirada de métricas+search terms RE-INCLUYE la ventana de maduración de la atribución (mínimo D-8..D-1 por los 7d de search terms; profundidad de las 30d se sella aquí con el lookback de 1.5 — hallazgo codex 1.4, nota en `app/ads/reports.py`: sin re-tirada, cada día congela conversiones inmaduras y los cortes deciden sobre datos incompletos, regla 6). Registrados en DEPLOY.md. `[tdd:skip:infra-no-code]` | `crontab -l` muestra los 3 jobs nuevos Y conserva intactos los de accounting; una corrida manual de cada job sale 0 y deja su rastro (`ingest_run` / `optimizer_cycle`) | 4.1 | cc:完了 |
| 4.3 | **CHECKPOINT HUMANO — seed de config y goals piloto** (sin esto el primer shadow decide en el vacío: hallazgo alto ×2 de revisión). Como `orbit_admin` (psql/script desde shell del server — el camino humano, no la API): (a) `config_version` v1 con la escalera global en `shadow` (clave `ads_optimizer_mode`, task 2.4) y `ads_target_acos_pct_amazon_us` / `ads_target_acos_pct_amazon_mx` (nombre COMPLETO del enum platform — corregido tras cross-review; `us`/`mx` solos NO pegan en la cascada); (b) goals piloto por mercado (scope plataforma habilitado + los goals de campaña que el dueño decida, con target/floor/ceiling y config de harvest donde se quiera probar HARVEST). La selección de campañas piloto y sus targets la decide el dueño con la evidencia de 1.2. `[tdd:skip:config-humana]` | `SELECT` muestra `config_version` v1 y los goals piloto; la escalera global está en `shadow`; qué campañas y por qué quedó anotado en AppFlowy | 4.2, 1.2 | cc:完了 |
| 4.4 | Primer ciclo shadow REAL sobre el backfill + revisión humana: spot-check numérico de ≥20 decisiones (us y mx; incluir si existen: un caso de floor, un PAUSE, un skip por harvest sin config, un skip por completitud <7 fechas — citados desde `optimizer_cycle.notes`) recalculadas a mano contra las reglas del diseño. Verificar cero mutaciones con el **log REDACTADO** (método+path+status, sin headers ni query-strings) — la evidencia que va a AppFlowy es esa forma redactada. `[tdd:skip:verificacion-manual]` | Tabla del spot-check (decisión → recálculo → match) en AppFlowy; envelope `done`; cero llamadas de mutación en el log redactado; si el spot-check revela CUALQUIER divergencia → se arregla y se repite antes de cerrar | 4.3 | cc:TODO |
| 4.5 | Cierre: PR final mergeado con CI verde (batería completa en CI); `ORBIT 03` Done en AppFlowy con evidencia completa (counts de backfill, watermarks, tabla lookback→kinds, spot-check, crons, PRs ligados); dejar sellado para ORBIT 05 el criterio de shadow SIN adaptive (Spec delta bullet 3): validación = datos reales + recálculo manual, reloj de "≥2 semanas" corre desde el primer ciclo diario estable, y el freeze sin optimización es costo aceptado desde ORBIT 02. `[tdd:skip:tracker-only]` | Fila `ORBIT 03` en Done con notas que cuentan el trabajo completo; nombre EXACTO existente (no renombrar) | 4.4 | cc:TODO |

## Priorización

- **Required**: 1.1–1.5, 2.1–2.4, 3.1–3.3, 4.1–4.5 (camino mínimo a un
  shadow auditable con datos reales; incluye los fixes altos de la revisión).
- **Recommended**: dentro de 4.4, ampliar el spot-check si los primeros 20
  casos revelan cualquier divergencia (ya en su DoD).
- **Optional**: ingesta de `fx_rate` migrando `currency_rates` del viejo
  (Traspaso 2 §4 — dato verificado limpio, diaria desde 2025-10-31; la
  fuente NO está por decidir). PR1 no la usa (umbrales en moneda local); la
  piden Fase 3 (margin-aware/TACoS) y Repricing. Solo si sobra tiempo.
- **Reject** (con razón):
  - Migrar métricas de ads desde `competitive.db`: la fuente externa es la
    fuente (regla 10) y el backfill usa el mismo camino que la ingesta
    diaria (regla 1). El archive queda como referencia forense.
  - Optimizador para MeLi: MeLi Ads bloqueado a nivel cuenta
    (proposal-only) y estructuralmente incomparable; fuera del diseño v2.
  - Endpoints de escritura (`/run`, `/goals` write) en PR1: sin auth no
    pueden portar `orbit_admin` en un host compartido (hallazgo Security
    alto); llegan en PR2 con auth propia. La escalera es decisión humana
    por el camino de 4.3.
  - AMS/Stream intradía, señales nuevas, budgets/placement: fases 4–5
    (ORBIT 07/08), con la regla "señales, no gates".
  - Módulo apply / apply_quota / harvest con fases: ORBIT 04 (PR2).
  - Redis/colas: la autopsia mata infraestructura por defecto.

## 事前確認

- 事項: secret-read — `ssh goncloud` lectura de `/mnt/data/appdata/orbit/secrets/*` (Amazon Ads LWA config/tokens), `/mnt/data/appdata/orbit/.env` (DSNs) y `credentials-inventory.md`
  理由: la ingesta necesita client_id/refresh_token y los DSN por servicio; se leen nombres/estructura y se cablean en runtime, los valores jamás se imprimen ni entran al repo
  scope: Phase 1 / Tasks 1.1–1.5, Phase 4 / Task 4.1
- 事項: external-send — llamadas a la API de Amazon Ads (LWA token refresh, GETs de estructura, POST de report-request + download): lectura de datos, cero mutación de campañas
  理由: sin llamadas reales no hay estructura, métricas ni backfill (DoD de 1.2/1.3/1.5)
  scope: Phase 1 / Tasks 1.2–1.5, Phase 4 / Tasks 4.2–4.4
- 事項: external-send — `git push` + `gh pr create` (un PR por phase) y merge con CI verde
  理由: el cierre de cada phase y la batería completa corren en CI vía PR
  scope: Phases 1–4 / cierre de cada phase
- 事項: external-send/estado — escritura APPEND-ONLY en la base de producción Orbit de goncloud (backfill 1.5, crons de ingesta, ciclos shadow, seed 4.3). Cero DDL, cero DELETE/UPDATE destructivo; los servicios Postgres, `bridge` y `accounting` no se tocan
  理由: el shadow ES escritura de observaciones y decisiones en la base viva; es su modo de operación permanente
  scope: Phase 1 / Task 1.5, Phase 4 / Tasks 4.2–4.4
- 事項: destructive/estado — editar crontab de `gon` en goncloud (AGREGAR 3 jobs; los de accounting no se tocan) y modificar el compose de deploy (agregar servicio `app`; el servicio de Postgres no se toca)
  理由: DoD de 4.1/4.2 — la app y los crons son el modo de operación permanente del shadow
  scope: Phase 4 / Tasks 4.1–4.2
