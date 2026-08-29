# ORBIT 05 — cutover: el motor pasa a LIVE con rampa

> **Propósito**: primera vez que Orbit aplica decisiones reales sin probe.
> Es un runbook con candados, no código nuevo (salvo lo que el preflight ya
> dejó). Fuente de verdad del orden: `docs/APPLY.md` §12 (ítems 4-10);
> precedencia §12 > este plan. Origen: backlog post-ORBIT 04 (Grok,
> 2026-08-28) contrastado por el lead; `plans/orbit-05-preflight.md` es
> prerequisito completo. Reglas 1-10 de `docs/CONTEXTO.md` intactas.
>
> **Prerequisitos SELLADOS (todos, sin excepción)**: (1) 2 semanas de shadow
> desde 2026-08-24 (~2026-09-07); (2) veto personal del dueño — CUMPLIDO
> (fila 4, `gon-personal`, 2026-08-28); (3) ensayo E2E 4/4 neto-cero —
> CUMPLIDO (4.3); (4) firma del dueño del spot-check — CUMPLIDA
> (2026-08-28, 33 decisiones); (5) CORTES 03 desplegada y verificada
> (preflight 1.1); (6) preflight 1.2-1.8 Done (incluida 1.6a: destino
> harvest US reactivado y sembrado); (7) este plan APROBADO por el dueño el
> 2026-08-28 ("1. si 2. no se 3 acotar 4 todos"): **goals del día 1 =
> TODOS (4 MX, 5/6/7 US)** — la rampa por goal queda disponible como
> mecanismo de rollback parcial, no como plan de encendido; el go del día
> del flip (1.1) lo re-confirma con la lista literal.
>
> **Reparto**: el día del flip lo ejecuta el lead con el dueño presente (o
> autorización escrita paso a paso); GLM no despliega ni flipea. Cross-review
> del PLAN: 1 ronda (codex) antes de aprobarlo; del día del flip no hay
> cross-review — hay verificación adversarial TRIPLE de las primeras
> decisiones aplicadas (ítem 9 del checklist).

## Decisiones SELLADAS (header manda sobre las tareas)

1. **Orden operativo**: backup REAL del día → discard masivo de filas shadow
   (`app_admin`, UNA transacción) → flip → rampa. Nunca al revés; nunca el
   mismo día que un deploy de código o que settings de dashboard
   (ORBIT 16 Phase 3 va DESPUÉS de 48h live quietas).
2. **El flip es doble**: el modo efectivo es el *meet* del retículo
   `off < shadow < live` entre la escalera global (`ads_optimizer_mode` de
   la config vigente) y `goal.mode` (`app/apply.py modo_efectivo`,
   `goals.modo_efectivo`). Subir la escalera a `live` NO enciende nada
   mientras los goals sigan en `shadow`. Eso permite **rampa por goal**: el
   dueño decide qué goals entran el día 1 (p. ej. solo US 6/7, o solo MX 4)
   y el resto se enciende después con el mismo runbook. No existe CLI para
   `goal.mode`: se hace por `UPDATE` admin con `updated_at` explícito, en la
   misma ceremonia y con su SELECT.
3. **Caps de día 1 = 10 bids / 2 pauses / 5 negatives / 2 harvests por día y
   plataforma** (config id 10, seeds 4.2). No se suben el día 1. La
   duplicación "cada 48h sanas" es decisión humana posterior (config nueva,
   append-only), fuera de este plan.
4. **El live arranca SOLO con decisiones frescas post-flip** con su ventana
   de 48h completa: por eso el discard masivo va ANTES del flip. Las filas
   `vetoed` (3 y 4) son terminales y no se tocan.
5. **La ventana de veto aplica por default**: si el dueño no abre `/cortes`
   en 48h, Orbit escribe. Digest diario por Telegram + plazo escrito en el
   runbook; el veto NO se automatiza.
6. **Primeras decisiones aplicadas = ritual completo**: (a) primer harvest
   live se revisa a mano contra Amazon (readback LIST, identidad completa),
   no contra la fila; (b) verificación adversarial TRIPLE (codex + grok +
   qwen) de las primeras decisiones APLICADAS (ritual sellado en la
   aprobación del plan de ORBIT 04) con **muestra MX forzada** (el bug del
   techo 2.50 era mexicano; el primer shadow validado fue 124 US / 9 MX);
   (c) spot-check del dueño sobre las primeras aplicadas — el recálculo de
   IA no lo sustituye.
