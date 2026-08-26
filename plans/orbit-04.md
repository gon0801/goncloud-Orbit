# ORBIT 04 — PR2 optimizador: APPLY con topes, veto y harvest real

> **Propósito**: el motor escribe a Amazon POR PRIMERA VEZ. Traducción del PR2
> del diseño v2 al mundo Orbit. Validación (tope de rondas CERRADO): panel de
> 5 perspectivas (30 hallazgos) → ronda 1 codex+qwen (codex 9A+3M; qwen 2A+
> medias — sus puntos 1-5 verbatim perdidos por un tail corto del lead,
> reconstruidos del cierre; declarado) → ronda 2 codex+grok (codex 5A+4M;
> grok 11A+11M). TODO incorporado en este v3; lo no adoptado está en
> Residuales con razón. ORBIT 04 entrega TODO con la escalera en `shadow` —
> cero mutaciones fuera de los probes autorizados (2.5/4.3); ORBIT 05 es el
> cutover, con criterio sellado: 2 semanas de shadow (~2026-09-07) +
> recálculo manual + **veto ejecutado por el dueño sobre una fila real**.
> Ronda 3 (suspensión explícita del tope por el dueño, razón: 16 altas en r2
> = sin convergencia + diseño nuevo sin revisar): kimi LGTM (cero hallazgos,
> verificó cada referencia contra el repo); qwen 2A+3M+3B "de formulación
> sellada, no de arquitectura" — cerrados en este v4.

## Decisiones SELLADAS (header manda sobre las tareas)

1. **HÍBRIDO con ventana de veto** (dueño 2026-08-24; cambio consciente del
   sello del diseño v2, razón: caso arras). BIDS aplican automático en su
   ciclo; CORTES (pause/negative/harvest) van a la cola con ventana.
   Perímetro: SOLO cortes en cola; default al vencer = APLICAR; nada nuevo se
   cuelga de la cola.
2. **Ventana 48h**, el reloj NO se detiene por infra caída (mitigación:
   notificación al ENCOLAR + un fallo de Telegram deja NOTA en `notes` del
   ciclo, visible en Salud — r2: el silencio del canal jamás es invisible).
3. **Veto DURABLE 30d** (editable al vetar) por CLAVE DE EFECTO (ver 4);
   vive en la fila `vetoed` (`vence_el` consultable); al vencer el motor
   re-propone con datos frescos.
4. **`apply_queue` (0002) — máquina de estados y CLAVE DE CONFLICTO POR
   EFECTO** (r2: la clave con `kind` dejaba eludir un veto de negative
   proponiendo harvest del MISMO término — el schema de `decision` ya los
   trata como excluyentes por término): clave de conflicto
   `(platform, ad_entity_id, familia, search_term)` con familia
   `entity_cut` (pause) / `term_cut` (negative Y harvest); `kind` queda como
   dato auditable. Estados: `pending_veto → vetoed | released`;
   `released → vetoed | applying` (r2 grok: mientras espera quota SIGUE
   siendo vetable — el 6º negative del lote arras esperando días era
   invetable; SOLO `applying` es punto de no retorno);
   `applying → applied | failed`; `pending_veto/released → discarded`
   (descartes: flip de cutover, re-validación fallida — ver 6). Terminales:
   `vetoed`, `applied`, `failed`, `discarded`. Nace `pending_veto` por
   trigger de INSERT (r2: el INSERT directo en `released` saltaría la
   ventana); transiciones selladas por trigger de UPDATE; único parcial
   en-vuelo sobre NO terminales, `NULLS NOT DISTINCT`; transiciones
   atómicas (`UPDATE … WHERE estado=…`). **La APP encola** (no un trigger en
   `decision`: el encolado necesita el modo efectivo por decisión) con
   invariante testeado: toda decisión de corte del ciclo tiene su fila en
   cola o su skip — jamás un corte huérfano en `decision` (r2 grok 8).
   **`vetoed` sellado en SCHEMA**: la transición a `vetoed` exige
   `current_user` admin por trigger (r2 grok 7: sin eso el UPDATE que el
   claim del motor necesita le permitiría vetar en SQL; el mismo hueco que
   la ronda 1 cerró para quota).
