# REPRICING 01 — Motor de precios por goal de margen (M1 / AUTO-07 / ORBIT 09)

Version: 1.1, 2026-09-16 UTC. Estado: **plan sellado sobre el spec v1.2
(decisiones 1–12 del 2026-09-15, revisión de cinco perspectivas y decisiones
13–15 del 2026-09-16); cero implementación**. Base: `origin/master` `0617328`.
Spec: `docs/superpowers/specs/2026-09-15-repricing-01-design.md` (manda sobre
este plan). Precedencia: `docs/CONTEXTO.md` (reglas 1–10) > `plans/ROADMAP.md`
> spec de márgenes y su acta 0.3 > spec de este plan > este plan.
Tracker: `ORBIT 09 — Módulo Repricing` y `AUTO-07 Repricing (plan formal)`.
`team_validation_mode: subagent`: producto, arquitectura, seguridad, QA y
escéptico sobre las fases A y 0/B (40 hallazgos, disposición en
`docs/evidencia/repricing-01/plan-validacion.md`); **las fases E, B revisada y
M llevan su propia ronda antes de implementarse**.
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
| `precio_envio_percentil` | lo sella E.2 | 0.50–0.95 |
| `precio_envio_ventana_dias` | 90 | 30–365 |
| `precio_envio_min_envios` | 6 | 3–50 |
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
   `fee_type = 'shipping_fee'`, 180 días, todo registrado en MXN — MX 455
   cargos por 38 861, US 710 cargos por 192 607. En US el envío **supera a la
   comisión por referencia** (121 147) y el cliente pagó cero envío en las 349
   ventas del periodo (`shipping_price`).
4. **La atribución del envío al producto es exacta**: el cargo trae `order_id`
   y no `product_id`, pero 431 de 439 órdenes con etiqueta en MX y 344 de 347
   en US son de **un solo producto y una sola unidad**. Costo típico por envío:
   MX 88–96 MXN, US 520–550 MXN; cola larga real (hasta 225 en MX y 988 en US).
5. **Orbit no guarda el canal por publicación**: `listing` no tiene columna de
   canal y `app/listings.py` no lo lee, aunque el bridge lo expone
   (`amazon_listing_prices.fulfillment_channel`). Sin eso no hay recuadro de
   cobertura (decisión 14) ni separación FBA/FBM en la decisión.
6. **Mercado Libre**: el acceso a la API existe y funciona a diario
   (`ClienteMeli` en `app/reputacion_clientes.py`, **GET-only por diseño**,
   con refresco de token), pero la caché de publicaciones del bridge
   (`meli_listings_cache`, 137 items) no se refresca **desde el 2026-05-01**,
   `meli_sku_mapping` está **vacío** (sin puente al costo), y hay **cero filas
   `meli`** en `ledger_event`, `listing`, `ad_entity` y las observaciones de
   precio. MeLi no es «encender»: es traerlo a Orbit primero.
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

## Etapas y tareas

### Fase A — Amazon México, FBA (171 publicaciones activas)

