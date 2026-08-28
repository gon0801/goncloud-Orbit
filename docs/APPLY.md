# APPLY — contrato fino del módulo de escritura (ORBIT 04 / PR2)

> **Propósito:** el motor escribe a Amazon POR PRIMERA VEZ. Este documento es
> la traducción operativa del header de `plans/orbit-04.md` (las 26
> decisiones SELLADAS): cada candado con su cita ("sellado N" = decisión N
> del header), cómo se verifica y qué deja abierto. Las tareas 1.2
> (migración `0002_apply.sql`) y 1.3 (`app/ads/write.py`) lo implementan AL
> PIE DE LA LETRA; las fases 2-4 lo consultan. Si este brief y el header se
> contradicen, **el header manda**: se detiene y se reporta, no se
> "arregla" en código.
>
> Notación: **sellado N** = decisión sellada N del header. **unknown** =
> punto que el header declara abierto (§13). **pendiente** = detalle fino
> que el header deja al implementador de 1.2/1.3 DENTRO de lo sellado; este
> brief no lo resuelve.

## 0. Checklist 1:1 — decisión sellada → sección → verificación

| Sellado | Sección | Candado central | Se verifica en |
|---|---|---|---|
| 1 Híbrido con ventana de veto | §1 | Solo cortes en cola; default al vencer = APLICAR; nada nuevo se cuelga de la cola | tests 2.2 |
| 2 Ventana 48h, reloj no se detiene | §1.4 | `vence_el` + notificación al encolar | tests 2.2, 3.3 |
| 3 Veto durable 30d | §1.4 | `vetoed` con `vence_el` editable al vetar; al vencer se re-propone | tests 2.2, 3.1 |
| 4 `apply_queue`: estados y clave de efecto | §1 | Tabla de transiciones + clave `(platform, ad_entity_id, familia, search_term)` + triggers | DoD 1.2 completo |
| 5 Skip por clave de efecto | §2 | En-vuelo o bloqueo vigente → no se re-decide la clave | tests 2.2 |
| 6 Shadow + flip + re-validación + orden | §2, §3 | Shadow jamás libera; discard masivo en el flip; re-validación PRE-claim; orden claim→quota→ledger→HTTP | tests 2.2 |
| 7 Rampa por config | §5 | Día 1: 10 bids / 2 pauses / 5 negatives / 2 harvests por día y plataforma; unidad = operación lógica; reversas exentas | tests 2.2 (DoD) |
| 8 Quota sellada en schema | §5 | Fila del día solo desde config (trigger); `used` creciente; `quota_date` = día UTC; 429 sin recobro; 5xx no reintenta | DoD 1.2 |
| 9 Cliente de escritura | §8 | Allowlist default-deny; scope del profile de LA plataforma; quantize 2 dec + moneda vs `goal.bid_currency` | DoD 1.3 |
| 10 Ledger `apply_attempt` | §4 | Toda mutación nace pre-HTTP; sello una sola vez (trigger acotado); no existe 4º intento = COUNT | DoD 1.2, tests 2.1 |
| 11 Integración en el ciclo | §9 | Apply DENTRO del lock; heartbeat; guard running; ownership-check pre-HTTP | tests 2.4 |
| 12 Reversas (regla 7) | §7 | Tabla de reversas; en ledger como tipo reversa; exentas; NO limpian cooldown | tests 2.1/2.2/2.3 |
| 13 Harvest + reconciliación viva | §6 | `harvest_job` nace AL LIBERAR; identidad completa; fallo definitivo → reversa + alerta | tests 2.3 |
| 14 Bid del harvest | §6 | Sugerido consultado al aplicar, clampeado, persistido pre-POST; sin sugerencia → default | tests 2.3 |
| 15 Goals de harvest | §11a | Un goal por config; destinos ENABLED; ids POST-sync | seeds 4.2 |
| 16 Cache actualizado CON el readback | §9 | UPDATE acotado de `ad_entity_state` con lo LEÍDO; re-check por GET fresco | DoD 1.2, tests 2.1 |
| 17 Gracia de reactivación 7d | §3.3 | `reactivacion_manual` (`detectada_en`) escrita por el aplicador; gracia desde ahí | tests 2.2 |
| 18 AUTH de escritura | §10 | Token estático (secrets 0600, `register_secret`, `compare_digest`, solo header); `ConexionEscritura` (DSN admin) | tests 3.1 |
| 19 Telegram fail-silent | §10 | Aviso al encolar + digest por ciclo ejecutor + alerta harvest; fallo → NOTA en `notes` | tests 3.3 |
| 20 UI mínima de veto | §10 | Pendientes + vencimiento + vetar con bloqueo editable; auth de 18 | tests 3.1 |
| 21 `applied_count` + cooldown por EJECUTOR | §9 | Cooldown mira `applied_cycle_id` (ciclo que EJECUTÓ) | tests 2.1 |
| 22 `HAY_MODULO_APPLY` | §9 | Se enciende en 2.4 con candado regla 9 (shadow → cero HTTP) | tests 2.4 |
| 23 PROBE de formas reales | §8, §13 | Las 4 formas con reversa/neto-cero; fija shapes de acks; `tools/smoke_apply.py` | tarea 2.5 |
| 24 Deploy endurecido + 0002 | §12 | 0002 con GRANTs positivos completos; aplicada en goncloud (4.1); `test_schema` parsea 0002 | 4.1 |
| 25 Spec deltas | — | CONTEXTO.md + DATABASE.md actualizados por 1.1 | este PR |
| 26 Goals amigables | §10 | CLI + endpoints write (incl. `harvest_*`) con auth; una sola implementación | tests 3.2 |

---

## 1. Máquina de estados de `apply_queue` (0002)

### 1.1 Perímetro (sellado 1)

HÍBRIDO con ventana de veto: **los bids aplican automático en su ciclo** (no
tocan la cola); **los cortes (pause / negative / harvest) van a la cola** con
ventana. Tres candados de perímetro:

- SOLO cortes viven en la cola.
- **Default al vencer la ventana = APLICAR** (el silencio del dueño no
  bloquea; el veto explícito sí).
- **Nada nuevo se cuelga de la cola**: si una superficie futura quiere
  encolar algo, requiere decisión nueva del dueño — no extensión silenciosa.

### 1.2 Tabla de transiciones EXACTA (sellado 4)

| Estado | Transiciones permitidas |
|---|---|
| `pending_veto` | `vetoed` \| `released` \| `discarded` |
| `released` | `vetoed` \| `applying` \| `discarded` |
| `applying` | `applied` \| `failed` |
| `vetoed` | (terminal) |
| `applied` | (terminal) |
| `failed` | (terminal) |
| `discarded` | (terminal) |

Notas selladas sobre la tabla:

- **`released` SIGUE vetable** (r2 grok: mientras espera quota sigue siendo
  vetable — el 6º negative del lote arras esperando días era invetable).
  SOLO `applying` es punto de no retorno: el veto contra `applying` se
  rechaza ("en vuelo").
- NO existe `applying → discarded`: la máquina no descarta en vuelo (r3
  qwen; ver orden en §3).
- Descartes (`pending_veto/released → discarded`): flip de cutover (§2) y
  re-validación fallida (§3), con nota.
