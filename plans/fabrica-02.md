# FABRICA 02 — Harvest por grupo (F2): reruteo, negativos cruzados y biblioteca viva

Version: 1.1, 2026-09-10 UTC. Estado: **PLAN (no implementado)**, tras revisión
de cinco perspectivas independientes (producto, arquitectura, seguridad, QA,
escéptico) sobre el borrador v0.1: 4 críticos y 9 mayores incorporados, el
resto declarado en "Divergencias y residuales". **Decisiones 1–3 del dueño
cerradas el 2026-09-10** (solo palabras en la biblioteca; caps bajados al
arranque; implementa GLM).
Base: `origin/master` `3b4a807`. Rama: `plan/fabrica-02`.
Precedencia: `docs/CONTEXTO.md` (reglas 1–10) > `plans/ROADMAP.md` >
`docs/superpowers/specs/2026-09-05-fabrica-campanas-grupos-design.md` §6–§7
(contrato de producto de F2, sellado con F1) > `docs/APPLY.md` (manda para el
módulo apply) > este plan.
Antecedente: `plans/fabrica-01.md` (F1, tareas 1–10 `完了`; tarea 11 espera al
2026-09-19 por la regla 6). Tracker: AUTO-02 (`plans/ROADMAP.md:125`);
**secuencia decidida por el dueño 2026-09-09: después de SP-API**, que cerró el
2026-09-10.
No hay fechas ni presupuestos en este plan. Ningún secreto en el repo ni en el plan.
`team_validation_mode: subagent`.

## Resultado y límites

Propósito: que un grupo de campañas nacido de la fábrica **aprenda solo**. Hoy,
cuando un término demuestra que vende, el harvest lo pasa a la campaña exacta
(F1 dejó el destino sembrado en el goal) — pero las hermanas (auto, phrase,
broad) siguen compitiendo por ese mismo término, y la biblioteca del tipo de
producto no se entera. El dueño lo resolvió a mano el 2026-09-09 (12 negativos
cruzados por el camino sellado). F2 hace eso solo, con ledger, readback y
reversa, y ata el destino del harvest al grupo y no a una siembra manual.

Dentro (v1, fijo; ampliar solo con el dueño):

- **Destino del harvest por grupo** (spec §7.1–7.3): campaña en grupo → ad group
  de la `category_exact` DE ESE GRUPO por SQL sobre `campana_grupo_rol`; campaña
  sin grupo → `harvest_excepcion`; **mientras no esté migrada, la terna vigente
  del goal de campaña sigue valiendo con motivo visible `migracion_pendiente`**
  (compatibilidad real, no prometida); sin nada → skip visible. Jamás por
  nombre, jamás placeholder.
- **Fase `hermanas_negadas`** en el job de harvest (spec §7): negative-exact del
  MISMO término en las hermanas del grupo, con ledger pre-HTTP por hermana,
  readback, **1 unidad de quota por harvest**, y reversa en orden keyword →
  hermanos → origen (regla 7). **La decisión se confirma en el readback de la
  keyword** (el evento de valor); las hermanas son higiene posterior,
  reintentable por ciclo, que jamás convierte un harvest exitoso en `failed`.
- **Biblioteca escrita por el motor** (0018 §6): `keyword_biblioteca` recibe el
  término de cada harvest **aplicado y leído de vuelta** (decisión del dueño
  pendiente sobre si guarda dinero, ver "Decisiones del dueño");
  `negative_biblioteca` recibe SOLO negativos de decisiones `kind = negative`
  (término que no vende). **Los negativos que nacen de un harvest —origen y
  hermanas— son ruteo, no exclusión, y jamás entran** (si entraran, el término
  ganador nacería negado en el siguiente grupo del mismo tipo de producto).
- **Migración de existentes a `harvest_excepcion`** (decisión 4): herramienta
  con `--go` literal por campaña, destino validado contra `ad_entity`
  (parentesco y plataforma), congeladas; y limpieza de la terna transitoria
  grupo por grupo con su propio go (spec §7), por `goals_write`.
- **Reversa implementada y ensayada con ids reales antes de encender** (regla 7).

Fuera (declarado):

- Nuevos `kind` en `apply_queue` (`migrations/0002_apply.sql:33-39`):
  `hermanas_negadas` es una FASE del job de harvest, no una cola nueva.
- Cambiar umbrales de harvest/negative, la madurez ≥10d (regla 6) o la ventana
  de veto de 48h.
- La pantalla `/cortes` y su botón único «vetar» (causa del veto por error del
  2026-09-04): es `cortes-ui-01` 1.2, ya planeada, pendiente del visto bueno
  del dueño. **Precondición de D.3**, no alcance de F2.
- Lotes v2 / publicaciones de ORBIT 19 (`campana_grupo.target_origen`, 0019):
  no se tocan; el número de migración de F2 va después.
- Encender `live` los goals del grupo `kit_arras`: decisión del dueño (D.3).

## Decisiones ya cerradas (no se reabren)

| # | Decisión | Fuente |
|---|---|---|
| 4 | Existentes sin grupo se migran una a una a `harvest_excepcion` con go literal, congeladas | spec §7.2; `migrations/0018:224-234` |
| 6 | Biblioteca acumulativa por `(tipo_producto, platform)`; F2 la alimenta desde aplicados | `migrations/0018:180-219` (su forma de guardar dinero: ver "Decisiones del dueño" 1) |
| 10 | Negativo cruzado en las hermanas; si Amazon rechaza negative keywords en el ad group de product targeting, quedan 3 y la hermana se declara skip con motivo EN EL JOB | spec §7 último párrafo |
| 12 | La terna `goal.harvest_*` de F1 es transitoria; `harvest_default_bid` sigue fijando el monto | spec §7 |
| — | Veto 48h, `pending_veto` al nacer, shadow jamás aplica; ledger pre-HTTP para toda mutación; `TOPE_INTENTOS = 3` sin 4º intento | `docs/CONTEXTO.md:319-336`; `0002_apply.sql:157-172, 206-215, 297-325` |
| — | Madurez ≥10d sin cambio | `docs/CONTEXTO.md:164-167`; spec §7 |
| — | F2 después de SP-API | ROADMAP:87-90 (dueño 2026-09-09) |

## Hechos verificados que condicionan el plan (2026-09-10)

1. **F1 ya sembró el destino en los cinco goals** del grupo 1 (`kit_arras |
   Personalizado`): `harvest_campaign_id = 145787501515469` (la exacta),
   `harvest_ad_group_id = 182421284463033`, bid 11.62, `mode = shadow` (leído de
   producción como `orbit_read`). Hoy el destino sale del goal en cuatro sitios:
   `app/cycle.py:1149-1167` (`_config_harvest_de`, que además devuelve el
   dedupe `keywords_campana_destino`), `app/apply_harvest.py:1129-1141`
   (re-validación pre-claim), `app/optimizer/replay.py:129-139` (congelado) y
   **`app/apply_harvest.py:637-665` (`_contexto`, que lee el goal FRESCO al
   POSTear)**. Congelar el destino sin tocar `_contexto` no cambia dónde se
   escribe.