| ID | Tarea | DoD (verificable) | Deps | Estado |
|---|---|---|---|---|
| A.0 | [stage:implementacion] [lane:gate] [tdd:required] **Migración `00NN_precio.sql`** (Muse): enum `precio_mode`; `precio_goal` (FK compuesta `(listing_id, platform)`, CHECK banda de goal, CHECK `live ⇔ go_literal`, índice parcial un vigente, trigger «solo cierra vigencia»); `precio_decision` (`UNIQUE (listing_id, platform, decision_date)`, `decision_date` por trigger UTC, `currency NOT NULL`, `canal`, `envio_muestra_id`); `precio_cotizacion`; `precio_envio_muestra`; `precio_cambio` (transiciones por trigger, índice único parcial de cambio abierto); GRANTs por columna; `apply_cap_de_config` ampliado con `precio:amazon_mx`, `precio:amazon_us`, `precio:meli`; bloque DO bajo `SET ROLE`. | Rojo-primero con PostgreSQL real: `UPDATE precio_goal SET margen_goal_pct` rechazado; segundo vigente rechazado; `live` sin `go_literal` rechazado; `valid_to` se fija una vez; `precio_cambio` `confirmado → pendiente` y `UPDATE precio_despues` rechazados; segundo cambio abierto del mismo listing rechazado; `decision_date` del cliente ignorado; `INSERT apply_quota_state (motor='precio:amazon_mx')` nace con `cap` de config y sin clave revienta; `app_read` no inserta; `docs/DATABASE.md` y `docs/DEPLOY.md` §«Migración 00NN» | — | cc:TODO |
| A.1 | [stage:implementacion] [lane:gate] [tdd:required] **`app/precio/goals_write.py` + `tools/precio_goal.py`** (Muse): sembrar goal (`--listing-id --platform --goal-pct 30.00`), lote CSV deduplicado, `--mode live` con ceremonia completa, `--mode shadow` sin ceremonia, `--cerrar`. Dry-run imprime `m_actual` y `P*` por fila y aborta si `|P* − P_actual| > 25%` salvo `--confirmar-salto`. Solo `app_admin`, cero Amazon. | Dry-run no escribe; `--go` escribe exactamente N filas; huella distinta aborta; CSV con fila repetida aborta antes de la huella; `--goal-pct 0.30` rechazado; goal fuera de banda rechazado en Python y en la base; `live` sin go rechazado; salto > 25% sin confirmación aborta; candado `_IDENT_PRECIO_GOAL` pasa y **falla** con un `UPDATE precio_goal` crudo sembrado en `tools/` | A.0 | cc:TODO |
| A.2 | [stage:implementacion] [lane:gate] [tdd:required] **Reglas puras** (Muse): `app/precio/{tipos,reglas,objetivo,ventas,config}.py`; `objetivo` con forma cerrada (`ref`, `fijo` de `fee_details`, `L` por canal) y ≤ 2 cotizaciones reales; `ventas` con días cubiertos, inventario y racha; `config` con cotas. Banco de pruebas con los fixtures de la acta. | Rojo-primero por regla de S4 con bordes: insumo faltante → motivo exacto; divergencia 1.01% sí y 0.99% no; goal 30% con `m = 29.50%` → `mantener` y `29.49%` → `subir`; `P*` hacia arriba con `m(P_aplicado) ≥ goal` y tope hacia abajo (`116.01·1.10 → ≤ 127.61`); `tax_amount` → `impuesto_fee_pendiente`; no concilia → `fee_error`; `u60=600, u15=90` no dispara y `89` sí; ventanas `[hoy−15,hoy−1]` y `[hoy−75,hoy−16]`; hueco de ledger, `u60=19`, `n15=9`, 74 días, un día sin stock, listing inactivo, racha 2 de 3 → cada uno su `sin_dato`; subida confirmada hace 10 días + caída → `frenado(perdiendo_tras_subida)`; `no_confirmado` de hace 6 días → `mantener(cooldown)`; 3 sin converger → `frenado`; `P* > 2P` y `P* ≤ C + L`; prioridad registrada; `shadow` igual con `aplicado=false`. Todo en `Decimal`. Catálogo de mutantes propio y test de pureza sobre `app/precio/*` | A.0 | cc:TODO |
| A.3 | [stage:implementacion] [lane:gate] [tdd:required] **Cliente de escritura, escritura y reversa de Amazon** (Muse): `app/spapi/write_client.py` (default-deny, `patch_listing` con `validar_patch_listings`, 401 un refresh, 429 un reintento, sin instancia compartida); `app/spapi/precio_write.py` (`leer_precio_vivo`, `cambiar_precio` con ack → `enviado`, readback informativo, `cerrar_por_observacion`, `revertir`); `tools/precio_reversa.py --cambio-id …`. Commits: write client → reversa → escritura. | Rojo-primero con cliente falso: ack `ACCEPTED` + GET viejo → `enviado`; ack error + GET nuevo → `error`; ack ok + GET distinto de ambos → `enviado` con readback ok; 429 agotado → `readback_estado=fallido` y cero escritura extra; observación D+1 igual → `confirmado`, distinta → `no_confirmado`; reversa se cierra por observación; reversa por lote salta el cambio con precio vivo distinto; dry-run no llama al cliente; **tres candados** con fuga sembrada (`httpx.patch` crudo, import del write client, imports de los tools); forma del parche `pendiente_sonda` hasta A.4; el cuerpo nunca se loguea | A.0 | cc:TODO |
| A.4 | [stage:verificacion] [lane:release] [tdd:skip:sonda] **Sonda de escritura y reversa con ids reales** (dueño con `!`, go literal; lead lee): un producto controlado MX; `+0.01` por `precio_write.py` y reversa por la herramienta. Sella la forma exacta del parche. | `docs/evidencia/repricing-01/A.4/` en orden temporal: observación del día anterior = `P`; fila `pendiente` con `enviado_at` antes del ack; ack con `submissionId`; GET con `lastUpdatedDate > enviado_at` y precio `P + 0.01`; ack y GET de la reversa; observación del día siguiente = `P`; **control negativo** (otro listing propio sin cambio conserva su precio); **sin E/A.4 no hay D.2** | A.3 | cc:TODO |
| A.5 | [stage:implementacion] [lane:gate] [tdd:required] **Corrida diaria** (Muse): `app/precio/corrida.py` (claim en `ads_optimizer_lock` `precio:<platform>` + advisory lock; cierra por observación los `enviado` de ayer; decide para todos los goals vigentes; ordena por prioridad; aplica solo `live` bajo `app/precio/cuota.py` sin importar `app.apply`; en `shadow` cambios virtuales; orden INSERT+COMMIT → PATCH → sello), `app/cli.py precio --platform` y `precio --reporte --desde --hasta`, cron `10 13 * * *` con `flock` y log, claves de config. | Rojo-primero: día sin insumos → N `no_evaluado` y cero escrituras; cuota saturada → `mantener(cuota)` por prioridad con las de Ads intactas; reversas consumen cuota; segunda corrida del día no decide; **dos hilos con PostgreSQL real → exactamente un PATCH**; `shadow` nunca escribe en Amazon y sí consume cooldown; `enviado` de ayer se cierra antes de decidir; freno tras 3 días de `error`; línea de crontab pinzada por test; umbral fuera de cota → `ValueError` al arrancar | A.1, A.2, A.3 | cc:TODO |
| A.6 | [stage:implementacion] [lane:gate] [tdd:required] **Pantalla y avisos** (Muse): `/precios` con los cinco bloques de S7 (cobertura arriba), bloque `precios` en `/salud` dentro de `plataformas.<p>`, **un** sender `notifica_precio(tipo, …)` en flanco por racha: por `(plataforma, motivo)` con conteo y 5 SKUs para `no_evaluado`/`goal_inalcanzable`/`frenado`, por producto para `no_confirmado`/`buy_box_perdida`; fail-silent. | Rojo-primero: `/precios` 200 con los cinco bloques y frases con números del fixture; `/salud` cuenta lo mismo que `precio_decision`; 200 productos con el mismo motivo → **un** aviso; builder de texto **sin** costo, margen, goal ni cuerpo de error; un fallo del sender no tumba `correr` | A.5, A.7 | cc:TODO |
| A.7 | [stage:implementacion] [lane:gate] [tdd:required] **Catálogo y cobertura** (decisión 14; Muse): traer a Orbit, por publicación, el **canal** (`AMAZON_NA` → `fba`, `DEFAULT` → `fbm`) y el estado vendible desde la ingesta de listings del bridge (`amazon_listing_prices`), con migración propia y `observed_at`; `app/precio/cobertura.py` que produce el recuadro de S10 por plataforma y cuadra contra el catálogo activo. | Rojo-primero: canal desconocido → `canal_sin_dato`, nunca un default; publicación sin reportar > 3 días → `catalogo_desactualizado` contada y avisada; el recuadro **cuadra exacto** con las activas del bridge en un fixture de 12 publicaciones repartidas en los cuatro estados; una publicación sin goal aparece listada, no oculta; `fuera_de_alcance` nombra la fase (`fase_E_envio_fbm`, `fase_M_meli`), no un genérico; readback en producción contra los números del bridge | A.0 | cc:TODO |
| R.1 | [stage:revision] [lane:gate] [tdd:required] **Revisión independiente** (kimi sobre un SHA; lead audita): catálogo de mutantes del implementador (una por regla y borde de S4, candados, transiciones, dinero, cuota, lock, cobertura) re-ejecutado con base real; cero sobrevivientes o se cierran con test en el mismo PR. | `docs/evidencia/repricing-01/R.1/` con catálogo, re-mutación y APPROVE de kimi y del lead sobre el SHA | A.6 | cc:TODO |
| D.1 | [stage:cierre-pr] [lane:release] [tdd:skip:validacion-entrega] **Despliegue en sombra MX (FBA)** (dueño con `!`): backup, migración en una transacción y verificación como lector, deploy por `git archive` + md5 + rebuild, cron instalado, goals sembrados en `shadow` para los productos que el dueño elija, **cinco corridas** leídas por el lead. | E/D.1 con SHA y salidas: **≥ 80% de los goals vigentes evaluados** cada día; **el recuadro de cobertura cuadra** con las 284 activas de MX (171 en alcance, 113 en `fase_E_envio_fbm`); cuenta de tres productos reproducida a mano; trayectoria de sombra con cooldown visible; ningún PATCH salvo A.4 | R.1 | cc:TODO |
| D.2 | [stage:cierre-pr] [lane:release] [tdd:skip:ops] **Encendido de 3–5 productos MX (FBA)** con go literal y **medición de 30 días en dos cortes** (14: subida; 30: señal de ventas), con `precio --reporte`. | E/D.2 con la salida literal en ambos cortes. «Funcionó» = (a) 100% de `precio_cambio` en `confirmado`; (b) `|m_actual − goal| ≤ tol` o `frenado`/`goal_inalcanzable` con motivo; (c) al día 30 `u15` ≥ esperado salvo que haya disparado la rama de pérdida, y esa rama ejercida o declarada «no ocurrió» con números; (d) cero `no_evaluado` sin motivo; (e) Buy Box D y D+1. (a) y (d) en todos y (b) en ≥ N−1 → ampliar; cualquier `no_confirmado` → parar. Decisión literal del dueño | D.1, A.4 | cc:TODO |
| D.3 | [stage:cierre-pr] [lane:release] [tdd:skip:ops] **Ampliación MX FBA** por lotes con go, en orden de prioridad. | E/D.3; cobertura de MX FBA con `sin_goal` bajando lote a lote; tracker anotado | D.2 | cc:TODO |

