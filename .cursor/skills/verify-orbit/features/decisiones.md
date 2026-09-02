# Decisiones

Decisiones es el feed de entidades que SI decidieron (una fila por `decision`). Motivo en espanol. El search_term del comprador va escapado. La paginacion es por cursor (`id <`), nunca offset.

## Sub-features

- `decisiones-nav` abre `/decisiones` y marca `aria-current="page"`.
- `decisiones-fila` muestra id, nombre, kind, decided_at, search_term, target usado, motivo, old/new, moneda.
- `decisiones-cursor` el boton `Cargar mas` (`#btn-mas`, `data-cursor`) pide `/decisiones?cursor=<id>`.
- `decisiones-api` `GET /api/dashboard/decisiones` es el mismo feed.

## How to get to it (user POV)

- Elegir `Decisiones` en el nav (`<a href="/decisiones">`).
- Si hay mas paginas, pulsar `Cargar mas (pagina siguiente)`.

## Driving it with curl

Preconditions:

- Doctor en verde.
- Semilla: al menos una decision bid sobre `Campana A` con motivo `banda_menos_12` y `target_acos_pct_usado` 25.00.

- **Abrir el feed.** Corre `curl -sS "$BASE/decisiones"`. Status 200. El HTML contiene `data-pantalla="decisiones"` y `h2` `Decisiones — feed por cursor (solo entidades que decidieron)`.
- **Leer la fila sembrada.** El HTML contiene `Campana A`, chip `bid`, `25.00%` (o el target usado), y un motivo en espanol (no un stacktrace). `old_value`/`new_value` de la semilla son `1.00` / `0.88` en USD.
- **Cursor (si `#btn-mas` existe).** Lee `data-cursor` del boton. Corre `curl -sS "$BASE/decisiones?cursor=<ese-id>"`. La pagina solo trae ids menores. Si no hay boton, el feed cabe en una pagina: anotalo, no inventes un cursor.
- **Confirmar lado JSON.** Corre `curl -sS "$BASE/api/dashboard/decisiones"`. Status 200. `items` incluye la bid de `Campana A`; `has_more`/`next_cursor` coinciden con la presencia de `#btn-mas`.
- **Proof.** Guarda HTML ± cursor y el JSON bajo `evidence/<run_id>/decisiones/`.

## Gotchas

- Sin `?cursor=` siempre es la primera pagina. Ignorar el query era un bug real: 'Cargar mas' recargaba lo mismo.
- Motivo desconocido se pinta con el id crudo. La semilla trae un `negative` con `term_sin_ventas` (no esta en el dict): no es un stacktrace.
- El search_term es texto libre: el HTML servido no puede contener `<script>` crudo. Si siembras un payload, afirma el escape `&lt;script&gt;`.
- Skips no son filas de este feed: viven agregados en Salud.
- El boton solo se renderiza si `has_more` y `next_cursor`. Ausencia del boton no es fallo si el JSON dice `has_more: false`.
