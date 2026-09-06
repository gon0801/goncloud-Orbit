# FABRICA 01 — fabrica de campañas Amazon SP con estructura fija por grupo

**Estado: REVISADO (2026-09-05).** Decisiones del dueno en §1 (brainstorming
formal, una pregunta a la vez); revision del lead contra el repo vivo con ok
del dueno (decisiones 12-14 y correcciones en §2-§10).
Precedencia: `docs/CONTEXTO.md` (reglas 1-10) > `docs/traspaso/ADS_OPTIMIZER_V2_DESIGN.md` > este spec.
Modulo 2 del roadmap (`docs/traspaso/MODULOS-AVANZADOS.md` §"Creación de
Campañas por API"), acotado a Amazon SP con estructura fija; SB/SD y MeLi
quedan FUERA (MeLi Ads es proposal-only a nivel cuenta, Traspaso 1 §4).

## 1. Decisiones del dueno (2026-09-05, brainstorming)

1. **Solo campañas nuevas.** La fabrica crea grupos desde cero; no adopta
   campañas existentes a grupos. Las existentes siguen su camino (§8).
2. **Grupos multi-producto con target por margen.** Se permite mezclar
   productos, pero el target del grupo se deriva ANTES de crear:
   `target = fraccion × margen MINIMO del grupo` (conservador: nadie puja por
   encima de lo que soporta el margen mas bajo). Producto sin margen medible
   NO entra al grupo (regla 3).
3. **Margen por producto desde el ledger** (misma maquinaria de ORBIT 06
   Fase 2: ventas con `order_id`, COGS a la fecha, cargos de la venta,
   ventana madura), no de economics proyectados. Vista nueva
   `v_margen_producto` (§4).
4. **Harvest: reemplazo total del destino manual** — con salvedad literal del
   dueno: «las que ya estan decidir mandarlas como excepcion a alguna exact y
   que queden asi ya». Las campañas existentes con `harvest_*` configurado se
   migran una a una a `harvest_excepcion` con go del dueno y quedan
   congeladas; la config nueva solo nace de grupos (§7).
5. **Bids y budgets iniciales por argumentos CLI en cada corrida.** Cero
   defaults escondidos: el dry-run muestra los numeros y el go los sella en
   el ledger (regla 3: nunca constantes inventadas).
6. **Semillas desde historial propio**, con biblioteca acumulativa: «si no hay
   historial, tenemos que planear la forma de llenarlas y sobre todo guardar
   historial de palabra y negativas para que campañas nuevas con mismo tipo de
   productos se generen con historial lo mas completo posible» (§6).
7. **`tipo_producto` = etiqueta del dueno por grupo** (texto, p.ej.
   `collar_perro`); agrupa la biblioteca de keywords y negativos. Explicito y
   auditable.
8. **Las campañas nacen ENABLED.** El go literal ya es la autorizacion de
   gasto; el motor las necesita ENABLED para optimizarlas (CAMPANA ACTIVA 01).
9. **Campañas viejas del mismo producto: intactas.** La fabrica solo crea y
   las REPORTA en el dry-run; pausarlas es otra decision con su propio camino
   (regla 1).
10. **Negative cruzado a las 4 hermanas**: auto, phrase, broad Y product
    targeting (§7).
11. **Enfoque A**: herramienta CLI `tools/fabrica_campanas.py` (patron sellado
    de `tools/archiva_inertes.py`) + migracion de tablas de grupo. La creacion
    NO pasa por `apply_queue` (sellado ORBIT 04: «solo cortes en la cola;
    nada nuevo se cuelga de ella») ni por la escalera: es operacion de
    negocio con go humano.
12. **Terna de harvest completa en F1 (transitoria).** El CHECK
    `goal_harvest_completo` exige los tres campos juntos o ninguno, y
    `edita_goal` valida lo mismo. F1 siembra la terna entera apuntando a la
    exact del grupo (campaign + ad group externos + `--bid-exact`): asi el
    harvest funciona HOY con el motor sellado sin tocarlo. F2 relaja el
    candado cuando el ruteo pase a leer `campana_grupo_rol` (§7).
13. **`--modo shadow|live` explicito por CLI.** El default de `mode` es
    `off` y el goal de campana PISA al de plataforma entero, modo incluido
    (`resuelve_goal`): cinco goals en `off` serian cinco campanas que el motor
    jamas toca. Cero defaults escondidos (decision 5): el modo viaja en el go.
14. **Productos sin margen maduro quedan FUERA.** La ventana `[D-105, D-15)`
    con >= 60 dias cubiertos deja fuera a todo producto nuevo (el caso
    «Nuevo» del roadmap). Consecuencia aceptada de las decisiones 2 y 3; un
    `--target-acos` manual con go seria otro spec (residual 7).
    **Enmienda 2026-09-05 (decision escrita del dueno, tarea 1 de
    `plans/fabrica-01.md`):** el guard por producto es >= **30** dias con
    venta sobre la ventana **`[2026-02-20, D-15)`** (arranque fijo = primer
    `valid_from` de `sku_cost`). Evidencia: con `[D-105, D-15)` y 60 dias
    ningun producto entraba (max 27), y con 365 dias ninguno cubria costo
    (64-90 % < 0.95). La consecuencia (productos nuevos fuera) se mantiene.

## 2. Alcance y phases

- **F1 — Fabrica proposal-only** (no toca el motor): migracion 0018,
  `v_margen_producto`, `crea_goal` en `app/goals_write.py`,
  `tools/fabrica_campanas.py` (dry-run -> go -> creacion con product ads ->
  readback -> sync de estructura -> ledger), reversa `--desarmar`, biblioteca
  de semillas (lectura; la escritura por harvest/negative llega con F2).
  El primer grupo real ES la sonda de los shapes nuevos (§5.5).
- **F2 — Enrutamiento de harvest por grupo + negative cruzado** (toca el
  motor, TDD estricto, regla 9 demostrada): migracion 0019 (fase nueva de
  `harvest_job` + candado `goal_harvest_completo` relajado); resolucion de
  destino grupo > excepcion > skip; negative-exact cruzado en las hermanas
  con su reversa; alimentacion de la biblioteca desde harvest/negatives
  aplicados; migracion de las existentes a `harvest_excepcion` con go.

Fuera de alcance (las dos phases): adopcion de campañas existentes a grupos,
MeLi, SB/SD, pausa de campañas viejas, budgets en el motor (M4 sigue fuera),
productos sin margen maduro en el ledger (decision 14), cualquier escritura
que no sea la creacion del grupo y su reversa.

## 3. Estructura fija del grupo

Cada grupo crea exactamente 5 campañas SP, una por rol:

| Rol | Tipo | Semilla inicial |
|---|---|---|
| `auto_discovery` | auto | Negativos de `negative_biblioteca[tipo_producto]` |
| `category_phrase` | manual keyword phrase | Biblioteca + terminos con ventas del mismo producto (§6) |
| `product_targeting` | manual product | ASINs con ventas del mismo producto + biblioteca |
| `category_broad` | manual keyword broad | Misma lista que phrase (Amazon distingue el match) |
| `category_exact` | manual keyword exact | VACIA (recibe harvest; excepcion §6) |

Nombre en Amazon (la API lo exige; NO es el vinculo — el vinculo es la tabla):
`<tipo_producto> | <nombre_base> | <rol> | <YYYY-MM-DD>`.

Cada campaña lleva UN ad group y un product ad POR PRODUCTO del grupo
(POST `/sp/productAds` con `sku` = `listing.seller_sku` de la plataforma;
shape sellado por la sonda 2026-08-31, solo camino de error). Sin anuncio la
campaña no sirve nada: producto sin `seller_sku` en esa plataforma no entra
al grupo (validacion sin HTTP, §5.1).

## 4. Target del grupo y `v_margen_producto`

Vista nueva por producto con la MISMA maquinaria sellada de
`v_target_margen_plataforma` (spec ORBIT 06 Fase 2): ledger, ventana
`[2026-02-20, D-15)` con guard de 30 dias (enmienda de la decision 14; la
plataforma sigue con `[D-105, D-15)` y 60), COGS vigente a la fecha de la venta en MISMA
moneda, cargos con `order_id` de ventas cubiertas, cobertura por monto; sin
margen medible la fila no existe (regla 3), jamas cero.

```text
target_grupo = fraccion × min(margen_neto_pct de los productos del grupo)
```

- `fraccion` = la misma del setting `ads_target_fraccion_margen_<platform>`
  de ORBIT 06 (hoy 0.5, «la mitad»). Sin setting valido -> aborta (interruptor).
- Clamp a la banda [10, 45], misma del peldaño margen_plataforma.
- El valor se CONGELA en `campana_grupo.target_acos_pct` con
  `target_procedencia` (snapshot auditable al crear; el re-ajuste automatico
  del target de un grupo ya creado queda FUERA — residual 3).
- La fabrica escribe goal de scope campaña para cada una de las 5 via
  `app.goals_write.crea_goal` (camino unico de goals, sellado 26 de ORBIT 04
  — `docs/APPLY.md` §10.3; hoy solo existe `edita_goal`, que es UPDATE): ese target, `enabled=true`, `mode` =
  `--modo` (decision 13), floor/techo de `DEFAULTS_POR_MONEDA`, y la terna
  harvest COMPLETA apuntando a la exact del grupo con `harvest_default_bid =
  --bid-exact` (decision 12; el monto del harvest no cambia de camino).

## 5. Flujo de creacion (`tools/fabrica_campanas.py`)

Patron sellado de `tools/archiva_inertes.py` (su docstring manda en el
como): plan desde la base con `ORBIT_DSN_READ`, dry-run por defecto con
tabla + huella del conjunto (sha256 de los elementos autorizados), mutacion
solo con `--acepto-mutacion-real --esperado N --huella H --go "<literal>"`.

```bash
docker exec -i orbit-app-1 python - --plataforma amazon_mx \
  --tipo-producto collar_perro --nombre-base "Collar reflectante" \
  --productos <product_id,...> --modo shadow \
  --budget-auto 150 --budget-phrase 120 --budget-product 120 \
  --budget-broad 120 --budget-exact 150 \
  --bid-auto 4.50 --bid-phrase 5.00 --bid-product 5.00 \
  --bid-broad 4.00 --bid-exact 6.00 \
  < tools/fabrica_campanas.py
```

En orden:

1. **Validacion SIN HTTP** (fail-closed temprano): perfil aceptado de
   `evaluar_perfiles`; cada producto con margen en `v_margen_producto` y con
   `listing.seller_sku` en la plataforma (sin SKU no hay anuncio); target
   derivado y clampeado (§4); moneda por plataforma (regla 4); bids > 0 y
   dentro de piso/techo por moneda (USD 0.10/2.50, MXN 1.00/45.00 — mismos
   defaults de goal, regla 4); budgets > 0 y >= su bid (el piso/techo es
   SOLO de bids: `--budget-auto 150` con `--bid-auto 45` en MXN es valido;
   sin techo de budget, el tope real es el go del dueño); `--productos` no vacio;
   `--modo` en {shadow, live}; `ORBIT_DSN_INGEST` presente (lo exige el
   paso 4).
2. **Dry-run**: las 5 campañas (nombre, rol, budget, bid, targeting, SKUs de
   los anuncios, conteo de semillas por rol, explícito si es 0, modo del
   goal) + reporte de campañas existentes de los mismos productos (solo
   informa, decision 9) + huella.
3. **Mutacion**: orden fijo `exact -> phrase -> broad -> product -> auto`
   (la exact primero: si el lote muere a medias, jamas queda discovery sin
   destino de harvest). Por campaña: fila `planeado` en el ledger con commit
   (intencion durable ANTES del HTTP, regla 7) -> POST `/sp/campaigns` ->
   POST `/sp/adGroups` -> POST `/sp/productAds` (uno por producto, `sku`) ->
   siembra de keywords/targets/negativos -> readback por LIST (campaña, ad
   group, anuncios y semillas) -> `applied`. Cada POST es un
   `fabrica_lote_paso` propio. Cualquier rechazo o readback que no cuadra ->
   `failed` y el lote SE DETIENE declarando que quedo creado.
4. **Sync de estructura y registro interno**: las filas `ad_entity` de las
   campañas nuevas (FK de `campana_grupo_rol` y del trigger
   `goal_scope_campana_real`) las escribe SOLO el sync de estructura
   (`sync_structure` de `app/ads/structure.py`, rol `app_ingest`);
   `app_admin` no tiene INSERT en `ad_entity`. La fabrica lo invoca con
   `ORBIT_DSN_INGEST` tras el readback (mismo escritor, camino unico; llena
   tambien `ad_entity_state`, que el ciclo filtra por status ENABLED) y
   recien entonces, con los ids internos resueltos, escribe `campana_grupo`,
   `campana_grupo_rol`, `campana_grupo_producto` y los 5 goals via
   `crea_goal` (§4).
5. **HTTP propio con sello v3**: `httpx` directo, vendor v3 exacto en
   Content-Type Y Accept, POSTs envueltos en su clave de lista (sellos del
   probe 2.5 y de `reactiva_campanas`/`archiva_inertes`). **NO importa
   `app/ads/client.py` para mutar ni `app/ads/write.py`** — un segundo dueno
   de la mutacion (candado en `tests/test_architecture.py`). El readback usa
   el cliente de lectura (`list_objects`). Shapes NUNCA ejercitados en vivo:
   POST `/sp/campaigns`, `/sp/adGroups`, `/sp/targets` (create) y el camino
   feliz de `/sp/productAds` (solo keywords, negativeKeywords y el PUT de
   campañas estan sellados). Sonda = el primer grupo real, con UN producto y
   los budgets minimos por moneda; su log queda en `out/` como evidencia y
   los vendors confirmados se sellan en el modulo.
6. **Reversa** (regla 7, mismo PR): `--desarmar <grupo> --go "<literal>"`
   PAUSA las 5 (PUT `/sp/campaigns` state PAUSED, sellado en
   `reactiva_campanas`; nunca archiva: pausar es la reversa segura y
   reversible) y pone `enabled=false` en sus 5 goals via `edita_goal`, para
   que el motor no siga decidiendo sobre campañas pausadas.

Reconciliacion: `--reconciliar` cruza `planeado`/`failed` contra el LIST real
y promueve solo lo verificado; aborta si algo queda sin verificar O si el
objeto vive pero no cuadra con el payload del ledger (fail-closed,
idempotente).

## 6. Semillas y biblioteca por tipo_producto

- `keyword_biblioteca` / `negative_biblioteca`: por `(tipo_producto, platform,
  texto)`: origen (que grupo/campaña la aporto), orders/cost/revenue
  acumulados, `first_seen_at`. Todo dinero con `(valor, moneda)` (regla 4).
- La siembra de phrase/broad: biblioteca del `tipo_producto` con orders >= 1
  + terminos con ventas del MISMO producto en campañas existentes
  (`search_term_observation`, ya ingerido; colapso bitemporal por
  `observed_at`, regla 5, sin dia en curso). Product targeting: ASINs que
  convirtieron como search term (`is_asin_like`) + ASINs de biblioteca.
- `category_exact` nace VACIA. Excepcion: un termino del historial propio
  que YA cumple el criterio HARVEST sellado (orders >= 2 y ACoS <= min(35%,
  target)) puede nacer sembrado en exact — mismo criterio del motor, aplicado
  al sembrar.
- **Sin historial**: las manuales nacen vacias y el dry-run lo dice explicito
  («phrase: 0 semillas»). La biblioteca se llena sola: cada harvest y cada
  negative que el motor APLIQUE en campañas de un grupo escribe en la
  biblioteca de su `tipo_producto` (F2), asi el siguiente grupo del mismo
  tipo arranca con historial mas completo (decision 6).
- Terminos ASIN-like solo alimentan product targeting, jamas keywords
  (regla sealed own-ASIN). Aplica SOLO a la siembra: el motor ya salta
  ASIN-like siempre (`MOTIVO_ASIN_LIKE`, ambos kinds) y F2 no toca ese skip.

## 7. Harvest por grupo y negative cruzado (F2)

**Resolucion del destino** (reemplaza la siembra manual de `goal.harvest_*`):

1. Campaña origen en grupo -> ad group de la `category_exact` DE ESE GRUPO
   (SQL puro via `campana_grupo_rol`; sin convencion de nombres).
2. Campaña sin grupo -> `harvest_excepcion` (existentes migradas una a una
   con go del dueno, congeladas — decision 4).
3. Sin grupo ni excepcion -> skip con motivo `sin destino de harvest`
   (visible en audit; jamas placeholder).

`goal.harvest_campaign_id / harvest_ad_group_id` dejan de ser camino de
config nueva; `harvest_default_bid` SIGUE fijando el monto (la fabrica lo
siembra con `--bid-exact`). Migracion 0019 (F2): el CHECK
`goal_harvest_completo` se reemplaza por un trigger que admite
`harvest_default_bid` solo (campaign/ad_group NULL) unicamente si la campaña
esta en `campana_grupo_rol`; `edita_goal` valida lo mismo. La terna
transitoria de F1 (decision 12) se limpia grupo por grupo al migrar, con el
mismo go.

**Negative cruzado**: la primera fase del harvest ya crea el negative-exact
en la campaña ORIGEN (sellado ORBIT 04, fases `negative_created ->
exact_created -> done`). F2 agrega la fase `hermanas_negadas` entre
`exact_created` y `done` (migracion 0019: CHECK de `harvest_job.fase` y
trigger `harvest_job_sella_fases`): creada y verificada la keyword en la
exact del grupo, se crea negative-exact del MISMO termino en las hermanas
(auto, phrase, broad, product targeting — decision 10), ids en
`external_ids`. Mismo ledger, mismo readback; el harvest sigue cobrando 1
operacion logica de quota aunque sean 6 HTTPs (unidad sellada en
`apply_attempt`). Orden siempre keyword primero, negatives despues, y la
reversa (`reversa_harvest_completo`) borra keyword -> negativos hermanos ->
negativo de origen (regla 7). Madurez >= 10d sin cambio (trigger
`decision_madurez_corte`).

Hermana `product_targeting`: Amazon SP acepta negative keywords en ad
groups auto y de keyword; en los de product targeting el negativo es por
ASIN/marca (`negativeTargets`), no por texto. Se sonda en vivo ANTES de
sellar la fase (regla 8 aplicada a la API): si el POST se acepta, las 4
hermanas; si lo rechaza, esa hermana se declara skip con motivo en el job y
la decision 10 queda en 3 hermanas (residual 8).

## 8. Migracion 0018 (tablas nuevas, con sus COMMENT ON)

- `campana_grupo(id, platform, tipo_producto, nombre_base, target_acos_pct,
  target_procedencia, go_literal, created_at)`.
- `campana_grupo_rol(grupo_id, rol, ad_entity_id, ad_group_ad_entity_id)` —
  `rol` ENUM de los 5 valores; `UNIQUE(grupo_id, rol)`; `UNIQUE(ad_entity_id)`
  (una campaña pertenece a lo sumo a un grupo). El ad group de la exact va
  guardado, no resuelto por `parent_id`: el destino del harvest es un SELECT
  directo y auditable.
- `campana_grupo_producto(grupo_id, product_id REFERENCES product, margen_pct,
  seller_sku)` — snapshot al alta; el target del grupo nace del minimo.
- `keyword_biblioteca` / `negative_biblioteca` (§6).
- `harvest_excepcion(campaign ad_entity_id, destino campaign+ad_group
  externos, go_literal, created_at)` — una por campaña, congelada.
- Ledger de creacion `fabrica_lote` + `fabrica_lote_paso` (patron
  `keyword_archivo_manual`: intencion durable pre-HTTP, `ack` JSONB,
  `readback_estado`, estados `planeado/applied/failed`; un paso por POST:
  campaña, ad group, cada product ad, cada semilla).
- `v_margen_producto`: grano `ledger_event.product_id`. Los cargos con
  `order_id` de una orden multi-producto se prorratean por el monto de venta
  del producto dentro de la orden; los sin `order_id` por cobertura, como en
  la vista de plataforma. Antes de escribirla, el `SELECT` que mida cuantas
  ordenes son multi-producto y cuantos cargos traen `product_id` (regla 8;
  residual 6 si el grano no alcanza).
- GRANTs en la misma migracion: `app_admin` INSERT/UPDATE en las tablas
  nuevas; `app_decide` SELECT en `campana_grupo_rol` (el ruteo de F2 corre
  como motor); `app_read` SELECT en todo.

Invariantes nuevos del esquema = con su test, y antes del test el `SELECT`
que confirma la forma real del dato en produccion (regla 8).

## 9. Errores y testing

- Fail-closed en todo: lote detenido al primer rechazo/readback que no
  cuadra; sin perfil aceptado no se muta; sin margen no entra el producto;
  sin huella/esperado/go no hay mutacion.
- **Regla 9**: los tests del reruteo (F2) se demuestran FALLANDO contra el
  codigo actual, que solo conoce `harvest_*`. TDD estricto en F2.
- F1 nace con tests de plan, huella, validaciones y ledger **contra Postgres
  real** donde aplique (precedente: el bug `%s::platform`
  IndeterminateDatatype solo lo vio una base de verdad).
- Candado en `tests/test_architecture.py`: el escaneo de `app.ads.write`
  ya cubre `tools/` entero; la fabrica suma una allowlist POSITIVA de imports
  (patron `ALLOWLIST_IMPORTS_SNAPSHOT_LISTAS`) que incluye `app.goals_write`
  y `app.ads.structure` y excluye `app.ads.write`.
- Suite + ruff + pre-commit verdes antes de terminar cada phase; jamas
  `--no-verify`.

## 10. Residuales declarados

1. **Adopcion de campañas existentes a grupos**: fuera (decision 1). Si un
   dia se quiere, es su propio spec con verificacion de estructura.
2. **`tipo_producto` libre**: la disciplina de etiquetado es del dueno; no
   hay catalogo cerrado ni sugerencia automatica (se rechazo el hibrido por
   regla 2).
3. **Re-ajuste del target de un grupo ya creado**: el target se congela al
   crear; el auto-ajuste (como el peldaño margen_plataforma) queda para una
   phase posterior con diseno propio.
4. **Pausa de campañas viejas del mismo producto**: la fabrica solo reporta
   (decision 9); la transicion es decision humana aparte.
5. **Semilla de exact desde historial** (excepcion del §6) usa la ventana y
   madurez del criterio HARVEST ya sellado; no introduce umbrales nuevos.
6. **`v_margen_producto` por producto**: si el grano real del ledger no
   soporta margen por producto con cobertura suficiente, F1 se detiene en la
   vista y se reporta — no se siembra target inventado (regla 3).
7. **Productos sin margen maduro** (decision 14): fuera. Un `--target-acos`
   manual con go para productos nuevos contradice la decision 3 y seria un
   spec propio.
8. **Negative keyword en la hermana `product_targeting`**: pendiente de la
   sonda en vivo de §7; hasta entonces la decision 10 se implementa con
   skip declarado si Amazon rechaza el shape.
