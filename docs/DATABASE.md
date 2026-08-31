# Orbit — Diseño de base de datos

> Implementación: `migrations/0001_initial.sql` (PostgreSQL 16, una sola
> transacción `BEGIN;…COMMIT;`). Este documento explica el diseño tabla por
> tabla y su justificación; el detalle fino de cada constraint vive en los
> `COMMENT ON` del propio SQL para que no se desincronicen.
>
> Principio rector: **la base rechaza, la aplicación no recuerda**. Cada
> decisión mata un error concreto del sistema viejo (`goncloud-MCP-2`,
> cancelado 2026-08-21). Las "Regla N" referencian las reglas de diseño
> innegociables de `docs/CONTEXTO.md`; los umbrales del optimizador mandan en
> `docs/traspaso/ADS_OPTIMIZER_V2_DESIGN.md`.

## Convenciones transversales

- **Dinero**: dominio `money_amount` (`NUMERIC(14,4)`) + columna `currency`
  (ENUM `MXN`/`USD`) en toda tabla que guarde montos (regla 4). Prohibido
  float. **Excepción de tipo, no de regla**: `decision.old_value`/`new_value`
  son `NUMERIC` crudo (no `money_amount`) porque su significado depende del
  `kind`; la moneda se les exige por CHECK para **todo kind que mueve dinero
  —`bid`, `budget` y `harvest`—** y se prohíbe la moneda suelta en los que no
  (`decision_moneda_solo_en_kinds_con_dinero`). Ninguna tabla con dinero sin
  moneda; ningún agregado que mezcle
  monedas (las vistas agrupan por plataforma —y por moneda donde aplica— y
  convierten solo vía `fx_resolve`; `v_tacos` agrupa por plataforma y
  convierte cada lado por fila a la moneda canónica MXN).
- **Desviación declarada de la regla 4 ("fecha_fx")**: el esquema es más
  fuerte que la letra de la regla — **nunca persiste el monto convertido**, así
  que no hay fecha_fx que guardar; `fx_resolve` devuelve `rate_date` + `source`
  en lectura, y eso ES la fecha_fx. Que nadie lo "corrija" después agregando
  columnas convertidas.
- **Append-only**: `ads_metric_observation`, `search_term_observation`,
  `ledger_event`, `config_version`, `decision`, `fx_rate` y
  `external_reconciliation` tienen trigger `prohibir_mutacion`
  (`BEFORE UPDATE OR DELETE`). Corregir = insertar una fila nueva, jamás pisar
  (regla 5: el UPSERT in-place invalidó todos los backtests del sistema viejo
  sin dar síntoma).
- **Excepciones mutables deliberadas** (todas documentadas en su tabla):
  `ingest_run` y `optimizer_cycle` nacen abiertas y se cierran con UPDATE
  acotado por columna; `decision_application` se pisa solo por readback;
  `ad_entity_state` es cache del estado actual en Amazon; `ads_optimizer_goal`
  es configuración viva; `ads_optimizer_lock`, `harvest_job`, son estado de
  operación; `apply_quota_state` solo consume (`UPDATE (used)`); `sku_cost`
  solo cierra vigencias (`UPDATE (valid_to)`); el resto del catálogo
  (`product`, `listing`, `ad_entity`) admite correcciones controladas.
  `sku_cost` es la única con **candado propio** en vez de simple ausencia de
  GRANT: el trigger `sku_cost_solo_cierra_vigencia` permite cambiar `valid_to`
  y **nada más**, y prohíbe el `DELETE` (borrar una vigencia publicada
  reescribe el histórico de márgenes hacia atrás). **Cerrar es una transición
  única `NULL → fecha`**: mover el corte de una vigencia ya cerrada
  (`DATE → DATE`) o reabrirla (`DATE → NULL`) también reescribe el período en
  que ese costo aplicó, y el `EXCLUDE` **no lo detecta** — encoger un rango
  nunca solapa, y extenderlo tampoco si la fila no tiene sucesora. Precio
  declarado: cerrar en la fecha equivocada se corrige con una migración, no
  con un UPDATE.
- **Toda FK tiene índice de apoyo**: PostgreSQL no crea uno por el
  `REFERENCES`. Sin él, cada verificación de integridad al tocar la tabla
  padre barre la hija entera y los JOIN que el propio esquema declara
  (`v_margen_plataforma` cruza `ledger_event.product_id` contra `sku_cost`)
  salen a secuencial. Los índices **parciales no cuentan** (la verificación de
  integridad consulta la clave sin el filtro del `WHERE`): por eso
  `ads_optimizer_goal` y `harvest_job` llevan índice propio además de sus
  únicos parciales. Lo afirma `test_toda_fk_tiene_indice_de_apoyo` sobre el
  AST.
- **Dato faltante = fila ausente o NULL, nunca constante mágica** (regla 3):
  `fx_resolve` devuelve cero filas si no hay tasa usable; `sku_cost` rechaza
  costo 0; la config de harvest va completa o no va; `is_asin_like` no tiene
  default; `margen_contribucion` es NULL si la cobertura de COGS no es 100%.