### Fase E — envío medido y FBM (habilita 113 activas en MX y 109 en US)

| ID | Tarea | DoD (verificable) | Deps | Estado |
|---|---|---|---|---|
| E.1 | [stage:verificacion] [lane:gate] [tdd:skip:sonda] **Medición del envío por producto** (lead, solo lectura): sobre `shipping_fee` de los últimos 180 días, por producto y plataforma — cuántos productos alcanzan 6 envíos, dispersión (mediana, p75, p90, máximo), cuántas órdenes se descartan por traer más de un producto o por no ligar a una venta, y cuánto cambia el margen estimado de una muestra de productos FBM al restar cada percentil candidato. | `docs/evidencia/repricing-01/E.1/` con las consultas y sus salidas; tabla por producto con `envios, mediana, p75, p90, max`; conteo de descartes con su razón; el efecto de p50 vs p75 vs p90 sobre el margen de al menos 10 productos | — | cc:TODO |
| E.2 | [stage:contrato] [lane:fast] [tdd:skip:contrato] **Acta del envío medido** (dueño): con los números de E.1, sella el **percentil**, la **ventana**, el **mínimo de envíos** y qué pasa con un producto sin historia; y si el cobro de envío al cliente entra al ingreso donde exista. Propuesta del lead: p75, 90 días, mínimo 6, sin historia no se evalúa. | `docs/evidencia/repricing-01/E.2/acta.md` con la decisión literal del dueño; spec delta en S10; ninguna ausencia convertida en cero | E.1 | cc:TODO |
| E.3 | [stage:implementacion] [lane:gate] [tdd:required] **`L` medido en el margen estimado FBM** (Muse): `app/precio/envio.py` (`muestra_envio(product_id, platform, hoy, cfg) -> Muestra` puro sobre filas ya leídas) + la lectura que las trae; persistencia en `precio_envio_muestra`; el escenario FBM produce contribución con `L` de la muestra y `F` de la cotización de referral; `shipping_price` de la venta al ingreso donde exista. | Rojo-primero: orden con dos productos **excluida** de la muestra (y contada aparte); 5 envíos → `envio_sin_historia`; 6 envíos → muestra con el percentil sellado; percentil calculado sobre `Decimal` y verificado contra un caso a mano; ventana que excluye un envío de hace 91 días; producto FBA nunca toca este camino (`L = 0`); la muestra queda ligada a la decisión (`envio_muestra_id` no nulo en toda decisión FBM); mutante que use promedio en vez del percentil muere | E.2, A.2 | cc:TODO |
| E.4 | [stage:implementacion] [lane:gate] [tdd:required] **El motor distingue canal** (Muse): `canal` en la decisión y en el recuadro de cobertura; `precio_goal` admite publicaciones FBM; la corrida evalúa FBA y FBM con la misma regla y distinta `L`; `/precios` y `/salud` muestran el canal y, en FBM, la muestra de envío con su dispersión. | Rojo-primero: una publicación FBM con goal y muestra completa produce `subir` con `L` de la muestra en la cuenta; la misma sin muestra → `no_evaluado(envio_sin_historia)`; el recuadro de cobertura de MX pasa de 113 en `fuera_de_alcance` a 113 en alcance; `/precios` muestra «envío medido p75: 94.50 MXN sobre 18 envíos» y no un número sin origen | E.3, A.7 | cc:TODO |
| E.5 | [stage:cierre-pr] [lane:release] [tdd:skip:ops] **Sombra y encendido FBM en México**: goals FBM en `shadow`, cinco corridas leídas, luego 3–5 productos en vivo con go y 30 días en dos cortes. | E/E.5 con el mismo criterio de D.1 y D.2, más: ningún producto FBM movido sin `envio_muestra_id`; la cobertura de MX cuadra con las 284 activas y `fuera_de_alcance` en cero | E.4, D.2 | cc:TODO |