5. **Skip `veto_pendiente` por CLAVE DE EFECTO** (no por entidad ni por
   kind): en-vuelo o bloqueo vigente → el ciclo no re-decide esa clave;
   claves distintas avanzan. Frescura: ver re-validación en 6.
6. **La cola en SHADOW y el flip** (r2 codex+grok: el flush de cortes
   rancios al cutover era el hueco más caro): en shadow los cortes SÍ se
   encolan MARCADOS `modo='shadow'` — así el dueño practica el veto con
   candidatos reales — pero una fila shadow JAMÁS transiciona a `released`
   (solo vetable o descartable). En el flip de ORBIT 05, TODA fila shadow
   pendiente pasa a `discarded` en bloque: el live arranca SOLO con
   decisiones frescas post-flip y su ventana completa. Además,
   **re-validación al liberar** (r2 grok 20): el corte se re-evalúa contra
   la ventana FRESCA de datos — si ya no califica (el término vendió
   durante las 48h), pasa a `discarded` con nota; jamás se corta por
   silencio contra la regla. **Orden interno SELLADO del apply de un
   corte** (r3 qwen: la máquina no tiene `applying → discarded` y la quota
   no debe quemarse en descartes): re-validación SOBRE la fila `released`
   (PRE-claim) → claim atómico `released → applying` → cobro de quota →
   fila del ledger → HTTP. Un descarte ocurre siempre ANTES del claim y
   jamás después del cobro.
7. **Rampa por config**: día 1 = 10 bids / 2 pauses / 5 negatives /
   2 harvests POR DÍA Y PLATAFORMA; unidad = OPERACIÓN LÓGICA (harvest = 1
   aunque sean 2 HTTPs — con test que lo demuestre, r2); REVERSAS EXENTAS
   (con test); claves `ads_apply_cap_<platform>_<kind>` en `config_version`
   con **mapeo sellado y testeado** a la clave de quota
   `ads_optimizer:<platform>:<kind>` (r2 grok 13: dos vocabularios sin
   mapeo). Fail-closed: sin clave → NO nace fila del día → cero applies —
   y el estado "fail-closed activo" es VISIBLE en Salud (no se disfraza de
   rampa sana). Duplicación MANUAL cada 48h sanas (semántica de resolución:
   ventana móvil, incidentes nuevos sin resolver bloquean, históricos
   resueltos no). Éxito = "avanza sin incidentes".
8. **Quota sellada en schema (0002)**: fila del día solo desde config
   vigente (trigger), `used` creciente, vocabulario cerrado,
   **`quota_date` validado contra el día UTC de la base** (r2 codex: DATE
   sin zona + sesiones con TZ distinta duplicaban el cap). Se consume al
   APLICAR, ANTES del HTTP, UNA vez por operación lógica; **un 429
   (rechazado sin procesar) se reintenta SIN re-cobrar quota — es el mismo
   intento del ledger; 5xx/fallo ambiguo NO se reintenta** (r2 grok 19: el
   espejo real del read client, no un "jamás retry" que quemaría el cap con
   throttling); huérfano `applying` conserva su cobro (la reconciliación
   decide). Cap agotado → cortes esperan FIFO (y SIGUEN vetables); bids no
   aplicados = DESCARTADOS (jamás reintentados). **Selección de bids bajo
   cap SELLADA** (r3 qwen): prioridad por urgencia de hemorragia —
   banda_menos_25 > banda_menos_12 > banda_mas_15, y dentro de cada banda
   por costo de la ventana descendente; los descartados se cuentan en el
   digest ("N bids fuera de cap hoy") — la ausencia de fila no se confunde
   con un bug porque el conteo lo declara.
