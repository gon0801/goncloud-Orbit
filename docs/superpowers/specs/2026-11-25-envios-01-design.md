# ENVIOS 01 — Especificación de diseño

- **Fecha:** 2026-11-25
- **Base arquitectónica:** Diseño `/architect` de Claude Fable 5.2, revisado contra el código real de Orbit (`master` `36c9cc10`) y la sonda en vivo ([sonda.md](../../evidencia/envios-01/sonda.md)).
- **Decisiones aprobadas del dueño (2026-11-25):**
  - **1B:** Sin alertas por Telegram en v1 (solo consulta, filtros, contador y resaltado en `/envios`). No se construye tabla ni sender de alertas de envíos en v1 (Regla 1).
  - **2B:** Actualización automática cada **4 horas** para Amazon MX, Amazon US y Mercado Libre.
  - Solo lectura contra APIs externas. Cero PII almacenada o registrada en logs.
- **Estado en producción (26-nov UTC, dato del coordinador):** #782 (`3c5e53fb`) está desplegado y muestra `in_packing_list` como En camino, con un tope fijo de relectura de 90 días. Este documento describe la rama de seguimiento, que lo corrige y **aún no está desplegada**.

## 1. Arquitectura elegida (Forma B: modelo unificado + clasificador puro)

Dos tablas append-only (`envio_observation` y `envio_paquete_observation`) guardan únicamente **hechos de la fuente** (sin campos derivados, sin montos y sin PII). Un único escritor (`app/envios/store.py`) inserta una fila nueva solo cuando cambia la huella SHA-256 del envío. Un único clasificador puro (`app/envios/estado.py::clasificar`) calcula el estado unificado, el tipo logístico y si requiere atención, recibiendo `juzgado_al = min(ahora, última lectura ok de esa plataforma)` como parámetro explícito.

### Límites de responsabilidad
- **Amazon SP-API:** entra por la misma llamada `GET /orders/2026-01-01/orders` en `app/spapi/orders.py` ampliando `includedData` a `FULFILLMENT,PROCEEDS,PACKAGES` y la ventana a `min(ultimo_observado - 1d, ahora - 30d)`. `spapi_order_observation` recibe exactamente el mismo conjunto de resúmenes que hoy (`corte_resumen`); `envio_observation` se alimenta en la misma transacción vía `app/envios/amazon.py` + `app/envios/store.py`. Ningún consumidor de `spapi_order_observation` (Repricing, ledger, salud, vigilante) cambia.
- **Mercado Libre:** ingesta dedicada `app/envios/meli_ingesta.py` (CLI `python -m app.cli ingest envios_meli`, `ingest_run.source = 'envios_meli'`, `platform = 'meli'`) con `ClienteMeli` solo GET. Descubre órdenes de los últimos 30 días por `/orders/search`, agrupa packs por `shipping.id`, y relee `/shipments/{id}` para envíos nuevos o abiertos (su última observación no clasifica como cerrada, ver §3; un cerrado no se relee aunque siga en la búsqueda). Si un envío abierto ya salió de la ventana de 30 días de `/orders/search`, conserva `pedidos`, `pack_ref`, `articulos` y `pedido_creado_el` de su última fila en `v_envio_actual` para no violar `cardinality(pedidos) >= 1` ni perder sus artículos. `--releer REF` (repetible) suma envíos conocidos aunque estén cerrados; una referencia ausente de la base sale 2 sin consultar nada. Un envío sin leer (`envio_no_leido`) o ilegible (`envio_ilegible`: sin `id`, con otro `id` o sin `status` de texto; no se escribe y la observación previa queda) sella la corrida como fallida y los válidos se escriben igual; `orden_ilegible` y `orden_sin_envio` no la bajan. **Excepción (envío antiguo):** si el envío abierto ya no vino en la búsqueda y su `pedido_creado_el` es anterior a la ventana de 30 días, que es lo que la pantalla ya no muestra, el fallo queda en `skip_reason` como `envio_viejo_no_leido` o `envio_viejo_ilegible` (aviso en `/salud`) sin bajar `ok`. Una compra de hace 30 días exactos o sin `pedido_creado_el` cuenta como reciente. Un fallo sobre una referencia pedida con `--releer` baja `ok` siempre, y un 401 aborta la corrida.
- **Amazon no tiene ventana de cierre:** no hay relectura por envío, así que nada puede quedar releyéndose para siempre. Un pedido deja de venir en la búsqueda cuando lleva 30 días sin actualizarse en Amazon (`lastUpdatedAfter`) y sale de la pantalla a los 30 días de su compra. Una plataforma futura que relea envío por envío y pueda no llegar nunca a un estado final debe adoptar el mismo cierre por vencimiento (§3), con su propia ventana configurable. Subir N vuelve a poner en relectura los envíos que quedan dentro de la ventana nueva.
- **Cero PII (dos capas):**
  1. `app/spapi/client.py`: agregar `"shipfromaddress"` a `_PII_GLOBAL` para que `sanear()` descarte el objeto completo (incluyendo `name`).
  2. Lista cerrada de lectura en `app/envios/amazon.py` y `app/envios/meli.py`: nunca leen `shipFromAddress`, `buyer`, `recipient`, `receiver_address`, `sender_address`, `receiver_id`, `sender_id`, `destination`, `origin`, `comments` ni montos.