2. **La campaña exacta se harvestea a sí misma** (origen = destino). Hoy lo
   absorbe el dedupe (`harvest_duplicado`). F2 lo declara: rol `category_exact`
   como origen → skip `origen_es_destino` (motivo nuevo del vocabulario cerrado
   de `app/optimizer/hygiene.py:141-155`).
3. **El harvest automático nunca completó un ciclo real.** El único (2026-09-04,
   `apply_queue` 7) lo vetó el dueño por error de interfaz; el veto es terminal
   hasta 2026-10-04 y la keyword se creó a mano (`plans/orbit-05.md:124`). F2
   construye sobre un mecanismo probado en tests y sonda, no en producción.
4. **`TOPE_INTENTOS = 3` por decisión** (`app/apply.py:135, 509-511, 787-789`,
   cuenta `apply_attempt.tipo = 'normal'`). Un harvest ya gasta 2 (negativo de
   origen `apply_harvest.py:898`, keyword `:996`). Las hermanas **no caben** en
   ese presupuesto: necesitan su propio `tipo` en `attempt_tipo_valido`
   (`0002_apply.sql:297-299`).
5. **Las fases "en vuelo" están escritas en seis lugares**: índice parcial
   `harvest_job_en_vuelo` (`0001_initial.sql:994-996`), `_SQL_JOB_EXISTENTE`
   (`apply_harvest.py:145`), `_SQL_JOBS_EN_VUELO` (`:152`),
   `_SQL_JOB_EN_VUELO_DE` (`:255`), `_continua_job` (`:1064-1072`) y el CHECK
   de `harvest_job.fase` (`0001:981-983`) + trigger `harvest_job_sella_fases`
   (`0002:602-635`). Un job en una fase que falte en cualquiera queda zombi.
6. **`app_decide` no puede escribir la biblioteca**: 0018 da `INSERT, UPDATE`
   solo a `app_admin` (`0018:446-451`) y el `USAGE` de las secuencias
   `keyword_biblioteca_id_seq` / `negative_biblioteca_id_seq` (PK `BIGSERIAL`,
   `0018:188, 209`) también solo a `app_admin` (`0018:452-453`). Un `GRANT
   INSERT` sin `USAGE` revienta en el primer harvest real con «permission
   denied for sequence» — la misma clase del bug 0033→0034.
7. **El pre-check LIST trunca en silencio**: `_lista_todos`
   (`apply_harvest.py:443-462`) lista todo el perfil con
   `TOPE_PAGINAS_LIST = 20` y devuelve lo leído si aún hay `nextToken`. MX ya
   tiene ~2 600 negativos (26 páginas de 100). Un pre-check por perfil entero
   diría «no existe» a un negativo que sí existe.
8. **Los 12 negativos manuales del 2026-09-09 no pueden chocar con F2**: son las
   4 exactas sembradas, que viven en la exacta → cualquier término igual es
   `harvest_duplicado` y jamás genera job. El riesgo real de duplicar un negativo
   es reintento tras caída y LIST truncado (hecho 7), no los 12.
9. **`resuelve_goal` cae al goal de plataforma** cuando la campaña no tiene goal
   propio (`app/optimizer/goals.py:175-180`), y el goal 4 de MX trae terna a la
   campaña manual. Cualquier comparación «terna vs grupo» debe mirar solo
   `scope = campaign`.
10. **`harvest_excepcion` acepta texto libre** (`0018:224-234`): sin FK, sin
    plataforma, sin parentesco ad group ↔ campaña. Es el único camino real de
    postear fuera del perímetro.
11. **La reversa completa no tiene puerta de entrada**: `reversa_harvest_completo`
    (`apply_harvest.py:794-806`) no la llama nadie en `app/` y su firma es
    `(negative_id, keyword_id)`.
12. **`apply_harvest.py` tiene 1 437 líneas** y está en la allowlist de
    `tests/test_architecture.py:62-71` como «candidato DECLARADO a partirse la
    próxima vez que se toque en grande: reconciliación vs ejecución». A.3 es
    tocar en grande.

## Diseño (lo que el implementador no decide)

**Resolutor único** — `app/optimizer/harvest_destino.py` (nuevo, patrón de
`hygiene.py`: `psycopg` solo bajo `TYPE_CHECKING`, decisión pura testeable sin
base, SQL en UNA función lectora; `tests/test_architecture.py` verde).
`resolver_destino(conn, platform, campaign_ad_entity_id) -> DestinoHarvest |
Skip(motivo)`. El parámetro es la **campaña** (`campana_grupo_rol.ad_entity_id`
y `harvest_excepcion.ad_entity_id` son campañas; `decision.ad_entity_id` y
`harvest_job.ad_entity_id` son el ad group — cada caller resuelve el padre, como
hace `_SQL_PADRE` en `apply_harvest.py:217-219`). Orden:

1. `campana_grupo_rol` por campaña → si rol `category_exact` →
   `Skip("origen_es_destino")`; si no → ad group y campaña de la hermana
   `category_exact` del mismo `grupo_id`, **con `ad_entity.platform = platform`
   asertado** (no hay candado de plataforma en `campana_grupo_rol`).
   `resuelto_por = "grupo"`.
2. `harvest_excepcion` por campaña → `resuelto_por = "excepcion"`.
3. Terna del goal **de scope `campaign`** (nunca la de plataforma) →
   `resuelto_por = "terna"` con motivo informativo `migracion_pendiente`
   (visible en `/salud`; se retira en D.2).
4. `Skip("sin_destino_de_harvest")`.

Si la campaña está en grupo Y su goal de scope `campaign` trae terna distinta
de la que resuelve el grupo → `Skip("destino_inconsistente")` + aviso (A.6).
Nunca «gana uno» en silencio.

**Congelado y dedupe** — `_goal_json` (`app/cycle.py:643-673`) recibe el destino
resuelto y congela `inputs.goal.harvest = {campaign_id, ad_group_id,
default_bid, moneda, resuelto_por, grupo_id|null}`; la bandera `completa` se
deriva del destino resuelto, **no** de la terna del goal (si no, tras D.2
congelaría `null` y `replay_coincide` fallaría en todo harvest posterior). El
dedupe `keywords_campana_destino` se apunta al `campaign_id` resuelto (segundo
elemento de `_config_harvest_de`, `cycle.py:1167`, y `apply_harvest.py:1141`):
sin esto un término cosechado se re-propone cada día durante la ventana de
~90 días. `harvest_default_bid` sigue saliendo del goal. Replay lee el
congelado. **Apply (`_contexto`) lee el congelado y lo re-valida antes del
POST**: si `resuelto_por = grupo` y la exacta vigente del grupo ya no es la
congelada → descarte con motivo `destino_desincronizado`; y por LIST, que el ad
group destino pertenece a esa campaña y a esa plataforma. Jamás re-rutear ni
postear a un congelado que ya no es hermana.