9. **Cliente de ESCRITURA (`app/ads/write.py`)**: allowlist default-deny —
   update bid y pause/resume de **keyword Y `product_target`** (r2 grok 4:
   549 targets US + 861 MX reciben decisiones del motor y la v2 los olvidó;
   path `/sp/targets` PUT), create/delete negative exact, create/delete
   keyword — nada más; payloads de UN objeto; **headers vendor
   Content-Type/Accept por path de mutación + `Amazon-Advertising-API-Scope`
   del profile de LA plataforma de la decisión** (r2 grok 18: un apply MX
   con scope US escribe en la cuenta equivocada); constructor exige
   `modo_confirmado` re-resuelto POR DECISIÓN (escalera + `goal.mode` +
   `enabled` + existencia) y **solo `app/apply.py` y la herramienta de
   smoke autorizada pueden construirlo** (candado de imports con la
   excepción EXPLÍCITA en test_architecture — r2 codex 5); **el read client
   SIGUE rechazando PUT/PATCH/DELETE, con test** (r2: el atajo natural era
   relajar el guard viejo). **Presentación sellada** (r2 grok 17): el bid
   viaja quantizado a 2 decimales y la moneda del payload se verifica
   contra `goal.bid_currency` antes del HTTP (regla 4).
10. **LEDGER de intentos (`apply_attempt`, 0002, append-only)** (r2 codex 3:
    la intención durable no tenía dónde vivir para bids/reintentos/
    reversas): TODA mutación — bid, corte, reversa, probe — nace como fila
    del ledger ANTES del HTTP: decision_id (nullable para probes), seq,
    tipo (normal/reversa/probe), request_payload EXACTO (para harvest, el
    bid efectivo a escribir), quota_cobrada, started_at; el ack/resultado
    se sella al volver — **con el patrón del repo para esta tercera vía**
    (r3 qwen: el "append-only" del trigger `prohibir_mutacion` bloquearía
    el sello): trigger acotado por columnas estilo
    `sku_cost_solo_cierra_vigencia` — SOLO ack/resultado/finished_at pasan
    de NULL a valor, UNA vez — y la tabla se declara excepción deliberada
    en los invariantes de test_schema (mismo trato que
    `decision_application` en 0001). "No existe 4º intento" = COUNT
    verificable.
    `decision_application` queda como RESUMEN por decisión (su PK única se
    respeta: reintentos = UPDATE del resumen con acks acumulados + fila
    NUEVA del ledger — r2 grok 12), con `applied_cycle_id` sellado AL
    CONFIRMAR (jamás pre-HTTP: un crash no cuenta como applied).
11. **Integración en el ciclo = tarea propia** (r2 grok 2: nadie recableaba
    `corre_ciclo` y el lock se soltaba antes de cualquier HTTP): fase de
    apply DENTRO del lock, con heartbeat DURANTE mutaciones y readback;
    guard `status='running'` en el cierre del envelope (la mejora que
    cycle.py 166-171 ya anuncia como "de PR2") y **validación de ownership
    del lock ANTES de cada HTTP** — lease perdido = abortar el apply
    fail-closed (r2 codex 1: el zombie post-TTL con apply son dos procesos
    escribiendo a Amazon); test con zombie y sucesor concurrentes.
12. **Regla 7 — reversas**: bid → `old_value`; pause → resume; negative →
    delete; harvest parcial → delete del negativo; harvest completo →
    keyword PRIMERO, negativo después. Cada una en el ledger como tipo
    reversa, exenta de quota, testeada antes en el mismo PR. **Una reversa
    NO limpia el cooldown** (r3 qwen, declarado deliberado): la entidad
    origen queda fría 7d igual — anti-loop: revertir y re-decidir lo mismo
    al día siguiente sería el ciclo tonto que el cooldown existe para
    impedir.
13. **Harvest**: fases + `harvest_job` nace AL LIBERAR el corte de la cola
    (primer paso del apply), jamás al decidir (r2 grok 8: nacer al decidir
    dejaba un `pending` eterno si el harvest se vetaba; la COLA manda,
    `harvest_job` es la ejecución). Reconciliación al INICIO del ciclo
    contra Amazon VIVO con **identidad completa: plataforma/profile +
    adGroupId destino + keyword_text + match_type** (r2 codex 7: el texto
    solo producía falsos "ya aplicada" — fixture señuelo en otro ad group);
    cubre también negatives normales y `applying` huérfanos del ledger.
    La 0002 sella transiciones de `harvest_job` por trigger de UPDATE.
    Fallo definitivo en `negative_created` → reversa automática + alerta.
