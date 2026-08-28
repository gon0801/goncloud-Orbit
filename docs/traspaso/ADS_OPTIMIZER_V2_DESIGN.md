# Ads Optimizer v2 — diseño y fuente de verdad

> Reemplazo simple y accionable del stack bayesian (adaptive/bid/hygiene/capital
> motor) para Sponsored Products. Nació del plan externo "Amazon Ads Optimizer
> (nivel Perpetua)" corregido contra este repo y contra dos rondas de
> cross-review con codex (2026-08-21, 22 + 15 hallazgos — ver
> [Residuales](#residuales-declarados)). Documento autoritativo del sistema;
> cualquier cambio de regla o umbral se refleja acá y en los tests de
> acoplamiento numérico.

## Por qué existe

El stack anterior no actuaba (gates evidenciales + cooldowns + damping
apilados) y era inauditable. Este sistema: umbrales explícitos, una decisión
por entidad por ciclo, todo en `ads_optimizer_audit`, live automático con
topes.

## Qué reutiliza (no reescribe)

| Pieza | Fuente |
|---|---|
| Ingesta date-grained | crons existentes → `search_term_metrics`, `keyword_daily_metrics` (upsert replace-por-fecha — sin doble conteo por diseño) |
| Estructura | `bid_cache` (bid+estado por entidad, cron 6:45 UTC), `ad_campaigns` (targeting_type/status/acos_target, cron 12h) |
| HTTP | `engines/amazon_ads_http.py` (retries 429/5xx; POSTs de creación no-idempotentes fail-closed) |
| Mutaciones (PR 2) | primitivas `_bm_amazon_ads_put_*` de `bid_motor.py` + payloads de negativos/harvest probados + `_parse_single_207` |
| Escalera de modos | `MOTOR_SETTINGS_SCHEMA` + `_STATE_MACHINE_FLAGS` + `_CANARY_GATED_MODE_FLAGS` en `routers/motors_settings.py` |
| Envelope de ciclo | `motor_cycle_envelopes` (motor='ads_optimizer') |

## Módulos

- `engines/ads_optimizer_core.py` — constantes, `ao_mode` (tri-state
  `ads_optimizer_v2_enabled`, default off, canary→live), ensure de las 3 tablas,
  claim de ciclo, resolución de goals.
- `engines/ads_optimizer_windows.py` — ventanas 30d y guardas de frescura.
- `engines/ads_optimizer_bid_engine.py` — decisiones puras de bid.
- `engines/ads_optimizer_hygiene.py` — negativos y harvest (decisiones puras).
- `engines/ads_optimizer_cycle.py` — orquestador (PR 1: shadow-only).
- `engines/ads_optimizer_apply.py` — PR 2 (apply con `apply_quota`, readback,
  harvest con fases y reconciliación).
- `routers/ads_optimizer.py` — `/api/ads-optimizer/{status,audit,goals,run}`.

## Reglas (con acoplamiento numérico sellado en tests)

Bids (ventana 30d por entidad, target en %, costos en moneda LOCAL):

1. **PAUSE**: orders=0 ∧ clicks≥100 ∧ cost≥{us: 40 USD, mx: 500 MXN} (CORTES 03, dueno 2026-08-28; antes 25 / 12 USD / 200 MXN; CORTES 01 habia puesto fallback 50; umbral final = max(100, bruto adaptativo))
2. **−25%**: ACoS% > 1.35×target (orders≥1)
3. **−12%**: ACoS% > 1.15×target
4. **+15%**: ACoS% < 0.85×target ∧ orders≥3
5. Clamp: cambio por decisión ∈ [−30%, +20%]; resultado ∈ [floor, ceiling];
   |Δ|<0.01 → no-op. Ejemplo sellado: target 25, bid 1.00, floor 0.40,
   ceiling 2.50, ACoS 36 → −25% → **0.75**; con floor 0.80 → **0.80**.

Hygiene sobre search terms:

- **NEGATIVE_EXACT**: orders=0 ∧ clicks≥20 ∧ cost≥{us: 8, mx: 130}. Términos
  ASIN-like SIEMPRE skip (regla sealed own-ASIN de `business-rules.md`).
- **HARVEST**: orders≥2 ∧ ACoS ≤ min(35%, target). Requiere
  `harvest_campaign_id` (campaña MANUAL) + `harvest_ad_group_id` +
  `harvest_default_bid` en el goal; duplicados verificados por
  `keyword_daily_metrics` (bid_cache no tiene keyword_text); sin placeholder:
  falta config → skip con motivo.

Guardas del ciclo:

- Elegibilidad: SOLO campañas con goal habilitado (campaña o plataforma) — el
  live nunca alcanza campañas no configuradas. Además `ad_campaigns.status`
  ENABLED y `campaign_optimization_state` 'active'.
- Frescura: ventana termina en `max(metric_date) − 3d`; plataforma saltada si
  watermark > 7d o `bid_cache.synced_at` > 48h (alerta en `system_alerts`).
- Completitud: ≥7 fechas distintas por entidad en ventana.
- Claim: `ads_optimizer_locks` — cron y `/run` comparten job_key; TTL 30 min
  con heartbeat.
- Cooldown 7d: solo cuenta `mode='live' AND applied=1 AND verify_ok≠0` (una
  divergencia verify_ok=0 NO enfría: se reintenta).
- PR 1: modo live sin módulo apply → ciclo degradado a shadow + alerta
  (fail-closed).

Cascada de target ACoS: goal → setting `ads_target_acos_pct_<platform>` →
`ad_campaigns.acos_target` → default {us: 55, mx: 55}. Precedencia de goals:
campaña > plataforma; floor/ceiling igual, defaults 0.10/2.50.

## Fases

- **PR 1 (este)**: shadow completo — decisiones + audit + envelope + router +
  cron 8:40 UTC + flag en escalera. Cero escrituras a Amazon.
- **PR 2**: `ads_optimizer_apply.py` (apply_quota con namespace
  ads_optimizer: caps diarios + reserva PAUSE; audit INSERT+COMMIT → HTTP →
  readback → UPDATE; harvest con fases negative_created→exact_created→done y
  reconciliación al inicio de ciclo; consulta a `cross_motor_cut_guard`) +
  checklist de cutover:
  1. ≥2 semanas de shadow revisadas contra lo que hizo (o no hizo) adaptive.
  2. Goals piloto con floor/ceiling.
  3. `GET /api/measurement/motor-flags` — inventario real de escritores.
  4. Apagar `adaptive_motor_apply_live_*` de los canales tomados,
     `bid_motor_enabled='off'`, `hygiene_motor_enabled='off'`, resolver
     candidatos pendientes de `search_term_candidates`, flags de sku_health.
     Dayparting NO se toca (cron propio `dayparting_apply`, 0/6/14h UTC).
  5. Backup: dump de `bid_cache` + `/sp/keywords/list` + `/sp/targets/list` +
     `/sp/negativeKeywords/list` + `ad_campaigns`.
  6. Caps bajos día 1 → off→shadow→live por la escalera → monitoreo 48h.
- **Fase 3 — Consolidación post-cutover** (absorbe M1+M2 del roadmap externo
  `AMAZON_ADS_OPTIMIZER_ROADMAP_MODULOS.md`): digest diario de decisiones
  (Telegram — creds ya en env del repo), vista de lectura sobre
  status/audit/goals (los endpoints ya existen), negativos ASIN con los
  payloads probados del endpoint apply-approved, margin-aware targets
  (`product_profit`/`_platform_margin_ex_tax`). M2 (ad groups + harvesting
  real) queda SUBSUMIDO en el PR 2. Sin approval queue: la decisión sellada
  del diseño es live automático con topes — la proposal-only con aprobación
  humana era parte del problema del stack viejo; `ads_optimizer_audit` es la
  cola natural si algún día se quiere un modo de aprobación por lote.
- **Fase 4 — Señales, no gates** (absorbe M5 re-escopado): tendencia 7d vs
  14d y confianza por volumen como SEGUNDOS umbrales con comportamiento
  acotado (máximo: abstenerse, nunca re-enrutar), y perfiles por `objective`
  (launch/profit/defense → sets de multiplicadores de las reglas existentes).
  Lección sellada de la autopsia del stack viejo: nada de gates apilados —
  cada señal nueva debe demostrar en shadow que AUMENTA la tasa de acción
  útil, no que la reduce.
- **Fase 5 — Palancas nuevas, una a la vez** (absorbe M3/M4/M6/M7; cada una
  con diseño propio antes de entrar y regla de oro del roadmap: poder
  apagarse sin romper el resto):
  - **Placement (M3)**: la palanca ya vive en PLACEMENT-DIRECTION v2
    (`placement_direction_mode`, cron semanal) — al cutover se decide si ese
    motor sobrevive o se portan sus reglas al optimizador.
  - **Budget/pacing (M4)**: solapa con dayparting (live, 0/6/14h UTC) — el
    optimizador NO toma budgets en el corto plazo (dos sistemas moviendo el
    mismo presupuesto = conflicto); a lo sumo vista de pacing + alertas.
  - **Frecuencia intradía (M6)**: el ciclo ya es seguro para N corridas/día
    (claim + cooldown + caps por apply_quota); AMS realtime
    (`ad_realtime_metrics` + `realtime_reflex`, ambos ya en el repo) sirve
    para ALERTAS de gasto, no para decisiones de ACoS intradía — los reportes
    con atribución tienen lag de días y el ACoS intradía no es confiable.
  - **Portfolio (M7)**: ranking de ASINs y recomendaciones de reasignación
    en modo read-only (`product_profit` + `ad_daily_metrics`); la
    canibalización ya tiene motor propio (`cannibalization_motor`).
- **Retiro de motores viejos**: extracción de las primitivas de escritura a
  módulo compartido + borrado de los motores reemplazados.

## Residuales declarados (tope del kit de cross-review alcanzado)

Sin tercera ronda de revisión; lo que sigue quedó fuera del PR 1 por diseño o
pendiente del PR 2, declarado acá y en el PR:

1. `apply_quota.py` usa mapas cerrados (`_MOTOR_AUDIT_MAP`,
   `_MOTOR_DEFAULT_CAPS`, `_MOTOR_PAUSE_RESERVED_DEFAULTS`): el PR 2 los
   extiende aditivamente; la granularidad per-platform depende de lo que su
   API de quotas soporte (a definir al implementar).
2. El canary (`ads_optimizer_canary_pct`) está registrado en la escalera pero
   el gate efectivo se cablea en el PR 2 vía apply_quota.
3. Reactivación manual reciente (gracia de 7d para PAUSE ante un ENABLED
   manual): el PR 1 solo respeta estado ENABLED + opt-out de campaña; la
   detección de reactivación manual queda para el PR 2 (comparación
   `bid_cache.synced_at` vs audit propio).
4. Los motores viejos siguen activos hasta el cutover (decisión del dueño):
  puede haber decisiones simultáneas de adaptive y propuestas de hygiene en el
  mismo período; el shadow del PR 1 es exactamente para cuantificar eso.

## Tablas propias

Ver `database.md` (`ads_optimizer_goals`, `ads_optimizer_audit`,
`ads_optimizer_locks`).
