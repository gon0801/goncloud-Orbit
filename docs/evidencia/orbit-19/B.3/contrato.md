# ORBIT 19 / B.3 — Contrato de disponibilidad comercial

Estado: implementado contra fuentes verificadas en 0.3. El resultado REAL
en vivo (snapshot del servidor cargado a produccion) lo corre el lead: ver
`reporte-pruebas.md`. Featured Offer queda **Sin verificar** como ampliacion
pendiente declarada; NO se declara integracion terminada.

## Fuentes (tabla (c)/(d) de 0.3, `docs/evidencia/orbit-19/0.3/reporte.md`)

| fuente | veredicto 0.3 | uso en B.3 |
|---|---|---|
| `amazon_fba_inventory.quantity_available` (por seller_sku + marketplace_id) | verificada | FBA. Frescura por fila (`fetched_at`, fresco 2026-09-06). |
| `amazon_listing_prices.quantity` con `fulfillment_channel='DEFAULT'` | verificada | FBM. En `AMAZON_NA` quantity es SIEMPRE NULL (359/359): la fila NO es observacion FBM y se omite; jamas se lee como 0 ni como desconocido FBM. |
| `amazon_inventory_cache` | inutilizable (stale ~27d, 806/806 filas en cero) | PROHIBIDO. Candado: tests/test_disponibilidad.py siembra el cache con ceros y asserts que el adaptador nunca lo consulta. |
| Featured Offer / buybox / eligibility | no_verificada (cero columnas, HITS=[]) | `estado_disponibilidad(..., aspecto="featured_offer")` devuelve `sin_verificar` constante. Ampliacion abierta, no integracion. |

Lectura del snapshot: SQLite local copiada por el lead en modo ro
(`file:...?mode=ro`), metodo 0.3 (`sqlite3 ... .backup()`). El adaptador
(`app/disponibilidad.py`) nunca toca el servidor bridge/accounting.

## Tres estados distinguibles (AC8)

| estado | significado | origen del dato |
|---|---|---|
| `cero` | cero OBSERVADO | `quantity_available=0` o `quantity=0` escritos como 0 |
| `positivo` | stock observado > 0 | cantidad > 0 en al menos una fuente |
| `desconocido` | dato faltante | quantity NULL o fila ausente (jamas se rellena con 0) |

- FBA y FBM **no se suman**: una fila append-only por fuente; la etiqueta
  expone `cantidad` y `freshness` por fuente.
- Frescura: `fetched_at` del bridge (UTC) pasa integra; un dato viejo no se
  descarta ni se maquilla, se expone.
- `estado_disponibilidad` devuelve `{estado, cantidad, fuente, freshness}`;
  `sin_verificar` SOLO existe para Featured Offer.
- Ningun estado bloquea la seleccion de publicaciones (disponibilidad es
  Recommended; B.3 no es dependencia de B.4).

## Conciliacion con Orbit

El `(platform, seller_sku)` debe existir en `listing`. SKU sin match = fila
no mapeada, contada en `ingest_run.skip_reason`, no inventada. Marketplaces
mapeados: `A1AM78C64UM0Y8` -> amazon_mx, `ATVPDKIKX0DER` -> amazon_us; uno
fuera de dominio es rechazo contado.

## Persistencia (migrations/0022_disponibilidad_snapshot.sql)

`disponibilidad_observation`: append-only (Regla 5, sin UPDATE/DELETE),
`quantity BIGINT NULL` (NULL = desconocido, CHECK >= 0), UNIQUE
`(platform, seller_sku, metric_date, fuente, observed_at)` anti-duplicado,
GRANTs a app_ingest/app_read. Ingesta sellada en `ingest_run`
(source `bridge_disponibilidad`), patron de app/listings.py.