**Fase `hermanas_negadas`** (`app/apply_harvest.py`):

- Orden hacia adelante inamovible: negativo de origen → keyword exacta →
  readback de la keyword → **ahí se sella la decisión** (`_confirma_resumen`,
  `verify_ok`, cola `applied`, cooldown) → recién entonces hermanas. Un fallo
  de la keyword = cero HTTP a hermanas (si no, el término queda negado en
  cuatro campañas y vivo en ninguna).
- Hermanas = roles del grupo **menos `category_exact` menos el rol de origen**
  (su negativo ya existe: prueba `external_ids["negative_id"]`, no el LIST).
  **`product_targeting` ENTRA: la sonda 0.1 (2026-09-12) confirmó que Amazon
  acepta negative keywords por texto en su ad group** (207 con id, visible en
  el LIST por identidad), así que por la regla del spec §7 la decisión 10 queda
  en **4 hermanas**. El motivo `pt_no_acepta_negative_keyword` NO desaparece:
  deja de ser una decisión de diseño y pasa a ser el camino de fallo en vivo —
  si un POST a una hermana se rechaza, esa hermana queda pendiente con su
  motivo en `external_ids["hermanas"]` y el job sigue (nunca tumba el harvest).
  Residual declarado en E/0.1: aceptado ≠ efectivo — un negativo por texto en
  un ad group que targetea ASINs puede ser inerte; la sonda respondió el
  contrato de la API, no el efecto. Si algún día se mide y resulta inerte,
  sacar PT de las hermanas es un cambio de una línea.
- **Un solo LIST por job**, filtrado por los ad groups de las hermanas
  (`adGroupIdFilter`), con el criterio de tres ejes de `_solo_en_otro_ad_group`
  (ad group + `matchType` + estado, ignorando `ARCHIVED`,
  `apply_harvest.py:465-486`). LIST truncado (aún hay `nextToken` en el tope) =
  **fail-closed**: no se POSTea, la hermana queda pendiente con motivo
  `list_truncado`; nunca «no existe».
- Por hermana: fila `apply_attempt` **`tipo = 'hermana'`** (nuevo en
  `attempt_tipo_valido`; tope propio `TOPE_INTENTOS_HERMANA = 3` por
  `(decision_id, ad_group)`; `quota_cobrada = false`) → POST
  `crear_negative_exacto` → readback → id en `external_ids["hermanas"][rol]`.
  `_avanza` hace merge superficial y descarta `None` (`:673-684`): se pasa el
  dict completo de `hermanas` en cada avance.
- Fallo en una hermana (≥400, ack sin id, tope): la hermana queda **pendiente**
  con motivo; el job sigue en `hermanas_negadas`; la reconciliación de cada
  ciclo reintenta las pendientes (idempotente por el LIST filtrado) **sin
  re-cobrar quota** (la fila de cola ya está `applied`). Tras
  `TOPE_CICLOS_HERMANAS` ciclos → `done` con `external_ids["hermanas_pendientes"]`
  y `AlertaHarvest`. **Jamás se revierte la keyword por una hermana.** El peor
  caso degrada al estado de hoy (hermanas compitiendo), nunca destruye valor ni
  deja el ledger mintiendo.
- Quota: la unidad la cobra el caller (`aplica_harvest`, `:1089`) y no se toca;
  el ledger completo del job es `[(1,'normal',cobrada), (2,'normal',no),
  (3..N,'hermana',no)]`. La migración actualiza el COMMENT de `apply_attempt`
  («2+N HTTPs por operación lógica») y `docs/DATABASE.md`.
- Reversa completa **operable**: `tools/reversa_harvest.py --job <id>` (patrón
  `--acepto-mutacion-real --esperado --huella --go`, cliente solo vía
  `apply._cliente_reversa`) borra keyword → hermanos → origen, una fila
  `tipo='reversa'` por borrado. Es la puerta de entrada que hoy no existe.
- Reanudación: `hermanas_negadas` entra en el índice parcial, en los tres SQL de
  «en vuelo» y en `_continua_job`. Solo pasan por la fase nueva las decisiones
  cuyo congelado trae `resuelto_por = grupo`; jobs viejos en `exact_created`
  siguen `exact_created → done`.

**Biblioteca** (contabilidad derivada; **jamás bloquea el sello**):

- Escritura en `SAVEPOINT` dentro de la transacción del sello (patrón CX2 de
  `_nace_job`, `:612`); si falla → `AlertaHarvest` + motivo, el sello sigue.
- `keyword_biblioteca`: al sellar `done` de un harvest **de grupo**, insert-si-no-
  existe por `(campana_grupo.tipo_producto, platform, texto normalizado con
  strip+casefold — el mismo del motor, `hygiene.py:161`)`, `origen =
  'grupo:<id>/campana:<campaign_ad_entity_id>/harvest:<job_id>'`,
  `first_seen_at`. Si ya existe: solo `updated_at` (y, si el dueño decide
  guardar dinero, la regla de la decisión 1). Sin grupo (excepción/terna) → no
  se escribe (regla 3: no hay `tipo_producto` que inventar).
- `negative_biblioteca`: SOLO desde la confirmación de decisiones `kind =
  negative` aplicadas (sitios reales: `app/apply_cola.py:758, 796` y
  `_reconcilia_negativas`, `apply_harvest.py:1272`), misma normalización y
  `origen`. Invariante con test: intersección vacía entre ambas bibliotecas por
  `(tipo_producto, platform, texto)`.
- Dinero: **no se escribe** (decisión del dueño 2026-09-10: solo palabras). Las
  columnas `orders/cost/revenue/moneda` quedan NULL en toda fila que escriba el
  motor; los números se calculan desde la fuente (`search_term_observation`)
  cuando hagan falta. Llenarlas algún día exige decisión nueva y las reglas de
  congelado, «último harvest gana» y prohibición de sumar ventanas solapadas.

**Migración `00NN`** (número al aplicar; hoy el siguiente libre es 0038; no
re-runnable, encabezado patrón 0033/0035; **no es puramente expansiva**: recrea
un índice y suelta un CHECK, ambos en la transacción — declararlo en el runbook):

- (a) `harvest_job.fase` admite `hermanas_negadas`; el trigger
  `harvest_job_sella_fases` admite **estructuralmente** `exact_created →
  hermanas_negadas`, `hermanas_negadas → done|failed` y conserva `exact_created
  → done` (jobs viejos). La obligación «harvest de grupo pasa por la fase» se
  cumple en la app con su test, no en plpgsql (un trigger que consulte la
  membresía viva deja jobs sin salida legal).