14. **Bid del harvest**: `new_value` congela el default; el sugerido de
    Amazon se consulta al aplicar (regla 8 en vivo define endpoint, cliente
    y guard), se clampea, se PERSISTE como intención en el ledger pre-POST
    y queda en el ack; sin sugerencia → default (regla 3).
15. **Goals de harvest** (dueño): MX = `AC - Category Exact - MX` ad group
    553629449717842, fallback 10.00 MXN; US = **DOS goals scope=campaign**
    (r2 grok 15: un goal de plataforma solo admite UN destino): AU2 →
    `AU2 - Category Exact - US` y USPerNog → `USPerNog - Category Exact -
    US`, fallback 1.00 USD cada uno; los ids del seed usan `ad_entity.id`
    verificados POST-sync (los citados en sesión son PK internas de
    referencia); el DoD del seed exige destino con `status='ENABLED'` (r2:
    harvest a campaña pausada gasta quota sin servir). A1U fuera. Alta de
    familias futuras = un goal por config.
16. **Post-apply, el cache se actualiza CON el readback** (r2 grok 3: sin
    esto el ciclo siguiente calcula +15% sobre el bid viejo — regla 2, la
    fuente es Amazon y el readback ES de Amazon): la 0002 otorga a
    `app_decide` UPDATE acotado de `ad_entity_state`
    (current_bid/status/synced_at) que el apply ejecuta con lo LEÍDO tras
    confirmar; el re-check "ya estaba" usa GET fresco, jamás el cache.
17. **Gracia de reactivación 7d DESDE el ENABLED detectado — con casa en el
    schema** (r3 qwen: `synced_at` se pisa en cada sync y ninguna tarea
    tocaba al escritor): tabla chica `reactivacion_manual` en la 0002
    (`ad_entity_id` PK, `detectada_en`), escrita por el APLICADOR — no por
    el sync: el apply ya hace GET fresco en su re-check y ahí detecta
    "pause verificado propio + estado vivo ENABLED" → marca el instante si
    no existe (grant INSERT/SELECT acotado a `app_decide`); gracia = 7d
    desde `detectada_en`. `structure.py` no se toca. Solo el caso
    detectable; residual declarado.
18. **AUTH de escritura**: token estático (secrets 0600 ro, register_secret,
    compare_digest, solo header) en TODO endpoint de escritura; los
    endpoints de escritura usan **`ORBIT_DSN_ADMIN` vía dependencia nueva
    `ConexionEscritura`** (r2 grok 9: la API solo abre el DSN read y
    DEPLOY.md decía "la app no lo usa" — 3.1 lo cablea y 4.1 lo divide por
    servicio) y **los candados OpenAPI solo-GET se actualizan a la lista
    sellada GET + escrituras autenticadas** (sin eso, CI rojo o el veto
    escondido). `/run` = Reject formal; docstring de api.py corregido.
19. **Telegram (`app/notifica.py`, nuevo, fail-silent con warning + NOTA en
    notes del ciclo)**: aviso al encolar (con vencimiento), digest mínimo
    por ciclo ejecutor, alerta de harvest failed.
20. **UI mínima de veto** en el dashboard (cortes pendientes + vencimiento +
    botón vetar con bloqueo editable), auth de 18.
21. **`applied_count` + cooldown por ciclo EJECUTOR** (r2 codex 6): el
    cooldown de `goals.py` pasa a mirar `applied_cycle_id` (el modo live
    del ciclo que EJECUTÓ, no del que decidió) — con test de punta a punta
    del caso decisión-shadow-aplicada-en-live; `applied_count` cuadra por
    ciclo ejecutor (invariante).
22. **`HAY_MODULO_APPLY=True` se enciende en la tarea de integración (2.4)**
    con el candado regla 9: escalera `shadow` → cero HTTP (mock registra);
    el DoD del cierre verifica escalera en shadow.
