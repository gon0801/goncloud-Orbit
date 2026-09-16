# REPRICING 01 — Motor de precios por goal de margen (M1 / AUTO-07)

Fecha: 2026-09-15 UTC. Versión 1.1 (2026-09-16): diseño aprobado por el dueño
en diálogo el 2026-09-15 (decisiones literales en S1) y corregido con la
revisión de cinco perspectivas del 2026-09-16 (S9). Sellado para el plan
`plans/repricing-01.md`. Base de lectura `origin/master` `0617328`.

Precedencia: `docs/CONTEXTO.md` (reglas 1–10) > `plans/ROADMAP.md` (M1 /
AUTO-07) > `docs/superpowers/specs/2026-09-08-margen-estimado-design.md` y su
acta `docs/evidencia/margen-estimado-01/0.3/acta.md` (contrato del margen
estimado) > `docs/traspaso/MODULOS-AVANZADOS.md` §Módulo 1 (criterio del
traspaso) > este spec > el plan.

## Objetivo y límites

Que cada producto con un goal de margen fijado por el dueño **llegue a ese
margen por precio**, y que el precio baje **solo** cuando el producto está
perdiendo ventas, nunca por debajo del goal. El motor protege margen; la Buy
Box se observa y se avisa, no se persigue.

Lo que este spec NO cubre y queda declarado, no rellenado con ceros: FBM,
Mercado Libre, promociones, kits, decisiones por inventario (se lee para
invalidar la señal de ventas, nunca para mover precio), elasticidad, modos
«agresivo/equilibrado» del traspaso.

## S1. Decisiones del dueño (cerradas 2026-09-15, textos literales)

| # | Decisión | Texto literal |
|---|---|---|
| 1 | Objetivo principal: proteger margen; Buy Box deseable, no a cualquier precio | «A, proteger margen» |
| 2 | Subir precio cuando el margen no alcanza el goal | «quisiera subir precios si el margen no esta llegando al goal» |
| 3 | Goal de margen **por producto**, fijado por el dueño | «b» (goal por producto) |
| 4 | El margen que se compara es el **estimado** (precio, costo, comisiones, retenciones), no el observado tras Ads | «A, estimado» |
| 5 | Si el precio del goal queda arriba de la competencia, sube igual; la Buy Box se avisa | «A» |
| 6 | Baja **solo si se están perdiendo ventas**, nunca abajo del goal | «baja solo si se estan perdiendo ventas» |
| 7 | «Perder ventas» = unidades de los **últimos 15 días** contra el **promedio de los 60 días previos** | «por unidades las unidades venidas de los ultimos 15 dias» · «promedio de los 60 dias previos» |
| 8 | Aplicación **automática dentro de límites**, con sombra primero | «a» |
| 9 | Alcance: **Amazon MX y US**; MeLi y FBM fuera | «b» |
| 10 | Enfoque: motor por goal con **precio resuelto** (no escalera ciega); fases 0 (US), A (MX), B (US) | «si, la 1 y ese orden» |
| 11 | **Ningún silencio**: todo producto no evaluado se registra con motivo y se muestra | «si un dia falta un dato ... tiene que mostrarlo claramente no puede fallas en silencio» |
| 12 | El repricing tiene **su propia cuota**, separada de las de Ads | «tiene su propia cuota» |

Umbrales del lead, aceptados como **valores de config, no constantes**
(claves y cotas en el plan): caída de ventas ≥ 40% sostenida 3 corridas;
volumen mínimo `u60 ≥ 20`; escalón máximo por movimiento 10% en ambas
direcciones; movimiento mínimo el mayor de 1% o 1.00 MXN / 0.10 USD; un
cambio por producto cada 7 días; tope diario de cambios por plataforma 5;
banda de goal admisible 10%–60%; freno tras 3 cambios en la misma dirección
sin acercarse al goal; aviso tras 3 días seguidos sin evaluar; reintento de
escritura una vez por día, freno al tercer día. Todo umbral fuera de su cota
al leer la config es un error ruidoso, nunca un valor corregido en silencio.

## S2. Fuentes: una por número, todas existentes

