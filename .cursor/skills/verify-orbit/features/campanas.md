# Campanas

Campanas lista las campanas que tienen metricas en la ventana 30d (cost, revenue, clicks, ACoS), el target efectivo y el peldaño de procedencia (cinco: goal_campana, goal_plataforma, setting_plataforma, cache_estado, default). El form GET filtra. Los `th` ordenan. Cada fila lleva su moneda. No hay total al pie.

## Sub-features

- `campanas-nav` abre `/campanas` desde el nav y marca `aria-current="page"`.
- `campanas-fila` muestra nombre, chip de estado (`activa` si `ENABLED`), plataforma · moneda, y metricas.
- `campanas-procedencia` muestra `target_efectivo.valor` y el chip del peldaño.
- `campanas-goal` muestra scope · mode · floor/ceiling, o `sin goal`.
- `campanas-filtro` el form `form.filtros` hace GET a `/campanas` (plataforma, estado, nombre, cost, revenue, clicks, acos, target, procedencia). Filtro activo sin filas pinta `Ninguna campana coincide con el filtro.`
- `campanas-orden` los `<th aria-sort>` enlazan `?ordenar=` y `dir`.
- `campanas-api` el JSON `GET /api/dashboard/campanas` es la lista sin esos filtros HTML (solo query `platform`).

## How to get to it (user POV)

- Desde Resumen, elegir `Campañas` en el nav (`<a href="/campanas">`; el texto visible lleva tilde, el href no).
- Abrir `http://127.0.0.1:<puerto>/campanas`.

## Driving it with curl

Preconditions:

- Doctor en verde.
- Semilla: fila `Campana A`, status ENABLED, goal de plataforma 25%, metrica D-15.

- **Partir de Resumen.** Corre `curl -sS "$BASE/"`. Status 200 y `data-pantalla="resumen"`.
- **Seguir el nav.** Corre `curl -sS -D - "$BASE/campanas"`. Status 200. El HTML contiene `data-pantalla="campanas"`, `h2` `Campanas — metricas 30d y target efectivo con procedencia`, y `href="/campanas"` junto a `aria-current="page"`.
- **Leer la fila sembrada.** El HTML contiene `Campana A`, chip `activa`, `amazon_us · USD`, `12.3400`, `45.6700`, target `25.00%`, chip `goal_plataforma`.
- **Filtrar.** Corre `curl -sS "$BASE/campanas?plataforma=amazon_mx"`. Status 200. El HTML contiene `Ninguna campana coincide con el filtro.` y no contiene `Campana A`. Corre `curl -sS "$BASE/campanas?plataforma=amazon_us"`: vuelve `Campana A`.
- **Ordenar.** Corre `curl -sS "$BASE/campanas?ordenar=cost&dir=desc"`. Status 200. El `th` de Cost lleva `aria-sort="descending"` y la fila `Campana A` sigue ahi.
- **Confirmar lado JSON.** Corre `curl -sS "$BASE/api/dashboard/campanas"`. Status 200. Un item tiene `nombre` `Campana A`, `status` `ENABLED`, `metricas_30d.cost` `12.3400`, `metricas_30d.clicks` `8`, `metricas_30d.acos` no nulo, `target_efectivo.peldano` `goal_plataforma`.
- **Proof.** Artefactos en `evidence/<run_id>/campanas/` (HTML de `/` y `/campanas`, headers, JSON, screenshot, filtro mx). O corre `.cursor/skills/verify-orbit/helpers/orbit-verify drive-campanas`.

## Gotchas

- El texto del nav es `Campañas` (tilde); el path es `/campanas`. Afirma el href y `data-pantalla`, no un match exacto del glifo en todos los dumps.
- Solo entran campanas con observacion en `[D-30, D-1]` (`JOIN` a `v_metric_latest`). Una campana sin metrica en la ventana no aparece.
- El JSON `GET /api/dashboard/campanas` acepta `platform` y no el resto de filtros del form. HTML y JSON pueden divergir si el HTML lleva query params.
- Clicks `0` es dato y se pinta `0`, no `—`. Solo `None` es hueco.
- ACoS con revenue 0 conocido muestra el chip `sin ventas`, no `0%`.
- La ventana 30d incluye inmaduros: los totales aun maduran. El copy del `<p class="mutado">` lo dice.
- Un 503 aqui es DSN, no "tabla vacia". Tabla vacia seria 200 sin filas `<td>`.
