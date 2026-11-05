# Resumen

Resumen presenta el contrato multicanal de publicidad. Por omisión entran todos
los canales (Amazon MX, Amazon US y Mercado Libre) y 30 días, sin el día en
curso. Cada canal conserva su moneda. None se ve como raya. El cero se ve como
0. Mercado Libre no hereda la ventana D-8 de Amazon.

## Sub-features

- `resumen-nav` abre `/` desde el sidebar (`aside.sidebar`) y deja `aria-current="page"` en Resumen.
- `resumen-filtros` muestra píldoras de canal (`Todos`, `Amazon MX`, `Amazon US`, `Mercado Libre`), píldoras de periodo (`7 dias`, `30 dias`, `90 dias`), y `<details class="custom-range">` plegado con `form.filtros` (`desde`/`hasta`). Al enviar, la elección queda en la URL y en los controles.
- `resumen-tarjetas` (sección `1. Cómo va cada canal`) muestra 1 tarjeta por canal visible con etiqueta `Bien` / `Atención` / `Sin dato`, ACoS grande con variación y valor previo, `Meta ACoS` con enlace `Configurar` a `/settings`, mini-sparkline SVG de `ma7.ad_revenue`, 3 mini-KPIs (`Ventas`, `Gasto`, `ROAS`) y frase resumen.
- `resumen-que-revisar` (sección `2. Qué revisar`) lista diagnósticos priorizados calculados desde las cifras del periodo elegido con enlaces a `/gasto-sin-venta`, `/busquedas-sin-venta`, `/campanas` y `/decisiones`.
- `resumen-tendencia` (sección `3. Tendencia · ventas vs gasto`) presenta una tarjeta de gráfica con pestañas por canal (`button[data-tab-canal]`) y `canvas#tendencia-<canal>` dentro de `.lienzo`. Lee `script#datos-resumen`. El día va fino y `ma7` es la línea gruesa. `null` es hueco.
- `resumen-monedas` (sección `4. Totales por moneda`) muestra tarjetas oscuras `.curr-card` para `MXN` y `USD` sin mezclar divisas.
- `resumen-detalle` (sección `5. Tablas detalladas y series diarias`) pliega con `<details class="detail-box">` la `Tabla por canal`, `Periodo anterior por canal` y las tablas `#serie-<canal>` (`Datos diarios · <canal>`).
- `resumen-api` el JSON `GET /api/dashboard/series/plataforma?platform=amazon_us` sigue describiendo la serie antigua. El Resumen nuevo consume `dash.resumen_ads` (`/api/dashboard/resumen`).

## How to get to it (user POV)

- Abrir `http://127.0.0.1:<puerto>/`.
- Elegir el enlace `Resumen` del sidebar (`<a href="/">` dentro de `aside.sidebar`).
- Cambiar canal, periodo o rango y pulsar `Filtrar`.

## Driving it with curl

Preconditions:

- Doctor en verde sobre la instancia de este run.
- Semilla presente (amazon_us tiene al menos un dia con cost/revenue).

- **Abrir Resumen.** Corre `curl -sS -D - "$BASE/"`. Status 200. El HTML contiene `data-pantalla="resumen"`, `<title>` `Orbit — Dashboard`, `<h1>` `Resumen`, `aside.sidebar`, `nav a[href="/"]` con `aria-current="page"`, `href="/inertes"`, `href="/campanas/nuevas"` y `href="/settings"`, `html[data-tema="dia"]` y `button#btn-tema`. También `Resumen de publicidad`, `value="" selected`, `value="30" selected`, `id="datos-resumen"`, `Tabla por canal`, `Periodo anterior`, `id="tendencia-amazon_us"`, `id="serie-amazon_us"`, `media de 7 dias` y `Amazon MX · Amazon US · Mercado Libre`.
- **Cambiar canal y periodo.** Corre `curl -sS "$BASE/?canal=meli&periodo=7"`. Status 200. El HTML contiene `value="meli" selected` y `value="7" selected`. El tramo de `<h2>Mercado Libre` no contiene `Cifras inmaduras`.
- **Periodo 90.** Corre `curl -sS "$BASE/?periodo=90"`. Status 200 y `value="90" selected`.
- **Rango.** Corre `curl -sS "$BASE/?desde=2026-10-01&hasta=2026-10-07"`. Status 200, las dos fechas en los inputs y el texto `rango personalizado`.
- **Confirmar lado JSON antiguo.** Corre `curl -sS "$BASE/api/dashboard/series/plataforma?platform=amazon_us"`. Status 200. `plataforma` es `amazon_us`, `moneda` es `USD`, y el día sembrado tiene `cost` `12.3400` y `ad_revenue` `45.6700`. Un hueco sigue en `null`.
- **Proof.** Guarda el HTML de `/`, los filtros, el JSON de la serie y las capturas (1280, 7, 90, rango, Mercado Libre, 390 px y tema noche) bajo `evidence/<run_id>/resumen/`. A 390 px el body no se desplaza y `.tabla-scroll` sí. El foco de teclado en esa tabla deja el outline visible.

## Gotchas

- Sin `ORBIT_DSN_READ` esta ruta es 503: no es un fallo de template, es fail-closed de conexion.
- `amazon_mx` puede no tener filas de metrica: igual debe existir el bloque. Hueco = `null` en el contrato y raya en la tabla, no un 0 pintado.
- ACoS con revenue 0 conocido sigue `null` en el contrato. No se pinta 0%.
- Chart.js vive en `/static/vendor/chart.umd.min.js`. La CSP `default-src 'self'` bloquea CDN.
- La gráfica no mezcla MXN y USD: hay un canvas por canal.
- A 390 px solo `.tabla-scroll` se desplaza en horizontal.