- El discard de una fila **shadow** exige admin POR SCHEMA (candado del
  trigger, como el veto): es la ceremonia del flip de ORBIT 05 (§12) — el
  motor solo descarta filas live (hallazgo post-merge PR #25).

### 1.3 Clave de efecto (sellado 4)

Clave de conflicto: **`(platform, ad_entity_id, familia, search_term)`** con

- `familia = 'entity_cut'` → pause (`search_term` NULL)
- `familia = 'term_cut'` → negative Y harvest (`search_term` NOT NULL)

`kind` (pause/negative/harvest) queda como **dato auditable**, NO en la
clave: con `kind` en la clave, un veto de negative se eludía proponiendo
harvest del MISMO término (r2). Con familia de efecto, negative y harvest
del mismo término **chocan** — consistente con `decision`, que ya los trata
como excluyentes por término (únicos parciales de 0001).

### 1.4 Ventana de 48h y veto durable 30d (sellados 1, 2, 3)

- **Ventana 48h** (`vence_el`): el reloj NO se detiene por infra caída. Si
  Telegram falla al notificar, el fallo deja **NOTA en `notes` del ciclo**
  (visible en Salud) — el silencio del canal jamás es invisible (r2).
  Notificación al ENCOLAR con vencimiento (sellado 19).
- **Veto DURABLE 30d, editable al vetar** (columna `vence_el` de la fila
  `vetoed`, consultable; `vetoed_at`/`vetoed_by` dejan el rastro). Al
  vencer el veto, el motor **re-propone con datos frescos** (nueva decisión,
  nueva fila en cola) — el vencimiento NO reanima la fila vieja.

### 1.5 Candados de schema (sellado 4)

- **Nace `pending_veto` por trigger de INSERT**: un INSERT directo en
  `released` revienta (saltaría la ventana).
- **Transiciones selladas por trigger de UPDATE**: toda transición fuera de
  la tabla §1.2 revienta. Las transiciones son atómicas en la app:
  `UPDATE … WHERE estado = <estado esperado>`; cero filas afectadas =
  perdió la carrera (releer, no aplicar).
- **Único parcial en-vuelo sobre NO terminales, `NULLS NOT DISTINCT`**: a lo
  sumo una fila no-terminal por clave de efecto. `NULLS NOT DISTINCT` es
  obligatorio porque `search_term` es NULL en los pause (sin él, dos pauses
  de la misma entidad no chocarían). Verificación contra el PG16 del
  server: unknown (§13).
- **`vetoed` sellado en SCHEMA**: la transición a `vetoed` exige
  `current_user` admin (trigger) — el rol del motor (`app_decide`) NO puede
  vetar, ni siquiera con el UPDATE que necesita para el claim (r2 grok 7:
  el mismo hueco que la ronda 1 cerró para quota). El endpoint de veto
  corre como admin (§10, sellado 18).

### 1.6 Encola la APP; invariante corte↔cola (sellado 4)

- **La APP encola** (INSERT en `apply_queue`), no un trigger sobre
  `decision`: el encolado necesita el **modo efectivo por decisión**
  (escalera + `goal.mode` + `enabled` + existencia) para decidir el
  shadow-mark (§2).
- **Invariante corte↔cola (testeado)**: toda decisión de corte del ciclo
  tiene su fila en la cola O su skip registrado — jamás un corte huérfano
  en `decision` (r2 grok 8). El skip es por clave de efecto con motivo
  `veto_pendiente` (§2).

**Verificación (DoD 1.2):** transición ilegal revienta en las 3 máquinas;
INSERT directo en `released` revienta; `app_decide` no veta (trigger
`current_user`); dos cortes en vuelo de la misma clave de efecto chocan
(incluye pause con `search_term` NULL y negative-vs-harvest del mismo
término); GRANTs probados con el rol real (no superuser).

---

## 2. Shadow y flip; skip por clave de efecto (sellados 5, 6)

### 2.1 Shadow

- En shadow los cortes SÍ se encolan, marcados **`modo='shadow'`** — el
  dueño practica el veto con candidatos reales.
- Una fila shadow **JAMÁS transiciona a `released`**: solo es vetable
  (práctica del veto, con admin) o descartable. Cero HTTP sale de una fila
  shadow (candado regla 9; escalera shadow → cero HTTP).
- **Flip de ORBIT 05**: TODA fila shadow pendiente pasa a `discarded` EN
  BLOQUE (una transacción; conteo antes/después en el checklist §12). El
  live arranca SOLO con decisiones frescas post-flip y su ventana completa
  — sin flush de cortes rancios (r2 codex+grok: era el hueco más caro).

### 2.2 Skip `veto_pendiente` por clave de efecto (sellado 5)

El ciclo no re-decide una clave de efecto cuando existe:

1. **en-vuelo**: fila NO terminal de esa clave en la cola, o
2. **bloqueo vigente**: fila `vetoed` de esa clave con `vence_el` vigente.

Claves distintas avanzan (el bloqueo es por clave de efecto, no por entidad
ni por kind). Un veto VENCIDO no bloquea: al vencer, el motor re-propone
(sellado 3). El skip queda registrado con su motivo — invariante §1.6.

---

## 3. Orden sellado del apply de un corte (sellados 6, 13, 17)

```
1. re-validación sobre la fila released (PRE-claim)
2. claim atómico          released -> applying   (UPDATE ... WHERE estado='released')
3. cobro de quota         (una operación lógica; §5)
4. fila del ledger        apply_attempt ANTES del HTTP (§4)
5. HTTP de mutación       (write client, §8; heartbeat y ownership-check, §9)
6. readback + sello       ack/resultado/finished_at una vez; resumen y
                          applied_cycle_id AL CONFIRMAR (§4); cache con lo
                          LEÍDO (§9)
```

**Un descarte ocurre SIEMPRE antes del claim y NUNCA después del cobro**
(r3 qwen: la quota no se quema en descartes; la máquina no tiene
`applying → discarded`).

### 3.1 Re-validación (PRE-claim, sobre la fila `released`)

Re-evalúa el corte contra la **ventana FRESCA** de datos — jamás reusa los
insumos congelados de la decisión:

- **Regla re-evaluada completa**: PAUSE `orders=0 ∧ clicks≥umbral_corte ∧
  cost≥{us: 12 USD, mx: 200 MXN}`; NEGATIVE `orders=0 ∧ clicks≥umbral_corte
  ∧ cost≥piso` (pisos adaptativos de CORTES 01). Si ya no califica — el
  término vendió durante las 48h, o el umbral re-resuelto ya no se alcanza
  — pasa a `discarded` CON NOTA. Jamás se corta por silencio contra la
  regla (sellado 6).
- **`umbral_corte` se RE-RESUELVE de CORTES 01** con evidencia FRESCA
  anclada al **reloj de LIBERACIÓN**: `ventanas_evidencia_ad_group(conn,
  platform, decided_at)` se llama con el instante de LIBERAR como
  `decided_at` (no el de decidir) — contrato cross-plan sellado en el spec
  de cortes-01. **Jamás** reusa `inputs.corte.umbral_clicks_usado`
  congelado ni se limita a `orders>0` (el umbral fresco también puede
  descartar).
- **Re-check de estado vivo por GET fresco** (jamás el cache, sellado 16):
  además alimenta la detección de reactivación manual (§3.3).

### 3.2 Claim atómico

`UPDATE apply_queue SET estado='applying' WHERE id=:id AND
estado='released'` — cero filas = perdió la carrera (p.ej. un veto que
llegó primero): releer y no aplicar. Carreras veto/claim testeadas: veto en
`released` gana o pierde limpio; veto en `applying` → "en vuelo".

### 3.3 Reactivación manual y gracia 7d (sellado 17)

El re-check por GET fresco detecta "pause verificado propio + estado vivo
ENABLED" → marca `reactivacion_manual (ad_entity_id PK, detectada_en)` si
no existe (INSERT idempotente por PK; **la escribe el APLICADOR, no el
sync**; grants INSERT/SELECT acotados a `app_decide`). **Gracia = 7d desde
`detectada_en`**: durante la gracia el motor no vuelve a cortar esa
entidad. `structure.py` no se toca; solo el caso detectable (residual #3
del header declarado).

---

## 4. Ledger `apply_attempt` (0002) y resumen `decision_application` (sellado 10)

### 4.1 Ledger append-only con sello

TODA mutación — bid, corte, reversa, probe — nace como fila del ledger
**ANTES del HTTP** con: `decision_id` (NULL permitido SOLO para probes),
`seq` (número de intento de la decisión), `tipo` (`normal` / `reversa` /
`probe`), **`request_payload`** con la INTENCIÓN lógica (para harvest: el
bid efectivo a escribir, sellado 14; el envelope de contenedor/enum/type-cast
del wire lo aplica `write.py` de forma determinista — los deletes SÍ se
congelan exactos porque su wire es el filtro; declaración post-probe 2.5,
hallazgo reviewer Obs 3), `quota_cobrada`, `started_at`.

El ack/resultado/`finished_at` se sellan **al volver, SOLO una vez**:

- **Trigger acotado por columnas, patrón `sku_cost_solo_cierra_vigencia`**
  (r3 qwen: el append-only estricto de `prohibir_mutacion` bloquearía el
  sello): SOLO ack/resultado/finished_at pasan de NULL a valor, UNA vez;
  cualquier otro UPDATE/DELETE revienta.
- La tabla se declara **excepción deliberada** en los invariantes de
  `tests/test_schema.py` — mismo trato que `decision_application` en 0001.

**"No existe 4º intento" = COUNT verificable**: tope de reintentos por
decisión = 3 (tarea 2.1); un 4º intento revienta contra el COUNT del
ledger.

**Residual declarado (ADV-08, review adversaria de phase 2):** el sello por
decisión (`ok:reconciliado` con `ack` NULL) cierra TODAS las filas sin
sello de la decisión cuando la evidencia viva resuelve el efecto — incluida
la de un intento que murió ANTES de enviar (crash entre ledger-commit y
HTTP): el ledger puede afirmar un intento que jamás salió. El tope-3 las
CUENTA igual (conservador: un intento fantasma consume un hueco del tope,
jamás lo regala). No hay forma barata de distinguir "nunca salió" de
"salió y no se selló" sin leer `started_at` contra el log de acceso — si
ese poder se necesita, es decisión nueva del dueño.

### 4.2 `decision_application` queda como RESUMEN

- Su PK única (`decision_id`) se respeta: **reintentos = UPDATE del
  resumen** (acks acumulados) **+ fila NUEVA del ledger** (r2 grok 12).
- **`applied_cycle_id` se sella AL CONFIRMAR** (jamás pre-HTTP: un crash no
  cuenta como applied; lo consume el cooldown por ciclo EJECUTOR, §9).
- El resumen sigue siendo la fuente del veredicto para cooldown
  (`verify_ok IS TRUE`), igual que en 0001.

**Verificación (DoD 1.2):** el trigger del ledger deja pasar SOLO el sello
NULL→valor una vez; `test_schema` declara la excepción; COUNT del tope
testeado en 2.1.

---

## 5. Quota y rampa (sellados 7, 8)

### 5.1 La fila del día nace SOLO desde config (fail-closed)

- **Trigger en `apply_quota_state`**: la fila del día `(motor, quota_date)`
  solo nace con `cap` copiado de la clave mapeada en `config_version`
  vigente. **Sin clave → NO nace fila → cero applies** (fail-closed), y el
  estado "fail-closed activo" es **VISIBLE en Salud** (no se disfraza de
  rampa sana).
- **`used` creciente** (trigger: un UPDATE que decremente `used` revienta —
  en 0001 esto no era enforceable; 0002 lo sella).
- **`quota_date` = día UTC de la base** (`(now() AT TIME ZONE 'UTC')::date`
  en la expresión, no `CURRENT_DATE`: r2 codex — DATE sin zona + sesiones
  con TZ distinta duplicaban el cap).
- **Vocabulario cerrado** en `motor`: `ads_optimizer:<platform>:<kind>` con
  `kind ∈ {bid, pause, negative, harvest}` (los cuatro de la rampa). Un
  `motor` fuera de vocabulario revienta. Kinds nuevos de quota = decisión
  nueva del dueño, no extensión del motor.

### 5.2 Mapeo sellado config ↔ quota (testeado)

| `kind` | Clave en `config_version` | Fila en `apply_quota_state` (`motor`) |
|---|---|---|
| bid | `ads_apply_cap_<platform>_bid` | `ads_optimizer:<platform>:bid` |
| pause | `ads_apply_cap_<platform>_pause` | `ads_optimizer:<platform>:pause` |
| negative | `ads_apply_cap_<platform>_negative` | `ads_optimizer:<platform>:negative` |
| harvest | `ads_apply_cap_<platform>_harvest` | `ads_optimizer:<platform>:harvest` |

`<platform>` ∈ {`amazon_us`, `amazon_mx`}. Dos vocabularios sin mapeo era el
hueco (r2 grok 13); el mapeo es EXPLÍCITO y testeado.

### 5.3 Consumo y reintentos

- Se consume **AL APLICAR, ANTES del HTTP, UNA vez por operación lógica**.
- **Unidad = operación lógica**: harvest = 1 aunque sean 2 HTTPs (con test
  que lo demuestre).
- **REVERSAS EXENTAS** de quota (con test).
- **429 (rechazado sin procesar) → se reintenta SIN re-cobrar**: es el mismo
  intento del ledger. **5xx / fallo ambiguo → NO se reintenta** (r2 grok
  19: el espejo real del read client, no un "jamás retry" que quemaría el
  cap con throttling).
- **Huérfano `applying` conserva su cobro**; la reconciliación decide (§6).
- Si un probe consume quota o va exento: **pendiente** de 2.5 (el header no
  lo sella; lo auditable es `quota_cobrada` del ledger).

### 5.4 Cap agotado

- **Cortes esperan FIFO** (la fila `released` sigue vetable mientras
  espera).
- **Bids fuera de cap se DESCARTAN** (jamás reintentados): la selección
  bajo cap es sellada — prioridad por urgencia de hemorragia:
  **banda_menos_25 > banda_menos_12 > banda_mas_15**, y dentro de cada
  banda por **costo de la ventana descendente**. Los descartados se
  cuentan en el digest ("N bids fuera de cap hoy") — la ausencia de fila
  no se confunde con un bug porque el conteo lo declara.

### 5.5 Rampa día 1 y duplicación manual

- **Día 1 (por día y plataforma): 10 bids / 2 pauses / 5 negatives /
  2 harvests** (seeds 4.2).
- **Duplicación MANUAL cada 48h sanas**. "48h sanas" = **ventana móvil de
  48h sin incidentes SIN resolver**: un incidente nuevo sin resolver dentro
  de la ventana BLOQUEA la duplicación; un incidente histórico ya resuelto
  NO bloquea. Señales que cuentan como incidente (las audtables del
  sistema): fila `failed` en la cola o el ledger, divergencia de readback
  (`verify_ok=false`) sin resolver, alerta de harvest failed. La
  catalogación fina de "resuelto" es del operador (la duplicación es
  manual); la evidencia son las filas. Éxito = "avanza sin incidentes".
- Cómo se duplica: nueva `config_version` (append-only, `app_admin`) con
  las claves `ads_apply_cap_*` mayores. OJO: el cap de una fila del día ya
  nacida NO se puede subir (sellado 8; PK `(motor, quota_date)`); el efecto
  rige desde la primera fila que nazca después del cambio (normalmente el
  día siguiente). Subir el cap del día en curso no está sellado → si la
  operación lo necesita, lo define 4.2 como admin, nunca el motor.

---

## 6. Reconciliación al inicio del ciclo, contra Amazon VIVO (sellado 13)

Corre al INICIO del ciclo, contra Amazon VIVO, con **identidad completa**:
**plataforma/profile + adGroupId destino + keyword_text + match_type** (r2
codex 7: el texto solo producía falsos "ya aplicada" — fixture señuelo en
otro ad group). Cubre harvest pendiente, negatives normales y `applying`
huérfanos del ledger.

**Evidencia en vivo (regla 8; log `out/regla8-negkeywords.log`):**
`POST /sp/negativeKeywords/list` con vendor
`application/vnd.spnegativekeyword.v3+json` responde 200 en AMBOS perfiles
(US y MX); contenedor `negativeKeywords`; paginación `nextToken` +
`totalResults`; item: `{adGroupId, campaignId, keywordId, keywordText,
matchType, state}`. La tarea 1.3 amplía `LIST_REQUEST_TYPES` con este path.

### 6.1 Matriz de reconciliación (celda por celda; la prueba 2.3)

| Estado local | Qué se consulta | Identidad completa exigida | Veredicto → acción |
|---|---|---|---|
| Cola `pending_veto` / `released` | NADA (no hay mutación en vuelo) | — | Sin acción: la ventana sigue su reloj (§1.4) |
| Ledger sin sello — bid (los bids NO van a la cola, §1.1) | readback GET fresco (keyword / product_target por id externo) | plataforma/profile + entidad + `new_value` quantizado | GET == pedido → confirmar (sellar ledger §4, resumen, `applied_cycle_id`); GET != pedido → divergencia → reintento (tope COUNT) o `failed`; GET ambiguo/5xx → `failed` SIN reintento (conserva su cobro) |
| Cola `applying` huérfana — pause | GET fresco de estado | plataforma/profile + entidad | PAUSED → confirmar; ENABLED → `failed` y además, si "pause verificado propio + ENABLED vivo" → marcar `reactivacion_manual` (§3.3) |
| Cola `applying` huérfana — negative | negativeKeywords list (evidencia arriba) | plataforma/profile + adGroupId destino + keywordText + matchType | Existe con identidad → confirmar; existe SOLO en otro ad group (señuelo) → NO confirma → `failed`; no existe → reintento (tope) o `failed` |
| `harvest_job` fase `pending` | negativeKeywords list del ad group ORIGEN | plataforma/profile + adGroupId origen + keyword_text + match_type | Negativo NO existe → reintentar el POST (seguro: la fuente confirma que no está; el job en vuelo bloquea duplicados); existe → avanzar a `negative_created` |
| `harvest_job` fase `negative_created` | negativeKeywords list (origen) + keywords list (destino) | negativo origen + adGroupId DESTINO + keyword_text + match_type | Keyword destino existe → avanzar `exact_created`/`done`; no existe → reintentar POST keyword; **fallo definitivo → `failed` + reversa automática (delete del negativo) + alerta** (sellado 13) |
| `harvest_job` fase `exact_created` | keywords list del destino | plataforma/profile + adGroupId destino + keyword_text + match_type | Existe → `done` (sellar resumen); no existe → `failed` → reversa (§7) + alerta |
| Ledger sin sello — reversa / probe | El GET/list que corresponda al `tipo` | La misma identidad por kind | Resultado visible → sellar el ledger una vez (§4); ambiguo → `failed` |

Reglas de la matriz:

- La reconciliación NO reintenta mutaciones ambiguas: cierra el estado
  (`failed`); el ciclo siguiente re-decide la clave con datos frescos si
  sigue calificando (la clave terminal ya no está en vuelo).
- El señuelo en otro ad group JAMÁS cuenta como "ya aplicada" (regla 9:
  test con fixture señuelo).
- `harvest_job` nace AL LIBERAR el corte (primer paso del apply del
  harvest), jamás al decidir (r2 grok 8: nacer al decidir dejaba un
  `pending` eterno si el harvest se vetaba; la COLA manda, `harvest_job`
  es la ejecución). **Harvest vetado JAMÁS crea `harvest_job`.** Las
  transiciones de `harvest_job` las sella 0002 por trigger de UPDATE.
- **Bid del harvest (sellado 14):** `new_value` congela el default; el
  sugerido de Amazon se consulta AL APLICAR (endpoint/cliente/guard: regla
  8 en vivo — unknown hasta entonces, §13), se clampea, se PERSISTE como
  intención en el ledger PRE-POST y queda en el ack; sin sugerencia →
  default (regla 3).

---

## 7. Reversas (regla 7 de diseño; sellado 12)

Ninguna acción irreversible sin su reversa implementada antes (regla 7):
**testeada antes en el mismo PR**.

| Mutación original | Reversa |
|---|---|
| bid | escribir `old_value` |
| pause | resume |
| negative | delete del negativo |
| harvest parcial (solo negativo creado) | delete del negativo |
| harvest completo (negativo + keyword) | delete de la keyword PRIMERO, delete del negativo DESPUÉS |

- Cada reversa vive en el ledger como **tipo `reversa`**, **exenta de
  quota** (con test).
- **Una reversa NO limpia el cooldown**: la entidad origen queda fría 7d
  igual (r3 qwen, deliberado) — anti-loop: revertir y re-decidir lo mismo
  al día siguiente sería el ciclo tonto que el cooldown existe para
  impedir.
- El orden de reversa del harvest completo es SELLADO (keyword primero,
  negativo después) — test de orden (regla 9).
- **Residual declarado (ADV-10, review adversaria de phase 2):** la
  reconciliación del harvest adopta por IDENTIDAD COMPLETA (grupo destino +
  texto + exact) y la reversa borra por **id externo** — si el dueño crea a
  mano una keyword EXACT con el mismo texto en el destino durante la
  ventana, el job puede ADOPTARLA y una reversa automática posterior
  BORRARÍA su keyword manual. Mitigación disponible HOY: el **veto dentro de
  la ventana** mata el job antes de que ejecute. Una marca de origen
  (p. ej. conservar el `keywordId` del ack propio y no adoptar ids ajenos
  salvo evidencia) es decisión nueva del dueño si el caso aparece.

---

## 8. Cliente de escritura `app/ads/write.py` (sellado 9; detalle en 1.3)

### 8.1 Allowlist default-deny — nada más que esto

| Operación | Path sellado (probe 2.5, 2026-08-26) |
|---|---|
| update bid de keyword | PUT `/sp/keywords` (body `{keywords: [{keywordId, bid}]}`) |
| pause/resume de keyword | PUT `/sp/keywords` (`state` del REQUEST: HIPÓTESIS `ESTADO_PUT_*`) |
| update bid de product_target | PUT `/sp/targets` (body `{targetingClauses: [{targetId, bid}]}`) |
| pause/resume de product_target | PUT `/sp/targets` (`state` del REQUEST: HIPÓTESIS `ESTADO_PUT_*`) |
| create negative exact | POST `/sp/negativeKeywords` (`matchType=NEGATIVE_EXACT`, `state=ENABLED`) |
| delete negative | POST `/sp/negativeKeywords/delete` (filtro de ids; archiva) |
| create keyword | POST `/sp/keywords` (`matchType=EXACT`, `state=ENABLED`, bid número) |
| delete keyword | POST `/sp/keywords/delete` (filtro de ids; archiva) |

(r2 grok 4: 549 targets US + 861 MX reciben decisiones del motor y la v2
los olvidó.) Todo path fuera de la allowlist se rechaza — default-deny,
con test por path. Shapes de request/ack **fijados por el probe 2.5**
(§11d, ledger probe ids 1-20); única hipótesis pendiente: el `state` del
PUT de pause/resume.

### 8.2 Contrato del request

- **Payload de UN objeto**: multi-objeto rechazado.
- **Headers vendor (Content-Type/Accept) POR PATH de mutación** +
  **`Amazon-Advertising-API-Scope` del profile de LA plataforma de la
  decisión** (r2 grok 18: un apply MX con scope US escribe en la cuenta
  equivocada). Plataforma→profile sellado: scope equivocado imposible.
- **Presentación (sellado):** el bid viaja **quantizado a 2 decimales** y
  la moneda del payload se **verifica contra `goal.bid_currency`** antes
  del HTTP (regla 4).
- **Retry:** 429 reintenta SIN recobrar quota (mismo intento del ledger);
  5xx/ambiguo NO se reintenta (§5.3).

### 8.3 Quién puede construirlo

- El constructor exige **`modo_confirmado` re-resuelto POR DECISIÓN**
  (escalera + `goal.mode` + `enabled` + existencia — incluido el caso
  envelope-live/goal-shadow).
- **Solo `app/apply.py` y `tools/smoke_apply.py`** pueden construirlo:
  candado de imports con la excepción EXPLÍCITA en `test_architecture`
  (r2 codex 5). El smoke corre con `ORBIT_DSN_DECIDE`; sus filas de ledger
  nacen con la identidad del motor, tipo `probe` (sellado 23).
- **El read client SIGUE rechazando PUT/PATCH/DELETE, con test** (r2: el
  atajo natural era relajar el guard viejo).
- 1.3 amplía `LIST_REQUEST_TYPES` con `/sp/negativeKeywords/list` (evidencia
  §6).

---

## 9. Ciclo e integración (sellados 11, 16, 21, 22)

- **Fase de apply DENTRO del lock**: el lock se libera DESPUÉS del apply
  (r2 grok 2: nadie recableaba `corre_ciclo` y el lock se soltaba antes de
  cualquier HTTP).
- **Heartbeat DURANTE mutaciones y readback** (mutaciones lentas no dejan
  morir el lease).
- **Guard `status='running'` en el cierre del envelope** (la mejora que
  `cycle.py` 166-171 anuncia como "de PR2").
- **Ownership-check del lock ANTES de cada HTTP**: lease perdido = abortar
  el apply **fail-closed** (r2 codex 1: el zombie post-TTL con apply son
  dos procesos escribiendo a Amazon). Test con zombie y sucesor
  concurrentes → solo uno muta (regla 9).
- **`HAY_MODULO_APPLY=True` se enciende en la tarea de integración (2.4)**,
  con el candado regla 9: escalera `shadow` → cero HTTP (mock registra); el
  DoD del cierre verifica la escalera en shadow.
- **Cache actualizado CON el readback (sellado 16):** la 0002 otorga a
  `app_decide` UPDATE acotado de `ad_entity_state`
  (current_bid/status/synced_at) que el apply ejecuta con lo LEÍDO tras
  confirmar — sin esto el ciclo siguiente calcula +15% sobre el bid viejo
  (regla 2: la fuente es Amazon y el readback ES de Amazon). El re-check
  "ya estaba" usa GET fresco, jamás el cache.
- **`applied_count` + cooldown por ciclo EJECUTOR (sellado 21):** el
  cooldown de `goals.py` pasa a mirar `applied_cycle_id` (el modo live del
  ciclo que EJECUTÓ, no del que decidió) — con test de punta a punta del
  caso decisión-shadow-aplicada-en-live; `applied_count` cuadra por ciclo
  ejecutor (invariante).

---

## 10. Superficies: auth de escritura, veto, Telegram, goals (sellados 18-20, 26)

### 10.1 AUTH de escritura (sellado 18)

- **Token estático** en TODO endpoint de escritura: secrets `0600` ro,
  `register_secret`, `compare_digest`, **solo header** (query string no
  autentica, con test). Rotación: §11b.
- Los endpoints de escritura usan **`ORBIT_DSN_ADMIN` vía dependencia nueva
  `ConexionEscritura`** (3.1 lo cablea; 4.1 divide env por servicio).
- **Los candados OpenAPI solo-GET se actualizan a la lista sellada GET +
  escrituras autenticadas** (sin eso, CI rojo o el veto escondido).
- `/run` = Reject formal; docstring de `api.py` corregido.
- **Endpoint de veto (admin):** transición a `vetoed` con actor
  (`vetoed_by`), rastro y `vence_el` editable al vetar; corre como admin
  (el trigger de §1.5 lo exige). UI mínima de veto (Phase 3): cortes
  pendientes + vencimiento + botón vetar con bloqueo editable, auth de
  18, XSS cubierto (regla 9).

### 10.2 Telegram (sellado 19)

`app/notifica.py` (nuevo), **fail-silent**: aviso al encolar (con
vencimiento), digest mínimo por ciclo ejecutor, alerta de harvest failed.
Un fallo del canal → warning + **NOTA en `notes` del ciclo** (visible en
Salud) — el silencio del canal jamás es invisible; jamás tumba el ciclo.

### 10.3 Goals amigables (sellado 26)

CLI (`app.cli goals set`) + endpoints write (target/enabled/floor/ceiling
**y los campos `harvest_*`** — dashboard-01 Phase 3 los pide) con auth de
18. `goal` = UPDATE con `updated_at` explícito; config = fila nueva; **una
sola implementación CLI/endpoint** (regla 1).

---

## 11. Procedimientos operativos

### 11a. Alta de una familia harvest nueva (sellado 15)

Regla sellada: **un goal por config** (un goal de plataforma solo admite UN
destino — por eso US tiene DOS goals scope=campaign).

1. En Amazon: campaña manual + ad group destino ACTIVOS (el DoD del seed
   exige destino con `status='ENABLED'` — harvest a campaña pausada gasta
   quota sin servir; destino PAUSED rechazado por el DoD).
2. Correr el sync de estructura y resolver el **`ad_entity.id` POST-sync**
   (los ids citados en sesión son PK internas de referencia; jamás sembrar
   ids sin verificar contra la base).
3. Crear el goal (`app_admin`) con `harvest_campaign_id`,
   `harvest_ad_group_id`, `harvest_default_bid` — el CHECK all-or-nothing
   de 0001 exige la config completa o nada (sin placeholders: la decisión
   HARVEST se salta con motivo). Vía amigable: §10.3.
4. Sembrar/verificar el cap `ads_apply_cap_<platform>_harvest` (§5.2) y el
   fallback del bid (MX: 10.00 MXN; US: 1.00 USD — seeds 4.2).
5. Verificación en vivo (regla 8): `SELECT` del goal + destino ENABLED
   antes de dar el alta por buena.

Goals sellados del seed (4.2): MX = `AC - Category Exact - MX` ad group
553629449717842, fallback 10.00 MXN; US = DOS goals scope=campaign: AU2 →
`AU2 - Category Exact - US` y USPerNog → `USPerNog - Category Exact - US`,
fallback 1.00 USD cada uno. A1U fuera.
**RATIFICACIÓN del dueño (2026-08-27) — el seed EJECUTADO fue otro y este
párrafo queda como historia de la decisión 15, no como estado vigente:**
MX = Arras Manual (108, external `97835222467967`, ad group external
`272585315669297`) con default 2.50 MXN (mediana de bids EXACT reales de la
campaña clampeada al techo del goal); US = goals scope=campaign 6/7 sobre
A1U Exact (3909) y AU2 Exact (3926) SIN terna — el destino harvest US y su
fallback quedan como decisión abierta del dueño. USPerNog (3919) estaba
PAUSED al sembrar: descalificada por el propio DoD "destino PAUSED
rechazado". Para familias NUEVAS el fallback se deriva de los bids reales
del destino elegido (regla 3), no de los literales 10.00/1.00 de arriba.

### 11b. Rotación del token estático de escritura (sellado 18)

1. Generar el token nuevo EN EL SERVER (nunca en el repo; `out/` tampoco).
2. Escribirlo en `secrets/` con permisos `0600` (dir `700`, dueño root; el
   mount es `:ro` — ver DEPLOY.md "Qué se monta").
3. Registrarlo con `register_secret` (redacción de logs) y reiniciar la
   app; el endpoint sigue `compare_digest` y **solo header** (una rotación
   jamás habilita query string).
4. Verificar: sin token → 401; con el token nuevo → 404 sobre un
   `queue_id` INEXISTENTE (p. ej. 999999999 — NUNCA un queue_id real: la
   verificación de la rotación no debe mutar nada); el token viejo →
   401. El fallo de lectura del secret es fail-closed (escrituras 503),
   jamás fail-open.

### 11c. Duplicación de caps (sellado 7)

Ver §5.5: duplicación MANUAL cada **48h sanas** (ventana móvil sin
incidentes sin resolver); se ejecuta insertando una `config_version` nueva
con las claves `ads_apply_cap_*` mayores (append-only, `app_admin`) y
verificando en Salud que la fila del día siguiente nace con el cap nuevo.
No existe camino del motor para subir caps (sellado 8).

### 11d. Probe autorizado de formas reales (tarea 2.5, sellado 23)

Herramienta: `tools/smoke_apply.py` (runbook completo en su docstring; esta
sección es su referencia operativa). **La corrida la AUTORIZA el dueño con
una campaña sacrificable y la coordina el lead; JAMÁS se ejecuta "a ver si
funciona"** (la tarea 2.5 entrega la herramienta y sus tests: la corrida
real es un acto del dueño).

