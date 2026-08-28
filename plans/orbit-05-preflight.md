# ORBIT 05 preflight — candados antes del cutover a live

> **Propósito**: dejar TODO lo que el checklist de cutover (`docs/APPLY.md`
> §12) exige ANTES del día del flip, sin tocar el flip. Origen: backlog
> post-ORBIT 04 (chat de estrategia Grok, 2026-08-28) contrastado por el lead
> contra repo y base viva: 4 correcciones incorporadas (P0.2 superada por la
> decisión de replay fiel de CORTES 03; P0.6 reescrito como "default de goal
> por moneda" — solo existe UN goal MX y ya está corregido; P3.2 y P3.4
> eliminados: no hay token vigente que rotar ni tabla vieja de umbrales).
> Reglas 1-10 de `docs/CONTEXTO.md` intactas. Precedencia: `docs/APPLY.md`
> §12 > este plan. Un PR por tarea (o por pareja 1.4+1.5 si el lead lo
> autoriza); nada de umbrales nuevos aquí (los umbrales viven en CORTES 03).
>
> **Reloj**: el cutover no se adelanta por "se ve bien": 2 semanas de shadow
> desde 2026-08-24 (~2026-09-07) + este preflight Done + `plans/orbit-05.md`
> aprobado por el dueño.
>
> **Reparto**: GLM implementa 1.1-1.4 (código); DeepSeek 1.5 (superficie de
> lectura + alerta, sin ssh/push/tracker); el lead cierra 1.6-1.8 y revisa
> cada tarea contra la base viva + reviewer fresco + bots. Cross-review:
> 1 ronda por tarea; 2ª SOLO si la 1ª halló alta; jamás 3ª.

## Decisiones SELLADAS (header manda sobre las tareas)

1. **CORTES 03 es candado del flip**: con los umbrales de pausa vigentes
   (50 clics / 12 USD / 200 MXN) el dueño NO autoriza live (checklist §12
   ítem 3c). Los umbrales nuevos (100 clics / 40 USD / 500 MXN) y el replay
   fiel por construcción (freeze `cost_min_usado` + defaults históricos
   solo-replay) ya están decididos en PR #43; aquí solo se DESPLIEGAN y se
   VERIFICAN.
2. **Un número de dinero lleva su moneda**: el default de esquema
   `bid_floor/bid_ceiling = 0.10/2.50` nació en USD y se aplicó al goal 4
   (MXN) hasta que el spot-check lo atrapó (144/233 keywords y 44/51
   targets MX activos por encima de 2.50 MXN). Sellado: un goal MXN jamás
   nace con defaults USD. Los números MXN son los que el dueño ya firmó:
   **piso 1.00 / techo 45.00 MXN**; USD queda 0.10/2.50 (máx real observado
   2.00). NO se inventan otros pisos/techos: se listan y se pregunta.
3. **Conciliar contra Amazon, no contra consistencia interna** (regla 10):
   el snapshot de listas del día del flip se produce con un TOOL del repo
   con test, no con código inline (4.4 lo hizo inline: quedó declarado como
   hueco del runbook). Cero mutaciones: solo POST de lectura por el cliente
   allowlist.
4. **Quota visible antes de cobrarse en anger**: `apply_quota_state` tiene 0
   filas hoy; el primer ciclo live es la primera cobrada. Antes del flip la
   superficie de lectura muestra `used` vs cap por forma y plataforma y el
   canal Telegram avisa (fail-silent) al saturar un cap. Sin "retry mañana"
   que ignore el cap (fail-closed sellado en 4.2 se queda).
5. **Las preguntas de producto son del dueño** (destino harvest US, AGM2M,
   halo US): el lead pregunta con opciones escritas; no decide ni codifica
   hasta tener respuesta firmada o diferimiento explícito.

