# REPRICING 01 — Motor de precios por goal de margen (M1 / AUTO-07 / ORBIT 09)

Version: 1.0, 2026-09-16 UTC. Estado: **plan sellado sobre el spec v1.1
(aprobado por el dueño el 2026-09-15 y corregido por cinco perspectivas el
2026-09-16); cero implementación**. Base: `origin/master` `0617328`.
Spec: `docs/superpowers/specs/2026-09-15-repricing-01-design.md` (manda sobre
este plan). Precedencia: `docs/CONTEXTO.md` (reglas 1–10) > `plans/ROADMAP.md`
> spec de márgenes y su acta 0.3 > spec de este plan > este plan.
Tracker: `ORBIT 09 — Módulo Repricing` y `AUTO-07 Repricing (plan formal)`.
`team_validation_mode: subagent`: producto, arquitectura, seguridad, QA y
escéptico sobre el borrador; 40 hallazgos, disposición en
`docs/evidencia/repricing-01/plan-validacion.md`.
No hay fechas ni presupuestos en este plan. Ningún secreto en el repo.

## Resultado y límites

Cada producto con un goal de margen fijado por el dueño llega a ese margen
por precio, sube solo cuando le falta, baja solo cuando pierde ventas de
verdad y nunca abajo del goal. Todo cambio de precio queda en un ledger
append-only con su cuenta completa, su acuse de Amazon y su cierre por la
observación del día siguiente; la reversa existe y se prueba con ids reales
antes del primer cambio real. Todo día en que el motor no pudo evaluar un
producto queda escrito con su motivo y se ve.

Fuera de alcance, declarado: FBM, Mercado Libre, promociones, kits, decidir
por inventario (solo invalida la señal de ventas), elasticidad, modos
«agresivo/equilibrado» del traspaso, reversa automática.

## Decisiones ya cerradas (no se reabren)

Las doce de S1 del spec, con texto literal del dueño (2026-09-15). Resumen:
proteger margen; subir si el margen estimado no llega al goal por producto;
sube igual aunque rebase a la competencia (Buy Box se avisa); baja solo si las
unidades de 15 días caen contra el promedio de 60; automático dentro de
límites con sombra primero; MX y US; precio resuelto, no escalera; ningún
silencio; cuota propia.

Umbrales aceptados como **config** (`config_version.settings`, tipo JSON
número), con clave y cota; fuera de cota = `ValueError` ruidoso al leer,
como `_cap_de_config` en `app/apply.py`:

| Clave | Valor inicial | Cota |
|---|---|---|
| `precio_caida_ventas_pct` | 0.40 | 0.10–0.90 |
| `precio_senal_dias` | 3 | 1–7 |
| `precio_u60_min` | 20 | 1–1000 |
| `precio_fechas_excluidas` | `[]` (lista de `[desde, hasta]` ISO) | rangos válidos |
| `precio_escalon_max_pct` | 0.10 | 0.01–0.25 |
| `precio_movimiento_min_pct` | 0.01 | 0–0.10 |
| `precio_movimiento_min_abs_mxn` / `_usd` | 1.00 / 0.10 | > 0 |
| `precio_tolerancia` | 0.005 (fracción) | 0–0.05 |
| `precio_dias_entre_cambios` | 7 | 1–30 |
| `precio_cap_amazon_mx` / `precio_cap_amazon_us` | 5 | 0–20 |
| `precio_goal_min_pct` / `precio_goal_max_pct` | 0.10 / 0.60 | 0 < min < max < 1 |
| `precio_freno_cambios` | 3 | 2–10 |
| `precio_aviso_dias_sin_evaluar` | 3 | 1–14 |
| `precio_freno_dias_error` | 3 | 1–14 |
| `precio_divergencia_max_pct` | 0.01 | 0–0.10 |

## Hechos verificados que condicionan el plan (2026-09-15/16, lectura de producción y código)

1. `spapi_price_observation` trae a diario precio propio, Buy Box (precio,
   dueño, `is_own`), precio mínimo y conteos para **342 ASINs MX y 176 US**.
2. La estimación por venta está desplegada **solo para FBA MX** (acta 0.3).
   `leer_escenarios` entrega contribución, componentes y `fee_observation_id`;
   `fee_details` guarda el desglose (`ReferralFee`, `FBAFees`, `tax_amount`
   opcional), suficiente para modelar `F(P)` como semilla. **Pero**
   `estimacion_fee_observation` exige por FK que el precio cotizado sea el de
   la oferta, solo `app_ingest` inserta, y el lector toma la cotización más
   reciente y la declara `fee_incompatible` si el precio difiere: una
   cotización del motor ahí **apagaría la estimación del día siguiente**.
   De ahí `precio_cotizacion` (S5). `MARKETPLACE_MX` está fijo en
   `app/estimacion_fees.py`: 0.3 lo parametriza.