**Las dos capas de autorización (fail-closed):**

1. `ORBIT_SMOKE_AUTH`: token EFÍMERO que el dueño setea SOLO para la
   corrida y borra al terminar. **La capa es REAL (CX5 de la cross-review):
   el valor del env se compara con `compare_digest` contra la clave
   `ads_smoke_auth` de la `config_version` VIGENTE** — sembrada con la misma
   ceremonia de admin que la campaña. Cualquier string no-vacío YA NO basta:
   sin clave sembrada o con token distinto → exit != 0 ANTES de abrir
   credenciales o HTTP. Sin el env (o vacío): exit != 0 ANTES de abrir
   cualquier conexión.
2. `--acepto-mutacion-real`: flag explícito. El env solo NO corre nada —
   nada sale por accidente.

**Campaña allowlisted (JAMÁS por flag/env):** la clave
`ads_smoke_campaign_<platform>` en la `config_version` VIGENTE, con el
`external_id` de la campaña sacrificable. Se siembra con ceremonia de admin:

```sql
-- app_admin; OJO: config_version se resuelve por ÚLTIMA fila — copiar los
-- settings vigentes y AGREGAR las claves (sembrar solo las claves apagaría
-- los caps ads_apply_cap_* para las lecturas de ese día).
INSERT INTO config_version (label, settings)
VALUES ('smoke 2.5', '<settings vigentes
  + "ads_smoke_campaign_<platform>": "<external_id>"
  + "ads_smoke_auth": "<token de un uso de esta corrida>">'::jsonb);
```

