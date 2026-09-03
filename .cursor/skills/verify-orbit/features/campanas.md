# Campanas

Campanas lista cada campana con metricas 30d (cost, revenue, clicks, ACoS), el target efectivo y el peldaño de procedencia (cinco: goal_campana, goal_plataforma, setting_plataforma, cache_estado, default). Cada fila lleva su moneda. No hay total al pie. La UI filtra y ordena en memoria via query GET (form `class="filtros"` + headers sort); el JSON gemelo solo lista (filtro opcional `?platform=`).

## Sub-features

- `campanas-nav` abre `/campanas` desde el nav y marca `aria-current="page"`.
- `campanas-fila` muestra nombre, chip de estado (`activa` si `ENABLED`), plataforma · moneda, y metricas.
- `campanas-procedencia` muestra `target_efectivo.valor` y el chip del peldaño.
- `campanas-goal` muestra scope · mode · floor/ceiling, o `sin goal`.
- `campanas-filtros` form GET (`plataforma`, `estado`, `nombre`, `cost`, `revenue`, `clicks`, `acos`, `target`, `procedencia`) + boton `Filtrar`; `Limpiar` → `/campanas` solo si hay filtro/sort activo. Vocab cerrado fuera de rango → 422. Vacio filtrado: `Ninguna campana coincide con el filtro.`
- `campanas-orden` headers con `vista.hrefs_orden.*` (`ordenar` + `dir`); CSP: cero JS en esta pantalla.
- `campanas-api` el JSON `GET /api/dashboard/campanas` es la misma lista base (sin sort/filtros de UI salvo `platform`).

## How to get to it (user POV)

- Desde Resumen, elegir `Campañas` en el nav (`<a href="/campanas">`; el texto visible lleva tilde, el href no).
- Abrir `http://127.0.0.1:<puerto>/campanas`.
- Filtrar con el form o ordenar pulsando un header de columna.

## Driving it with curl

Preconditions:

- Doctor en verde.
- Semilla: fila `Campana A`, status ENABLED, goal de plataforma 25%, metrica D-15.

- **Partir de Resumen.** Corre `curl -sS "$BASE/"`. Status 200 y `data-pantalla="resumen"`.
- **Seguir el nav.** Corre `curl -sS -D - "$BASE/campanas"`. Status 200. El HTML contiene `data-pantalla="campanas"`, `h2` `Campanas — metricas 30d y target efectivo con procedencia`, `href="/campanas"` junto a `aria-current="page"`, y `class="filtros"` con `name="estado"` / `name="procedencia"` y boton `Filtrar`.
- **Leer la fila sembrada.** El HTML contiene `Campana A`, chip `activa`, `amazon_us · USD`, `12.34`, `45.67` (presentacion a 2 decimales; el JSON gemelo sigue en 4), target `25.00%`, chip `goal_plataforma`.
- **Filtrar.** Corre `curl -sS "$BASE/campanas?estado=ENABLED&procedencia=goal_plataforma"`. Status 200 y sigue mostrando `Campana A`. Corre `curl -sS "$BASE/campanas?estado=PAUSED"`: el HTML contiene `Ninguna campana coincide con el filtro.` (semilla ENABLED).
- **Confirmar lado JSON.** Corre `curl -sS "$BASE/api/dashboard/campanas"`. Status 200. Un item tiene `nombre` `Campana A`, `status` `ENABLED`, `metricas_30d.cost` `12.3400`, `metricas_30d.clicks` `8`, `metricas_30d.acos` no nulo, `target_efectivo.peldano` `goal_plataforma`.
- **Proof.** Artefactos en `evidence/<run_id>/campanas/` (HTML de `/` y `/campanas`, headers, JSON, screenshot). O corre `.cursor/skills/verify-orbit/helpers/orbit-verify drive-campanas`.

## Gotchas

- El texto del nav es `Campañas` (tilde); el path es `/campanas`. Afirma el href y `data-pantalla`, no un match exacto del glifo en todos los dumps.
- Clicks `0` es dato y se pinta `0`, no `—`. Solo `None` es hueco.
- ACoS con revenue 0 conocido muestra el chip `sin ventas`, no `0%`.
- La ventana 30d incluye inmaduros: los totales aun maduran. El copy del `<p class="mutado">` lo dice.
- Un 503 aqui es DSN, no "tabla vacia". Tabla vacia seria 200 sin filas `<td>`.
- Filtros/sort son capa UI (`app/ui.py::filtra_y_ordena_campanas`); no reimplementes el SQL del JSON.