- **Idempotencia de ingesta por schema**: índices únicos parciales sobre los
  ids de fuente (`source_report_id`, `source_event_id`); re-correr un sync es
  `INSERT … ON CONFLICT DO NOTHING`, no duplicar. Las filas absorbidas por el
  dedupe **cuentan como `rows_skipped` con motivo en `ingest_run`** — un dedupe
  que no deja rastro es un dato perdido disfrazado de eficiencia.
- **CHECKs y zona horaria**: PostgreSQL **acepta** expresiones no inmutables en
  un CHECK sin quejarse y luego las evalúa **según la TimeZone de cada sesión**
  (los casts `timestamptz::date` / `date::timestamptz` son STABLE): el mismo
  CHECK pasaría en una sesión y fallaría en otra. Por eso los invariantes que
  comparan fecha con timestamp viven en **triggers** con UTC fijado en la
  expresión (`decision_madurez_corte` usa `decided_at AT TIME ZONE 'UTC'`); la
  app corre con TZ UTC como segunda capa.
- **Moneda sellada por plataforma en métricas de ads**: trigger
  `metric_moneda_de_plataforma` con mapa fijo (amazon_mx→MXN, amazon_us→USD,
  meli→MXN). `ledger_event` está **exento a propósito**: guarda montos en
  moneda ORIGINAL, que puede no ser la de la plataforma (ej. cargo en USD en
  cuenta MX).
- **Los invariantes sellados tienen test**: `tests/test_schema.py` los afirma
  sobre el AST de la migración con pglast (corren siempre), más un test de
  integración que aplica la migración en un Postgres temporal y prueba rechazos
  reales (skip automático si no hay servidor local).

## Tablas

### Procedencia y catálogo

**`ingest_run`** — Una corrida de ingesta = una fila; toda fila de hechos
apunta a su corrida. `rows_skipped > 0` exige `skip_reason` (regla 3 y 10: en
el viejo las filas saltadas por falta de FX solo dejaban un `log.error` y se
perdían al salir de la ventana de reproceso). El cierre es UPDATE por columna
(`finished_at`, `rows_written`, `rows_skipped`, `skip_reason`, `ok`), nada más.
*Cómo se audita*: invariante diario — corridas con `finished_at` NULL viejas
(corrida colgada) y corridas con `ok = false` o `rows_skipped > 0` sin
reproceso posterior.

**`product`** — Identidad canónica = `odoo_sku`, NO el `seller_sku` de
plataforma (confundirlos duplicaba ventas 48% en un JOIN).
*Cómo se audita*: `odoo_sku` UNIQUE; job de catálogo cruza contra Odoo.

**`listing`** — Un producto tiene 2–4 listings (varios ASIN por SKU).
Precio con moneda obligatoria por `CHECK` (ambos NULL o ambos presentes).
*Cómo se audita*: UNIQUE `(platform, external_id)`; conteo de listings por
producto fuera de rango esperado levanta alerta.

**`sku_cost`** — Costo con vigencia temporal: el costo de una venta es el
vigente *a su fecha* (en el viejo, cada cambio en Odoo corrompía el histórico
hacia atrás). `EXCLUDE USING gist` prohíbe rangos solapados por producto
(por eso `btree_gist`). `includes_tax` es `NOT NULL` **sin default**: obliga a
contestar si el costo lleva IVA (pregunta sin respuesta un año = 8 puntos de
margen en MX). Costo 0 rechazado: costo faltante = ausencia de fila (el 49%
del COGS de MeLi era 0 y pasó tres auditorías como "limpio"). **La ingesta
solo puede `UPDATE (valid_to)`**: cerrar vigencias sí; reescribir
`cost_amount`/`valid_from` de una fila publicada, jamás — la corrección es una
fila nueva con vigencia nueva (el EXCLUDE ya garantiza que no se solape).
**Y no descansa en el permiso**: el trigger `sku_cost_solo_cierra_vigencia`
(`BEFORE UPDATE OR DELETE`, más el de sentencia contra `TRUNCATE`) rechaza
cualquier UPDATE que toque algo distinto de `valid_to`, admite en `valid_to`
**sólo la transición `NULL → fecha` y una sola vez** (mover el corte o reabrir
la vigencia reescribe el período en que el costo aplicó, y el `EXCLUDE` no lo
ve porque encoger no solapa) y prohíbe el DELETE, aunque el rol tenga todos
los permisos — la misma razón por la que las
append-only tienen trigger y no sólo GRANT.
*Cómo se audita*: el `EXCLUDE` garantiza un solo costo vigente por fecha;
invariante — productos activos sin costo vigente hoy (lista explícita, no
cero disfrazado).

**`ad_entity`** — Campaña / ad group / keyword / product target / placement,
con `external_id` de la API. Los search terms NO son entidades: tabla aparte.
**Convención keywords**: `match_type` y `keyword_text` son columnas propias,
exigidas NOT NULL solo cuando `kind='keyword'` y NULL en los demás kinds por
CHECK — el duplicate-check del harvest contra la campaña manual destino las
necesita sin parsear payloads (el `bid_cache` viejo no tenía `keyword_text`).
**Identidad inmutable por permisos**: `app_ingest` solo puede
`UPDATE (name, listing_id)` — `platform`, `kind`, `external_id`, `parent_id`,
`match_type` y `keyword_text` se fijan en el INSERT (mutarlos tras insertar
hechos rompería el sello de moneda a posteriori y dejaría goals apuntando a
kinds que ya no son campaign); corregir una entidad = crear una nueva y
desactivar la vieja.
*Cómo se audita*: UNIQUE `(platform, kind, external_id)`; índice sobre
`parent_id` para recorrer la jerarquía.