## 2. Esquema SQL (`migrations/0102_envios01_seguimiento.sql` + reversa)

Compatible con `tools/despliegue/comun.sh`: sin `BEGIN/COMMIT`, cabecera `-- sonda-aplicada:` en la línea 1, reversa `migrations/0102_reversa_envios01_seguimiento.sql`.

### `envio_observation`
- `id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY`
- `platform platform NOT NULL`
- `envio_ref TEXT NOT NULL` (Amazon: `orderId`; MeLi: `shipment.id` como texto)
- `pedidos TEXT[] NOT NULL` (`cardinality(pedidos) >= 1 AND array_position(pedidos, NULL) IS NULL`)
- `pack_ref TEXT` (MeLi `pack_id`, `NULL` en Amazon)
- `logistica_fuente TEXT` (Amazon `fulfilledBy`; MeLi `logistic_type`)
- `estado_fuente TEXT` (Amazon `fulfillmentStatus`; MeLi `shipment.status`)
- `subestado_fuente TEXT` (MeLi `shipment.substatus`; `NULL` en Amazon)
- `pedido_creado_el TIMESTAMPTZ`
- `fuente_actualizado_el TIMESTAMPTZ` (excluido de `huella`)
- `despachar_antes_de TIMESTAMPTZ` (Amazon `shipByWindow.latestDateTime`; MeLi `estimated_handling_limit.date` si viene)
- `entrega_prometida_el TIMESTAMPTZ` (Amazon `deliverByWindow.latestDateTime`; MeLi `estimated_delivery_time.date`)
- `entrega_limite_el TIMESTAMPTZ` (MeLi `estimated_delivery_time.offset.date`; `NULL` en Amazon)
- `entregado_el TIMESTAMPTZ` (MeLi `status_history.date_delivered`; `NULL` si ausente o en Amazon)
- `devuelto_el TIMESTAMPTZ` (MeLi `status_history.date_returned`; `NULL` si ausente o en Amazon)
- `articulos JSONB NOT NULL` (`jsonb_typeof(articulos) = 'array'`, objetos `{pedido_ref, sku, titulo, cantidad, producto_ref}`)
- `huella TEXT NOT NULL` (`huella ~ '^[0-9a-f]{64}$'`)
- `observed_at TIMESTAMPTZ NOT NULL`
- `ingest_run_id BIGINT NOT NULL REFERENCES ingest_run (id)`
- `UNIQUE (platform, envio_ref, observed_at)`
- Índice dedicado: `CREATE INDEX envio_observation_actual_idx ON envio_observation (platform, envio_ref, observed_at DESC, id DESC);`
- Triggers: `envio_tiempo_coherente` (en Amazon, `pedido_creado_el <= fuente_actualizado_el` usando comparación absoluta de `TIMESTAMPTZ`, sin `now()`) y `prohibir_mutacion()` para `BEFORE UPDATE OR DELETE` y `BEFORE TRUNCATE`.

### `envio_paquete_observation`
- `id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY`
- `envio_obs_id BIGINT NOT NULL REFERENCES envio_observation (id)`
- `paquete_ref TEXT NOT NULL` (`paquete_ref <> ''`; Amazon `packageReferenceId`; MeLi `shipment.id`)
- `transportista TEXT` (Amazon `carrier`; MeLi `tracking_method`)
- `servicio TEXT` (Amazon `shippingService`; `NULL` en MeLi)
- `guia TEXT` (`trackingNumber` / `tracking_number`)
- `despachado_el TIMESTAMPTZ` (Amazon `shipTime`; MeLi `status_history.date_shipped`)
- `estado_fuente TEXT` (Amazon `packageStatus.status`; `NULL` en MeLi)
- `detalle_fuente TEXT` (Amazon `packageStatus.detailedStatus`; `NULL` en MeLi)
- `UNIQUE (envio_obs_id, paquete_ref)`
- Índice: `CREATE INDEX envio_paquete_por_envio ON envio_paquete_observation (envio_obs_id);`
- Triggers: `prohibir_mutacion()` (`BEFORE UPDATE OR DELETE` y `BEFORE TRUNCATE`).