| Número | Fuente (única) | Frescura exigida | Si falta |
|---|---|---|---|
| **Precio actual `P_actual`**, contribución estimada y componentes `P, I, C, F, L, R` | El **escenario vigente de la estimación por venta** (`estimacion_*`, acta 0.3): oferta ≤ 6 h, costo vigente de la corrida `ok` del día, FX exacto o ≤ 7 días, cotización de fees ligada a esa oferta. `P_actual` es el precio de esa oferta: es el precio al que se cotizaron las fees y se calculó la contribución | escenario `disponible` del día UTC | el motivo que ya declara la estimación (`oferta_desactualizada`, `costo_desactualizado`, `fx_ausente`, `fee_ausente`, `impuesto_fee_pendiente`…) |
| Buy Box (precio, dueño, `is_own`), precio más bajo, ofertas; **control de coherencia** del precio | `spapi_price_observation` (pase diario de Pricing v0, 05:00 UTC) | fila del día | `precio_sin_observar` |
| Unidades vendidas por **producto** y día | `ledger_event` `kind = 'sale'` (`quantity`, `product_id`, `event_date`). El ledger va por producto: **la señal de ventas es por `(product_id, platform)`** y la comparten todas las publicaciones de ese producto en esa plataforma | día *cubierto* = `event_date ≤` la fecha máxima cargada por un `ingest_run` `ok` del ledger de la plataforma; último día cubierto ≤ 3 días de antigüedad | `ventas_sin_dato` |
| Publicación (SKU, ASIN, `product_type`, `status`) | `listing` + `spapi_listing_estado_observation` | fila del día; `seller_sku` no nulo | `listing_sin_estado`, `listing_sin_sku`, `listing_inactivo` |
| Inventario | `spapi_inventario_observation` (`total_quantity`) | fila diaria; solo **invalida la señal** de ventas (S4 #5) | `inventario_sin_observar` |
| Goal de margen, modo y límites | `precio_goal` (nueva, S3) y `config_version` | vigente en la fecha | `sin_goal` → no se evalúa |
| Cotización de fees a un precio candidato | `precio_cotizacion` (nueva, S5): Product Fees a `P*`, guardada aparte de la estimación para no invalidar el escenario del día siguiente | ligada a la oferta del escenario | `fee_error:<code>` |

Regla 2 de Orbit: el motor **no** recalcula margen ni costo ni FX; consume el
escenario tal cual. Su cuenta se guarda con `escenario_id`,
`fee_observation_id` (la del escenario) y `cotizacion_id` (la propia), y con
la moneda de cada precio (regla 4). Si la observación de Pricing y el
escenario difieren en más de `precio_divergencia_max_pct` (1%), el día es
`no_evaluado(precio_divergente)` con los dos precios y sus horas: hay una
promoción o un precio programado que el dueño revisa; si persiste 3 días
entra en el aviso de no evaluados y en un bloque propio de `/precios`.
Moneda de la observación distinta de la del escenario → `no_evaluado(moneda_divergente)`.

## S3. Goal de precio por producto (`precio_goal`)

Tabla append-only con vigencia, patrón de `sku_cost`:

```
precio_goal(id, listing_id, platform, margen_goal_pct NUMERIC(6,4),
            mode precio_mode ('shadow'|'live'), valid_from DATE, valid_to DATE NULL,
            creado_por TEXT, go_literal TEXT NULL, created_at)
FK (listing_id, platform) REFERENCES listing (id, platform)   -- UNIQUE (id, platform) en listing
CHECK (platform IN ('amazon_mx','amazon_us'))
CHECK (margen_goal_pct BETWEEN precio_goal_min_pct y precio_goal_max_pct)  -- 0.10–0.60 por config, replicado en goals_write
CHECK ((mode = 'live') = (go_literal IS NOT NULL AND btrim(go_literal) <> ''))
UNIQUE (listing_id, platform, valid_from); un solo vigente por (listing, platform) (índice parcial valid_to IS NULL)
```

- **Sin fila vigente no hay decisión.** No hay default por plataforma ni
  herencia (decisión 3; regla 3 de Orbit).
- `margen_goal_pct` se expresa sobre el ingreso sin IVA (`I`), igual que la
  contribución estimada (acta 0.3: 42.50 MXN sobre `I = 100` es 42.50%). La
  herramienta lo recibe **como porcentaje con dos decimales** (`--goal-pct
  30.00`), nunca como fracción.
- `mode` por producto con la ceremonia de los goals de Ads (PR #283):
  **subir a `live` exige go literal** (y el CHECK lo impone en la base,
  también contra `psql`), bajar a `shadow` no. Cerrar un goal es fijar
  `valid_to` una sola vez (trigger «solo cierra vigencia», `GRANT UPDATE
  (valid_to)` a `app_admin`); ninguna otra columna se edita.
- Escritor único `app/precio/goals_write.py`; `tools/precio_goal.py` solo
  pasa por él, acepta **`listing_id`** (un `--sku` se resuelve en dry-run y
  aborta si mapea a más de una publicación o si la última observación no es
  FBA), deduplica el CSV antes de calcular la huella, imprime por fila
  `m_actual` de hoy y el `P*` resuelto, y aborta si `|P* − P_actual| > 25%`
  salvo `--confirmar-salto <listing_id>` explícito. Candado de escritor único
  propio (`_IDENT_PRECIO_GOAL`) en `tests/test_architecture.py`, sin tocar el
  de Ads.
- Un producto puede tener goal en MX y otro en US; son filas distintas.

## S4. Reglas de decisión (lo que el implementador no decide)

Cada corrida diaria evalúa cada `(listing, platform)` con goal vigente y
produce **exactamente una** fila `precio_decision` (S5): `subir`, `bajar`,
`mantener(motivo)`, `no_evaluado(motivo)`, `goal_inalcanzable(motivo)` o
`frenado(motivo)`. Toda aritmética en `Decimal`.

1. **Insumos completos o nada.** Si cualquier fuente de S2 falta o está
   vencida, `no_evaluado` con el motivo exacto. Nunca una constante ni el
   valor de ayer.
2. **Margen actual.** `m_actual = contribución / I` del escenario vigente a
   `P_actual` (el precio de la oferta del escenario).
3. **Subir** si `m_actual < goal − tol` (`tol` en fracción, 0.005). Precio
   objetivo `P*` por **forma cerrada** con la fórmula del acta:
   `contribución = I − C − F − L − R`, `I = P / (1 + iva)`, `R = r_isr · I`,
   `L = 0` (FBA), `F = ref · P + fijo` donde `ref = FinalFee(ReferralFee) /
   P_actual` y `fijo = Σ FinalFee` del resto, ambos de `fee_details` del
   escenario. Luego **una cotización real** de Product Fees a `P*`
   (`precio_cotizacion`) y recálculo; si `|m(P₁) − goal| > tol`, una segunda
   y última; si sigue fuera, `goal_inalcanzable(fee_no_lineal)`. Cotización
   con `TaxAmount ≠ 0` → `no_evaluado(impuesto_fee_pendiente)` (misma regla
   del acta); cotización que no concilia o falla → `no_evaluado(fee_error:<code>)`.
   Movimiento del día = `min(P*, P_actual · (1 + escalón))`; `P*` se
   redondea al centavo **hacia arriba** (nunca queda bajo el goal), el tope
   del escalón **hacia abajo** (nunca lo rebasa).
4. **Bajar** solo si `m_actual > goal + tol` **y** `perdiendo_ventas`
   (regla 5) **y** no aplica la regla 6. Un movimiento a
   `max(P_goal, P_actual · (1 − escalón))`, con `P_goal` redondeado hacia
   arriba: nunca menor que el goal, nunca más de un escalón por movimiento.
5. **Perdiendo ventas** (por `(product_id, platform)`): `u15 = Σ quantity`
   de los 15 días cubiertos hasta ayer (`[hoy−15, hoy−1]`), `u60 = Σ` de los
   60 cubiertos anteriores (`[hoy−75, hoy−16]`); los rangos en
   `precio_fechas_excluidas` (config, p. ej. Hot Sale, Buen Fin) no cuentan
   en ninguna ventana y el promedio se escala por días contados.
   `perdiendo = u15 < (u60 / n60 · n15) · (1 − caída)` con `<` estricto,
   **solo si**: `u60 ≥ precio_u60_min`; `n15 ≥ 10` días contados; historia
   ≥ 75 días desde la **primera venta del producto en el ledger**; y en cada
   uno de los 15 días hay observación de inventario con `total_quantity > 0`
   y el listing estuvo activo. Si falla algo, la señal es `sin_dato(motivo)`
   (`volumen_bajo`, `ventana_corta`, `sin_historia`, `sin_stock`,
   `listing_inactivo`, `inventario_sin_observar`) y no dispara nada. La
   señal debe ser `true` en **3 corridas consecutivas** (`precio_senal_dias`)
   para contar: una quincena floja por azar no baja precios. Se guardan
   `u15`, `u60`, `n15`, `n60` y la racha.
6. **Freno por ventas tras subida propia.** Si `perdiendo_ventas` es `true`
   y hay un cambio propio `confirmado` (subida) en los últimos 22 días (15
   de ventana + 7 de cooldown), no se sube ni se baja:
   `frenado(perdiendo_tras_subida)` con `u15`, `u60` y el precio anterior en
   la fila, aviso una vez por racha. Se reanuda con un goal nuevo. No es
   bajar debajo del goal (decisión 6 intacta): es dejar de subir y avisar.
7. **Buy Box.** Se registra `buy_box_is_own` y el precio de la Buy Box de
   la observación del día del cambio y del siguiente. Perderla **no** frena
   ni revierte (decisión 5); genera `buy_box_perdida` en flanco (S7).
8. **Movimiento mínimo.** Si `|P_objetivo − P_actual|` es menor que el mayor
   de `precio_movimiento_min_pct` (1%) y `precio_movimiento_min_abs` (1.00
   MXN / 0.10 USD), `mantener(movimiento_minimo)` sin consumir cooldown ni
   cuota.
9. **Cooldown.** Un cambio por `(listing, platform)` cada 7 días, medido
   contra **cualquier** fila `precio_cambio` no-reversa con `enviado_at`
   (pendiente, enviado, confirmado, no_confirmado o error): un intento
   fantasma también ocupa el hueco. Un goal nuevo reinicia el freno (#10),
   **no** el cooldown.
10. **Freno por no convergencia.** Tres cambios consecutivos en la misma
    dirección sin que `|m_actual − goal|` disminuya → `frenado(no_converge)`,
    aviso; se reanuda con un goal nuevo.
11. **Goal inalcanzable.** `P* > 2 · P_actual` o `P* ≤ C` (costo mal cargado)
    → `goal_inalcanzable(motivo)`, sin movimiento, aviso.
12. **Cuota y prioridad.** Tope diario por plataforma bajo la cuota propia
    `motor = 'precio:<platform>'` (decisión 12); un cambio `no_confirmado` o
    `error` también consume; **las reversas también consumen** (un bug
    oscilante no tiene otro tope). Cuando hay más candidatos que cupo, el
    orden es explícito y queda en la fila: `prioridad = |m_actual − goal| ·
    ingreso_60d`, desempate por `listing_id`; los que no alcanzan cupo quedan
    `mantener(cuota)`.
13. **Sombra fiel.** En `shadow` se calcula, se cotiza y se registra todo, y
    se escribe un `precio_cambio` **virtual** (`aplicado = false`,
    `precio_despues = P_objetivo`) que consume cooldown y freno igual que
    uno real, sin PATCH y sin cuota. Así cinco corridas en sombra muestran
    la trayectoria (escalones, cooldown, freno), no cinco copias del día
    cero. Al pasar a `live` los virtuales no cuentan: el conteo real empieza
    en cero. Las frases de sombra van en condicional («subiría de 116.00 a
    127.60 MXN»).

## S5. Persistencia, identidad y reproducibilidad

- `precio_decision` (append-only; `UNIQUE (listing_id, platform,
  decision_date)`; `decision_date` fijado por trigger a `(now() AT TIME ZONE
  'UTC')::date`, jamás fecha del cliente): resultado, motivo, `product_id`,
  `m_actual`, `goal`, `P_actual`, `P_objetivo`, `P_aplicado` (cada uno con
  `currency NOT NULL`), componentes `I, C, F, L, R`, `escenario_id`,
  `fee_observation_id`, `cotizacion_id`, `u15`, `u60`, `n15`, `n60`,
  `racha_senal`, `perdiendo`, `buy_box_is_own`, `mode`, `prioridad`,
  `config_version_id`. Con esa fila cualquiera reproduce la cuenta sin
  volver a consultar Amazon.
- `precio_cotizacion` (append-only, `INSERT` a `app_decide`): `decision_id`,
  `oferta_observation_id`, `quoted_price` + moneda, `total_fees`,
  `fee_details JSONB`, `fees_estimated_at`, `estado`, `error_code`,
  `source_event_id UNIQUE`. Separada de `estimacion_fee_observation` (cuya FK
  exige el precio de la oferta y cuyo lector toma la más reciente): así la
  cotización del motor **no invalida** la estimación del día siguiente.
- `precio_cambio` (append-only en sus valores): `decision_id`, `listing_id`,
  `platform`, `precio_antes` (leído por GET del item **inmediatamente antes**
  del PATCH) y `precio_observado_antes` (Pricing del día), `precio_despues`,
  monedas, `aplicado BOOLEAN`, `estado`, `enviado_at`, `ack JSONB`
  (`submissionId`, `status`, `issues` literales), `readback_precio`,
  `readback_estado`, `readback_at`, `confirmado_por` (observación D+1),
  `error_code`, `es_reversa BOOLEAN`, `reversa_de BIGINT NULL`. **Estados y
  transiciones selladas por trigger**: `pendiente → enviado | error`;
  `enviado → confirmado | no_confirmado` (solo por la observación del día
  siguiente); nada más. `GRANT UPDATE (estado, ack, readback_*, confirmado_por,
  error_code)` a `app_decide`; ninguna columna de precio se actualiza.
  Índice único parcial `(listing_id, platform) WHERE estado IN ('pendiente','enviado')`:
  un cambio abierto bloquea otro del mismo listing.
- **Nunca se edita el precio en `listing`** desde el motor: `listing` lo
  sigue refrescando su ingesta; la verdad del motor es `precio_cambio`.
- Idempotencia y concurrencia: la corrida toma
  `pg_advisory_xact_lock(hashtext('precio:' || platform))` además del
  `flock` del cron; el orden es inmutable: `INSERT precio_decision` +
  `INSERT precio_cambio pendiente` + `COMMIT` → PATCH → sello `enviado`. Dos
  corridas simultáneas producen exactamente un PATCH (test con dos hilos y
  cliente falso).

## S6. Escritura en Amazon y reversa (regla 7: la reversa antes)

- **Cliente de escritura aparte**: `app/spapi/write_client.py`, default-deny,
  una sola ruta permitida (`PATCH` + `construir_ruta_listings(seller_id,
  sku)` con `seller_id ∈ VENDEDORES_PROPIOS`), sin instancia compartida,
  401 un refresh, 429 un reintento, respuesta tal cual; **`SpapiClient` no
  gana el verbo PATCH** (sus cuatro ingestas siguen siendo de solo lectura).
  Importado únicamente por `app/spapi/precio_write.py`. Tres candados en
  `tests/test_architecture.py` con fuga sembrada: imports del write client,
  regex de `PATCH`/`.patch(` junto a `listings/2021-08-01` o
  `sellingpartnerapi` fuera de esos dos archivos, y allowlist de imports de
  `tools/precio_goal.py` y `tools/precio_reversa.py`.
- **El PATCH es asíncrono**: `PATCH /listings/2021-08-01/items/{sellerId}/{sku}`
  responde `202` con `submissionId` y `status: ACCEPTED` y Amazon aplica el
  precio después. Por eso: (1) fila `pendiente` con `precio_antes` leído por
  `GET` del item con `includedData=offers` justo antes; (2) PATCH con
  `productType` (de `spapi_listing_estado_observation`) y un parche `replace`
  sobre `purchasable_offer[...].our_price[...].schedule[...].value_with_tax`
  del marketplace — **la forma exacta se sella con la sonda A.4**, no se
  asume del doc; (3) `ack` literal → `enviado` (o `error` si HTTP 4xx/5xx o
  `status ≠ ACCEPTED`, aunque un GET lea el precio nuevo); (4) un GET
  informativo tras el PATCH (`readback_precio`, `readback_estado =
  ok|fallido(motivo)`), que **no** decide el estado; (5) la observación de
  Pricing del día siguiente cierra: `confirmado` si coincide al centavo con
  `precio_despues`, `no_confirmado` si no. Un readback fallido (429 agotado,
  5xx, cuerpo sin la oferta) es `readback_estado = fallido`, nunca
  `no_confirmado`.
- **Reversa** = un `precio_cambio` con `es_reversa = true` y
  `precio_despues = precio_antes` del cambio revertido, por el mismo camino y
  con el mismo cierre por observación. **Existe y se prueba con ids reales
  antes** de que el primer cambio real salga (A.4). **No hay reversa
  automática**: un `no_confirmado` produce `frenado(no_confirmado)` y aviso,
  y el dueño decide con `tools/precio_reversa.py --cambio-id …` (dry-run +
  huella + go), que lee el precio vivo antes de escribir y salta los cambios
  cuyo precio vivo ya no es `precio_despues`. Mismo criterio que el resto de
  Orbit: una lectura fallida nunca dispara una mutación.
- **Errores de API**: `error` con código (método + ruta + status, sin
  cuerpo); un reintento por día; al tercer día consecutivo
  `frenado(api_error)` y aviso. Nunca ráfagas.
- **Nada de escrituras masivas sin ceremonia**: la cuota diaria es el
  candado; el primer encendido a `live` se limita a 3–5 productos elegidos
  por el dueño y se mide 30 días antes de ampliar (S8).
- Logs: `sellerId` es público; el cuerpo del PATCH y los secretos jamás se
  registran.

## S7. Visibilidad y avisos (decisión 11: ningún silencio)

- `/precios` (server-rendered como `/cortes`): (a) productos con goal:
  margen de hoy, goal, precio actual, precio objetivo, modo, último cambio y
  su estado; (b) lo que el motor hizo o habría hecho hoy, una frase por fila
  («subí de 116.00 a 127.60 MXN para llegar al goal de 30%; hoy vas en
  24.1%»; en sombra, «subiría…»); (c) **no evaluados del día con su motivo**
  en palabras («costo vencido desde el 12-sep»); (d) bloque propio
  «precio divergente» con los dos precios y sus horas. Las frases con
  margen y costo viven **solo aquí** (túnel privado).
- `/api/dashboard/salud` → bloque `precios` **dentro de
  `plataformas.<plataforma>`**: `evaluados`, `movidos`, `sombra`,
  `no_evaluados` por motivo, `frenados`, `goal_inalcanzable`, cuota
  `{used, cap, fuente}`.
- Telegram, **un** sender `notifica_precio(tipo, …)` en `app/notifica.py`,
  fail-silent, en flanco una vez por racha (patrón `notifica_spapi_fallo`):
  - por **(plataforma, motivo)** con conteo y hasta 5 SKUs de ejemplo:
    `no_evaluado` 3 días seguidos, `goal_inalcanzable`, `frenado`
    («amazon_mx: 212 productos sin evaluar por ventas_sin_dato desde el
    12-sep (SKU-A, SKU-B, …); lista completa en /precios»);
  - por **producto**: `no_confirmado` y `buy_box_perdida` tras un cambio propio.
  - El texto lleva solo SKU/ASIN, plataforma, precios, estado y motivo:
    **nunca costo, margen, goal ni cuerpos de error** (builders puros con
    test que lo garantiza).

## S8. Fases, horario, encendido y aceptación

- **Horario.** La estimación se refresca cada 6 h a :45 y el costo del día se
  sella a las 08:15 UTC: el primer escenario `disponible` del día nace a las
  12:45. El motor corre a las **13:10 UTC** (`10 13 * * *`), después del
  ledger, de Pricing y de esa estimación, fuera del ciclo de Ads. Un solo
  valor en spec, plan y test.
- **Fase A — Motor FBA MX**: migración (`precio_goal`, `precio_decision`,
  `precio_cotizacion`, `precio_cambio`, enum, triggers, GRANTs por columna,
  `apply_cap_de_config` ampliado para `precio:<platform>`), reglas puras con
  banco de pruebas, cliente de escritura + escritura + reversa con sonda
  real, corrida diaria con lock y cuota propia, `/precios` + `/salud` +
  aviso, herramientas selladas, sombra en producción con **≥ 80% de los
  goals evaluados** (no solo «cinco corridas»), revisión independiente con
  catálogo de mutantes, encendido de 3–5 productos con go, **medición de 30
  días en dos cortes** (14: subida; 30: señal de ventas post-cambio).
- **Fase 0 — Márgenes US**: **después de D.2** (por atención del dueño y
  para no tocar `app/estimacion_*` mientras arranca A.x). Sellar la política
  fiscal de `amazon_us` (`I = P` sin IVA; retenciones = lo que 0.1 demuestre
  en Finances, o 0 declarado con razón; `PerItemFee` sugiere plan Individual:
  hecho a confirmar, no a suponer), FX MXN→USD para `C` con `fx_resolve`
  (par inverso solo con fuente; hoy el sync solo carga USD→MXN), cotización
  de fees US con `marketplace_id` parametrizado (hoy `MARKETPLACE_MX` es
  fijo). Sale como acta 0.4 de `margen-estimado-01`. **Sin esto, Fase B no
  arranca**; el motor MX no depende de ella.
- **Fase B — US**: mismos caminos; sombra, encendido con go.
- **Aceptación verificable** (por fase, en el plan): cada regla de S4 con su
  test rojo-primero y su mutante, incluidos los bordes exactos (tolerancia,
  40%, redondeos); dry-run de las herramientas no escribe; la reversa deshace
  con cierre por observación; `/salud` cuenta lo mismo que la base; ningún
  camino a Amazon fuera de `precio_write.py`; un día sin insumos produce N
  filas `no_evaluado` y cero llamadas de escritura; dos corridas simultáneas
  producen un PATCH.

## S9. Revisión de cinco perspectivas (2026-09-16) y qué cambió

Cinco revisores independientes (producto, arquitectura, seguridad, QA,
escéptico) leyeron el borrador v1.0 y el código real. Detalle y disposición
de los 40 hallazgos en `docs/evidencia/repricing-01/plan-validacion.md`.
Cambios que el dueño verá en el comportamiento, todos dentro de S1:

1. **Quedarse sin inventario o un listing inactivo ya no cuenta como
   «perder ventas»** (invalida la señal). Y la señal exige volumen mínimo,
   tres corridas seguidas y fechas excluidas configurables: con productos
   que venden 1–3 unidades por semana, la ventana de 15 días es ruido puro
   sin esas guardas.
2. **Si el producto pierde ventas justo después de una subida propia, el
   motor deja de subir y avisa** (regla 6). Antes seguía subiendo.
3. **El escalón del 10% aplica también al bajar** y hay movimiento mínimo:
   un goal mal cargado no puede tirar un precio 40% de golpe ni gastar
   cupos en centavos.
4. **La confirmación de un cambio se cierra al día siguiente** con la
   observación de Pricing, porque el PATCH de Amazon es asíncrono; y **no
   hay reversa automática**: un cambio no confirmado frena y avisa, la
   reversa la corres tú con la herramienta.
5. **La cotización de fees a un precio candidato vive en su propia tabla**;
   con la tabla de la estimación habría apagado el margen del día siguiente
   de cada producto tocado.
6. **El motor corre a las 13:10 UTC**, no a las 09:30: a esa hora el
   escenario del día aún no existe y todo saldría «no evaluado».
7. **Los avisos van por plataforma y motivo con conteo**, no uno por
   producto: un día sin ledger no manda 300 Telegrams.
8. **La medición del encendido es de 30 días**, no 14: en 14 no se puede
   ver la rama que protege tus ventas.

## Divergencias con el traspaso, declaradas

`docs/traspaso/MODULOS-AVANZADOS.md` §Módulo 1 pide estrategias «match/beat
competitor», «Buy Box oriented», «inventory aware», «time-based» y modos
agresivo/equilibrado/conservador. Por decisión del dueño (S1 #1, #5, #6) este
motor tiene **una** estrategia: margen goal con bajada solo por pérdida de
ventas. Las demás quedan fuera de alcance; no son deuda, son decisión. El
historial completo, el floor por margen, el anti-thrashing y el dry-run
obligatorio del traspaso sí están (S4 #9–#12, S5, S6).

Lecciones del sistema viejo que este spec convierte en regla: la fórmula que
ignoraba comisiones y dejó 46 días sin propuestas (aquí `F` siempre viene de
una cotización real y su ausencia bloquea, no calla; y el horario garantiza
que el escenario exista); el paso de «sustain» que midió −12% (aquí el
precio objetivo se resuelve y el escalón acota en ambas direcciones); el
stop-loss prometido que nunca corrió (aquí la reversa se prueba con ids
reales antes del primer cambio y no depende de una lectura que puede fallar).
