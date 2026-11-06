# Campanas

Campanas lista cada campaña con métricas 30d (cost, revenue, clicks, ACoS), el target efectivo y el peldaño de procedencia (seis: goal_campana, goal_plataforma, margen_plataforma, setting_plataforma, cache_estado, default). Cada fila lleva su moneda. Arriba, una tarjeta por plataforma («Campañas contra su target») suma las filas que dejó el formulario. La vista rápida recorta solo la tabla. El JSON gemelo solo lista (filtro opcional `?platform=`).

## Sub-features

- `campanas-nav` abre `/campanas` desde el nav y marca `aria-current="page"`.
- `campanas-crear` enlace `Crear campañas` a `/campanas/nuevas` en el header (`a.btn-crear`). El texto va pegado al `>`.
- `campanas-aviso` `p.aviso-inmaduro`: «La ventana 30d incluye siempre días inmaduros (D-8..D-1): los totales y el ACoS aún maduran.»
- `campanas-tarjetas` `section.bloque-targets` con `h2#h-plat` «Campañas contra su target». Cada `article.tarjeta-plataforma` tiene `h3` «Amazon MX» o «Amazon US», moneda, «N campañas», «Target efectivo …». Si los targets de esa plataforma no son un solo número, el texto es «varios targets» y el ACoS no se colorea contra uno inventado. Métricas: ACoS (suma cost / suma revenue), Cost (30d), Revenue Ads (30d), Clicks. Un None en la suma pinta «—».
- `campanas-barra` barra `.barra` y `ul.leyenda-estados`. Orden: En target, Sobre target, Gasto sin venta, Sin target (solo si hay), Sin actividad. Colores: en `#0F766E` / franja `#14B8A6`, sobre `#B45309` / `#F59E0B`, gasto `#9A3412` / `#C2410C`, sin actividad `#4B5563` / `#D1D5DB`, sin target `#4B5563` / `#9CA3AF`.
- `campanas-vista` `div[role="tablist"][aria-label="Vista rápida"]` dentro de `section.lista-campanas` (`h2#h-lista` «Lista de campañas»). Pestañas: Todas, Sobre target, Gasto sin venta, En target, Sin actividad. Son enlaces GET `?vista=` (`todas` se omite). Valor ajeno → 422. Los conteos y las tarjetas no cambian al cambiar de pestaña; sí cambian con el formulario.
- `campanas-fila` tabla real. Franja en `tr.fila-en|sobre|gasto|sint|inactiva`. Chip de estado (`activa` si `ENABLED`). Plataforma «amazon_us · USD». En ACoS, el valor y debajo `.estado-etiqueta` («En target», «+X.XX pp sobre target», «Gasto sin venta», «Sin actividad», «Sin target»).
- `campanas-procedencia` muestra `target_efectivo.valor` y el chip del peldaño. Sin target: dos `<span class="mutado">sin target</span>` (valor y procedencia). La etiqueta de fila no usa esa clase.
- `campanas-goal` muestra scope · mode · floor/ceiling, o `sin goal`.
- `campanas-filtros` form GET `class="filtros"`: `plataforma`, `estado`, `nombre`, `procedencia`. `details.filtros-avanzados` con summary «Filtros por métricas» y `cost`, `revenue`, `clicks`, `acos`, `target` (placeholders `30d`, `30d`, vacío, `% o sin ventas`, vacío). Botón `Filtrar`. `Limpiar` → `/campanas` si hay filtro, orden o vista. Vacío filtrado: `Ninguna campaña coincide con el filtro.`
- `campanas-contador` «N de M campañas · ventana 30d incluye inmaduros». N es la vista rápida; M es el formulario.
- `campanas-orden` headers con `vista.hrefs_orden.*` (`ordenar` + `dir`). Conservan `vista` si no es `todas`.
- `campanas-scroll` `.tabla-scroll` con `overflow-x: auto` y tabla `min-width: 1100px`. En 40rem no se apilan las celdas.
- `campanas-api` el JSON `GET /api/dashboard/campanas` es la misma lista base (sin sort, filtros de UI ni `vista`).

