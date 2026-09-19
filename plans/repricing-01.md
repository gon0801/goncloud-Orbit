# REPRICING 01 — Motor de precios por goal de margen (M1 / AUTO-07 / ORBIT 09)

Version: 1.3, 2026-09-18 UTC. Estado: **plan sellado sobre el spec v1.3
(decisiones 1–15 del dueño, dos rondas de revisión de cinco perspectivas);
Fase 8 cerrada** (A.0, A.2 y A.3 en `master`; evidencia de E.1 mergeada, fila
abierta por E.0b). Base: `origin/master` `6e8d094`. **Qué cambia en la v1.3**
(revisión del lead de la Fase 8 contra el código, 2026-09-18 UTC): fila **D.0**
nueva (base de producción lista para la sonda) y A.4 con sus prerrequisitos
reales (A.1 y D.0, no solo A.3); **E.0 partida** en E.0a (veredicto, lead) y
E.0b (regla en la ingesta, Muse); hechos 20–22 medidos por E.1; salvedades que
el código ya tiene y las celdas no decían; y la nota de pureza de `app/precio/`
para A.1, A.5 y A.7. Ninguna decisión del dueño se reabre.
Spec: `docs/superpowers/specs/2026-09-15-repricing-01-design.md` (manda sobre
este plan). Precedencia: `docs/CONTEXTO.md` (reglas 1–10) > `plans/ROADMAP.md`
> spec de márgenes y su acta 0.3 > spec de este plan > este plan.
Tracker: `ORBIT 09 — Módulo Repricing` y `AUTO-07 Repricing (plan formal)`.
`team_validation_mode: subagent`. **Dos rondas, ambas cerradas:** la primera
sobre las fases A y 0/B (40 hallazgos,
`docs/evidencia/repricing-01/plan-validacion.md`); la segunda sobre E, B y M
(`docs/evidencia/repricing-01/plan-validacion-ebm.md`), que **invalidó cuatro
afirmaciones que la v1.1 de este plan daba por medidas** — el cobro de envío en
US, la cobertura que habilita la fase E, la justificación del percentil y el
canal por publicación. Las correcciones están en los hechos 13–19 y en las
fases E, B y M. Una de ellas, la duplicación entre fuentes de `shipping_fee`,
**quedó en disputa entre dos mediciones** y por eso abre la fase E como tarea
E.0a (veredicto) y E.0b (regla) en vez de escribirse como hecho.
No hay fechas ni presupuestos en este plan. Ningún secreto en el repo.

## Resultado y límites

Cada producto con un goal de margen fijado por el dueño llega a ese margen por
precio, sube solo cuando le falta, baja solo cuando pierde ventas de verdad y
nunca abajo del goal. El alcance final es **toda publicación activa del
negocio**: Amazon México y Estados Unidos en FBA y FBM, y Mercado Libre. Se
llega por fases y **ninguna publicación queda invisible** mientras tanto: la
que no tiene motor todavía aparece contada, con su motivo y con la fase que la
habilita.

Fuera de alcance, declarado: promociones, kits, decidir por inventario (solo
invalida la señal de ventas), elasticidad, modos «agresivo/equilibrado» del
traspaso, reversa automática.

## Decisiones ya cerradas (no se reabren)

Las quince de S1 del spec, con texto literal del dueño. Resumen: proteger
margen; goal por producto; se compara con el margen estimado; sube aunque
rebase a la competencia; baja solo si caen las unidades de 15 días contra el
promedio de 60; automático dentro de límites con sombra primero; precio
resuelto, no escalera; ningún silencio; cuota propia. **Ampliación del
2026-09-16**: el envío FBM se mide de lo que Amazon cobra por las etiquetas
(13), toda publicación activa queda contemplada (14), y Mercado Libre entra al
alcance (15).

Umbrales aceptados como **config** (`config_version.settings`, tipo JSON
número), con clave y cota; fuera de cota = `ValueError` ruidoso al leer:

| Clave | Valor inicial | Cota |
|---|---|---|
| `precio_caida_ventas_pct` | 0.40 | 0.10–0.90 |
| `precio_senal_dias` | 3 | 1–7 |
| `precio_u60_min` | 20 | 1–1000 |
| `precio_fechas_excluidas` | `[]` | rangos ISO válidos |
| `precio_escalon_max_pct` | 0.10 | 0.01–0.25 |
| `precio_movimiento_min_pct` | 0.01 | 0–0.10 |
| `precio_movimiento_min_abs_mxn` / `_usd` | 1.00 / 0.10 | > 0 |
| `precio_tolerancia` | 0.005 | 0–0.05 |
| `precio_dias_entre_cambios` | 7 | 1–30 |
| `precio_cap_amazon_mx` / `_us` / `_meli` | 5 | 0–20 |
| `precio_goal_min_pct` / `precio_goal_max_pct` | 0.10 / 0.60 | 0 < min < max < 1 |
| `precio_freno_cambios` | 3 | 2–10 |
| `precio_aviso_dias_sin_evaluar` | 3 | 1–14 |
| `precio_freno_dias_error` | 3 | 1–14 |
| `precio_divergencia_max_pct` | 0.01 | 0–0.10 |
| `precio_envio_estadistico` | `mediana` (sellado en S10) | solo `mediana`; `p90` y máximo son diagnóstico, no configuración |
| `precio_envio_ventana_dias` | 90 propuesto; lo sella E.2 | 30–365 |
| `precio_envio_min_ordenes` | 6 | 6–50; bajar de 6 exige acta nueva del dueño y delta en S10 |
| `precio_envio_min_salida` | 3 | 1 < salida < entrada |
| `precio_envio_rezago_dias` | sella E.2, del p90 de **ingesta** (hecho 20: el de emisión no se reproduce) | 0–120 |
| `precio_catalogo_max_dias_sin_reportar` | 3 | 1–14 |

## Hechos verificados que condicionan el plan (2026-09-15/16)

**El catálogo** (ingesta de listings del bridge, fresca al 2026-09-16 00:36;
`AMAZON_NA` = FBA, `DEFAULT` = FBM):

| Plataforma | FBA activas | FBM activas | Total activas |
|---|---|---|---|
| `amazon_mx` | 171 | 113 | 284 |
| `amazon_us` | **0** | 109 | 109 |
| `meli` | — | 137 (caché al 2026-05-01) | 137 |

1. **Estados Unidos no tiene ninguna publicación activa en FBA** (0 de 109;
   las 82 FBA que existen están inactivas). La fase de US tal como estaba
   escrita —vía cotización de fees FBA— no habría cubierto ni una
   publicación: **US depende de la fase E (envío FBM), no de la ruta FBA**.
2. La estimación por venta cubre hoy **221 SKUs de MX, todos FBA**; cero en
   US. Con el plan v1.0 el motor habría alcanzado 171 de 530 publicaciones
   activas (32%).
3. **El costo de las etiquetas ya está en Orbit** (decisión 13): `ledger_event`
   `fee_type = 'shipping_fee'`, todo registrado en MXN y con **signo negativo**
   por la convención del ledger. **Corregido el 2026-09-16**: la cifra «US 710
   cargos por 192 607» que traía la v1.1 de este plan era la **suma cruda con
   duplicados**. Aplicando la regla de deduplicación que el escéptico propuso
   darían 185 488, pero **ese total es provisional**: si las tres fuentes son
   componentes distintos y no duplicados, el total correcto es el crudo. Lo
   resuelve E.0a (punto 14); hasta entonces ninguna de las dos cifras se sella.
4. **La atribución del envío al producto es exacta, pero un envío no es una
   fila**: el cargo trae `order_id` y no `product_id`, y 431 de 439 órdenes con
   etiqueta en MX y 344 de 347 en US son de un solo producto y una sola unidad,
   así que la atribución producto↔orden sí es exacta. Lo que **no** es exacto es
   contar filas: en US **152 de 173 órdenes traen 2 cargos y 18 traen 3**. La
   muestra agrupa por orden antes de percentilar.
5. **Orbit sí trae ya el canal por publicación.** `estimacion_oferta_observation`
   tiene `canal` y `mapear_canal` ya hace `AMAZON_NA` → `fba` y `DEFAULT` →
   `fbm`: 7 514 filas sobre 221 listings, fresca al 2026-09-17 **UTC** — la ronda se fechó el 16 en hora local y las
   lecturas contra producción cayeron ya en el 17 UTC, que es la fecha efectiva
   de extracción de todos los números de esta tanda. Lo único que
   impide que lleguen FBM y US son ~8 líneas de `_motivo_universo`. **Corrige la
   v1.1 de este plan**, que daba el canal por ausente y presupuestaba una
   migración y un cambio en `app/listings.py` para traerlo: A.7 se queda solo
   con `app/precio/cobertura.py`.
6. **Mercado Libre**: el acceso a la API existe y funciona a diario
   (`ClienteMeli` en `app/reputacion_clientes.py`, **GET-only por diseño**,
   con refresco de token), pero la caché de publicaciones del bridge
   (`meli_listings_cache`, 137 items) no se refresca **desde el 2026-05-01**,
   `meli_sku_mapping` está **vacío** (sin puente al costo), y hay **cero filas
   `meli`** en `ledger_event`, `listing`, `ad_entity` y las observaciones de
   precio. **Pero el dinero sí llega**: `ingest_run` reporta cada día
   `5126x plataforma meli excluida`, creciendo 15–20 filas por día — la ingesta
   contable lo recibe y lo descarta en una rama. Abrir el ledger de MeLi es
   quitar esa rama y mapear (horas), no construir una ingesta. `estimacion_canal`
   solo admite `fba|fbm` y necesita su propio valor.
7. La estimación corre cada 6 h a :45 y el costo del día se sella a las 08:15
   UTC: el primer escenario `disponible` nace a las 12:45. **Horario del
   motor: 13:10 UTC.**
8. Unidades por producto: `ledger_event` `kind='sale'` con `quantity` y
   `product_id`, sin `listing_id` ni ASIN; hay productos con dos ASINs en la
   misma plataforma. **La señal de ventas es por `(product_id, platform)`.**
   Volumen real: ~1 650 ventas en 290 días sobre ~370 productos.
9. **No existe camino de escritura de precios** en ninguna plataforma.
   `SpapiClient` solo tiene `get` y `post_fees`; el PATCH de Listings Items es
   **asíncrono** (`202 ACCEPTED` + `submissionId`). `ClienteMeli` rechaza todo
   método que no sea GET.
10. `apply_quota_state` **no admite un motor nuevo tal cual**:
    `apply_cap_de_config` (0002) es un `CASE` cerrado y el trigger hace
    `RAISE` con clave NULL. `app/apply.py` importa `app.ads.write`: el motor de
    precios no importa `app.apply`.
11. `estimacion_fee_observation` exige por FK que el precio cotizado sea el de
    la oferta y solo `app_ingest` inserta; su lector toma la cotización más
    reciente: una cotización del motor ahí apagaría la estimación del día
    siguiente. De ahí `precio_cotizacion`.
12. `ads_optimizer_lock` sirve como lock de base; `orbit_admin` hereda
    `app_decide` (el motor corre con `ORBIT_DSN_DECIDE`); `app/notifica.py`
    tiene el patrón de flanco por racha y es fail-silent;
    `tests/test_architecture.py` tiene candados de pureza, escritor único e
    imports de escritura que **no se tocan**: los nuevos van en paralelo.

**Medidos en la segunda ronda de revisión (2026-09-16), sobre las fases E, B y
M.** Los seis invalidan algo que la v1.1 de este plan daba por cierto:

13. **La fase E no habilita 222 publicaciones; habilita ~20 hoy.** Contando
    **órdenes** con etiqueta en 90 días, solo **7 productos de MX y 9 de US**
    llegan al mínimo de 6 envíos; cruzados con publicaciones activas son **10 y
    10**. El techo no lo pone el umbral sino el volumen: de 264 activas de MX
    solo 117 vendieron algo en 90 días y 15 vendieron ≥ 6 unidades; en US, 48 de
    106 y 10. La palanca medida es la **ventana**: 180 días lleva los productos
    de 7 a 14 y de 8 a 17; 365 días, a 17 y 22. El percentil no mueve nada.
    **Ojo con el 9 y el 8**: la misma medición reporta 9 productos de US a 90
    días en un corte y 8 en la serie de ventanas. No se reconcilió y no se
    sella ninguno: **E.1 mide las tres ventanas con un solo filtro** y su
    evidencia deja el número, con el filtro escrito, antes de que E.2 decida.
14. **Tres fuentes de envío se solapan; si duplican dinero está EN DISPUTA.**
    A 90 días en US: `finance:ShippingHB` (170 filas, promedio −85.23),
    `finance:LabmanLabelPurchase` (54, −441.60) y el reporte `shipping_label`
    (137, −462.78). Dos mediciones independientes del 2026-09-16 no coinciden:
    el revisor escéptico reporta **18 órdenes (10.4%) con la misma etiqueta por
    dos fuentes** y 7 701 MXN duplicados; la verificación independiente, con
    prueba de **monto exacto**, encuentra **cero** en US y MX a 90 y 180 días.
    Coinciden en lo demás: 152 órdenes de US con 2 cargos y 18 con 3, y
    137 + 54 = 191 = 173 órdenes + 18, que es el número en disputa. Una orden de
    muestra con tres cargos trae −127.12, −102.99 y −2 149.87: montos que no se
    parecen, lo que explica que la prueba exacta no encuentre nada y deja dos
    lecturas abiertas. **Resolverlo es el primer entregable de la fase E
    (E.0a)**; hasta entonces `L` en US no se sella. El origen está **aguas
    arriba** (la contabilidad que alimenta el ledger), no en Orbit.
15. **La dispersión que justificaba el percentil no existe.** MX es tarifa plana
    (p50 = p75 = 95.00 de dic-25 a may-26; 91.00 desde jun-26) y en US el
    p75 − p50 va de 2 a 13 MXN: elegir p75 en vez de p50 mueve el margen entre
    0.00 y 0.43 puntos (mediana 0.17) en 15 de 16 productos. Y el p75 móvil de
    90 días **tardó ~75 días** en registrar el cambio de 95 a 91 del 1-jun (el
    p50 tardó 45), así que ante una **subida** de tarifa subestima `L` durante
    ~75 días. Y el rezago son **dos medidas distintas**: el de *ingesta*, entre
    el `event_date` y la corrida que trajo la fila, 1 a 11 días; y el de
    *emisión*, entre la fecha del envío y el `event_date` con que Amazon lo
    cobra, p50 27 días en MX y 22 en US, p90 ~57–59, máximo 73. La ventana la
    cierra el segundo, y `precio_envio_rezago_dias` sale de su p90.
16. **En US el cobro de envío al cliente es NULL, no cero.** De 351 ventas de US
    en 180 días, **ninguna** trae `shipping_price` ni `item_price`; la causa es
    estructural: `_money_from_payload` descarta el desglose cuando el
    `CurrencyCode` del payload (USD) no coincide con la moneda del `amount`
    (MXN). En MX, **68 de 641** traen `shipping_price` y las 68 son > 0 —
    misma poblacion y misma ventana que el spec. **Corrige
    la v1.1**, que declaraba «el cliente pagó cero envío en las 349 ventas».
17. **La rama de bajar precio no se dispara con el volumen actual.** En
    `[hoy−75, hoy−16]` MX movió 199 unidades entre 74 productos (2.7 de
    promedio) y US 120 entre 35. **Un solo producto del negocio** llega a
    `u60 ≥ 20` (MX 1621, `u60 = 25`). El criterio (c) de D.2, E.5, B.2 y M.6 va
    a salir «no ocurrió» en las cuatro mediciones de 30 días.
18. **El margen de US no es el problema; Ads sí.** Por unidad, sobre los 9
    productos de US con muestra, la contribución va de **33% a 63%, mediana
    ~50%**. El envío (94 035 a 90 días) supera a la comisión (65 067) pero no se
    come el margen. **Ads US gastó 106 721 sobre 446 129 de ventas: TACoS
    23.9%, contra 9.0% en MX.** Un goal pre-Ads de 30% en US es ~6% después de
    Ads. Ya medido para el acta 0.2: retención ISR US 2.07% de ventas (MX
    1.99%), `tax_withheld` US 6.56%.
19. **Hay dos denominadores de «publicación activa» y no coinciden.** La fuente
    propia de Orbit (`spapi_listing_estado_observation`) dice **264** vendibles
    en MX y **106** en US; la caché del bridge dice **284** y **109**. 20 de
    diferencia en MX (7%). No es hueco de carga: son dos definiciones. AC17 no
    puede pasar en ninguna fase hasta que el spec nombre una fuente canónica —
    y ya la nombra (S10): la propia de Orbit.

**Medidos por la Fase 8 (E.1, producción, 2026-09-17 UTC;
`docs/evidencia/repricing-01/E.1/medicion.md`).** Condicionan el acta E.2:

20. **El rezago de emisión del hecho 15 no se reproduce.** Entre la fecha de
    la venta y el `event_date` del cargo: p50 0, p90 0–2, máximo 3 días, con
    cualquiera de las tres fechas disponibles. El que sí existe es el de
    **ingesta** incremental: p90 4.2 días en MX y 13 en US (máx 7 y 16). La
    ventana la cierra ese rezago, y `precio_envio_rezago_dias` sale de ahí, no
    de los 27/22 días del hecho 15. La historia de `shipping_fee` empieza el
    2025-12-04: «365 días» son **287** de datos.
21. **Productos que alcanzan el mínimo de 6 envíos, por ventana:** MX 7 / 14 /
    17 y US 9 / 17 / 22 a 90 / 180 / 365 días; el 9 y el 8 del hecho 13 se
    reconciliaron en **9** con un solo filtro. La mediana casi no se mueve con
    la ventana (MX 91 en las tres, tarifa plana; US 525–550 por producto). El
    parpadeo con corte seco es 9 de 13 en MX y 10 de 15 en US; con histéresis
    6/3 baja a 2 y 3, y ninguna salida completó una reentrada.
22. **148 órdenes con cargo de envío tienen venta sin `product_id`** (86 MX,
    62 US): no se pueden atribuir a producto y quedan contadas aparte. Y el
    hecho 14 sigue en disputa: 53 de 54 pares `LabmanLabelPurchase`/`shipping_label`
    difieren ≤ 1 % (parecen el mismo cobro), mientras los 521 pares
    `ShippingHB`/`shipping_label` difieren > 5 % en el 100 % (parecen
    componentes distintos). Sin veredicto: es E.0a.

## Diseño (lo que el implementador no decide)

Está en el spec S2–S11. Invariantes que el revisor verifica en cada fila:

- **Un camino de escritura por plataforma**: `app/spapi/write_client.py` y, en
  su fase, `app/meli/write_client.py`, default-deny, importados solo por su
  módulo de precios. Los clientes de lectura no ganan verbos de escritura.
- **Un escritor de goals**: `app/precio/goals_write.py`, con candado propio.
- **Append-only** en `precio_goal` (solo cierra `valid_to`), `precio_decision`,
  `precio_cotizacion`, `precio_envio_muestra`; `precio_cambio` solo avanza
  `pendiente → enviado | error` y `enviado → confirmado | no_confirmado`.
- **Dinero con moneda** (regla 4), `currency NOT NULL`.
- **Sin insumo, sin decisión**: `no_evaluado(motivo)` es una fila.
- **Cobertura que cuadra** (decisión 14): activas = evaluadas + no evaluadas +
  sin goal + fuera de alcance, por plataforma, en cada corrida.
- **`L` medido lleva su muestra**: ninguna decisión FBM sin `envio_muestra_id`.
- **Sombra fiel**: cambios virtuales que consumen cooldown y freno.
- **Reversa antes y nunca automática**, por plataforma.
- **Cuota propia** `precio:<platform>`; `no_confirmado`, `error` y reversas
  consumen; prioridad explícita registrada.
- **Concurrencia**: advisory lock + `ads_optimizer_lock`; UNIQUE por día;
  índice único parcial de cambio abierto; orden INSERT+COMMIT → escritura.
- **Telegram sin dinero**: solo SKU/ASIN, plataforma, precios, estado, motivo.
- **`app/precio/` es puro y el candado lo mide con `rglob`** (Fase 8,
  `tests/test_architecture.py`): ningún módulo ahí importa red, base ni
  reloj. Los módulos con I/O del motor que el plan nombra dentro de
  `app/precio/` (`goals_write.py` en A.1, `corrida.py` y `cuota.py` en A.5, la
  lectura de fuentes de A.7) **entran a una lista explícita de excepciones
  del candado, uno por uno, cada uno con su propio candado** (qué importa y
  qué escribe), y el resto de la carpeta sigue bajo `rglob`. La lógica que
  pueda ser pura (el recuadro de cobertura, el reparto de cupo, la muestra de
  envío) se escribe pura y se prueba sin base.

## Etapas y tareas

### Fase A — Amazon México, FBA (171 publicaciones activas)