7. **No evaluar ACoS de día 1**: venta 5-8 días, costo hasta 15, fees
   15-30. Revisión de impacto = ventana madura (~30 días). Advertencia:
   "ACoS MX temprano ~1.5× peor" es hipótesis del chat de estrategia, no
   dato del repo — se lee con cautela, no se codifica.

## Phase 1 — Día del flip [lane:release]

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 1.1 | **Go/No-go**: el lead verifica los 7 prerequisitos con evidencia (SELECTs: config vigente shadow, cola 2/5 `pending_veto` + 3/4 `vetoed`, `apply_attempt` solo probe, `apply_quota_state` 0, goals con floor/ceiling por moneda **y `enabled=true` en cada goal que entra** (CodeRabbit PR #44: `modo_efectivo` devuelve `shadow` si `enabled=false` aunque `mode='live'`, `app/apply.py:975-978`; `enabled` NO se activa automáticamente — si alguno está en false, se pregunta), CORTES 03 viva: última pause con `cost_min_usado`), CI de master verde, y el dueño firma el go con la lista de goals que entran a live el día 1 (sellado 2). `[tdd:skip:go-no-go]` | Tabla de prerequisitos con SELECT y resultado en la evidencia (incluido `enabled` por goal); firma del dueño (mensaje literal) con la lista de goals | preflight 1.8 | cc:TODO |
| 1.2 | **Backup pre-cutover REAL** (checklist §12 ítem 4a): runbook DEPLOY.md §"Backup pre-cutover" con `tools/snapshot_listas.py` (preflight 1.3): dump -Fc + globals + CSV `ad_entity_state` + listas Amazon en `backups/precutover_orbit05_<fecha>/` (700/600, root, fuera de rotación); **VERIFY_OK de los CUATRO artefactos** (restore real a `orbit_verify_tmp` + conteos del día idénticos, globals = roles, CSV = count+1, JSON cargable con totales = cache). `[tdd:skip:ops]` | VERIFY_OK con los cuatro gates y conteos del día en la evidencia; base temporal borrada; `/tmp` limpio | 1.1 | cc:TODO |
| 1.3 | **Discard masivo de filas shadow** (ítem 4b) — **en ventana sin ciclo** (codex plan r1): el cron del ciclo corre a las 08:40 UTC (DEPLOY.md §Crons); 1.3 y 1.4 se ejecutan seguidos, en una ventana <30 min lejos de esa hora, y ANTES del flip se re-verifica `SELECT max(id) FROM optimizer_cycle` sin cambio desde el discard y cero filas shadow no terminales (una fila shadow nueva entre discard y flip bloquearía por clave de efecto el corte live fresco vía skip `veto_pendiente`). **Nota de historia (Greptile PR #45)**: la fila shadow **2** ya se descartó el 2026-08-29 en el preflight 1.1 (motivo declarado en `discard_motivo`, para liberar la clave de efecto y probar CORTES 03 en vivo): el conteo del día NO la incluirá y su estado pre-discard vive en la propia fila (`discarded_at` + motivo) y en los backups diarios; el `RETURNING` debe conciliar contra las filas shadow pendientes DEL DÍA, no contra un número histórico. Con `ORBIT_DSN_ADMIN`, UNA transacción: `SELECT count(*)` antes → `UPDATE apply_queue SET estado='discarded', discarded_at=now(), discard_motivo='flip ORBIT 05 <fecha>' WHERE modo='shadow' AND estado IN ('pending_veto','released') RETURNING id` → conteo después; el trigger exige admin (candado PR #26). Filas `vetoed` intactas. `[tdd:skip:ops]` | `RETURNING` = el conteo previo DEL DÍA (al 2026-08-29 queda solo la fila 5 en `pending_veto`: la 2 se descartó en el preflight 1.1 y la 3/4 son `vetoed`; súmense las filas shadow que nazcan hasta el flip); `SELECT` posterior: cero filas shadow no terminales; 3 y 4 siguen `vetoed`; evidencia con ambos conteos | 1.2 | cc:TODO |
| 1.4 | **Flip** (ítem 5): (a) config NUEVA (append-only, patrón 4.2 / APPLY §11: copia de la vigente con `ads_optimizer_mode='live'` y las mismas 10 claves de caps/targets, label `flip ORBIT 05 <fecha>`); (b) `UPDATE ads_optimizer_goal SET mode='live', updated_at=now() WHERE id IN (<goals firmados en 1.1>)`; (c) SELECT de config vigente y goals (`mode`, **`enabled`** — antes y después del UPDATE; un goal con `enabled=false` sigue en shadow por `modo_efectivo`); (d) `GET /api/ads-optimizer/status` cita `live` en la escalera y `HAY_MODULO_APPLY` con escalera live (ítem 10). Nada más se toca ese día. `[tdd:skip:ops]` | Config vigente `live`; goals firmados en `live` **y `enabled=true`** y el resto en `shadow`; status/salud lo reflejan; evidencia con los SELECT; hora exacta del flip registrada | 1.3 | cc:TODO |
| 1.5 | **Primer ciclo live y rampa** (ítems 6-7): esperar el ciclo del cron (no forzar `/run`); verificar: bids aplicados ≤ cap por plataforma y `apply_quota_state` con filas (primera cobrada), ledger `apply_attempt` tipo `normal` con ack + readback + sello, cortes nuevos en cola `modo='live'` `pending_veto` con `vence_el = +48h`, aviso Telegram de encolado recibido, cero filas shadow nuevas. Cualquier `failed` o readback divergente → PARAR con el **rollback COMPLETO** (codex plan r1 + CodeRabbit/Greptile PR #44: bajar solo la escalera no basta y la reversa de bid no cubre todo): (i) config nueva con escalera `shadow` Y `goal.mode='shadow'` en los goals encendidos; (ii) discard admin (una transacción, motivo `rollback ORBIT 05 <fecha>`) de TODA fila live `pending_veto/released` — si quedaran, al reactivar live vencerían y se aplicarían de golpe; (iii) **filas `applying` NO se descartan** (la máquina lo prohíbe): cada una se resuelve por readback LIST + ledger (aplicada de verdad → tratar como applied; no → `failed` con nota) ANTES de cerrar el rollback; (iv) **compensación por tipo de mutación ya aplicada**, decisión escrita del dueño por decisión: bid verificado → `POST /reversa/bid`; pause → `POST /reversa/pause` (resume); negative → `POST /reversa/negative` (archivar); harvest → parcial ya lo revierte `apply_harvest` (reversa automática + alerta), completo → archivar la keyword creada (reversa de harvest, una por decisión, ledger); **bid con readback divergente o `failed`** → los endpoints de reversa lo RECHAZAN (exigen `applied_cycle_id`, que solo nace tras verificación) → reversión MANUAL (PUT por el cliente de escritura con autorización del dueño, o consola de Amazon) con nota en ledger; (v) si el dueño decide CONSERVAR alguna mutación, el resultado se registra como **rollback PARCIAL**, nunca como completo; cierre del rollback = cero `applying` y cero mutaciones externas sin resolver (SELECT + readback en la evidencia); (vi) análisis antes del siguiente ciclo. `[tdd:skip:ops]` | SELECTs del primer ciclo en la evidencia; quota `used ≤ cap`; sin `failed`; el dueño recibió el aviso; decisión explícita "seguimos" del lead+dueño | 1.4 | cc:TODO |

## Phase 2 — Primeras 48h live [lane:gate]

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 2.1 | **Verificación adversarial TRIPLE** (ítem 9, sellado 6b): codex + grok + qwen reciben las primeras decisiones APLICADAS **SANEADAS** (decisión + inputs congelados + request/ack pasados por el scrub del repo + readback; JAMÁS tokens, headers, profile ids ni ids de cuenta — los datos comerciales (keyword, gasto, ventas, bid) sí viajan, como en todos los cross-reviews previos; codex plan r1: divulgación declarada en 事前確認), con muestra MX forzada (mínimo 5 MX si existen; si no hay MX aplicadas el día 1, se declara y se repite al primer día con MX). Cada uno recalcula contra las reglas selladas y busca divergencias entre decisión, request, ack y readback. Tope: 1 ronda; residuales declarados. `[tdd:skip:verificacion-adversarial]` | Tres reportes adjudicados por el lead; cero divergencia sin explicar; si hay bug → escalera a shadow y tarea de fix ANTES de seguir | 1.5 | cc:TODO |
| 2.2 | **Spot-check del dueño sobre las primeras aplicadas** (sellado 6c): tabla en lenguaje de negocio (keyword, campaña, gasto, ventas, ACoS, acción, ack) de las primeras ≥10 decisiones aplicadas + la cola live pendiente; el dueño firma o veta. `[tdd:skip:checkpoint-humano]` | Firma del dueño en AppFlowy con fecha; vetos ejecutados por él si los hay | 1.5 | cc:TODO |
| 2.3 | **Primer harvest live a mano** (sellado 6a): cuando el primer harvest salga de la cola (48h después del flip como mínimo): readback LIST en la campaña destino (MX = Arras Manual, terna goal 4; US = lo que el dueño decidió en preflight 1.6), identidad completa (keyword_text + match_type + ad group + bid), ledger sellado, y `keywords_campana_destino` sin duplicado. `[tdd:skip:ops]` | Keyword nueva visible en Amazon con el bid del goal; ledger `applied` con readback; cero duplicados; evidencia | 1.5 | cc:TODO |
| 2.4 | **Monitoreo 48h + hábito de veto** (ítem 8, sellado 5): digest diario por Telegram, `/cortes` revisado por el dueño al menos una vez al día (registrado), quota `used/cap` en la superficie de preflight 1.4-1.5, alertas de cap saturado, cero `failed` sin analizar; anotar en el runbook que el ACoS de día 1 no se evalúa (sellado 7). `[tdd:skip:ops]` | Dos días consecutivos con: ciclos `done`, quota dentro de cap, cola sana (sin `applying` colgado), dueño con al menos una visita a `/cortes` por día registrada | 1.5 | cc:TODO |
| 2.5 | **Cierre**: post-flip (ítem 10) — SELECT de la cola (cero shadow pendientes), quota del día, escalera live verificada; CHAT-CONTEXT "ORBIT 05 CERRADA" (qué goals están live, cuáles no, estado de la cola y de las decisiones del dueño), `ORBIT 05` Done en AppFlowy. La duplicación de caps y el encendido de goals restantes = decisiones humanas registradas como tareas nuevas. `[tdd:skip:cierre-docs]` | Checklist §12 completo con fechas; CHAT-CONTEXT al día; AppFlowy Done con evidencia | 2.1-2.4 | cc:TODO |

## Reject (con razón)

- **Forzar `/run` el día del flip**: el camino del cron es el que se ensayó
  (2.5, 4.3); un `/run` manual cambia el reloj de liberación y la evidencia.
- **Subir caps el día 1 o encender todos los goals a la vez sin firma**: el
  retículo permite rampa por goal precisamente para no hacerlo.
- **Deploy de código o settings de dashboard el mismo día**: un cambio a la
  vez (sellado 1).
- **Automatizar el veto o "retry mañana" que ignore el cap**: contra el
  diseño híbrido y el fail-closed sellados.

## Residuales declarados

1. Destino harvest US: decidido (preflight 1.6a: USPerNog Exact 3919
   reactivada + terna del goal 5). Hasta que 1.6a esté Done, los harvest
   US se saltan por `harvest_sin_config` (correcto: sin config jamás
   placeholder) y 2.3 solo puede ocurrir en MX.
2. Halo US: el dueño eligió **acotar con ambos supuestos** — vale para la
   fase margin-aware (reportar/decidir con el rango con-halo / sin-halo);
   ORBIT 05 sigue optimizando ACoS con revenue completo (CONTEXTO manda),
   así que la rentabilidad real de US sigue sin un solo número hasta esa
   fase. AGM2M (165) queda DIFERIDO por el dueño ("no sé"): PAUSED, fuera
   del piloto, se re-pregunta tras 48h live.
3. `apply_queue.vetoed_by` sigue siendo texto libre bajo un token compartido
   (residual 5 de ORBIT 04): la autoría de los vetos del dueño se prueba por
   la ceremonia (él ejecuta), no por la base.

## 事前確認

- 事項: destructive/external-send — **MUTACIONES REALES a Amazon Ads** por el motor en modo live (bids automáticos; cortes tras 48h de ventana), con caps de día 1 y solo en los goals firmados por el dueño
  理由: es el propósito de ORBIT 05; autorización = firma del dueño en 1.1 + go explícito en 1.5
  scope: Phase 1 / 1.4-1.5, Phase 2 / 2.3-2.4
- 事項: destructive — discard masivo de filas shadow (`app_admin`, una transacción) y flip de config/goals
  理由: ítems 4-5 del checklist §12
  scope: Phase 1 / 1.3-1.4
- 事項: external-send — backup real con lecturas a Amazon (`tools/snapshot_listas.py`), restore a base temporal, SELECTs de verificación
  理由: ítem 4a; regla 10
  scope: Phase 1 / 1.2, 1.5
- 事項: external-send — divulgación de datos COMERCIALES saneados (keywords, gasto, ventas, bids, acks sin secretos) a tres IAs externas (codex, grok, qwen) en 2.1
  理由: ritual adversarial triple sellado; misma práctica de los cross-reviews de ORBIT 03/04; autorizada por el dueño al aprobar este plan
  scope: Phase 2 / 2.1
- 事項: external-send — `git push` + PRs de docs (este plan, CHAT-CONTEXT), mensajes Telegram (avisos y digest reales)
  理由: patrón del repo; el canal es parte del mecanismo de veto
  scope: todas