23. **PROBE de formas reales AUTORIZADO en Phase 2 (tarea 2.5)** (r2 codex 9
    + grok 10: fijar los tests de readback contra un shape adivinado viola
    la regla 8): con autorización del dueño y campaña sacrificable elegida
    por él, LAS CUATRO formas (r3 qwen: faltaba el POST/DELETE de keyword —
    el corazón del harvest): (1) bid keyword ±0.01 con reversa; (2) bid
    `product_target` ±0.01 con reversa; (3) negative create+delete NETO
    CERO sobre término basura; (4) **keyword create+delete NETO CERO**
    sobre término basura. Fija los shapes de TODOS los acks ANTES de
    finalizar los tests de readback (los de 2.1-2.2 nacen marcados
    "pendientes de shape" hasta 2.5, y 2.3 DEPENDE de 2.5). Mecanismo:
    `tools/smoke_apply.py` con autorización efímera (env one-shot) +
    campaña allowlisted en config + excepción explícita en
    test_architecture; **corre con `ORBIT_DSN_DECIDE`** (r3 qwen: sus filas
    de ledger nacen con la identidad del motor, tipo `probe` auditable).
    El ensayo E2E final (4.3) re-usa la misma herramienta.
24. **Deploy endurecido**: env por servicio (el DSN admin solo donde toca) +
    non-root CON el esquema de permisos de secrets resuelto; **la 0002 se
    APLICA en goncloud como tarea con DoD y entra al runbook de DEPLOY.md**
    (r2 grok 21e: no había tarea de aplicarla); **0002 incluye GRANTs
    positivos completos** — USAGE de las secuencias IDENTITY nuevas,
    `applied_cycle_id` agregado al GRANT de columnas de
    `decision_application` — y **`tests/test_schema.py` amplía su parser a
    0002** para que los invariantes (FK con índice, append-only) cubran las
    tablas nuevas (r2 grok 11).
25. **Spec deltas**: 1.1 actualiza CONTEXTO.md ("Módulo apply") **y
    DATABASE.md** (r2 grok 14: su tabla-por-tabla quedaría mintiendo sobre
    quota/reserva PAUSE el día del merge).
26. **Superficie amigable de goals**: CLI + endpoints write (target/enabled/
    floor/ceiling **y los campos harvest_***, r2 grok 21f — dashboard-01
    Phase 3 los pide) con auth; goal=UPDATE con `updated_at` explícito;
    una sola implementación CLI/endpoint.

## Reject (con razón)

- **Canary pct**: rampa + veto + caps cubren su función (el gate que nunca
  disparó en 220,494 decisiones).
- **Cron propio del apply** / **`/run` HTTP** / **`cross_motor_cut_guard`** /
  **reserva PAUSE** / **auth compleja** / **apply MeLi** / **rampa
  automática**: razones de v1/v2 vigentes.
- **Fencing tokens completos estilo distributed-lock**: el par heartbeat-
  durante-apply + ownership-check pre-HTTP + guard running en el cierre
  (decisión 11) cubre el zombie con la maquinaria del lock EXISTENTE; un
  esquema de fencing tokens nuevo sería una segunda implementación de lo
  mismo (residual: ventana teórica entre ownership-check y HTTP, aceptada —
  es la misma clase de ventana que cualquier fencing sin soporte del lado
  de Amazon).
- **Detección total de reactivación manual**: `ad_entity_state` no tiene
  historia; solo el caso detectable (17).

## Residuales declarados (tope de rondas alcanzado)

1. Credenciales LWA únicas con scope de escritura: separación read/write por
   código, no por credencial (Amazon no ofrece tokens read-only).
2. Ventana teórica ownership-check→HTTP del zombie (ver Reject fencing).
3. Reactivación manual invisible para entidades nunca tocadas por el motor.
4. Puntos 1-5 verbatim de qwen ronda 1 perdidos (convergentes con codex por
   su cierre; capturas posteriores completas).

unknowns declarados: endpoint/shape del bid sugerido; shapes de acks (los
fija 2.5); ad groups e ids `ad_entity` reales de las Exact US
(post-reactivación); payload de negativeKeywords/list; `NULLS NOT DISTINCT`
verificado contra el PG16 del server.