## How to get to it (user POV)

- Desde Resumen, elegir `Campañas` en el nav (`<a href="/campanas">`).
- Abrir `http://127.0.0.1:<puerto>/campanas`.
- Filtrar con el form, cambiar la vista rápida o ordenar pulsando un header.

## Driving it with curl

Preconditions:

- Doctor en verde.
- Semilla: fila `Campana A`, status ENABLED, goal de plataforma 25%, métrica D-15. `Campana B` con métrica y sin target.

- **Partir de Resumen.** Corre `curl -sS "$BASE/"`. Status 200 y `data-pantalla="resumen"`.
- **Seguir el nav.** Corre `curl -sS -D - "$BASE/campanas"`. Status 200. El HTML contiene `data-pantalla="campanas"`, `h2` `Campañas contra su target`, `h2` `Lista de campañas`, `href="/campanas"` junto a `aria-current="page"`, `href="/campanas/nuevas"`, `class="aviso-inmaduro"`, `role="tablist"` con `Vista rápida`, `class="filtros"` con `name="estado"` / `name="procedencia"`, `Filtros por métricas` y botón `Filtrar`.
- **Leer la fila sembrada.** El HTML contiene `Campana A`, chip `activa`, `amazon_us · USD`, `12.34`, `45.67` (presentación a 2 decimales; el JSON gemelo sigue en 4), target `25.00%`, chip `goal_plataforma`.
- **Filtrar.** Corre `curl -sS "$BASE/campanas?plataforma=amazon_mx"`. Status 200. El HTML contiene `Ninguna campaña coincide con el filtro.` y no contiene `Campana A`. Corre `curl -sS "$BASE/campanas?plataforma=amazon_us"`: vuelve `Campana A`. Corre `curl -sS "$BASE/campanas?estado=ENABLED&procedencia=goal_plataforma"`: sigue `Campana A`. Corre `curl -sS "$BASE/campanas?estado=PAUSED"`: `Ninguna campaña coincide con el filtro.`
- **Vista rápida.** Corre `curl -sS "$BASE/campanas?vista=sobre"`. Status 200. La tabla queda en las filas Sobre target y el contador dice «N de M». Las tarjetas siguen contando M. `vista=no-existe` → 422.
- **Ordenar.** Corre `curl -sS "$BASE/campanas?ordenar=cost&dir=desc"`. Status 200. El `th` de Cost lleva `aria-sort="descending"` y la fila `Campana A` sigue ahí.
- **Confirmar lado JSON.** Corre `curl -sS "$BASE/api/dashboard/campanas"`. Status 200. Un item tiene `nombre` `Campana A`, `status` `ENABLED`, `metricas_30d.cost` `12.3400`, `metricas_30d.clicks` `8`, `metricas_30d.acos` no nulo, `target_efectivo.peldano` `goal_plataforma`.
- **Proof.** Artefactos en `evidence/<run_id>/campanas/`. O corre `.cursor/skills/verify-orbit/helpers/orbit-verify drive-campanas`.

## Gotchas

- El texto del nav es `Campañas` (tilde); el path es `/campanas`.
- Solo entran campañas con observación en `[D-30, D-1]`. Una campaña sin métrica en la ventana no aparece.
- El JSON no acepta `vista` ni el resto de filtros del form, salvo `platform`.
- Clicks `0` es dato y se pinta `0`, no `—`. Solo `None` es hueco.
- ACoS con revenue 0 conocido muestra el chip `sin ventas`, no `0%`.
- La suma de la tarjeta es la de las filas de Campañas, no el total del Resumen. No se «corrige» para que cuadren.
- Orden de estado: cost 0, luego cost > 0 con revenue 0, luego sin target con gasto, luego ACoS contra el target.
- Un 503 aquí es DSN, no «tabla vacía».
- Filtros, orden y estado de fila viven en `app/ui.py` y `app/ui_campanas.py`. No reimplementes el SQL.