### Fase 0 — política fiscal de Estados Unidos

| ID | Tarea | DoD (verificable) | Deps | Estado |
|---|---|---|---|---|
| 0.1 | [stage:verificacion] [lane:gate] [tdd:skip:sonda] **Inventario US** (lead, solo lectura): cotización de fees para una oferta **FBM** real de US (`Success`, conciliación, `TaxAmount`, tipos devueltos; `PerItemFee` sugiere plan Individual: confirmar, no suponer); cargos recurrentes en Finances US (¿retención? ¿sales tax en el precio?); `fx_resolve` MXN→USD (hoy el sync solo carga USD→MXN); cobertura de `sku_cost` y de ofertas US frescas. | `docs/evidencia/repricing-01/0.1/` con las sondas y respuestas literales; tabla de insumos US presente/ausente por componente `I, C, F, L, R` | D.2 | cc:TODO |
| 0.2 | [stage:contrato] [lane:fast] [tdd:skip:contrato] **Acta 0.4 de `margen-estimado-01`: política fiscal US** (dueño): `I = P` sin IVA; `R` según 0.1 o 0 declarado con razón; `L` = envío medido de la fase E; FX de `C` con fecha. Amplía el universo a `amazon_us`. | Acta con decisión literal; si un componente no tiene fuente, `amazon_us` queda `blocked` con motivo y la fase B no arranca | 0.1, E.2 | cc:TODO |
| 0.3 | [stage:implementacion] [lane:gate] [tdd:required] **Estimación por venta para US** (Muse): universo, normalización de 0.2, `marketplace_id` parametrizado en `estimacion_fees`, cotización US ligada a la oferta, `fx_ausente` como motivo. | Rojo-primero: escenario US con oferta fresca produce contribución con sus componentes; sin FX → `null` con `fx_ausente`; sin cotización → `fee_ausente`; MX intacto (suite previa verde); mutante que fije FX constante muere; readback en producción de N listings US | 0.2 | cc:TODO |

