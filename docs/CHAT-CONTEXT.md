# Orbit — contexto para Claude Chat

> Archivo mantenido por la sesión lead de Claude Code: se actualiza al cierre
> de cada phase. Si la fecha de abajo se ve vieja, pide al dueño que haga
> "Sync now" en el Project o pregúntale el estado antes de asumir.

**2026-08-29 — ORBIT 05 PREFLIGHT CERRADO (1.1-1.8, todo en master).** Las ocho tareas están Done: CORTES 03 desplegada y verificada en vivo (1.1), defaults de piso/techo por moneda + migración 0003 aplicada en producción (1.2), `tools/snapshot_listas.py` con conciliación real contra el cache (1.3), quota `used/cap/fuente` en el endpoint + aviso Telegram de cap agotado (1.4), la pantalla `/salud` dibujándola (1.5), las cuatro decisiones del dueño registradas (1.6), destino de harvest US reactivado con dedup y terna del goal 5 sembrada (1.6a), y el hito de revocación de `orbit_test` ADMIN OPTION cerrado en DEPLOY.md con su tarea de tracker (1.7).

**Estado de la cola de apply hoy (SELECT en vivo, 2026-08-29): 5 filas, TODAS `shadow`** — 1 `discarded` (la 2, con motivo declarado por el dueño para probar CORTES 03 en vivo), 2 `vetoed` (la 3 por delegación, actor `gon`; la 4 veto PERSONAL del dueño, actor `gon-personal`) y 2 `pending_veto` (la 5, harvest MX; la 6, el primer harvest US nacido del ciclo 24). **Cero escrituras a Amazon**: la escalera sigue en `shadow` y los cuatro goals (4 MX; 5/6/7 US) están en `mode='shadow'` con la config vigente id 10. Base viva: 24 ciclos desde el 2026-08-23 y 1,475 decisiones (1,389 bid / 48 negative / 34 pause / 4 harvest).

**Lo que FALTA para el flip** — nada de esto es código; son candados humanos y de calendario (`docs/APPLY.md` §12 ítems 3-10): (a) **2 semanas de shadow cumplidas, llegan ~2026-09-07** (shadow desde 2026-08-24); es el único sub-ítem abierto del candado 3, porque la firma del spot-check (b) y CORTES 03 desplegada (c) ya están cumplidas; (b) **la firma del dueño sobre `plans/orbit-05.md`** (runbook del cutover: backup real → discard con `app_admin` → flip doble → rampa), acto humano que ningún recálculo de la IA sustituye; (c) el **backup pre-cutover REAL del mismo día** —el de `backups/precutover_orbit04_2026-08-28/` quedará obsoleto porque la base y las listas cambian a diario— y el discard masivo de las filas shadow; (d) la **verificación adversarial triple de las primeras decisiones APLICADAS en vivo**. Deudas declaradas abiertas: `orbit_test` conserva ADMIN OPTION sobre el cluster de prod hasta que la DB de test salga de ahí (hito y tarea de tracker en `docs/DEPLOY.md`), y quedan 6 textos EXACT duplicados entre 3909↔3926 fuera del alcance autorizado de 1.6a.