3. La estimación corre cada 6 h a :45 y el costo del día se sella a las
   08:15 UTC: el primer escenario `disponible` del día es el de 12:45. Un
   motor a las 09:40 vería el 100% `costo_desactualizado`. **Horario: 13:10 UTC.**
4. Unidades por producto y día: `ledger_event` `kind='sale'` con `quantity`
   y `product_id`, sin `listing_id` ni ASIN; hay productos con dos ASINs en
   la misma plataforma (0008). **La señal de ventas es por
   `(product_id, platform)`.** Volumen real: ~1 650 ventas en 290 días sobre
   ~370 productos (< 1 unidad/semana el producto típico): sin volumen mínimo
   y racha, la señal de 15 días es ruido.
5. `spapi_listing_estado_observation` trae `seller_sku`, `asin`,
   `product_type` y `status`; `spapi_inventario_observation` trae
   `total_quantity` diario. `listing.seller_sku` es NULL-able.
6. **No existe camino de escritura de precios**. `SpapiClient` solo tiene
   `get` y `post_fees` con allowlist de rutas; `VENDEDORES_PROPIOS` da el
   `sellerId`; el GET de listings pide `summaries`, no `offers`. El PATCH de
   Listings Items es **asíncrono** (`202 ACCEPTED` + `submissionId`).
7. `apply_quota_state` **no admite un motor nuevo tal cual**:
   `apply_cap_de_config` (0002) es un `CASE` cerrado y el trigger hace
   `RAISE` con clave NULL. A.0 lo amplía. `app/apply.py` importa
   `app.ads.write`: el motor de precios no importa `app.apply`.
8. `ads_optimizer_lock` (`job_key`, claim con TTL) sirve como lock de base
   para `precio:<platform>`; el `flock` del cron solo cubre el proceso.
9. `orbit_admin` hereda `app_decide` (`docs/DEPLOY.md`): el contenedor web
   alcanza las tablas del motor por herencia; el motor corre con
   `ORBIT_DSN_DECIDE`, no admin. Declarado en DEPLOY.md.
10. Avisos: `app/notifica.py` tiene el patrón de flanco por racha y es
    fail-silent. `tests/test_architecture.py` tiene candados de pureza (solo
    `app/optimizer/`), de escritor único (solo `ads_optimizer_goal`) y de
    imports de escritura (`PERMITIDOS_IMPORTAR_ADS_WRITE`): los nuevos van en
    paralelo, sin tocar los existentes.
11. Sistema viejo (tracker): fórmula sin fees → 46 días sin propuestas;
    «sustain» → −12%; stop-loss que nunca corrió. Cada uno tiene su regla.

## Diseño (lo que el implementador no decide)

Está en el spec S2–S8. Invariantes que el revisor verifica en cada fila:

- **Un camino de escritura**: `app/spapi/write_client.py` (default-deny, una
  ruta) importado solo por `app/spapi/precio_write.py`; `SpapiClient` sin
  PATCH; tres candados con fuga sembrada.
- **Un escritor de goals**: `app/precio/goals_write.py`; candado
  `_IDENT_PRECIO_GOAL` paralelo al de Ads.
- **Append-only** en `precio_goal` (solo cierra `valid_to` una vez),
  `precio_decision`, `precio_cotizacion`; `precio_cambio` solo avanza
  `pendiente → enviado | error`, `enviado → confirmado | no_confirmado` por
  trigger y `GRANT UPDATE` por columna.
- **Dinero con moneda** (regla 4) en todas las columnas de precio,
  `currency NOT NULL`.
- **Sin insumo, sin decisión**: `no_evaluado(motivo)` es una fila, nunca una
  ausencia; ninguna constante sustituye un dato; ningún umbral fuera de cota.
- **Sombra fiel**: cotiza y resuelve igual, escribe cambios virtuales que
  consumen cooldown y freno, sin PATCH ni cuota.
- **Reversa antes y nunca automática**: A.4 precede a cualquier cambio real;
  `no_confirmado` frena y avisa; la reversa la corre el dueño.
- **Cuota propia** `precio:<platform>`; `no_confirmado`, `error` y reversas
  consumen; prioridad explícita registrada.
- **Concurrencia**: advisory lock + `ads_optimizer_lock`; UNIQUE por día;
  índice único parcial de cambio abierto; orden INSERT+COMMIT → PATCH.
- **Telegram sin dinero**: solo SKU/ASIN, plataforma, precios, estado, motivo.

## Etapas y tareas

