# Decisiones

Decisiones lista entidades que SI decidieron (una fila por `decision`). Motivo en espanol. El search_term del comprador va escapado. La pagina HTML pagina por `?page=` (PageWindow). El JSON `/api/dashboard/decisiones` sigue siendo feed por cursor.

## Sub-features

- `decisiones-nav` abre `/decisiones` y marca `aria-current="page"`.
- `decisiones-fila` muestra id, nombre, kind, decided_at, search_term, target usado, motivo, Old, New, moneda.
- `decisiones-pagina` el `nav.paginador` tiene anterior / pagina N de M / siguiente como `<a href="?page=">`.
- `decisiones-api` `GET /api/dashboard/decisiones` es el feed JSON por cursor.

## How to get to it (user POV)

- Elegir `Decisiones` en el nav (`<a href="/decisiones">`).
- Si hay mas de una pagina, pulsar `siguiente` o `anterior`.

## Driving it with curl

Preconditions:

- Doctor en verde.
- Semilla: al menos una decision bid sobre `Campana A` con motivo `banda_menos_12` y `target_acos_pct_usado` 25.00.

- **Abrir el feed.** Corre `curl -sS "$BASE/decisiones"`. Status 200. El HTML contiene `data-pantalla="decisiones"` y `h2` `Decisiones`.
- **Leer la fila sembrada.** El HTML contiene `Campana A`, chip `bid`, `25.00%` (o el target usado), y un motivo en espanol (no un stacktrace). Old/New de la semilla se ven a 2 decimales (`1.00` / `0.88`) en columnas propias, en USD.
- **Pagina (si `a[rel=next]` existe).** Corre `curl -sS "$BASE/decisiones?page=2"`. El HTML dice `pagina 2 de` y trae `rel="prev"`. Si no hay `rel=next`, el feed cabe en una pagina: anotalo, no inventes una pagina.
- **Confirmar lado JSON.** Corre `curl -sS "$BASE/api/dashboard/decisiones"`. Status 200. `items` incluye la bid de `Campana A`.
- **Proof.** Guarda HTML de `?page=1` y `?page=2` (si aplica) y el JSON bajo `evidence/<run_id>/decisiones/`.

## Gotchas

- Sin `?page=` es la pagina 1. Un `page` mayor que `pages` se clampa a la ultima.
- El search_term es texto libre: el HTML servido no puede contener `<script>` crudo. Si siembras un payload, afirma el escape `&lt;script&gt;`.
- Skips no son filas de este feed: viven agregados en Salud.
- El JSON sigue paginando por cursor (`has_more` / `next_cursor`). La pagina HTML no usa `#btn-mas`.