## Phase 1 — Migración 0002, cola, quota y cliente de escritura [lane:gate]

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 1.1 | Brief `docs/APPLY.md`: contrato fino de TODO el header (checklist 1:1) — máquinas de estado (cola con familia de efecto y shadow-mark, harvest_job, ledger), matriz de reconciliación con identidad completa, tabla de reversas, claves config↔quota con mapeo, semántica 48h sanas, secuencia ledger→HTTP→readback→sello, procedimiento de familia harvest nueva y rotación de token, checklist de cutover ORBIT 05 (incluye discard masivo de filas shadow). Spec delta a CONTEXTO.md **y DATABASE.md**. Marcar `ORBIT 04` In progress. `[tdd:skip:docs-brief]` | Checklist 1:1 verificable punto por punto; ambos spec deltas; CI verde | - | cc:完了 [4ae5f5c] |
| 1.2 | Migración `0002_apply.sql`: `apply_queue` (familia de efecto, estados con `discarded`, shadow-mark, triggers INSERT nace-pending y UPDATE de transiciones, vetoed exige admin por trigger, único parcial en-vuelo NULLS NOT DISTINCT, `request_payload`, `vence_el`, `vetoed_at/by`) + `apply_attempt` (ledger con trigger acotado por columnas: solo ack/resultado/finished_at NULL→valor una vez, y excepción declarada en los invariantes de test_schema) + `reactivacion_manual` (ad_entity_id PK, detectada_en; INSERT/SELECT solo app_decide) + sellos de `apply_quota_state` (fila desde config con mapeo de claves, used creciente, quota_date = día UTC de la base) + triggers de `harvest_job` + `decision_application.applied_cycle_id` (incluido en el GRANT de columnas) + UPDATE acotado de `ad_entity_state` para app_decide + GRANTs positivos completos (secuencias IDENTITY) + `tests/test_schema.py` parsea también 0002. `[tdd:required]` | Tests: transición ilegal revienta en las 3 máquinas; INSERT directo en released revienta; app_decide no veta (trigger current_user), no inventa cap, no decrementa used; dos cortes en vuelo de la misma CLAVE DE EFECTO chocan (incluye pause NULL y negative-vs-harvest del mismo término); GRANTs positivos probados con el ROL real (no superuser); invariantes de test_schema cubren 0002 | 1.1 | cc:完了 [f1b3867] |
| 1.3 | Cliente de escritura `app/ads/write.py`: allowlist con keyword Y product_target (+reversas), payload de UN objeto, headers vendor + scope del profile por plataforma, presentación (quantize 2 decimales + moneda vs goal.bid_currency), 429-retry-sin-recobro / ambiguo-no-retry, constructor con modo confirmado, construcción solo desde apply/smoke (test_architecture con excepción explícita), read client sigue rechazando PUT/PATCH/DELETE (test). Ampliar LIST_REQUEST_TYPES con negativeKeywords (regla 8 en vivo). `[tdd:required]` | Tests: cada path fuera de allowlist rechazado; multi-objeto rechazado; scope equivocado imposible (plataforma→profile sellado, demostrado fallando); moneda equivocada revienta; retry solo en 429 y sin recobro; constructor fuera de apply/smoke revienta; superficie read intacta | 1.1 | cc:完了 [e4cb36f] |