| ID | Tarea | DoD (verificable) | Deps | Estado |
|---|---|---|---|---|
| A.0 | [stage:implementacion] [lane:gate] [tdd:required] **Migración `00NN_precio.sql`** (Muse): enum `precio_mode`; `precio_goal` (S3: FK compuesta `(listing_id, platform) REFERENCES listing (id, platform)` con `UNIQUE (id, platform)` en `listing`, CHECK plataforma MX/US, CHECK banda de goal, CHECK `live ⇔ go_literal`, índice parcial un vigente, trigger «solo cierra vigencia»); `precio_decision` (S5: `UNIQUE (listing_id, platform, decision_date)`, `decision_date` por trigger UTC, `currency NOT NULL`); `precio_cotizacion`; `precio_cambio` (transiciones por trigger, índice único parcial de cambio abierto); GRANTs por columna (`app_admin` inserta goals y cierra `valid_to`; `app_decide` inserta decisiones/cotizaciones/cambios y avanza `estado, ack, readback_*, confirmado_por, error_code`; `app_read` solo lee); `apply_cap_de_config` ampliado con `'precio:amazon_mx' → 'precio_cap_amazon_mx'` y `_us`; bloque DO bajo `SET ROLE` que prueba y revierte. | Rojo-primero con PostgreSQL real: `UPDATE precio_goal SET margen_goal_pct` rechazado; segundo vigente rechazado; `live` sin `go_literal` rechazado; `valid_to` se fija una vez y no se reabre; `precio_cambio` `confirmado → pendiente` rechazado y `UPDATE precio_despues` rechazado; segundo cambio abierto del mismo listing rechazado; `decision_date` del cliente ignorado; `INSERT apply_quota_state (motor='precio:amazon_mx')` nace con `cap` de config y sin clave revienta; `app_read` no inserta nada; `docs/DATABASE.md` y `docs/DEPLOY.md` §«Migración 00NN» con backup y verificación como lector | — | cc:TODO |
| A.1 | [stage:implementacion] [lane:gate] [tdd:required] **`app/precio/goals_write.py` + `tools/precio_goal.py`** (Muse; patrón `tools/goals_modo_grupo.py`): sembrar goal (`--listing-id --platform --goal-pct 30.00`), lote desde CSV (`--csv`, deduplicado antes de la huella, `--sku` resuelto en dry-run y abortado si mapea a >1 listing o la última observación no es FBA), `--mode live` con ceremonia `--acepto-mutacion-real --esperado --huella --go`, `--mode shadow` sin ceremonia, cerrar goal (`--cerrar`). Dry-run imprime por fila `m_actual` de hoy y `P*` resuelto y aborta si `|P* − P_actual| > 25%` salvo `--confirmar-salto <listing_id>`. Solo `app_admin`, cero Amazon. | Dry-run no escribe; `--go` escribe exactamente N filas; huella distinta aborta; CSV con fila repetida aborta antes de la huella; `--goal-pct 0.30` rechazado (es fracción); goal fuera de banda rechazado en Python y en la base; `live` sin go rechazado; salto > 25% sin confirmación aborta; candado `_IDENT_PRECIO_GOAL` pasa y **falla** con un `UPDATE precio_goal` crudo sembrado en `tools/` | A.0 | cc:TODO |
| A.2 | [stage:implementacion] [lane:gate] [tdd:required] **Reglas puras** (Muse): `app/precio/tipos.py`, `app/precio/reglas.py` (`decidir(insumos, cfg, hoy) -> Decision`, sin I/O ni reloj), `app/precio/objetivo.py` (`precio_objetivo(componentes, goal, cotizar, cfg) -> Objetivo`: forma cerrada con `ref = FinalFee(ReferralFee)/P_actual`, `fijo = Σ resto`, luego ≤ 2 cotizaciones reales vía `cotizar`), `app/precio/ventas.py` (`senal_ventas(unidades_por_dia, dias_cubiertos, inventario_por_dia, listing_activo_por_dia, primera_venta, hoy, cfg) -> Senal`), `app/precio/config.py` (lectura con cotas). Banco de pruebas con fixtures de la acta (`P=116, I=100, C=40, F=15, R=2.5`). | Rojo-primero por regla de S4, con bordes: (1) cada insumo faltante → `no_evaluado(motivo)` exacto; (2) `P_actual` = precio de la oferta; divergencia 1.01% → `no_evaluado(precio_divergente)`, 0.99% no; moneda distinta → `moneda_divergente`; (3) goal 30%: `m = 29.50%` → `mantener`, `29.49%` → `subir` (tolerancia en fracción); `P*` con tercer decimal se redondea hacia arriba y `m(P_aplicado) ≥ goal`; tope `116.01·1.10` → `P_aplicado ≤ 127.61`; una cotización de verificación cierra; dos no cierran → `goal_inalcanzable(fee_no_lineal)`; `tax_amount = 1.23` → `no_evaluado(impuesto_fee_pendiente)`; no concilia → `no_evaluado(fee_error:…)`; (4) `m = 35%` con pérdida sostenida → `bajar` a `max(P_goal, P·0.90)`, `P_goal` hacia arriba; `m = goal + 0.4pp` → `mantener`; (5) `u60=600, u15=90` → no dispara (`<`), `89` sí; ventanas `[hoy−15,hoy−1]` y `[hoy−75,hoy−16]` con una venta en `hoy` y otra en `hoy−76` fuera; hueco de ledger → `sin_dato` con el día; `u60=19` → `volumen_bajo`; `n15=9` → `ventana_corta`; 74 días → `sin_historia`; un día con `total_quantity=0` → `sin_stock`; listing inactivo → `listing_inactivo`; racha 2 de 3 → no dispara, 3 sí; fechas excluidas no cuentan y el promedio se escala; dos listings del mismo producto comparten señal; (6) subida confirmada hace 10 días + caída sostenida → `frenado(perdiendo_tras_subida)`; (7) Buy Box perdida no frena; (8) diferencia 0.40 MXN → `mantener(movimiento_minimo)` sin cooldown; (9) `no_confirmado` o `error` de hace 6 días → `mantener(cooldown)`; goal nuevo no reinicia cooldown; (10) 3 cambios sin acercarse → `frenado(no_converge)`, goal nuevo reinicia; (11) `P* > 2P`, `P* ≤ C`; (12) prioridad calculada y registrada; (13) `shadow` misma decisión con `aplicado=false`. Todo en `Decimal` (mutante `float` muere). Catálogo de mutantes propio: uno por regla y por borde, todos muertos; test de pureza tipo `test_fabrica_plan_es_puro` sobre `app/precio/*` | A.0 | cc:TODO |
| A.3 | [stage:implementacion] [lane:gate] [tdd:required] **Cliente de escritura, escritura y reversa** (Muse): `app/spapi/write_client.py` (default-deny: `patch_listing(seller_id, sku, marketplace_id, body)` con `validar_patch_listings`, `seller_id ∈ VENDEDORES_PROPIOS`, 401 un refresh, 429 un reintento, sin loop, respuesta tal cual, sin instancia compartida); `app/spapi/precio_write.py` (`leer_precio_vivo(listing) -> PrecioVivo` por GET `includedData=offers`; `cambiar_precio(decision, precio, moneda) -> Cambio`: fila `pendiente` con `precio_antes` vivo y observado → PATCH `purchasable_offer` → `ack` literal → `enviado | error` → GET informativo (`readback_*`); `revertir(cambio_id) -> Cambio` con `es_reversa`; `cerrar_por_observacion(hoy)` que pasa `enviado → confirmado | no_confirmado` con `spapi_price_observation`); `tools/precio_reversa.py --cambio-id … [--cambio-id …]` (dry-run lee el precio vivo, huella, go; salta los cambios cuyo precio vivo ≠ `precio_despues`). Commits en este orden: write client → reversa → escritura. | Rojo-primero con cliente falso: ack `ACCEPTED` + GET viejo → `enviado` (no `no_confirmado`); ack error + GET nuevo → `error`; ack ok + GET distinto de ambos → `enviado` con `readback_estado=ok`; 429 agotado en readback → `readback_estado=fallido`, cero PATCH adicional; observación D+1 igual → `confirmado`, distinta → `no_confirmado`; `error` con código sin cuerpo; reversa escribe `precio_antes` y se cierra por observación; reversa por lote salta el cambio con precio vivo distinto; dry-run no llama al cliente; **tres candados** de arquitectura con fuga sembrada (`httpx.patch` crudo con `listings/2021-08-01` fuera de `precio_write.py`; import del write client desde otro módulo; import no permitido en los tools); **forma del parche `pendiente_sonda`** hasta A.4; el cuerpo del PATCH nunca se loguea (test) | A.0 | cc:TODO |
| A.4 | [stage:verificacion] [lane:release] [tdd:skip:sonda] **Sonda de escritura y reversa con ids reales** (dueño con `!`, go literal; lead lee): un producto controlado MX elegido por el dueño; cambio de `+0.01` (o el mínimo que produzca un valor que no exista en ninguna otra oferta propia ese día) por `precio_write.py`, y **reversa** por `tools/precio_reversa.py`. Sella la forma exacta del parche. | `docs/evidencia/repricing-01/A.4/` en orden temporal: observación del día anterior = `P`; fila `pendiente` con `enviado_at` **antes** del ack; ack literal con `submissionId`; GET del item con `lastUpdatedDate > enviado_at` y precio `P + 0.01`; ack y GET de la reversa; observación del día siguiente = `P`; **control negativo**: un segundo listing propio sin cambio conserva su precio; si el parche no funcionó, se corrige A.3 y se repite: **sin E/A.4 no hay D.2** | A.3 | cc:TODO |
| A.5 | [stage:implementacion] [lane:gate] [tdd:required] **Corrida diaria** (Muse): `app/precio/corrida.py` (`correr(platform, hoy) -> Resumen`: claim en `ads_optimizer_lock` `job_key='precio:<platform>'` + `pg_advisory_xact_lock`; cierra por observación los `enviado` de ayer; lee insumos de S2; llama `decidir`; escribe `precio_decision` para **todos** los goals vigentes; ordena candidatos por prioridad; aplica solo `live` bajo `app/precio/cuota.py` (`motor='precio:<platform>'`, mismo SQL atómico que `apply.py:321-327`, sin importar `app.apply`); en `shadow` escribe cambios virtuales; orden INSERT+COMMIT → PATCH → sello; freno y reintento de errores), `app/cli.py precio --platform` y `precio --reporte --desde --hasta` (tabla de D.2), cron `10 13 * * *` en el crontab de `gon` con `flock` y log (`docs/DEPLOY.md`), `config_version` con las claves de umbrales. | Rojo-primero: día sin insumos → N filas `no_evaluado` y **cero** llamadas de escritura; cuota saturada → `mantener(cuota)` por prioridad, con las cuotas de Ads intactas; reversas consumen cuota; segunda corrida del día no decide ni escribe; **dos hilos `correr` con PostgreSQL real → exactamente un PATCH** (sin el advisory lock, dos); `shadow` nunca llama a `cambiar_precio` y sí escribe virtuales que consumen cooldown; `enviado` de ayer se cierra antes de decidir hoy; freno tras 3 días de `error`; línea de crontab pinzada por test como el vigilante; umbral fuera de cota → `ValueError` al arrancar | A.1, A.2, A.3 | cc:TODO |
| A.6 | [stage:implementacion] [lane:gate] [tdd:required] **Visibilidad y avisos** (Muse): `/precios` server-rendered (cuatro bloques de S7, frases en palabras, sombra en condicional), bloque `precios` en `/api/dashboard/salud` **dentro de `plataformas.<p>`**, **un** sender `notifica_precio(tipo, …)` en flanco por racha: por `(plataforma, motivo)` con conteo y 5 SKUs para `no_evaluado`/`goal_inalcanzable`/`frenado`, por producto para `no_confirmado`/`buy_box_perdida`; fail-silent. | Rojo-primero: `/precios` 200 con los cuatro bloques y frases con números del fixture; `/salud` cuenta lo mismo que `precio_decision` del día; 200 productos con el mismo motivo → **un** aviso, dos motivos → dos; cada aviso sale una vez por racha y calla al corregirse; builder de texto **sin** costo, margen, goal ni cuerpo de error (test que lo garantiza); un fallo del sender no tumba `correr` | A.5 | cc:TODO |
| R.1 | [stage:revision] [lane:gate] [tdd:required] **Revisión independiente** (kimi sobre un SHA concreto; lead audita): catálogo de mutantes del implementador (uno por regla y borde de S4, uno por candado, transiciones de `precio_cambio`, dinero, cuota, lock) re-ejecutado con base real; cero sobrevivientes o se cierran con test en el mismo PR. | `docs/evidencia/repricing-01/R.1/` (catálogo, re-mutación, APPROVE de kimi y del lead sobre el SHA) | A.6 | cc:TODO |
| D.1 | [stage:cierre-pr] [lane:release] [tdd:skip:validacion-entrega] **Despliegue en sombra MX** (dueño con `!`): backup del schema, migración en una transacción y verificación como lector, deploy por `git archive` + md5 + rebuild, cron instalado, `precio_goal` sembrado en `shadow` para los productos que el dueño elija (lote con go), **cinco corridas** leídas por el lead. | E/D.1 con SHA, salidas y la lectura: **≥ 80% de los goals vigentes con resultado distinto de `no_evaluado`** cada día (si no, se corrige la cadena de frescura antes de seguir), cuenta de tres productos reproducida a mano, trayectoria de sombra con cooldown visible; `/precios` y `/salud` con datos reales; ningún PATCH emitido salvo A.4 | R.1 | cc:TODO |
| D.2 | [stage:cierre-pr] [lane:release] [tdd:skip:ops] **Encendido de 3–5 productos MX** con go literal (`tools/precio_goal.py --mode live`), **medición de 30 días en dos cortes** con `precio --reporte`: día 14 (subida) y día 30 (señal de ventas post-cambio). | E/D.2 con la salida literal del reporte en ambos cortes. «Funcionó» = por producto: (a) 100% de `precio_cambio` en `confirmado` (cero `no_confirmado`/`error`); (b) `|m_actual − goal| ≤ tol` o `frenado`/`goal_inalcanzable` con motivo visible; (c) al día 30, `u15` posterior ≥ esperado salvo que haya disparado `bajar` o `frenado(perdiendo_tras_subida)`, y al menos una decisión de esa rama ejercida o declarada «no ocurrió» con números; (d) cero días `no_evaluado` sin motivo; (e) Buy Box D y D+1 registrada. (a) y (d) en todos y (b) en ≥ N−1 → ampliar; cualquier `no_confirmado` → parar. Decisión literal del dueño | D.1, A.4 | cc:TODO |
| D.3 | [stage:cierre-pr] [lane:release] [tdd:skip:ops] **Ampliación MX** por lotes con go, en orden de prioridad; `ORBIT 09` y `AUTO-07` avanzan en el tracker. | E/D.3; tracker anotado | D.2 | cc:TODO |
| 0.1 | [stage:verificacion] [lane:gate] [tdd:skip:sonda] **Inventario US para márgenes** (lead de datos, solo lectura): Product Fees `amazon_us` con una oferta FBA real (`Success`, conciliación, `TaxAmount`, tipos: `PerItemFee` ¿plan Individual?); cargos recurrentes en Finances US (¿retención? ¿sales tax en el precio?); `fx_resolve` MXN→USD (el sync solo carga USD→MXN: ¿hay fuente para el par?); cobertura de `sku_cost` y ofertas US frescas ≤ 6 h. | `docs/evidencia/repricing-01/0.1/` con las sondas y sus respuestas literales; tabla de insumos US presente/ausente por componente `I, C, F, L, R` | D.2 | cc:TODO |
| 0.2 | [stage:contrato] [lane:fast] [tdd:skip:contrato] **Acta 0.4 de `margen-estimado-01`: política fiscal US sellada por el dueño** (`I = P` sin IVA; `R` = lo que 0.1 demostró o 0 declarado con razón; `L = 0` FBA; FX de `C` con fecha). Spec delta S9 en el spec de márgenes; ampliación del universo a `amazon_us/fba`. | Acta con decisión literal del dueño; ninguna ausencia convertida en cero; si un componente no tiene fuente, `amazon_us` queda `blocked` con motivo y B.x no arranca | 0.1 | cc:TODO |
| 0.3 | [stage:implementacion] [lane:gate] [tdd:required] **Estimación por venta para `amazon_us/fba`** (Muse): universo, normalización de 0.2, `marketplace_id` parametrizado en `estimacion_fees`, cotización de fees US ligada a la oferta, `fx_ausente` como motivo. | Rojo-primero: escenario US con oferta fresca produce contribución con los cinco componentes; sin FX → `null` con `fx_ausente`; sin cotización → `fee_ausente`; MX intacto (suite previa verde); mutante que fije FX constante muere; readback en prod de N listings US con estimación | 0.2 | cc:TODO |
| B.1 | [stage:cierre-pr] [lane:release] [tdd:skip:ops] **US en sombra**: goals US sembrados en `shadow`, cinco corridas leídas con el mismo umbral de D.1. | E/B.1 igual que D.1 | 0.3, D.2 | cc:TODO |
| B.2 | [stage:cierre-pr] [lane:release] [tdd:skip:ops] **Encendido US** de 3–5 productos con go y 30 días; luego ampliación. Cierra el plan; `ORBIT 09` `Done`. | E/B.2 igual que D.2; tracker `Done` con evidencia | B.1 | cc:TODO |