### Vista y permisos
- `v_envio_actual`: `SELECT DISTINCT ON (platform, envio_ref) * FROM envio_observation ORDER BY platform, envio_ref, observed_at DESC, id DESC;`
- `GRANT SELECT ON envio_observation, envio_paquete_observation, v_envio_actual TO app_read, app_ingest, app_admin;`
- `GRANT INSERT ON envio_observation, envio_paquete_observation TO app_ingest;`
- `GRANT USAGE ON SEQUENCE envio_observation_id_seq, envio_paquete_observation_id_seq TO app_ingest;`
- `app_decide` no recibe ningún permiso (ninguna decisión lee envíos).

## 3. Reglas de clasificación (`app/envios/estado.py`, puro)

Vocabularios cerrados:
- `Estado`: `"pendiente"`, `"por_despachar"`, `"en_camino"`, `"en_reparto"`, `"esperando_recoleccion"`, `"entregado"`, `"enviado_sin_confirmacion"`, `"problema"`, `"en_devolucion"`, `"devuelto"`, `"cancelado"`, `"cerrado_sin_confirmar"`, `"sin_clasificar"`.
- `Tipo`: `"amazon_fba"`, `"amazon_fbm"`, `"meli_full"`, `"meli_flex"`, `"meli_envios"`.
- `Motivo`: `"problema_entrega"`, `"despacho_vencido"`, `"entrega_vencida"`.
- `AMAZON_CONFIRMA_ENTREGA = frozenset({("amazon_mx", "AMZMX"), ("amazon_mx", "DHLC")})`.

### Tipo logístico
- `AMAZON` → `amazon_fba`; `MERCHANT` → `amazon_fbm`
- `fulfillment` → `meli_full`; `self_service` → `meli_flex`; `xd_drop_off`, `drop_off`, `cross_docking` → `meli_envios`; otro → `None`.

### Estado Amazon
- **Con paquetes** (por cada paquete):
  - `DELIVERED` → `entregado`
  - `UNDELIVERABLE` + `RETURNING_TO_SELLER` → `en_devolucion`
  - `UNDELIVERABLE` + `RETURNED_TO_SELLER` / `RETURNED` → `devuelto`
  - `UNDELIVERABLE` (otro detalle) → `problema`
  - `IN_TRANSIT` + `OUT_FOR_DELIVERY` → `en_reparto`
  - `IN_TRANSIT` (otro detalle, ej. `DROPPED_OFF`, `PICKED_UP`) → `en_camino`
  - `SHIPPED` con `(platform, transportista)` en `AMAZON_CONFIRMA_ENTREGA` → `en_camino`
  - `SHIPPED` fuera de `AMAZON_CONFIRMA_ENTREGA` (incl. `amazon_us/DHL` y `amazon_mx/DHL`) → `enviado_sin_confirmacion`
  - otro → `sin_clasificar`
  - **Agregado del envío:** primer estado presente en orden de severidad: `problema`, `en_devolucion`, `sin_clasificar`, `por_despachar` (solo si `envio.estado_fuente == "PARTIALLY_SHIPPED"`), `en_reparto`, `en_camino`, `enviado_sin_confirmacion`, `devuelto`, `entregado`.
- **Sin paquetes** (`fulfillmentStatus`):
  - `PENDING`, `PENDING_AVAILABILITY`, `INVOICE_UNCONFIRMED` → `pendiente`
  - `UNSHIPPED`, `PARTIALLY_SHIPPED` → `por_despachar`
  - `SHIPPED` → `enviado_sin_confirmacion`
  - `CANCELLED` → `cancelado`
  - `UNFULFILLABLE` → `problema`
  - otro → `sin_clasificar`

### Estado Mercado Libre (`status`, `substatus`, `devuelto_el`)
Una sola tabla en `app/envios/estado.py`. "Cualquiera" es una entrada explícita de la tabla; "ninguno" es `substatus` ausente.

| `status` | `substatus` | Estado |
|---|---|---|
| `pending` | cualquiera | `pendiente` |
| `handling` | ninguno | `por_despachar` |
| `ready_to_ship` | ninguno, `ready_to_print`, `printed`, `delayed`, `stale`, `in_packing_list` | `por_despachar` |
| `ready_to_ship` | `dropped_off`, `picked_up`, `in_hub` | `en_camino` |
| `shipped` | ninguno, `delayed` | `en_camino` |
| `shipped` | `out_for_delivery`, `soon_deliver`, `at_the_door`, `in_delivery_zone` | `en_reparto` |
| `shipped` | `waiting_for_withdrawal` | `esperando_recoleccion` |
| `shipped` | `bad_address`, `refused_delivery`, `not_localized`, `contact_with_carrier_required` | `problema` |
| `delivered` | cualquiera | `entregado` |
| `not_delivered` | con `devuelto_el` | `devuelto` |
| `not_delivered` | `returning_to_sender`, `returned_to_hub` | `en_devolucion` |
| `not_delivered` | cualquier otro | `problema` |
| `cancelled` | cualquiera | `cancelado` |