## Phase 1 — Preflight [lane:gate]

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 1.1 | **CORTES 03 desplegada y verificada en vivo**: merge de PR #43 (con el cierre de replay del lead: freeze `cost_min_usado`, `REPLAY_*` históricos, 34/34 fieles); deploy en goncloud con **`docker compose up -d --no-deps --build app`** (DEPLOY.md:51/119 — `app` usa `build:`, un `--force-recreate` sin `--build` recrea la imagen VIEJA; codex plan r1) como root; prueba de que el código nuevo está VIVO dentro del contenedor: `docker exec orbit-app-1 python -c "from app.optimizer import cortes, bid; print(cortes.F_PAUSE, cortes.LEGACY_PAUSE, bid.PAUSE_COST_MIN)"` → `100 100 {...40, ...500}` + `git rev-parse` del commit construido y digest de la imagen; un ciclo shadow del cron (08:40 UTC) como verificación: la keyword de la decisión 774 (72 clics / 25.21 USD) ya NO produce pause; `SELECT` de las nuevas decisiones pause (si las hay) con `inputs.corte.cost_min_usado = '40'/'500'`; replay de las 34 históricas fiel. Marcar `docs/APPLY.md` §12 ítem 3(c). `[tdd:skip:deploy-verificacion]` | PR #43 en master con CI verde; imagen reconstruida (digest + commit citados) y constantes leídas DENTRO del contenedor = 100/100/40/500; ciclo shadow posterior sin pause de la 774 y con `cost_min_usado` congelado en toda pause nueva; ítem 3c marcado con fecha | - | cc:TODO |
| 1.2 | **Default de goal por moneda** (sellado 2): `app/optimizer/goals.py` — `resuelve_floor_ceiling(goal)` resuelve los None por `bid_currency` (`USD → 0.10/2.50`, `MXN → 1.00/45.00`, otra moneda → error explícito, jamás un número inventado); `app/goals_write.edita_goal` y el camino de alta (INSERT admin de 4.2 / CLI) resuelven floor/ceiling por moneda ANTES de persistir (una fuente: la misma tabla de defaults); **migración `migrations/0003_goal_bounds_explicit.sql`: `ALTER TABLE ads_optimizer_goal ALTER COLUMN bid_floor DROP DEFAULT, ALTER COLUMN bid_ceiling DROP DEFAULT`** (NOT NULL se queda) — un INSERT admin que omita piso/techo REVIENTA en vez de nacer en USD (codex plan r1: el candado en código solo no bastaba); aplicada en goncloud con el runbook de 4.1 (backup previo del schema, `SELECT` de verificación) y en CI (schema tests). Auditoría regla 8: `SELECT id, platform, bid_currency, bid_floor, bid_ceiling, harvest_default_bid FROM ads_optimizer_goal` en la evidencia (hoy: 4 = MXN 1.00/45.00/2.50; 5-7 = USD 0.10/2.50). `[tdd:required]` | Tests regla 9: goal MXN con None → 1.00/45.00 (rojo con el default único); USD → 0.10/2.50 intacto; moneda desconocida → excepción; `edita_goal` no persiste un MXN con defaults USD; test de schema: INSERT sin `bid_floor`/`bid_ceiling` → `NotNullViolation` (rojo antes de 0003); el golden del ciclo shadow no cambia para US; 0003 aplicada en vivo y verificada (`SELECT column_default … = NULL`); SELECT de auditoría en la evidencia; delta a `docs/APPLY.md` §5 (goals), CONTEXTO y DEPLOY.md (migración) | 1.1 | cc:TODO |
| 1.3 | **`tools/snapshot_listas.py` + test** (sellado 3): reusar `app.ads.structure.perfiles_aceptados` + `AdsClient.list_objects` sobre `/sp/keywords/list`, `/sp/negativeKeywords/list`, `/sp/targets/list` (paginación completa), salida JSON agrupada por `campaignId` + resumen de conteos por plataforma/recurso; flag `--out <dir>` (archivos 600, `umask 077`) y `--solo-conteos`; cero mutaciones (test de arquitectura: el tool solo importa el cliente de lectura y jamás `app/ads/write.py`). Partes puras (agrupado, conteos, comparación contra cache) con tests; corrida real read-only una vez (regla 8) comparando contra `ad_entity` (incl. ARCHIVED): hoy MX 2,645 kw / 861 targets, US 1,336 / 549. DEPLOY.md §"Backup pre-cutover" cita el tool en vez del inline. `[tdd:required]` | Tests puros verdes + red-log; test de arquitectura que falla si el tool importa write.py; corrida real con conteos = cache (diferencias explicadas); runbook actualizado; sin credenciales en repo ni logs | - | cc:TODO |
| 1.4 | **Quota visible + alerta** (sellado 4): `GET /api/ads-optimizer/status` (o `salud`) expone por plataforma y forma (`bid/pause/negative/harvest`) `used` y `cap` — **`cap` = el de la fila `apply_quota_state` del día UTC si existe (es INMUTABLE una vez nacida: el que realmente rige hoy), si no el de la config vigente, con un campo `fuente` que dice cuál de los dos es** (codex plan r1: tras cambiar config, la fila del día conserva su cap y el dashboard mentiría) — sin nueva copia de la lógica de caps (reusar el mapeo config↔quota de `app/apply.py`, decisión 6: jamás dos fuentes); `app/notifica.py` manda aviso fail-silent cuando un ciclo ejecutor agota un cap (una vez por cap y día; NOTA en `notes` si el canal falla). `[tdd:required]` | Tests: `status` muestra `used/cap` coherentes con una fila sembrada de `apply_quota_state` (rojo sin el campo); cap agotado → 1 aviso y no 2 el mismo día; canal caído no tumba el ciclo (conftest aísla, cero HTTP real); OpenAPI sin endpoints de escritura nuevos | 1.1 | cc:TODO |
| 1.5 | **Pantalla `/salud` o `/cortes` muestra la quota** (DeepSeek, server-rendered, solo lectura): `used/cap` por plataforma y forma desde el endpoint de 1.4; sin JS nuevo; sin tocar `app/apply.py` ni `app/ads/write.py`. `[tdd:required]` | Test de render con la fixture de 1.4; test_architecture verde; cero escritura | 1.4 | cc:TODO |
| 1.6 | **Decisiones del dueño (sellado 5) — RESPONDIDAS 2026-08-28** (registradas en `docs/CHAT-CONTEXT.md` "Decisiones del dueño para ORBIT 05"): (a) **destino harvest US = SÍ**: reactivar USPerNog Exact (ad_entity 3919, external 251723662158466, hoy PAUSED; su ad group 4012 ya ENABLED) y sembrar la terna del goal 5 → tarea 1.6a; (b) **AGM2M (165) = DIFERIDO** ("no sé"): fuera del piloto, sigue PAUSED, no semi-viva; se re-pregunta después de 48h live; (c) **halo US = ACOTAR con ambos supuestos** (CONTEXTO.md:188): la fase margin-aware reporta y decide con el RANGO (con halo / sin halo), nunca con un solo número; ORBIT 05 sigue optimizando ACoS con revenue completo (CONTEXTO manda) — sin código aquí; (d) **goals del día 1 = TODOS** (4 MX, 5/6/7 US) — ver `plans/orbit-05.md`. `[tdd:skip:decision-dueno]` | Las cuatro decisiones en CHAT-CONTEXT y AppFlowy con fecha y texto literal del dueño | - | cc:完了 [dueño 2026-08-28: "1. si 2. no se 3 acotar 4 todos"] |
| 1.6a | **Destino harvest US: reactivar 3919 + terna del goal 5** (patrón 4.2 + CAMPANAS 01): (1) el dueño autoriza en el momento (mutación real); reactivación POR API con el camino ya sellado en PR #37 (`tools/reactiva_campanas.py`, PUT con state UPPER, readback LIST) de la campaña 251723662158466; sync de estructura y `SELECT` de `ad_entity_state`: campaña y ad group 4012 ENABLED; (2) `goals set 5 --harvest-campaign 251723662158466 --harvest-ad-group 522582072501798 --harvest-bid <mediana de los bids EXACT ENABLED reales de esa campaña, SELECT en la evidencia>` (terna all-or-nothing, `goal_harvest_completo`); (3) verificación: el siguiente ciclo shadow US deja de saltar `harvest_sin_config` y los términos US que califican nacen como decisiones harvest hacia 3919 (shadow: no se aplica nada). Si la campaña tiene residuos `zzsmokeprobe*` (ARCHIVED, del 2.5/4.3) se declaran, no se tocan. `[tdd:skip:seed-config]` | SELECTs: 3919 y 4012 ENABLED; goal 5 con terna completa y `harvest_default_bid` justificado; ciclo shadow posterior con decisiones harvest US (o skip distinto de `harvest_sin_config`); evidencia `out/orbit-05-preflight-1-6a-<fecha>.md`; autorización literal del dueño en la evidencia | 1.6 | cc:TODO |
| 1.7 | **Fecha de revocación de `orbit_test` ADMIN OPTION** (DEPLOY.md:95/281/300): atarla a "sacar la DB de test del cluster de prod" con fecha o hito concreto acordado con el dueño; documentar en DEPLOY.md; nada se revoca en este plan. `[tdd:skip:docs]` | DEPLOY.md con hito/fecha; AppFlowy con la tarea futura creada | - | cc:TODO |
| 1.8 | **Cierre del preflight**: CHAT-CONTEXT al día ("ORBIT 05 preflight CERRADO", estado de la cola, prerequisitos del flip con lo cumplido), `plans/orbit-05.md` aprobado por el dueño (su firma en AppFlowy), `ORBIT 05` en AppFlowy con la evidencia. `[tdd:skip:cierre-docs]` | Checklist §12 ítems 1-3 con estado real; 1.1-1.7 Done; el dueño aprobó orbit-05.md | 1.1-1.7 | cc:TODO |