### Tipo de cambio

**`fx_rate`** — Append-only, PK `(rate_date, base, quote)` con AMBAS monedas
(el viejo filtraba solo por quote y tomaba tasas invertidas al revés).
Tasa > 0, par distinto.
*Cómo se audita*: **`fx_resolve(fecha, base, quote, max_age=7)`** — devuelve la
tasa exacta o la anterior más cercana dentro de 7 días (tope medido contra la
cadencia real), declarando `source` (`exact`/`nearest_prior`). Sin tasa usable
→ **cero filas, nunca una constante** (el fallback silencioso a 20.5 infló
revenue +28,549 MXN). `rate_date` + `source` devueltos son la fecha_fx de la
regla 4, resuelta en lectura. Invariante: huecos de FX > 5 días por par.

### Métricas de ads (el corazón)

**`ads_metric_observation`** — **Append-only bitemporal** (regla 5, la más
importante): PK `(ad_entity_id, metric_date, observed_at)` — el día del hecho
Y el día de la observación. Cada re-lectura de Amazon es una fila nueva; el
clawback (Amazon retira costo hasta D+15) se registra sin pisar la historia,
y el backtest honesto (`metrics_as_of`) es posible. Moneda obligatoria y
**sellada por trigger contra la plataforma de la entidad** (amazon_us reporta
ads en USD y ventas en MXN; cruzarlas era el error de 18.66× siempre a favor
de "todo es rentabilísimo"). `revenue_same_sku <= ad_revenue` por CHECK (el
halo es atribución, no causalidad). No-negativos incluye `impressions`.
Idempotencia: índice único parcial `(ad_entity_id, metric_date,
source_report_id)`. Nota: el invariante "`observed_at >= metric_date`" no vive
en un CHECK (cast STABLE evaluado por sesión, ver Convenciones); lo valida la
ingesta.
*Cómo se audita*: trigger `prohibir_mutacion` + sello de moneda; guardas del
ciclo leen de aquí (frescura: ventana termina en `max(metric_date) − 3d`;
completitud ≥7 fechas por entidad).

**`search_term_observation`** — Append-only igual que la anterior pero para
search terms: PK `(platform, ad_entity_id, search_term, metric_date,
observed_at)`, dedupe por `source_report_id`, sello de moneda por trigger.
**`is_asin_like` es NOT NULL SIN DEFAULT**: la ingesta está obligada a
clasificar cada término — un ASIN propio sin clasificar no puede entrar (los
ASIN-like siempre se saltan en negativos, regla sealed own-ASIN; un default
`false` convertiría "no lo revisé" en "no es ASIN", el dato faltante
disfrazado que prohíbe la regla 3). Alimenta NEGATIVE_EXACT y HARVEST.
*Cómo se audita*: trigger `prohibir_mutacion`; las mismas guardas de
frescura/completitud del ciclo.

### Ledger

**`ledger_event`** — Ventas, fees, refunds, retenciones. Monto **siempre en
moneda original** (la conversión es vista; el viejo guardaba todo convertido y
77 ventas quedaron irreversibles con tasa inventada). `order_id` NULLABLE a
propósito: el ISR de Amazon nunca lo trae y llega en bultos quincenales (en
MeLi al revés: 950/950 con order_id) — por eso el ISR se caía de todo margen.
**`quantity` INTEGER**: una venta con `product_id` exige `quantity > 0` por
CHECK (sin unidades no hay COGS). **Convención de signos por CHECK**:
`sale > 0`; `fee`/`refund`/`withholding` `<= 0` — así `SUM(amount)` funciona
sin filtros y ningún reporte inventa su convención. **Idempotencia**: tres
únicos parciales que cubren TODO el espacio `(source_event_id, order_id,
ninguno)`: `ledger_dedupe_source` por `source_event_id`; `ledger_dedupe_sin_orden`
para cargos sin id de fuente ni orden (el ISR); `ledger_dedupe_con_orden` para
cargos CON `order_id` pero sin id de fuente (un re-sync de fees con orden) —
sin él, esa fila no caía en ninguno de los otros dos y se duplicaba en
silencio. Los dos últimos usan la clave natural `(platform, kind,
[fee_type | order_id,] event_date, amount, amount_currency)` con
**`NULLS NOT DISTINCT`** — la moneda va en la clave (sin ella 100 USD y 100
MXN colisionan y un cargo se pierde) y sin NULLS NOT DISTINCT dos cargos con
`fee_type` NULL nunca chocarían.
*Cómo se audita*: trigger `prohibir_mutacion`; conflictos de dedupe contados
en `ingest_run.rows_skipped`; `v_margen_plataforma` expone el margen con y sin
cargos no atribuibles; `external_reconciliation` es el chequeo final.

### Configuración y decisiones