- (b) `DROP INDEX harvest_job_en_vuelo` + `CREATE UNIQUE INDEX ... WHERE fase IN
  ('pending','negative_created','exact_created','hermanas_negadas')` (sin
  `CONCURRENTLY`; la tabla es pequeña).
- (c) `attempt_tipo_valido` gana `'hermana'`; COMMENT de `apply_attempt`
  actualizado.
- (d) `ALTER TABLE ads_optimizer_goal DROP CONSTRAINT goal_harvest_completo` +
  trigger que admite `harvest_default_bid` solo (campaign/ad_group NULL)
  únicamente si la campaña está en `campana_grupo_rol`; y trigger simétrico en
  `campana_grupo_rol` que rechaza re-apuntar o sacar una campaña cuyo goal ya
  tiene terna NULL (sin él, `app_admin` puede dejar un goal sin destino en
  silencio).
- (e) GRANT a `app_decide`: `INSERT` en `keyword_biblioteca` y
  `negative_biblioteca`; **`USAGE ON SEQUENCE keyword_biblioteca_id_seq,
  negative_biblioteca_id_seq`**; `UPDATE (updated_at)` **únicamente** en
  `keyword_biblioteca` (decisión del dueño 2026-09-10: sin dinero; el statement
  del motor no toca otra columna). `harvest_excepcion` sigue solo `app_admin`.
- (f) `DO $$` con asserts positivos (el **statement literal del motor** bajo
  `SET ROLE app_decide`, fila leída de vuelta) y negativos (`DELETE` en
  bibliotecas, `UPDATE` en `harvest_excepcion`, `UPDATE` de `origen`/`texto`/
  `first_seen_at` → `InsufficientPrivilege`).
- (g) `tests/test_schema.py` (`PROGRESION_HARVEST`, `:1132-1139, 1359-1381`)
  parsea también `00NN`; `docs/APPLY.md` §6.1 (matriz de reconciliación) y §7
  (reversas), y `docs/DATABASE.md:345, 356`, ganan la fase y la unidad nueva.

**`goals_write`** — parámetro nuevo `harvest_limpia_destino=True` (NULL en
`harvest_campaign_id/ad_group_id`, bid intacto), validado **después** de leer la
fila (la campaña debe estar en `campana_grupo_rol`); `harvest_limpia` sigue
igual. El candado «escritor único de goals»
(`tests/test_architecture.py:347-391`) se extiende a `tools/`, que hoy queda
fuera del escaneo.

## Etapas y tareas

DoD = criterio binario. `E/<task>/` = `docs/evidencia/fabrica-02/<task>/`.
`pytest_focal` = `uv run --frozen python -m pytest -q <archivo>` **con
`ORBIT_TEST_DSN` apuntado: N passed, 0 skipped** (sin DSN los tests de base
skipean en verde fuera de CI, `tests/test_schema.py:1587`). Cada regresión se
demuestra fallando contra el código previo (regla 9). Un implementador por
fase; el lead revisa una ronda por bloque y **muta**; R.1 la toma un revisor que
no implementó y re-muta. La suite completa corre en CI sobre el PR.

### Fase 0 — Sondas (escrituras reversibles, con go)