## Clasificación (Required / Recommended / Optional / Reject)

- **Required**: A.0–A.6, R.1, D.1, D.2; 0.1–0.3, B.1, B.2 para el alcance US
  decidido por el dueño (Fase 0 va después de D.2 por secuencia, no por
  dependencia técnica).
- **Owner-gated**: 0.2, A.4, D.1, D.2, D.3, B.1, B.2 (todo lo que toca
  producción o Amazon lleva go literal con `!`).
- **Reject** (decisión del dueño 2026-09-15): estrategias «match/beat
  competitor», «Buy Box oriented», «inventory aware», «time-based» y modos
  del traspaso; bajar precio por Buy Box perdida; explorar hacia arriba sin
  goal; aprobación manual por cambio; defaults de goal por plataforma;
  escalera de porcentaje fijo sin resolver el precio.
- **Reject** (revisión 2026-09-16): reversa automática por lectura; PATCH en
  `SpapiClient`; cotizaciones del motor en `estimacion_fee_observation`;
  cooldown que ignore `no_confirmado`/`error`; avisos por producto para
  motivos de plataforma; medición de 14 días como criterio de ampliación;
  `--sku` ambiguo en la siembra; goal como fracción en la herramienta.
- **Reject** (reglas de Orbit): recalcular margen, costo o FX dentro del motor
  (regla 2); constantes en lugar de datos faltantes (regla 3); `UPDATE`
  sobre ledgers; reintentos en ráfaga; escrituras masivas sin ceremonia.