| ID | Tarea | DoD (verificable) | Deps | Estado |
|---|---|---|---|---|
| A.0 | [stage:implementacion] [lane:gate] [tdd:required] **Migración `00NN_precio.sql`** (Muse): enum `precio_mode`; `precio_goal` (FK compuesta `(listing_id, platform)`, CHECK banda de goal, CHECK `live ⇔ go_literal`, índice parcial un vigente, trigger «solo cierra vigencia»); `precio_decision` (`UNIQUE (listing_id, platform, decision_date)`, `decision_date` por trigger UTC, `currency NOT NULL`, `canal`, `envio_muestra_id`); `precio_cotizacion`; `precio_envio_muestra`; `precio_cambio` (transiciones por trigger, índice único parcial de cambio abierto); GRANTs por columna; `apply_cap_de_config` ampliado con `precio:amazon_mx`, `precio:amazon_us`, `precio:meli`; bloque DO bajo `SET ROLE`. | Rojo-primero con PostgreSQL real: `UPDATE precio_goal SET margen_goal_pct` rechazado; segundo vigente rechazado; `live` sin `go_literal` rechazado; `valid_to` se fija una vez; `precio_cambio` `confirmado → pendiente` y `UPDATE precio_despues` rechazados; segundo cambio abierto del mismo listing rechazado; `decision_date` del cliente ignorado; `INSERT apply_quota_state (motor='precio:amazon_mx')` nace con `cap` de config y sin clave revienta; `app_read` no inserta; `docs/DATABASE.md` y `docs/DEPLOY.md` §«Migración 00NN» | — | cc:完了 `14accfa` (PR #298). Salvedades: la migración 0039 NO está aplicada en producción (fila **D.0** desde la v1.3, la corre el dueño con `!`; D.1 solo la verifica); S5 se desvía a propósito (sobrevive `precio_decision.cotizacion_id`, se quita `precio_cotizacion.decision_id`); `precio_envio_muestra` con la forma literal de S5 (la ajusta E.3); `apply_quota_fila_desde_config` intacto; `NEW.goal := v_goal` pisa en silencio el goal del cliente (se decide en A.5); el test del segundo cambio abierto solo cubre `pendiente` + `pendiente` en la base (`enviado` + nuevo lo cubre el índice y las funciones de A.3). **Salvedades que el código trae y la celda no decía (v1.3)**: `ALTER TABLE listing ADD UNIQUE (id, platform)` (la FK compuesta lo exige; no es puramente expansiva); EXCLUDE gist `precio_goal_sin_solape` además del índice parcial; `valid_to >= valid_from` (goal anulado el mismo día); `precio_decision.mode NOT NULL` y trigger `precio_decision_coherente` que exige goal vigente **del mismo mode** y coteja `product_id` contra el listing; trigger `precio_cambio_nacimiento` (virtual nace `confirmado/virtual`, reversa sin `decision_id`, real solo `pendiente`); el cambio ata `precio_despues = p_aplicado` de su decisión y la reversa `precio_despues = precio_antes` del revertido; la banda del goal es el literal `0.10–0.60` en el CHECK (la config `precio_goal_min/max_pct` se replica en A.1: dos fuentes, declarado) |
| A.1 | [stage:implementacion] [lane:gate] [tdd:required] **`app/precio/goals_write.py` + `tools/precio_goal.py`** (Muse): sembrar goal (`--listing-id --platform --goal-pct 30.00`), lote CSV deduplicado, `--mode live` con ceremonia completa, `--mode shadow` sin ceremonia, `--cerrar`. Dry-run imprime `m_actual` y `P*` por fila y aborta si `abs(P* − P_actual) > 25%` salvo `--confirmar-salto`. Solo `app_admin`, cero Amazon. | Dry-run no escribe; `--go` escribe exactamente N filas; huella distinta aborta; CSV con fila repetida aborta antes de la huella; `--goal-pct 0.30` rechazado; goal fuera de banda rechazado en Python y en la base; `live` sin go rechazado; salto > 25% sin confirmación aborta; candado `_IDENT_PRECIO_GOAL` pasa y **falla** con un `UPDATE precio_goal` crudo sembrado en `tools/` | A.0 | cc:完了 `662db38` (PR #303) + `eeefb72` (PR #307, cierre r1 de la revisión de cierre). Salvedades: nadie siembra goals en producción todavía (las tablas llegan con **D.0**; el goal del producto controlado es del dueño); `--mode live` sin escenario `disponible` aborta y `shadow` sigue con aviso (decisión del lead, DoD d); con escenario pero sin referencia derivable (`fee_error`, `margen_imposible`) imprime `m_actual` con `P*` ausente y el motivo, y sigue; el go escribe fila por fila, sin transacción global, y lo que el dry-run puede ver (fila repetida, goal vigente, listing inexistente en la plataforma, banda, salto > 25 %) aborta antes de la huella; goal y `m_actual` se imprimen en por ciento (`contribucion_pct` está en por ciento); `--sku` (aborta con 0 o 2+ publicaciones) y `--confirmar-salto <listing_id>` por listing según el spec S3; la ceremonia difiere de la reversa en que `shadow` no lleva `--go` y `--huella` sin `--acepto-mutacion-real` también aborta; el candado de `goals_write.py` aplica `PROHIBIDOS_PRECIO` menos `psycopg` y los detectores de reloj, entorno e import dinámico; CodeRabbit sin cuota en #303 y en la primera vuelta de #307 |
| A.2 | [stage:implementacion] [lane:gate] [tdd:required] **Reglas puras** (Muse): `app/precio/{tipos,reglas,objetivo,ventas,config}.py`; `objetivo` con forma cerrada (`ref`, `fijo` de `fee_details`, `L` por canal) y ≤ 2 cotizaciones reales; `ventas` con días cubiertos, inventario y racha; `config` con cotas. Banco de pruebas con los fixtures de la acta. | Rojo-primero por regla de S4 con bordes: insumo faltante → motivo exacto; divergencia 1.01% sí y 0.99% no; goal 30% con `m = 29.50%` → `mantener` y `29.49%` → `subir`; `P*` hacia arriba con `m(P_aplicado) ≥ goal` y tope hacia abajo (`116.01·1.10 → ≤ 127.61`); `tax_amount` → `impuesto_fee_pendiente`; no concilia → `fee_error`; `u60=600, u15=90` no dispara y `89` sí; ventanas `[hoy−15,hoy−1]` y `[hoy−75,hoy−16]`; hueco de ledger, `u60=19`, `n15=9`, 74 días, un día sin stock, listing inactivo, racha 2 de 3 → cada uno su `sin_dato`; subida confirmada hace 10 días + caída → `frenado(perdiendo_tras_subida)`; `no_confirmado` de hace 6 días → `mantener(cooldown)`; 3 sin converger → `frenado`; `P* > 2P` y `P* ≤ C + L`; prioridad registrada; `shadow` igual con `aplicado=false`. Todo en `Decimal`. Catálogo de mutantes propio y test de pureza sobre `app/precio/*` | A.0 | cc:完了 `39cba88` (PR #299). Salvedades: `bajar` también verifica con cotización real (más allá de S4 #4); escenario incoherente → `no_evaluado(escenario_incoherente)` con candado de dirección; cobertura del ledger según S2 (≤ 3 días); el cupo solo lo consumen `subir`/`bajar` en `live`; ninguna clave `precio_*` sembrada todavía (sin ellas `leer_config` falla ruidoso); la rama de `bajar` casi no se dispara con el volumen de hoy (residual del spec); docstrings con acentos contra AGENTS.md (cosmético). **v1.3**: `L` por canal NO está en las reglas (`canal` viaja en `EntradaDecision` y nadie lo lee; `L` es el importe que pone quien arma el escenario: E.3/E.4); literales del spec fijos en código, no config: 22 días del freno, `n15 < 10`, 75 días de historia, tolerancia de coherencia 0.01; `repartir_cupo` revienta con monedas mixtas y no valida plataforma (A.5 no mezcla plataformas en una llamada); monedas fuera de MXN/USD → `escenario_incoherente` (MeLi necesita su mínimo absoluto); los frenos #6 y #10 ignoran cambios anteriores al goal vigente (`goal_vigente_desde`); `no_converge` con distancias iguales cuenta como «sin acercarse»; entrada principal `decidir(entrada, *, hoy, config, cotizaciones) -> Decision | PideCotizacion` |
| A.3 | [stage:implementacion] [lane:gate] [tdd:required] **Cliente de escritura, escritura y reversa de Amazon** (Muse): `app/spapi/write_client.py` (default-deny, `patch_listing` con `validar_patch_listings`, 401 un refresh, 429 un reintento, sin instancia compartida); `app/spapi/precio_write.py` (`leer_precio_vivo`, `cambiar_precio` con ack → `enviado`, readback informativo, `cerrar_por_observacion`, `revertir`); `tools/precio_reversa.py --cambio-id …`. Commits: write client → reversa → escritura. | Rojo-primero con cliente falso: ack `ACCEPTED` + GET viejo → `enviado`; ack error + GET nuevo → `error`; ack ok + GET distinto de ambos → `enviado` con readback ok; 429 agotado → `readback_estado=fallido` y cero escritura extra; observación D+1 igual → `confirmado`, distinta → `no_confirmado`; reversa se cierra por observación; reversa por lote salta el cambio con precio vivo distinto; dry-run no llama al cliente; **tres candados** con fuga sembrada (`httpx.patch` crudo, import del write client, imports de los tools); forma del parche `pendiente_sonda` hasta A.4; el cuerpo nunca se loguea | A.0 | cc:完了 `efc0555` (PR #300). Salvedades: la forma del cuerpo del PATCH sigue `pendiente_sonda` (A.4) y el go del tool aborta antes de insertar; ningún precio se mueve; la reversa NO entra el mismo día del cambio (el índice único de S5 bloquea mientras el original está abierto; entra al día siguiente tras el cierre por observación); `cambiar_precio` no escribe si el precio vivo ya no es el `p_actual` de la decisión; `leer_precio_vivo` usa Pricing (ofertas), no el GET del item (A.4 lo confirma); una `pendiente` huérfana por muerte del proceso entre COMMIT y PATCH se recupera en A.5. **v1.3**: `cambiar_precio(conn, decision_id, *, lector, escritor, construir_cuerpo=None, ahora=None)` exige `conn.autocommit` y una decisión `subir|bajar` en `live`; NO acepta `limitador` (el GET de Pricing previo al PATCH y el readback van sin cubo; `revertir` sí lo acepta): A.5 lo agrega; el dry-run de `precio_reversa.py` sí hace GETs de Pricing por cambio (cumple «no llama al escritor», no «cero red»); la reversa el mismo día SÍ entra si el cambio original está en `error` y el vivo coincide; `cerrar_por_observacion` toma la observación más reciente en `(día_envío, hoy]`, no estrictamente D+1; `FORMA_PARCHE` es una constante de módulo y `construir_cuerpo_parche` levanta incondicionalmente: sellarla (A.4) es escribir el cuerpo real ahí con el importe como `str` (el escritor prohíbe `Decimal`/`float`), cambiar el literal, la guarda (l.134) y el docstring (l.9), y ajustar **los dos** tests que fijan `pendiente_sonda` (`tests/test_precio_write.py` l.557 y l.1141); los `monkeypatch` de `construir_cuerpo_parche` siguen válidos |
| A.4 | [stage:verificacion] [lane:release] [tdd:skip:sonda] **Sonda de escritura y reversa con ids reales** (dueño con `!`, go literal; lead lee): un producto controlado MX; `+0.01` por `precio_write.py` y reversa por la herramienta. Sella la forma exacta del parche. **Prerrequisitos (v1.3), que la v1.2 no nombraba**: la 0039 aplicada y las claves sembradas (**D.0**); un goal `live` con go literal para el producto controlado, sembrado con `tools/precio_goal.py` (**A.1**); y una fila de `precio_decision` `subir` en `live` con la cuenta completa (los diez importes, `p_actual` = precio vivo, `p_aplicado` = `p_actual + 0.01`), **insertada a mano como `app_decide`** porque la corrida (A.5) todavía no existe — el INSERT literal va en la evidencia. La sonda es iterativa: se propone el cuerpo inyectando `construir_cuerpo=` a `cambiar_precio`, y cuando Amazon lo acepta y el GET lo confirma, se sella en un PR `fase<N>/a4-forma-parche` que escribe el cuerpo en `construir_cuerpo_parche`, cambia `FORMA_PARCHE` y ajusta los tests que fijan `pendiente_sonda`; ese PR es la única edición de código de A.4 y lleva el loop normal. | `docs/evidencia/repricing-01/A.4/` en orden temporal: observación del día anterior = `P`; fila `pendiente` con `enviado_at` antes del ack; ack con `submissionId`; GET con `lastUpdatedDate > enviado_at` y precio `P + 0.01`; ack y GET de la reversa; observación del día siguiente = `P`; **control negativo** (otro listing propio sin cambio conserva su precio); PR de sello mergeado con `git grep -n pendiente_sonda -- app/spapi/precio_write.py` **sin línea de código** (solo el comentario ancla de l.59–61 puede quedar), `FORMA_PARCHE` sellado y `construir_cuerpo_parche` devolviendo el cuerpo real; **sin E/A.4 no hay D.2** | A.1, A.3, D.0 | cc:TODO |
| A.5 | [stage:implementacion] [lane:gate] [tdd:required] **Corrida diaria** (Muse): `app/precio/corrida.py` (claim en `ads_optimizer_lock` `precio:<platform>` + advisory lock; cierra por observación los `enviado` de ayer; decide para todos los goals vigentes; ordena por prioridad; aplica solo `live` bajo `app/precio/cuota.py` sin importar `app.apply`; en `shadow` cambios virtuales; orden INSERT+COMMIT → PATCH → sello), `app/cli.py precio --platform` y `precio --reporte --desde --hasta`, cron `10 13 * * *` con `flock` y log, claves de config. | Rojo-primero: día sin insumos → N `no_evaluado` y cero escrituras; cuota saturada → `mantener(cuota)` por prioridad con las de Ads intactas; reversas consumen cuota; segunda corrida del día no decide; **dos hilos con PostgreSQL real → exactamente un PATCH**; `shadow` nunca escribe en Amazon y sí consume cooldown; `enviado` de ayer se cierra antes de decidir; freno tras 3 días de `error`; línea de crontab pinzada por test; umbral fuera de cota → `ValueError` al arrancar | A.1, A.2, A.3 | cc:完了 `53c6067` (PR #309). Salvedades: nada corre en producción (la 0039 llega con **D.0**; el cron `10 13 * * *` queda documentado en `docs/DEPLOY.md` y lo instala **D.1**; la forma del parche sigue `pendiente_sonda` hasta **A.4**, así que antes de A.4 solo goals en `shadow`); el freno por error (`frenado(api_error)`) está implementado y probado, pero no se alcanza con el cooldown sellado (S6 contra S4 #9 y la lista Reject: lo decide el dueño); la cuota es monótona por el trigger de la 0002 (cada intento reservado consume aunque `cambiar_precio` salte sin escribir); advisory lock de sesión (`pg_try_advisory_lock`) en vez del transaccional de S5, porque la corrida va en autocommit; `app/precio/tipos.py` solo suma `api_error` y `no_confirmado` a `MOTIVOS_FRENADO` (desviación declarada); `cerrar_por_observacion` cierra los `enviado` de todas las plataformas y el resumen los atribuye a la que corre; **el `diagnostico` de `reglas.decidir` (los dos precios con sus horas de AC16) no se guarda ni se registra** (media de R.1, pendiente para la fila que toque la corrida o la 0039); lecturas de historia completa por goal y tests que pueden fallar al cruzar las 00:00 UTC. R.1: kimi sin altas ni medias sobre el squash; re-mutación 93/94 con un equivalente. |
| A.6 | [stage:implementacion] [lane:gate] [tdd:required] **Pantalla y avisos** (Muse): `/precios` con los cinco bloques de S7 (cobertura arriba), bloque `precios` en `/salud` dentro de `plataformas.<p>`, **un** sender `notifica_precio(tipo, …)` en flanco por racha: por `(plataforma, motivo)` con conteo y 5 SKUs para `no_evaluado`/`goal_inalcanzable`/`frenado`, por producto para `no_confirmado`/`buy_box_perdida`; fail-silent. | Rojo-primero: `/precios` 200 con los cinco bloques y frases con números del fixture; `/salud` cuenta lo mismo que `precio_decision`; 200 productos con el mismo motivo → **un** aviso; builder de texto **sin** costo, margen, goal ni cuerpo de error; un fallo del sender no tumba `correr` | A.5, A.7 | cc:完了 `510beda` (PR #312). Salvedades: sin la 0039 (hoy en producción, hasta **D.0**) `/salud` deja el bloque de precios en `None` y `/precios` responde 503 «el motor de precios todavía no está instalado en esta base»; `/salud` también deja el bloque en `None` si falta `precio_aviso_dias_sin_evaluar`, `precio_catalogo_max_dias_sin_reportar` o `precio_cap_<p>` (D.0 siembra las tres); **pendiente de redacción**: la frase del bloque (c) con un cambio `live` en `error`, `pendiente` o `no_confirmado` dice «subió/bajó» con el estado al lado aunque el precio no se movió o no se confirmó (lo correcto: «decidió subir …; el cambio quedó en error / pendiente de envío / sin confirmar»), y un `mantener` sin `p_actual` sale «None»; una reejecución el mismo día con una decisión nueva reenvía los avisos del día, y una corrida que cae entre persistir y avisar no avisa ese día; el aviso sale con el lock de la corrida tomado; el aviso `buy_box_perdida` agrupa hoy y ayer por `COALESCE(seller_sku, external_id)` y `seller_sku` no es único por plataforma, así que dos publicaciones con el mismo SKU se confunden (lo robusto: por `listing_id`); un `frenado(no_confirmado)` manda el aviso de grupo y el de producto; el bloque (e) no muestra el precio de Pricing con sus horas (AC16: la 0039 no lo guarda); el contraste del bridge no trae `updated_at`; `/precios` es fail-closed global; `validar_precio_aviso_dias` es la tercera réplica del validador entero; el 503 por `ValueError` sale sin `scrub`. R.1: re-mutación 75/75 sobre el squash; kimi sin altas ni medias. |
| A.7 | [stage:implementacion] [lane:gate] [tdd:required] **Cobertura** (decisión 14; Muse): **sin migración** — el canal ya está en `estimacion_oferta_observation` y `mapear_canal` ya traduce `AMAZON_NA` → `fba` y `DEFAULT` → `fbm` (hecho 5); las ~8 líneas de `_motivo_universo` (`app/estimacion_insumos.py`) que hoy dejan fuera FBM y US de la **estimación** son de **E.3/0.3, no de A.7** (v1.3: A.7 no toca ese archivo). `app/precio/cobertura.py` **puro** (recibe filas y produce el recuadro de S10 por plataforma), `app/precio/fuentes.py` con la lectura de la **fuente canónica** (`spapi_listing_estado_observation`, hecho 19), del canal (`estimacion_oferta_observation`), de los goals y de las decisiones del día (I/O: entra a la lista de excepciones del candado de pureza con candado propio), y `tools/precio_cobertura.py --platform <p>` que imprime el recuadro contra producción como lector; cuadra con el catálogo activo y muestra al lado la cuenta del bridge como contraste (v1.3: el `app/listings.py` y la migración de la v1.1 no van). | Rojo-primero: canal desconocido → `canal_sin_dato`, nunca un default; publicación sin reportar > 3 días → `catalogo_desactualizado` contada y avisada; el recuadro **cuadra exacto** con las activas de la fuente canónica en un fixture de 12 publicaciones repartidas en los cuatro estados; una publicación sin goal aparece listada, no oculta; `fuera_de_alcance` nombra la fase (`fase_E_envio_fbm`, `fase_M_meli`), no un genérico; readback en producción contra la fuente canónica (264 MX, 106 US), con la cuenta del bridge (284, 109) al lado y la diferencia avisada por pasar del 5% | A.0 | cc:完了 `0d88cc8` (PR #305). Salvedades: readback en producción del 2026-09-18 (`docs/evidencia/repricing-01/A.7/readback.md`) cuadra exacto contra la fuente canónica de ese día: **MX 260** (102 `canal_sin_dato` + 158 `sin_goal`) y **US 100** (100 `canal_sin_dato`), no las 264/106 del hecho 19; el canal de FBM y de todo US es `canal_sin_dato` hasta que E.3/0.3 amplíen el universo de la estimación, así que hoy no salen como `fuera_de_alcance(fase_E_envio_fbm)`; la cuenta de activas del bridge **no está en Orbit** (`listing` es identidad): `puente_activas=unknown` salvo `--puente-activas N`, y con las cifras del hecho 19 el aviso del 5 % sale (9.2 % MX, 9.0 % US); MeLi no tiene fuente canónica en Orbit (`activas=unknown` hasta M.3b; AC21 se cumple en la función pura); `tools/precio_cobertura.py` en producción **necesita D.0** (sin `precio_goal` sale `exit 2`), y por eso el readback omitió goals y decisiones con su razón escrita; el lector `app_read` no escribe ninguna tabla que lee `fuentes.py` (probado); una activa canónica sin `listing` cuenta como `sin_listing` |
| R.1 | [stage:revision] [lane:gate] [tdd:required] **Revisión independiente** (kimi sobre un SHA; lead audita): catálogo de mutantes del implementador (una por regla y borde de S4, candados, transiciones, dinero, cuota, lock, cobertura) re-ejecutado con base real; cero sobrevivientes o se cierran con test en el mismo PR. | `docs/evidencia/repricing-01/R.1/` con catálogo, re-mutación y APPROVE de kimi y del lead sobre el SHA | A.6 | cc:完了 `4ac1f57` (PR #314) + bis `de8c54f` (PR #313). Salvedades: la cruzada de kimi corrió por squash y partida por archivos (tope de 60 000 caracteres de `cross-review.ps1`; tres archivos de prueba lo pasan solos y kimi leyó el resto en el repo), en un worktree aparte y no con `checkout --detach` en el del lead; re-mutación con base real de los seis catálogos: A.1 31/31, A.2 58/58, A.3 51/51 más 2 equivalentes, A.7 32/32, A.5 93/94 más 1 equivalente, A.6 75/75; tres sobrevivientes propios del lead (A.7, A.2, A.3) cerrados con test en el bis, en una sola rama en vez de una por fila; la observación media sobre A.5 (el diagnóstico de AC16 no se guarda ni se registra) queda en la celda de A.5. |
| D.0 | [stage:cierre-pr] [lane:release] [tdd:skip:ops] **Base de producción lista para la sonda** (dueño con `!`; lead lee; v1.3): aplicar `migrations/0039_precio.sql` con el procedimiento sellado de `docs/DEPLOY.md` §«Migración 0039» (backup `--schema-only` completo con marcador de cierre, `psql --single-transaction -v ON_ERROR_STOP=1`, verificación como `orbit_read` y desde el contenedor app); sembrar las claves `precio_*` de la tabla de umbrales con su valor inicial **como una `config_version` nueva, append-only, copiando la vigente** (`INSERT INTO config_version (label, settings) SELECT '<go literal>', settings || jsonb_build_object(...) FROM config_version ORDER BY id DESC LIMIT 1`, patrón sellado de `docs/DEPLOY.md` §caps de harvest; `config_version` tiene trigger append-only y **una fila con solo las claves `precio_*` dejaría sin caps a Ads y su `apply_quota_state` reventaría fail-closed**); son **20 claves** (las 16 filas de la tabla menos las cinco `precio_envio_*`, que se siembran cuando E.2 selle y E.3 las lea; las filas con barra cuentan cada clave). **Orden**: (1) migración: los tres caps `precio:*` salen **vacíos** porque las claves aún no existen, y eso no es fallo; (2) siembra; (3) readback. Ningún goal, ninguna decisión, ningún precio. Antes de aplicar, confirmar contra la versión de PostgreSQL de producción que el EXCLUDE con enum + `daterange` se crea (hoy `docs/DEPLOY.md` se lo pide a D.1). | `docs/evidencia/repricing-01/D.0/` con: el backup nombrado; la salida de la migración; readback como lector, **después de la siembra**: las cinco tablas `precio_*` existen y tienen **cero filas**, `listing_id_platform_key` existe, `apply_cap_de_config('precio:amazon_mx')` devuelve `5` **y los caps de Ads siguen vivos** (`apply_cap_de_config('ads_optimizer:amazon_mx:bid')` devuelve su cap de hoy, no NULL); el `id` y el `label` de la `config_version` nueva; desde el contenedor app, `leer_config(settings_vigentes)` no levanta `ValueError` y `apply_quota_state` para `precio:amazon_mx` nace con `cap = 5` en un `BEGIN … ROLLBACK`; la lista literal de las **20** claves sembradas con su valor; `docs/DEPLOY.md` §0039 dice que la 0039 **ya está aplicada** con fecha y ya no atribuye la aplicación a D.1 | A.0, A.2 | cc:完了 [2026-09-19 15:06:30–15:06:50 UTC (dueño con `!`; lead lee): `docs/evidencia/repricing-01/D.0/correr.sh` desde un worktree en `origin/master` `1bcfc5e` (#317), `D0-VERDE`; salidas en `docs/evidencia/repricing-01/D.0/salidas/20260919-150630/`. Preflight como lector: PostgreSQL **16.15**, `btree_gist`, cero duplicados `(id, platform)` en `listing`, ocho caps de Ads vivos, 0039 `ausente`, cero claves `precio_*`. Backup `pre0039_precio_20260919-150636.sql` (341 424 bytes, `600`). 0039 en una transacción, **el `EXCLUDE` con enum + `daterange` se creó**; los dos `WARNING` de `02-migracion.txt` son el `BEGIN`/`COMMIT` propios de la 0039 dentro del `-1` de psql: todo quedó en una transacción que confirma el `COMMIT` final del archivo. Siembra: `config_version` **20**, copia de la **19** (34 claves = las 14 de la 19 + las 20 `precio_*`; caps de harvest 2/2 intactos). Readback como lector después de la siembra: cinco tablas `precio_*` en cero filas, `listing_id_platform_key`, `precio_goal_sin_solape`, dos índices parciales, triggers habilitados en las cinco, caps `precio:*` = 5 y los ocho de Ads **idénticos** antes y después (`caps-ads-diff.txt` vacío); la lista literal de las 20 claves con su valor está en `04-readback.txt`. `CONFIG-OK 20 claves precio_*`; `apply_quota_state` de `precio:amazon_mx` nace con cap 5 y el `ROLLBACK` deja cero filas. `docs/DEPLOY.md` §0039 ya dice que está aplicada, con fecha, y el `EXCLUDE` confirmado en producción. **Desviación declarada** (`LEEME.md`): los pasos 5 y 6 no corrieron «desde el contenedor app», porque el código del motor se despliega en D.1: el 5 corrió local con el código de `origin/master` sobre la config leída como lector, y el 6 con el DSN de admin de la app desde el contenedor de la base. **Dos anomalías, sin efecto en los datos**: (1) el `label` de la 20 quedó `D.0: <tu go literal>`, el marcador de la plantilla que el lead pasó en el chat, porque el dueño corrió el comando tal cual; el go es esa corrida con `!` en la sesión, y `config_version` es append-only, así que el `label` no se corrige; (2) `CORRIDA.txt`, `00-preflight.txt` y `04-readback.txt` reportan `config_id` 9 antes y después: `id::text` conservaba el nombre `id` y el `ORDER BY id` ordenaba el texto ("9" > "20"); los ids reales son 19 y 20 (`03-siembra.txt`, `RETURNING`). Corregido en el PR de cierre (`max(id)`) con `test_config_id_es_el_de_la_vigente_con_ids_de_dos_digitos`, que falló antes del arreglo; las salidas se commitean como salieron, salvo los espacios al final de línea de la tabla de psql en `03-siembra.txt`, que quita el candado `trailing-whitespace`] |
| D.1 | [stage:cierre-pr] [lane:release] [tdd:skip:validacion-entrega] **Despliegue en sombra MX (FBA)** (dueño con `!`): backup, la migración ya aplicada por D.0 (se verifica, no se repite), deploy por `git archive` + md5 + rebuild, cron instalado, goals sembrados en `shadow` para los productos que el dueño elija, **cinco corridas** leídas por el lead. | E/D.1 con SHA y salidas: **≥ 80% de los goals vigentes evaluados** cada día; **el recuadro de cobertura cuadra** con las **264** activas de MX de la fuente canónica (hecho 19), con las 284 del bridge mostradas al lado; cuenta de tres productos reproducida a mano; trayectoria de sombra con cooldown visible; ningún PATCH salvo A.4 | R.1, D.0 | cc:TODO |
| D.2 | [stage:cierre-pr] [lane:release] [tdd:skip:ops] **Encendido de 3–5 productos MX (FBA)** con go literal y **medición de 30 días en dos cortes** (14: subida; 30: señal de ventas), con `precio --reporte`. | E/D.2 con la salida literal en ambos cortes. «Funcionó» = (a) 100% de `precio_cambio` en `confirmado`; (b) `abs(m_actual − goal) ≤ tol` o `frenado`/`goal_inalcanzable` con motivo; (c) al día 30 `u15` ≥ esperado salvo que haya disparado la rama de pérdida, y esa rama ejercida o declarada «no ocurrió» con números; (d) cero `no_evaluado` sin motivo; (e) Buy Box D y D+1. (a) y (d) en todos y (b) en ≥ N−1 → ampliar; cualquier `no_confirmado` → parar. Decisión literal del dueño | D.1, A.4 | cc:TODO |
| D.3 | [stage:cierre-pr] [lane:release] [tdd:skip:ops] **Ampliación MX FBA** por lotes con go, en orden de prioridad. | E/D.3; cobertura de MX FBA con `sin_goal` bajando lote a lote; tracker anotado | D.2 | cc:TODO |

### Fase E — envío medido y FBM (habilita ~10 activas en MX y ~10 en US con la ventana de 90 días)

**El encabezado de la v1.1 decía «habilita 113 en MX y 109 en US» y era falso.**
Con el mínimo de 6 envíos en 90 días, hoy califican 7 productos de MX y 9 de US,
que son 10 y 10 publicaciones activas (hecho 13). El resto de las FBM queda en
`fuera_de_alcance(fase_E_sin_historia_envio)`, contado y visible, no escondido.
La palanca para subir esa cobertura es **la ventana**, y E.2 la sella con los
números de E.1: 180 días duplica, 365 triplica. Ampliar la ventana también
envejece la tarifa, y ese intercambio es exactamente lo que el dueño decide en
E.2 — no el percentil, que mueve ≤ 0.43 puntos de margen.

| ID | Tarea | DoD (verificable) | Deps | Estado |
|---|---|---|---|---|
| E.0a | [stage:verificacion] [lane:gate] [tdd:skip:sonda] **Veredicto sobre las fuentes de `shipping_fee`** (lead, solo lectura; **insumo del dueño**; v1.3, antes E.0): resolver el hecho 14 — para las 18 órdenes de US con tres cargos, determinar **contra el documento de origen** (el reporte de Seller Central o la contabilidad que alimenta el ledger, **que el dueño entrega al lead**; Orbit no lo tiene) si `ShippingHB`, `LabmanLabelPurchase` y `shipping_label` son tres componentes distintos de un mismo envío o el mismo cobro informado dos veces. Punto de partida medido (hecho 22): los pares `Labman`/`shipping_label` casan al 1 % y los `ShippingHB`/`shipping_label` no. También: clasificar los ~109 descartes diarios por convención de signos (plataforma y `fee_type` no visibles hoy). **Sin veredicto, `L` está mal por construcción**: nada de E depende de un `L` medido antes de que cierre. | `docs/evidencia/repricing-01/E.0/veredicto.md` con las 18 órdenes desglosadas, la fuente documental de cada cargo, el veredicto literal (componentes distintos / duplicado) por par de fuentes, la regla de costo por orden que resulta, escrita como tabla `fuente → cuenta / descarta / otros`, y la lista de descartes por signo con plataforma y `fee_type`. Si el dueño no entrega el documento, la fila queda `blocked` con la lista literal de órdenes a consultar; **la fase E se detiene ahí** (E.0b y E.1 cuelgan de ella) y el resto del plan sigue | — | cc:TODO — blocked: falta la vista de transacciones de Seller Central (US) de las **54 órdenes** listadas en la sección 7 de `docs/evidencia/repricing-01/E.0/veredicto.md`, exportada a `/Users/dn/dev/orbit-insumos/E.0/`. Veredicto parcial con evidencia en `7b5d6e9` (PR #304) y `b7a0839` (PR #306): los tres pares `sin veredicto`; las «18 órdenes con tres cargos» del hecho 14 hoy son 54 (36 etiquetas entraron por rezago de ingesta el 2026-09-17); `finance:LabmanLabelPurchase` nace el 2026-08-18 y casa con `shipping_label` en 53 de 54 pares (32 al centavo), compatible con `duplicado` sin probarlo; `ShippingHB` no casa con ninguna fuente; regla de costo por orden escrita para cada veredicto posible (insumo de E.0b); descartes por convención de signos 105–112 por corrida, con plataforma y `fee_type` `unknown` (no están en Orbit; consulta de la base de contabilidad versionada y **no corrida**); el corredor de consultas rechaza escrituras, metacomandos de `psql`, control de transacción y toda sentencia que no sea `select`/`with` (`E.1/correr.sh` no tiene esos candados: queda para la fase que toque E.1) |
| E.0b | [stage:implementacion] [lane:gate] [tdd:required] **Regla de costo por orden en la ingesta** (Muse; v1.3, antes parte de E.0): fijar en la ingesta la regla que E.0a dejó escrita, con toda fila descartada **contada con su razón**, nunca borrada; una fuente desconocida cae en `otros` contada; los descartes por convención de signos salen en `ingest_run` con plataforma y `fee_type`. | Rojo-primero para la regla de E.0a; una fuente desconocida cae en `otros` contada, nunca descartada en silencio; los descartes por signo salen en `ingest_run` con plataforma y `fee_type`; readback en producción con el total de US de 90 días antes y después, y la diferencia explicada fila por fila | E.0a | cc:TODO — blocked: E.0a sin veredicto (la vista de transacciones de Seller Central de las 54 órdenes de la sección 7 de `docs/evidencia/repricing-01/E.0/veredicto.md` sigue pendiente del dueño; el carril D no corrió en la Fase 11). |
| E.1 | [stage:verificacion] [lane:gate] [tdd:skip:sonda] **Medición del envío por producto** (lead, solo lectura) sobre el ledger **ya deduplicado** por E.0b, agrupando por **orden** y sobre `abs(amount)`: cuántos productos alcanzan el mínimo en ventanas de 90, 180 y 365 días; dispersión (mediana, p90, máximo); órdenes descartadas por traer más de un producto o no ligar a venta, con su razón; **rezago** del cargo (p50, p90, máximo) para fijar el cierre de ventana; y cuántos productos entran y salen del mínimo a lo largo de seis ventanas móviles (el parpadeo del hecho 15). | `docs/evidencia/repricing-01/E.1/` con las consultas y sus salidas; tabla por producto con `ordenes, mediana, p90, max` en las tres ventanas; conteo de descartes por razón; tabla de rezago; tabla de parpadeo; y el efecto sobre el margen de **90 vs 180 vs 365 días** en al menos 10 productos — que es la decisión real, no p50 vs p75 | E.0b | cc:TODO — **fila abierta** (depende de E.0b, ledger con la regla de E.0a, que no es de esta fase); la **evidencia** de la Fase 8 ya está mergeada en `6127708` (PR #297) bajo dos lecturas de la disputa del hecho 14. Salvedades: «365 días» son 287 de datos; el rezago de emisión del hecho 15 (27/22 días) NO se reproduce (p50 0, máx 3) y el que sí se mide es el de ingesta (MX p90 4.2, US p90 13): lo decide E.2; 148 órdenes con cargo tienen venta sin `product_id` |
| E.2 | [stage:contrato] [lane:fast] [tdd:skip:contrato] **Acta del envío medido** (dueño; **en dos partes desde la v1.3**: la parte 1, con los hechos 20–22, sella (a), (b) y (d) porque E.1 midió que ventana, mínimo e histéresis dan lo mismo bajo las dos lecturas de la disputa; la parte 2, (c) y (e), espera a E.0a/E.0b porque el **valor** sí cambia con el veredicto): con los números de E.1 sella (a) la **ventana**, sabiendo que más ventana es más cobertura y tarifa más vieja; (b) el **mínimo de envíos y su histéresis** (propuesta: entra con 6, sale con 3, para que no parpadee); (c) el **valor** (propuesta: mediana de la ventana, con p90 y máximo mostrados como dispersión); (d) qué pasa con un producto sin historia; y (e) que el ingreso por envío entra **donde el dato exista**, y donde no, `ingreso_envio_sin_dato` — nunca cero. | `docs/evidencia/repricing-01/E.2/acta.md` con la decisión literal del dueño; spec delta en S10; ninguna ausencia convertida en cero; el acta dice explícitamente que en US **no hay** dato de ingreso por envío (hecho 16) y que eso no bloquea la fase; la parte 1 lleva los hechos 20–22 como insumo y la parte 2 el veredicto de E.0a | parte 1: hechos 20–22 (ya medidos); parte 2: E.1 | cc:TODO |
| E.3 | [stage:implementacion] [lane:gate] [tdd:required] **`L` medido en el margen estimado FBM** (Muse): `app/precio/envio.py` (`muestra_envio(product_id, platform, hoy, cfg) -> Muestra`, puro sobre filas ya leídas) + la lectura que las trae; persistencia en `precio_envio_muestra`; el escenario FBM produce contribución con `L` de la muestra y `F` de una cotización **pedida con cumplimiento FBM**. **Amplía el universo de la estimación a FBM y a US en la misma edición** (ver nota de fusión con 0.3). | Rojo-primero: percentil/mediana sobre `abs(amount)` — un mutante que use el monto crudo elige el envío más barato y **muere**; agrupa por `order_id` antes de resumir — un mutante que percentile filas muere; `L_unidad = L_orden / unidades` con una orden de 2 unidades sembrada; orden con dos productos **excluida** y contada aparte; histéresis: 6 entra, 5 no, y un producto dentro con 4 **sigue dentro** hasta caer a 3; ventana que termina en `hoy − rezago` y excluye un envío fuera de rango; la cotización FBM **no** trae comisión de logística (mutante que pida FBA en FBM duplica `L` y muere); producto FBA nunca toca este camino (`L = 0`); `envio_muestra_id` no nulo en toda decisión FBM; `ingreso_envio_sin_dato` cuando falta `shipping_price`, jamás cero | E.2, A.2 | cc:TODO |
| E.4 | [stage:implementacion] [lane:gate] [tdd:required] **El motor distingue canal** (Muse): `canal` en la decisión y en el recuadro de cobertura; `precio_goal` admite publicaciones FBM; la corrida evalúa FBA y FBM con la misma regla y distinta `L`; `/precios` y `/salud` muestran el canal y, en FBM, la muestra con su ventana efectiva, sus órdenes y su dispersión. La **rama de inventario queda `sin_dato` por diseño en FBM** y así se muestra: la fuente que la alimenta es de FBA y en FBM nunca se puebla. | Rojo-primero: una publicación FBM con goal y muestra completa produce `subir` con `L` de la muestra en la cuenta; la misma sin muestra → `no_evaluado(envio_sin_historia)`; el recuadro de MX mueve **~10** publicaciones de `fuera_de_alcance` a en alcance y **el resto del subconjunto FBM canónico** queda en `fase_E_sin_historia_envio` **contado** (el número exacto sale de la fuente canónica en A.7, no del 113 del bridge); `/precios` muestra «envío medido, mediana 94.50 MXN sobre 18 órdenes, ventana 90 d al 2026-09-01» y no un número sin origen; en FBM el motivo de inventario es `sin_dato(inventario_no_aplica_fbm)`, nunca una rama que no puede dispararse | E.3, A.7 | cc:TODO |
| E.5 | [stage:cierre-pr] [lane:release] [tdd:skip:ops] **Sombra y encendido FBM en México**: goals FBM en `shadow`, cinco corridas leídas, luego 3–5 productos en vivo con go y 30 días en dos cortes. | E/E.5 con el mismo criterio de D.1 y D.2, más: ningún producto FBM movido sin `envio_muestra_id`; la cobertura de MX cuadra con las **264** activas de la fuente canónica y `fuera_de_alcance` queda solo con los motivos declarados (`sin_historia_envio`, `sin_goal`), no en cero; el criterio (c) se declara «no ocurrió» con números (hecho 17) si no se ejerce | E.4, D.2 | cc:TODO |

### Fase 0 — política fiscal de Estados Unidos

| ID | Tarea | DoD (verificable) | Deps | Estado |
|---|---|---|---|---|
| 0.1 | [stage:verificacion] [lane:gate] [tdd:skip:sonda] **Inventario US** (lead, solo lectura): cotización de fees para una oferta **FBM** real de US (`Success`, conciliación, `TaxAmount`, tipos devueltos; `PerItemFee` sugiere plan Individual: confirmar, no suponer); cargos recurrentes en Finances US (¿retención? ¿sales tax en el precio?); FX para US: el par MXN→USD **no se carga en este repo y no se va a inventar** — la conversión usa `fx_resolve(fecha, 'USD', 'MXN')` y **divide**; confirmar que hay tasa para las fechas del periodo; cobertura de `sku_cost` y de ofertas US frescas. | `docs/evidencia/repricing-01/0.1/` con las sondas y respuestas literales; tabla de insumos US presente/ausente por componente `I, C, F, L, R` | D.2 | cc:TODO |
| 0.2 | [stage:contrato] [lane:fast] [tdd:skip:contrato] **Acta 0.4 de `margen-estimado-01`: política fiscal US** (dueño): `I = P` sin IVA; `R` según 0.1 o 0 declarado con razón; `L` = envío medido de la fase E; FX de `C` con fecha. Amplía el universo a `amazon_us`. | Acta con decisión literal; si un componente no tiene fuente, `amazon_us` queda `blocked` con motivo y la fase B no arranca | 0.1, E.2 | cc:TODO |
| 0.3 | [stage:implementacion] [lane:gate] [tdd:required] **Estimación por venta para US** (Muse): **es una edición sobre el SHA de E.3, no un módulo nuevo** — las dos tareas amplían `_motivo_universo` y el mismo escenario, separadas por dos mediciones de 30 días. E.3 abre FBM y deja US detrás de una bandera de config; 0.3 la enciende con la normalización del acta 0.2, `marketplace_id` parametrizado en `estimacion_fees`, cotización US ligada a la oferta y `fx_ausente` como motivo. Si 0.2 llega antes de E.3, se funden en una sola tarea. | Rojo-primero: escenario US con oferta fresca produce contribución con sus componentes; sin tasa `USD→MXN` para la fecha → `null` con `fx_ausente`, jamás una constante; sin cotización → `fee_ausente`; MX intacto (suite previa verde); mutante que fije FX constante muere; readback en producción de N listings US | 0.2 | cc:TODO |

### Fase B — Amazon Estados Unidos (106 activas en la fuente canónica, todas FBM)

**La fase B no va a subir precios, y eso no es un fallo.** Medido: la
contribución por unidad en US va de 33% a 63%, mediana ~50% (hecho 18). El
envío supera a la comisión pero no se come el margen. El hueco real es Ads:
**TACoS 23.9% en US contra 9.0% en MX**, así que un goal pre-Ads de 30% en US
es ~6% después de Ads. B va a producir `mantener` en la mayoría; su éxito es
que la cuenta cuadre y que las excepciones salgan con motivo. Cuál es la
palanca de US queda **fuera** de este plan.

| ID | Tarea | DoD (verificable) | Deps | Estado |
|---|---|---|---|---|
| B.1 | [stage:cierre-pr] [lane:release] [tdd:skip:ops] **US en sombra**: goals US en `shadow`, cinco corridas leídas con el umbral de D.1. `/precios` y el cierre muestran el **TACoS de 90 días** junto a `m_actual`, porque 50% de margen con 24% de Ads encima no es el mismo negocio que con 9%. | E/B.1 igual que D.1; el recuadro de cobertura de US cuadra con las **106** activas de la fuente canónica; el reporte trae la distribución de `m_actual` y el TACoS por producto; **una mayoría de `mantener` es resultado esperado, no un fallo** | 0.3, E.4 | cc:TODO |
| B.2 | [stage:cierre-pr] [lane:release] [tdd:skip:ops] **Encendido US** de 3–5 productos con go y 30 días; luego ampliación por lotes. | E/B.2 igual que D.2; cobertura de US con `sin_goal` bajando lote a lote | B.1, **E.5** | cc:TODO |

### Fase M — Mercado Libre (137 publicaciones)

| ID | Tarea | DoD (verificable) | Deps | Estado |
|---|---|---|---|---|
| M.1 | [stage:verificacion] [lane:gate] [tdd:skip:sonda] **Inventario de insumos de MeLi** (lead, solo lectura): por qué la caché del bridge no se refresca desde el 2026-05-01 y qué la refrescaría; qué devuelve la API por item (precio, estado, categoría, envío, comisión) con el cliente GET que ya existe; si hay fuente para mapear SKU de MeLi a producto de Odoo (`meli_sku_mapping` está vacío; su resultado alimenta M.0); qué trae el reporte de liquidación de MeLi (comisión, envío, impuesto) y si se puede ingerir con el patrón del ledger. | `docs/evidencia/repricing-01/M.1/` con las sondas literales y una tabla de los cinco insumos del margen (`I, C, F, L, R`) con su fuente propuesta o su ausencia declarada | D.2 | cc:TODO |
| M.2 | [stage:contrato] [lane:fast] [tdd:skip:contrato] **Acta de MeLi** (dueño): con M.1, sella la fórmula del margen en MeLi (impuesto sobre el precio, comisión por categoría, envío, retención), el mapeo SKU→producto y el equivalente observable de la Buy Box, o declara qué queda sin fuente. | Acta con decisión literal; si falta un componente, MeLi queda `blocked` con motivo y M.3+ no arrancan | M.1 | cc:TODO |
| M.0 | [stage:verificacion] [lane:gate] [tdd:required] **Mapeo SKU de MeLi → producto** (dueño decide la fuente; Muse puebla): `meli_sku_mapping` está vacío y **sin ese puente no hay costo por publicación**, así que ninguna otra pieza de MeLi sirve. Se adelanta al frente de la fase y es owner-gated: el dueño confirma la correspondencia antes de escribir. | `docs/evidencia/repricing-01/M.0/` con la fuente del mapeo y el visto del dueño; SKU sin correspondencia queda **listado**, nunca mapeado a un `product_id` adivinado; readback con la cuenta de las 137 publicaciones mapeadas y sin mapear | M.2 | cc:TODO |
| M.3a | [stage:implementacion] [lane:gate] [tdd:required] **Abrir el ledger de MeLi** (Muse): el dinero **ya llega a diario** y se descarta en una rama — `ingest_run` reporta `5126x plataforma meli excluida` cada día (hecho 6). Quitar esa rama, mapear los tipos de cargo (comisión, envío, impuesto) al vocabulario del ledger y agregar el valor `meli` a `estimacion_canal`, que hoy solo admite `fba\|fbm`. Es trabajo de horas, no una ingesta nueva. | Rojo-primero: las filas antes excluidas entran con su `fee_type`; un cargo sin tipo conocido → `otros` y **contado**, nunca descartado; la convención de signos se respeta; `estimacion_canal` acepta `meli` y los enums viejos siguen válidos; MX/US intactos; readback: el conteo diario de `plataforma meli excluida` baja a cero y las filas aparecen en `ledger_event` | M.0 | cc:TODO |
| M.3b | [stage:implementacion] [lane:gate] [tdd:required] **Catálogo y precios de MeLi** (Muse): publicaciones y precios a `listing` y a una observación de precio propia, con el refresco que M.1 haya identificado para la caché parada desde el 2026-05-01. | Rojo-primero: item sin SKU mapeado → la fila no se escribe con `product_id` inventado, queda contada con motivo; ingesta idempotente por `(external_id, observed_at)`; readback en producción con las 137 publicaciones y sus precios frescos | M.3a | cc:TODO |
| M.4 | [stage:implementacion] [lane:gate] [tdd:required] **Margen estimado de MeLi** (Muse): la fórmula del acta M.2 con el mismo contrato de ausencias que Amazon. | Rojo-primero: cada componente ausente produce su motivo; ninguna ausencia vale cero; una publicación con todo produce contribución reproducible a mano | M.3b | cc:TODO |
| M.5 | [stage:implementacion] [lane:gate] [tdd:required] **Escritura de precio en MeLi y su reversa** (Muse): `app/meli/write_client.py` default-deny con una sola ruta de actualización de item; `app/meli/precio_write.py` con el mismo orden (fila, escritura, ack, readback, cierre) y la reversa primero; `ClienteMeli` **intacto**; candado de arquitectura propio. Más **sonda con ids reales** del dueño (`!`, go literal) que sella la forma y la sincronía de la escritura. | Rojo-primero con cliente falso, los mismos casos que A.3; candado con fuga sembrada; `ClienteMeli` sigue rechazando todo método que no sea GET (test); evidencia `M.5/` de la sonda con el control negativo, igual que A.4 | M.4 | cc:TODO |
| M.6 | [stage:cierre-pr] [lane:release] [tdd:skip:ops] **MeLi en sombra y encendido**: goals en `shadow`, cinco corridas, 3–5 productos en vivo con go y 30 días. Cierra el plan; `ORBIT 09` `Done`. | E/M.6 igual que D.1 y D.2; la cobertura de MeLi cuadra con las publicaciones activas y `fuera_de_alcance` en cero en las tres plataformas | M.5, B.2 | cc:TODO |

## Clasificación (Required / Recommended / Optional / Reject)

- **Required**: A.0–A.7, R.1, D.0–D.3 (fase A); **E.0a, E.0b**–E.5 (fase E, decisión
  13); A.7 y el recuadro de cobertura (decisión 14); 0.1–0.3 y B.1–B.2 (US);
  M.0–M.6 (decisión 15). **E.0a es la que no se puede saltar**: sin ella `L`
  está mal por construcción y todo lo que E produzca es aritmética sobre un
  número que nadie verificó.
- **Owner-gated**: A.4, D.0, D.1, D.2, D.3, E.0a (documento de origen), E.2, E.5, 0.1, 0.2, B.1, B.2, M.0, M.1,
  M.2, M.5 (sonda), M.6.
- **Reject** (decisión del dueño 2026-09-15): estrategias «match/beat
  competitor», «Buy Box oriented», «inventory aware», «time-based» y modos del
  traspaso; bajar por Buy Box perdida; explorar sin goal; aprobación manual por
  cambio; defaults de goal; escalera de porcentaje fijo.
- **Reject** (revisión 2026-09-16): reversa automática por lectura; PATCH en
  `SpapiClient` o escritura en `ClienteMeli`; cotizaciones del motor en
  `estimacion_fee_observation`; cooldown que ignore `no_confirmado`/`error`;
  avisos por producto para motivos de plataforma; medición de 14 días como
  criterio de ampliación; `--sku` ambiguo en la siembra; goal como fracción.
- **Reject** (ampliación 2026-09-16): repartir el costo de una etiqueta entre
  los productos de una orden multi-producto (se descarta de la muestra y se
  cuenta aparte); usar el promedio de envío en vez del estadístico sellado;
  suponer que el cliente paga envío donde el ledger dice que no; cubrir MeLi
  con un margen parcial «mientras llega» el resto de los insumos.
- **Reject** (reglas de Orbit): recalcular margen, costo o FX dentro del motor;
  constantes en lugar de datos faltantes; `UPDATE` sobre ledgers; reintentos en
  ráfaga; escrituras masivas sin ceremonia.

## Propiedad de archivos y concurrencia

| Tasks | Archivos/área previstos | Restricción |
|---|---|---|
| A.0 | `migrations/00NN_precio.sql` (incluye `CREATE OR REPLACE apply_cap_de_config`), `tests/test_precio_migracion.py`, `tests/test_schema.py`, `docs/DATABASE.md`, `docs/DEPLOY.md` | Número posterior al último en master al abrir el PR; el test de 0002 sobre `apply_cap_de_config` sigue verde |
| A.1 | `app/precio/goals_write.py` (en la lista de excepciones del candado de pureza, con candado propio `_IDENT_PRECIO_GOAL`), `tools/precio_goal.py`, `tests/test_precio_goals.py`, `tests/test_architecture.py` (la excepción y el candado, en paralelo a los existentes), `docs/evidencia/repricing-01/A.1/mutantes.md` | Solo `app_admin` (`ORBIT_DSN_ADMIN`); cero Amazon; no toca `app/precio/{tipos,reglas,objetivo,ventas,config}.py` |
| A.2 | `app/precio/{tipos,reglas,objetivo,ventas,config}.py`, `app/estimacion_fees.py` (solo `cotizar_a_precio`), `tests/test_precio_reglas.py` | `app/precio/*` puro: sin I/O ni reloj |
| A.3 | `app/spapi/write_client.py`, `app/spapi/precio_write.py`, `tools/precio_reversa.py`, tests | `SpapiClient` intacto; reversa antes que escritura |
| A.5 | `app/precio/corrida.py`, `app/precio/cuota.py` (los dos en la lista de excepciones del candado de pureza, cada uno con candado propio y fuga sembrada), `app/cli.py`, `docs/DEPLOY.md` §cron, `tests/test_architecture.py` (las dos excepciones y sus candados, en paralelo a los existentes), tests | No toca `app/cycle.py`, `app/apply.py` ni las cuotas de Ads |
| A.6 | `app/api_dashboard.py`, plantillas, `app/notifica.py` (un sender), tests | `notifica_*` existentes intactos |
| A.7 | `app/precio/cobertura.py` (puro: recuadro a partir de filas), `app/precio/fuentes.py` (lectura de la fuente canónica, el canal, los goals y las decisiones; en la lista de excepciones del candado), `tools/precio_cobertura.py` (readback), `tests/test_precio_cobertura.py`, `tests/test_architecture.py` (la excepción y su candado), `docs/evidencia/repricing-01/A.7/` | **Sin migración** y sin tocar `app/listings.py` (hecho 5; corrige la v1.1); no toca `app/precio/{tipos,reglas,objetivo,ventas,config}.py` |
| D.0 | `docs/evidencia/repricing-01/D.0/`, `docs/DEPLOY.md` §0039 (la línea «aplicada el …», el párrafo que hoy atribuye la aplicación a D.1 y la confirmación del EXCLUDE que hoy está en D.1) | Producción solo por el dueño con `!`; el lead lee; D.0 no se corre en la misma ventana de despliegue que D.3 de `fabrica-02` |
| E.0a | `docs/evidencia/repricing-01/E.0/veredicto.md` | Solo lectura; el documento de origen lo entrega el dueño |
| E.0b | `app/ingest*.py` (la rama de `shipping_fee`), tests, `docs/evidencia/repricing-01/E.0/` | `UPDATE` sobre ledgers prohibido; toda fila descartada se cuenta |
| E.1–E.2 | `docs/evidencia/repricing-01/{E.1,E.2}/`, spec S10 | Solo lectura en producción; decisión literal del dueño |
| E.3–E.4 | `app/precio/envio.py`, `app/precio/{reglas,corrida,cobertura}.py`, `app/estimacion_*` (canal FBM), plantillas, tests | FBA intacto: la suite de la fase A verde sin cambios |
| 0.1–0.2 | `docs/evidencia/repricing-01/0.1/`, `docs/evidencia/margen-estimado-01/0.4/`, spec de márgenes | Solo lectura; decisión literal |
| 0.3 | `app/estimacion_*.py` (universo US, `marketplace_id`), `app/fx.py` (solo con fuente demostrada), tests | No cambia FBA MX |
| M.1–M.2 | `docs/evidencia/repricing-01/{M.1,M.2}/`, spec S11 | Solo lectura; `ClienteMeli` sin cambios |
| M.3–M.5 | `app/meli/*` (nuevo), `app/ledger.py` (fuente MeLi), `migrations/00NN+2_meli.sql`, `app/reputacion_clientes.py` **solo** si hace falta exponer credenciales sin tocar el cliente GET, tests | La reputación de MeLi no se toca ni se detiene; su cliente sigue GET-only |
| R.1/D.x/E.5/B.x/M.6 | Evidencia, `docs/DEPLOY.md`, PRs | Revisor solo lectura; deploy, siembra y encendido los corre el dueño con `!` |

Choca potencialmente con: **`fabrica-02` D.3** (desde el 19-sep: sin archivos
en común, comparte ventana de despliegue y atención del dueño); **`bids-01`
lote de inertes (4-oct)**: sin archivos en común; **`margen-estimado-01`**
(E.3, 0.2/0.3 y M.4 amplían su spec y su código como spec delta, sin reabrir
sus filas); **`reputacion-01`** (M.3/M.5 tocan el área de MeLi: la ingesta de
reputación y su cliente GET no se modifican).

## Aceptación verificable

| ID | Caso | Resultado esperado | Evidencia |
|---|---|---|---|
| AC1 | Goal 30%, insumos completos, `m = 24%`, FBA | `subir` a `min(P*, P·1.10)`, cuenta completa, una cotización de verificación | A.2 + D.1 |
| AC2 | Sin cotización de fees hoy | `no_evaluado(fee_ausente)`, cero escritura, visible en pantalla | A.2/A.5/A.6 |
| AC3 | `m = 35%`, pérdida sostenida 3 corridas, con stock | `bajar` a `max(P_goal, P·0.90)` | A.2 |
| AC4 | 74 días de historia, o `u60 = 19`, o un día sin stock, o racha 2 | `mantener` con `sin_dato(motivo)` | A.2 |
| AC5 | Escritura aceptada, lectura inmediata con precio viejo, observación D+1 distinta | `enviado` hoy; `no_confirmado` mañana; `frenado` + aviso; cero reversa automática | A.3/A.5/A.6 |
| AC6 | Reversa de un cambio real por la herramienta | cerrada por observación D+1 en el precio original; control negativo intacto | A.4 |
| AC7 | Tercer cambio sin acercarse al goal | `frenado(no_converge)`; goal nuevo reinicia el freno, no el cooldown | A.2 |
| AC8 | Cuota en cap con 8 candidatos | los 5 de mayor prioridad se aplican, 3 `mantener(cuota)`; Ads intacto | A.5 |
| AC9 | `httpx.patch` con la ruta de listings fuera de `precio_write.py`; escritura en `ClienteMeli` | candados fallan | A.3, M.5 |
| AC10 | Segunda corrida del día; dos corridas simultáneas | ninguna decisión nueva; exactamente una escritura | A.5 |
| AC11 | Cinco corridas en sombra | ≥ 80% de los goals evaluados; cuenta reproducida a mano en 3 productos; cero escrituras | D.1 |
| AC12 | 30 días de 3–5 productos en vivo | reporte de los cortes 14 y 30 con los criterios (a)–(e); decisión literal | D.2 |
| AC13 | Subida confirmada hace 10 días + caída sostenida | `frenado(perdiendo_tras_subida)` | A.2 |
| AC14 | Goal `0.30` o fuera de banda; `--sku` con dos listings | la herramienta aborta; la base rechaza | A.1/A.0 |
| AC15 | 200 productos con el mismo motivo tres días | un solo aviso con conteo y 5 SKUs, sin costo ni margen | A.6 |
| AC16 | Pricing 1.5% distinto de la oferta del escenario | `no_evaluado(precio_divergente)` con ambos precios y horas | A.2/A.6 |
| **AC17** | **Recuadro de cobertura** en cualquier corrida | activas = evaluadas + no evaluadas + sin goal + fuera de alcance, **exacto**, por plataforma, contra la **fuente canónica** (`spapi_listing_estado_observation`: 264 MX, 106 US), con la cuenta del bridge al lado y aviso si difieren más de 5%; una publicación sin reportar 4 días sale como `catalogo_desactualizado` y avisa | A.7, D.1, E.5, B.1, M.6 |
| **AC18** | **Orden FBM con dos productos** en la ventana de envío | excluida de la muestra y contada aparte; nunca repartida. **Protege contra poco**: en 180 días, 431 de **439** órdenes en MX y 344 de **347** en US son de un solo producto y una sola unidad (los mismos denominadores que el hecho 4), así que lo que AC18 excluye son 8 órdenes en MX y 3 en US, no 1 y 1. Lo que sí hay que contar son las órdenes con cargos de varias fuentes (18 en US) y las ~109 filas diarias que la ingesta descarta por convención de signos | E.3, E.0b |
| **AC19** | **Producto FBM con 5 envíos** | `no_evaluado(envio_sin_historia)` y cero escritura **si venía de fuera**; si ya estaba dentro, **sigue dentro** hasta caer a 3 (histéresis). Un producto que oscila entre 5 y 7 envíos no debe alternar estado ni avisar en cada corrida | E.3 |
| **AC20** | **Decisión FBM aplicada** | `envio_muestra_id` no nulo, con **ventana efectiva** (cerrada en `hoy − rezago`), número de **órdenes** (no filas), fuentes usadas, filas descartadas con su razón y el valor sellado; `/precios` muestra el número con su origen | E.4/E.5 |
| **AC23** | **Percentil sobre montos negativos** | la muestra se calcula sobre `abs(amount)`; un mutante que use el monto crudo elige el envío **más barato** y muere | E.3 |
| **AC24** | **Cotización de fees para una oferta FBM** | se pide con cumplimiento FBM y su `F` es solo referral; un mutante que la pida como FBA suma la comisión de logística y **cuenta el envío dos veces** | E.3 |
| **AC25** | **Publicación FBM y la rama de inventario** | sale `sin_dato(inventario_no_aplica_fbm)`; la fuente de inventario es de FBA y en FBM nunca se puebla, así que la rama no puede dispararse y no se finge que sí | E.4 |
| **AC26** | **Venta de US sin `shipping_price`** | `ingreso_envio_sin_dato` visible, **jamás** ingreso de envío igual a cero; hoy son las 351 ventas de 180 días | E.3 |
| **AC21** | **Publicación de MeLi antes de su fase** | aparece en cobertura como `fuera_de_alcance(fase_M_meli)`, nunca ausente | A.7 |
| **AC22** | **Item de MeLi sin SKU mapeado** en la ingesta | la fila no se escribe con `product_id` inventado; queda contada con motivo | M.0, M.3b |
| **AC27** | **Cargo de MeLi que hoy se descarta** | tras M.3a el conteo diario `plataforma meli excluida` baja a **cero** y las filas aparecen en `ledger_event` con su `fee_type`; un tipo desconocido cae en `otros` **contado** | M.3a |

## Secuencia de despliegue y reversa

1. Migraciones en una transacción, tras el backup del schema, con
   verificación como `orbit_read` (tablas, enum, triggers, GRANTs por columna,
   índices parciales, `apply_cap_de_config`). Reversa: restaurar el dump.
2. Deploy del código por `git archive` del SHA aprobado, md5 idéntico,
   rebuild, `/health`, `/precios` 200, `/salud` con el bloque `precios`.
   Reversa: `predeploy-<stamp>/` y rebuild.
3. Cron `precio` (13:10 UTC) aditivo, con `flock` y log. Reversa: quitar la
   línea.
4. Siembra de goals en `shadow` (dueño, go). Reversa: `--cerrar`, sin tocar la
   plataforma.
5. Encendido de 3–5 productos (dueño, go). Reversa: `--mode shadow` y, si hubo
   cambios, `tools/precio_reversa.py --cambio-id …`.

Cada fase que suma una plataforma o un canal repite los pasos 4 y 5 para ese
conjunto; ninguna fase enciende dos conjuntos a la vez.

## Divergencias y residuales declarados

- Traspaso §Módulo 1: una sola estrategia por decisión del dueño. Con la
  decisión 15, MeLi vuelve al alcance por fases.
- Inventario: invalida la señal de ventas, no decide precio.
- Señal de ventas por producto, no por publicación: dos publicaciones del
  mismo producto en una plataforma comparten señal.
- **`L` en FBM es medido, no cotizado.** Es la única componente que no viene
  de una cotización; por eso lleva su muestra adjunta y su valor sellado. Un
  producto sin historia de envíos no se evalúa.
- **La rama de bajar precio no se va a ejercer en esta tanda.** Un solo
  producto del negocio llega a `u60 ≥ 20` (hecho 17), así que el criterio (c)
  de D.2, E.5, B.2 y M.6 va a salir «no ocurrió» con números. Se declara ahora,
  no al cerrar. Bajar `precio_u60_min` para forzarla sería bajar precios con
  ruido estadístico: no se hace sin acta nueva del dueño.
- **En Estados Unidos no hay dato de ingreso por envío** y no lo va a haber
  hasta que la ingesta contable cargue el desglose que hoy descarta por
  diferencia de moneda (hecho 16). Mientras tanto `ingreso_envio_sin_dato`, y
  esa parte del DoD de E.3 queda declarada como no implementable en US. **No
  bloquea la fase.**
- **La fase E habilita ~20 publicaciones, no 222** (hecho 13). El techo lo pone
  el volumen de ventas, no el umbral. Las FBM restantes quedan contadas en
  `fuera_de_alcance(fase_E_sin_historia_envio)`, que es exactamente lo que la
  decisión 14 pide: visibles, no cubiertas.
- **El precio no es la palanca de Estados Unidos** (hecho 18). El margen ya va
  de 33% a 63%; lo que pesa es un TACoS de 23.9% contra 9.0% en México. Este
  plan hace repricing en US porque la decisión 14 exige cubrir todo el
  catálogo, no porque vaya a mover el resultado. Qué hacer con Ads en US queda
  fuera.
- **Cuatro mediciones de 30 días en serie** (D.2, E.5, B.2, M.6) son ~120 días
  de reloj encadenados, y ninguna empieza hasta que cierra la anterior. Es
  consecuencia de la decisión del dueño de medir 30 días por encendido, y se
  declara para que el calendario no sorprenda.
- **La cola del envío es real**: un envío lejano cuesta cerca del doble del
  típico en los pocos casos donde hay cola, pero la dispersión que justificaba
  sellar un percentil **no existe** (hecho 15): MX es tarifa plana y en US el
  p75 − p50 mueve el margen ≤ 0.43 puntos. Por eso el valor sellado es la
  mediana y lo que E.2 decide es **la ventana**. La dispersión (p90, máximo) se
  muestra junto a la muestra, y define cuánto de esa cola absorbe el
  margen; el resto se declara en la evidencia.
- Con cuota 5/día y cooldown de 7 días, un catálogo de cientos de
  publicaciones tarda meses en converger: es intencional (decisión 8) y por
  eso las ampliaciones van por prioridad y por lotes.
- Fases 0 y M pueden terminar en «no liberado» si su sonda no encuentra fuente
  para algún componente; entonces esa plataforma queda `blocked` con motivo y
  sus publicaciones siguen contadas como `fuera_de_alcance`. Se declara, no se
  fuerza.
- La caché de publicaciones de MeLi del bridge no se refresca desde el
  2026-05-01: M.1 tiene que explicar por qué antes de que M.3 dependa de ella.
- Sin reversa automática en ninguna plataforma: un cambio no confirmado frena
  y avisa; la reversa es del dueño con la herramienta.

## Snippet para `plans/manifest.json` (aplicado en el PR de la v1.0 y actualizado en el de la v1.3; no cambia `active`)

```json
{
  "name": "repricing-01",
  "path": "plans/repricing-01.md",
  "description": "REPRICING 01 - motor de precios por goal de margen (M1/AUTO-07/ORBIT 09). Spec v1.3 (dos rondas de revision; la segunda invalido cuatro hechos que el plan daba por medidos): proteger margen; goal por producto; sube si el margen estimado no llega, baja solo si caen las unidades de 15 dias contra 60; sombra primero; cuota propia; envio FBM medido de las etiquetas (decision 13); toda publicacion activa contemplada (14); Mercado Libre en alcance (15). Plan v1.3 (2026-09-17): Fase 8 cerrada (A.0, A.2 y A.3 en master, evidencia de E.1; ningun precio movido, 0039 sin aplicar); fila D.0 (0039 + claves, del dueno) antes de la sonda A.4; E.0 partida en E.0a (veredicto) y E.0b (regla). Siguiente: Fase 10 del autopilot (A.1, A.7, E.0a)."
}
```

## Estado para la siguiente sesión

- Plan v1.3 sobre el spec v1.3. **Fase 8 cerrada** (2026-09-18 UTC): A.0
  (`14accfa`, PR #298), A.2 (`39cba88`, #299) y A.3 (`efc0555`, #300) en
  `master`; E.1 con su evidencia mergeada (`6127708`, #297) y la fila abierta
  por E.0b. Migración 0039 **no aplicada** en producción; ninguna clave
  `precio_*` sembrada; ningún precio movido; forma del PATCH `pendiente_sonda`.
- **Siguiente fase de implementación (Fase 10 del autopilot; la 9 es de
  openclaw)**: A.1 y luego A.7, con Muse, secuenciales; el lead hace E.0a si el
  dueño entrega el documento de origen. Runbook en
  `docs/runbooks/autopilot-fase10.md` de goncloud-openclaw.
- **Del dueño, en paralelo y en este orden**: E.2 **parte 1** (ventana, mínimo
  con histéresis y producto sin historia, con los hechos 20–22; la parte 2
  espera a E.0a), D.0 (0039 + claves como `config_version` nueva copiando la
  vigente, con `!`, **no en la misma ventana de despliegue que D.3 de
  `fabrica-02`**), goal `live` del producto controlado (con la herramienta de
  A.1) y A.4. A.5 arranca cuando A.1 esté en
  `master`; A.6 cuando A.5 y A.7 lo estén.
- **A.4 no corre sin D.0 ni sin A.1**: `cambiar_precio` lee una decisión
  `subir` en `live` que exige un goal `live` vigente; sin tablas ni goal no hay
  fila que leer. La v1.2 lo hacía depender solo de A.3 y estaba mal.
- E.1 sigue detrás de E.0: E.0a (veredicto contra el documento de origen, del
  lead con insumo del dueño) y E.0b (la regla en la ingesta, de Muse).