Quitarlas al cerrar = fila NUEVA de config sin las claves (append-only).

**Corrida (con `ORBIT_DSN_DECIDE` en el entorno — identidad del motor: sus
filas de ledger nacen tipo `probe` auditable, decision_id NULL,
`quota_cobrada=false`). Dos variantes según dónde corra:**

```bash
# Variante HOST/server (repo clone con out/):
set -o pipefail   # si no, el exit code del pipeline es el de tee, no el del tool
export ORBIT_SMOKE_AUTH="<el MISMO token sembrado en ads_smoke_auth>"
python tools/smoke_apply.py --forma todas --platform <platform> \
  --acepto-mutacion-real 2>&1 | tee out/smoke-apply-<fecha>.log
unset ORBIT_SMOKE_AUTH
# '--forma todas' SOLO si UNA campaña allowlisted cubre las cuatro formas;
# si no (p. ej. las de keyword en una campaña y bid_target en otra), correr
# formas por invocación con configs sucesivas (ver variante contenedor).

# Variante CONTENEDOR (post-4.1: el tool NO va en la imagen y el contenedor
# es non-root — /app no es escribible; la 4.3 corrió en el contenedor y esta
# receta endurecida salió del cross-review posterior: esa corrida pasó el
# token por `-e` argv, con el token efímero ya muerto en la config 10).
# TODO se ejecuta desde el host del repo contra goncloud; el token viaja por
# ARCHIVO dentro del contenedor, JAMÁS por argv de docker exec (no queda en
# history ni en ps), y los archivos nacen 600 (umask 077 en AMBOS lados —
# Greptile P1 PR #39: un `cat >` con umask por defecto deja el token 644):
#   0) ssh goncloud 'rm -f /tmp/smoke_token; umask 077; head -c 48
#        /dev/urandom | base64 | tr -dc A-Za-z0-9 | head -c 32
#        > /tmp/smoke_token'
#      (el `rm -f` previo evita heredar un archivo/symlink 644 anterior —
#      umask solo rige archivos NUEVOS; token efímero SOLO en el host, jamás
#      impreso; sembrarlo en `ads_smoke_auth` leyéndolo del archivo — ver
#      siembra arriba)
#   1) cat tools/smoke_apply.py | ssh goncloud 'docker exec -i orbit-app-1
#        sh -c "cat > /tmp/smoke_apply.py"'
#      ssh goncloud 'docker exec -i orbit-app-1 sh -c "rm -f /tmp/smoke_token;
#        umask 077; cat > /tmp/smoke_token" < /tmp/smoke_token'
#   2) set -o pipefail   # en ESTE shell: el pipe con tee es LOCAL; un
#                        # pipefail dentro del ssh no cubre el `| tee`
#      ssh goncloud 'docker exec orbit-app-1 sh -c
#        "ORBIT_SMOKE_AUTH=\$(cat /tmp/smoke_token) PYTHONPATH=/app python
#        /tmp/smoke_apply.py --forma <X> --platform <platform>
#        --acepto-mutacion-real"' 2>&1 | tee out/smoke-apply-<fecha>.log
#      rc=$?   # capturarlo AQUÍ: el ssh de limpieza del paso 3 lo pisa
#        (stdout = evidencia; la durable es el ledger)
#   3) ssh goncloud 'docker exec orbit-app-1 rm -f /tmp/smoke_apply.py
#        /tmp/smoke_token && rm -f /tmp/smoke_token'   (limpiar el token en
#        AMBOS lados; `&&` para que un fallo del docker exec NO quede tapado
#        por el rm del host; el allowlist de UNA campaña por plataforma y
#        las configs sucesivas A/B: ver OJO al final de esta sección)
#      echo "smoke rc=$rc"   # 0 = neto cero; cualquier otro = NO seguir
# RESIDUAL declarado (bots PR #39): la ruta /tmp/smoke_token es predecible
# y hay una ventana rm→`>` en la que otro proceso con escritura en ese /tmp
# podría colar un symlink. Modelo de amenaza real: en el host solo entra
# root por ssh; en el contenedor corre UN proceso (uid 10001) que ya tiene
# los secrets de Amazon y el DSN — quien pueda escribir ahí no gana nada con
# el token; y el token es de un solo uso, muere con el cierre sin claves.
# mktemp con ruta propagada a cada paso no cambia ese balance.
```