**`config_version`** — Snapshot inmutable (JSONB) de la config completa; **la
config "viva" vigente es la fila más reciente** (segundo peldaño de la cascada
de target ACoS: `ads_target_acos_pct_<platform>`). Cada decisión apunta a la
versión que regía. La inserta `app_admin` (config humana, escalera
off→shadow→live), no los motores. Claves de settings del optimizador (resuelve
`app/optimizer/goals.py`, task 2.4): `ads_optimizer_mode` (escalera global,
valores `off|shadow|live`, ausente → `off` fail-closed) y
`ads_target_acos_pct_<platform>` (target por plataforma, ej
`ads_target_acos_pct_amazon_us`; la siembra humana es 4.3).
**`settings` JAMÁS contiene credenciales**: `app_read` tiene SELECT aquí.
Contrato fail-closed completo: `ads_optimizer_mode` ausente, NULL o inválida → `off`
(una config corrupta jamás habilita `live`); si el modo efectivo es `live` sin
módulo apply (PR1) degrada a `shadow` + nota. Cada target ACoS (`goal`, setting,
cache) debe ser numérico, finito y > 0 — un peldaño presente pero corrupto
revienta con `ValueError`, jamás cae al default 55 en silencio (regla 3).
*Cómo se audita*: append-only por trigger; JOIN desde `decision`.

**`optimizer_cycle`** — Envelope de ciclo: una corrida = una fila (`motor`,
`mode`, `platform`, contadores, `status`, `notes`). Nace en `running`; mutable
solo para cerrarse, con UPDATE acotado por columna. `status` sellado por
CHECK: `running` / `done` / `degraded` / `skipped` / `failed`. Un ciclo que no
decidió nada también es un resultado auditable (las guardas dejan su motivo en
`notes`). **Orbit NO tiene tabla `system_alerts`** (era del repo viejo): el
sustituto por ahora es `status='degraded'` + `notes`, y el digest diario de la
Fase 3.
*Cómo se audita*: invariante — ciclos sin cerrar viejos; `decisions_count`
cuadra contra `decision` por `cycle_id`.

**`decision`** — Append-only. Reemplaza al `ads_optimizer_audit` del diseño
v2 (auditar no es tabla aparte: ES la tabla). `data_observed_at <= decided_at`
por CHECK (comparación timestamptz↔timestamptz, inmutable: decidir con dato
posterior es lookahead, imposible por schema). **`window_start`/`window_end`
(DATE, NOT NULL)**: la ventana de métricas con que se decidió — sin ella la
regla de madurez no se puede cumplir ni auditar. **`search_term` TEXT**:
identidad del término para `negative`/`harvest` (NOT NULL por CHECK en esos
kinds; NULL en `bid`/`budget`/`pause`/`resume`). **Unicidad por ciclo, hecha
cumplir por la base**: un único parcial por `(cycle_id, ad_entity_id)` para
kinds de entidad, y otro por `(cycle_id, ad_entity_id, search_term)` para
kinds de término — la misma entidad puede generar varios negative/harvest en
un ciclo (uno por término), pero nunca dos sobre el mismo término.
**Madurez para cortar (regla 6) por trigger, no CHECK**:
`decision_madurez_corte` (`BEFORE INSERT`) exige para `pause`, `negative` **y
`harvest`** (su primera fase es crear el negativo en el origen: un corte) que
`window_end <= decided_at − 10 días`, con **`decided_at AT TIME ZONE 'UTC'`**
fijado en la expresión (curva medida de falsos cortes: día 0 = 8.7%, día 10 =
0.00%; pausar es irreversible en la práctica porque la entidad pausada deja de
generar la señal que la revertiría). No es CHECK porque PostgreSQL acepta
expresiones STABLE en CHECKs y las evalúa por sesión; el trigger fija UTC en
la expresión misma (la app con TZ UTC es segunda capa).
**Moneda por kind (regla 4)**: `old_value`/`new_value` son `NUMERIC` crudo
(su significado depende del `kind`), así que el candado de moneda es explícito
— `decision_valor_con_moneda` exige `value_currency` para `bid`, `budget` **y
`harvest`** (su `new_value` es el bid inicial de la keyword harvesteada,
`goal.harvest_default_bid`), y `decision_moneda_solo_en_kinds_con_dinero`
prohíbe la moneda suelta en los kinds que no mueven dinero (moneda sin importe
= dato inventado, regla 3).
*Cómo se audita*: trigger append-only + trigger de madurez + únicos parciales;
índices `(ad_entity_id, decided_at DESC)`, `(cycle_id)` y
`(config_version_id)` para cooldown, reconstrucción de ciclo e integridad; cooldown 7d solo cuenta applies verificados
(`decision_application.verify_ok IS TRUE`).

**`decision_application`** — Separación decidir/aplicar (la regla más cara del
viejo: `applied=1` no significaba que Amazon lo aceptara). Patrón:
INSERT+COMMIT → HTTP → readback → UPDATE. La **terna
`confirmed_at` + `platform_ack` + `verify_ok` se escribe junta en el
readback**, y los CHECKs lo hacen cumplir: confirmado sin ack no existe,
confirmado sin veredicto tampoco; `error` y `confirmed_at` excluyentes.
**`verify_ok` BOOLEAN**: NULL = en vuelo, TRUE = la plataforma tiene lo
pedido, FALSE = divergencia (se reintenta). El cooldown 7d consulta
`verify_ok` directamente — **nunca parsea el ack**: el ack es evidencia, no
señal de control. Es la excepción mutable deliberada: el readback pisa el
intento, nunca la decisión.
*Cómo se audita*: invariante diario — intentos en vuelo viejos; divergencias
(`verify_ok = false`) no enfrían el cooldown.