Un par que no está en la tabla es `sin_clasificar` (visible), nunca `por_despachar` por omisión. Una fecha que la fuente no manda se ve como `—`; no se estima ni se rellena con la del pedido.

### Cierre por vencimiento sin confirmación (solo Mercado Libre)
- `cerrado_sin_confirmar` (`Cerrado por vencimiento, sin confirmar entrega`) cuando el estado de la tabla no es `entregado`, `devuelto` ni `cancelado`, hay `pedido_creado_el` y `ahora - pedido_creado_el > N días`. A los N días exactos sigue abierto; sin `pedido_creado_el` no vence. `ahora` entra como argumento (no es `juzgado_al`).
- Cuenta como cerrado (`ESTADOS_CERRADOS`), no genera atención y nunca se presenta como entregado ni cancelado.
- N es `config_version.settings.envios_meli_dias_cierre`: texto entero de 30 (la ventana de búsqueda) a 365; sin la clave, 90. Un valor presente e inválido es error visible (`envios_meli` sale 1; API y pantalla, 503). La migración `0103` siembra `"90"`; se ajusta con `python -m app.cli envios-ventana --dias N` (por `config_write.guarda_config`).
- Nada se borra ni se reescribe. El vencido solo deja de ser elegible para relectura, salvo `--releer`; si entonces la fuente informa un estado terminal, gana la fuente con una observación nueva.

### Motivo de atención (`vendedor` = `tipo in {"amazon_fbm", "meli_flex", "meli_envios"}`)
Se devuelve el primero que aplique (o `None`):
1. `problema_entrega`: `vendedor` y `estado in {"problema", "en_devolucion"}`.
2. `despacho_vencido`: `vendedor` y `estado == "por_despachar"` y (`(despachar_antes_de is not None and juzgado_al > despachar_antes_de)` o `(platform == "meli" and subestado_fuente in {"delayed", "stale"})`).
3. `entrega_vencida`: `vendedor` y `estado in {"en_camino", "en_reparto"}` y `limite is not None and juzgado_al > limite` (donde `limite = entrega_limite_el or entrega_prometida_el`).

Con esto: **FBA, Full, US-DHL, MX-DHL y `esperando_recoleccion` jamás producen falsos positivos de entrega vencida**, y `sin_clasificar` nunca genera atención.

## 4. API, pantalla `/envios` y cron

- **Ventana única:** `DIAS_SEGUIMIENTO = 30`. `HORAS_LECTURA_ATRASADA = 8` (2 ciclos de 4 h).
- **Vistas de `/api/envios` y `/envios`:** `atencion`, `en_curso` (defecto; incluye `pendiente`, `por_despachar`, `en_camino`, `en_reparto`, `esperando_recoleccion`, `enviado_sin_confirmacion`, `problema`, `en_devolucion`, `sin_clasificar`), `cerrados` (`entregado`, `devuelto`, `cancelado`, `cerrado_sin_confirmar`), `todos`.
- **Contador de vencidos:** `vencimiento = {"dias_cierre": N, "cerrados": M}` en `GET /api/envios` y en `/envios` (`#envios-vencimiento`). M cuenta los `cerrado_sin_confirmar` sobre toda la última observación de Mercado Libre, sin la ventana de 30 días. Como N es al menos 30, un vencido ya está fuera de la ventana y no aparece en la lista: el contador es donde se ve. La consulta SQL equivalente está en `docs/DEPLOY.md`.
- **Rutas:** `GET /api/envios` (filtros `vista`, `plataforma`, `tipo`, `estado`, `q`, `page`, 50/pág, 422 ante vocabulario inválido), `GET /api/envios/contador` (`{"atencion": N}`), `GET /envios` (SSR sin JS propio + contador en `shell.js` / `base.html`: en el sidebar y en el destino `Envíos` de la barra móvil).
- **Etiquetas:** en `app/etiquetas.py` (`estado_envio`, `tipo_envio`, `motivo_envio`, `FUENTE_INGESTA["envios_meli"]`), subiendo `ALLOWLIST_TAMANO_TECHO["app/etiquetas.py"]` en `tests/test_architecture.py` con razón explícita.
- **Cron cada 4 h** en `tools/despliegue/crontab-orbit.txt` (02:25/06:25/10:25/14:25/18:25/22:25 UTC escalonados para `amazon_mx`, `amazon_us`, `envios_meli`, con `cron-avisa --cada 4` y `flock -n -E 75`).