RESIDUAL declarado (visto en 4.3): `config_version` es append-only, así que
el token del smoke queda en **texto plano** en las filas históricas
(`settings.ads_smoke_auth` de las configs de humo: 4/5 del 2.5, 8/9 del
4.3). Está muerto funcionalmente — el tool es fail-closed contra la config
VIGENTE y el cierre sin claves lo desarma — pero cualquiera con lectura a la
base lo ve. Por eso el token es de UN solo uso por corrida y nunca se
reutiliza; no hay borrado (la tabla no admite UPDATE/DELETE).

OJO (medido en 4.3): el allowlist es UNA campaña por plataforma — si las
formas necesitan dos campañas (las de keyword viven en una y el
`bid_target` en otra), se siembran DOS configs sucesivas (A → correr formas
de keyword; B → correr `bid_target`; cierre sin claves), cada una copiando
los settings vigentes.

Cada forma imprime UNA línea JSON de evidencia (saneada por scrub):
request EXACTO, ack (body + headers sin secretos), readback y reversa.
Las cuatro formas (decisión 23): `bid_keyword` (±0.01 con reversa al
ORIGINAL LEÍDO), `bid_target` (idem), `negative` (create+delete neto cero
sobre término basura), `keyword` (create+delete neto cero — el corazón del
harvest; su bid sale de una fuente REAL: el bid LEÍDO de la primera keyword
EXACT de la campaña). `--forma todas` corre las cuatro en orden y SE DETIENE
en la primera que falla (fail-closed). Exit 0 solo si TODO quedó neto cero.