**`external_reconciliation`** — Regla 10: conciliar contra la fuente externa
(reportes de liquidación de Amazon), no contra la propia consistencia interna.
Totales externo e interno por período/plataforma/moneda, `difference`
generada. **Append-only con historia** (trigger `prohibir_mutacion`), sin
UNIQUE por período a propósito: re-conciliar un período corregido es una fila
nueva — la verificación anterior es historia que no se pisa, y la diferencia
que desaparece entre dos corridas es la prueba de que la corrección funcionó.
La vigente por período/fuente es la de mayor `checked_at` (índice dedicado).
La escribe `app_ingest` (entra por el pipeline de conciliación).
*Cómo se audita*: diferencia ≠ 0 fuera de tolerancia es alerta, no se
"ajusta"; la serie histórica por período muestra si las correcciones convergen.

### Optimizador (spec: ADS_OPTIMIZER_V2_DESIGN.md)

**`ads_optimizer_goal`** — Goal por campaña o por plataforma (`scope`, con
CHECK de coherencia y únicos parciales: un goal por campaña, uno por
plataforma). Trigger `goal_scope_campana_real` (`BEFORE INSERT/UPDATE`):
un goal de campaña tiene que apuntar a una entidad que ES `kind='campaign'`
(la FK sola no lo garantiza). `target_acos_pct`, `bid_floor`/`bid_ceiling`
(NOT NULL sin DEFAULT desde 0003; defaults solo en `DEFAULTS_POR_MONEDA`
de app/optimizer/goals.py, por moneda; `floor <= ceiling`), config de harvest
(`harvest_campaign_id`, `harvest_ad_group_id`, `harvest_default_bid` — cuya
moneda es `bid_currency` del mismo goal) **nullable con CHECK de
completitud**: falta config → la decisión HARVEST se salta con motivo, nunca
placeholder. `enabled` y `mode` (`off`/`shadow`/`live`, default `off`): la
elegibilidad dura del ciclo. **Precedencia campaña > plataforma resuelta en la
app**. Mutable a propósito: lo que una decisión usó queda congelado en
`decision.inputs` + `config_version`. **La escribe `app_admin`** (el endpoint
`/goals` corre como `app_admin`), no los motores: la escalera
off→shadow→live es decisión humana.
*Cómo se audita*: los cambios de goal se reconstruyen desde
`config_version`/`decision.inputs`; invariante — goals `live` sin
floor/ceiling razonables.

**`ads_optimizer_lock`** — Claim de ciclo: `job_key` PK, `owner`,
`claimed_at`, `heartbeat_at`, `ttl_seconds` (default 1800 = 30 min). Cron y
`/run` comparten `job_key`; la expiración por TTL la evalúa la app al tomar el
claim.
*Cómo se audita*: un lock con heartbeat vencido y ciclo sin cerrar = ciclo
muerto, reclamable; queda rastro en `optimizer_cycle`.

**`ad_entity_state`** — Cache MUTABLE del estado actual en Amazon (bid,
moneda, status, targeting_type, `acos_target` publicado, `synced_at`).
Reemplaza `bid_cache` + `ad_campaigns` del viejo. Excepción deliberada al
append-only: es estado, no historia (la historia de lo que el sistema creyó
vive en `decision.inputs`).
*Cómo se audita*: guarda de frescura del ciclo — plataforma saltada si
`synced_at > 48h` (ciclo `degraded` + `notes`); bid con moneda obligatoria por
CHECK.

**`harvest_job`** — Tracking de harvest por fases con orden sellado:
**`pending` → `negative_created` → `exact_created` → `done` / `failed`**. La
fila se registra en `pending` **antes del primer POST** — y **la base lo
exige**: el trigger `harvest_job_decision_coherente` rechaza todo INSERT que
no nazca en `pending` (fail-closed ante crash: un crash no deja ventana de
duplicación porque el único en-vuelo ya la bloquea; las fases posteriores solo
se alcanzan por UPDATE). **`decision_id` NOT
NULL** con el mismo trigger: el job debe corresponder
a una decisión `kind='harvest'` sobre la misma (entidad, término, plataforma
vía `ad_entity`) — un typo en la app no puede crear un job huérfano que la
reconciliación perseguiría contra Amazon en vano. **Único parcial
`(platform, ad_entity_id, search_term) WHERE fase IN ('pending',
'negative_created','exact_created')`**: un solo job en vuelo por término —
protege el POST no idempotente; los jobs cerrados no bloquean. La
**reconciliación la hace la app al inicio del ciclo siguiente** contra
`/sp/keywords/list` (regla 10).
*Cómo se audita*: invariante — jobs en fase intermedia viejos (ni done ni
failed) entran a reconciliación.