## Propiedad de archivos y concurrencia

| Tasks | Archivos/área previstos | Restricción |
|---|---|---|
| A.0 | `migrations/00NN_precio.sql` (incluye `CREATE OR REPLACE apply_cap_de_config`), `tests/test_precio_migracion.py`, `tests/test_schema.py`, `docs/DATABASE.md`, `docs/DEPLOY.md` | Número de migración posterior al último en master al abrir el PR; el test de 0002 sobre `apply_cap_de_config` sigue verde |
| A.1 | `app/precio/goals_write.py` (nuevo), `tools/precio_goal.py` (nuevo), `tests/test_architecture.py` (candado nuevo, en paralelo) | Solo `app_admin`; cero Amazon |
| A.2 | `app/precio/{tipos,reglas,objetivo,ventas,config}.py`, `app/estimacion_fees.py` (solo `cotizar_a_precio(client, oferta, precio, marketplace_id)` reutilizando `_normalizar_detalle` y la conciliación), `tests/test_precio_reglas.py` | `app/precio/*` puro: sin I/O ni reloj (la fecha entra como argumento); `cotizar_a_precio` no cambia la ruta de la estimación |
| A.3 | `app/spapi/write_client.py` (nuevo), `app/spapi/precio_write.py` (nuevo), `tools/precio_reversa.py` (nuevo), `tests/test_spapi_write_client.py`, `tests/test_precio_write.py`, `tests/test_architecture.py` | `SpapiClient` intacto; reversa antes que escritura en el orden de commits |
| A.5 | `app/precio/corrida.py`, `app/precio/cuota.py`, `app/cli.py` (subcomando), `docs/DEPLOY.md` §cron, `tests/test_precio_corrida.py` | No toca `app/cycle.py`, `app/apply.py` ni las cuotas de Ads |
| A.6 | `app/api_dashboard.py`, plantillas, `app/notifica.py` (un sender), tests | `notifica_*` existentes intactos; bloque dentro de `plataformas` |
| 0.1–0.2 | `docs/evidencia/repricing-01/0.1/`, `docs/evidencia/margen-estimado-01/0.4/`, spec de márgenes (S9 nueva) | Solo lectura en producción; decisión literal del dueño |
| 0.3 | `app/estimacion_*.py` (universo, normalización US, `marketplace_id`), `app/fx.py` (solo si 0.1 demostró fuente para el par), tests | No cambia FBA MX; una fila `null` con motivo antes que un cero |
| R.1/D.x/B.x | Evidencia, `docs/DEPLOY.md`, PRs | Revisor solo lectura; deploy, siembra y encendido los corre el dueño con `!` |