**HIPÓTESIS SIN VERIFICAR (orden explícito del dueño):** los ENUMS y tipos
del REQUEST de mutación JAMÁS corrieron contra la API real — la corrida
autorizada los FIJA. Declaradas en `HIPOTESIS_SHAPES` de la herramienta y
viajan en la evidencia de cada forma: `matchType 'exact'` vs
`'negativeExact'`; `state 'userPaused'/'enabled'`; el bid como string
quantizado a 2 decimales; el campo del id creado en el ack (`keywordId` vs
`negativeKeywordId`); el contenedor del GET de readback
(`'keywords'/'targets'`); el body del DELETE. Si la corrida corrige uno,
se arregla `write.py` y los tests se re-sellan. **Actualización 2026-08-27:
el `state` del PUT ya NO es hipótesis — quedó sellado UPPER
(`PAUSED`/`ENABLED`) por la corrida de reactivación (ver abajo).**

**Cómo se cierra la tarea 2.5 con esta corrida:** (1) verificar exit 0 y
`neto_cero=true` en las cuatro líneas de evidencia + estado final ==
inicial; (2) contra cada ack/readback REAL, confirmar o corregir las
hipótesis de arriba; (3) FINALIZAR los tests de readback de 2.1-2.3 hoy
marcados "pendientes de shape" (§13.2) sellándolos contra los shapes reales
(regla 8); (4) el dueño borra `ORBIT_SMOKE_AUTH` y el admin siembra config
nueva sin las claves de campaña NI `ads_smoke_auth`; (5) evidencia (log +
`SELECT` del ledger probe) al registro de ORBIT 04. El ensayo E2E de 4.3
re-usa esta misma herramienta.

