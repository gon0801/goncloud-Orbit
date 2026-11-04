# Validación del plan REPRICING 01 — cinco perspectivas (2026-09-16 UTC)

Base de lectura: worktree en `origin/master` `0617328`, borrador spec v1.0 +
plan v1.0 (commits `516f88f`, `fab9741`). Cinco revisores independientes en
sesiones frescas, solo lectura, con acceso al código real: producto,
arquitectura, seguridad, QA y escéptico. 40 hallazgos. Disposición del lead:
**incorporado** (spec v1.1 / plan v1.0 sellado) o **declarado** (residual con
razón). Ninguno reabre las 12 decisiones del dueño.

## Hallazgos que cambiaron el diseño (convergentes entre perspectivas)

| # | Hallazgo | Perspectivas | Disposición |
|---|---|---|---|
| 1 | El PATCH de Listings Items es asíncrono (`202 ACCEPTED` + `submissionId`); un readback inmediato produce `no_confirmado` sistemático y disparaba la reversa automática | QA C1, seguridad C1/C3, escéptico M2 | **Incorporado**: estados `pendiente → enviado \| error`, `enviado → confirmado \| no_confirmado` cerrados por la observación D+1; readback inmediato informativo con `readback_estado`; ack literal guardado; **sin reversa automática** (S6) |
| 2 | La cotización de fees a `P*` no cabe en `estimacion_fee_observation` (FK al precio de la oferta, INSERT solo `app_ingest`) y el lector tomaría la más reciente → apaga la estimación del día siguiente | arquitectura C1, QA C2, escéptico M3 | **Incorporado**: tabla `precio_cotizacion` + `cotizar_a_precio(client, oferta, precio, marketplace_id)`; `precio_decision` con `fee_observation_id` y `cotizacion_id` (S5) |
| 3 | `apply_quota_state` revienta con un motor nuevo (`apply_cap_de_config` es un CASE cerrado con `RAISE`); el hecho 6 del borrador era falso | arquitectura C2, seguridad M4 | **Incorporado**: A.0 amplía `apply_cap_de_config` con `precio:<platform>`; `app/precio/cuota.py` sin importar `app.apply`; reversas consumen cuota (S4 #12) |
| 4 | Cooldown y freno medidos solo sobre `confirmado \| pendiente`: `no_confirmado`/`error` no cuentan → escalera de +10% diaria; en sombra no existía `precio_cambio` → nunca aplicaba | seguridad C1, producto C2, escéptico m7 | **Incorporado**: cooldown sobre cualquier fila no-reversa con `enviado_at`; sombra escribe cambios virtuales que consumen cooldown y freno (S4 #9, #13) |
| 5 | A las 09:30/09:40 UTC el escenario del día aún es `costo_desactualizado` (estimación a :45 cada 6 h, costo a 08:15): 100% `no_evaluado` por diseño; además 09:30 ya lo usa `reputacion:meli` | escéptico C1, arquitectura m7, QA m8 | **Incorporado**: corrida a las 13:10 UTC; AC11 exige ≥ 80% evaluados (S8) |
| 6 | Sin inventario o listing inactivo, las ventas caen y el motor bajaría precio por una causa que no es el precio | producto C1 | **Incorporado**: la señal exige stock > 0 y listing activo en los 15 días; si no, `sin_dato(sin_stock \| listing_inactivo \| inventario_sin_observar)` (S4 #5) |
| 7 | Con el volumen real (< 1 unidad/semana el producto típico) la señal de 15 vs 60 es ruido: falsos «perdiendo» del 12–37% por día; trinquete hacia `P_goal` | escéptico M4, producto M4, QA M4 | **Incorporado**: `u60 ≥ 20`, `n15 ≥ 10`, racha de 3 corridas, fechas excluidas configurables, días cubiertos por `ingest_run`, borde `<` estricto y ventanas explícitas (S4 #5) |
| 8 | Si el producto pierde ventas justo tras una subida propia, el motor seguía subiendo | producto C3, seguridad C2(4) | **Incorporado**: `frenado(perdiendo_tras_subida)` (S4 #6), AC13 |
| 9 | Un goal mal cargado (0.03, 0.5, 0.9) bajaba 40% de golpe (sin escalón hacia abajo) o subía sin freno | seguridad C2 | **Incorporado**: escalón en ambas direcciones, banda 10–60% con CHECK, `--goal-pct 30.00`, dry-run que imprime `m_actual` y `P*` y aborta saltos > 25%, cotas en la config (S1, S3, S4 #4) |
| 10 | Tolerancia en pp contra goal en fracción (`0.24 < −0.20`); redondeo de `P_goal` sin dirección; `ceil(P·1.10)` rebasa el escalón | QA M5 | **Incorporado**: tolerancia en fracción 0.005, `P*` hacia arriba, tope hacia abajo, `Decimal`, bordes en el DoD de A.2 (S4 #3/#4) |
| 11 | Micro-movimientos que gastan cuota y cooldown; tolerancia asimétrica | escéptico M6, producto M6 | **Incorporado**: tolerancia simétrica y movimiento mínimo (S4 #4, #8) |
| 12 | `precio_divergente` se contradecía («manda el más reciente» y «no decide») | producto M5, QA m8, escéptico M6 | **Incorporado**: `P_actual` = precio de la oferta del escenario (el cotizado); Pricing es control: divergencia > 1% → `no_evaluado(precio_divergente)` con ambos precios y horas, bloque propio en `/precios`, aviso a los 3 días (S2). La nota-sin-bloqueo del escéptico se descarta: decidir con un precio que Amazon no muestra mueve dinero |
| 13 | Señal por listing no existe: el ledger va por `product_id` y hay productos con dos ASINs; «75 días de historia de la publicación» sin fuente | arquitectura M3, QA M4 | **Incorporado**: señal por `(product_id, platform)` compartida; edad desde la primera venta del producto en el ledger (S2, S4 #5) |
| 14 | `SpapiClient` no tiene PATCH; dárselo convierte a las cuatro ingestas en escritoras potenciales; el candado «construir la ruta» no es verificable | arquitectura M4, seguridad M7 | **Incorporado**: `app/spapi/write_client.py` default-deny, sin instancia compartida; tres candados con fuga sembrada; readback GET `includedData=offers` (S6) |
| 15 | GRANTs: `live` insertable por psql sin go; cerrar goal necesita UPDATE en tabla append-only; `orbit_admin` hereda `app_decide` | seguridad M5 | **Incorporado**: CHECK `live ⇔ go_literal`, trigger «solo cierra vigencia», `GRANT UPDATE (valid_to)`, transiciones de `precio_cambio` por trigger, DO bajo `SET ROLE`, motor con `ORBIT_DSN_DECIDE` declarado (S3, S5, plan hecho 9) |
| 16 | Idempotencia sin UNIQUE ni lock declarados; `decision_date` del cliente; PATCH antes del COMMIT; `platform` admite `meli` | seguridad M6, arquitectura M6 | **Incorporado**: `UNIQUE (listing_id, platform, decision_date)`, `decision_date` por trigger UTC, índice único parcial de cambio abierto, `pg_advisory_xact_lock` + `ads_optimizer_lock`, orden INSERT+COMMIT → PATCH, CHECK MX/US, test de dos hilos (S5, A.5) |
| 17 | Avisos por producto: un fallo de plataforma manda cientos de Telegrams; y los textos filtraban costo/margen a un tercero | producto M7, seguridad M8 | **Incorporado**: un sender por `(plataforma, motivo)` con conteo y 5 SKUs; por producto solo `no_confirmado`/`buy_box_perdida`; texto sin costo, margen, goal ni cuerpos de error (S7) |
| 18 | La medición de 14 días no puede ver la rama de bajada ni define «funcionó» | producto M8, QA M7 | **Incorporado**: 30 días en dos cortes con criterios (a)–(e) y `precio --reporte` reproducible (D.2, AC12) |
| 19 | La sonda A.4 no demostraba que el +0.01 lo escribió el motor | QA M6 | **Incorporado**: orden temporal, `submissionId`, `lastUpdatedDate > enviado_at`, valor único, control negativo (A.4) |
| 20 | Siembra por CSV: `platform` contradictoria, `--sku` ambiguo, listings FBM, duplicados | seguridad M8 | **Incorporado**: FK compuesta `(listing_id, platform)`, solo `listing_id` (SKU resuelto y abortado si ambiguo o no FBA), dedupe antes de la huella (S3, A.1) |
| 21 | Cuota 5/día sin orden: starvation y no reproducible; catálogo tarda meses | escéptico M5 | **Incorporado** el orden (`prioridad = \|m − goal\| · ingreso_60d`, desempate `listing_id`, registrado); **declarado** el tiempo de convergencia como intencional (decisión 8) con `precio_cap_*` ajustable tras D.2 |
| 22 | Fase 0 mal secuenciada (compite con D.3 de F2 y toca `estimacion_*` mientras A.x arranca); `PerItemFee` sugiere plan Individual en US | escéptico m8 | **Incorporado**: Fase 0 después de D.2; el hecho del plan Individual va a 0.1 como hecho a confirmar (S8) |
| 23 | Inversión de 3 pasos es más de lo necesario (F lineal en la sonda MX); no-conciliación no es estado del goal; `tax_amount` sin regla | escéptico M3, arquitectura M5 | **Incorporado**: forma cerrada + una cotización de verificación (una segunda y última); `fee_error` y `impuesto_fee_pendiente` como `no_evaluado` (S4 #3) |
| 24 | `precio_decision` sin `currency`; moneda de observación ≠ escenario | QA m8 | **Incorporado**: `currency NOT NULL`; `moneda_divergente` (S2, S5) |
| 25 | Pureza de `app/precio/*` y escritor único de `precio_goal` sin candado con la forma supuesta (el de Ads asserta una lista cerrada) | arquitectura m8 | **Incorporado**: tests paralelos (`test_precio_es_puro`, `_IDENT_PRECIO_GOAL`) sin tocar los de Ads (A.1, A.2) |
| 26 | Recortes YAGNI: cinco senders → uno; `--desde` → `--cambio-id`; Buy Box antes/después desde observaciones D y D+1 | escéptico m8 | **Incorporado** (S6, S7, S4 #7) |

## Hallazgos declarados (no incorporados) y razón

| Hallazgo | Perspectiva | Razón |
|---|---|---|
| Gate de Poisson (`P(X ≤ u15 \| λ) < 0.05`) en vez de umbral fijo | escéptico M4 | Cumple lo mismo que volumen mínimo + racha de 3 con menos explicabilidad para el dueño; la decisión 7 fija la comparación 15 vs 60; queda como mejora posible si D.2 muestra falsos positivos |
| Divergencia Pricing/oferta como nota sin bloqueo | escéptico M6 | Rechazado: decidir con un precio que Amazon no muestra mueve dinero (ver #12) |
| Sombra «solo demuestra el día 0» → declararlo | escéptico m7 | Superado por los cambios virtuales (#4) |
| Que la sombra no escriba `precio_cambio` y las frases hereden al pasar a `live` | producto C2 (variante) | Se eligió la variante con cambios virtuales que no heredan (#4) |
| Refresco extra de la estimación a las 08:30 en vez de mover el motor | escéptico C1 (alternativa a) | Mover el motor a 13:10 no toca la cadena de la estimación; se declara la alternativa por si Amazon MX exige mover precios de madrugada |

## Lo que las cinco perspectivas verificaron como correcto (sin observación)

`spapi_price_observation` con moneda y Buy Box; `fx_resolve` genérica por par
(el sync solo carga USD→MXN: cautela de 0.1 correcta); patrón de racha
fail-silent en `notifica.py`; ceremonia `--acepto-mutacion-real --esperado
--huella --go` en los tools existentes; `go_literal NOT NULL CHECK (btrim <> '')`
como patrón; `GRANT UPDATE (col)` y transiciones por trigger en 0002;
`leer_escenarios` entrega contribución, componentes y `fee_observation_id`;
`fee_details` separa `ReferralFee`/`FBAFees`; ventana de despliegue lejos de
08:40 y de las ingestas.