Choca potencialmente con: **`fabrica-02` D.3** (desde el 19-sep: sin archivos
en común, comparte ventana de despliegue y atención del dueño; D.1 de este
plan va después); **`bids-01` lote de inertes (4-oct)**: sin archivos en
común; **`margen-estimado-01`** (0.2/0.3 amplían su spec y código como spec
delta, sin reabrir sus filas; A.2 agrega una función a `estimacion_fees.py`
sin tocar la ruta existente).

## Aceptación verificable

| ID | Caso | Resultado esperado | Evidencia |
|---|---|---|---|
| AC1 | Goal 30%, todos los insumos, `m = 24%` | `subir` a `min(P*, P·1.10)` (tope hacia abajo, `P*` hacia arriba), cuenta con `I, C, F, L, R`, `escenario_id`, `cotizacion_id`; una cotización de verificación | test A.2 + fila en D.1 |
| AC2 | Mismo producto sin cotización de fees hoy | `no_evaluado(fee_ausente)`, cero PATCH, visible en `/precios` y `/salud` | test A.2/A.5/A.6 + D.1 |
| AC3 | `m = 35%`, goal 30%, `u15` 50% abajo del promedio de 60 tres corridas seguidas, stock y listing activos | `bajar` a `max(P_goal, P·0.90)`, `P_goal` hacia arriba | test A.2 |
| AC4 | Igual que AC3 con 74 días de historia, o `u60 = 19`, o un día sin stock, o racha de 2 | `mantener`, señal `sin_dato(motivo)` | test A.2 |
| AC5 | PATCH ack `ACCEPTED`, GET inmediato con precio viejo; observación D+1 distinta | `enviado` hoy; `no_confirmado` mañana; `frenado(no_confirmado)` + aviso; cero reversa automática; cuota consumida | test A.3/A.5/A.6 |
| AC6 | Reversa de un cambio real por la herramienta | `precio_cambio` `es_reversa` cerrado por observación D+1 en el precio original; control negativo intacto | A.4 |
| AC7 | Tercer cambio consecutivo sin acercarse al goal | `frenado(no_converge)`, aviso; goal nuevo reinicia el freno, no el cooldown | test A.2 |
| AC8 | Cuota `precio:amazon_mx` en cap con 8 candidatos | los 5 de mayor prioridad se aplican, 3 `mantener(cuota)`; cuotas de Ads intactas | test A.5 |
| AC9 | `httpx.patch` crudo con `listings/2021-08-01` sembrado fuera de `precio_write.py` | candado de arquitectura falla | test A.3 |
| AC10 | Segunda corrida del mismo día; dos corridas simultáneas | ninguna decisión nueva; exactamente un PATCH | test A.5 |
| AC11 | Cinco corridas en sombra en producción | ≥ 80% de los goals evaluados cada día; cuenta reproducida a mano en 3 productos; cambios virtuales con cooldown visible; cero PATCH | D.1 |
| AC12 | 30 días de 3–5 productos en vivo | reporte literal de los cortes 14 y 30 con los criterios (a)–(e); decisión literal del dueño | D.2 |
| AC13 | Subida confirmada hace 10 días + caída sostenida | `frenado(perdiendo_tras_subida)`, cero PATCH, aviso | test A.2 |
| AC14 | Goal sembrado como `0.30` o fuera de banda; `--sku` con dos listings | la herramienta aborta; la base rechaza | test A.1/A.0 |
| AC15 | 200 productos `no_evaluado(ventas_sin_dato)` tres días | un solo Telegram con conteo y 5 SKUs, sin costo ni margen | test A.6 |
| AC16 | Un día con Pricing 1.5% distinto de la oferta del escenario | `no_evaluado(precio_divergente)` con los dos precios y horas; bloque propio en `/precios` | test A.2/A.6 |