## Phase 2 — El apply integrado [lane:gate]

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 2.1 | Núcleo (`app/apply.py`): re-resolución por decisión (escalera + goal.mode + enabled + existencia — incluido el caso envelope-live/goal-shadow del residual de cycle.py), ledger pre-HTTP para TODA mutación, secuencia sellada, retry con tope 3 (COUNT del ledger), bids descartados si no caben, applied_cycle_id al confirmar, actualización del cache con el readback, reversa de bid. `[tdd:required]` | Tests: escalera shadow → cero HTTP (regla 9); envelope live + goal shadow NO aplica (regla 9); crash entre ledger y HTTP deja rastro y la reconciliación resuelve; no existe 4º intento (COUNT); bid descartado no reaparece; cache actualizado post-readback (demostrado fallando: sin ello el ciclo siguiente decide sobre bid viejo); terna parcial revienta; applied_count por ciclo ejecutor | 1.2, 1.3 | cc:完了 [3abf4eb] |
| 2.2 | Cola de cortes: encolar por la APP con invariante corte↔cola, shadow-mark (shadow jamás libera), skip por clave de efecto, liberar vencidos (FIFO, caps lógicos, reversas exentas — ambos con test), released sigue vetable, claim atómico released→applying, orden sellado re-validación(PRE-claim)→quota→claim→ledger→HTTP (errata post-implementación: quota ANTES del claim — sin ella la fila que espera cap quedaría atrapada en applying, invetable; declarado en libera_vencidos) (descarte jamás post-cobro; la re-validación RE-RESUELVE `umbral_corte` de CORTES 01 con evidencia FRESCA anclada al reloj de LIBERACIÓN (el `decided_at` de `ventanas_evidencia_ad_group` es el instante de liberar, no el de decidir — ronda 2 qwen de cortes-01) — jamás reusa `inputs.corte.umbral_clicks_usado` congelado ni se limita a orders>0: contrato cross-plan sellado en el spec de cortes-01), re-check de estado vivo por GET fresco (que además detecta y marca `reactivacion_manual`), gracia 7d desde `detectada_en`, veto durable por clave de efecto. `[tdd:required]` | Tests: carreras (veto en released gana o pierde limpio contra claim; veto en applying → "en vuelo"); invariante corte↔cola demostrado fallando; fila shadow jamás libera; re-validación descarta al que vendió en la ventana Y al que ya no alcanza el umbral adaptativo fresco (regla 9, casos separados negative y pause donde SOLO cambia la evidencia); harvest=1 quota/2 HTTPs y reversa exenta (regla 9); gracia desde el ENABLED; reversas pause/negative | 2.1 | cc:完了 [f23e44d] |
| 2.3 | Harvest: harvest_job nace al LIBERAR, fases + reconciliación viva con identidad completa (fixture señuelo en otro ad group), bid sugerido (regla 8 define endpoint/cliente/guard) clampeado + intención en ledger, fallo definitivo → reversa + alerta, orden de reversas. `[tdd:required]` | Tests: matriz completa del brief celda por celda; señuelo no da falso "ya aplicada" (regla 9); harvest vetado JAMÁS crea harvest_job; intención sobrevive crash; orden de reversa (regla 9); failed → alerta | 2.2, 2.5 | cc:完了 [14fe6bf] |
| 2.4 | Integración en `corre_ciclo`: fase de apply DENTRO del lock, heartbeat durante mutaciones/readback, guard `status='running'` en el cierre, ownership-check pre-HTTP con aborto fail-closed, `HAY_MODULO_APPLY=True`. `[tdd:required]` | Tests: zombie y sucesor concurrentes → solo uno muta (regla 9, la mejora anunciada en cycle.py 166-171); lease perdido aborta sin HTTP; lock se libera DESPUÉS del apply; escalera shadow → cero HTTP re-verificado tras el flip del flag | 2.1 | cc:完了 [690a237] |
| 2.5 | PROBE autorizado de formas reales (dueño: autorización + campaña sacrificable): `tools/smoke_apply.py` (autorización efímera + campaña allowlisted + excepción sellada en test_architecture, corre con ORBIT_DSN_DECIDE y sus filas de ledger son tipo probe) — LAS CUATRO formas: bid keyword ±0.01 y reversa, bid product_target ±0.01 y reversa, negative create+delete neto cero, y keyword create+delete neto cero (el POST/DELETE del corazón del harvest); fija los shapes de TODOS los acks y los tests de readback se FINALIZAN contra ellos (regla 8; los de 2.1-2.2 nacen marcados pendientes-de-shape hasta esta tarea). `[tdd:skip:probe-produccion]` | Evidencia completa en ORBIT 04 (request/ack/readback/reversa × 4 formas); estado final == inicial; tests de readback de 2.1-2.3 sellados contra shapes reales | 2.1 | cc:完了 [5a36793 — herramienta construida; corrida real PENDIENTE de autorización del dueño] |