**`apply_quota_state`** — Caps diarios por motor: PK `(motor, quota_date)`,
`used`/`cap`. Live automático CON TOPES; el consumo es atómico en la app
(`INSERT … ON CONFLICT … DO UPDATE … WHERE used < cap`). El **CHECK
`quota_no_excedida` (`used <= cap`)** es un backstop que hace cumplir que el
**orden importe en la app**: consumir SIEMPRE con `WHERE used < cap`, nunca un
UPDATE ciego. **El motor solo puede `UPDATE (used)`: el cap no se puede subir
DESPUÉS de creada la fila.** "`used` nunca decrece" NO es enforceable por
CHECK (compararía contra el valor viejo del UPDATE): queda cubierto por el
patrón de consumo atómico + la auditoría de `decision` (cada apply tiene su
fila append-only). La verdad completa, declarada: la fila del día la inserta
el propio motor copiando el cap desde `config_version` (que escribe
`app_admin`), y `app_admin` también tiene `INSERT` para fijar caps manualmente
— la integridad del valor inicial del cap descansa en la config administrada
por humanos, no en un permiso. **La reserva para PAUSE la maneja la app** (un
cap lleno nunca deja una hemorragia sin pausar).
*Cómo se audita*: caps bajos el día 1 del cutover; `used > cap` es imposible
si la app consume bien, y su sola aparición es señal de bug grave.

### Módulo apply (ORBIT 04 — migración 0002, AÚN NO APLICADA)

> Las tablas y sellos siguientes viven en `migrations/0002_apply.sql`
> (tarea 1.2 de `plans/orbit-04.md`); **4.1 la aplica en goncloud**. Hasta
> entonces esta sección es diseño sellado, no estado del schema vivo. El
> contrato fino (máquinas de estado, matriz de reconciliación, quota,
> cutover) está en `docs/APPLY.md`.

**`apply_queue`** — Cola de cortes (pause/negative/harvest) del módulo
apply; los bids NO van a la cola (aplican en su ciclo). Máquina de estados
sellada por trigger: `pending_veto → vetoed|released`;
`released → vetoed|applying`; `applying → applied|failed`;
`pending_veto/released → discarded`; terminales `vetoed/applied/failed/
discarded` (no existe `applying → discarded`). **Nace `pending_veto` por
trigger de INSERT** (un INSERT directo en `released` salta la ventana de
veto y revienta); transiciones por trigger de UPDATE, atómicas en la app
(`UPDATE … WHERE estado=…`). **Clave de efecto `(platform, ad_entity_id,
familia, search_term)`** con familia `entity_cut` (pause, término NULL) /
`term_cut` (negative Y harvest — con `kind` en la clave un veto de negative
se eludía proponiendo harvest del mismo término; `kind` queda como dato
auditable). **Único parcial en-vuelo sobre no terminales con
`NULLS NOT DISTINCT`** (sin él, dos pause de la misma entidad no
chocarían). **`vetoed` exige admin**: la transición a `vetoed` valida
`current_user` por trigger — el rol del motor no puede vetar con el UPDATE
que usa para el claim. Ventana 48h (`vence_el`, el reloj no se detiene por
infra), veto durable 30d editable al vetar (`vetoed_at`/`vetoed_by`),
shadow-mark (`modo='shadow'`: una fila shadow jamás se libera — candado del
trigger de transiciones: su perímetro es `vetoed`|`discarded`),
`request_payload` con lo que se va a mandar. La encola la APP (necesita el
modo efectivo por decisión), con invariante testeado: toda decisión de
corte del ciclo tiene fila en cola o skip registrado.
*Cómo se audita*: invariante — cortes del ciclo sin fila ni skip; filas no
terminales con `vence_el` vencido (el liberador las toma FIFO); vetos
vigentes por clave de efecto (los consulta el skip del ciclo).

**`apply_attempt`** — Ledger de intentos de TODA mutación (bid, corte,
reversa, probe): `decision_id` (NULL solo para probes), `seq` (tope de
reintentos = 3, "no existe 4º intento" es un COUNT), `tipo`
(normal/reversa/probe), `request_payload` EXACTO (en harvest, el bid
efectivo a escribir), `quota_cobrada`, `started_at`. **Nace ANTES del
HTTP** (la intención durable) y el ack/resultado/`finished_at` se sellan
al volver **UNA vez**: trigger acotado por columnas patrón
`sku_cost_solo_cierra_vigencia` — solo la transición NULL→valor de esas
columnas pasa; todo otro UPDATE/DELETE revienta. **Excepción deliberada**
declarada en los invariantes de `tests/test_schema.py` (mismo trato que
`decision_application` en 0001).
*Cómo se audita*: invariante — intentos sin sello viejos entran a
reconciliación; COUNT por decisión ≤ 3.

**`reactivacion_manual`** — Detección de reactivación manual del dueño:
`ad_entity_id` PK, `detectada_en`. La escribe el APLICADOR (no el sync): el
re-check por GET fresco del apply detecta "pause verificado propio + estado
vivo ENABLED" y marca el instante si no existe (INSERT idempotente por PK).
**Gracia de 7d desde `detectada_en`**: durante la gracia el motor no vuelve
a cortar esa entidad. Solo el caso detectable (entidades nunca tocadas por
el motor quedan invisibles — residual declarado del header). Grants
INSERT/SELECT solo a `app_decide`. `structure.py` no se toca.
*Cómo se audita*: invariante — cortes a entidades dentro de su gracia.