## Reject (con razón)

- **Rotar "el token vigente" del smoke (backlog P3.2)**: no existe token
  vigente — el del smoke es de un solo uso por corrida y la config vigente
  (id 10) no lleva la clave; el residual (texto plano en el historial
  append-only de `config_version`) ya está declarado en APPLY.md §11d.
- **Limpiar "tabla vieja de umbrales" en CHAT-CONTEXT (P3.4)**: no existe;
  el único 25/12 que aparece es la nota de replay histórico de CORTES 03
  (correcta y necesaria).
- **Partir `cycle.py` antes del flip (P3.1)**: deuda real (80 KB) pero
  cirugía sobre el archivo que el flip usa; va DESPUÉS de 48h live quietas.
- **Cambiar caps 10/2/5/2, NEGATIVE, o meter margin-aware/Mercado Libre**:
  fuera de alcance sellado.

## Residuales declarados

1. "ACoS MX temprano ~1.5× peor" (backlog P1.4) es hipótesis del chat de
   estrategia, no dato del repo: se anota en el runbook de orbit-05 como
   advertencia de lectura (no evaluar ACoS de día 1), no como regla.
2. Tras 0003 un goal solo nace con piso/techo explícitos; los goals YA
   existentes no se tocan (4 = 1.00/45.00 MXN por el dueño; 5-7 = 0.10/2.50
   USD, máx real 2.00). Si el dueño quisiera otros valores US, es
   `goals set`, no este plan.