### Fase B — Amazon Estados Unidos (109 activas, todas FBM)

| ID | Tarea | DoD (verificable) | Deps | Estado |
|---|---|---|---|---|
| B.1 | [stage:cierre-pr] [lane:release] [tdd:skip:ops] **US en sombra**: goals US en `shadow`, cinco corridas leídas con el umbral de D.1. | E/B.1 igual que D.1; el recuadro de cobertura de US cuadra con las 109 activas | 0.3, E.4 | cc:TODO |
| B.2 | [stage:cierre-pr] [lane:release] [tdd:skip:ops] **Encendido US** de 3–5 productos con go y 30 días; luego ampliación por lotes. | E/B.2 igual que D.2; cobertura de US con `sin_goal` bajando lote a lote | B.1 | cc:TODO |

### Fase M — Mercado Libre (137 publicaciones)

| ID | Tarea | DoD (verificable) | Deps | Estado |
|---|---|---|---|---|
| M.1 | [stage:verificacion] [lane:gate] [tdd:skip:sonda] **Inventario de insumos de MeLi** (lead, solo lectura): por qué la caché del bridge no se refresca desde el 2026-05-01 y qué la refrescaría; qué devuelve la API por item (precio, estado, categoría, envío, comisión) con el cliente GET que ya existe; si hay fuente para mapear SKU de MeLi a producto de Odoo (`meli_sku_mapping` está vacío); qué trae el reporte de liquidación de MeLi (comisión, envío, impuesto) y si se puede ingerir con el patrón del ledger. | `docs/evidencia/repricing-01/M.1/` con las sondas literales y una tabla de los cinco insumos del margen (`I, C, F, L, R`) con su fuente propuesta o su ausencia declarada | D.2 | cc:TODO |
| M.2 | [stage:contrato] [lane:fast] [tdd:skip:contrato] **Acta de MeLi** (dueño): con M.1, sella la fórmula del margen en MeLi (impuesto sobre el precio, comisión por categoría, envío, retención), el mapeo SKU→producto y el equivalente observable de la Buy Box, o declara qué queda sin fuente. | Acta con decisión literal; si falta un componente, MeLi queda `blocked` con motivo y M.3+ no arrancan | M.1 | cc:TODO |
| M.3 | [stage:implementacion] [lane:gate] [tdd:required] **Ingesta de MeLi a Orbit** (Muse): publicaciones y precios a `listing` y a una observación de precio propia; ventas y cargos (comisión, envío, impuesto) a `ledger_event` con el patrón y los candados del ledger existente; mapeo SKU→producto poblado. | Rojo-primero: item sin SKU mapeado → fila no se escribe con `product_id` inventado; cargo sin tipo conocido → `otros` y contado, nunca descartado; ingesta idempotente por `(external_id, observed_at)`; MX/US intactos; readback en producción con las 137 publicaciones y sus precios | M.2 | cc:TODO |
| M.4 | [stage:implementacion] [lane:gate] [tdd:required] **Margen estimado de MeLi** (Muse): la fórmula del acta M.2 con el mismo contrato de ausencias que Amazon. | Rojo-primero: cada componente ausente produce su motivo; ninguna ausencia vale cero; una publicación con todo produce contribución reproducible a mano | M.3 | cc:TODO |
| M.5 | [stage:implementacion] [lane:gate] [tdd:required] **Escritura de precio en MeLi y su reversa** (Muse): `app/meli/write_client.py` default-deny con una sola ruta de actualización de item; `app/meli/precio_write.py` con el mismo orden (fila, escritura, ack, readback, cierre) y la reversa primero; `ClienteMeli` **intacto**; candado de arquitectura propio. Más **sonda con ids reales** del dueño (`!`, go literal) que sella la forma y la sincronía de la escritura. | Rojo-primero con cliente falso, los mismos casos que A.3; candado con fuga sembrada; `ClienteMeli` sigue rechazando todo método que no sea GET (test); evidencia `M.5/` de la sonda con el control negativo, igual que A.4 | M.4 | cc:TODO |
| M.6 | [stage:cierre-pr] [lane:release] [tdd:skip:ops] **MeLi en sombra y encendido**: goals en `shadow`, cinco corridas, 3–5 productos en vivo con go y 30 días. Cierra el plan; `ORBIT 09` `Done`. | E/M.6 igual que D.1 y D.2; la cobertura de MeLi cuadra con las publicaciones activas y `fuera_de_alcance` en cero en las tres plataformas | M.5, B.2 | cc:TODO |