**Sellos de `apply_quota_state` (0002)** — La fila del día `(motor,
quota_date)` **solo nace desde `config_version` vigente** (trigger: copia
el cap de la clave `ads_apply_cap_<platform>_<kind>` mapeada 1:1 al
`motor` `ads_optimizer:<platform>:<kind>`, vocabulario cerrado
bid/pause/negative/harvest); **sin clave no nace fila → cero applies**
(fail-closed, visible en Salud — no se disfraza de rampa sana). **`used`
creciente** por trigger (en 0001 no era enforceable). **`quota_date` =
día UTC de la base** (`(now() AT TIME ZONE 'UTC')::date` en la expresión —
`CURRENT_DATE` se evalúa por sesión y duplicaba el cap con TZ distinta).
Unidad = operación lógica (harvest = 1 aunque sean 2 HTTPs); 429 reintenta
sin recobrar; reversas exentas; cap agotado: cortes FIFO esperan (siguen
vetables), bids se descartan con conteo en el digest.
*Cómo se audita*: fila del día sin clave vigente = fail-closed activo
(alerta, no rampa); `used > cap` imposible por CHECK + trigger.

**`decision_application.applied_cycle_id` (0002)** — Columna nueva al
RESUMEN por decisión: el ciclo (modo live) que **EJECUTÓ** el apply, sellado
**AL CONFIRMAR** (jamás pre-HTTP: un crash no cuenta como applied). El
cooldown de `goals.py` pasa a mirarla (test punta a punta del caso
decisión-shadow-aplicada-en-live); `applied_count` cuadra por ciclo
ejecutor. Se agrega al GRANT de columnas de UPDATE de `app_decide`.

**GRANTs nuevos (0002)** — Positivos y completos (sellado 24): USAGE de
las secuencias IDENTITY nuevas; UPDATE acotado de `ad_entity_state` a
`app_decide` (current_bid/status/synced_at — el cache se actualiza CON el
readback, con lo LEÍDO); INSERT/SELECT de `reactivacion_manual` a
`app_decide`; `applied_cycle_id` en el GRANT de columnas de
`decision_application`; INSERT/UPDATE acotados de `apply_queue` y
INSERT + sello de `apply_attempt` a `app_decide`; la transición a `vetoed`
solo `app_admin` (el trigger valida `current_user`). `tests/test_schema.py`
amplía su parser a 0002: los invariantes transversales (FK con índice de
apoyo, sin float para dinero, CHECKs sin TimeZone de sesión) cubren las
tablas nuevas; los candados propios de 0002 (máquina de estados, perímetro
shadow, sello del ledger, sellos de quota, GRANTs por columna) tienen sus
tests dedicados en `tests/test_schema.py` (estáticos) y
`tests/test_apply_schema.py` (integración).

## Vistas y funciones (lo que la app consume; nadie lee tablas crudas)

Regímenes de lectura, explícitos (reemplazan al "todo lee lo maduro"):

- **Bids** leen **`v_metric_latest`** (última observación por entidad/fecha)
  con la guarda de frescura del spec: la ventana termina en
  `max(metric_date) − 3d`.
- **Cortes** (`pause`/`negative`/`harvest`) exigen madurez **D−10**: la base lo
  rechaza por trigger si `window_end` es más reciente.
- **`v_metric_mature`** (D−15, costo cerrado) es para **análisis económico y
  TACoS**, no para el ciclo: el día en curso tiene ~20% del costo y ~12% del
  revenue finales, y el costo madura hacia abajo por clawback — un ACoS de
  día 1 sale ~1.5× peor que el real. El corte usa **UTC fijado en la
  expresión** (`(now() AT TIME ZONE 'UTC')::date − 15`), no `CURRENT_DATE`
  (que se evaluaría según la TimeZone de cada sesión — la misma defensa que
  el trigger `decision_madurez_corte`).
- **`metrics_as_of(ts)`** — lo que el sistema PODÍA VER en ese instante:
  backtest honesto (sin esto todo backtest se auto-engaña y sale espectacular).
- **`v_margen_plataforma`** — **margen de contribución** por **plataforma Y
  moneda**: `venta`, `cargos_con_orden`, `cargos_sin_orden`, **`cogs_conocido`**
  (costo vigente a `event_date` × `quantity`, solo ventas con producto y costo
  vigente, convertido a la moneda de la venta con la tasa de ese día; sin tasa
  utilizable ese costo NO entra y baja la cobertura), **`cobertura_cogs_pct`**
  (ventas con costo conocido / ventas totales) y **`margen_contribucion`**,
  calculado **SOLO con cobertura 100% — NULL en caso contrario, con la
  cobertura visible al lado**. `venta` es la **VENTA TOTAL**: todas las
  ventas, con o sin `order_id`, sin supuesto de atribución — la separación
  atribuible/no atribuible se aplica a los **cargos**
  (`cargos_con_orden`/`cargos_sin_orden`), no a la venta. La idea: nunca un
  margen que esconde huecos — el error histórico es el 49% del COGS de MeLi
  en 0 disfrazado de dato que pasó tres auditorías como "limpio". FULL OUTER
  JOIN entre los tres agregados: una plataforma/moneda con solo cargos sin
  orden (o solo ventas) también aparece. **Sin dimensión temporal a
  propósito**: es historia completa; el análisis por período filtra
  `event_date` en la query contra `ledger_event`, no contra esta vista.
  OJO: `cargos_sin_orden = 0` puede significar **"no llegó"** (el ISR no se
  ingirió), no "no hubo" — sin fuente externa la ausencia es indistinguible
  del cero; lo atrapa `external_reconciliation`, no esta vista.