| Task | Contenido | DoD | Depends | Status |
|---|---|---|---|---|
| 0.1 | [stage:verificacion] [lane:gate] [tdd:skip:sonda] **Sonda de hermanas con la herramienta sellada** `tools/smoke_apply.py` (ledger `tipo='probe'`, doble autorización, `termino_basura`; cliente solo vía `apply._cliente_reversa`; si hoy no puede fijar el ad group, se le agrega ese flag): con go literal del dueño, (i) negative keyword en el ad group **product targeting** del grupo 1 (`187855388248650`) → decisión 10: acepta sí/no; (ii) **ensayo de la reversa en orden con ids reales**: negativos en las 3 hermanas de keyword → `borrar_negative` hermanos en orden, readback. **Todo lo que la sonda cree se revierte en la misma corrida, incluido el negativo de PT si Amazon lo aceptó** (regla 7: cero rastro activo del probe). `borrar_negative` **archiva** (`app/ads/write.py:346-354`): la evidencia es readback `ARCHIVED` de cada id creado (PT incluido), y el DoD dice quién archiva a mano y en qué plazo si algún borrado falla. Únicas escrituras a Amazon antes del deploy. | E/0.1: ids de `apply_attempt` tipo `probe`, request/ack/readback sanitizados, veredicto binario «PT acepta: sí/no» que sella `HERMANAS_ROLES`, la secuencia de ids de la reversa, y readback `ARCHIVED` de **todos** los ids creados (cero negativos vivos del probe al cerrar) | — | cc:完了 [2026-09-12 (dueño, go literal en el momento): **VEREDICTO — PT ACEPTA negative keywords por texto: SÍ**, así que por la regla sellada del spec §7 la decisión 10 queda en **4 hermanas**. `http_create` 207 con `negativeKeywordId 45705293970881`, readback por identidad, `http_delete` 207 (archiva) y `readback_final` ausente: **neto cero**, `rc=0`. Ledger `apply_attempt` 154 (create) y 155 (delete), ambas `tipo=probe`, `decision_id` nulo, `quota_cobrada=false`, selladas con su ack; la 154 nació antes del HTTP. Ceremonia: `config_version` 17 (16 claves = 14 vigentes + 2; caps y `modo=live` intactos), token efímero de 32 chars por archivo, jamás por argv; cierre en `config_version` 18 sin las dos claves y contenedor limpio. **No hizo falta tocar la herramienta PARA ESTA SONDA**: la campaña sondeada (`70314694808265`) tiene exactamente un ad group, así que `primer_ad_group_de_campana` resolvió al correcto. **La conclusión NO se generaliza** (hallazgo CodeRabbit en el PR #255): una consulta de lectura mostró un solo ad group en las cinco campañas del grupo 1, pero eso es una foto de hoy y de UN grupo — `primer_ad_group_de_campana` toma el primero que coincide, así que con dos ad groups elegiría mal. **0.2 confirma la cardinalidad en todo el universo**; si aparece alguna campaña con más de uno, el flag de ad group vuelve al alcance de A.0/A.3. **Alcance reducido por decisión del dueño**: se corrió solo la mitad (i); la (ii) (reversa en orden con ids reales) se DIFIERE a A.3 (simulador, DoD (g)) y D.3 (en vivo con `tools/reversa_harvest.py`), porque la herramienta sellada crea y archiva en la misma corrida y el orden que se quiere ensayar es el de `reversa_harvest_completo`, que aún no existe. Residual declarado: aceptado ≠ efectivo (un negativo por texto en un ad group que targetea ASINs puede ser inerte; la sonda responde el contrato de la API, no el efecto). E/0.1] |
| 0.2 | [stage:verificacion] [lane:fast] [tdd:skip:lectura] **Inventario de existentes** como `orbit_read`: campañas con goal `enabled` de scope `campaign` y terna `harvest_*` que NO están en `campana_grupo_rol` (candidatas a `harvest_excepcion`, con su destino actual y si el ad group es hijo de esa campaña y de esa plataforma), y las que están en grupo (terna ↔ grupo consistente). Conteo de negativos por perfil (páginas que ocupa el LIST). Sin mutaciones. | E/0.2: tabla campaña → destino → ¿grupo? → ¿consistente? → ¿parentesco ok?; páginas de LIST por plataforma | — | cc:TODO |

### Fase A — Código y migración

| Task | Contenido | DoD | Depends | Status |
|---|---|---|---|---|
| A.0 | [stage:implementacion] [lane:gate] [tdd:required] **Banco de pruebas de F2**: fixture de Postgres unificado (`ORDEN` = 0001, 0002, 0003, 0004, 0013–0019, `00NN`; precedente `_ORDEN_DB` en `tests/test_evaluacion_catalogo.py:639-655`) + helpers de harvest de `tests/test_apply_harvest.py` (`_semilla`, `_handler_harvest`, `_aplicador`, `_encola_fila`) + `_semilla_grupo(conn)` (grupo, 5 roles, ad groups, goals) + handler de LIST que **honra `adGroupIdFilter`** y `nextToken` (para simular truncación) + flag de fallo **por hermana** (por `adGroupId`). Sin tests de comportamiento todavía. | El fixture levanta y siembra; un test humo por helper; `tests/test_architecture.py` verde | — | cc:TODO |
| A.1 | [stage:implementacion] [lane:gate] [tdd:required] **Resolutor de destino** (`app/optimizer/harvest_destino.py`) + cableado en los **cuatro** sitios (`_config_harvest_de` con su dedupe, re-validación pre-claim, replay, **`_contexto`**) + congelado con `resuelto_por` y `completa` derivada del destino + motivos `origen_es_destino`, `sin_destino_de_harvest`, `destino_inconsistente` (scope campaign), `destino_desincronizado`, `migracion_pendiente` en el vocabulario cerrado. | Rojo-primero: (a) en grupo, goal con terna NULL, fixture con una campaña **señuelo fuera del grupo llamada** `category_exact` → la decisión congela el `external_id` de la exacta del grupo y el POST viaja a ese `adGroupId` (mata «por nombre» y «fallback al goal»); (b) rol exact como origen → `motivo == "origen_es_destino"` y `!= harvest_duplicado`, sembrado donde el dedupe no aplica; (c) sin grupo con excepción → excepción; (c') sin grupo con terna scope campaign → destino de la terna y skip informativo `migracion_pendiente` contado; (c'') campaña sin goal propio, solo goal de plataforma con terna → NO es `destino_inconsistente`; (d) sin nada → `sin_destino_de_harvest`; (e) terna scope campaign distinta del grupo → `destino_inconsistente`, cero HTTP; (f) decidido con G1, se muta `campana_grupo_rol` antes de liberar → el POST **no** se emite, motivo `destino_desincronizado`; y con la terna limpiada después de decidir, apply y replay usan el congelado; (g) término ya en la exacta del grupo → `harvest_duplicado`, cero fila de cola (dedupe re-apuntado); (h) terna NULL y bid 11.62 → el POST lleva `bid` 11.62 clampeado; mutantes del lead mueren | A.0, 0.2 | cc:TODO |
| A.2 | [stage:implementacion] [lane:gate] [tdd:required] **Migración `00NN`** (a)–(g) del diseño. | Tests de esquema con rol real: transiciones válidas/inválidas incl. `exact_created → done` conservada; job sembrado en `hermanas_negadas` → un segundo job del mismo `(platform, ad_entity_id, search_term)` viola `harvest_job_en_vuelo`; constante de fases en vuelo cruzada contra `pg_index.indpred`; `tipo='hermana'` no consume ni amplía el presupuesto `normal`; trigger de goal y trigger simétrico de `campana_grupo_rol`; **bajo `SET ROLE app_decide` el statement literal del motor inserta y actualiza de verdad** en las dos bibliotecas (fila leída) y los negativos truenan (patrón `tests/test_apply_schema.py:709-745`, no catálogo); mutante `REVOKE USAGE` de secuencia truena la migración; `PROGRESION_HARVEST` parsea `00NN` | A.0 | cc:TODO |
| A.3a | [stage:implementacion] [lane:gate] [tdd:skip:refactor-sin-comportamiento] **Partir `app/apply_harvest.py`** en ejecución vs reconciliación (deuda declarada en `tests/test_architecture.py:62-71`), sin cambio de comportamiento. | La suite de `tests/test_apply_harvest.py` pasa idéntica antes y después (mismo conteo, 0 skipped); allowlist de tamaño actualizada con razón; `tests/test_architecture.py` verde | A.0 | cc:TODO |
| A.3 | [stage:implementacion] [lane:gate] [tdd:required] **Fase `hermanas_negadas`** completa según el diseño: sello de la decisión en el readback de la keyword; hermanas = grupo − exact − origen (las 4, con PT confirmada por 0.1; un rechazo en vivo deja esa hermana pendiente con motivo, jamás tumba el job); un LIST filtrado por job con criterio de tres ejes y fail-closed por truncación; ledger `tipo='hermana'` por hermana; reintento por ciclo sin quota; `TOPE_CICLOS_HERMANAS`; `done` con pendientes declaradas + `AlertaHarvest`; las cinco listas de fases en vuelo y `_continua_job`; `tools/reversa_harvest.py --job`. | Rojo-primero con `MockTransport` y el fixture de A.0: (a) 3 hermanas creadas → `external_ids` **exacto leído de la base** con los tres ids y la decisión ya confirmada (`verify_ok`, cola `applied`) antes del primer POST a hermanas; (b) orden hacia adelante: secuencia de requests = negativo origen → keyword → LIST readback → luego hermanas; `fallo_keyword_status=400` → **cero** HTTP a hermanas; (c) LIST sembrado con negativo `ENABLED` en h1, `ARCHIVED` en h2, `NEGATIVE_PHRASE` en h3, nada en h4 → 1 POST omitido con id registrado, 3 emitidos; bodies de LIST con `adGroupIdFilter` de las hermanas y **una sola** llamada por job; (d) LIST con `nextToken` en el tope → cero POST, hermanas pendientes `list_truncado`; (e) fallo en la 2ª hermana → keyword intacta, decisión confirmada, hermana 2 pendiente con motivo, job sigue en `hermanas_negadas`; ciclo siguiente la reintenta sin nueva fila de quota (`apply_quota_state.used` no cambia); tras `TOPE_CICLOS_HERMANAS` → `done` + `hermanas_pendientes` + alerta; (f) ledger completo y ordenado `[(1,'normal',True),(2,'normal',False),(3..N,'hermana',False)]` con `cap = 1` sembrado; (g) reversa por **secuencia de ids** `[keyword, h1, h2, h3, origen]` con una fila `tipo='reversa'` por borrado; (h) proceso caído con job en `hermanas_negadas` → `reconcilia_harvest` lo retoma, `_reconcilia_harvest_huerfanas` **no** cierra su fila, y un job nuevo del mismo término choca con el índice; (i) rol de origen `auto_discovery` → no se re-niega el origen; (j) PT rechazada en vivo → **hermana pendiente** con motivo `pt_no_acepta_negative_keyword` en `external_ids["hermanas"]`, el job sigue y la reconciliación la reintenta; al tope de ciclos cierra `done` con la pendiente declarada — **la misma semántica que cualquier otra hermana rechazada**, no un `skip` aparte (contradicción señalada por CodeRabbit en el PR #255); (k) harvest sin grupo (excepción/terna) → `exact_created → done` como hoy; (l) fila vetada o `shadow` → cero jobs (precedente `test_harvest_vetado_jamas_crea_harvest_job`); mutantes del lead mueren | A.2, A.3a, 0.1 | cc:TODO |
| A.4 | [stage:implementacion] [lane:gate] [tdd:required] **Biblioteca escrita por el motor** según el diseño (SAVEPOINT; solo términos de harvest de grupo; solo `kind=negative` a negativos; normalización; sin dinero salvo decisión 1). | Rojo-primero: (a) harvest `done` de grupo → fila en `keyword_biblioteca` con `tipo_producto` del grupo, texto normalizado y `origen` con grupo/campaña/job; (b) mismo término otra vez → una fila, `updated_at` movido; (c) `failed`/vetado/shadow/sin grupo → cero filas; (d) término harvesteado → **cero** filas en `negative_biblioteca` (invariante de intersección vacía); negativo `kind=negative` aplicado (camino cola y camino reconciliación) → fila; (e) fallo inyectado en la escritura de biblioteca → el job **sí** sella `done`, la cola `applied`, alerta emitida; (f) `SET ROLE app_decide` escribe y NO puede `DELETE` ni tocar `origen`; (g) las columnas de dinero quedan NULL en toda fila escrita (un mutante que escriba `cost` o `moneda` muere) | A.2, A.3 | cc:TODO |
| A.5 | [stage:implementacion] [lane:gate] [tdd:required] **`tools/harvest_excepcion.py`** (patrón `tools/archiva_inertes.py:944-960`: `--acepto-mutacion-real --esperado --huella --go`, dry-run primero; solo `app_admin`; sin Amazon): migra UNA campaña sin grupo a `harvest_excepcion` **resolviendo el par destino contra `ad_entity`** (kind `ad_group`, `parent_id` = campaña, misma `platform`; texto libre rechazado); y limpia la terna de UN grupo por `goals_write.edita_goal(harvest_limpia_destino=True)`. | Tests: dry-run no escribe; `--go` escribe exactamente una fila/un grupo; huella distinta aborta; par inválido (otra plataforma, ad group de otra campaña) rechazado; idempotente; el candado de escritor único ampliado a `tools/` pasa y **falla** con un `UPDATE ads_optimizer_goal` crudo sembrado en `tools/` | A.1, A.2 | cc:TODO |
| A.6 | [stage:implementacion] [lane:gate] [tdd:required] **Visibilidad y aviso**: motivos nuevos traducidos donde se ven los skips (`/salud`, `/cortes`), fase `hermanas_negadas` con etiqueta en el feed de fases, el renglón de veto de un harvest de grupo **nombra las hermanas que se negarán**, y aviso por `app/notifica.py` (sender nuevo, fail-silent, en flanco por campaña) para `destino_inconsistente` y `sin_destino_de_harvest` en campañas de grupo. Sin ruta ni fetch nuevos. | Assert de texto traducido exacto `!= id crudo` (el fallback de `tests/test_api_dashboard.py:1637` lo tragaría); renglón con las hermanas; aviso una vez por racha y no en la segunda corrida | A.1, A.3 | cc:TODO |

### Fase R — Revisión independiente

| Task | Contenido | DoD | Depends | Status |
|---|---|---|---|---|
| R.1 | [stage:revision] [lane:gate] [tdd:skip:revision] Revisión independiente sobre SHA concreto por un revisor que no implementó (kimi/codex/grok). El **implementador entrega** `E/R.1/mutantes.md` con el catálogo enumerado (≥1 por AC y, obligatorios: `_avanza` merge superficial de `hermanas`; LIST sin `adGroupIdFilter`; LIST que cuenta `ARCHIVED`; hermanas antes del readback de keyword; reversa origen-antes-que-hermanos; `quota_cobrada=True` en filas `hermana`; `COALESCE` de moneda; resolutor por nombre; fallback al goal con terna presente; `USAGE` de secuencia ausente; fase nueva fuera del índice parcial; fase nueva fuera de los SELECT de reconciliación; `completa` derivada de la terna; dedupe apuntado al goal); el **lead audita** el catálogo; el **revisor re-muta** y verifica guard (ningún verbo nuevo a Amazon), dinero, append-only, quota, reversa, GRANTs por columna, replay. | APPROVE sobre SHA; **cero sobrevivientes** entre los enumerados (un sobreviviente se cierra con test en el mismo PR, no se declara); E/R.1 | A.1–A.6 | cc:TODO |

### Fase D — Despliegue y cierre (lead + dueño)

| Task | Contenido | DoD | Depends | Status |
|---|---|---|---|---|
| D.1 | [stage:cierre-pr] [lane:release] [tdd:skip:validacion-entrega] **Precondiciones**: cero filas `harvest` no terminales en `apply_queue` (patrón orbit-05 1.3); caps diarios de harvest **bajados al arranque** (decisión del dueño 2026-09-10; número exacto fijado en D.1 con el multiplicador a la vista — referencia 2 harvest/día ≈ 12 escrituras; 1 unidad = hasta 6 mutaciones; los negativos de hermanas no cuentan contra el cap de negative, y eso queda escrito en el runbook). Backup; migración `00NN` en una transacción (declara el índice recreado y el CHECK soltado); verificación como `orbit_read` (fases, índice, tipo `hermana`, triggers, GRANTs ±, secuencias); deploy `git archive` + md5 + rebuild; smoke de lectura. **Verificación de apagado** (no «reversa»): goals del grupo en `shadow` → ningún job nuevo sale a HTTP. La reversa real se ensayó en 0.1 con ids reales. | Runbook `docs/DEPLOY.md` sección F2; E/D.1 con SHA, salidas, caps decididos y la verificación de apagado | R.1 | cc:TODO |
| D.2 | [stage:cierre-pr] [lane:release] [tdd:skip:ops] **Migrar existentes** con `tools/harvest_excepcion.py`, una a una, con go literal del dueño según E/0.2; limpiar la terna del grupo 1 con su go. Hasta que cada campaña esté migrada, sigue por su terna (`migracion_pendiente` visible). | Cada fila de `harvest_excepcion` con `go_literal` y par validado; terna NULL en el grupo 1; `/salud` sin `migracion_pendiente` al terminar; E/D.2 | D.1, 0.2 | cc:TODO |
| D.3 | [stage:cierre-pr] [lane:release] [tdd:skip:ops] **Primer harvest de grupo en vivo**: encendido de `kit_arras` a `live` = go del dueño; seguir el primer harvest natural hasta `done`: keyword en la exacta, negativos en las hermanas por LIST, biblioteca con la fila, ledger sellado, `/cortes` mostrando las hermanas en el renglón antes de vencer el veto. | E/D.3 con ids reales y readback; AUTO-02 y `ORBIT 17` cierran en el tracker | D.2, `cortes-ui-01` 1.2 | cc:TODO |

## Clasificación (Required / Recommended / Optional / Reject)

- **Required**: 0.1, 0.2, A.0, A.1, A.2, A.3a, A.3, A.4, A.5, A.6, R.1, D.1, D.2.
  A.5/D.2 son decisión 4 del dueño y la única forma de dejar un solo camino de
  config (regla 1); A.6 es la única superficie de producto de los skips; A.0 y
  A.3a son el costo real de tocar `apply_harvest.py` en grande.
- **Owner-gated**: D.3 (encender live; depende de `cortes-ui-01` 1.2 y de los
  caps re-decididos en D.1).
- **Reject** (decisión del dueño 2026-09-10): guardar dinero en
  `keyword_biblioteca` — solo palabra y origen; las columnas de dinero quedan
  NULL.
- **Reject**: `kind` nuevo en la cola; revertir la keyword exacta por una
  hermana (destruye valor, deja el ledger sin confirmar y quema el término para
  siempre por el dedupe ciego al estado); fallar un harvest por una hermana;
  inferir `tipo_producto` o hermanas por nombre; un LIST por hermana; trigger
  de fase que consulte la membresía viva; `harvest_excepcion` con texto libre.

## Propiedad de archivos y concurrencia

| Tasks | Archivos/área previstos | Restricción |
|---|---|---|
| 0.1–0.2 | E/0.x; `tools/smoke_apply.py` (solo un flag de ad group si falta) | Escrituras reversibles con go, ledger `probe`; nada más a Amazon |
| A.0 | `tests/conftest_f2.py` o `tests/test_fabrica_f2.py` (fixture/helpers) | Reutiliza helpers; no duplica migraciones |
| A.1 | `app/optimizer/harvest_destino.py` (nuevo), `app/cycle.py`, `app/apply_harvest.py` (`_contexto`, re-validación, dedupe), `app/optimizer/replay.py`, `app/goals_write.py`, tests | `decide_hygiene` intacto; `goals_write` único escritor (candado ampliado a `tools/`) |
| A.2 | `migrations/00NN_*.sql`, `tests/test_fabrica_migracion.py`/`test_apply_schema.py`, `tests/test_schema.py`, `docs/APPLY.md` §6–7, `docs/DATABASE.md` | Número reservado al implementar; 0001/0002/0018 no se editan |
| A.3a/A.3 | `app/apply_harvest.py` (partido), `app/apply.py` (tipo `hermana`, `_cliente_reversa`), `tools/reversa_harvest.py` (nuevo), tests | Sin verbo HTTP nuevo en `app/ads/write.py`; `AdsWriteClient` solo desde `app/apply.py` |
| A.4 | sello en apply_harvest (ejecución), `app/apply_cola.py`, `_reconcilia_negativas`, tests | Solo en la transacción del sello, en SAVEPOINT |
| A.5 | `tools/harvest_excepcion.py` (nuevo), `app/goals_write.py`, `tests/test_architecture.py` | Solo `app_admin`; sin Amazon |
| A.6 | `app/api_dashboard.py`, plantillas, `app/notifica.py` (sender nuevo), tests | Sin ruta ni fetch nuevos; `notifica_*` existentes intactos |
| R.1/D.x | Evidencia, `docs/DEPLOY.md`, PRs | Revisor solo lectura; deploy y merge los corre el dueño con `!` |

Choca potencialmente con: **`orbit-05` 2.3/2.5** (harvest live de campañas no
agrupadas: siguen por terna hasta D.2 gracias al paso 3 del resolutor; D.1 exige
cola de harvest vacía); **ORBIT 19** (0019/0020 y `campana_grupo`: no se editan;
número posterior); **`cortes-ui-01` 1.2** (precondición de D.3).

## Aceptación verificable

| ID | Caso | Resultado esperado | Evidencia |
|---|---|---|---|
| AC1a | Campaña en grupo, terna NULL o coherente | Keyword en la exacta DEL GRUPO (no la del señuelo por nombre) | A.1 (a) + D.3 |
| AC1b | Campaña en grupo, terna scope campaign distinta | Cero HTTP, `destino_inconsistente`, aviso | A.1 (e), A.6 |
| AC2 | Origen con rol `category_exact` | `origen_es_destino`, cero HTTP | A.1 (b) |
| AC3 | Reanudación tras caída / LIST truncado | Cero POST duplicado; job retomado; pendiente `list_truncado` | A.3 (d)(h) |
| AC4 | Fallo en una hermana | Keyword intacta y decisión confirmada; hermana pendiente; reintento sin quota; `done` + alerta al tope | A.3 (e) |
| AC5 | Vetado / shadow / `failed` / sin grupo | Cero jobs nuevos y cero filas en bibliotecas | A.3 (l), A.4 (c) |
| AC6 | Fallo al escribir la biblioteca | La fila no se escribe; **el job sí sella `done`**; alerta | A.4 (e) |
| AC7 | Replay y apply de una decisión tras cambiar el grupo o limpiar la terna | Apply no postea a un destino desincronizado; replay reproduce el congelado | A.1 (f) |
| AC8 | `app_decide` | Escribe bibliotecas (statement real), NO borra, NO toca `origen`, NO escribe `harvest_excepcion` | A.2 (f), A.4 (f) |
| AC9 | Campaña existente sin grupo, sin migrar | Sigue cosechando por su terna, con `migracion_pendiente` visible | A.1 (c'), A.6 |
| AC10 | Término harvesteado | En `keyword_biblioteca`, **jamás** en `negative_biblioteca` | A.4 (d) |
| AC11 | Terna NULL en grupo | El POST lleva el `harvest_default_bid` del goal | A.1 (h) |
| AC12 | Quota | 1 unidad por harvest; filas `hermana` con `quota_cobrada=false`; reintentos sin nueva unidad | A.3 (f) |

## Secuencia de despliegue y reversa

1. Precondición: cola de harvest sin filas no terminales; caps re-decididos.
2. Backup; migración `00NN` (índice recreado y CHECK soltado, en transacción) ANTES
   del código: el código nuevo sella fases y tipos que el esquema viejo rechaza.
3. Deploy; smoke de lectura; verificación de apagado (grupo en `shadow`).
4. D.2 en la misma ventana operativa: hasta migrar cada campaña, sigue por terna.
5. Reversa: **apagar** = goals del grupo en `shadow`; deshacer un harvest
   completado = `tools/reversa_harvest.py --job` (keyword → hermanos → origen),
   ensayada en 0.1 con ids reales. La migración se conserva sin uso.

## Confirmación operativa previa por fase

Inventario harness-plan; **no es aprobación concedida**. Sin
`plan-preapprovals.json`: la confirmación es del dueño por tarea.

| Asunto/operación | Motivo | Scope y límites |
|---|---|---|
| POST negative keyword en el ad group PT + 3 hermanas y su `borrar_negative` (archiva) | Decisión 10 y ensayo de reversa (regla 7) | 0.1; `tools/smoke_apply.py`, ledger `probe`, `termino_basura`, go literal |
| SELECT en producción como `orbit_read` | Inventario y verificación | 0.2, D.x |
| Re-decidir caps `ads_apply_cap_*_harvest` / `_negative` | 1 unidad = hasta 6 mutaciones | D.1; go del dueño con el número visible en `/salud` |
| Backup + migración `00NN` (índice + CHECK) + deploy | Publicar F2 recuperable | D.1; runbook |
| `tools/harvest_excepcion.py --go` | Migrar existentes y limpiar terna | D.2; una campaña/grupo por go; par validado |
| Escritura a Amazon por el motor (negativos en hermanas) | Función de F2 | Solo tras D.3 con goals `live`; veto 48h intacto |
| git push, PR, CI | Revisión | Ramas desde `origin/master`; jamás `--no-verify` |

## Decisiones del dueño (cerradas 2026-09-10)

1. **Biblioteca: solo palabras.** `keyword_biblioteca` guarda término, origen y
   fechas; las columnas de dinero quedan NULL. Razón: llenarlas creaba una
   segunda fuente de esos números (regla 2) en una fila mutable (regla 5) con
   ventanas solapadas que no se pueden sumar, y nadie las lee: sembrar grupos
   nuevos solo usa el texto.
2. **Caps bajados al arranque.** El tope diario de harvest baja para las
   primeras semanas en vivo (referencia 2/día ≈ 12 escrituras); el número
   exacto se fija en D.1 con el multiplicador a la vista y sube cuando el dueño
   vea que las hermanas se bloquean bien.
3. **Implementa GLM** (A.0–A.6, por brief y por fase; vigilar su proceso:
   red-logs TDD, tope de cross-reviews, no tocar trackers). R.1 a kimi o codex.
   Sondas 0.1/0.2 y despliegue D.x: lead + dueño.

Decisión de diseño tomada por el plan con recomendación de 3 de 5 revisores,
**revisable por el dueño**: si falla bloquear el término en una hermana, la
palabra nueva se conserva (ya vende), el bloqueo se reintenta solo cada día y
se avisa; jamás se deshace la palabra por un fallo de higiene.

## Spec delta (aplica el lead en este mismo PR)

`docs/superpowers/specs/2026-09-05-fabrica-campanas-grupos-design.md` §7, seis
precisiones que el spec no fija y que cambian comportamiento:

1. Origen con rol `category_exact` → skip `origen_es_destino`.
2. Transición: mientras una campaña sin grupo no esté en `harvest_excepcion`,
   su terna de scope `campaign` sigue valiendo con motivo `migracion_pendiente`;
   la terna de plataforma nunca es destino ni contradicción.
3. El destino resuelto se congela en la decisión (`resuelto_por`); apply lo
   re-valida contra el grupo vigente (`destino_desincronizado`) y replay lo lee.
4. La decisión se confirma en el readback de la keyword; `hermanas_negadas` es
   higiene posterior reintentable; jamás se revierte la keyword por una hermana;
   `apply_attempt` gana `tipo = 'hermana'` fuera del tope de 3.
5. `negative_biblioteca` recibe solo negativos de decisiones `kind = negative`;
   los negativos de harvest (origen y hermanas) jamás.
6. `harvest_excepcion` solo acepta pares `(campaña, ad group)` validados contra
   `ad_entity` (parentesco y plataforma).

`docs/APPLY.md` §6.1/§7 y `docs/DATABASE.md`: los cambia A.2 en su PR (no este).

## Divergencias y residuales declarados

- Spec §7 dice «la terna se limpia grupo por grupo al migrar»: aquí es A.5/D.2
  por `goals_write` con go, no dentro de la migración (una migración no pide go).
- Spec §7 no fija qué pasa si falla una hermana ni si la biblioteca guarda
  dinero: ver delta 4 y decisión 1 del dueño.
- `keywords_campana_destino` es ciega al estado (incluye `ARCHIVED`): un término
  cuya keyword se archive queda `harvest_duplicado` para siempre. Preexistente;
  F2 lo evita no revirtiendo keywords. Queda declarado, no se toca aquí.
- `_identidad` compara `keywordText` sensible a mayúsculas mientras el motor
  normaliza; residual de precisión del pre-check, declarado.
- `keyword_biblioteca.texto` propaga consultas de comprador a otros grupos del
  mismo tipo de producto: clase de dato ya existente en
  `search_term_observation`, alcance nuevo; sin PII.
- Consumo de la biblioteca por la siembra de grupos nuevos (F1) no cambia.

## Snippet para `plans/manifest.json` (aplicado en este PR; no cambia `active`)

```json
{
  "name": "fabrica-02",
  "path": "plans/fabrica-02.md",
  "description": "FABRICA 02 — PLAN (no implementado) 2026-09-10: harvest por grupo (F2/AUTO-02): destino por campana_grupo_rol + harvest_excepcion (terna vigente como transicion), fase hermanas_negadas confirmada en el readback de la keyword con 1 quota y reversa operable, biblioteca escrita por el motor solo desde aplicados, migracion de existentes con go. Precondicion de live: cortes-ui-01 1.2."
}
```

## Estado para la siguiente sesión

- Plan v1.1 revisado por cinco perspectivas y con las decisiones 1–3 del dueño
  cerradas. PR #253 + #254.
- **0.1 CERRADA 2026-09-12**: PT acepta negative keywords por texto →
  `HERMANAS_ROLES` = **4 hermanas** (`auto_discovery`, `category_broad`,
  `category_phrase`, `product_targeting`). Neto cero, ledger `probe` 154/155,
  E/0.1. La mitad (ii) del 0.1 se difirió a A.3 y D.3 por decisión del dueño.
- **Falta 0.2** (inventario de existentes, solo lectura): es lo único que queda
  antes del código, y alimenta A.1 y D.2.
- Implementación: GLM por brief y por fase (A.0 → A.1 → A.2 → A.3a → A.3 →
  A.4 → A.5 → A.6); el lead revisa una ronda por bloque y muta; R.1 a kimi o
  codex.