## Clasificación (Required / Recommended / Optional / Reject)

- **Required**: A.0–A.7, R.1, D.1–D.3 (fase A); E.1–E.5 (fase E, decisión 13);
  A.7 y el recuadro de cobertura (decisión 14); 0.1–0.3 y B.1–B.2 (US);
  M.1–M.6 (decisión 15).
- **Owner-gated**: A.4, D.1, D.2, D.3, E.2, E.5, 0.1, 0.2, B.1, B.2, M.1, M.2,
  M.5 (sonda), M.6.
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
  cuenta aparte); usar el promedio de envío en vez del percentil sellado;
  suponer que el cliente paga envío donde el ledger dice que no; cubrir MeLi
  con un margen parcial «mientras llega» el resto de los insumos.
- **Reject** (reglas de Orbit): recalcular margen, costo o FX dentro del motor;
  constantes en lugar de datos faltantes; `UPDATE` sobre ledgers; reintentos en
  ráfaga; escrituras masivas sin ceremonia.

## Propiedad de archivos y concurrencia

| Tasks | Archivos/área previstos | Restricción |
|---|---|---|
| A.0 | `migrations/00NN_precio.sql` (incluye `CREATE OR REPLACE apply_cap_de_config`), `tests/test_precio_migracion.py`, `tests/test_schema.py`, `docs/DATABASE.md`, `docs/DEPLOY.md` | Número posterior al último en master al abrir el PR; el test de 0002 sobre `apply_cap_de_config` sigue verde |
| A.1 | `app/precio/goals_write.py`, `tools/precio_goal.py`, `tests/test_architecture.py` (candado nuevo, en paralelo) | Solo `app_admin`; cero Amazon |
| A.2 | `app/precio/{tipos,reglas,objetivo,ventas,config}.py`, `app/estimacion_fees.py` (solo `cotizar_a_precio`), `tests/test_precio_reglas.py` | `app/precio/*` puro: sin I/O ni reloj |
| A.3 | `app/spapi/write_client.py`, `app/spapi/precio_write.py`, `tools/precio_reversa.py`, tests | `SpapiClient` intacto; reversa antes que escritura |
| A.5 | `app/precio/corrida.py`, `app/precio/cuota.py`, `app/cli.py`, `docs/DEPLOY.md` §cron, tests | No toca `app/cycle.py`, `app/apply.py` ni las cuotas de Ads |
| A.6 | `app/api_dashboard.py`, plantillas, `app/notifica.py` (un sender), tests | `notifica_*` existentes intactos |
| A.7 | `migrations/00NN+1_listing_canal.sql`, `app/listings.py` (lee el canal del bridge), `app/precio/cobertura.py`, tests | La ingesta de listings sigue idempotente; no cambia el precio que ya escribe |
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
| **AC17** | **Recuadro de cobertura** en cualquier corrida | activas = evaluadas + no evaluadas + sin goal + fuera de alcance, **exacto**, por plataforma; una publicación sin reportar 4 días sale como `catalogo_desactualizado` y avisa | A.7, D.1, E.5, B.1, M.6 |
| **AC18** | **Orden FBM con dos productos** en la ventana de envío | excluida de la muestra y contada aparte; nunca repartida | E.3 |
| **AC19** | **Producto FBM con 5 envíos** | `no_evaluado(envio_sin_historia)`, cero escritura | E.3 |
| **AC20** | **Decisión FBM aplicada** | `envio_muestra_id` no nulo, con ventana, envíos y percentil; `/precios` muestra el número con su origen | E.4/E.5 |
| **AC21** | **Publicación de MeLi antes de su fase** | aparece en cobertura como `fuera_de_alcance(fase_M_meli)`, nunca ausente | A.7 |
| **AC22** | **Item de MeLi sin SKU mapeado** en la ingesta | la fila no se escribe con `product_id` inventado; queda contada con motivo | M.3 |

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
  de una cotización; por eso lleva su muestra adjunta y su percentil sellado.
  Un producto sin historia de envíos no se evalúa.