**SHAPES FIJADOS (corrida real 2026-08-26, ledger `apply_attempt` probe ids
1-20, log `out/smoke-apply-20260826.log`; 4/4 formas neto cero; RE-CONFIRMADOS
punta a punta por el ensayo E2E 4.3 del 2026-08-28 — ids 22-29 — contra el
deploy real, mismo resultado 4/4):**

- **Readback por LIST**: el GET directo de entidad sp responde **403**
  (RETIRADO; apply_attempt 4-5) — el único camino de lectura es el POST de
  lista (`/sp/keywords/list` contenedor `keywords`, `/sp/targets/list`
  contenedor `targetingClauses`) con cruce de id. El readback del MOTOR
  (bids/pauses/reversas/reconciliaciones) migró a `list_sellado`
  (`app/apply.py`, `app/apply_cola.py`, `app/apply_harvest.py`);
  `get_sellado` queda SOLO para el PENDIENTE-DE-REGLA-8 de bid sugerido.
- **Contenedor del body**: toda mutación de colección viaja como ÚNICA
  entrada de la lista bajo el contenedor del recurso (`keywords` /
  `targetingClauses` / `negativeKeywords`; apply_attempt 1, 6-7, 13, 18-19).
- **Bid como NÚMERO JSON** quantizado a 2 decimales (string → 400
  "STRING_VALUE is not an expected Json type", apply_attempt 3); el LEDGER
  sigue congelando la intención con el bid string (`_bid_payload`).
- **Enums UPPER**: `matchType` es `EXACT` en keywords y `NEGATIVE_EXACT` en
  negatives; `state` es OBLIGATORIO en los POST (enum [ENABLED, PROPOSED,
  PAUSED], apply_attempt 9). El LIST responde states UPPER
  (`ENABLED/PAUSED/ARCHIVED`, apply_attempt 19-20) — `userPaused` NO existe
  en la RESPUESTA.
- **Delete v3**: el DELETE directo del collection responde **403 SigV4**
  (NO existe, apply_attempt 12); el camino real es `POST
  /sp/{recurso}/delete` con `{"<recurso>IdFilter": {"include": [id]}}` → 207
  (apply_attempt 14 y 17). El "delete" **ARCHIVA**: el item sigue en el
  list con `state=ARCHIVED` — operativamente muerto (la identidad viva lo
  ignora).
- **Ack 207** con `success`/`error` anidados por recurso; el id del objeto
  creado vive en el primer `success` (apply_attempt 13 y 16; ya lo parsea
  `_id_de_ack`).
- **State del PUT de pause/resume — SELLADO 2026-08-27** (corrida de
  reactivación autorizada por el dueño, evidencia
  `out/reactiva-campanas-20260827.log`): el REQUEST exige UPPER
  (`PAUSED`/`ENABLED`; `'paused'` minúscula responde 400 con el enum exacto
  `[ENABLED, PROPOSED, PAUSED]`). La hipótesis vieja `userPaused`/`enabled`
  (`write.py ESTADO_PUT_*`) quedó **refutada** y las constantes corregidas.
  Sellos extra de esa corrida: el **id viaja como STRING** en el body (con
  número JSON: 400 `NUMBER_VALUE can not be converted to a String`) y los
  headers exigen el **vendor v3 EXACTO en `Content-Type` Y `Accept`** (sin
  `Accept`: 415; campañas: `application/vnd.spcampaign.v3+json`). El READBACK
  compara contra el wire verificado del list (`app/apply.py ESTADO_WIRE_*`).