## 事前確認

- 事項: external-send — `git push` + `gh pr create`/merge (un PR por tarea)
  理由: patrón del repo, batería en CI
  scope: Phase 1 / todas
- 事項: destructive — deploy de la app (`up -d --no-deps --build app`) en 1.1
  理由: CORTES 03 debe estar viva antes del flip; crons y base intactos
  scope: Phase 1 / 1.1
- 事項: destructive — DDL en la base viva: migración 0003 (DROP DEFAULT en `ads_optimizer_goal.bid_floor/bid_ceiling`) con backup previo del schema (runbook 4.1)
  理由: sellado 2 (un goal MXN jamás nace con defaults USD); reversible (`SET DEFAULT`)
  scope: Phase 1 / 1.2
- 事項: destructive/external-send — MUTACIÓN REAL a Amazon Ads en 1.6a: reactivar la campaña USPerNog Exact US (251723662158466 → PUT state ENABLED por el camino de PR #37) + seeds del goal 5 (`goals set 5 --harvest-*`, append/UPDATE admin auditado)
  理由: decisión del dueño 2026-08-28 ("1. si"); autorización efímera del dueño EN EL MOMENTO, como en 2.5/4.3/CAMPANAS 01
  scope: Phase 1 / 1.6a
- 事項: external-send — LECTURAS a Amazon Ads (los tres `/list`) en 1.3
  理由: regla 10 (conciliar contra Amazon); cero mutaciones
  scope: Phase 1 / 1.3
- 事項: external-send — SELECTs read-only a la base viva y anotaciones en AppFlowy
  理由: regla 8 y registro obligatorio
  scope: Phase 1 / todas
- 事項: external-send — mensajes Telegram de prueba del aviso de quota (canal real SOLO en la verificación final, tests con canal aislado)
  理由: DoD de 1.4
  scope: Phase 1 / 1.4