## Secuencia de despliegue y reversa

1. Migración `00NN` en una transacción, después del backup del schema
   (`pre00NN_precio_*.sql`), verificación como `orbit_read` (tablas, enum,
   triggers, GRANTs por columna, índices parciales, `apply_cap_de_config`
   con las claves nuevas). Reversa: restaurar el dump en una transacción; no
   recrear tablas a mano.
2. Deploy del código por `git archive` del SHA aprobado, md5 idéntico,
   rebuild, `/health`, `/precios` 200, `/salud` con el bloque `precios`.
   Reversa: `predeploy-<stamp>/` y rebuild.
3. Cron `precio` (13:10 UTC) instalado en el crontab de `gon` (aditivo,
   `flock`, log). Reversa: quitar la línea.
4. Siembra de goals en `shadow` (dueño, go). Reversa: cerrar goals
   (`--cerrar`), sin tocar Amazon.
5. Encendido de 3–5 productos (dueño, go). Reversa: `--mode shadow` sin
   ceremonia y, si hubo cambios, `tools/precio_reversa.py --cambio-id …`.

## Divergencias y residuales declarados

- Traspaso §Módulo 1: una sola estrategia por decisión del dueño (spec,
  Divergencias). No es deuda.
- Inventario: invalida la señal de ventas, no decide precio. «Subir con stock
  bajo» sería una regla nueva con su propia decisión del dueño.