---

## 12. Checklist de cutover ORBIT 05

El flip NO es parte de ORBIT 04 (ORBIT 04 entrega TODO con la escalera en
`shadow`). Prerequisitos sellados: **2 semanas de shadow (~2026-09-07) +
recálculo manual + veto ejecutado por el dueño sobre una fila real (4.3)**.

1. [x] Prerequisito: veto real del dueño sobre una fila shadow registrado
       con su actor (4.3: fila 3 por delegación del dueño actor 'gon' + fila
       4 veto PERSONAL del dueño actor 'gon-personal' 2026-08-28) +
       re-verificación del smoke E2E contra el deploy real (4.3, 4/4 formas
       neto-cero) + recálculo manual (spot-check 33 decisiones / 0
       divergencias de recálculo, 2026-08-28; firma del dueño pendiente —
       `out/orbit-04-4-4-cierre-20260828.md` §3.4).
2. [x] **Backup pre-cutover** (2026-08-28): dump completo + globals +
       CSV de `ad_entity_state` (5,899 filas) + listas de Amazon
       (keywords/negativeKeywords/targets, 2 plataformas) en
       `backups/precutover_orbit04_2026-08-28/` (fuera de la rotación) —
       **VERIFY_OK**: restore real con los 5 conteos idénticos a producción
       (4.4).
3. [ ] **Discard masivo de filas shadow:** TODA fila shadow pendiente →
       `discarded` en UNA transacción; conteo antes/después concilia; cero
       filas shadow no terminales al terminar.
4. [ ] **Flip:** modo live por la escalera (decisión humana, config nueva;
       off→shadow→live del diseño v2 adoptado en CONTEXTO).
5. [ ] Rampa día 1 ya sembrada: 10 bids / 2 pauses / 5 negatives /
       2 harvests por día y plataforma (4.2), fail-closed verificado.
6. [ ] El live arranca SOLO con decisiones frescas post-flip (ventana
       completa 48h desde cero).
7. [ ] Monitoreo 48h (checklist PR2 del diseño v2: caps bajos día 1,
       monitoreo 48h) + digest por ciclo ejecutor activo.
8. [ ] **Verificación adversarial TRIPLE (codex+grok+qwen) de las primeras
       decisiones APLICADAS EN VIVO** (ritual sellado en la aprobación del
       plan, AppFlowy 2026-08-24; movido aquí desde el DoD de 4.4 — el
       commit 1e41a1f lo había colado en la tarea de cierre, pero pertenece
       a ORBIT 05: solo tiene sentido con decisiones live reales).
9. [ ] Post-flip: SELECT de la cola (cero shadow pendientes), quota del
       día, `HAY_MODULO_APPLY` con escalera live verificada.

---

## 13. Unknowns declarados (header) y pendientes de este brief

Unknowns del header (los fija la tarea indicada; nadie los "resuelve"
antes):

1. **Endpoint/shape del bid sugerido** — regla 8 en vivo define endpoint,
   cliente y guard (sellado 14). Hasta entonces: sin sugerencia → default.
2. **Shapes de acks** — los fija **2.5** con el probe autorizado (las
   CUATRO formas, incluido keyword create+delete neto cero; procedimiento y
   hipótesis declaradas: §11d); los tests de
   readback de 2.1-2.2 nacen marcados "pendientes de shape" hasta ahí.
3. **Ad groups e ids `ad_entity` reales de las Exact US**
   (post-reactivación) — se verifican POST-sync en 4.2.
4. **Payload de list** — el REQUEST payload exacto de
   `/sp/negativeKeywords/list` no está capturado; el RESPONSE ya está
   verificado en vivo (§6). Lo fija 1.3/2.3 con regla 8.
5. **`NULLS NOT DISTINCT` contra el PG16 del server** — se verifica en
   vivo en 1.2/4.1 antes de confiar el único parcial a esa sintaxis.

Pendientes declarados por este brief (detalle fino DENTRO de lo sellado;
los fija el implementador de la tarea correspondiente):

- **Residual (ADV-04, review adversaria de phase 2):** la reconciliación de
  ledger sin sello cableada en phase 2 cubre SOLO intentos `tipo='normal'`
  de decisions kind `bid`. Las filas **reversa/probe sin sello** (matriz
  §6.1 última fila) NO tienen reconcilador: un crash entre el ledger de una
  reversa y su HTTP queda abierto hasta que el operador lo resuelva contra
  el GET/list correspondiente. Se declara en vez de implementarse a ciegas
  porque cada tipo exige su propio readback y no existe aún un caso real.
- Nombres de columnas no citados por el header (p.ej. el sello
  ack/resultado del ledger): los fija 1.2 sin salirse de §4.
- Si el probe (2.5) consume quota o va exento (§5.3).
- Atribución de `applied_cycle_id` cuando la confirmación llega por
  RECONCILIACIÓN en el ciclo siguiente (§6): el header sella "ciclo
  EJECUTOR" sin cubrir la confirmación tardía; lo fija 2.1/2.3 con test.
- Subir el cap de una fila del día ya nacida: no sellado (§5.5); si la
  operación lo necesita, lo define 4.2 como admin.

Notas de la revisión de Phase 1 (r1 del reviewer; para 2.1/2.3/0003, nadie
las resuelve antes):

- `AdsWriteClient` hereda los métodos de LECTURA del read client (`get`,
  `list_objects`, ...) que ACEPTAN `profile_id` arbitrario: el scope sellado
  a la instancia cubre las 10 mutaciones; el aplicador (2.1) debe pasar el
  MISMO profile del cliente en el re-check GET fresco — o envolverlo en un
  helper de lectura con scope sellado.
- `>= 400` lanza `AdsApiError` con solo status+método+path (redacción del
  read client): el cuerpo del error de Amazon se pierde y el `resultado` del
  ledger (2.1) heredaría esa pérdida; valorar exponer la respuesta o un
  snippet saneado.
- Índices/backstops futuros baratos como 0003: índice `(estado, vence_el)`
  para el barrido FIFO del liberador (irrelevante con los caps del día 1) y
  `UNIQUE (decision_id, seq)` en `apply_attempt` cuidando los probes con
  `decision_id` NULL (el tope-3 es COUNT de la app, sellado 10).
- Un discard bajo `SET ROLE app_admin` (como el flip de ORBIT 05) no está
  ejercitado en los tests DB de 1.2; los grants sí están pineados estáticos.

Declarados SIN fix tras la cross-review del dueño (codex+grok+qwen,
2026-08-26; suscrito por el reviewer — nota, no sello roto):

- Tope-3 check-then-act (qwen): sin UNIQUE `(decision_id, seq)` el COUNT y
  el INSERT no son atómicos entre DOS aplicadores concurrentes; el claim
  del lock serializa la fase de apply POR PLATAFORMA (una decisión, un
  camino, un dueño), así que la carrera requiere romper el claim primero.
  Backstop UNIQUE = candidato 0003 (ya declarado arriba).
- Doble scrub idempotente (qwen): `_snippet_cuerpo` y la excepción aplican
  `scrub()` dos veces; redaction.py asume idempotencia en todos sus usos.
  Blindaje barato pendiente (P2): try/except alrededor del
  `json.loads(scrub(...))` del ack.
- Un 5xx/fallo ambiguo de mutación ABORTA el lote de bids en curso (qwen):
  diseño sellado (§5.3, "no se reintenta") — el ciclo sella degraded y el
  siguiente reconcilia; confirmado deseado.