- **La cola del envío es real**: un envío lejano cuesta cerca del doble del
  típico. El percentil sellado en E.2 define cuánto de esa cola absorbe el
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

## Snippet para `plans/manifest.json` (aplicado en este PR; no cambia `active`)

```json
{
  "name": "repricing-01",
  "path": "plans/repricing-01.md",
  "description": "REPRICING 01 - motor de precios por goal de margen (M1/AUTO-07/ORBIT 09). Spec v1.2: proteger margen; goal por producto; sube si el margen estimado no llega; baja solo si caen las unidades de 15d vs 60d con volumen minimo, racha de 3 y stock; escalon 10% en ambas direcciones; sombra fiel; cuota propia; escritura asincrona cerrada por observacion D+1; sin reversa automatica; ningun silencio. Ampliacion 2026-09-16 (decisiones 13-15): FBM entra con el envio medido de shipping_fee del ledger (percentil sellado, muestra adjunta), toda publicacion activa queda contemplada en un recuadro de cobertura que cuadra, y MeLi entra al alcance. Fases: A Amazon MX FBA (171 activas), E envio medido y FBM (habilita 113 MX + 109 US), 0 politica fiscal US, B Amazon US (109 activas, TODAS FBM: US no tiene FBA activo), M Mercado Libre (137 publicaciones; hoy sin precios frescos, sin mapeo SKU y sin dinero en el ledger). Cero implementacion."
}
```

## Estado para la siguiente sesión

- Plan v1.1 sellado sobre el spec v1.2. La fase A y las fases 0/B originales
  están validadas por cinco perspectivas; **las fases E, B revisada y M
  necesitan su propia ronda** antes de implementarse.
- Nada implementado. Primer movimiento: brief de A.0 (migración) y A.2
  (reglas puras) para Muse, en paralelo con A.3; **E.1 puede arrancar ya**
  porque es solo lectura y su resultado es insumo del acta E.2.
- Este plan no arranca antes de cerrar D.3 de `fabrica-02` (19-sep) por
  atención del dueño, no por dependencia técnica.