**2026-08-29 — ORBIT 05 preflight 1.6a CERRADA** (PR #54 → master `38693aa`; GO del dueño "go con la 1"): USPerNog Exact US (3919) **reactivada por API con dedup** — 9 pausas de keywords EXACT ANTES del resume por el camino sellado de PR #37 (cada PUT con readback, reconciliación 9/1 ok; `--solo-campana` + `--esperado-external` anti-typo + guard de ya-ENABLED, 17 tests). La re-derivación colapsada (v_metric_latest) cazó que la tabla del lead sumaba la bitemporalidad SIN colapsar (inflaba 2-4.6x) y volteaba 1 fila ('silver arras for wedding'); el dueño resolvió "A": lista tal cual. Quedan DECLARADOS 6 textos EXACT duplicados fuera del alcance (5 entre 3909↔3926, varios convirtiendo en ambas; + 'arras de boda cristiana' 3919↔3909, copia 3909 sin datos 90d que la tabla listó "—") — para decisión futura, nada se tocó fuera de lo autorizado. **Goal 5 con terna completa** (251723662158466 / 522582072501798 / **0.68 USD** = mediana de las EXACT ENABLED de las 3 hermanas n=21, decisión "A" del dueño; la mediana literal n=3 daba 1.25). **Ciclo shadow 24 US: desaparece `harvest_sin_config` y nace la primera decisión harvest** (1475, 'arras para boda cristiana': 58 cl / $24.76 / 2 órdenes / $203.20) hacia la terna sembrada — con EXTERNALES en inputs, encolada shadow pending_veto, cero escrituras. Evidencia `out/orbit-05-preflight-1-6a-20260829.md`.

**2026-08-29 — ORBIT 05 preflight 1.5: la pantalla /salud muestra la quota del dia** — la tarjeta de cada plataforma dibuja "Quota del dia" con una fila por forma (bid/pause/negative/harvest, recorriendo el dict que llega del endpoint de 1.4): used/cap y estado — `fila_del_dia` (chip ok: el cap INMUTABLE que rige hoy) vs `config_vigente` (chip neutro: sin consumo hoy); cap nulo se ve como "—" CON etiqueta (`sin_clave` = fail-closed, `config_rota` = estado de alarma), jamas como 0; `used >= cap` se ve SATURADA (el estado que dispara el aviso Telegram de 1.4). Server-rendered, sin JS ni CSS nuevo (CSP 'self'), cero escritura.

**2026-08-29 — ORBIT 05 preflight 1.4: la quota ya es visible antes del primer cobro real** — `/api/dashboard/salud` expone por plataforma y forma (bid/pause/negative/harvest) `used/cap` con su `fuente` (`fila_del_dia`: el cap INMUTABLE de la fila de hoy aunque la config cambie / `config_vigente` / `sin_clave` = fail-closed explícito), y el ciclo avisa por Telegram (fail-silent, UNA vez por cap y día, anclado en la transición `used == cap`) cuando una rampa se agota; si el canal falla queda la NOTA en Salud y el ciclo sigue 'done'. Detalle en `docs/APPLY.md` §5.6.

**2026-08-29 — ORBIT 05 preflight 1.3 CERRADA** (PR #48 → master `14ae6c0`): el snapshot de listas de Amazon del backup pre-cutover deja de ser codigo inline y es `tools/snapshot_listas.py` con tests — lee los tres `/sp/*/list` con la paginacion existente (guard de `totalResults` incluido), agrupa por campana, concilia contra `ad_entity` con diferencia CON SIGNO y no puede mutar nada (candado de arquitectura con allowlist positiva). Corrida real: MX 2,645 kw / 2,597 neg / 861 targets; US 1,336 / 1,536 / 549. **Diferencia 0 contra el cache en keywords y targets de ambas plataformas** (verificado por el lead con SELECT propio sobre `ad_entity` incl. ARCHIVED); los negativeKeywords NO tienen espejo en `ad_entity`, así que su conteo va declarado con `cache=None` y queda FUERA de esa conciliación. Escritura endurecida tras 6 hallazgos de bots (temporal exclusivo, dir 700, symlink rechazado, runbook que propaga el rc). Preflight: 1.1, 1.2 y 1.3 Done; siguen 1.4/1.5 (quota visible), 1.6a (destino harvest US), 1.7 y 1.8.

**2026-08-29 — ORBIT 05 preflight 1.2 CERRADA** (PR #46 → master `66d449e`): default de piso/techo de goals **POR MONEDA** (USD 0.10/2.50, MXN 1.00/45.00; otra moneda = error explícito) con `DEFAULTS_POR_MONEDA` como fuente única, y **migración 0003 aplicada en goncloud el 2026-08-29 04:10 UTC** (GO del dueño; chequeo previo: cero goals MXN con techo USD; backup del schema; verificado `column_default = NULL` con `NOT NULL` intacto y los 4 goals sin cambio): la DB ya no tiene DEFAULT, así que **un goal que nazca sin piso/techo revienta** en vez de heredar números pensados en USD. Preflight: 1.1 y 1.2 Done; siguen 1.3-1.8.**

**2026-08-28 — ORBIT 05 preflight 1.3: el snapshot de listas Amazon del backup pre-cutover es ahora el tool `tools/snapshot_listas.py` del repo con test (en 4.4 corrió inline); flags excluyentes `--out`/`--solo-conteos`, escribe `listas_por_plataforma.json` a 600.**

**2026-08-29 — CORTES 03 MERGEADA (#43 → master `5e5b16b`) y DESPLEGADA en
goncloud (preflight 1.1 de ORBIT 05, GO del dueño; imagen `a5d5d579`,
constantes 100/100/40/500 leídas dentro del contenedor). Verificación con
ciclos shadow 19 (US, 109) y 20 (MX, 47) por el mismo camino del cron:
cero pauses nuevas, 156/156 decisiones congelan `cost_min_usado`; **prueba
directa** en el ciclo 21 (tras descartar con motivo la fila shadow 2 que
bloqueaba su clave): la keyword de la 774 (72 clics / $25.21 / 0 ventas)
salió como bid 0.25 → 0.22 (banda −12 %, `umbral 100`, `cost_min_usado
40`) — **ya no pausa**. Efecto del techo MX
45.00: `rango_bloquea_ajuste` MX bajó de 43 (ciclo 18) a 2 (ciclo 20). Las
47 decisiones MX del ciclo 20 concilian así: 5 que ya decidían en el 18 +
41 desbloqueadas por el techo (43 → 2) + 1 que salió de
`pause_cortes_incompleto` (47 → 46); por banda: 37 −12 %, 6 +15 %, 4 −25 %;
máx old 42.63 → new 37.51, dentro del techo.
Checklist §12 ítem 3(c) marcado.** Detalle previo (PR #43):
umbral de PAUSE del dueño → **100 clics / 40 USD / 500 MXN** (origen
spot-check 4.4 fila 30 / decisión 774: 72 clics / 25.21 USD / 0 ventas
pausó prematuro); fallback y piso legacy de PAUSE también suben a 100;
NEGATIVE intacto. Replay hecho **fiel por construcción** (decisión del
lead 2026-08-28): el motor de bids congela `cost_min_usado` en su freeze y
las filas históricas sin la clave rejuegan con los históricos REPLAY_*
(25 clics / 12 USD, solo-replay) — 34/34 pauses medidas fieles.

**Última actualización: 2026-08-28 — ORBIT 04 CERRADA (4.1-4.4, todo en
`shadow`); el dueño FIRMÓ el spot-check el 2026-08-28 ("spot check
confirmado", revisado en lenguaje de negocio con el lead) → DoD de 4.4
cumplido, `ORBIT 04` Done en AppFlowy:**

- **4.1** deploy endurecido (env por servicio, non-root uid 10001, 0002
  aplicada, wiring admin→ledger).
- **4.2** seeds: goal 4 (platform MX) con terna harvest → Arras Manual
  (2.50 MXN, mediana de bids EXACT reales); goals 6/7 scope=campaign (A1U
  3909, AU2 3926) en shadow; caps día 1 en config id 7 (10/2/5/2 por
  plataforma); fail-closed de quota probado en vivo en ambos sentidos.
- **4.3** ensayo E2E: 4/4 formas neto-cero contra Amazon real (ledger probe
  22-29), SHAPES re-confirmados; veto real por endpoint (fila 3, actor
  'gon', delegado) + **veto PERSONAL del dueño (fila 4, actor
  'gon-personal', 06:54 UTC)**; neto-cero RE-VERIFICADO post-sync en 4.4.
- **4.4** backup pre-cutover VERIFY_OK en
  `backups/precutover_orbit04_2026-08-28/` (dump 762 KB + globals + CSV de
  ad_entity_state 5,899 filas + listas Amazon de 2 plataformas; restore real
  con conteos idénticos 4/29/9/977/5899); spot-check de 33 decisiones shadow
  recalculadas por el implementador (GLM, autor del motor: NO es una
  verificación independiente) + 11 re-calculadas por el lead desde
  `bid.py` (**0 divergencias** en ambas; tabla en AppFlowy "ORBIT 04 4.4 —
  spot-check" y en `out/orbit-04-4-4-cierre-20260828.md` §3 — **FIRMADA
  por el dueño 2026-08-28**, la única validación independiente; de su
  revisión salieron el techo MX y CORTES 03, ver abajo);
  escalera shadow verificada (config id 10 mode=shadow, attempts solo probe,
  quota 0 filas, cola 2 pending + 2 vetoed, `/api/ads-optimizer/status`
  cita shadow); corrección 1e41a1f: la verificación adversarial TRIPLE se
  movió al checklist §12 (ítem 9, ritual de ORBIT 05). La FIRMA del dueño
  del spot-check quedó como ítem 3 SIN MARCAR del checklist §12: candado
  pre-flip.

**Estado de la cola (2026-08-29, tras el preflight 1.1)**: fila 2 pause
**`discarded`** (descartada por el dueño con motivo declarado para liberar
la clave de efecto de la keyword de la 774 y probar en vivo CORTES 03),
fila 3 harvest `vetoed` (gon), fila 4 harvest `vetoed` (gon-personal),
fila 5 harvest `pending_veto` — cero released/applying. El día del cutover
queda **una** fila shadow pendiente (la 5, más las que nazcan hasta
entonces): se descartan en bloque con `app_admin`, en el orden sellado
**backup real → discard → flip → rampa** (checklist APPLY.md §12 ítems 4-6).

**Prerequisitos de ORBIT 05**: cumplidos — veto del dueño (fila 4, con su
mano), ensayo E2E (4/4 neto-cero), **runbook del backup pre-cutover
ensayado y verificado restaurable** (el snapshot del 28 NO es el punto de
restauración del flip), caps día 1 sembrados, spot-check preparado (33
decisiones, implementador + 11 del lead) **y FIRMADO por el dueño
2026-08-28** (AppFlowy "ORBIT 04 4.4 — spot-check shadow", Done).
**Pendientes** — 2 semanas de shadow (~2026-09-07);
`tools/snapshot_listas.py` con test; **backup pre-cutover REAL el día del
flip** (ítem 4); verificación adversarial TRIPLE de las primeras
decisiones live (ítem 9); resto del preflight (1.2-1.8 de
`plans/orbit-05-preflight.md`). **CUMPLIDOS post-#40**: techo de bids MX
1.00/45.00 MXN en el goal 4 (ver abajo) y **CORTES 03 mergeada, desplegada
y verificada en vivo el 2026-08-29** (preflight 1.1; ítem 3c del checklist
§12 marcado).

**Decisiones del dueño salidas del spot-check (2026-08-28, post-#40)**:
(1) **Techo de bids MX**: el default 2.50 del esquema (número pensado en
USD, `goals.py DEFAULT_CEILING`) estaba aplicado al goal 4 de México en
MXN — 144/233 keywords y 44/51 targets MX activos tienen bid > 2.50 MXN
(mediana 2.92 / 8.98, máx 42.63): en live los habría aplastado hacia 2.50.
Verificado que NO hay mezcla de monedas (`bid.py:89` fija MXN/USD por
plataforma; cache y decisiones 100% consistentes). El dueño aplicó desde su
terminal `goals set 4 --floor 1.00 --ceiling 45.00` (verificado por SELECT,
`updated_at` 18:05 UTC); US queda 0.10/2.50 (máx real 2.00). (2) **Umbrales
de pausa**: 72 clics / $25 / 0 ventas (fila 30 del spot-check) le pareció
poco → PAUSE exigirá **≥100 clics y ≥$40 USD** sin ventas (**MX: ≥500
MXN**, confirmado por el dueño) = tarea **CORTES 03** (CORTES 02 ya es la lista curada de términos producto-diferente; cambio de spec v3: `cortes.py
F_PAUSE/LEGACY_PAUSE`, `bid.py PAUSE_COST_MIN`, tests, docs/traspaso),
prerequisito de ORBIT 05, implementa GLM por PR con TDD. La firma del
spot-check se cumplió el 2026-08-28.

**Decisiones del dueño para ORBIT 05 (2026-08-28, respuesta literal "1. si
2. no se 3 acotar 4 todos" a las 4 preguntas de `plans/orbit-05-preflight.md`
1.6)**: (1) **destino harvest US = SÍ**: reactivar USPerNog Exact US
(ad_entity 3919, external 251723662158466, hoy PAUSED) y sembrar la terna
del goal 5 (preflight 1.6a; mutación real con autorización en el momento);
(2) **AGM2M (165) = DIFERIDO** ("no sé"): PAUSED, fuera del piloto, se
re-pregunta tras 48h live; (3) **halo US = ACOTAR con ambos supuestos**
(CONTEXTO "la pregunta sin respuesta"): la fase margin-aware reporta y
decide con el rango con-halo / sin-halo; ORBIT 05 sigue con revenue
completo; (4) **goals del día 1 = TODOS** (4 MX, 5/6/7 US); la rampa por
goal queda como mecanismo de rollback parcial. Con esto los planes
`orbit-05-preflight` y `orbit-05` quedan APROBADOS por el dueño — evidencia
de la aprobación: su mensaje literal en la sesión del lead (2026-08-28) y
la nota fechada en la fila `ORBIT 05` de AppFlowy (la PR #44 lleva la
misma cita; no hay review `APPROVED` de GitHub porque el dueño no revisa
por GitHub). El cutover no arranca antes de ~2026-09-07 ni sin el preflight
Done.

Previo (2026-08-28): ORBIT 04 4.3 CERRADA con DoD literal
(ensayo E2E + veto delegado en la fila 3 + VETO PERSONAL DEL DUEÑO en la
fila 4, actor `gon-personal`, 06:54 UTC, verificado en `apply_queue`;
prerequisito de ORBIT 05 cumplido). Review del lead post-cierre: pipefail
al shell local, token 600 por umask, residual del token en el historial
append-only de `config_version`, DoD de 4.4 con spot-check ≥20 +
adversarial triple, y el "neto-cero contra el cache" de la evidencia §6 era
ANTERIOR a la corrida (re-verificar post-sync en 4.4): re-corrida del smoke 2.5
contra el deploy real, mismas campañas sacrificables (A: USPerNog Category
Exact 251723662158466 — hoy PAUSED; B: USPerNog Auto Discovery
140602818838686), dentro del contenedor (tool a `/tmp` + `PYTHONPATH=/app`:
post-4.1 la imagen es non-root y sin tools; variante documentada en
APPLY.md §11d y en el docstring del tool). **4/4 formas ok/neto-cero**:
bid_keyword 0.51→0.52→0.51, negative crear+archivar, keyword crear+archivar
(bid 0.51 real leído), bid_target 0.32→0.33→0.32; ledger `apply_attempt`
probe ids 22-29 (quota_cobrada=false), config 8/9 de humo + **id 10 de
cierre limpia** (11 claves: mode, targets 20/20, caps 10/2/5/2). SHAPES
re-confirmados. **VETO REAL por el endpoint** (`POST /api/ads-optimizer/veto`):
fila 3 (harvest "arras matrimoniales cristianas") → `vetoed`,
`vetoed_by='gon'`, vence 2026-09-27. **Declarado: ejecutado por el lead por
delegación expresa del dueño** ("el veto el que consideres mejor"); la fila
2 (pause de una keyword con 62 clicks/$22.78/0 órdenes en 30d) se dejó
intacta a propósito — es un corte correcto. **Veto PERSONAL del dueño CUMPLIDO** el
2026-08-28 06:54 UTC: fila 4 (harvest "arras matrimoniales personalizadas")
→ `vetoed`, `vetoed_by='gon-personal'`, vence 2026-09-27 — ejecutado por
él desde su terminal contra el endpoint real, verificado en `apply_queue`.
La cola queda sana para el cutover: 2 shadow `pending_veto` (filas 2 y 5)
→ descarte en bloque en el flip; 2 `vetoed` terminales (3 y 4). Resto de la cola de fase: 4.4 cierre
(backup, CHAT-CONTEXT, PR final). Evidencia
`out/orbit-04-4-3-ensayo-e2e-20260828.md` + `out/smoke-apply-20260828.log`.

Previo (2026-08-27, noche): ORBIT 04 4.2 CERRADA (seeds de
configuración en vivo): goal 4 (platform amazon_mx) con terna harvest
completa → Arras Manual (external `97835222467967`, ad group
`272585315669297`, ambos ENABLED; `harvest_default_bid` 2.50 MXN = mediana
2.525 de los bids reales de 18 keywords EXACT ENABLED clampeada al techo
2.50 del goal), escrita por el camino único (`goals set` →
`goals_write.edita_goal`). Goals 6 y 7 scope=campaign: A1U Exact US (3909) y
AU2 Exact US (3926), USD, shadow, target NULL (la cascada da 20 desde
config). Caps día 1 en `config_version` id 7 (fila NUEVA append-only, la 6
intacta): `ads_apply_cap_*` = 10 bids / 2 pauses / 5 negatives / 2 harvests
por día y plataforma (rampa sellada: decisión 7 + APPLY.md §5.5). DoD en
vivo: `goal_harvest_completo` rechaza la terna a medias (CLI exit 2 y
`CheckViolation` con ROLLBACK); fail-closed de quota probado en ambos
sentidos contra el trigger vivo (sin clave no nace fila; con clave nace con
el cap de config; cap que no coincide también se rechaza). Divergencia con
la decisión sellada 15 ratificada por el dueño: manda el brief
(destino MX = Arras Manual; USPerNog 3919 sigue PAUSED — el destino US de
harvest queda como decisión abierta). `apply_quota_state` sigue en 0 filas
hasta el primer apply real; sistema en shadow. Evidencia
`out/orbit-04-4-2-seeds-20260827.md`. Pendiente Phase 4: 4.3 ensayo E2E +
veto real del dueño, 4.4 cierre.

Previo (2026-08-27, tarde): REACTIVACIÓN POR API EJECUTADA
(autorizada por el dueño, "hazlo tú"): 25 keywords pausadas (dedup de
CAMPANAS 01: 4 en Arras Manual 108, 18 broads en AD_READY 157-160, 3 phrases
en AU2 3920) y 5 campañas reactivadas (108 Arras Manual MX, 3934 Wedding
Coin ASIN US, 3911 A1U Category Phrase US, 3909 A1U Category Exact US, 3926
AU2 Category Exact US), cada una con readback verificado y cache sincronizado
(ingest_run 19, ok). AGM2M (165) quedó FUERA (veredicto reactivar-con-ajuste:
decisión aparte). Herramienta `tools/reactiva_campanas.py` (operación de
NEGOCIO por API, no del motor: no pasa por apply_queue; dry-run por defecto,
`--acepto-mutacion-real` obligatorio, fail-closed contra la base viva).
Evidencia `out/reactiva-campanas-20260827.log`. **Sellos NUEVOS de la API
v3 que refutaron hipótesis del repo:** el state del PUT de pause/resume es
UPPER (`PAUSED`/`ENABLED` — `'paused'` minúscula: 400 con el enum exacto;
`'userPaused'` de write.py REFUTADO y `ESTADO_PUT_*` corregidas con sus
tests); el id del body viaja como STRING (con número: 400 `NUMBER_VALUE...`);
los headers exigen el vendor v3 EXACTO en Content-Type **y** Accept (sin
Accept: 415; campañas `application/vnd.spcampaign.v3+json` — shape de resume
de campaña NUEVO, sellado con 3909 primero). Regla 8 atrapó un error del doc
de dedup: 'arras matrimoniales de oro' BROAD no existe en la 160 (decía
×4, eran 3) — la herramienta abortó fail-closed y la lista quedó en 25.
Pendiente Phase 4: 4.2 seeds (las Exact US YA están ENABLED — destrabado),
4.3 ensayo E2E, 4.4 cierre. CAMPANAS 01 1.1 完了.

Previo (2026-08-27): ORBIT 04 task 4.1 CERRADA (deploy endurecido, EN VIVO): env por servicio (db ya no hereda el .env completo —
llevaba hasta ORBIT_DSN_ADMIN; solo POSTGRES_* por interpolación), app
non-root como uid 10001 (secrets/ chown 10001:10001 con 600/700 intactos;
se retira el residual `user: "0:0"` de ORBIT 03), y wiring admin→ledger
resuelto (`GRANT app_decide TO orbit_admin` — las reversas ya no revientan
con InsufficientPrivilege). Backup previo `backups/pre-4.1-20260827.dump`.
Verificado en vivo: health ok, 8010 solo loopback+wg0, 5432 loopback, db
env con 0 ORBIT_DSN, datos intactos (apply_attempt=21), veto sin token 401 /
con token 422 (auth lee el secret como uid 10001), bridge y crons intactos.
Decisión documentada: la membresía cluster de orbit_test SE QUEDA mientras
la suite corra por túnel (revocación atada a sacar la base de test de prod).
0002 ya estaba aplicada (SELECT: apply_queue, apply_attempt, reactivacion_manual).
Candados re-sellados con rojo demostrado (sello bloque db, db sin DSNs, app
non-root, runbook con el uid). Rama orbit-04/4-1-deploy-endurecido, PR a
master. Pendiente Phase 4: 4.2 seeds (el dueño reactiva las Exact US —
lista de dedup en out/campanas-01-dedup-20260827.md), 4.3 ensayo E2E, 4.4 cierre.

Previo (2026-08-27): ORBIT 04 Phase 3 COMPLETA (3.1-3.3) y
task 2.5 MERGEADAS a master (squash: #28 3.1, #33 3.2, #34 3.3 y 2.5
probe-shapes; #29-32 quedaron cerrados por la cascada de bases). 3.3
(`app/notifica.py`, canal Telegram fail-silent): aviso por cada
corte NUEVO encolado (con vence_el 48h de la MISMA fuente de la fila),
digest único por ciclo (live Y shadow: el encabezado declara el modo para
que un digest de shadow no se confunda con uno live — cierre del hallazgo
medio de la review del lead) y alerta de harvest failed (en `_falla_job`,
junto a la reversa). Un fallo del canal JAMÁS tumba el ciclo ni degrada el
status: warning scrubbeado + NOTA `notes['telegram']` (solo claves de lo
que falló, regla 3) integrada antes del sello post-apply — estructuralmente
TX4 corre después del cierre del envelope, ahí estaba la única escritura de
notes restante — y VISIBLE en Salud (endpoint + pantalla). Canal
deshabilitado (sin secrets/telegram.json) = no es fallo: True, sin NOTA.
Avisos también en shadow (el mensaje declara modo — el dueño practica el
veto con candidatos reales, sellado 6). Ciclos skipped/failed sin digest
(estructura del ciclo; visibles por su propio status). tests/conftest.py
aísla el canal por defecto: cero HTTP real en tests es invariante
determinístico. Review: GO tras fixes (test directo del mapeo
harvest→NOTA, loop de alertas envuelto, acento). Rojo honesto del DoD
capturado (KeyError 'telegram' contra la base 3.2). 17 tests nuevos;
161+ focused en verde con PG16 real por túnel; batería completa en el CI
del PR.

Previo (2026-08-27): ORBIT 04 Phase 3 EN CURSO: 3.1 y 3.2
listas (PRs DRAFT #28 y apilado — el lead revisa). 3.2 (goals amigables,
rama orbit-04/3-2-goals-set): UNA implementación `app/goals_write.edita_goal`
(UPDATE solo-campos-pasados con `updated_at` EXPLICITO obligatorio — mutante
demostrado cazado; pre-validación de entrada pura SIN I/O que combina
nuevo+existente: floor<=ceiling, finitos, ids no vacíos, terna harvest
all-or-nothing con `harvest_limpia`, edición vacía rechazada) despachada por
DOS superficies: POST /api/ads-optimizer/goals/{goal_id} (misma auth de 3.1)
y CLI `python -m app.cli goals set` (ORBIT_DSN_ADMIN fail-closed exit 2,
allow_abbrev=False — el hueco de `--targe` abreviado cazado por test).
Candado de camino único en test_architecture (regex IGNORECASE+\s+; tools/
fuera, declarado) y superficie OpenAPI sellada +ruta. Test de punta a punta:
editar target 25→20 y correr UN ciclo REAL — la decisión congela
inputs.target_acos_pct_usado == "20.00" (rastro completo). Declaración
sellada: NINGÚN campo de goals set vive en config_version — la regla
config=fila-nueva queda para la pantalla de settings de ORBIT 16 Phase 3.
Review: GO tras fixes (ids vacíos, edición vacía que re-sellaba updated_at,
Decimal Infinity/NaN, docstrings stale); 100 tests focused en verde con
PG16 real por túnel; batería completa en el CI del PR.

Previo (2026-08-27): ORBIT 04 task 2.5 CERRADA: el probe
real se ejecutó (2026-08-26, autorización del dueño, campaña sacrificable
amazon_us) con 4/4 formas en neto cero (evidencia out/smoke-apply-20260826.log,
ledger probe ids 1-20) y los shapes quedaron fijados contra la API VIVA —
las hipótesis adivinadas estaban mal y se corrigieron (regla 8 cumplida a
contrapelo): el bid viaja como NÚMERO JSON (no string), los enums son UPPER
(matchType EXACT/NEGATIVE_EXACT, state ENABLED/PAUSED), los deletes v3 van
por POST /sp/{recurso}/delete con filtro de ids (DELETE con body da 403),
el readback es por LIST (el GET directo da 403) y los acks son 207 con
success/error. Tests de readback de 2.1-2.3 re-sellados contra esos shapes;
cross-review del dueño (codex+qwen) cerrada (readback paginado, 207
verificados campo por campo, constantes del pause a una fuente). Residuo
verificado y limpio: los 4 términos basura zzsmokeprobe* quedaron ARCHIVED
(en Amazon delete=archivar; ledger probe ids 1-20 — el id 21 es de la
limpieza del residuo del 2.5 del 2026-08-27, script
`out/limpia_residuo_probe_2_5.py`, verificado por payload).
Rama orbit-04/2-5-probe-shapes, PR DRAFT #31 apilado sobre #29 (3.2).

Previo (2026-08-26): ORBIT 04 Phase 3 EN CURSO: 3.1 lista
(rama orbit-04/3-1-auth-escritura, PR DRAFT — el lead revisa). Auth de
escritura: token estático SOLO-header (x-orbit-token, compare_digest,
register_secret, fail-closed 503 sin secret/DSN, query string no
autentica), ConexionEscritura (ORBIT_DSN_ADMIN), POST /veto (actor,
vence_el editable default 30d, 409 en applying/terminal, 404 inexistente)
y POST /reversa/{bid,pause,negative} vía apply.reversa_manual
(negative_id SIEMPRE del ledger; una reversa por decisión — 409 "ya
revertida"), pantalla /cortes (pendientes + vencimiento + botón vetar, JS
estático CSP-self, XSS testeado), candados OpenAPI = lista sellada 3 GET +
4 POST con auth-dependency introspectada, docstring api.py corregido
(/run Reject permanente), rotación de token en DEPLOY.md (verifica con
queue_id inexistente — jamás muta). Ciclo adversario por tocar auth: 6
hallazgos; ADV-2/3/4 ARREGLADOS con tests en rojo primero; ADV-1
DECLARADO (orbit_test quedó miembro ADMIN OPTION de app_* en el cluster
para tests locales por túnel — REVOKE documentado en DEPLOY.md, 4.1
resuelve); declarado también: reversas necesitan además membresía
app_decide (GRANT apply_attempt es solo decide, NOTA en DEPLOY.md, wiring
en 4.1). 65 tests nuevos/focused en verde contra PG16 real por túnel;
batería completa en el CI del PR. Siguiente: 3.2 goals amigables (CLI +
endpoint, una implementación), 3.3 app/notifica.py.

Previo (2026-08-26): ORBIT 04 Phase 2: implementación
COMPLETA (5/5 tareas construidas y mergeables, rama orbit-04/phase-2, PR
DRAFT sin mergear — el dueño revisa) pero ACEPTACIÓN PENDIENTE: la corrida
real del probe 2.5 es acto del dueño y los tests de readback se sellan
contra los shapes reales en esa corrida: el apply integrado. (2.1) app/apply.py: re-resolución por decisión (escalera + goal,
JAMÁS inputs.modo), quota atómica con cap de config, ledger PRE-HTTP con
tope-3, readback con GET sellado, cache con LO LEÍDO, reversa de bid.
(2.2) app/apply_cola.py: encola cortes (invariante corte↔cola), skip
veto_pendiente por clave de efecto, liberación FIFO con re-validación de
evidencia FRESCA al reloj de liberación (contrato cross-plan CORTES 01),
descarte pre-cobro, filas released reintentadas al día siguiente.
(2.3) app/apply_harvest.py: harvest_job nace AL LIBERAR, reconciliación
viva por identidad completa (señuelo en otro ad group no engaña), bid
sugerido clampeado con intención pre-POST (endpoint NO pineado: v2
retirado, v3 exige SigV4 — fail-open al default sellado; regla 8
documentada), reversas keyword-primero. (2.4) fase de apply DENTRO del
lock en corre_ciclo: heartbeat + ownership-check pre-HTTP con aborto
fail-closed, guard status=running en el cierre, HAY_MODULO_APPLY=True
(escalera shadow sigue = cero HTTP, testeado tras el flip). (2.5)
tools/smoke_apply.py CONSTRUIDO y NO ejecutado (doble autorización +
campaña solo por config; corrida real = dueño). Review de fase con
adversario: 11 hallazgos (1 crítico: TX4 sin commit invisible por
autocommit en tests — corregido con test SIN autocommit), 8 fixes
aplicados y aprobados por el reviewer. Cross-review ordenada por el dueño
(codex+grok+qwen en paralelo): 6 altas reales (tope-3 contando reversas,
UniqueViolation al reusar job, reversa rompiendo keyword-primero, fases
de harvest avanzando sin ids del ack, GET sin capturar abortando el
barrido, cierre job+cola sin transacción) + medias — 13 fixes aplicados.
Suite 550 passed con batería DB real (túnel). Previo (2026-08-25): ORBIT 04 Phase 1 CERRADA (3/3 tareas,
rama orbit-04/phase-1, PR de fase): (1.1) docs/APPLY.md, el contrato fino de
PR2 — máquina de estados de la cola de cortes con clave de efecto y ventana
de veto 48h, ledger de intentos, quota sellada con mapeo config↔motor,
matriz de reconciliación, tabla de reversas y checklist de cutover; spec
deltas en CONTEXTO.md y DATABASE.md. (1.2) migración 0002_apply.sql: tabla
apply_queue (nace pending_veto, transiciones exactas por trigger, veto exige
admin por schema, fila shadow JAMÁS sale de vetoed|discarded, único parcial
NULLS NOT DISTINCT por clave de efecto), ledger apply_attempt (sello una
sola vez), reactivacion_manual, sellos de apply_quota_state (fila del día
solo desde config, used creciente, día UTC de la base), fases de
harvest_job, applied_cycle_id y GRANTs por columna; test_schema parsea 0002
y test_apply_schema ejercita el DoD en DB (CI). (1.3) app/ads/write.py:
cliente de escritura allowlist default-deny (10 mutaciones exactas, scope
sellado por instancia, moneda verificada pre-HTTP, 429 reintenta sin
recobro / 5xx no reintenta, constructor fail-closed exige modo live, candado
de imports) + negativeKeywords/list verificado EN VIVO (regla 8, 200 en US y
MX). Suite local 359 passed / 78 DB-skips; batería DB completa en el CI del
PR. Siguiente: Phase 2 (el apply integrado: 2.1 núcleo, 2.2 cola, 2.3
harvest, 2.4 integración al ciclo, 2.5 probe autorizado). Previo (2026-08-24): Phases 1–3 en master; 4.1 y 4.2 hechas
en goncloud (servicio `app` en 127.0.0.1:8010 + 3 crons aditivos, profundidad
diaria D-31..D-1). 4.3 EJECUTADA por el dueño: escalera global en shadow,
targets ACoS 20 (mx) y 20 (us — presión máxima elegida a sabiendas), TODAS
las campañas activas vía goals de plataforma; harvest sin bid fijo por
decisión (el bid sugerido de Amazon llega con el apply de PR2 — regla 3:
jamás inventar el número). 4.4 VALIDADA por el dueño: primer shadow real corrido (133 decisiones,
us 124 / mx 9), spot-check completo y verificación adversarial TRIPLE
(codex, grok y qwen: 133/133 limpias, skips cuadrando al entero).
ORBIT 03 COMPLETO (17/17 tareas): el optimizador decide EN SOMBRA todos
los dias (crons 06:45/07:10/08:40-08:41 UTC) sobre todas las campanas
activas, con cero capacidad de escribir a Amazon. El reloj de las 2
semanas de shadow para el cutover (ORBIT 05) corre desde el 2026-08-24.
ORBIT 16 Phase 1 CERRADA (7/7 tareas): dashboard de LECTURA en master
y desplegado — 4 pantallas (Resumen con gráficas, Campañas con estado
y procedencia del target, Decisiones por cursor, Salud), acceso por
túnel ssh al 8010. El dueño validó el smoke 1.7 (gráficas OK bajo CSP
estricta; pidió y recibió la columna de estado de campaña; se le
explicó con datos vivos por qué el motor negativiza sus términos
"arras": 116 clicks / 0 ventas en ventana madura — la decisión
estratégica listing-vs-negative queda anotada para el apply). La
review en cadena (lead + reviewer fresco + kimi/codex/grok + bots)
atrapó y cerró bugs reales del bloque 2. Phase 2 CERRADA: el dashboard
también se ve por la VPN WireGuard del dueño (cel y compu, validado
por él) — bind adicional en la IP wg0, allowlist EXACTA de mapeos
sellada por candado, sign-off del dueño antes de aplicar. Queda solo
Phase 3 (settings de escritura, bloqueada por ORBIT 04). CORTES 01
CERRADO (5/5): los cortes NEGATIVE y PAUSE ahora usan umbral de clicks
ADAPTATIVO por producto (evidencia del ad group 90d: expected_clicks ×
1.5, piso max con legacy) y NEGATIVE además piso de COSTO adaptativo
(AOV del producto × 1.0, respaldos 45 USD/600 MXN) — nacido del caso
arras y calibrado con 57 anotaciones del dueño (regla nueva 21/22 con
su instinto vs 0/22 de la vieja); contrafactual final: 28 cortes
legacy → 1 con el paquete. Los términos PRODUCTO-DIFERENTE se cortan
por la otra vía: lista curada por AI con aprobación del dueño
(CORTES 02, sembrada). Shadow valida la regla nueva ~2 semanas antes
del cutover. Siguiente proyecto grande: ORBIT
04 (PR2: el apply con topes). Este archivo tiene candado de frescura:
el CI exige actualizarlo en cada PR que cierre tareas.

## Qué es Orbit

Sistema nuevo desde cero que optimiza **Amazon Ads (Sponsored Products)** para
un negocio que vende en **Amazon MX y US** (y opera también Mercado Libre)
bajo el régimen mexicano de plataformas tecnológicas. Decide, con reglas
explícitas y auditables: **cuánto pujar, qué pausar, qué términos negativizar
y qué harvestear**. Reemplaza a un sistema viejo (`goncloud-MCP-2`, apagado el
2026-08-22) que murió por monolito: 62 módulos, 206 flags y 147 jobs para 3
decisiones. Orbit arranca en modo **shadow**: decide y registra todo, pero NO
escribe nada a Amazon hasta pasar validación humana (el "apply" llega en PR2).

## Estado actual (se actualiza por phase)

- **Hecho y en master**: toda la ingesta (cliente HTTP read-only con redacción
  de secretos, sync de estructura, pipeline de métricas y search terms,
  backfill histórico completo: 95 días de métricas, ~65 de terms, us y mx),
  y todo lo siguiente (mergeado 2026-08-24): la capa de ventanas de datos, el
  motor de decisión puro (bids, hygiene, goals) con todos los umbrales
  sellados y testeados, 3.1 el orquestador del ciclo (claim atómico del
  lock con TTL y heartbeat, envelope que se sella en todos los caminos,
  skips estructurados en notes, decisiones con inputs congelados y golden
  replay que las reproduce exactas), 3.2 la API de solo lectura
  (`/api/ads-optimizer/{status,audit,goals}`, GET como `orbit_read`, con
  watermarks de la misma fuente del motor y notes de formato mixto
  tolerado) y 3.3 el CLI `python -m app.cli {ingest,cycle}` (envoltorio
  delgado: el ciclo usa el mismo claim/job_key del cron y `ingest` delega
  a los pipelines de `app/ads/`); candados anti-monolito activos
  (complejidad, fronteras de imports, tamaño de módulos, frescura de este
  archivo).
- **Phase 4 (PR #15, en cierre)**: 4.1 servicio `app` vivo en el server
  (`/health` OK, puerto solo loopback, `secrets/` 0600 intactos), 4.2
  tres crons diarios (accounting intacto), 4.3 seed del dueño ejecutado
  (shadow, targets 20/20) y 4.4 primer shadow real VALIDADO (133
  decisiones, triple verificación adversarial limpia). Falta solo el
  merge (4.5).
- **Datos reales ya en la base viva** (Postgres en el server `goncloud`):
  5,897 entidades, ~22,000 observaciones de métricas, ~6,900 de search terms.

## Cómo se construye (roles)

- **GLM** (otra sesión de IA) implementa las tareas del plan.
- **Claude Code (Fable, sesión lead)** revisa cada tarea con un reviewer de
  contexto fresco, verifica contra la base viva, y mantiene tracker y docs.
- **Cross-reviews** con otras IAs (Codex, Grok, Kimi, CodeRabbit, Greptile)
  por tarea; tope duro de 2 rondas.
- **Gon (el dueño)** decide QUÉ se construye, aprueba planes, y tiene dos
  checkpoints humanos en Phase 4: elegir campañas piloto (4.3) y el
  spot-check manual de ≥20 decisiones del primer shadow (4.4).
- Un PR por phase; nada llega a master sin CI verde y reviews atendidas.
- Registro de trabajo: fila `ORBIT 04` en AppFlowy (EHV Tasks).

## Arquitectura (mapa de carpetas)

```
app/
├── redaction.py   ← ningún secreto sale en logs/errores
├── db.py          ← conexión a Postgres (DSN redactado)
├── ads/           ← ESTACIÓN 1: hablar con Amazon (única capa con internet)
│   ├── client.py     cliente READ-ONLY (sin capacidad física de mutar campañas)
│   ├── structure.py  catálogo: campañas, ad groups, keywords, targets
│   └── reports.py    métricas y search terms → base de datos
└── optimizer/     ← ESTACIÓN 2: pensar (PURO, sin internet ni base — por test)
    ├── windows.py    única puerta a la base: ventanas de datos colapsadas
    ├── bid.py        decisiones de puja y pause
    ├── hygiene.py    negative exact y harvest
    └── goals.py      metas por campaña/plataforma, modo efectivo, cooldown
app/cycle.py ← orquestador del ciclo: ventanas → motor → tabla decision (auditoría)
```

Flujo de una vía: Amazon → `ads/` → Postgres → `windows.py` → motor →
tabla `decision` (auditoría) → API/CLI de lectura.

## Reglas de diseño selladas (resumen de docs/CONTEXTO.md)

1. Una decisión, un camino, un dueño — no se construye lo que no decide.
2. Un número, una fuente.
3. Dato faltante = None y la fila no se escribe; JAMÁS una constante inventada.
4. Todo dinero lleva (valor, moneda); mezclar monedas es imposible por schema.
5. Métricas append-only bitemporales; el motor colapsa a la última observación.
6. Cortes (pause/negative/harvest) exigen datos con ≥10 días de maduración.
7. Nada irreversible sin su reversa implementada antes.
8. Verificar la forma real del dato en producción antes de testear invariantes.
9. Toda prueba de regresión se demuestra fallando contra el código anterior.
10. Conciliar contra la fuente externa, no contra consistencia interna.

## Umbrales del optimizador (sellados; fuente: diseño v2)

| Decisión | Regla |
|---|---|
| PAUSE | orders=0 ∧ clicks ≥ `max(100, ceil(1.5×clicks/órdenes del ad group))` ∧ cost≥ {us: 40 USD, mx: 500 MXN} (piso y fallback 100 = CORTES 03; umbral adaptativo = CORTES 01) |
| Bajar puja −25% | ACoS > 1.35×target (con orders≥1) |
| Bajar puja −12% | ACoS > 1.15×target |
| Subir puja +15% | ACoS < 0.85×target ∧ orders≥3 |
| NEGATIVE_EXACT | orders=0 ∧ clicks ≥ `max(20, ceil(1.5×clicks/órdenes del ad group))` (fallback 40 si el grupo no califica) ∧ cost ≥ `max({us: 8, mx: 130}, AOV×1.0)` (respaldo 45/600); ASIN-like nunca — CORTES 01 |
| HARVEST | orders≥2 ∧ ACoS ≤ min(35%, target); exige config completa en el goal |

ACoS = cost / ad_revenue COMPLETO (halo incluido). Clamp por decisión
[−30%, +20%]; resultado dentro del [floor, ceiling] DEL GOAL (goal 4 MX:
1.00/45.00 MXN por decisión del dueño; goals US: 0.10/2.50 USD — el default
del esquema, pensado en USD, deja de aplicarse a ciegas en el preflight 1.2).
Target ACoS en cascada: goal → config global → cache del estado → default 55.
Precedencia: PAUSE gana a todo; −25 gana a −12. Modo: off→shadow→live, y en
PR1 'live' degrada a shadow (fail-closed).

## Trampas del dominio (nunca olvidarlas)

- **Tres relojes**: la venta atribuida madura en 5–8 días, el costo hasta el
  día 15, los fees a 15–30. Un ACoS de día 1 en MX sale ~1.5× peor que el real.
- **Halo**: 56–58% del ingreso atribuido es de OTROS SKUs. Es atribución de
  Amazon, no causalidad. Por eso el ACoS usa el revenue completo.
- **Monedas**: el sistema viejo mezcló MXN/USD (error de 18.66× a favor de
  "todo es rentabilísimo"). En Orbit es imposible por schema.
- **El día en curso** llega incompleto y etiquetado con su fecha: el cron
  re-tira D-31..D-1 (sello 4.2: tope de un request = 31d, cubre las
  columnas 30d y el mínimo D-8..D-1 de terms) y la bitemporalidad lo hace
  seguro.
- **La pregunta sin respuesta**: si la cuenta US gana o pierde depende del
  supuesto de halo (entre +1,671 y −2,238 USD en 91 días). Decisión pendiente
  antes de la fase margin-aware: acotar, holdout, o TACoS.

## Glosario rápido

- **Shadow**: el motor decide y registra, pero no toca Amazon.
- **Harvest**: promover un término de búsqueda ganador a keyword exact propia.
- **ASIN-like**: término que es un código de producto (B0 + 8 caracteres);
  jamás se negativiza.
- **Bitemporal**: cada métrica guarda el día del hecho Y el momento en que se
  observó; permite reconstruir "qué sabía el motor cuándo".
- **Watermark**: última fecha con datos por plataforma; si está vieja (>7d),
  el ciclo se salta esa plataforma.
- **Golden replay**: test que re-alimenta los inputs congelados de una
  decisión al motor y debe reproducirla idéntica — la garantía de que toda
  decisión es auditable.
- **Candados anti-monolito**: tests y linters que hacen imposible que el motor
  haga IO, que un módulo engorde sin decisión visible, o que la complejidad
  derive en silencio.

## Dónde vive todo

- **Código**: GitHub `gon0801/goncloud-Orbit` (master = lo aprobado; un PR
  abierto por phase en curso).
- **Base de datos viva**: Postgres en el server `goncloud` (Docker, solo
  localhost; acceso por túnel SSH).
- **Tracker**: AppFlowy (notion.goncloud.cc), grid EHV Tasks, fila
  `ORBIT 03 — PR1 optimizador: SHADOW completo (cero escrituras a Amazon)`.
- **Fuentes de verdad**: `docs/CONTEXTO.md` (reglas), `docs/traspaso/
  ADS_OPTIMIZER_V2_DESIGN.md` (umbrales), `docs/DATABASE.md` (schema),
  `plans/orbit-03.md` (plan y estado por tarea).

## Instrucciones para el asistente de chat

- Responde SIEMPRE en español (mexicano).
- Los umbrales y reglas de arriba están SELLADOS: cítalos tal cual, jamás
  inventes números ni "mejoras" de reglas — cambios de reglas son decisión
  del dueño y se hacen vía plan en el repo, no en el chat.
- Si el estado parece desfasado respecto a lo que el dueño cuenta, di que tu
  copia puede estar vieja y sugiérele hacer "Sync now" en el Project.
- Este Project es para PENSAR con el dueño (estrategia, dudas del negocio,
  entender decisiones del motor); la implementación vive en Claude Code.