## Phase 3 — Superficies: veto, goals y Telegram [lane:gate]

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 3.1 | Auth de escritura + `ConexionEscritura` (ORBIT_DSN_ADMIN) + endpoint de veto (admin, rastro, vencimiento editable) + pantalla mínima de veto + actualización de los candados OpenAPI a la lista sellada GET+escrituras + corrección del docstring de api.py (/run Reject) + rotación de token en DEPLOY.md. `[tdd:required]` | Tests: sin token → 401; query string no autentica; veto con actor y vence_el; XSS (regla 9); candados OpenAPI nuevos demostrados fallando; app sin DSN admin → escrituras 503 fail-closed | 1.2 | cc:TODO |
| 3.2 | Goals amigables: `app.cli goals set` + endpoints write (target/enabled/floor/ceiling/harvest_*) con auth — UPDATE con updated_at explícito, config=fila nueva, una implementación CLI/endpoint. Desbloquea dashboard-01 Phase 3. `[tdd:required]` | Tests: edición visible al ciclo siguiente con rastro; updated_at (regla 9); harvest_* respeta all-or-nothing del CHECK; sin auth rechazado; camino único | 3.1 | cc:TODO |
| 3.3 | `app/notifica.py`: aviso al encolar + digest por ciclo ejecutor + alerta harvest failed; fallo del canal → warning + NOTA en notes del ciclo (visible en Salud). `[tdd:required]` | Tests (mock): mensajes correctos sin secretos; fallo de Telegram no tumba el ciclo Y deja nota (regla 9: sin la nota el silencio sería invisible) | 2.2 | cc:TODO |

## Phase 4 — Deploy, seeds y ensayo final [lane:release]

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 4.1 | Deploy endurecido: env por servicio (DSN admin solo en app), non-root con permisos de secrets resueltos, **aplicar 0002 en goncloud** (runbook DEPLOY.md actualizado, backup previo del schema). `[tdd:required]` | Candados de compose/Dockerfile demostrados fallando; 0002 aplicada y verificada en vivo (regla 8: SELECT de las tablas nuevas); servicio non-root leyendo secrets; accounting/bridge/crons intactos | Phase 3 | cc:TODO |
| 4.2 | Seeds (dueño reactiva Exacts US ANTES): goals harvest MX + 2 goals campaign US (ids ad_entity verificados post-sync, destinos ENABLED verificado), caps día 1, ventana 48h, veto 30d. `[tdd:skip:seed-config]` | SELECTs en vivo verifican todo; goal_harvest_completo satisfecho; destino PAUSED rechazado por el DoD; fail-closed sin clave probado | 4.1 | cc:TODO |
| 4.3 | Ensayo E2E pre-cutover con `tools/smoke_apply.py` (re-verificación de 2.5 contra el deploy real) + el dueño ejecuta UN VETO real sobre una fila shadow de la cola (prerequisito de ORBIT 05). `[tdd:skip:smoke-produccion]` | Evidencia en ORBIT 04; estado final == inicial; veto del dueño registrado con su actor | 4.2 | cc:TODO |
| 4.4 | Cierre: backup pre-cutover (ad_entity_state + keywords/negativeKeywords/targets lists), CHAT-CONTEXT al día, PR final, `ORBIT 04` Done. FLIP + rampa + discard masivo de filas shadow = ORBIT 05 (checklist en el brief). `[tdd:skip:cierre-docs]` | Backup verificado; CI verde; escalera en `shadow` verificada; evidencia completa en AppFlowy | 4.3 | cc:TODO |

## 事前確認

- 事項: external-send — `git push` + `gh pr create`/merge (un PR por phase)
  理由: patrón del repo, batería en CI
  scope: Phases 1-4
- 事項: external-send — lecturas nuevas a Amazon Ads (negativeKeywords/list, bid recommendations) para regla 8 y reconciliación
  理由: DoD de 1.3 y 2.3
  scope: Phase 1-2
- 事項: external-send — escritura append-only en la base Orbit (0002 vía admin en 4.1, seeds 4.2, cola/ledger en ciclos)
  理由: modo de operación; DDL solo la 0002 revisada
  scope: Phases 1, 4
- 事項: external-send/destructive — MUTACIONES REALES a Amazon SOLO en 2.5 y 4.3 (bids ±0.01 con reversa + negative neto-cero, campaña del dueño, autorización efímera explícita en el momento) y mensajes Telegram
  理由: regla 8 de los acks (2.5) y ensayo final (4.3); el resto corre en shadow = cero mutaciones
  scope: Phase 2 / 2.5, Phase 4 / 4.3, Phase 3 / 3.3
- 事項: destructive — deploy: rebuild non-root + env split + aplicar 0002; crons intactos
  理由: DoD de 4.1
  scope: Phase 4 / 4.1
