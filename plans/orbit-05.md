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
> **FIRMA DEL DUEÑO SOBRE ESTE RUNBOOK — 2026-08-29, literal "2 firmado"**
> (preflight 1.8). Es distinta del punto (7): aquélla respondía las cuatro
> preguntas de 1.6; ésta aprueba el DOCUMENTO —orden operativo, rollback por
> tipo de mutación y terna adversarial— como el procedimiento del día del
> flip. Con esto el preflight queda cerrado SIN pendientes humanos: lo único
> que falta para el flip es el calendario (2 semanas de shadow, ~2026-09-07)
> y lo que se ejecuta ese mismo día (backup real, discard, flip, rampa).
> La firma aprueba el PROCEDIMIENTO, no dispara el flip: el día del flip
> exige su propio go explícito del dueño (preapproval #4).
>
> **ENMIENDA DEL DUEÑO AL PREREQUISITO (1) — 2026-09-02, literal "adelanto
> el flip, renuncio a los días restantes de shadow"**: el flip se adelanta
> del ~2026-09-07 al 2026-09-02 con 9 días de shadow corridos. GO del día
> recibido el mismo 2026-09-02 (literal "confirmo": goals del día 1 =
> TODOS — 4 MX, 5/6/7 US; "ok hoy despues del ciclo de las 08:40").
> Evidencia del Go/No-go en la tarea 1.1.
>
> **ENMIENDA DEL DUEÑO A LA TAREA 1.5 (y al Reject "forzar el ciclo el día
> del flip") — 2026-09-02, literal "corre hoy"**: el lead expuso el trade-off
> (es seguro por diseño —quota por día UTC y cooldown 7d por entidad— pero
> el camino ensayado era el cron y el reloj de 48h de los cortes arrancaría
> a una hora distinta); el dueño decidió no esperar al cron del 2026-09-03.
> El primer ciclo live corrió A MANO por el MISMO comando del cron
> (`python -m app.cli cycle --platform …`, no `/run`), US a las 16:12:50 y
> MX a las 16:14:07 UTC, con el dueño presente y una plataforma a la vez
> (verificar US antes de correr MX). Marcador escrito en `optimizer.log`.
> Hallazgo de la revisión de esas primeras aplicadas: **2 de 20 bids cayeron
> en campañas PAUSED** → `plans/campana-activa-01.md` (PR #122 plan, #123
> código). Decisión del dueño: conservar esas 2 pujas ("dejalas asi") y
> cerrar el gate ANTES del papeleo de ORBIT 05. Evidencia en la tarea 1.5.
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
| 1.1 | **Go/No-go**: el lead verifica los 7 prerequisitos con evidencia (SELECTs: config vigente shadow, cola: cero filas shadow no terminales sin contar (al 2026-08-29: 5 `pending_veto`, 3/4 `vetoed`, 2 `discarded` en el preflight 1.1; lo que cuenta es el conteo DEL DÍA), `apply_attempt` solo probe, `apply_quota_state` 0, goals con floor/ceiling por moneda **y `enabled=true` en cada goal que entra** (CodeRabbit PR #44: `modo_efectivo` devuelve `shadow` si `enabled=false` aunque `mode='live'`, `app/apply.py:975-978`; `enabled` NO se activa automáticamente — si alguno está en false, se pregunta), CORTES 03 viva: última pause con `cost_min_usado`), CI de master verde, y el dueño firma el go con la lista de goals que entran a live el día 1 (sellado 2). `[tdd:skip:go-no-go]` | Tabla de prerequisitos con SELECT y resultado en la evidencia (incluido `enabled` por goal); firma del dueño (mensaje literal) con la lista de goals | preflight 1.8 | cc:完了 [2026-09-02 ~08:10 UTC. Evidencia con SELECTs vivos (orbit_read): config vigente id 10 `shadow` con caps 10/2/5/2 y targets ACoS 20/20; cola del día: 2 shadow `pending_veto` (ids 5,6 — vencidas, se descartan en 1.3), 2 `vetoed` (3,4), 1 `discarded` (2), cero live; `apply_attempt` solo probe (29, último 2026-08-28); `apply_quota_state` 0 filas; goals 4 (MXN 1.00/45.00, harvest cfg), 5/6/7 (USD 0.10/2.50), los 4 `enabled=true` `mode=shadow`; CORTES 03 viva: 880 decisiones con `cost_min_usado` congelado, última ciclo 30 (2026-09-01); ciclos shadow diarios `done` al 2026-09-01; CI master verde (run 2026-09-02 07:05 UTC). **ENMIENDA del dueño al prerequisito de calendario** (2 semanas de shadow → 9 días corridos), literal: "adelanto el flip, renuncio a los días restantes de shadow". **GO del dueño**, literal: "confirmo" — goals del día 1 = TODOS (4 MX, 5/6/7 US); ceremonia: "ok hoy despues del ciclo de las 08:40"] |
| 1.2 | **Backup pre-cutover REAL** (checklist §12 ítem 4a): runbook DEPLOY.md §"Backup pre-cutover" con `tools/snapshot_listas.py` (preflight 1.3): dump -Fc + globals + CSV `ad_entity_state` + listas Amazon en `backups/precutover_orbit05_<fecha>/` (700/600, root, fuera de rotación); **VERIFY_OK de los CUATRO artefactos** (restore real a `orbit_verify_tmp` + conteos del día idénticos, globals = roles, CSV = count+1, JSON cargable con totales = cache). `[tdd:skip:ops]` | VERIFY_OK con los cuatro gates y conteos del día en la evidencia; base temporal borrada; `/tmp` limpio | 1.1 | cc:完了 [2026-09-02 08:48-08:50 UTC, lead. `backups/precutover_orbit05_2026-09-02/` (700/600, root, fuera de rotación): dump -Fc (1,547,778 B), globals (3,236 B), CSV `ad_entity_state` (925,477 B), `listas_amazon/listas_por_plataforma.json` (3,107,135 B, vía `tools/snapshot_listas.py` en el contenedor, read-only). **VERIFY_OK de los CUATRO**: (1) restore real a `orbit_verify_tmp` con `--exit-on-error` + `pg_restore --list` (22 TABLE DATA) y conteos idénticos prod=restore: apply_queue 5, apply_attempt 29, config_version 9, decision 1,981, ad_entity_state 18,426; base temporal borrada con FORCE; (2) globals 10 `CREATE ROLE` = 10 roles no-pg del cluster; (3) CSV 18,427 líneas = 18,426 + header; (4) JSON cargable, totales MX 2,645 kw / 861 targets / 2,597 neg y US 1,336 kw / 549 targets / 1,536 neg = `ad_entity` (incl. ARCHIVED) exacto] |
| 1.3 | **Discard masivo de filas shadow** (ítem 4b) — **en ventana sin ciclo** (codex plan r1): el cron del ciclo corre a las 08:40 UTC (DEPLOY.md §Crons); 1.3 y 1.4 se ejecutan seguidos, en una ventana <30 min lejos de esa hora, y ANTES del flip se re-verifica `SELECT max(id) FROM optimizer_cycle` sin cambio desde el discard y cero filas shadow no terminales (una fila shadow nueva entre discard y flip bloquearía por clave de efecto el corte live fresco vía skip `veto_pendiente`). **Nota de historia (Greptile PR #45)**: la fila shadow **2** ya se descartó el 2026-08-29 en el preflight 1.1 (motivo declarado en `discard_motivo`, para liberar la clave de efecto y probar CORTES 03 en vivo): el conteo del día NO la incluirá y su estado pre-discard vive en la propia fila (`discarded_at` + motivo) y en los backups diarios; el `RETURNING` debe conciliar contra las filas shadow pendientes DEL DÍA, no contra un número histórico. Con `ORBIT_DSN_ADMIN`, UNA transacción: `SELECT count(*)` antes → `UPDATE apply_queue SET estado='discarded', discarded_at=now(), discard_motivo='flip ORBIT 05 <fecha>' WHERE modo='shadow' AND estado IN ('pending_veto','released') RETURNING id` → conteo después; el trigger exige admin (candado PR #26). Filas `vetoed` intactas. `[tdd:skip:ops]` | `RETURNING` = el conteo previo DEL DÍA (al 2026-08-29 queda solo la fila 5 en `pending_veto`: la 2 se descartó en el preflight 1.1 y la 3/4 son `vetoed`; súmense las filas shadow que nazcan hasta el flip); `SELECT` posterior: cero filas shadow no terminales; 3 y 4 siguen `vetoed`; evidencia con ambos conteos | 1.2 | cc:完了 [2026-09-02 08:52:54 UTC, lead. Re-verificación previa: max ciclo=32 (los dos ciclos shadow de hoy, 31 US / 32 MX, `done`), conteo previo DEL DÍA=2 (`pending_veto` shadow: ids 5 y 6; ninguna fila shadow nueva nació en los ciclos de hoy). UNA transacción con `ORBIT_DSN_ADMIN`: `RETURNING` = {5,6} = el conteo previo; post: cero filas shadow no terminales, 3 y 4 siguen `vetoed`, motivo 'flip ORBIT 05 2026-09-02' en ambas, max ciclo sin cambio (32). Nota de proceso: el primer intento corrió sin efecto por `docker exec` sin `-i` (stdin no adjunto, psql salió 0 en silencio) — detectado porque el SELECT post no cuadraba; re-ejecutado con `-i` y verificado] |
| 1.4 | **Flip** (ítem 5): (a) config NUEVA (append-only, patrón 4.2 / APPLY §11: copia de la vigente con `ads_optimizer_mode='live'` y las mismas 10 claves de caps/targets, label `flip ORBIT 05 <fecha>`); (b) `UPDATE ads_optimizer_goal SET mode='live', updated_at=now() WHERE id IN (<goals firmados en 1.1>)`; (c) SELECT de config vigente y goals (`mode`, **`enabled`** — antes y después del UPDATE; un goal con `enabled=false` sigue en shadow por `modo_efectivo`); (d) `GET /api/ads-optimizer/status` cita `live` en la escalera y `HAY_MODULO_APPLY` con escalera live (ítem 10). Nada más se toca ese día. `[tdd:skip:ops]` | Config vigente `live`; goals firmados en `live` **y `enabled=true`** y el resto en `shadow`; status/salud lo reflejan; evidencia con los SELECT; hora exacta del flip registrada | 1.3 | cc:完了 [2026-09-02 08:53:32 UTC, lead. (a) config NUEVA id **11** (append-only: `jsonb_set` de la id 10, mismas 10 claves caps/targets, `ads_optimizer_mode='live'`, label 'flip ORBIT 05 2026-09-02', `INSERT 0 1`); (b) `UPDATE ads_optimizer_goal SET mode='live'` en ids 4,5,6,7 (`UPDATE 4`, RETURNING verificado); (c) SELECTs antes/después: goals shadow→live, `enabled=true` intacto en los 4, config vigente = 11 live; (d) endpoints `/api/ads-optimizer/status` y `/api/dashboard/salud` responden 200 (su `ultimo_ciclo.mode=shadow` es correcto: ciclo 31/32 corrió pre-flip) y **verificación directa con la función real** `modo_efectivo` dentro del contenedor: escalera global = live y los 4 goals (4 MX, 5 US, 6 y 7 campaña) → `modo_efectivo=live`. Hora exacta del flip registrada: **2026-09-02 08:53:32 UTC**. Nada más se tocó] |
| 1.5 | **Primer ciclo live y rampa** (ítems 6-7): esperar el ciclo del cron (no forzar `/run`); verificar: bids aplicados ≤ cap por plataforma y `apply_quota_state` con filas (primera cobrada), ledger `apply_attempt` tipo `normal` con ack + readback + sello, cortes nuevos en cola `modo='live'` `pending_veto` con `vence_el = +48h`, aviso Telegram de encolado recibido, cero filas shadow nuevas. Cualquier `failed` o readback divergente → PARAR con el **rollback COMPLETO** (codex plan r1 + CodeRabbit/Greptile PR #44: bajar solo la escalera no basta y la reversa de bid no cubre todo): (i) config nueva con escalera `shadow` Y `goal.mode='shadow'` en los goals encendidos; (ii) discard admin (una transacción, motivo `rollback ORBIT 05 <fecha>`) de TODA fila live `pending_veto/released` — si quedaran, al reactivar live vencerían y se aplicarían de golpe; (iii) **filas `applying` NO se descartan** (la máquina lo prohíbe): cada una se resuelve por readback LIST + ledger (aplicada de verdad → tratar como applied; no → `failed` con nota) ANTES de cerrar el rollback; (iv) **compensación por tipo de mutación ya aplicada**, decisión escrita del dueño por decisión: bid verificado → `POST /reversa/bid`; pause → `POST /reversa/pause` (resume); negative → `POST /reversa/negative` (archivar); harvest → parcial ya lo revierte `apply_harvest` (reversa automática + alerta), completo → archivar la keyword creada (reversa de harvest, una por decisión, ledger); **bid con readback divergente o `failed`** → los endpoints de reversa lo RECHAZAN (exigen `applied_cycle_id`, que solo nace tras verificación) → reversión MANUAL (PUT por el cliente de escritura con autorización del dueño, o consola de Amazon) con nota en ledger; (v) si el dueño decide CONSERVAR alguna mutación, el resultado se registra como **rollback PARCIAL**, nunca como completo; cierre del rollback = cero `applying` y cero mutaciones externas sin resolver (SELECT + readback en la evidencia); (vi) análisis antes del siguiente ciclo. `[tdd:skip:ops]` | SELECTs del primer ciclo en la evidencia; quota `used ≤ cap`; sin `failed`; el dueño recibió el aviso; decisión explícita "seguimos" del lead+dueño | 1.4 | cc:完了 [2026-09-02, lead con el dueño presente; corrida A MANO por enmienda del dueño ("corre hoy", ver header). Foto previa 16:12 UTC: max ciclo 32, max `apply_attempt` 29 (solo probe), max `decision` 1981, cola live 0, shadow no terminal 0, `apply_quota_state` 0 filas; config vigente 11 `live` con caps 10/2/5/2. **US ciclo 33** (16:12:50-51, `live`, `done`): 76 decisiones bid, `bids_aplicados` 10, `bids_descartados` 66 (tope), `bids_divergentes` 0, cortes encolados 0; `apply_attempt` 30-39 tipo `normal`, `quota_cobrada`, `resultado=ok`, ack de Amazon con `keywordId`/`targetId` por fila; `apply_quota_state` `ads_optimizer:amazon_us:bid` 2026-09-02 used 10 / cap 10. **MX ciclo 34** (16:14:07, `live`, `done`): 47 decisiones, 10 aplicadas, 37 descartadas, 0 divergentes, 0 cortes; `apply_attempt` 40-49 igual; quota `amazon_mx:bid` 10/10. Readback: `ad_entity_state.current_bid` = `new_value` redondeado a centavos en las 20 (Amazon redondea; reconciliación ROUND del motor, 0 divergencias). Las 20 son BAJADAS (US todas −25%; MX −12% salvo dos −25%). Cola live: 0 filas (ningún corte salió; `veto_pendiente` MX 2 = filas 3/4 vetoed hasta 09-27, correcto); cero filas shadow nuevas; `degradacion_live` null; logs del contenedor sin errores. Tabla de negocio de las 20 entregada al dueño en sesión. **Aviso Telegram de cap agotado**: envío fail-silent, SIN NOTA `telegram.cap_agotado` en notes de 33/34 (= no falló); recepción de los DOS avisos (US y MX) confirmada por el dueño, literal «si y si» (2026-09-02). **Hallazgo**: 2 de 20 en campañas PAUSED (1989 US 3918, 2104 MX 165) → `plans/campana-activa-01.md`; decisión del dueño: conservar ("dejalas asi"). **"Seguimos"** = decisión del dueño de continuar en live y arreglar el gate antes del papeleo (literal "antes de cerrar papeleo vamos a arreglar eso"); sin rollback] |

## Phase 2 — Primeras 48h live [lane:gate]

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 2.1a | **Herramienta del expediente adversarial (Cursor)** — tools/dossier_adversarial.py + prompt_revisor.md + tests; la corrida contra produccion y el envio a codex/grok/qwen son del lead | DoD concreto: tool + tests verdes + prompt generado; cero secretos en salida; allowlist | 1.5 | cc:完了 [tool + 5 tests verdes; prompt generado con nombres reales; escaner fail-closed; allowlist de claves. Corrida prod y envio = lead] |
| 2.1 | **Verificación adversarial TRIPLE** (ítem 9, sellado 6b): codex + grok + qwen reciben las primeras decisiones APLICADAS **SANEADAS** (decisión + inputs congelados + request/ack pasados por el scrub del repo + readback; JAMÁS tokens, headers, profile ids ni ids de cuenta — los datos comerciales (keyword, gasto, ventas, bid) sí viajan, como en todos los cross-reviews previos; codex plan r1: divulgación declarada en 事前確認), con muestra MX forzada (mínimo 5 MX si existen; si no hay MX aplicadas el día 1, se declara y se repite al primer día con MX). Cada uno recalcula contra las reglas selladas y busca divergencias entre decisión, request, ack y readback. Tope: 1 ronda; residuales declarados. `[tdd:skip:verificacion-adversarial]` | Tres reportes adjudicados por el lead; cero divergencia sin explicar; si hay bug → escalera a shadow y tarea de fix ANTES de seguir | 1.5 | cc:TODO [insumo listo desde 2026-09-02: 20 aplicadas (10 MX → la muestra MX forzada existe), decisiones 1989-2057 US / 2059-2104 MX, `apply_attempt` 30-49. El "bug" ya hallado por el lead (campañas PAUSED) se declara a los tres revisores como conocido y en fix (#123); la escalera NO baja a shadow por él: no mueve dinero] |
| 2.2 | **Spot-check del dueño sobre las primeras aplicadas** (sellado 6c): tabla en lenguaje de negocio (keyword, campaña, gasto, ventas, ACoS, acción, ack) de las primeras ≥10 decisiones aplicadas + la cola live pendiente; el dueño firma o veta. `[tdd:skip:checkpoint-humano]` | Firma del dueño en AppFlowy con fecha; vetos ejecutados por él si los hay | 1.5 | cc:完了 [2026-09-02: tabla de negocio de las 20 aplicadas (país, keyword/target, campaña, bid antes→después, %) entregada al dueño en sesión el mismo día; cola live pendiente = 0. Decisión del dueño sobre las 2 en campañas pausadas: conservar ("dejalas asi", registrada en AppFlowy y en CHAT-CONTEXT). Las 18 en campañas activas: FIRMADAS por el dueño, literal «si y si» (2026-09-02, respuesta a «¿firmas las 18…?» y «¿te llegaron los 2 avisos…?»); cero vetos. Registrado en AppFlowy ORBIT 05 con fecha] |
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

## 2.1a — decisiones y logs rojos (Cursor)

### Decisiones (brief ↔ codigo)

- `decision_application.applied_cycle_id` vive en `migrations/0002_apply.sql` (no en 0001). El fixture aplica 0001+0002+0003, como `tests/test_cycle.py`.
- `pais/plataforma` de la tabla MD sale de `inputs.platform` (congelado). `ad_entity.platform` existe en el esquema pero no entra en `CLAVES_ENTIDAD` (allowlist sellada: sin ids de cuenta ni columnas de mas).
- `replay_coincide` compara `kind`, moneda y `Decimal(new_value)` cuando hay plata; si `new_value` es `None` (pause) no se llama `Decimal(None)`.
- `reproduce` vive en `app/optimizer/replay.py`, que solo depende del motor puro. `app.cycle` lo reexporta para conservar el spot-check 4.4. El tool importa el modulo puro y el test en un proceso limpio sella que no carga `app.ads` ni `app.apply`.
- El escaner compila patrones sin distinguir mayusculas y minusculas. Tambien recorre claves JSON con `casefold` antes del replay y antes de publicar.
- El replay queda aislado por decision. Un `inputs` invalido produce `replay_coincide=false` y valores nulos sin impedir que las otras decisiones entren al dossier.
- El readback incluye `status`. Los intentos salen en orden `(decision_id, seq, id)` y el resumen MD usa el ultimo intento `normal/ok`, no una reversa posterior.
- La publicacion arma los tres archivos en `.staging-<pid>` con permisos 700 y los mueve al destino solo despues del escaneo completo en memoria.
- El JSON de salida lleva wrapper `{generado_utc, ciclos, registros}`. El brief no nombro el contenedor; sin el, el archivo seria un dump suelto.

### Log ROJO (regla 9, codigo ausente)

Corrida: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_dossier_adversarial.py -q --tb=short`

```text
==================================== ERRORS ====================================
______________ ERROR collecting tests/test_dossier_adversarial.py ______________
ImportError while importing test module '/workspace/tests/test_dossier_adversarial.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
/usr/lib/python3.12/importlib/__init__.py:90: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
tests/test_dossier_adversarial.py:36: in <module>
    import dossier_adversarial as da  # noqa: E402
    ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E   ModuleNotFoundError: No module named 'dossier_adversarial'
=========================== short test summary info ============================
ERROR tests/test_dossier_adversarial.py
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 0.38s
```