- Unidades vendidas desde el ledger contable por producto, no por
  publicación ni desde Orders: dos publicaciones del mismo producto comparten
  señal; el ledger llega con un día de rezago (dos en US) y un hueco deja
  `ventas_sin_dato`, no una bajada indebida.
- La forma exacta del PATCH de precio se sella en A.4 con producto real.
- Con cuota 5/día y cooldown de 7 días, un catálogo de ~250 goals tarda
  meses en converger: es intencional (decisión 8) y por eso D.3 amplía por
  prioridad y por lotes; el dueño puede subir `precio_cap_*` con la
  evidencia de D.2.
- Fase 0 puede terminar en «US no liberado» si 0.1 no encuentra fuente para
  algún componente; entonces B.x queda `blocked` con motivo y el plan cierra
  con MX. Se declara, no se fuerza.
- Sin reversa automática: un cambio no confirmado frena y avisa; la reversa
  es del dueño con la herramienta. Es una decisión de riesgo (una lectura
  fallida nunca dispara una mutación), no una omisión.

## Snippet para `plans/manifest.json` (aplicado en este PR; no cambia `active`)

```json
{
  "name": "repricing-01",
  "path": "plans/repricing-01.md",
  "description": "REPRICING 01 - motor de precios por goal de margen (M1/AUTO-07/ORBIT 09). Spec v1.1 aprobado por el dueno (2026-09-15) y corregido por cinco perspectivas (2026-09-16): proteger margen; sube si el margen estimado no llega al goal por producto; baja solo si las unidades de 15d caen vs promedio de 60d con volumen minimo, racha de 3 y stock presente, nunca bajo el goal; escalon 10% en ambas direcciones; automatico dentro de limites con sombra fiel primero; cuota propia; PATCH asincrono cerrado por observacion D+1; sin reversa automatica; ningun silencio. Fases: A motor FBA MX (migracion, goals sellados, reglas puras, write client + escritura + reversa con sonda real, corrida 13:10 UTC, /precios, R.1, sombra >=80% evaluados, encendido 3-5 productos y 30 dias), 0 margenes US (acta 0.4, despues de D.2), B US. Cero implementacion."
}
```

## Estado para la siguiente sesión

- Plan v1.0 sellado sobre el spec v1.1; validado por cinco perspectivas
  (evidencia en `docs/evidencia/repricing-01/plan-validacion.md`).
- Nada implementado. Primer movimiento: brief de A.0 para Muse (migración) y
  A.2 (reglas puras) pueden ir en paralelo con A.3; A.4 exige un producto
  controlado elegido por el dueño.
- Este plan no arranca antes de cerrar D.3 de `fabrica-02` (19-sep) por
  atención del dueño, no por dependencia técnica.