- **`v_tacos`** — **por plataforma** (no por moneda: amazon_us gasta en USD
  pero vende en MXN, y amazon_mx + meli comparten MXN): gasto desde
  `ads_metric_observation` vía `ad_entity.platform` **al grano
  keyword + product_target desde 0005** (las métricas guardan el mismo costo
  también en la fila `campaign`; sumar ambos granos duplicaba el gasto ~2x —
  bug confirmado en vivo 2026-08-31), venta desde `ledger_event`
  por plataforma. **Ventanas simétricas**: ambos lados cortan en **D−15 UTC**
  (el gasto vía `v_metric_mature`; la venta con la misma expresión
  `(now() AT TIME ZONE 'UTC')::date − 15`) — antes la venta tomaba todo el
  mes y el mes en curso salía con TACoS sistemáticamente bajo (optimista:
  "todo se ve rentable"). **Sin supuesto de moneda única**: cada fila se
  convierte a la canónica **MXN** con `fx_resolve` y el JOIN es por
  `(platform, mes)` sobre montos ya en MXN — si un mes tuviera dos monedas de
  venta, ambas se convierten y se SUMAN (el gasto no se repite por fila).
  **Fail-loud ante huecos de FX**: la fila sin tasa utilizable NO entra al
  agregado y se cuenta en `filas_gasto_sin_tasa` / `filas_venta_sin_tasa`;
  con una sola fila sin convertir (o si falta cualquier lado entero),
  `tacos_pct` es **NULL — nunca un agregado parcial disfrazado de completo**.
  **Y lo mismo ante el costo ausente**: `SUM` tampoco suma los NULL, así que
  una fila de métrica con `cost` NULL dejaba el gasto corto y `tacos_pct`
  optimista sin dejar señal; ahora se cuenta en `filas_gasto_sin_costo` y
  anula `tacos_pct` igual. (El lado de la venta no lo necesita:
  `ledger_event.amount` es NOT NULL.) TACoS por
  plataforma es la métrica que no necesita suposición de atribución (meta
  declarada: 8–12%).
- **`fx_resolve`** — ver sección `fx_rate`.

## Roles y candados

- **`app_ingest`** — sincronizadores: INSERT en hechos y catálogo (incluye
  `external_reconciliation`); UPDATE genérico solo en cache (`ad_entity_state`)
  y catálogo (`product`, `listing`); `ad_entity` **solo por columnas
  (`name`, `listing_id`)** — `platform`/`kind`/`external_id`/`parent_id`/
  `match_type`/`keyword_text` son inmutables por permisos (mutarlos rompería
  el sello de moneda y los goals); cierres por
  columna: `ingest_run` (`finished_at`, `rows_written`, `rows_skipped`,
  `skip_reason`, `ok`) y `sku_cost` (**solo `valid_to`** — cerrar vigencias
  sí, reescribir importes jamás).
- **`app_decide`** — motores: INSERT en `decision`, `decision_application`,
  envelope/quota/harvest; UPDATE en `decision_application` (readback) y
  `harvest_job`; cierre de `optimizer_cycle` **por columna**; en
  `apply_quota_state` **solo `UPDATE (used)`** — el cap lo fija el INSERT, el
  motor no puede subirse el tope a sí mismo; DML completo en
  `ads_optimizer_lock`. **No** escribe goals ni `config_version` (conserva
  SELECT).
- **`app_admin`** (NOLOGIN) — config humana: escribe `ads_optimizer_goal`,
  inserta `config_version` y `apply_quota_state` (fijar caps manualmente es
  decisión de admin, no del motor) — escalera off→shadow→live. El endpoint
  `/goals` corre como `app_admin`.
- **`app_read`** — dashboard/análisis: SELECT.
- Los permisos solos no bastan (un `GRANT ALL` futuro los derrotaría en
  silencio): el candado real es el trigger `prohibir_mutacion`, que bloquea
  UPDATE/DELETE en las siete tablas append-only **aunque el rol tenga
  permiso** — y `sku_cost_solo_cierra_vigencia`, que hace lo propio con la
  única mutación acotada del esquema. Lo que sigue descansando sólo en el
  GRANT por columna, y queda declarado: la identidad inmutable de `ad_entity`,
  los cierres por columna de `ingest_run`/`optimizer_cycle` y el `cap` de
  `apply_quota_state`.
- `REVOKE ALL … FROM PUBLIC` + default privileges de solo lectura: ningún rol
  futuro hereda escritura por accidente.
